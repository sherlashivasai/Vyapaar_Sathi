from __future__ import annotations

from typing import Any
from pydantic import Field, BaseModel
import pandas as pd
from typing import Optional
from langchain_core.tools import tool
from sqlalchemy import create_engine

class ExecuteDataAnalysisInput(BaseModel):
    """
    Input schema for the execute_data_analysis tool.
    """
    python_code: str = Field(
        ...,
        description="Python code to execute for data analysis. "
                    "The code can reference `pd`, `engine`, `TABLE_NAME`, and `DB_URL`."
    )

class DataAnalysisOutput(BaseModel):
    """
    Output schmea for the execute_data_analysis tool
    """
    result: str = Field(description="One line result of the function")
    summary: str = Field(description="Short overview how it got the output.")
    

DB_URL = "sqlite:///payment_data.db"
TABLE_NAME = "merchant_settlement_reports"
engine = create_engine(DB_URL)


@tool
def execute_data_analysis(python_code: ExecuteDataAnalysisInput) -> DataAnalysisOutput:
    """
    Execute custom pandas and SQLAlchemy analysis code against the merchant
    settlement SQLite database.

    The code can reference these variables:
    - `pd`: pandas
    - `engine`: SQLAlchemy engine connected to the SQLite DB
    - `TABLE_NAME`: name of the settlement report table
    - `DB_URL`: the database connection URL

    Example code:
      df = pd.read_sql_query(f"SELECT * FROM {TABLE_NAME} LIMIT 10", engine)
      df.head()
    """
    namespace: dict[str, Any] = {
        "pd": pd,
        "engine": engine,
        "TABLE_NAME": TABLE_NAME,
        "DB_URL": DB_URL,
    }

    try:
        compiled = compile(python_code, "<execute_data_analysis>", "exec")
        exec(compiled, namespace, namespace)

        for name in ("result", "summary", "df", "table"):
            if name in namespace:
                value = namespace[name]
                if isinstance(value, (pd.DataFrame, pd.Series)):
                    return value.head(20).to_string(index=False)
                if isinstance(value, str):
                    return value
                return str(value)

        dataframe_values = [
            value for value in namespace.values() if isinstance(value, (pd.DataFrame, pd.Series))
        ]
        if dataframe_values:
            return dataframe_values[-1].head(20).to_string(index=False)

        return "Analysis executed successfully."
    except Exception as exc:  # pragma: no cover - tool safety path
        return f"Error executing analysis: {type(exc).__name__}: {exc}"


# Unit Test

if __name__ == "__main__":

    from langchain_groq import ChatGroq
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    from langchain.agents import create_tool_calling_agent, AgentExecutor
    import os

    tools = [execute_data_analysis]

# Initialize Gemini model with function-calling support
    llm = ChatGroq(
        model="openai/gpt-oss-120b", # or "gemini-1.5-flash"
        temperature=0.0,
        api_key=os.getenv("GROQ_API_KEY")
)

# Define the conversational prompt template
    prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are an expert financial and merchant data analyst.\n"
        "You have access to a SQLite database via the `execute_data_analysis` tool.\n\n"
        "Rules:\n"
        "1. Never guess or approximate numbers. Always write Python/Pandas code using the tool to calculate metrics.\n"
        "2. Compute descriptive statistics, totals, averages, volume by instrument, and identify any anomalies or outliers.\n"
        "3. Provide direct, concise, and business-focused insights based strictly on the computation results."
    ),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])

# Build the tool-calling agent and executor loop
    agent = create_tool_calling_agent(llm, tools, prompt)
    agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
    query = "Analyze the merchant settlement data for Nov 6 to Nov 11. Provide totals, averages, and any anomalies."
    response = agent_executor.invoke({"input": query})
    print("\n--- FINAL INSIGHT ---")
    print(response["output"])