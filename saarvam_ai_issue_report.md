# Saarvam AI migration and remaining issue report

## Scope
This repository is now aimed at a Saarvam-only model setup for the live merchant assistant. The working backend no longer depends on the Google Gemini path for normal runtime use, but some legacy references and cleanup items remain.

## Executive summary
The project has moved from a mixed Gemini/Saarvam prototype to a cleaner Saarvam-first structure.

Current active stack:

- FastAPI + WebSocket backend in [main.py](./main.py)
- live voice runtime in [src/voice_agent_sarvam.py](./src/voice_agent_sarvam.py)
- shared SQLite database in [src/db.py](./src/db.py)
- merchant tools in [src/tools](./src/tools)
- Logfire for runtime observability

The main improvement is that the active runtime is now aligned with the Saarvam environment variables and model path. The major remaining problem is not a broken Gemini runtime anymore; it is residual cleanup and product polish.

## Current state of the model stack

### 1) Active runtime is Saarvam-based
Evidence:

- [main.py](./main.py) imports `SarvamLive` from `src.voice_agent_sarvam`
- environment variables use `SARVAM_API_KEY`, `SARVAM_MODEL`, `STT`, `TTS`, and `SARVAM_TTS_SPEAKER`
- the live session is created with the Sarvam client instead of the older Google client path

Status: good for the requirement that the app use Saarvam-only models.

### 2) Older compatibility shim remains but is not the active path
File: [src/voice_agent.py](./src/voice_agent.py)

This file still exists as a compatibility compatibility layer and aliases the old `GeminiLive` name to the Saarvam implementation. This is intentionally non-active and should not be treated as a live runtime dependency.

Status: acceptable as a compatibility bridge, but should be considered legacy support rather than the canonical implementation.

## Current issues still present

### Issue 1: Not all user-facing strings were sanitized
Files:

- [src/frontend](./src/frontend)
- [README.md](./README.md)
- some older docs and comments

There are still a few front-end and documentation strings that reflect the previous Gemini-era branding or naming.

Impact:

- UI and onboarding still look partially legacy
- product polish remains incomplete even though runtime behavior is now aligned with Saarvam

Severity: Medium

### Issue 2: Some legacy code comments still contain old vendor references
File: [src/tools/execute_data_analysis.py](./src/tools/execute_data_analysis.py)

This file still contains a historical Groq/Gemini demo block and old example comments, even though the active runtime does not use it as a live LLM integration path.

Impact:

- confusing for developers reading the file
- creates ambiguity about whether the app still depends on Groq or Gemini at analysis time

Severity: Medium

### Issue 3: The repository still contains prototype/demo data and configuration
Files:

- [main.py](./main.py)
- [src/tools/khata_ledger.py](./src/tools/khata_ledger.py)
- [src/prompts.py](./src/prompts.py)

The merchant profile and customer records are still hardcoded. This is fine for a demo, but it is not yet a real multi-merchant or production-grade configuration model.

Impact:

- limited scalability
- no per-merchant config abstraction yet

Severity: Medium

### Issue 4: Database and tool-layer standardization is mostly complete, but not finalized
Files:

- [src/db.py](./src/db.py)
- [src/data_ingestion.py](./src/data_ingestion.py)
- [src/tools/get_shop_environment_intelligence.py](./src/tools/get_shop_environment_intelligence.py)
- [src/tools/khata_ledger.py](./src/tools/khata_ledger.py)

The app now centers on a single SQLite file, `vyapar_saathi.db`, which is a substantial improvement. However, the database still needs a final normalization pass for production-grade schema ownership and naming consistency.

Impact:

- easy to navigate now, but still prototype-shaped

Severity: Medium

### Issue 5: Old usage notes still remain in historical artifacts
Files:

- [saarvam_ai_issue_report.md](./saarvam_ai_issue_report.md) (historical artifact)
- [work.md](./work.md) (project notes)
- [notes.ipynb](./notes.ipynb)

Some history still reflects earlier states of the project and should be treated as archival, not active runtime truth.

Impact:

- historical confusion if not clearly labeled

Severity: Low

## What was fixed in the current iteration

### A) Active runtime migration to Saarvam
The runtime path no longer depends on the old Google client path in the primary app flow.

### B) Duplicate business logic removed
The duplicate settlement-analysis implementation was removed and the canonical tool was kept in [src/tools/execute_data_analysis.py](./src/tools/execute_data_analysis.py).

### C) Business pulse feature removed
The `analyze_business_pulse` feature was deleted because it was not part of the narrowed set of live runtime tools and was a redundant extra.

### D) Database unification
The project is now centered on a single DB file, [vyapar_saathi.db](./vyapar_saathi.db), with the shared DB factory located in [src/db.py](./src/db.py).

### E) Observability coverage
Logfire is used for operational visibility around the ingestion, caching, and ledger flows.

## Current status summary

### Production readiness: partial
The app is much closer to the user’s requirement:

- active voice stack is Saarvam-based
- duplicate logic has been removed
- active DB flow is centralized
- backend is cleaner and easier to navigate

However, the project still needs final cleanup in the following areas:

- frontend name/branding cleanup
- stale docs and code comments cleanup
- production hardening of the merchant profile and schema configuration
- final end-to-end voice validation using actual Saarvam session startup

## Recommended next steps
1. Clean the frontend naming and strings to remove any Gemini-era branding.
2. Remove or reduce stale example comments in [src/tools/execute_data_analysis.py](./src/tools/execute_data_analysis.py).
3. Finalize the merchant profile abstraction so the app is not demo-only.
4. Validate all live voice flows with actual Saarvam session startup and tool calling.
5. Treat the historical notes as archival and keep only current runtime documentation in active user-facing docs.

## Conclusion
The app is no longer in the earlier broken mixed-model state. It is now substantially aligned with the Saarvam-only requirement, and the biggest remaining work is cleanup and polish rather than a major architectural fix.

5. Update the docs and env configuration to `SARVAM_API_KEY` and Saarvam model names only.
6. Run an end-to-end verification after the migration, including voice session startup, tool calls, and audio output.

## Final verdict
The codebase is functional as a Gemini prototype, but it is not yet compliant with a Saarvam-only requirement. The repository is mid-migration: it contains a Sarvam implementation, but the main app and supporting modules are still strongly coupled to Google Gemini.
