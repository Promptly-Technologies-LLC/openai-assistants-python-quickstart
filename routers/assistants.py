from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from openai import AsyncOpenAI
from utils.create_assistant import create_or_update_assistant, request
import logging
import os
from dotenv import load_dotenv

# Configure logger
logger = logging.getLogger("uvicorn.error")

# Load environment variables
load_dotenv()

router = APIRouter(
    prefix="/assistants",
    tags=["assistants"]
)


@router.post("/create-update")
async def create_update_assistant(
    client: AsyncOpenAI = Depends(lambda: AsyncOpenAI())
):
    """
    Create a new assistant or update an existing one.
    Returns the assistant ID and status of the operation.
    """
    assistant_id = os.getenv("ASSISTANT_ID")

    assistant_id: str = await create_or_update_assistant(
        client=client,
        assistant_id=assistant_id,
        request=request,
        logger=logger
    )
    if not assistant_id:
        raise HTTPException(status_code=400, detail="Failed to create or update assistant")

    return RedirectResponse(url="/", status_code=303)
