import os
import json
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

import httpx
import logfire
from sqlalchemy import Column, String, Float, DateTime, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from google import genai
from google.genai import types

Base = declarative_base()

# Refresh interval: 4 hours (in seconds)
CACHE_TTL_HOURS = 4
REFRESH_INTERVAL_SECONDS = CACHE_TTL_HOURS * 3600

# Initialize Google GenAI Client (uses GEMINI_API_KEY from environment)
genai_client = genai.Client()

WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 51: "Light drizzle", 61: "Slight rain", 63: "Moderate rain",
    65: "Heavy rain", 80: "Rain showers", 82: "Violent rain showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail"
}


# ==============================================================================
# 1. PERSISTENT CACHE DATABASE MODEL (SQLite)
# ==============================================================================

class EnvIntelligenceCache(Base):
    __tablename__ = "env_intelligence_cache"

    cache_key = Column(String, primary_key=True, index=True)  # "{store_location}::{shop_category}"
    store_location = Column(String, index=True)
    shop_category = Column(String)
    payload_json = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow)


engine = create_engine("sqlite:///./vyapar_saathi.db", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)

# Fast in-memory L1 cache layered on top of SQLite L2 cache
_MEMORY_CACHE: Dict[str, Dict[str, Any]] = {}


def _make_cache_key(store_location: str, shop_category: str) -> str:
    return f"{store_location.strip().lower()}::{shop_category.strip().lower()}"


# ==============================================================================
# 2. LIVE WEATHER + DYNAMIC LLM SEARCH GROUNDING ENGINE
# ==============================================================================

async def _fetch_open_meteo_weather(
    store_location: str,
    lat: Optional[float],
    lon: Optional[float],
    http_client: httpx.AsyncClient,
) -> Dict[str, Any]:
    """Fetches real-time weather, afternoon dip probability, and tomorrow's forecast."""
    try:
        if lat is None or lon is None:
            geo_resp = await http_client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": store_location.split(",")[0].strip(), "count": 1, "country": "IN"},
                timeout=3.0,
            )
            geo_data = geo_resp.json()
            if geo_data.get("results"):
                lat = geo_data["results"][0]["latitude"]
                lon = geo_data["results"][0]["longitude"]
            else:
                lat, lon = 17.4401, 78.3489  # Fallback: Gachibowli, Hyderabad

        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,precipitation,weather_code",
            "hourly": "temperature_2m,precipitation_probability,weather_code",
            "daily": "weather_code,temperature_2m_max,precipitation_sum,precipitation_probability_max",
            "timezone": "Asia/Kolkata",
            "forecast_days": 2,
        }
        resp = await http_client.get(url, params=params, timeout=4.0)
        data = resp.json()

        current = data.get("current", {})
        daily = data.get("daily", {})
        hourly = data.get("hourly", {})

        afternoon_rain_prob = max(hourly.get("precipitation_probability", [0] * 24)[13:18], default=0)
        afternoon_max_temp = max(hourly.get("temperature_2m", [30] * 24)[13:18], default=30)

        today_code = current.get("weather_code", 0)
        tomorrow_code = daily.get("weather_code", [0, 0])[1]
        tomorrow_rain_prob = daily.get("precipitation_probability_max", [0, 0])[1]

        return {
            "current_condition": WMO_CODES.get(today_code, "Normal"),
            "current_temp_c": current.get("temperature_2m"),
            "afternoon_window_1pm_to_5pm": {
                "max_temp_c": afternoon_max_temp,
                "rain_probability_pct": afternoon_rain_prob,
                "footfall_impact": (
                    "HIGH_WEATHER_DIP" if (afternoon_rain_prob > 50 or afternoon_max_temp > 40) else "NORMAL_WEATHER"
                ),
            },
            "tomorrow_forecast": {
                "condition": WMO_CODES.get(tomorrow_code, "Normal"),
                "max_temp_c": daily.get("temperature_2m_max", [30, 30])[1],
                "rain_probability_pct": tomorrow_rain_prob,
            },
        }
    except Exception as e:
        return {"error": f"Weather API error: {str(e)}", "footfall_impact": "UNKNOWN"}


