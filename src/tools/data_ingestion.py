from __future__ import annotations

import os
from pathlib import Path

import logfire
import pandas as pd
from sqlalchemy import (
    Column,
    MetaData,
    Numeric,
    String,
    Table,
    create_engine,
    inspect,
    insert,
    select,
    text,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert


PRIMARY_KEY_COLUMNS = ("phonepereferenceid", "instrument")
NUMERIC_COLUMNS = {"amount", "fee", "igst", "cgst", "sgst"}
SETTLEMENT_COLUMNS = (
    "paymenttype",
    "merchantreferenceid",
    "phonepereferenceid",
    "from",
    "instrument",
    "creationdate",
    "transactiondate",
    "settlementdate",
    "bankreferenceno",
    "amount",
    "fee",
    "igst",
    "cgst",
    "sgst",
)


def _define_table(table_name: str, column_names: tuple[str, ...]) -> Table:
    metadata = MetaData()
    columns = [
        Column(
            name,
            Numeric(18, 6) if name in NUMERIC_COLUMNS else String,
            primary_key=name in PRIMARY_KEY_COLUMNS,
            nullable=name not in PRIMARY_KEY_COLUMNS,
        )
        for name in column_names
    ]
    return Table(table_name, metadata, *columns)


def _migrate_sqlite_primary_key(
    engine,
    table_name: str,
    column_names: tuple[str, ...],
) -> None:
    temporary_name = f"{table_name}__migration"
    with engine.connect() as connection:
        if inspect(connection).has_table(temporary_name):
            raise RuntimeError(
                f"Cannot migrate '{table_name}': temporary table "
                f"'{temporary_name}' already exists."
            )

        old_table = Table(table_name, MetaData(), autoload_with=connection)
        new_table = _define_table(temporary_name, column_names)
        connection.commit()
        transaction = connection.begin()
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            new_table.create(connection)
            connection.execute(
                insert(new_table).from_select(
                    column_names,
                    select(*(old_table.c[name] for name in column_names)),
                )
            )
            preparer = connection.dialect.identifier_preparer
            connection.execute(text(f"DROP TABLE {preparer.quote(table_name)}"))
            connection.execute(
                text(
                    f"ALTER TABLE {preparer.quote(temporary_name)} "
                    f"RENAME TO {preparer.quote(table_name)}"
                )
            )
            transaction.commit()
        except Exception:
            transaction.rollback()
            raise


def _ensure_table(engine, table_name: str, column_names: tuple[str, ...]) -> None:
    inspector = inspect(engine)
    if not inspector.has_table(table_name):
        table = _define_table(table_name, column_names)
        table.metadata.create_all(engine)
        return

    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    expected_columns = set(column_names)
    if existing_columns != expected_columns:
        missing = sorted(expected_columns - existing_columns)
        unexpected = sorted(existing_columns - expected_columns)
        raise ValueError(
            f"Table '{table_name}' does not match the input schema. "
            f"Missing columns: {missing}; unexpected columns: {unexpected}. "
            "The table was left unchanged."
        )

    primary_key = tuple(
        inspector.get_pk_constraint(table_name).get("constrained_columns") or ()
    )
    if set(primary_key) != set(PRIMARY_KEY_COLUMNS):
        if engine.dialect.name != "sqlite":
            raise ValueError(
                f"Table '{table_name}' must use the composite primary key "
                f"{PRIMARY_KEY_COLUMNS}; automatic migration is only supported "
                "for SQLite."
            )
        _migrate_sqlite_primary_key(engine, table_name, column_names)


def _append_rows(table, connection, keys, data_iter) -> int:
    rows = [dict(zip(keys, row)) for row in data_iter]
    if not rows:
        return 0

    statement = sqlite_insert(table.table).values(rows)
    statement = statement.on_conflict_do_nothing(
        index_elements=list(PRIMARY_KEY_COLUMNS)
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
        db_url: str = "sqlite:///payment_data.db",
        table_name: str | None = None,
    ) -> int:
        """Append a CSV or PDF to a SQL table, skipping existing transaction keys."""
        file_extension = os.path.splitext(file_path)[1].lower()
        if table_name is None:
            table_name = Path(file_path).stem.lower().replace(" ", "_")

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
            if df[list(PRIMARY_KEY_COLUMNS)].isna().any().any():
                raise ValueError(
                    f"Primary-key columns {PRIMARY_KEY_COLUMNS} cannot contain nulls."
                )

            logfire.info(
                "Parsed input",
                row_count=len(df),
                columns=list(df.columns),
            )

            engine = create_engine(db_url)
            try:
                _ensure_table(engine, table_name, SETTLEMENT_COLUMNS)
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
            finally:
                engine.dispose()

            logfire.info(
                "Stored rows in SQL table",
                row_count=rows_inserted,
                duplicate_rows_skipped=len(df) - rows_inserted,
                table_name=table_name,
            )
            return rows_inserted


if __name__ == "__main__":
    logfire.configure(service_name="merchant-ai")
    project_root = Path(__file__).resolve().parents[2]
    data_directory = project_root / "data"
    data_files = sorted(data_directory.glob("Merchant_Settlement_Report_*.csv"))
    if not data_files:
        raise FileNotFoundError(f"No settlement CSV files found in {data_directory}")

    database_path = project_root / "payment_data.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    target_table = "merchant_settlement_reports"
    ingestion = DataIngestion()

    for path in data_files:
        ingestion.ingest_to_sql(
            file_path=str(path),
            db_url=database_url,
            table_name=target_table,
        )
