import asyncio
import base64
import inspect
import json
import logging
import os
from contextlib import asynccontextmanager

import logfire
from dotenv import load_dotenv

load_dotenv()
logfire.configure(service_name="merchant-ai")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.db import init_db
from src.demo_data import DEMO_MERCHANT_PROFILE, seed_demo_data
from src.voice_agent import GeminiLive
from src.tools.execute_data_analysis import execute_data_analysis
from src.tools.get_shop_environment_intelligence import (
    get_shop_environment_intelligence,
    start_4hour_env_refresh_scheduler,
)
from src.tools.khata_ledger import manage_khata_ledger
from src.prompts import build_merchant_system_prompt

# Configure logging - DEBUG for our modules, INFO for everything else
logging.basicConfig(level=logging.INFO)
logging.getLogger("google_genai").setLevel(logging.INFO)
logging.getLogger(__name__).setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

# Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")

merchant_profile = DEMO_MERCHANT_PROFILE


# ==============================================================================
# 1. 4-HOUR BACKGROUND ENVIRONMENT INTELLIGENCE SCHEDULER
# ==============================================================================

async def _invoke_tool_safely(func, **kwargs):
    """Helper to call either async or sync tool implementations."""
    if inspect.iscoroutinefunction(func):
        return await func(**kwargs)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(**kwargs))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_demo_data()
    scheduler_task = asyncio.create_task(
        start_4hour_env_refresh_scheduler(merchants_to_track=[merchant_profile])
    )
    yield
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass


# ==============================================================================
# 2. GEMINI TOOL DECLARATIONS & MAPPING
# ==============================================================================

def _build_env_tool(profile: dict):
    async def _env_wrapper(store_location: str = None, shop_category: str = None, **kwargs):
        resolved_location = store_location or profile["store_location"]
        is_profile_location = (
            resolved_location.strip().casefold()
            == profile["store_location"].strip().casefold()
        )
        kwargs.setdefault("lat", profile.get("lat") if is_profile_location else None)
        kwargs.setdefault("lon", profile.get("lon") if is_profile_location else None)
        return await _invoke_tool_safely(
            get_shop_environment_intelligence,
            store_location=resolved_location,
            shop_category=shop_category or profile["shop_category"],
            **kwargs,
        )

    return _env_wrapper


def _build_khata_tool(profile: dict):
    async def _khata_wrapper(**kwargs):
        kwargs.setdefault("merchant_id", profile["merchant_id"])
        return await _invoke_tool_safely(manage_khata_ledger, **kwargs)

    return _khata_wrapper


def _build_analysis_tool(_profile: dict):
    async def _analysis_wrapper(analysis_type: str):
        return await execute_data_analysis.ainvoke({"analysis_type": analysis_type})

    return _analysis_wrapper


TOOL_REGISTRY = {
    "get_shop_environment_intelligence": {
        "description": (
            "Fetches live weather and generated general retail suggestions. It does not independently verify "
            "local mandi prices, civic outages, protests, traffic disruptions, or event dates."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "store_location": {
                    "type": "STRING",
                    "description": "The merchant's registered store location.",
                },
                "shop_category": {
                    "type": "STRING",
                    "description": "The merchant's store category used to select dynamic mandi items.",
                },
            },
            "required": [],
        },
        "builder": _build_env_tool,
    },
    "khata_ledger": {
        "description": (
            "Use this tool to log, check, or update customer credit (udhaar) details in the merchant's khata ledger."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "enum": ["LOG", "CHECK", "UPDATE"],
                    "description": "Action to perform on the khata ledger.",
                },
                "name": {"type": "STRING", "description": "Customer name."},
                "phone_no": {"type": "STRING", "description": "Customer phone number."},
                "amount": {"type": "NUMBER", "description": "Credit amount."},
                "date_given": {"type": "STRING", "description": "Credit date in YYYY-MM-DD format."},
                "promised_return_date": {
                    "type": "STRING",
                    "description": "Promise date in YYYY-MM-DD format.",
                },
            },
            "required": ["action"],
        },
        "builder": _build_khata_tool,
    },
    "execute_data_analysis": {
        "description": (
            "Runs one of the predefined analyses on the merchant's settlement database. "
            "Choose a summary, daily totals, totals by payment instrument, or fees and taxes."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "analysis_type": {
                    "type": "STRING",
                    "enum": [
                        "summary",
                        "daily_totals",
                        "by_instrument",
                        "fees_and_taxes",
                    ],
                    "description": "Which predefined settlement analysis to run.",
                },
            },
            "required": ["analysis_type"],
        },
        "builder": _build_analysis_tool,
    },
}


