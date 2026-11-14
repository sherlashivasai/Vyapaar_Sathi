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
from src.voice_agent_sarvam import SarvamLive
from src.tools.execute_data_analysis import execute_data_analysis
from src.tools.get_shop_environment_intelligence import (
    get_shop_environment_intelligence,
    start_4hour_env_refresh_scheduler,
)
from src.tools.khata_ledger import manage_khata_ledger
from src.prompts import build_merchant_system_prompt

# Configure logging - DEBUG for our modules, INFO for everything else
logging.basicConfig(level=logging.INFO)
logging.getLogger("sarvam_live").setLevel(logging.DEBUG)
logging.getLogger(__name__).setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

# Configuration
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
SARVAM_MODEL = os.getenv("SARVAM_MODEL", "sarvam-105b-conversations")
SARVAM_STT_MODEL = os.getenv("STT", "saaras:v4")
SARVAM_TTS_MODEL = os.getenv("TTS", "bulbul:v4-flash")
SARVAM_TTS_SPEAKER = os.getenv("SARVAM_TTS_SPEAKER", "allie")

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
    # Cleanly cancel background task on shutdown
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass


# ==============================================================================
# 2. SARVAM TOOL DECLARATIONS & MAPPING
# ==============================================================================

TOOL_DECLARATIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_shop_environment_intelligence",
            "description": (
                "Fetches live weather and generated general retail suggestions. It does not independently verify "
                "local mandi prices, civic outages, protests, traffic disruptions, or event dates."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "store_location": {
                        "type": "string",
                        "description": "The merchant's registered store location.",
                    },
                    "shop_category": {
                        "type": "string",
                        "description": "The merchant's store category used to select dynamic mandi items.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "khata_ledger",
            "description": (
                "Use this tool to log, check, or update customer credit (udhaar) details in the merchant's khata ledger."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["LOG", "CHECK", "UPDATE"],
                        "description": "Action to perform on the khata ledger.",
                    },
                    "name": {"type": "string", "description": "Customer name."},
                    "phone_no": {"type": "string", "description": "Customer phone number."},
                    "amount": {"type": "number", "description": "Credit amount."},
                    "date_given": {"type": "string", "description": "Credit date in YYYY-MM-DD format."},
                    "promised_return_date": {"type": "string", "description": "Promise date in YYYY-MM-DD format."},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_data_analysis",
            "description": "Runs custom data analysis queries on the merchant's sales and transaction trends.",
            "parameters": {
                "type": "object",
                "properties": {
                    "python_code": {
                        "type": "string",
                        "description": "Pandas code to analyze settlement data; always print findings.",
                    },
                },
                "required": ["python_code"],
            },
        },
    },
]


def build_session_tool_mapping(profile: dict) -> dict:
    """
    Pre-binds `merchant_id`, `store_location`, and `shop_category` from `merchant_profile`
    so tools work seamlessly even if the LLM omits default merchant arguments.
    """
    async def _env_wrapper(store_location: str = None, shop_category: str = None, **kwargs):
        return await _invoke_tool_safely(
            get_shop_environment_intelligence,
            store_location=store_location or profile["store_location"],
            shop_category=shop_category or profile["shop_category"],
            **kwargs,
        )

    async def _khata_wrapper(**kwargs):
        kwargs.setdefault("merchant_id", profile["merchant_id"])
        return await _invoke_tool_safely(manage_khata_ledger, **kwargs)

    async def _analysis_wrapper(python_code: str):
        return await execute_data_analysis.ainvoke({"python_code": python_code})

    return {
        "get_shop_environment_intelligence": _env_wrapper,
        "khata_ledger": _khata_wrapper,
        "execute_data_analysis": _analysis_wrapper,
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
    """WebSocket endpoint for the Saarvam voice session."""
    await websocket.accept()
    logger.info("WebSocket connection accepted for merchant: %s", merchant_profile["merchant_name"])

    audio_input_queue = asyncio.Queue()
    video_input_queue = asyncio.Queue()
    text_input_queue = asyncio.Queue()

    async def audio_output_callback(data):
        await websocket.send_bytes(data)

    async def audio_interrupt_callback():
        pass

    sarvam_client = SarvamLive(
        api_key=SARVAM_API_KEY,
        model=SARVAM_MODEL,
        input_sample_rate=16000,
        tools=TOOL_DECLARATIONS,
        tool_mapping=build_session_tool_mapping(merchant_profile),
        system_prompt=build_merchant_system_prompt(merchant_profile),
        stt_model=SARVAM_STT_MODEL,
        tts_model=SARVAM_TTS_MODEL,
        tts_speaker=SARVAM_TTS_SPEAKER,
    )

    async def receive_from_client():
        try:
            while True:
                message = await websocket.receive()

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
            logger.error("Error receiving from client: %s", e)

    async def run_session():
        async for event in sarvam_client.start_session(
            audio_input_queue=audio_input_queue,
            text_input_queue=text_input_queue,
            audio_output_callback=audio_output_callback,
            audio_interrupt_callback=audio_interrupt_callback,
        ):
            if event:
                await websocket.send_json(event)

    receive_task = asyncio.create_task(receive_from_client())
    session_task = asyncio.create_task(run_session())
    try:
        done, _ = await asyncio.wait(
            (receive_task, session_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            task.result()
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected during session")
    except Exception as e:
        import traceback
        logger.error("Error in Sarvam session: %s: %s\n%s", type(e).__name__, e, traceback.format_exc())
    finally:
        for task in (receive_task, session_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(receive_task, session_task, return_exceptions=True)
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)