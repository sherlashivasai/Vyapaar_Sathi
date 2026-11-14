import asyncio
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from main import build_session_tool_mapping
from src.demo_data import DEMO_MERCHANT_PROFILE
from src.prompts import build_merchant_system_prompt
from src.tools.execute_data_analysis import execute_data_analysis
from src.tools.get_shop_environment_intelligence import (
    _fetch_open_meteo_weather,
    _run_sub_llm_grounded_intelligence,
)
from src.tools.khata_ledger import _parse_amount, _parse_date
from src.voice_agent import GeminiLive


class BackendFixTests(unittest.TestCase):
    def test_environment_tool_uses_merchant_coordinates_only_for_merchant_location(self):
        profile = {
            "merchant_id": "merchant-1",
            "store_location": "Mancherial, Telangana",
            "shop_category": "Kirana",
            "lat": 18.8756,
            "lon": 79.4591,
        }

        async def capture_tool_call(**kwargs):
            return kwargs

        with patch("main.get_shop_environment_intelligence", capture_tool_call):
            mapping = build_session_tool_mapping(profile)
            merchant_result = asyncio.run(
                mapping["get_shop_environment_intelligence"]()
            )
            other_location_result = asyncio.run(
                mapping["get_shop_environment_intelligence"](
                    store_location="Hyderabad, Telangana"
                )
            )

        self.assertEqual(merchant_result["lat"], profile["lat"])
        self.assertEqual(merchant_result["lon"], profile["lon"])
        self.assertIsNone(other_location_result["lat"])
        self.assertIsNone(other_location_result["lon"])

    def test_environment_tool_reports_unresolved_geolocation(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"results": []}

        class FakeClient:
            async def get(self, *args, **kwargs):
                return FakeResponse()

        result = asyncio.run(
            _fetch_open_meteo_weather(
                "Unknown location",
                None,
                None,
                FakeClient(),
            )
        )

        self.assertIn("error", result)
        self.assertIn("Could not find coordinates", result["error"])

    def test_environment_intelligence_uses_gemini_flash_json_response(self):
        captured = {}

        class FakeModels:
            async def generate_content(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(text='{"top_actionable_suggestions": ["Check local stock"]}')

        fake_client = SimpleNamespace(
            aio=SimpleNamespace(models=FakeModels())
        )
        with (
            patch(
                "src.tools.get_shop_environment_intelligence.gemini_client",
                fake_client,
            ),
            patch(
                "src.tools.get_shop_environment_intelligence.GEMINI_INTELLIGENCE_MODEL",
                "gemini-3.5-flash",
            ),
        ):
            result = asyncio.run(
                _run_sub_llm_grounded_intelligence("Hyderabad", "Kirana")
            )

        self.assertEqual(captured["model"], "gemini-3.5-flash")
        self.assertEqual(captured["config"].response_mime_type, "application/json")
        self.assertEqual(
            result["top_actionable_suggestions"],
            ["Check local stock"],
        )
        self.assertEqual(result["grounded_sources"], [])

    def test_invalid_dates_and_non_finite_amounts_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Use YYYY-MM-DD"):
            _parse_date("2026-99-99")
        with self.assertRaisesRegex(ValueError, "finite"):
            _parse_amount(float("nan"))

    def test_analysis_tool_schema_accepts_only_predefined_analytics(self):
        schema = execute_data_analysis.args_schema.model_json_schema()
        properties = schema["properties"]

        self.assertEqual(set(properties), {"analysis_type"})
        self.assertEqual(
            set(properties["analysis_type"]["enum"]),
            {"summary", "daily_totals", "by_instrument", "fees_and_taxes"},
        )

    def test_analysis_returns_explicit_database_error(self):
        with (
            patch(
                "src.tools.execute_data_analysis.pd.read_sql",
                side_effect=RuntimeError("database unavailable"),
            ),
            patch("src.tools.execute_data_analysis.logfire.exception"),
        ):
            result = execute_data_analysis.invoke({"analysis_type": "summary"})

        self.assertEqual(result["status"], "error")
        self.assertIn("database unavailable", result["message"])

    def test_analysis_summary_uses_database_rows(self):
        settlement_rows = pd.DataFrame(
            {
                "amount": [10, 20],
                "fee": [1, 2],
                "igst": [0, 0],
                "cgst": [0, 0],
                "sgst": [0, 0],
                "settlementdate": ["2026-11-13", "2026-11-14"],
                "instrument": ["UPI", "UPI"],
            }
        )
        with patch(
            "src.tools.execute_data_analysis.pd.read_sql",
            return_value=settlement_rows,
        ):
            result = execute_data_analysis.invoke({"analysis_type": "summary"})

        self.assertEqual(result["status"], "ok")
        self.assertIn("Settlement records: 2", result["result"])
        self.assertIn("Total amount: 30.00", result["result"])

    def test_plain_text_tool_results_use_frontend_result_field(self):
        self.assertEqual(
            GeminiLive._normalize_tool_result("Settlement total: 30"),
            {"result": "Settlement total: 30"},
        )

    def test_gemini_emits_frontend_compatible_tool_events(self):
        function_call = SimpleNamespace(
            name="echo",
            id="call-1",
            args={"text": "hello"},
        )
        response = SimpleNamespace(
            session_resumption_update=None,
            server_content=None,
            tool_call=SimpleNamespace(function_calls=[function_call]),
        )

        class FakeSession:
            has_sent_call = False

            async def receive(self):
                if not self.has_sent_call:
                    self.has_sent_call = True
                    yield response
                else:
                    await asyncio.Future()

            async def send_tool_response(self, **kwargs):
                return None

        session = FakeSession()

        class FakeLive:
            @asynccontextmanager
            async def connect(self, **kwargs):
                yield session

        async def echo_tool(**kwargs):
            return "hello"

        agent = GeminiLive.__new__(GeminiLive)
        agent.model = "test"
        agent.client = SimpleNamespace(aio=SimpleNamespace(live=FakeLive()))
        agent.tool_mapping = {"echo": echo_tool}
        agent._build_live_config = lambda: None

        async def get_events():
            stream = agent.start_session(
                audio_input_queue=asyncio.Queue(),
                video_input_queue=asyncio.Queue(),
                text_input_queue=asyncio.Queue(),
                audio_output_callback=lambda data: None,
            )
            try:
                return await stream.__anext__(), await stream.__anext__()
            finally:
                await stream.aclose()

        start_event, result_event = asyncio.run(get_events())

        self.assertEqual(start_event["type"], "tool_start")
        self.assertEqual(result_event["type"], "tool_call")
        self.assertEqual(result_event["result"], {"result": "hello"})

    def test_gemini_receive_failure_is_forwarded_to_client(self):
        class FakeSession:
            async def receive(self):
                raise RuntimeError("upstream receive failed")
                yield None

        class FakeLive:
            @asynccontextmanager
            async def connect(self, **kwargs):
                yield FakeSession()

        agent = GeminiLive.__new__(GeminiLive)
        agent.model = "test"
        agent.client = SimpleNamespace(aio=SimpleNamespace(live=FakeLive()))
        agent.tool_mapping = {}
        agent._build_live_config = lambda: None

        async def first_event():
            stream = agent.start_session(
                audio_input_queue=asyncio.Queue(),
                video_input_queue=asyncio.Queue(),
                text_input_queue=asyncio.Queue(),
                audio_output_callback=lambda data: None,
            )
            try:
                return await stream.__anext__()
            finally:
                await stream.aclose()

        error_event = asyncio.run(first_event())

        self.assertEqual(error_event["type"], "error")
        self.assertIn("upstream receive failed", error_event["error"])

    def test_telugu_profile_selects_telugu_greeting(self):
        prompt = build_merchant_system_prompt(DEMO_MERCHANT_PROFILE)

        self.assertIn("Namaskaram", prompt)


if __name__ == "__main__":
    unittest.main()
