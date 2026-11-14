from __future__ import annotations

import os
from pathlib import Path

import logfire
import pandas as pd
from sqlalchemy import (
    inspect,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.db import APP_DB_URL, DB_PATH, get_engine, init_db
from src.models import (
    SETTLEMENT_COLUMNS,
    SETTLEMENT_PRIMARY_KEY_COLUMNS,
    SETTLEMENT_TABLE_NAME,
)


def _ensure_table(engine) -> None:
    inspector = inspect(engine)
    table_name = SETTLEMENT_TABLE_NAME
    expected_columns = set(SETTLEMENT_COLUMNS)
    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    if existing_columns != expected_columns:
        missing = sorted(expected_columns - existing_columns)
        unexpected = sorted(existing_columns - expected_columns)
        raise ValueError(
            f"Table '{table_name}' does not match the registered schema. "
            f"Missing columns: {missing}; unexpected columns: {unexpected}. "
            "The table was left unchanged."
        )

    primary_key = tuple(
        inspector.get_pk_constraint(table_name).get("constrained_columns") or ()
    )
    if primary_key != SETTLEMENT_PRIMARY_KEY_COLUMNS:
        raise ValueError(
            f"Table '{table_name}' must use the composite primary key "
            f"{SETTLEMENT_PRIMARY_KEY_COLUMNS}; found {primary_key}."
        )


def _append_rows(table, connection, keys, data_iter) -> int:
    rows = [dict(zip(keys, row)) for row in data_iter]
    if not rows:
        return 0

    statement = sqlite_insert(table.table).values(rows)
    statement = statement.on_conflict_do_nothing(
        index_elements=list(SETTLEMENT_PRIMARY_KEY_COLUMNS)
    )
    result = connection.execute(statement)
    return result.rowcount or 0


class DataIngestion:
    """Ingest CSV and PDF files into a SQL database."""

    def parse_csv(self, file_path: str) -> pd.DataFrame:
        """Read a CSV while preserving transaction identifiers as text."""
        return pd.read_csv(
            file_path,
            dtype={
                "MerchantReferenceId": "string",
                "PhonePeReferenceId": "string",
                "BankReferenceNo": "string",
            },
        )

    def parse_pdf(self, file_path: str) -> pd.DataFrame:
        """Extract tables from a PDF, using the first row as the column headers."""
        import pdfplumber

        all_rows = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    for row in table:
                        all_rows.append([cell.strip() if cell else None for cell in row])

        if not all_rows:
            raise ValueError("No table data found in the PDF.")

        headers = [
            column if column else f"col_{index}"
            for index, column in enumerate(all_rows[0])
        ]
        rows = all_rows[1:]
        if any(len(row) != len(headers) for row in rows):
            raise ValueError("PDF tables contain inconsistent numbers of columns.")

        return pd.DataFrame(rows, columns=headers)

    def clean_column_names(self, df: pd.DataFrame) -> pd.DataFrame:
        """Standardize column names for SQL compatibility."""
        df = df.copy()
        df.columns = (
            df.columns.astype(str)
            .str.strip()
            .str.lower()
            .str.replace(r"[^\w\s]", "", regex=True)
            .str.replace(r"\s+", "_", regex=True)
        )
        if not df.columns.is_unique:
            duplicates = df.columns[df.columns.duplicated()].tolist()
            raise ValueError(f"Column names are not unique after cleaning: {duplicates}")
        return df

    def ingest_to_sql(
        self,
        file_path: str,
    ) -> int:
        """Append a CSV or PDF to a SQL table, skipping existing transaction keys."""
        init_db()
        file_extension = os.path.splitext(file_path)[1].lower()
        table_name = SETTLEMENT_TABLE_NAME

        with logfire.span(
            "Ingest input file",
            file_path=file_path,
            file_extension=file_extension,
            table_name=table_name,
        ):
            logfire.info(
                "Reading input file",
                file_path=file_path,
                file_extension=file_extension,
                database_url=APP_DB_URL,
            )
            try:
                if file_extension == ".csv":
                    df = self.parse_csv(file_path)
                elif file_extension == ".pdf":
                    df = self.parse_pdf(file_path)
                else:
                    raise ValueError(
                        f"Unsupported file format '{file_extension}'. "
                        "Please provide a .csv or .pdf file."
                    )
            except Exception:
                logfire.exception("Could not read input file", file_path=file_path)
                raise

            df = self.clean_column_names(df)
            if tuple(df.columns) != SETTLEMENT_COLUMNS:
                raise ValueError(
                    "Input columns do not match the expected settlement report "
                    f"schema. Found: {list(df.columns)}"
                )
            if df[list(SETTLEMENT_PRIMARY_KEY_COLUMNS)].isna().any().any():
                raise ValueError(
                    f"Primary-key columns {SETTLEMENT_PRIMARY_KEY_COLUMNS} cannot contain nulls."
                )

            logfire.info(
                "Parsed input",
                row_count=len(df),
                columns=list(df.columns),
                database_url=APP_DB_URL,
            )

            engine = get_engine()
            try:
                _ensure_table(engine)
                with engine.begin() as connection:
                    rows_inserted = int(
                        df.to_sql(
                            name=table_name,
                            con=connection,
                            if_exists="append",
                            index=False,
                            method=_append_rows
                            if engine.dialect.name == "sqlite"
                            else None,
                        )
                        or 0
                    )
            except Exception:
                logfire.exception(
                    "Could not write data to SQL table",
                    table_name=table_name,
                )
                raise
            logfire.info(
                "Stored rows in SQL table",
                row_count=rows_inserted,
                duplicate_rows_skipped=len(df) - rows_inserted,
                table_name=table_name,
            )
            return rows_inserted


if __name__ == "__main__":
    logfire.configure(service_name="merchant-ai")
    project_root = Path(__file__).resolve().parents[1]
    data_directory = project_root / "data"
    data_files = sorted(data_directory.glob("Merchant_Settlement_Report_*.csv"))
    if not data_files:
        raise FileNotFoundError(f"No settlement CSV files found in {data_directory}")

    ingestion = DataIngestion()
    logfire.info(
        "Using unified merchant database for settlement ingestion",
        database_path=str(DB_PATH),
        database_url=APP_DB_URL,
    )

    for path in data_files:
        ingestion.ingest_to_sql(
            file_path=str(path),
        )
