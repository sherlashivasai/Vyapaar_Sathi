# Merchant AI

A real-time, multimodal merchant assistant built with FastAPI and the Gemini Live API. The browser app supports live audio conversations, typed messages, and optional camera or screen input. An optional Twilio integration connects phone calls to the assistant. The project also includes utilities for loading and analyzing merchant settlement reports.

## Features

- Low-latency, two-way voice conversations through a browser.
- Optional text, camera, and screen-sharing input.
- Optional inbound and outbound phone calls through Twilio.
- CSV and PDF settlement-report ingestion into a local SQLite database.
- A data-analysis tool for querying settlement data.

## Requirements

- Python 3.13 or newer.
- A Gemini API key.
- A modern browser with microphone access. Camera and screen sharing are optional.
- `uv` is recommended for installing the locked project dependencies.

## Setup

1. Clone the repository and change into its directory.
2. Install dependencies:

   ```powershell
   uv sync
   ```

3. Create a `.env` file in the project root and set your Gemini API key:

   ```dotenv
   GEMINI_API_KEY=your_gemini_api_key
   ```

   `MODEL` is optional; the application defaults to `gemini-3.8-live`.

4. Start the server from the project root:

   ```powershell
   uv run uvicorn main:app --host 0.0.0.0 --port 8000
   ```

5. Open [http://localhost:8000](http://localhost:8000), select **Connect**, and grant microphone access. Start the microphone to speak. Camera, screen sharing, and typed messages are available in the connected interface.

## Optional Twilio phone integration

To enable phone calls, add these values to `.env`:

```dotenv
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_APP_HOST=your_public_host
```

`TWILIO_APP_HOST` should be the publicly reachable host serving the app, without a URL scheme. Configure your Twilio number to send inbound calls to `POST /twilio/inbound`. The outbound endpoint is `POST /twilio/outbound` and accepts `to_number` and `from_number` as E.164 query parameters. Twilio Media Streams require a public, TLS-enabled WebSocket endpoint.

## Settlement data

Settlement CSV files are in `data/`. The ingestion utility reads CSV or PDF reports that match the expected settlement schema and inserts them into SQLite while skipping duplicate transaction keys.

Run the bundled CSV ingestion workflow from the project root:

```powershell
uv run python -m src.tools.data_ingestion
```

This writes to `payment_data.db`, which is local runtime data and is excluded from version control. The analysis utilities expect a settlement table named `merchant_settlement_reports_for_Nov-06_to_Nov-11`.

## Project layout

```text
main.py                         FastAPI app, browser WebSocket, and Twilio endpoints
twilio_handler.py               Twilio Media Streams integration
src/voice_agent.py              Gemini Live session and event handling
src/tools/                      Settlement ingestion and analysis utilities
src/frontend/                   Browser interface and media client
data/                           Settlement report CSV inputs
```

## Notes

- Run commands from the repository root; the app serves frontend assets from `src/frontend`.
- Keep API keys in `.env`; do not commit secrets.
- Phone integration is optional; the browser application only requires `GEMINI_API_KEY`.