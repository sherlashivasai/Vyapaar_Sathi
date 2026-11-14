import difflib
import math
from datetime import date, datetime
from typing import Dict, Any, Optional, List

import logfire

from src.db import APP_DB_URL, SessionLocal
from src.models import KhataLedger


# ==============================================================================
# 1. DATABASE TABLE & SEEDER
# ==============================================================================

# ==============================================================================
# 2. HELPER FUNCTIONS
# ==============================================================================

def _parse_date(date_str: Optional[str], fallback: Optional[date] = None) -> Optional[date]:
    if not date_str:
        return fallback
    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid date '{date_str}'. Use YYYY-MM-DD.") from exc


def _parse_amount(amount: float, *, allow_zero: bool = False) -> float:
    try:
        parsed_amount = float(amount)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Amount must be a finite number.") from exc

    if not math.isfinite(parsed_amount) or parsed_amount < 0:
        raise ValueError("Amount must be a finite, non-negative number.")
    if not allow_zero and parsed_amount == 0:
        raise ValueError("Amount must be greater than zero.")
    return parsed_amount


def _find_customer(db, merchant_id: str, spoken_name: str) -> Optional[KhataLedger]:
    if not spoken_name:
        return None

    clean = (
        spoken_name.lower()
        .replace("bhai", "")
        .replace("garu", "")
        .replace("anna", "")
        .replace("ji", "")
        .strip()
    )
    rows = db.query(KhataLedger).filter_by(merchant_id=merchant_id).all()
    lookup = {r.name.lower(): r for r in rows}

    for k, obj in lookup.items():
        if clean == k or k in clean:
            return obj

    close = difflib.get_close_matches(clean, list(lookup.keys()), n=1, cutoff=0.65)
    return lookup[close[0]] if close else None


def _build_due_notifications(db, merchant_id: str) -> List[Dict[str, Any]]:
    """Checks for any customer whose promised_return_date is today or past due."""
    today = date.today()
    due_rows = (
        db.query(KhataLedger)
        .filter(
            KhataLedger.merchant_id == merchant_id,
            KhataLedger.amount > 0,
            KhataLedger.promised_return_date.isnot(None),
            KhataLedger.promised_return_date <= today,
        )
        .order_by(KhataLedger.promised_return_date.asc())
        .all()
    )

    notifications = []
    for r in due_rows:
        days_overdue = (today - r.promised_return_date).days
        notifications.append({
            "customer_name": r.name,
            "phone_no": r.phone_no or "Not saved",
            "amount_due": r.amount,
            "date_given": str(r.date_given) if r.date_given else None,
            "promised_return_date": str(r.promised_return_date),
            "days_overdue": days_overdue,
            "status": "DUE_TODAY" if days_overdue == 0 else f"OVERDUE_{days_overdue}_DAYS",
            "notification_banner": (
                f"Recovery Reminder: Collect ₹{r.amount:.0f} from {r.name} "
                f"(Promised return date: {r.promised_return_date})"
            ),
        })
    return notifications


# ==============================================================================
# 3. MAIN TOOL FUNCTION (LOG, CHECK, UPDATE)
# ==============================================================================