TOOL_DECLARATIONS = [{
    "function_declarations": [
        {
            "name": name,
            "description": definition["description"],
            "parameters": definition["parameters"],
        }
        for name, definition in TOOL_REGISTRY.items()
    ]
}]


def build_session_tool_mapping(profile: dict) -> dict:
    """Build the callable tool registry from the single source of truth."""
    return {
        name: definition["builder"](profile)
        for name, definition in TOOL_REGISTRY.items()
    }


# ==============================================================================
# 3. FASTAPI APP & WEBSOCKET ENDPOINT
# ==============================================================================

app = FastAPI(title="Paytm Vyapar Saathi", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
app.mount("/static", StaticFiles(directory="src/frontend"), name="static")


@app.get("/")
async def root():
    return FileResponse("src/frontend/index.html")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for the Gemini Live voice session."""
    await websocket.accept()
    logger.info("WebSocket connection accepted for merchant: %s", merchant_profile["merchant_name"])

    audio_input_queue = asyncio.Queue()
    video_input_queue = asyncio.Queue()
    text_input_queue = asyncio.Queue()

    async def audio_output_callback(data):
        await websocket.send_bytes(data)

    async def audio_interrupt_callback():
        pass

    async def send_error_event(error: Exception):
        try:
            await websocket.send_json({
                "type": "error",
                "error": f"{type(error).__name__}: {error}",
            })
        except (WebSocketDisconnect, RuntimeError):
            logger.debug("Could not send session error because the WebSocket is closed")

    gemini_client = GeminiLive(
        api_key=GEMINI_API_KEY,
        model=GEMINI_LIVE_MODEL,
        input_sample_rate=16000,
        tools=TOOL_DECLARATIONS,
        tool_mapping=build_session_tool_mapping(merchant_profile),
        system_prompt=build_merchant_system_prompt(merchant_profile),
    )

    async def receive_from_client():
        try:
            while True:
                message = await websocket.receive()

                if message.get("type") == "websocket.disconnect":
                    logger.info("WebSocket disconnected by client")
                    break

                if message.get("bytes"):
                    await audio_input_queue.put(message["bytes"])
                elif message.get("text"):
                    text = message["text"]
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        payload = None

                    if isinstance(payload, dict) and payload.get("type") == "image":
                        try:
                            logger.info("Received image chunk from client: %d base64 chars", len(payload["data"]))
                            image_data = base64.b64decode(payload["data"])
                            await video_input_queue.put(image_data)
                            continue
                        except (KeyError, ValueError) as e:
                            logger.warning("Ignoring invalid image message: %s", e)
                            continue

                    if isinstance(payload, dict) and isinstance(payload.get("text"), str):
                        text = payload["text"]

                    await text_input_queue.put(text)
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected")
        except Exception as e:
            logger.exception("Error receiving from client")
            await send_error_event(e)

    async def run_session():
        async for event in gemini_client.start_session(
            audio_input_queue=audio_input_queue,
            video_input_queue=video_input_queue,
            text_input_queue=text_input_queue,
            audio_output_callback=audio_output_callback,
            audio_interrupt_callback=audio_interrupt_callback,
        ):
            if event:
                await websocket.send_json(event)

    receive_task = asyncio.create_task(receive_from_client(), name="receive_from_client")
    session_task = asyncio.create_task(run_session(), name="run_session")

    try:
        done, pending = await asyncio.wait(
            (receive_task, session_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in done:
            if not task.cancelled() and task.exception():
                raise task.exception()
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected during session")
    except Exception as e:
        logger.exception("Error in Gemini Live session")
        await send_error_event(e)
    finally:
        for task in (receive_task, session_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(receive_task, session_task, return_exceptions=True)
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)