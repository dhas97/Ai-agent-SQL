import json

import pandas as pd
import sqlparse
from langchain_core.tools import tool
from sqlalchemy import text

from database import engine
from db_schema import get_db_schema


def execute_sql(query: str) -> pd.DataFrame:

    parsed = [
        statement
        for statement in sqlparse.parse(query)
        if str(statement).strip()
    ]

    if len(parsed) != 1:
        raise ValueError("Only one SQL statement is allowed.")

    if parsed[0].get_type() != "SELECT":
        raise ValueError("Only SELECT queries are allowed.")

    with engine.connect() as connection:

        transaction = connection.begin()

        try:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout = '15s'"))

            return pd.read_sql_query(text(query), connection)

        finally:
            transaction.rollback()


@tool
def get_schema() -> str:
    """Return the PostgreSQL database schema (tables and columns) as JSON."""

    return json.dumps(get_db_schema(), indent=2)


@tool(response_format="content_and_artifact")
def run_sql_query(query: str):
    """Execute a single read-only PostgreSQL SELECT query and return the rows."""

    try:
        df = execute_sql(query)
    except Exception as e:
        return f"Error: {e}", None

    return df.head(100).to_json(orient="records", date_format="iso"), df


_PROCEDURES_SQL = text("""
    SELECT p.proname AS name,
           p.prokind AS kind,
           pg_get_function_arguments(p.oid) AS arguments,
           pg_get_function_result(p.oid) AS returns,
           obj_description(p.oid, 'pg_proc') AS description,
           ARRAY(
               SELECT format_type(t, NULL)
               FROM unnest(string_to_array(p.proargtypes::text, ' ')::oid[])
                    WITH ORDINALITY AS u(t, i)
               ORDER BY i
           ) AS input_types
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public' AND p.prokind IN ('f', 'p')
    ORDER BY p.proname
""")


def _fetch_procedures(connection):

    return connection.execute(_PROCEDURES_SQL).mappings().all()


@tool
def list_procedures() -> str:
    """List stored procedures/functions available for dashboards, with their arguments, return type and description."""

    with engine.connect() as connection:
        rows = _fetch_procedures(connection)

    return json.dumps(
        [
            {
                "name": r["name"],
                "type": "procedure" if r["kind"] == "p" else "function",
                "arguments": r["arguments"],
                "returns": r["returns"],
                "description": r["description"],
            }
            for r in rows
        ],
        indent=2,
    )


@tool(response_format="content_and_artifact")
def call_procedure(name: str, args: list | None = None):
    """Call an existing stored procedure/function by name with positional args (use list_procedures first)."""

    args = args or []

    try:
        with engine.connect() as connection:

            # Only catalog-registered routines with matching arity can be called
            match = next(
                (
                    r for r in _fetch_procedures(connection)
                    if r["name"] == name and len(r["input_types"]) == len(args)
                ),
                None,
            )

            if match is None:
                return f"Error: no procedure/function '{name}' taking {len(args)} argument(s).", None

            quoted = engine.dialect.identifier_preparer.quote(name)
            placeholders = ", ".join(
                f"CAST(:p{i} AS {t})" for i, t in enumerate(match["input_types"])
            )
            params = {f"p{i}": value for i, value in enumerate(args)}

            if match["kind"] == "p":
                statement = f"CALL public.{quoted}({placeholders})"
            else:
                statement = f"SELECT * FROM public.{quoted}({placeholders})"

            transaction = connection.begin()

            try:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                connection.execute(text("SET LOCAL statement_timeout = '15s'"))

                result = connection.execute(text(statement), params)

                df = (
                    pd.DataFrame(result.fetchall(), columns=list(result.keys()))
                    if result.returns_rows
                    else pd.DataFrame()
                )
            finally:
                transaction.rollback()

    except Exception as e:
        return f"Error: {e}", None

    return df.head(100).to_json(orient="records", date_format="iso"), df


TOOLS = [get_schema, list_procedures, call_procedure, run_sql_query]