async def manage_khata_ledger(
    action: str,
    name: Optional[str] = None,
    phone_no: Optional[str] = None,
    amount: Optional[float] = None,
    date_given: Optional[str] = None,
    promised_return_date: Optional[str] = None,
    merchant_id: str = "M123456",
) -> Dict[str, Any]:
    """
    Logs, checks, or updates customer credit (udhaar) details in the merchant's Khata ledger.
    """
    logfire.info(
        "Khata ledger action requested",
        action=action,
        merchant_id=merchant_id,
        customer_name=name,
        database_url=APP_DB_URL,
    )
    db = SessionLocal()
    try:
        act = action.upper().strip()
        today = date.today()

        # ----------------------------------------------------------------------
        # ACTION 1: CHECK (Single customer or entire pending ledger)
        # ----------------------------------------------------------------------
        if act == "CHECK":
            notifications = _build_due_notifications(db, merchant_id)

            if not name:
                rows = (
                    db.query(KhataLedger)
                    .filter(KhataLedger.merchant_id == merchant_id, KhataLedger.amount > 0)
                    .order_by(KhataLedger.amount.desc())
                    .all()
                )
                all_records = [
                    {
                        "name": r.name,
                        "phone_no": r.phone_no,
                        "amount": r.amount,
                        "date_given": str(r.date_given) if r.date_given else None,
                        "promised_return_date": str(r.promised_return_date) if r.promised_return_date else None,
                    }
                    for r in rows
                ]
                total_udhaar = sum(r.amount for r in rows)
                return {
                    "status": "ok",
                    "retryable": False,
                    "action_performed": "CHECK_ALL",
                    "total_pending_udhaar": total_udhaar,
                    "customer_record": all_records,
                    "due_notifications": notifications,
                    "message": (
                        f"Found {len(all_records)} active credit accounts totaling ₹{total_udhaar:.0f}. "
                        f"There are {len(notifications)} customers due for collection today."
                    ),
                }

            cust = _find_customer(db, merchant_id, name)
            if not cust:
                return {
                    "status": "ok",
                    "retryable": False,
                    "action_performed": "CHECK_SINGLE",
                    "customer_record": None,
                    "due_notifications": notifications,
                    "message": f"No credit record found for '{name}'.",
                }

            return {
                "status": "ok",
                "retryable": False,
                "action_performed": "CHECK_SINGLE",
                "customer_record": {
                    "name": cust.name,
                    "phone_no": cust.phone_no,
                    "amount": cust.amount,
                    "date_given": str(cust.date_given) if cust.date_given else None,
                    "promised_return_date": str(cust.promised_return_date) if cust.promised_return_date else None,
                },
                "due_notifications": notifications,
                "message": (
                    f"{cust.name} has ₹{cust.amount:.0f} pending credit given on {cust.date_given}, "
                    f"promised back on {cust.promised_return_date or 'not set'}."
                ),
            }

        # Validate `name` for LOG and UPDATE
        if not name:
            return {
                "status": "error",
                "retryable": False,
                "message": "Customer 'name' is required for LOG and UPDATE actions.",
            }

        cust = _find_customer(db, merchant_id, name)
        parsed_given = _parse_date(date_given, fallback=today)
        parsed_promised = _parse_date(promised_return_date, fallback=None)

        # ----------------------------------------------------------------------
        # ACTION 2: LOG (Add new credit entry or add to existing customer's balance)
        # ----------------------------------------------------------------------
        if act == "LOG":
            if amount is None:
                return {
                    "status": "error",
                    "retryable": False,
                    "message": "A positive 'amount' is required to log credit.",
                }

            added_amt = _parse_amount(amount)
            if not cust:
                cust = KhataLedger(
                    merchant_id=merchant_id,
                    name=name.strip().title(),
                    phone_no=phone_no,
                    amount=added_amt,
                    date_given=parsed_given,
                    promised_return_date=parsed_promised,
                )
                db.add(cust)
            else:
                cust.amount += added_amt
                cust.date_given = parsed_given
                if phone_no:
                    cust.phone_no = phone_no
                if parsed_promised:
                    cust.promised_return_date = parsed_promised

            db.commit()
            db.refresh(cust)
            notifications = _build_due_notifications(db, merchant_id)

            return {
                "status": "ok",
                "retryable": False,
                "action_performed": "LOG",
                "customer_record": {
                    "name": cust.name,
                    "phone_no": cust.phone_no,
                    "amount_added": added_amt,
                    "amount": cust.amount,
                    "date_given": str(cust.date_given),
                    "promised_return_date": str(cust.promised_return_date) if cust.promised_return_date else None,
                },
                "due_notifications": notifications,
                "message": (
                    f"Logged ₹{added_amt:.0f} credit for {cust.name}. "
                    f"Total pending balance is ₹{cust.amount:.0f}, promised return date: {cust.promised_return_date or 'not set'}."
                ),
            }

        # ----------------------------------------------------------------------
        # ACTION 3: UPDATE (Modify balance/repayment, phone_no, or dates)
        # ----------------------------------------------------------------------
        if act == "UPDATE":
            if not cust:
                return {
                    "status": "error",
                    "retryable": False,
                    "message": f"Customer '{name}' not found in Khata ledger to update.",
                }

            if phone_no is not None:
                cust.phone_no = phone_no
            if amount is not None:
                cust.amount = _parse_amount(amount, allow_zero=True)
                # If balance reaches 0, clear promised_return_date so notifications stop
                if cust.amount == 0:
                    cust.promised_return_date = None
            if date_given is not None:
                cust.date_given = parsed_given
            if promised_return_date is not None:
                cust.promised_return_date = parsed_promised

            db.commit()
            db.refresh(cust)
            notifications = _build_due_notifications(db, merchant_id)

            return {
                "status": "ok",
                "retryable": False,
                "action_performed": "UPDATE",
                "customer_record": {
                    "name": cust.name,
                    "phone_no": cust.phone_no,
                    "amount": cust.amount,
                    "date_given": str(cust.date_given) if cust.date_given else None,
                    "promised_return_date": str(cust.promised_return_date) if cust.promised_return_date else None,
                },
                "due_notifications": notifications,
                "message": (
                    f"Updated {cust.name}'s credit record: balance is now ₹{cust.amount:.0f}, "
                    f"promised return date is {cust.promised_return_date or 'cleared'}."
                ),
            }

        return {
            "status": "error",
            "retryable": False,
            "message": f"Unsupported action '{action}'. Use 'LOG', 'CHECK', or 'UPDATE'.",
        }

    except Exception as exc:
        db.rollback()
        logfire.exception(
            "Khata ledger action failed",
            action=action,
            merchant_id=merchant_id,
            customer_name=name,
            database_url=APP_DB_URL,
        )
        return {
            "status": "error",
            "retryable": False,
            "message": f"Khata action failed: {exc}",
        }
    finally:
        db.close()


# ==============================================================================
# 4. LLM FUNCTION DECLARATION
# ==============================================================================

KHATA_TOOL_DECLARATION = {
    "type": "function",
    "function": {
        "name": "manage_khata_ledger",
        "description": (
            "Use this tool to log, check, or update customer credit (udhaar) details in the merchant's khata ledger. "
            "Returns due notifications for customers whose promised return date is today or overdue."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["LOG", "CHECK", "UPDATE"],
                    "description": "Action to perform on the khata ledger.",
                },
                "name": {"type": "string", "description": "Customer name."},
                "phone_no": {"type": "string", "description": "Customer phone number."},
                "amount": {"type": "number", "description": "Credit amount or updated balance."},
                "date_given": {"type": "string", "description": "Date in YYYY-MM-DD format."},
                "promised_return_date": {"type": "string", "description": "Promise date in YYYY-MM-DD format."},
            },
            "required": ["action"],
        },
    },
}

khata_ledger = manage_khata_ledger