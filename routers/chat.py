import logging
import re
from datetime import datetime
from typing import AsyncGenerator, Optional, Union
from fastapi.templating import Jinja2Templates
from fastapi import APIRouter, Form, Depends, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from openai import AsyncOpenAI
from openai.lib.streaming._assistants import AsyncAssistantStreamManager, AsyncAssistantEventHandler
from openai.types.beta.assistant_stream_event import (
    ThreadMessageCreated, ThreadMessageDelta, ThreadRunCompleted,
    ThreadRunRequiresAction, ThreadRunStepCreated, ThreadRunStepDelta
)
from openai.types.beta import AssistantStreamEvent
from openai.types.beta.threads.run import RequiredAction
from openai.types.beta.threads.message_content_delta import MessageContentDelta
from openai.types.beta.threads.text_delta_block import TextDeltaBlock
from fastapi.responses import StreamingResponse
from fastapi import APIRouter, Depends, Form

import json

from routers import files as files_router
from utils.custom_functions import get_weather, post_tool_outputs
from utils.sse import sse_format
from utils.streaming import AssistantStreamMetadata


logger: logging.Logger = logging.getLogger("uvicorn.error")
logger.setLevel(logging.DEBUG)


router: APIRouter = APIRouter(
    prefix="/assistants/{assistant_id}/messages/{thread_id}",
    tags=["assistants_messages"]
)

# Jinja2 templates
templates = Jinja2Templates(directory="templates")


# Route to submit a new user message to a thread and mount a component that
# will start an assistant run stream
@router.post("/send")
async def send_message(
    request: Request,
    assistant_id: str,
    thread_id: str,
    userInput: str = Form(...),
    client: AsyncOpenAI = Depends(lambda: AsyncOpenAI())
) -> HTMLResponse:
    # Create a new message in the thread
    await client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=f"System: Today's date is {datetime.today().strftime('%Y-%m-%d')}\n{userInput}"
    )

    # Render the component templates with the context
    user_message_html = templates.get_template("components/user-message.html").render(user_input=userInput)
    assistant_run_html = templates.get_template("components/assistant-run.html").render(
        assistant_id=assistant_id,
        thread_id=thread_id
    )

    return HTMLResponse(
        content=(
            user_message_html +
            assistant_run_html
        )
    )


