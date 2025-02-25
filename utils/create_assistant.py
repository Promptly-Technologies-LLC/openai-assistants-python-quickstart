import os
import logging
import asyncio
from typing import cast
from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai.types.beta.assistant_create_params import AssistantCreateParams
from openai.types.beta.assistant_update_params import AssistantUpdateParams
from openai.types.beta.assistant_tool_param import CodeInterpreterToolParam, FileSearchToolParam, FunctionToolParam
from openai.types.beta.assistant import Assistant
from openai.types.shared_params.function_definition import FunctionDefinition
from openai.types.beta.file_search_tool_param import FileSearch


request: AssistantCreateParams = AssistantCreateParams(
    instructions="You are a helpful assistant.",
    name="Quickstart Assistant",
    model="gpt-4o",
    tools=[
        CodeInterpreterToolParam(type="code_interpreter"),
        FileSearchToolParam(
            type="file_search",
            file_search=FileSearch(
                max_num_results=5
            )
        ),
        FunctionToolParam(
            type="function",
            function=FunctionDefinition(
                name="get_weather",
                description="Determine weather in my location",
                parameters={
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "The city and state e.g. San Francisco, CA"
                        },
                        "dates": {
                            "type": "array",
                            "description": "The dates (\"YYYY-MM-DD\") to get weather for",
                            "items": {
                                "type": "string"
                            }
                        }
                    },
                    "required": ["location"],
                    "additionalProperties": False,
                },
                # strict=True gives better adherence to the schema, but all arguments must be required
                strict=False
            )
        ),
    ],
)


def update_env_file(var_name: str, var_value: str, logger: logging.Logger):
    """
    Update the .env file with a new environment variable.

    If the .env file already contains the specified variable, it will be updated.
    The new value will be appended to the .env file if it doesn't exist.
    If the .env file does not exist, it will be created.

    Args:
        var_name: The name of the environment variable to update
        var_value: The value to set for the environment variable
        logger: Logger instance for output
    """
    lines = []
    # Read existing contents if file exists
    if os.path.exists('.env'):
        with open('.env', 'r') as env_file:
            lines = env_file.readlines()

        # Remove any existing line with this variable
        lines = [line for line in lines if not line.startswith(f"{var_name}=")]
    else:
        # Log when we're creating a new .env file
        logger.info("Creating new .env file")

    # Write back all lines including the new variable
    with open('.env', 'w') as env_file:
        env_file.writelines(lines)
        env_file.write(f"{var_name}={var_value}\n")
    
    logger.debug(f"Environment variable {var_name} written to .env: {var_value}")


async def create_or_update_assistant(
        client: AsyncOpenAI,
        assistant_id: str | None,
        request: AssistantCreateParams | AssistantUpdateParams,
        logger: logging.Logger
) -> str:
    """
    Create or update the assistant based on the presence of an assistant_id.
    """
    try:
        assistant: Assistant
        if assistant_id:
            # Update the existing assistant
            assistant = await client.beta.assistants.update(
                assistant_id,
                **cast(AssistantUpdateParams, request)
            )
            logger.info(f"Updated assistant with ID: {assistant_id}")
        else:
            # Create a new assistant
            assistant = await client.beta.assistants.create(
                **cast(AssistantCreateParams, request)
            )
            logger.info(f"Created new assistant: {assistant.id}")

            # Update the .env file with the new assistant ID
            update_env_file("ASSISTANT_ID", assistant.id, logger)

    except Exception as e:
        action = "update" if assistant_id else "create"
        logger.error(f"Failed to {action} assistant: {e}")

    return assistant.id


# Run the assistant creation in an asyncio event loop
if __name__ == "__main__":
    import sys

    # Configure logger to stream to console
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    logger: logging.Logger = logging.getLogger(__name__)
    
    load_dotenv(override=True)
    assistant_id = os.getenv("ASSISTANT_ID", None)

    # Initialize the OpenAI client
    openai: AsyncOpenAI = AsyncOpenAI()

    # Run the main function in an asyncio event loop
    asyncio.run(
        create_or_update_assistant(openai, assistant_id, request, logger)
    )
