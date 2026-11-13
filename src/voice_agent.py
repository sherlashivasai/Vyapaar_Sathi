import asyncio
import inspect
import traceback
from typing import Dict, Any, Optional, List

import logfire
from google import genai
from google.genai import types

from .prompts import build_merchant_system_prompt

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

class GeminiLive:
    """
    Handles real-time bidirectional voice interaction with Gemini Live API.
    """
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.8-live",
        input_sample_rate: int = 16000,
        tools: Optional[List[Any]] = None,
        tool_mapping: Optional[Dict[str, Any]] = None,
        system_prompt: Optional[str] = None,
        custom_vocabulary: Optional[List[str]] = None,
    ):
        self.api_key = api_key
        self.model = model
        self.input_sample_rate = input_sample_rate
        self.client = genai.Client(api_key=api_key)
        self.tools = tools or []
        self.tool_mapping = tool_mapping or {}
        self.system_prompt = system_prompt or build_merchant_system_prompt(merchant_profile)
        self.custom_vocabulary = custom_vocabulary or [
            "Paytm", "Vyapar Saathi", "Khata", "Udhaar", "Baaki", "Mandi", "Kirana"
        ]

    async def start_session(
        self,
        audio_input_queue: asyncio.Queue,
        video_input_queue: Optional[asyncio.Queue],
        text_input_queue: asyncio.Queue,
        audio_output_callback,
        audio_interrupt_callback=None,
        dynamic_system_prompt: Optional[str] = None,
    ):
        active_prompt = dynamic_system_prompt or self.system_prompt

        config_kwargs = {
            "response_modalities": ["AUDIO"],
            "speech_config": types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Kore"
                    )
                )
            ),
            "system_instruction": types.Content(
                parts=[types.Part.from_text(text=active_prompt)]
            ),
            "input_audio_transcription": types.AudioTranscriptionConfig(
                custom_vocabulary=self.custom_vocabulary
            ),
            "output_audio_transcription": types.AudioTranscriptionConfig(),
            "realtime_input_config": types.RealtimeInputConfig(
                turn_coverage="TURN_INCLUDES_ONLY_ACTIVITY",
            ),
            "tools": self.tools,
        }

        # Automatically add thinking_config if using extended-thinking model
        if "extended-thinking" in self.model:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level="low"
            )

        config = types.LiveConnectConfig(**config_kwargs)

        logfire.info("Connecting to Gemini Live", model=self.model)
        try:
            async with self.client.aio.live.connect(model=self.model, config=config) as session:
                logfire.info("Gemini Live session opened successfully")

                event_queue = asyncio.Queue()
                in_flight_tool_tasks: Dict[str, asyncio.Task] = {}

                async def send_audio():
                    try:
                        while True:
                            chunk = await audio_input_queue.get()
                            if chunk is None:
                                break
                            await session.send_realtime_input(
                                audio=types.Blob(
                                    data=chunk,
                                    mime_type=f"audio/pcm;rate={self.input_sample_rate}"
                                )
                            )
                    except asyncio.CancelledError:
                        logfire.debug("send_audio task cancelled")
                    except Exception as e:
                        logfire.error("send_audio error", error=str(e), traceback=traceback.format_exc())

                async def send_video():
                    if video_input_queue is None:
                        return
                    try:
                        while True:
                            chunk = await video_input_queue.get()
                            if chunk is None:
                                break
                            await session.send_realtime_input(
                                video=types.Blob(data=chunk, mime_type="image/jpeg")
                            )
                    except asyncio.CancelledError:
                        logfire.debug("send_video task cancelled")
                    except Exception as e:
                        logfire.error("send_video error", error=str(e), traceback=traceback.format_exc())

                async def send_text():
                    try:
                        while True:
                            text = await text_input_queue.get()
                            if text is None:
                                break
                            logfire.info("Sending text to Gemini", text=text)
                            await session.send_realtime_input(text=text)
                    except asyncio.CancelledError:
                        logfire.debug("send_text task cancelled")
                    except Exception as e:
                        logfire.error("send_text error", error=str(e), traceback=traceback.format_exc())

                async def execute_single_tool(fc):
                    """Runs a single tool call in the background without blocking audio receive."""
                    func_name = fc.name
                    call_id = fc.id
                    args = fc.args or {}

                    await event_queue.put({
                        "type": "tool_start",
                        "id": call_id,
                        "name": func_name,
                        "args": args
                    })

                    if func_name not in self.tool_mapping:
                        payload = {
                            "status": "error",
                            "retryable": False,
                            "message": f"Tool '{func_name}' is not registered."
                        }
                    else:
                        try:
                            tool_func = self.tool_mapping[func_name]
                            if inspect.iscoroutinefunction(tool_func):
                                raw_res = await tool_func(**args)
                            else:
                                loop = asyncio.get_running_loop()
                                raw_res = await loop.run_in_executor(None, lambda: tool_func(**args))

                            # Ensure 3.8-live status contract is met
                            if isinstance(raw_res, dict) and "status" in raw_res:
                                payload = raw_res
                            else:
                                payload = {
                                    "status": "ok",
                                    "retryable": False,
                                    "result": raw_res
                                }
                        except asyncio.CancelledError:
                            logfire.info("Tool call cancelled by user barge-in", name=func_name, id=call_id)
                            raise
                        except Exception as e:
                            logfire.error("Tool execution error", name=func_name, error=str(e))
                            payload = {
                                "status": "error",
                                "retryable": False,
                                "message": f"Execution failed: {str(e)}"
                            }

                    try:
                        await session.send_tool_response(
                            function_responses=[
                                types.FunctionResponse(
                                    name=func_name,
                                    id=call_id,
                                    response=payload
                                )
                            ]
                        )
                        await event_queue.put({
                            "type": "tool_call",
                            "id": call_id,
                            "name": func_name,
                            "args": args,
                            "result": payload
                        })
                    finally:
                        in_flight_tool_tasks.pop(call_id, None)

                async def receive_loop():
                    try:
                        while True:
                            async for response in session.receive():
                                if response.go_away:
                                    logfire.warning("Received GoAway from Gemini", response=response.go_away)

                                server_content = response.server_content
                                tool_call = response.tool_call
                                tool_cancel = getattr(response, "tool_call_cancellation", None)

                                if server_content:
                                    if server_content.model_turn:
                                        for part in server_content.model_turn.parts:
                                            if part.inline_data:
                                                if inspect.iscoroutinefunction(audio_output_callback):
                                                    await audio_output_callback(part.inline_data.data)
                                                else:
                                                    audio_output_callback(part.inline_data.data)

                                    if server_content.input_transcription and server_content.input_transcription.text:
                                        await event_queue.put({
                                            "type": "user",
                                            "text": server_content.input_transcription.text
                                        })

                                    if server_content.output_transcription and server_content.output_transcription.text:
                                        await event_queue.put({
                                            "type": "gemini",
                                            "text": server_content.output_transcription.text
                                        })

                                    if server_content.turn_complete:
                                        await event_queue.put({"type": "turn_complete"})

                                    # Track extended-thinking interaction status if present
                                    interaction_status = getattr(server_content, "interaction_status", None)
                                    if interaction_status:
                                        await event_queue.put({
                                            "type": "interaction_status",
                                            "status": str(interaction_status)
                                        })

                                    if server_content.interrupted:
                                        if audio_interrupt_callback:
                                            if inspect.iscoroutinefunction(audio_interrupt_callback):
                                                await audio_interrupt_callback()
                                            else:
                                                audio_interrupt_callback()
                                        await event_queue.put({"type": "interrupted"})

                                # Cancel in-flight tools if user barged in
                                if tool_cancel and getattr(tool_cancel, "ids", None):
                                    for cancel_id in tool_cancel.ids:
                                        task = in_flight_tool_tasks.pop(cancel_id, None)
                                        if task and not task.done():
                                            task.cancel()

                                # Fire tool calls asynchronously so audio stream never blocks
                                if tool_call and tool_call.function_calls:
                                    for fc in tool_call.function_calls:
                                        t = asyncio.create_task(execute_single_tool(fc))
                                        in_flight_tool_tasks[fc.id] = t

                            logfire.debug("Gemini receive iterator completed, re-entering receive loop")

                    except asyncio.CancelledError:
                        logfire.debug("receive_loop task cancelled")
                    except Exception as e:
                        logfire.error("receive_loop error", error_type=type(e).__name__, error=str(e), traceback=traceback.format_exc())
                        await event_queue.put({"type": "error", "error": f"{type(e).__name__}: {e}"})
                    finally:
                        for t in in_flight_tool_tasks.values():
                            t.cancel()
                        await event_queue.put(None)

                tasks = [
                    asyncio.create_task(send_audio()),
                    asyncio.create_task(send_video()),
                    asyncio.create_task(send_text()),
                    asyncio.create_task(receive_loop()),
                ]

                try:
                    while True:
                        event = await event_queue.get()
                        if event is None:
                            break
                        if isinstance(event, dict) and event.get("type") == "error":
                            yield event
                            break
                        yield event
                finally:
                    logfire.info("Cleaning up Gemini Live session tasks")
                    for t in tasks:
                        t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            logfire.error("Gemini Live session error", error_type=type(e).__name__, error=str(e), traceback=traceback.format_exc())
            raise
        finally:
            logfire.info("Gemini Live session closed")