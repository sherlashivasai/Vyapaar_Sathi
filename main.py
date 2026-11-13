import asyncio
import base64
import json
import logging
import os

import logfire
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from src.voice_agent import GeminiLive
from src.tools.execute_data_analysis import execute_data_analysis
from src.tools.get_shop_environment_intelligence import get_shop_environment_intelligence
from src.tools.analyze_business_pulse import analyze_business_pulse
from src.tools.khata_ledger import khata_ledger

from src.prompts import build_merchant_system_prompt 

# Load environment variables
load_dotenv()

logfire.configure(service_name="merchant-ai")

# Configure logging - DEBUG for our modules, INFO for everything else
logging.basicConfig(level=logging.INFO)
logging.getLogger("gemini_live").setLevel(logging.DEBUG)
logging.getLogger(__name__).setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

# Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("MODEL", "gemini-3.8-live")

merchant_profile = {
    "merchant_id": "M123456",
    "merchant_name": "Rajaiah",
    "shop_name": "Rajaiah Kirana Store",
    "shop_category": "Kirana & General Store",
    "items_available": ["Rice", "Wheat Flour", "Sugar", "Cooking Oil","Onions",'Potatoes','Ginger','Garlic','Chillies','Spices','Snacks','Biscuits','Soft Drinks'],
    "store_location": "vegetable market, Mancherial, Telangana",
    "lat": 18.8756,
    "lon": 79.4591,
    "preferred_language": "Telglish (Telugu + simple English)",
    "shadow_loan_limit": 50000,
    "existing_customers": ["Raju", "Sita", "Mohan"]
}


# Initialize FastAPI
app = FastAPI()

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
    """WebSocket endpoint for Gemini Live."""
    await websocket.accept()

    logger.info("WebSocket connection accepted")

    audio_input_queue = asyncio.Queue()
    video_input_queue = asyncio.Queue()
    text_input_queue = asyncio.Queue()

    async def audio_output_callback(data):
        await websocket.send_bytes(data)

    async def audio_interrupt_callback():
        # The event queue handles the JSON message, but we might want to do something else here
        pass

    gemini_client = GeminiLive(
        api_key=GEMINI_API_KEY, model=MODEL, input_sample_rate=16000,system_prompt=build_merchant_system_prompt(merchant_profile)
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
                            logger.info(f"Received image chunk from client: {len(payload['data'])} base64 chars")
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
            logger.error(f"Error receiving from client: {e}")

    async def run_session():
        async for event in gemini_client.start_session(
            audio_input_queue=audio_input_queue,
            video_input_queue=video_input_queue,
            text_input_queue=text_input_queue,
            audio_output_callback=audio_output_callback,
            audio_interrupt_callback=audio_interrupt_callback,
        ):
            if event:
                # Forward events (transcriptions, etc) to client
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
        logger.error(f"Error in Gemini session: {type(e).__name__}: {e}\n{traceback.format_exc()}")
    finally:
        for task in (receive_task, session_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(receive_task, session_task, return_exceptions=True)
        # Ensure websocket is closed if not already
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)