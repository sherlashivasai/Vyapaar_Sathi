from __future__ import annotations

from io import StringIO
from typing import Any

import logfire
import pandas as pd
from langchain_core.tools import tool

from src.db import APP_DB_URL, get_engine
from src.models import SETTLEMENT_TABLE_NAME

DB_URL = APP_DB_URL
TABLE_NAME = SETTLEMENT_TABLE_NAME
engine = get_engine()


@tool
def execute_data_analysis(python_code: str) -> str:
    """Run a Pandas analysis using the shared merchant settlement database.

    The code can reference `pd`, `engine`, `TABLE_NAME`, and `DB_URL`.
    """
    namespace: dict[str, Any] = {
        "pd": pd,
        "engine": engine,
        "TABLE_NAME": TABLE_NAME,
        "DB_URL": DB_URL,
    }

    logfire.info(
        "Executing SQL analytics workflow",
        table_name=TABLE_NAME,
        database_url=DB_URL,
        query_preview=python_code[:180],
    )

    output = StringIO()
    namespace["print"] = lambda *values, sep=" ", end="\n": output.write(
        sep.join(str(value) for value in values) + end
    )
    try:
        exec(compile(python_code, "<execute_data_analysis>", "exec"), namespace, namespace)

        printed_output = output.getvalue().strip()
        if printed_output:
            return printed_output

        for name in ("result", "summary", "df", "table"):
            if name in namespace:
                value = namespace[name]
                if isinstance(value, (pd.DataFrame, pd.Series)):
                    return value.head(20).to_string(index=False)
                if isinstance(value, str):
                    return value
                return str(value)

        dataframes = [
            value
            for value in namespace.values()
            if isinstance(value, (pd.DataFrame, pd.Series))
        ]
        if dataframes:
            return dataframes[-1].head(20).to_string(index=False)

        return "Analysis executed successfully."
    except Exception as exc:
        logfire.exception(
            "SQL analytics workflow failed",
            table_name=TABLE_NAME,
            database_url=DB_URL,
        )
        return f"Error executing analysis: {type(exc).__name__}: {exc}"
