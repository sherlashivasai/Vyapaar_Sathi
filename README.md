# Merchant AI

A real-time, multimodal merchant assistant built with FastAPI and the Saarvam AI voice stack. The browser app supports live audio conversations, typed messages, and optional camera or screen input. The project also includes utilities for loading and analyzing merchant settlement reports.

## Features

- Low-latency, two-way voice conversations through a browser.
- Optional text, camera, and screen-sharing input.
- A browser disconnect control that ends the live voice session.
- CSV and PDF settlement-report ingestion into a local SQLite database.
- A data-analysis tool for querying settlement data.

## Requirements

- Python 3.13 or newer.
- A Saarvam AI API key in `.env`.
- A modern browser with microphone access. Camera and screen sharing are optional.
- `uv` is recommended for installing the locked project dependencies.

## Setup

1. Clone the repository and change into its directory.
2. Install dependencies:

   ```powershell
   uv sync
   ```

3. Create a `.env` file in the project root and set your Saarvam AI credentials:

   ```dotenv
   SARVAM_API_KEY=your_saarvam_api_key
   SARVAM_MODEL=sarvam-105b-conversations
   STT=saaras:v4
   TTS=bulbul:v4-flash
   SARVAM_TTS_SPEAKER=allie
   ```

4. Start the server from the project root:

   ```powershell
   uv run uvicorn main:app --host 0.0.0.0 --port 8000
   ```

5. Open [http://localhost:8000](http://localhost:8000), select **Connect**, and grant microphone access. Start the microphone to speak. Camera, screen sharing, and typed messages are available in the connected interface.

## Settlement data

Settlement CSV files are in `data/`. The ingestion utility reads CSV or PDF reports that match the expected settlement schema and inserts them into the shared SQLite database while skipping duplicate transaction keys.

Run the bundled CSV ingestion workflow from the project root:

```powershell
uv run python -m src.data_ingestion
```

All settlement, khata, and environment-intelligence tables are stored in the shared `vyapar_saathi.db` database at the repository root. The backend also emits structured Logfire traces for database writes, cache refreshes, and key merchant operations so the database can be inspected and monitored in one place.

## Project layout

```text
main.py                         FastAPI app and browser WebSocket
src/voice_agent_sarvam.py       Saarvam voice session and event handling
src/tools/                      Settlement ingestion and analysis utilities
src/frontend/                   Browser interface and media client
data/                           Settlement report CSV inputs
```

## Notes

- Run commands from the repository root; the app serves frontend assets from `src/frontend`.
- Keep API keys in `.env`; do not commit secrets.
- The browser application requires the Saarvam credentials from `.env`.