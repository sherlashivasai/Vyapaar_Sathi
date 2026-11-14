from __future__ import annotations

from typing import Any, Literal

import logfire
import pandas as pd
from langchain_core.tools import tool

from src.db import APP_DB_URL, get_engine
from src.models import SETTLEMENT_TABLE_NAME

TABLE_NAME = SETTLEMENT_TABLE_NAME
engine = get_engine()

AnalysisType = Literal[
    "summary",
    "daily_totals",
    "by_instrument",
    "fees_and_taxes",
]


def _parse_settlement_dates(values: pd.Series) -> pd.Series:
    dates = pd.to_datetime(values, format="%Y-%m-%d", errors="coerce")
    missing = dates.isna()
    dates.loc[missing] = pd.to_datetime(
        values.loc[missing],
        format="%d-%m-%Y",
        errors="coerce",
    )
    return dates


@tool
def execute_data_analysis(analysis_type: AnalysisType) -> dict[str, Any]:
    """Run a predefined analysis on the merchant settlement database."""
    try:
        df = pd.read_sql(f"SELECT * FROM {TABLE_NAME}", engine)
    except Exception as exc:
        logfire.exception(
            "Could not load settlement data for analysis",
            table_name=TABLE_NAME,
            database_url=APP_DB_URL,
        )
        return {
            "status": "error",
            "retryable": True,
            "message": f"Could not load settlement data: {type(exc).__name__}: {exc}",
        }

    if df.empty:
        return {
            "status": "ok",
            "retryable": False,
            "result": "No settlement records are available.",
        }

    for column in ("amount", "fee", "igst", "cgst", "sgst"):
        df[column] = pd.to_numeric(df[column], errors="coerce")

    if analysis_type == "summary":
        dates = _parse_settlement_dates(df["settlementdate"]).dropna()
        date_range = (
            f"{dates.min().date()} through {dates.max().date()}"
            if not dates.empty
            else "unavailable"
        )
        result = (
            f"Settlement records: {len(df)}. "
            f"Total amount: {df['amount'].sum():.2f}. "
            f"Average transaction: {df['amount'].mean():.2f}. "
            f"Settlement date range: {date_range}."
        )
    elif analysis_type == "daily_totals":
        dates = _parse_settlement_dates(df["settlementdate"])
        daily = (
            df.assign(settlement_date=dates)
            .dropna(subset=["settlement_date"])
            .groupby("settlement_date", as_index=False)
            .agg(
                transaction_count=("amount", "size"),
                total_amount=("amount", "sum"),
            )
            .sort_values("settlement_date")
            .tail(30)
        )
        result = (
            "No valid settlement dates were found."
            if daily.empty
            else daily.to_string(index=False)
        )
    elif analysis_type == "by_instrument":
        grouped = (
            df.assign(instrument=df["instrument"].fillna("Unknown"))
            .groupby("instrument", as_index=False)
            .agg(
                transaction_count=("amount", "size"),
                total_amount=("amount", "sum"),
                average_amount=("amount", "mean"),
                total_fees=("fee", "sum"),
            )
            .sort_values("total_amount", ascending=False)
        )
        result = grouped.to_string(index=False)
    elif analysis_type == "fees_and_taxes":
        result = (
            f"Total fees: {df['fee'].sum():.2f}. "
            f"Total IGST: {df['igst'].sum():.2f}. "
            f"Total CGST: {df['cgst'].sum():.2f}. "
            f"Total SGST: {df['sgst'].sum():.2f}."
        )
    else:
        return {
            "status": "error",
            "retryable": False,
            "message": f"Unsupported analysis type: {analysis_type}",
        }

    logfire.info(
        "Settlement analysis completed",
        analysis_type=analysis_type,
        table_name=TABLE_NAME,
    )
    return {"status": "ok", "retryable": False, "result": result}
