import os
import logging
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from routers import files, messages, tools, api_keys, assistants
from utils.threads import create_thread


logger = logging.getLogger("uvicorn.error")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Optional startup logic
    yield
    # Optional shutdown logic

app = FastAPI(lifespan=lifespan)

# Mount routers
app.include_router(messages.router)
app.include_router(files.router)
app.include_router(tools.router)
app.include_router(api_keys.router)
app.include_router(assistants.router)

# Mount static files (e.g., CSS, JS)
app.mount("/static", StaticFiles(directory=os.path.join(os.getcwd(), "static")), name="static")

# Initialize Jinja2 templates
templates = Jinja2Templates(directory="templates")

# TODO: Implement some kind of thread id storage or management logic to allow
# user to load an old thread, delete an old thread, etc. instead of start new
@app.get("/")
async def read_home(request: Request):
    logger.info("Home page requested")
    
    # Check if environment variables are missing
    load_dotenv(override=True)
    if not os.getenv("OPENAI_API_KEY") or not os.getenv("ASSISTANT_ID"):
        return RedirectResponse(url="/setup")
    
    # Create a new assistant chat thread if no thread ID is provided
    if not thread_id or thread_id == "None" or thread_id == "null":
        thread_id: str = await create_thread()
    
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "assistant_id": os.getenv("ASSISTANT_ID"),
            "messages": messages,
            "thread_id": thread_id
        }
    )


# Add new setup route
@app.get("/setup")
async def read_setup(request: Request, message: str = None):
    # Check if assistant ID is missing
    load_dotenv(override=True)
    if not os.getenv("OPENAI_API_KEY"):
        message="OpenAI API key is missing."
    elif not os.getenv("ASSISTANT_ID"):
        message="Assistant ID is missing."
    else:
        message="All set up!"
    
    return templates.TemplateResponse(
        "setup.html",
        {"request": request, "message": message}
    )

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
