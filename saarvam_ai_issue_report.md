# Saarvam AI prototype: current issue and readiness report

## Current architecture

- [main.py](main.py) runs FastAPI and the `/ws` voice endpoint.
- [src/voice_agent_sarvam.py](src/voice_agent_sarvam.py) implements the active Saarvam chat/STT/TTS path.
- [src/demo_data.py](src/demo_data.py) is the single reusable home for sample merchant and khata fixture data.
- [src/db.py](src/db.py) owns the shared SQLite engine, session factory, schema initialization, and Logfire SQLAlchemy instrumentation.
- [src/models.py](src/models.py) centrally defines the `khata_ledger`, `env_intelligence_cache`, and `merchant_settlement_reports` tables.
- [src/data_ingestion.py](src/data_ingestion.py) validates and ingests settlement CSV/PDF data into the fixed shared settlement table.
- Active runtime dependencies no longer include the Gemini/Groq SDK packages. Historical compatibility names remain in [src/voice_agent.py](src/voice_agent.py), the frontend, and the notebook; they are not active model integrations.

## Completed cleanup

1. Consolidated demo profile and sample ledger fixtures in [src/demo_data.py](src/demo_data.py). The seed is idempotent and uses the same merchant ID as the app.
2. Centralized ORM models and table creation, and routed application persistence through the single [vyapar_saathi.db](vyapar_saathi.db) database.
3. Routed data ingestion through the shared engine and centralized settlement schema; duplicate transaction keys are ignored.
4. Corrected the settlement tool schema to accept `python_code` as a string and return captured analysis output.
5. Added the Logfire SQLAlchemy instrumentation extra and constrained SQLAlchemy to the version range supported by that instrumentation.
6. Corrected prompt/tool instructions so the agent no longer claims removed features such as business-pulse analysis, loan eligibility changes, or payment reminders.
7. Changed local-intelligence instructions to avoid presenting unverified prices or civic events as confirmed facts.

## Remaining issues

### High: model-provided Python is executed in-process

[src/tools/execute_data_analysis.py](src/tools/execute_data_analysis.py) executes Python supplied through the analysis tool. This is acceptable only for a trusted, isolated local prototype. Replace it with constrained, predefined analytics before exposing the app to untrusted users or running it on a shared/production host.

### Medium: frontend still carries Gemini-era behavior

The UI, client filename, CSS message class, and initial greeting still say Gemini. The backend preserves the `gemini` response event name for compatibility. Camera/image data is placed in a queue that is not passed to or consumed by `SarvamLive`, so camera and screen analysis do not currently work end to end.

### Medium: local market intelligence is not live-grounded

Open-Meteo provides weather data. The Sarvam-generated market/event text has no search/retrieval source, so prices, civic alerts, protests, and event dates cannot be treated as verified. The prompt and fallback behavior now explicitly avoid asserting unsupported facts; a verified source integration is still needed for those claims.

### Medium: live provider/audio path not verified

Saarvam environment variable presence was checked without revealing values, but no real STT/chat/TTS request was made. Provider credentials, selected model availability, audio encoding, and full WebSocket conversation behavior remain unverified.

### Low: test automation is absent

No automated test files were found. Compile, database, ingestion, page/static, analysis, and khata smoke checks passed, but regression coverage is still needed.

## Validation performed

- `uv lock --check`
- `uv run python -m compileall main.py src`
- Read-only inspection confirmed the unified database contains the settlement table and its existing rows.
- Database initialization created all three expected application tables.
- Demo seeding produced three records once and zero additional records on the next run.
- FastAPI root and a frontend static asset returned HTTP 200.
- Settlement analysis returned the expected row count (279 at test time).
- Demo khata lookup returned all three sample customers.
- Re-ingesting an already-loaded settlement CSV inserted zero duplicate records.

## Verdict

The project is **ready for local prototype demonstrations of its app shell and database-backed features**. It is **not yet verified as an end-to-end Saarvam voice experience**, and it is **not production-ready** while in-process Python execution, ungrounded local-market claims, and incomplete camera support remain.
