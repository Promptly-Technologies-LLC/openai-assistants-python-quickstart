import os
import logging
from typing import List, Dict, Any, AsyncIterable
from dotenv import load_dotenv
from fastapi import APIRouter, Request, UploadFile, File, HTTPException, Depends, Form, Path
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI

logger = logging.getLogger("uvicorn.error")

# Get assistant ID from environment variables
load_dotenv()
assistant_id_env = os.getenv("ASSISTANT_ID")
if not assistant_id_env:
    raise ValueError("ASSISTANT_ID environment variable not set")
assistant_id: str = assistant_id_env

router = APIRouter(
    prefix="/assistants/{assistant_id}/files",
    tags=["assistants_files"]
)

# Helper function to get or create a vector store
async def get_or_create_vector_store(assistantId: str, client: AsyncOpenAI = Depends(lambda: AsyncOpenAI())) -> str:
    assistant = await client.beta.assistants.retrieve(assistantId)
    if assistant.tool_resources and assistant.tool_resources.file_search and assistant.tool_resources.file_search.vector_store_ids:
        return assistant.tool_resources.file_search.vector_store_ids[0]
    vector_store = await client.beta.vector_stores.create(name="sample-assistant-vector-store")
    await client.beta.assistants.update(
        assistantId,
        tool_resources={
            "file_search": {
                "vector_store_ids": [vector_store.id],
            },
        }
    )
    return vector_store.id

@router.get("/")
async def list_files(client: AsyncOpenAI = Depends(lambda: AsyncOpenAI())) -> List[Dict[str, str]]:
    # List files in the vector store
    vector_store_id = await get_or_create_vector_store(assistant_id, client)
    file_list = await client.beta.vector_stores.files.list(vector_store_id)
    files_array: List[Dict[str, str]] = []
    
    if file_list.data:
        for file in file_list.data:
            file_details = await client.files.retrieve(file.id)
            vector_file_details = await client.beta.vector_stores.files.retrieve(
                vector_store_id=vector_store_id,
                file_id=file.id
            )
            files_array.append({
                "file_id": file.id,
                "filename": file_details.filename or "unknown_filename",
                "status": vector_file_details.status or "unknown_status",
            })
    return files_array

@router.post("/")
async def upload_file(file: UploadFile = File(...)) -> Dict[str, str]:
    try:
        client = AsyncOpenAI()
        vector_store_id = await get_or_create_vector_store(assistant_id)
        openai_file = await client.files.create(
            file=file.file,
            purpose="assistants"
        )
        await client.beta.vector_stores.files.create(
            vector_store_id=vector_store_id,
            file_id=openai_file.id
        )
        return {"message": "File uploaded successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def stream_file_content(content: bytes) -> AsyncIterable[bytes]:
    yield content

@router.get("/{file_id}")
async def get_file(
    file_id: str = Path(..., description="The ID of the file to retrieve"),
    client: AsyncOpenAI = Depends(lambda: AsyncOpenAI())
) -> StreamingResponse:
    try:
        file = await client.files.retrieve(file_id)
        file_content = await client.files.content(file_id)
        
        if not hasattr(file_content, 'content'):
            raise HTTPException(status_code=500, detail="File content not available")
            
        return StreamingResponse(
            stream_file_content(file_content.content),
            headers={"Content-Disposition": f'attachment; filename=\"{file.filename or "download"}\"'}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/delete")
async def delete_file(
    request: Request, 
    fileId: str = Form(...), 
    client: AsyncOpenAI = Depends(lambda: AsyncOpenAI())
) -> Dict[str, str]:
    vector_store_id = await get_or_create_vector_store(assistant_id, client)
    await client.beta.vector_stores.files.delete(vector_store_id=vector_store_id, file_id=fileId)
    return {"message": "File deleted successfully"}