async def _run_sub_llm_grounded_intelligence(store_location: str, shop_category: str) -> Dict[str, Any]:
    """
    Sub-LLM Worker: Uses Gemini 2.5 Flash + Google Search Grounding to dynamically
    select commodities based on `shop_category` and fetch real-time mandi prices,
    civic outages, protests, and festivals for `store_location`.
    """
    now_str = datetime.now().strftime("%A, %d %B %Y")

    prompt = f"""
    You are a hyper-local market intelligence analyst for a "{shop_category}" located in "{store_location}" on {now_str}.
    Use Google Search to gather real, current local information across these 4 areas:

    1. Dynamic Mandi Prices & Wholesale Trends (Based on Category: "{shop_category}"):
       - Dynamically pick the top 4 to 5 fast-moving wholesale commodities/items that a "{shop_category}" buys from the local mandi or wholesale market in/near {store_location}.
       - Find their current wholesale/mandi price, recent trend (PRICE_DROP, PRICE_SPIKE, or STABLE), and whether the shopkeeper should stock up today.
    2. Civic Outages:
       - Any scheduled or recent power cuts, electricity feeder maintenance, water supply shutdowns, or road/drainage work in or around {store_location}.
    3. Protests, Bandhs & Traffic Disruptions:
       - Any local protests, dharnas, rallies, strikes/bandhs, or major traffic diversions affecting street footfall in {store_location}.
    4. Festivals & Local Demand Radar:
       - Any upcoming local festivals, auspicious days, temple events, or payday surges in the next 7 days in {store_location}, and what specific items a "{shop_category}" should stock for it.

    Return ONLY a valid raw JSON object (no markdown fences) with this exact structure:
    {{
      "dynamically_selected_items": ["item1", "item2", "item3", "item4"],
      "mandi_prices_and_trends": [
        {{
          "item": "Commodity Name",
          "current_wholesale_price": "₹XX/kg or unit",
          "trend": "PRICE_DROP | PRICE_SPIKE | STABLE",
          "stocking_advice": "1-line actionable buying advice"
        }}
      ],
      "civic_outages": ["concise outage alert 1", ...],
      "protests_and_disruptions": ["concise protest/traffic alert 1", ...],
      "festivals_and_events": [
        {{
          "event_name": "Name of festival/event",
          "date_or_timing": "When it happens",
          "items_to_stock": ["item A", "item B"]
        }}
      ],
      "top_actionable_suggestions": [
        "1-sentence high-impact business tip for today/tomorrow",
        "2nd 1-sentence business tip"
      ]
    }}
    """

    try:
        response = await genai_client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.1,
            ),
        )

        raw_text = (response.text or "{}").strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:].strip()

        parsed = json.loads(raw_text)

        # Attach live search citations
        sources = []
        if response.candidates and response.candidates[0].grounding_metadata:
            chunks = response.candidates[0].grounding_metadata.grounding_chunks or []
            for c in chunks:
                if c.web and c.web.title:
                    sources.append({"title": c.web.title, "uri": c.web.uri})
        parsed["grounded_sources"] = sources[:5]
        return parsed

    except Exception as e:
        logfire.error("Sub-LLM Grounding failed", error=str(e))
        return {
            "dynamically_selected_items": ["Onion", "Tomato", "Sugar", "Edible Oil"],
            "mandi_prices_and_trends": [],
            "civic_outages": [f"No major power or water outages reported in {store_location} right now."],
            "protests_and_disruptions": ["Normal street traffic; no protests or bandhs reported."],
            "festivals_and_events": [],
            "top_actionable_suggestions": ["Maintain buffer stock of daily fast-moving essentials."],
            "grounding_error": str(e),
        }


# ==============================================================================
# 3. CACHE UPDATER & 4-HOUR ASYNC BACKGROUND LOOP
# ==============================================================================

async def compute_and_cache_env_intelligence(
    store_location: str = "Gachibowli, Hyderabad, Telangana",
    shop_category: str = "Kirana & General Store",
    lat: Optional[float] = 17.4401,
    lon: Optional[float] = 78.3489,
) -> Dict[str, Any]:
    """
    Executes the live Weather API + Sub-LLM Search Grounding in parallel and writes
    the result to both In-Memory L1 cache and SQLite L2 cache.
    """
    cache_key = _make_cache_key(store_location, shop_category)
    logfire.info("Refreshing 4-hour Environment Intelligence cache", cache_key=cache_key)

    async with httpx.AsyncClient() as http_client:
        weather_data, llm_intel = await asyncio.gather(
            _fetch_open_meteo_weather(store_location, lat, lon, http_client),
            _run_sub_llm_grounded_intelligence(store_location, shop_category),
        )

    now_utc = datetime.utcnow()
    payload = {
        "status": "ok",
        "retryable": False,
        "cache_metadata": {
            "last_refreshed_utc": now_utc.isoformat(),
            "next_refresh_utc": (now_utc + timedelta(hours=CACHE_TTL_HOURS)).isoformat(),
            "refresh_interval_hours": CACHE_TTL_HOURS,
        },
        "store_location": store_location,
        "shop_category": shop_category,
        "live_weather": weather_data,
        "local_intelligence": llm_intel,
        "message": (
            f"Loaded local weather, dynamic {shop_category} mandi trends, civic outages, "
            f"protests, and festival alerts for {store_location}."
        ),
    }

    # Save to L1 Memory Cache
    _MEMORY_CACHE[cache_key] = {
        "updated_at": now_utc,
        "payload": payload,
    }

    # Save to L2 SQLite Cache
    db = SessionLocal()
    try:
        row = db.query(EnvIntelligenceCache).filter_by(cache_key=cache_key).first()
        if not row:
            row = EnvIntelligenceCache(
                cache_key=cache_key,
                store_location=store_location,
                shop_category=shop_category,
                payload_json=json.dumps(payload),
                updated_at=now_utc,
            )
            db.add(row)
        else:
            row.payload_json = json.dumps(payload)
            row.updated_at = now_utc
        db.commit()
    finally:
        db.close()

    return payload


async def start_4hour_env_refresh_scheduler(merchants_to_track: Optional[List[Dict[str, Any]]] = None):
    """
    Background worker loop: Runs automatically every 4 hours to pre-fetch and cache
    environment intelligence for all registered merchants so live calls have 0ms wait time.
    Call `asyncio.create_task(start_4hour_env_refresh_scheduler())` inside FastAPI lifespan!
    """
    if not merchants_to_track:
        merchants_to_track = [
            {
                "store_location": "Gachibowli, Hyderabad, Telangana",
                "shop_category": "Kirana & General Store",
                "lat": 17.4401,
                "lon": 78.3489,
            }
        ]

    while True:
        try:
            for m in merchants_to_track:
                await compute_and_cache_env_intelligence(
                    store_location=m["store_location"],
                    shop_category=m.get("shop_category", "Kirana & General Store"),
                    lat=m.get("lat"),
                    lon=m.get("lon"),
                )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logfire.error("4-hour env scheduler error", error=str(e))

        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


# ==============================================================================
# 4. MAIN VOICE AGENT TOOL (Instant Cache Read + Non-Blocking Declaration)
# ==============================================================================

async def shop_env_intelligence(
    store_location: str = "Gachibowli, Hyderabad, Telangana",
    shop_category: str = "Kirana & General Store",
    force_live_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Tool called by the main Voice Agent (`gemini-3.8-live`).
    Instantly returns the latest 4-hour cached output from the background Sub-LLM.
    If the cache is older than 4 hours or missing, triggers a live refresh automatically.
    """
    cache_key = _make_cache_key(store_location, shop_category)
    now_utc = datetime.utcnow()
    max_age = timedelta(hours=CACHE_TTL_HOURS)

    # 1. Check L1 In-Memory Cache (< 1ms)
    if not force_live_refresh and cache_key in _MEMORY_CACHE:
        entry = _MEMORY_CACHE[cache_key]
        if (now_utc - entry["updated_at"]) < max_age:
            return entry["payload"]

    # 2. Check L2 SQLite Cache (< 5ms)
    if not force_live_refresh:
        db = SessionLocal()
        try:
            row = db.query(EnvIntelligenceCache).filter_by(cache_key=cache_key).first()
            if row and (now_utc - row.updated_at) < max_age:
                payload = json.loads(row.payload_json)
                _MEMORY_CACHE[cache_key] = {"updated_at": row.updated_at, "payload": payload}
                return payload
        finally:
            db.close()

    # 3. Cache Miss or Expired (> 4 hours old) -> Run Sub-LLM now and cache for next 4 hours
    return await compute_and_cache_env_intelligence(
        store_location=store_location,
        shop_category=shop_category,
    )


SHOP_ENV_TOOL_DECLARATION = types.FunctionDeclaration(
    name="shop_env_intelligence",
    description=(
        "Fetches local weather, dynamic category-specific mandi wholesale prices & trends, "
        "civic outages (power/water cuts), protests/traffic disruptions, and upcoming festivals "
        "for the merchant's store location. Backed by a 4-hour pre-computed intelligence cache for instant response."
    ),
    behavior="NON_BLOCKING",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "store_location": types.Schema(
                type="STRING",
                description="The merchant's registered store location (e.g., 'Gachibowli, Hyderabad, Telangana').",
            ),
            "shop_category": types.Schema(
                type="STRING",
                description="The merchant's store category (e.g., 'Kirana & General Store', 'Vegetable Vendor', 'Bakery & Tea Stall') used to dynamically select mandi items.",
            ),
        },
        required=["store_location"],
    ),
)

SHOP_ENV_TOOL_MAPPING = {
    "shop_env_intelligence": shop_env_intelligence,
}