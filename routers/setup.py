import logging
import os
from typing import Optional, List
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from openai import AsyncOpenAI
from openai.types.beta import Assistant
from urllib.parse import quote

from utils.create_assistant import create_or_update_assistant, ToolTypes
from utils.create_assistant import update_env_file

# Configure logger
logger = logging.getLogger("uvicorn.error")

# Load environment variables
load_dotenv()

router = APIRouter(prefix="/setup", tags=["Setup"])
templates = Jinja2Templates(directory="templates")

@router.put("/api-key")
async def set_openai_api_key(api_key: str = Form(...)) -> RedirectResponse:
    """
    Set the OpenAI API key in the application's environment variables.
    
    Args:
        api_key: OpenAI API key received from form submission
    
    Returns:
        RedirectResponse: Redirects to home page on success
    
    Raises:
        HTTPException: If there's an error updating the environment file
    """
    try:
        update_env_file("OPENAI_API_KEY", api_key, logger)
        return RedirectResponse(url="/", headers={"HX-Redirect": "/"}, status_code=303)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update API key: {str(e)}"
        )


# Add new setup route
@router.get("/")
async def read_setup(
    request: Request,
    client: AsyncOpenAI = Depends(lambda: AsyncOpenAI()),
    status: Optional[str] = None,
    message_text: Optional[str] = None
) -> Response:
    # Check if assistant ID is missing
    current_tools = []
    setup_message = "" # Message specific to setup state (e.g., API key missing)
    load_dotenv(override=True)
    openai_api_key = os.getenv("OPENAI_API_KEY")
    assistant_id = os.getenv("ASSISTANT_ID")
    
    if not openai_api_key:
        setup_message = "OpenAI API key is missing."
    else:
        if assistant_id:
            try:
                assistant = await client.beta.assistants.retrieve(assistant_id)
                current_tools = [tool.type for tool in assistant.tools]
            except Exception as e:
                logger.error(f"Failed to retrieve assistant {assistant_id}: {e}")
                # If we can't retrieve the assistant, proceed as if it doesn't exist
                assistant_id = None
                setup_message = "Error retrieving existing assistant. Please create a new one."
    
    return templates.TemplateResponse(
        "setup.html",
        {
            "request": request,
            "setup_message": setup_message,
            "status": status, # Pass status from query params
            "status_message": message_text, # Pass message from query params
            "assistant_id": assistant_id,
            "current_tools": current_tools
        }
    )


@router.post("/assistant")
async def create_update_assistant(
    tool_types: List[ToolTypes] = Form(...),
    client: AsyncOpenAI = Depends(lambda: AsyncOpenAI())
) -> RedirectResponse:
    """
    Create a new assistant or update an existing one.
    Returns the assistant ID and status of the operation.
    """
    current_assistant_id = os.getenv("ASSISTANT_ID")
    action = "updated" if current_assistant_id else "created"
    new_assistant_id = await create_or_update_assistant(
        client=client,
        assistant_id=current_assistant_id,
        tool_types=tool_types,
        logger=logger
    )
    
    if not new_assistant_id:
        status = "error"
        message_text = f"Failed to {action} assistant."
    else:
        status = "success"
        message_text = f"Assistant {action} successfully."
        
    # URL encode the message text
    encoded_message = quote(message_text)
    redirect_url = f"/setup/?status={status}&message_text={encoded_message}"
    return RedirectResponse(url=redirect_url, status_code=303)
