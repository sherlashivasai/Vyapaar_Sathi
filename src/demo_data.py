"""Reusable sample merchant profile and idempotent demo ledger seeding."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import logfire

from src.db import APP_DB_URL, SessionLocal, init_db
from src.models import KhataLedger

DEMO_MERCHANT_PROFILE: dict[str, Any] = {
    "merchant_id": "M123456",
    "merchant_name": "Mahesh",
    "shop_name": "mahesh Kirana Store",
    "shop_category": "Kirana & General Store",
    "items_available": [
        "Rice", "Wheat Flour", "Sugar", "Cooking Oil", "Onions",
        "Potatoes", "Ginger", "Garlic", "Chillies", "Spices",
        "Snacks", "Biscuits", "Soft Drinks",
    ],
    "store_location": "vegetable market, Mancherial, Telangana",
    "lat": 18.8792,
    "lon": 79.4491,
    "preferred_language": "Telglish (Telugu + simple English)",
    "existing_customers": ["Raju", "Lakshmi", "Suresh"],
    "shadow_loan_limit": 50000,
}


def seed_demo_data() -> int:
    """Insert missing sample ledger records for the demo merchant; safe to rerun."""
    merchant_id = DEMO_MERCHANT_PROFILE["merchant_id"]
    today = date.today()
    demo_rows = (
        {
            "name": "Raju",
            "phone_no": "9876500001",
            "amount": 1200.0,
            "date_given": today - timedelta(days=6),
            "promised_return_date": today - timedelta(days=1),
        },
        {
            "name": "Lakshmi",
            "phone_no": "9876500002",
            "amount": 850.0,
            "date_given": today - timedelta(days=4),
            "promised_return_date": today,
        },
        {
            "name": "Suresh",
            "phone_no": "9876500003",
            "amount": 450.0,
            "date_given": today - timedelta(days=2),
            "promised_return_date": today + timedelta(days=4),
        },
    )

    db = SessionLocal()
    try:
        existing_names = {
            name.lower()
            for (name,) in db.query(KhataLedger.name)
            .filter(KhataLedger.merchant_id == merchant_id)
            .all()
        }
        added = 0
        for row in demo_rows:
            if row["name"].lower() in existing_names:
                continue
            db.add(KhataLedger(merchant_id=merchant_id, **row))
            existing_names.add(row["name"].lower())
            added += 1
        db.commit()
        logfire.info(
            "Demo ledger data seeded",
            merchant_id=merchant_id,
            rows_added=added,
            database_url=APP_DB_URL,
        )
        return added
    except Exception:
        db.rollback()
        logfire.exception("Could not seed demo ledger data", merchant_id=merchant_id)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    logfire.configure(service_name="merchant-ai")
    init_db()
    print(f"Added {seed_demo_data()} missing demo ledger records.")