# Route to stream the response from the assistant via server-sent events
@router.get("/receive")
async def stream_response(
    assistant_id: str,
    thread_id: str,
    client: AsyncOpenAI = Depends(lambda: AsyncOpenAI())
) -> StreamingResponse:
    """
    Streams the assistant response via Server-Sent Events (SSE). If the assistant requires
    a tool call, we capture that action, invoke the tool, and then re-run the stream
    until completion. This is done in a DRY way by extracting the streaming logic 
    into a helper function.
    """

    async def handle_assistant_stream(
        templates: Jinja2Templates,
        logger: logging.Logger,
        stream_manager: AsyncAssistantStreamManager,
        step_id: str = ""
    ) -> AsyncGenerator[Union[AssistantStreamMetadata, str], None]:
        """
        Async generator to yield SSE events.
        We yield a final AssistantStreamMetadata instance once we're done.
        """
        required_action: Optional[RequiredAction] = None
        run_requires_action_event: Optional[ThreadRunRequiresAction] = None

        event_handler: AsyncAssistantEventHandler
        async with stream_manager as event_handler:
            event: AssistantStreamEvent
            async for event in event_handler:
                # Debug logging for all events
                logger.debug(f"SSE Event Type: {type(event).__name__}")
                logger.debug(f"SSE Event Data: {event.data}")

                if isinstance(event, ThreadMessageCreated):
                    step_id = event.data.id
                    logger.debug(f"Message Created - Step ID: {step_id}")

                    yield sse_format(
                        "messageCreated",
                        templates.get_template("components/assistant-step.html").render(
                            step_type="assistantMessage",
                            step_id=step_id
                        )
                    )

                if isinstance(event, ThreadMessageDelta) and event.data.delta.content:
                    content: MessageContentDelta = event.data.delta.content[0]
                    if isinstance(content, TextDeltaBlock) and content.text and content.text.value:
                        step_id = event.data.id
                        text_value = content.text.value
                        annotations = content.text.annotations
                        
                        # Check for file citation annotations
                        if annotations:
                            logger.debug(f"Annotation found: {annotations}")
                            for annotation in annotations:
                                if annotation.type == 'file_citation' and hasattr(annotation, 'file_citation') and annotation.file_citation:
                                    match = re.search(r'【.*?†(.*?)】', text_value)
                                    if match:
                                        file_name = match.group(1)
                                        # Manually construct the URL
                                        file_url = f'/assistants/{assistant_id}/files/{file_name}'
                                        text_value = f'[†]({file_url})'
                                        logger.debug(f"Replacing citation with link: {text_value}")
                                    else:
                                        # Handle error: pattern not found in the text
                                        logger.warning(f"Could not extract filename from citation text: {text_value}")
                                        file_name = None # Indicate failure
                                    
                                    # Assuming one citation per delta for now
                                    break 
                        
                        sse_data = f'<span hx-swap-oob="beforeend:#step-{step_id}">{text_value}</span>'
                        yield sse_format(
                            "textDelta",
                            sse_data
                        )

                if isinstance(event, ThreadRunStepCreated) and event.data.type == "tool_calls":
                    step_id = event.data.id
                    logger.debug(f"Tool Call Created - Step ID: {step_id}")

                    yield sse_format(
                        f"toolCallCreated",
                        templates.get_template('components/assistant-step.html').render(
                            step_type='toolCall',
                            stream_name=f'toolDelta{step_id}'
                        )
                    )

                if isinstance(event, ThreadRunStepDelta) and event.data.delta.step_details and event.data.delta.step_details.type == "tool_calls":
                    tool_calls = event.data.delta.step_details.tool_calls
                    if tool_calls:
                        # TODO: Support parallel function calling
                        tool_call = tool_calls[0]
                        logger.debug(f"Tool Call Delta - Type: {tool_call.type}")

                        # Handle function tool call
                        if tool_call.type == "function":
                            if tool_call.function and tool_call.function.name:
                                yield sse_format(
                                    f"toolDelta{step_id}",
                                    tool_call.function.name + "<br>"
                                )
                            if tool_call.function and tool_call.function.arguments:
                                yield sse_format(
                                    f"toolDelta{step_id}",
                                    tool_call.function.arguments
                                )
                        
                        # Handle code interpreter tool calls
                        elif tool_call.type == "code_interpreter":
                            if tool_call.code_interpreter and tool_call.code_interpreter.input:
                                logger.debug(f"Code Interpreter Input: {tool_call.code_interpreter.input}")
                                yield sse_format(
                                    f"toolDelta{step_id}",
                                    str(tool_call.code_interpreter.input)
                                )
                            if tool_call.code_interpreter and tool_call.code_interpreter.outputs:
                                for output in tool_call.code_interpreter.outputs:
                                    logger.debug(f"Code Interpreter Output Type: {output.type}")
                                    if output.type == "logs" and output.logs:
                                        yield sse_format(
                                            f"toolDelta{step_id}",
                                            str(output.logs)
                                        )
                                    elif output.type == "image" and output.image and output.image.file_id:
                                        logger.debug(f"Image Output - File ID: {output.image.file_id}")
                                        # Create the image HTML on the backend
                                        image_html = f'<img src="/assistants/{assistant_id}/files/{output.image.file_id}/content" class="code-interpreter-image">'
                                        yield sse_format(
                                            f"imageOutput",
                                            image_html
                                        )

                # If the assistant run requires an action (a tool call), break and handle it
                if isinstance(event, ThreadRunRequiresAction):
                    required_action = event.data.required_action
                    run_requires_action_event = event
                    logger.debug("Run Requires Action Event")
                    if required_action and required_action.submit_tool_outputs:
                        break

                if isinstance(event, ThreadRunCompleted):
                    yield sse_format("endStream", "DONE")

        # At the end (or break) of this async generator, yield a final AssistantStreamMetadata
        yield AssistantStreamMetadata.create(
            required_action=required_action,
            step_id=step_id,
            run_requires_action_event=run_requires_action_event
        )

    async def event_generator() -> AsyncGenerator[str, None]:
        """
        Main generator for SSE events. We call our helper function to handle the assistant
        stream, and if the assistant requests a tool call, we execute it and then re-stream the stream.
        """
        step_id: str = ""
        stream_manager: AsyncAssistantStreamManager[AsyncAssistantEventHandler] = client.beta.threads.runs.stream(
            assistant_id=assistant_id,
            thread_id=thread_id,
            parallel_tool_calls=False
        )

        while True:
            event: Union[AssistantStreamMetadata, str]
            async for event in handle_assistant_stream(templates, logger, stream_manager, step_id):
                if isinstance(event, AssistantStreamMetadata):
                    # Use the helper methods from our class
                    step_id = event.step_id
                    if event.requires_tool_call():
                        for tool_call in event.required_action.submit_tool_outputs.tool_calls:  # type: ignore
                            if tool_call.type == "function":
                                try:
                                    args = json.loads(tool_call.function.arguments)
                                    location = args.get("location", "Unknown")
                                    dates_raw = args.get("dates", [datetime.today().strftime("%Y-%m-%d")])
                                    dates = [
                                        datetime.strptime(d, "%Y-%m-%d")
                                        for d in dates_raw if isinstance(d, str)
                                    ]
                                except Exception as err:
                                    logger.error(f"Failed to parse function arguments: {err}")
                                    location = "Unknown"
                                    dates = [datetime.today()]

                                try:
                                    weather_output: list = get_weather(location, dates)
                                    logger.info(f"Weather output: {weather_output}")

                                    # Render the weather widget
                                    weather_widget_html = templates.get_template(
                                        "components/weather-widget.html"
                                    ).render(
                                        reports=weather_output
                                    )

                                    # Yield the rendered HTML
                                    yield sse_format("toolOutput", weather_widget_html)

                                    data_for_tool = {
                                        "tool_outputs": {
                                            "output": str(weather_output),
                                            "tool_call_id": tool_call.id
                                        },
                                        "runId": event.get_run_id(),
                                    }
                                except Exception as err:
                                    error_message = f"Failed to get weather output: {err}"
                                    logger.error(error_message)
                                    yield sse_format("toolOutput", error_message)
                                    data_for_tool = {
                                        "tool_outputs": {
                                            "output": error_message,
                                            "tool_call_id": tool_call.id
                                        },
                                        "runId": event.get_run_id(),
                                    }

                                # Afterwards, create a fresh stream_manager for the next iteration
                                new_stream_manager = await post_tool_outputs(
                                    client,
                                    data_for_tool,
                                    thread_id
                                )
                                stream_manager = new_stream_manager
                                # proceed to rerun the loop
                                break
                    else:
                        # No more tool calls needed; we're done streaming
                        return
                else:
                    # Normal SSE events: yield them to the client
                    yield str(event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )
