import asyncio
import inspect
import json
import logging
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


class GeminiLive:
    """Manages the bidirectional WebSocket session with the Gemini Multimodal Live API."""

    def __init__(
        self,
        api_key: Optional[str],
        model: str = "gemini-3.8-live",
        input_sample_rate: int = 16000,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_mapping: Optional[Dict[str, Callable]] = None,
        system_prompt: Optional[str] = None,
        voice_name: str = "Aoede",
    ):
        self.api_key = api_key
        self.model = model
        self.input_sample_rate = input_sample_rate
        self.tools = tools or []
        self.tool_mapping = tool_mapping or {}
        self.system_prompt = system_prompt
        self.voice_name = voice_name
        self.session_handle: Optional[str] = None

        self.client = genai.Client(
            api_key=self.api_key,
            http_options={"api_version": "v1alpha"},
        )

    def _build_live_config(self) -> types.LiveConnectConfig:
        """Constructs the LiveConnectConfig for the Gemini Live session."""
        audio_modality = getattr(types.Modality, "AUDIO", "AUDIO")

        config_kwargs: Dict[str, Any] = {
            "response_modalities": [audio_modality],
            "speech_config": types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.voice_name
                    )
                )
            ),
            "input_audio_transcription": types.AudioTranscriptionConfig(),
            "output_audio_transcription": types.AudioTranscriptionConfig(),
            "session_resumption": types.SessionResumptionConfig(
                handle=self.session_handle
            )
            if self.session_handle
            else types.SessionResumptionConfig(),
        }

        if self.system_prompt:
            config_kwargs["system_instruction"] = types.Content(
                parts=[types.Part.from_text(text=self.system_prompt)]
            )

        if self.tools:
            config_kwargs["tools"] = self.tools

        return types.LiveConnectConfig(**config_kwargs)

    @staticmethod
    def _normalize_tool_result(result: Any) -> Dict[str, Any]:
        """Ensures tool output is always a valid JSON-serializable dict for FunctionResponse."""
        if isinstance(result, dict):
            return result
        if hasattr(result, "model_dump"):
            return result.model_dump()
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
            return {"result": result}
        return {"result": str(result)}

    async def _execute_tool(self, func_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Executes a mapped tool function (async or sync) safely with a timeout."""
        func = self.tool_mapping.get(func_name)
        if not func:
            logger.warning("Requested tool '%s' is not registered in tool_mapping", func_name)
            return {
                "status": "error",
                "retryable": False,
                "message": f"Tool '{func_name}' not found.",
            }

        try:
            if inspect.iscoroutinefunction(func):
                raw_result = await asyncio.wait_for(func(**args), timeout=15.0)
            else:
                loop = asyncio.get_running_loop()
                raw_result = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: func(**args)),
                    timeout=15.0,
                )
            normalized = self._normalize_tool_result(raw_result)
            logger.info("Tool '%s' completed: %s", func_name, str(normalized)[:200])
            return normalized
        except asyncio.TimeoutError:
            logger.error("Tool '%s' timed out after 15s", func_name)
            return {
                "status": "error",
                "retryable": True,
                "message": f"Tool '{func_name}' timed out.",
            }
        except Exception as exc:
            logger.exception("Error executing tool '%s': %s", func_name, exc)
            return {
                "status": "error",
                "retryable": False,
                "message": str(exc),
            }

    async def start_session(
        self,
        audio_input_queue: asyncio.Queue,
        video_input_queue: asyncio.Queue,
        text_input_queue: asyncio.Queue,
        audio_output_callback: Callable[[bytes], Any],
        audio_interrupt_callback: Optional[Callable[[], Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Starts the Gemini Live session, streaming audio/video/text inputs and yielding
        structured JSON events back to the FastAPI WebSocket loop.
        """
        config = self._build_live_config()
        event_queue: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue()

        logger.info("Connecting to Gemini Live with model=%s", self.model)
        async with self.client.aio.live.connect(model=self.model, config=config) as session:
            logger.info("Gemini Live session opened successfully")

            async def fail_session(task_name: str, exc: Exception) -> None:
                logger.error(
                    "%s failed: %s: %s",
                    task_name,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                await event_queue.put({
                    "type": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                await event_queue.put(None)

            async def send_audio():
                try:
                    while True:
                        pcm_bytes = await audio_input_queue.get()
                        if pcm_bytes is None:
                            break
                        blob = types.Blob(
                            data=pcm_bytes,
                            mime_type=f"audio/pcm;rate={self.input_sample_rate}",
                        )
                        try:
                            await session.send_realtime_input(audio=blob)
                        except TypeError:
                            await session.send_realtime_input(media=blob)
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    await fail_session("send_audio", exc)

            async def send_video():
                try:
                    while True:
                        image_bytes = await video_input_queue.get()
                        if image_bytes is None:
                            break
                        blob = types.Blob(
                            data=image_bytes,
                            mime_type="image/jpeg",
                        )
                        await session.send_realtime_input(media=blob)
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    await fail_session("send_video", exc)

            async def send_text():
                try:
                    while True:
                        text = await text_input_queue.get()
                        if text is None:
                            break
                        await session.send_client_content(
                            turns=types.Content(
                                role="user",
                                parts=[types.Part.from_text(text=text)],
                            ),
                            turn_complete=True,
                        )
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    await fail_session("send_text", exc)

            async def receive_loop():
                try:
                    while True:
                        async for response in session.receive():
                            # 1. Session resumption updates
                            if response.session_resumption_update:
                                update = response.session_resumption_update
                                logger.info(
                                    "Session resumption update: new_handle=%r resumable=%s last_consumed_client_message_index=%s",
                                    update.new_handle,
                                    update.resumable,
                                    getattr(update, "last_consumed_client_message_index", None),
                                )
                                if update.resumable and update.new_handle:
                                    self.session_handle = update.new_handle

                            # 2. Server content (audio, text, transcripts, barge-in, turn state)
                            server_content = response.server_content
                            if server_content is not None:
                                if server_content.interrupted:
                                    logger.info("Gemini generation interrupted by user")
                                    if audio_interrupt_callback:
                                        if inspect.iscoroutinefunction(audio_interrupt_callback):
                                            await audio_interrupt_callback()
                                        else:
                                            audio_interrupt_callback()
                                    await event_queue.put({"type": "interrupted"})

                                if server_content.model_turn and server_content.model_turn.parts:
                                    for part in server_content.model_turn.parts:
                                        if part.inline_data and part.inline_data.data:
                                            await audio_output_callback(part.inline_data.data)
                                        if part.text:
                                            await event_queue.put(
                                                {"type": "text", "text": part.text}
                                            )

                                input_tx = getattr(server_content, "input_transcription", None)
                                if input_tx and getattr(input_tx, "text", None):
                                    await event_queue.put(
                                        {
                                            "type": "user",
                                            "text": input_tx.text,
                                            "finished": getattr(input_tx, "finished", False),
                                        }
                                    )

                                output_tx = getattr(server_content, "output_transcription", None)
                                if output_tx and getattr(output_tx, "text", None):
                                    await event_queue.put(
                                        {
                                            "type": "gemini",
                                            "text": output_tx.text,
                                            "finished": getattr(output_tx, "finished", False),
                                        }
                                    )

                                if getattr(server_content, "generation_complete", False):
                                    await event_queue.put({"type": "generation_complete"})

                                if server_content.turn_complete:
                                    await event_queue.put({"type": "turn_complete"})

                            # 3. Tool calls
                            tool_call = response.tool_call
                            if tool_call and tool_call.function_calls:
                                function_responses = []
                                for fc in tool_call.function_calls:
                                    func_name = fc.name
                                    func_args = dict(fc.args) if fc.args else {}
                                    logger.info(
                                        "Executing tool call: %s(args=%s)", func_name, func_args
                                    )
                                    await event_queue.put(
                                        {
                                            "type": "tool_start",
                                            "name": func_name,
                                            "args": func_args,
                                            "id": fc.id,
                                        }
                                    )

                                    result_dict = await self._execute_tool(func_name, func_args)

                                    await event_queue.put(
                                        {
                                            "type": "tool_call",
                                            "name": func_name,
                                            "args": func_args,
                                            "result": result_dict,
                                            "id": fc.id,
                                        }
                                    )

                                    function_responses.append(
                                        types.FunctionResponse(
                                            id=fc.id,
                                            name=func_name,
                                            response=result_dict,
                                        )
                                    )

                                await session.send_tool_response(
                                    function_responses=function_responses
                                )
                                logger.info("Sent tool responses back to Gemini Live session")

                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    await fail_session("receive_loop", exc)
                finally:
                    logger.info("receive_loop exiting")
                    await event_queue.put(None)

            tasks = [
                asyncio.create_task(send_audio(), name="send_audio"),
                asyncio.create_task(send_video(), name="send_video"),
                asyncio.create_task(send_text(), name="send_text"),
                asyncio.create_task(receive_loop(), name="receive_loop"),
            ]

            try:
                while True:
                    event = await event_queue.get()
                    if event is None:
                        break
                    yield event
            finally:
                logger.info("Cleaning up Gemini Live session tasks")
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.info("Gemini Live session closed")