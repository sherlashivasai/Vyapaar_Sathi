import os 
import sys
from langchain_core.tools import tool
import pandas as pd
from sqlalchemy import create_engine
from io import StringIO

DB_URL = "sqlite:///payment_data.db"
engine = create_engine(DB_URL)
TABLE_NAME = "merchant_settlement_reports_for_Nov-06_to_Nov-11"

@tool
def execute_data_analysis(python_code: str) -> str:
    """
    Executes Python/Pandas code to analyze merchant payment settlement data.
    
    Environment variables available in script:
      - `pd`: Pandas library
      - `engine`: SQLAlchemy engine connected to SQLite
      - `table_name`: Name of the settlement table
      
    Instructions:
      - Read data using: df = pd.read_sql(f'SELECT * FROM "{table_name}"', engine)
      - Always use print() to output results, numbers, aggregates, and findings.
    """
    # Create execution namespace
    local_scope = {
        "pd": pd,
        "engine": engine,
        "table_name": TABLE_NAME,
    }

    # Redirect stdout to capture print() statements
    stdout_buffer = StringIO()
    original_stdout = sys.stdout

    try:
        sys.stdout = stdout_buffer
        exec(python_code, {}, local_scope)
        output = stdout_buffer.getvalue()
        return output.strip() if output.strip() else "Code executed successfully with no printed output."
    except Exception as err:
        return f"Execution error: {str(err)}"
    finally:
        sys.stdout = original_stdout