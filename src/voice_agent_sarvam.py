import asyncio
import base64
import inspect
import json
import os
import traceback
from typing import Any, Dict, List, Optional

import logfire
from sarvamai import AsyncSarvamAI
from sarvamai.types.realtime_audio_input import RealtimeAudioInput

from .prompts import build_merchant_system_prompt

merchant_profile = {
    "merchant_id": "M123456",
    "merchant_name": "Rajaiah",
    "shop_name": "Rajaiah Kirana Store",
    "shop_category": "Kirana & General Store",
    "items_available": [
        "Rice", "Wheat Flour", "Sugar", "Cooking Oil", "Onions", "Potatoes",
        "Ginger", "Garlic", "Chillies", "Spices", "Snacks", "Biscuits",
        "Soft Drinks",
    ],
    "store_location": "vegetable market, Mancherial, Telangana",
    "lat": 18.8756,
    "lon": 79.4591,
    "preferred_language": "Telglish (Telugu + simple English)",
    "shadow_loan_limit": 50000,
    "existing_customers": ["Raju", "Sita", "Mohan"],
}


class SarvamLive:
    """Runs a voice session using Sarvam streaming STT, chat, and TTS APIs."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        input_sample_rate: int = 16000,
        tools: Optional[List[Any]] = None,
        tool_mapping: Optional[Dict[str, Any]] = None,
        system_prompt: Optional[str] = None,
        stt_model: Optional[str] = None,
        tts_model: Optional[str] = None,
        stt_language: Optional[str] = None,
        tts_language: Optional[str] = None,
        tts_speaker: Optional[str] = None,
    ):
        resolved_api_key = api_key or os.getenv("SARVAM_API_KEY")
        if not resolved_api_key:
            raise ValueError("A Sarvam API key is required; set SARVAM_API_KEY.")

        self.api_key = resolved_api_key
        self.model = model or os.getenv("SARVAM_MODEL", "sarvam-105b-conversations")
        self.stt_model = stt_model or os.getenv("STT", "saaras:v3-realtime")
        self.tts_model = tts_model or os.getenv("TTS", "bulbul:v3")
        self.stt_language = stt_language or os.getenv("SARVAM_STT_LANGUAGE", "auto")
        self.tts_language = tts_language or os.getenv("SARVAM_TTS_LANGUAGE", "te-IN")
        self.tts_speaker = tts_speaker or os.getenv("SARVAM_TTS_SPEAKER")
        if (
            self.tts_speaker is None
            and self.tts_model == "bulbul:v4-flash"
            and self.tts_language == "te-IN"
        ):
            self.tts_speaker = "kavitha_te_conversation"
        self.input_sample_rate = input_sample_rate
        self.client = AsyncSarvamAI(api_subscription_key=self.api_key)
        self.tools = [self._tool_schema(tool) for tool in (tools or [])]
        self.tool_mapping = tool_mapping or {}
        self.system_prompt = system_prompt or build_merchant_system_prompt(merchant_profile)

    @staticmethod
    def _tool_schema(tool: Any) -> Dict[str, Any]:
        if isinstance(tool, dict):
            if "function" in tool:
                return tool
            name = tool.get("name")
            description = tool.get("description", "")
            parameters = tool.get("parameters", {"type": "object", "properties": {}})
        else:
            name = getattr(tool, "name", None)
            description = getattr(tool, "description", "")
            args_schema = getattr(tool, "args_schema", None)
            parameters = (
                args_schema.model_json_schema()
                if args_schema is not None and hasattr(args_schema, "model_json_schema")
                else {"type": "object", "properties": {}}
            )

        if not name:
            raise ValueError(f"Cannot create a Sarvam function schema for {tool!r}: missing name.")

        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        }

    async def _invoke_callback(self, callback, *args):
        result = callback(*args)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _execute_tool(self, function_call, event_queue: asyncio.Queue):
        name = function_call.function.name
        call_id = function_call.id
        try:
            arguments = json.loads(function_call.function.arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object.")
        except (json.JSONDecodeError, ValueError) as exc:
            payload = {
                "status": "error",
                "retryable": False,
                "message": f"Invalid arguments for tool '{name}': {exc}",
            }
            arguments = {}
        else:
            await event_queue.put({
                "type": "tool_start",
                "id": call_id,
                "name": name,
                "args": arguments,
            })

            if name not in self.tool_mapping:
                payload = {
                    "status": "error",
                    "retryable": False,
                    "message": f"Tool '{name}' is not registered.",
                }
            else:
                try:
                    tool = self.tool_mapping[name]
                    if hasattr(tool, "ainvoke"):
                        result = await tool.ainvoke(arguments)
                    elif inspect.iscoroutinefunction(tool):
                        result = await tool(**arguments)
                    else:
                        loop = asyncio.get_running_loop()
                        result = await loop.run_in_executor(
                            None, lambda: tool(**arguments)
                        )

                    payload = result if isinstance(result, dict) and "status" in result else {
                        "status": "ok",
                        "retryable": False,
                        "result": result,
                    }
                except asyncio.CancelledError:
                    logfire.info("Sarvam tool call cancelled by interruption", name=name, id=call_id)
                    raise
                except Exception as exc:
                    logfire.error("Sarvam tool execution failed", name=name, error=str(exc))
                    payload = {
                        "status": "error",
                        "retryable": False,
                        "message": f"Execution failed: {exc}",
                    }

        await event_queue.put({
            "type": "tool_call",
            "id": call_id,
            "name": name,
            "args": arguments,
            "result": payload,
        })
        return payload

    async def _complete_turn(
        self,
        text: str,
        conversation: List[Dict[str, Any]],
        event_queue: asyncio.Queue,
        audio_output_callback,
    ):
        messages = [dict(message) for message in conversation]
        messages.append({"role": "user", "content": text})

        for _ in range(5):
            response = await self.client.chat.completions_v2(
                model=self.model,
                messages=messages,
                tools=self.tools or None,
                tool_choice="auto" if self.tools else None,
                temperature=0.2,
            )
            if not response.choices:
                raise RuntimeError("Sarvam chat returned no response choices.")

            assistant_message = response.choices[0].message
            tool_calls = assistant_message.tool_calls or []
            if tool_calls:
                messages.append(assistant_message.model_dump(exclude_none=True))
                for function_call in tool_calls:
                    result = await self._execute_tool(function_call, event_queue)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": function_call.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    })
                continue

            answer = (assistant_message.content or "").strip()
            if not answer:
                raise RuntimeError("Sarvam chat returned an empty assistant response.")

            messages.append({"role": "assistant", "content": answer})
            conversation[:] = messages
            await event_queue.put({"type": "gemini", "text": answer})

            tts_options = {
                "text": answer,
                "language_code": self.tts_language,
                "model": self.tts_model,
                "speech_sample_rate": 24000,
                "output_audio_codec": "linear16",
            }
            if self.tts_speaker:
                tts_options["speaker"] = self.tts_speaker

            async for audio_chunk in self.client.text_to_speech.convert_stream(**tts_options):
                await self._invoke_callback(audio_output_callback, audio_chunk)

            await event_queue.put({"type": "turn_complete"})
            return

        raise RuntimeError("Sarvam chat exceeded the maximum number of tool-call rounds.")

    async def start_session(
        self,
        audio_input_queue: asyncio.Queue,
        text_input_queue: asyncio.Queue,
        audio_output_callback,
        audio_interrupt_callback=None,
        dynamic_system_prompt: Optional[str] = None,
    ):
        active_prompt = dynamic_system_prompt or self.system_prompt
        conversation = [{"role": "system", "content": active_prompt}]
        event_queue = asyncio.Queue()
        turn_queue = asyncio.Queue()

        async def send_audio(session):
            try:
                while True:
                    chunk = await audio_input_queue.get()
                    if chunk is None:
                        return
                    await session.send_realtime_audio_input(
                        RealtimeAudioInput(
                            audio=base64.b64encode(bytes(chunk)).decode("ascii")
                        )
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logfire.error("Sarvam audio send failed", error=str(exc), traceback=traceback.format_exc())
                await event_queue.put({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
                await event_queue.put(None)

        async def send_text():
            try:
                while True:
                    text = await text_input_queue.get()
                    if text is None:
                        return
                    await turn_queue.put({"type": "user_text", "text": str(text)})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logfire.error("Sarvam text input failed", error=str(exc), traceback=traceback.format_exc())
                await event_queue.put({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
                await event_queue.put(None)

        async def receive_transcripts(session):
            try:
                while True:
                    response = await session.recv()
                    event_type = getattr(response, "event", None)
                    if event_type == "transcript.final" and response.text.strip():
                        await turn_queue.put({"type": "user_text", "text": response.text})
                    elif event_type == "vad.speech_start":
                        await turn_queue.put({"type": "interrupt"})
                    elif event_type == "error":
                        message = getattr(response, "message", str(response))
                        raise RuntimeError(f"Sarvam speech recognition error: {message}")
                    elif event_type == "session_end":
                        return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logfire.error("Sarvam STT receive failed", error=str(exc), traceback=traceback.format_exc())
                await event_queue.put({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
            finally:
                await turn_queue.put(None)
                await event_queue.put(None)

        async def coordinate_turns():
            response_task = None
            try:
                while True:
                    item = await turn_queue.get()
                    if item is None:
                        return
                    if item["type"] == "interrupt":
                        if response_task and not response_task.done():
                            response_task.cancel()
                            await asyncio.gather(response_task, return_exceptions=True)
                            response_task = None
                            if audio_interrupt_callback:
                                await self._invoke_callback(audio_interrupt_callback)
                            await event_queue.put({"type": "interrupted"})
                        continue

                    if response_task and not response_task.done():
                        response_task.cancel()
                        await asyncio.gather(response_task, return_exceptions=True)
                        if audio_interrupt_callback:
                            await self._invoke_callback(audio_interrupt_callback)
                        await event_queue.put({"type": "interrupted"})

                    text = item["text"].strip()
                    if not text:
                        continue
                    await event_queue.put({"type": "user", "text": text})
                    await event_queue.put({
                        "type": "interaction_status",
                        "status": "IN_PROGRESS",
                    })

                    async def run_turn(user_text: str):
                        try:
                            await self._complete_turn(
                                user_text,
                                conversation,
                                event_queue,
                                audio_output_callback,
                            )
                            await event_queue.put({
                                "type": "interaction_status",
                                "status": "REQUIRES_ACTION",
                            })
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logfire.error(
                                "Sarvam voice turn failed",
                                error_type=type(exc).__name__,
                                error=str(exc),
                                traceback=traceback.format_exc(),
                            )
                            await event_queue.put({
                                "type": "error",
                                "error": f"{type(exc).__name__}: {exc}",
                            })

                    response_task = asyncio.create_task(run_turn(text))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logfire.error(
                    "Sarvam turn coordination failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    traceback=traceback.format_exc(),
                )
                await event_queue.put({
                    "type": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                await event_queue.put(None)
            finally:
                if response_task and not response_task.done():
                    response_task.cancel()
                    await asyncio.gather(response_task, return_exceptions=True)

        logfire.info(
            "Connecting to Sarvam voice services",
            stt_model=self.stt_model,
            chat_model=self.model,
            tts_model=self.tts_model,
        )
        try:
            async with self.client.speech_to_text_realtime_streaming.connect(
                language_code=self.stt_language,
                model=self.stt_model,
                encoding="linear16",
                sample_rate=str(self.input_sample_rate),
                endpointing="vad",
                api_subscription_key=self.api_key,
            ) as stt_session:
                tasks = [
                    asyncio.create_task(send_audio(stt_session)),
                    asyncio.create_task(send_text()),
                    asyncio.create_task(receive_transcripts(stt_session)),
                    asyncio.create_task(coordinate_turns()),
                ]
                try:
                    while True:
                        event = await event_queue.get()
                        if event is None:
                            break
                        if event.get("type") == "error":
                            yield event
                            break
                        yield event
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as exc:
            logfire.error(
                "Sarvam voice session failed",
                error_type=type(exc).__name__,
                error=str(exc),
                traceback=traceback.format_exc(),
            )
            raise
        finally:
            logfire.info("Sarvam voice session closed")
