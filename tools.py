import json

import pandas as pd
import sqlparse
from langchain_core.tools import tool
from sqlalchemy import text

from database import engine
from db_schema import get_db_schema, get_db_tables


MAX_TOOL_CONTENT_BYTES = 4000


def _schema_page(items: list, offset: int, item_key: str) -> str:

    if offset < 0:
        return json.dumps({"error": "offset must be nonnegative"})

    page = []
    payload = {item_key: page, "total": len(items), "next_offset": None}
    for item in items[offset:offset + 25]:
        page.append(item)
        payload["next_offset"] = offset + len(page) if offset + len(page) < len(items) else None
        if len(json.dumps(payload).encode("utf-8")) > MAX_TOOL_CONTENT_BYTES:
            page.pop()
            if not page:
                return json.dumps({"error": "One metadata entry exceeds the preview limit."})
            payload["next_offset"] = offset + len(page)
            break

    return json.dumps(payload)


def _data_preview(df: pd.DataFrame) -> str:

    rows = json.loads(df.head(5).to_json(orient="records", date_format="iso"))
    payload = {"total_rows": len(df), "rows": rows, "preview_truncated": len(df) > len(rows)}
    while len(json.dumps(payload).encode("utf-8")) > MAX_TOOL_CONTENT_BYTES:
        rows.pop()
        payload["preview_truncated"] = True
    return json.dumps(payload)


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

    if engine.dialect.name == "mysql" and any(
        token.normalized == "INTO" for token in parsed[0].flatten()
    ):
        raise ValueError("SELECT INTO is not allowed.")

    with engine.connect() as connection:

        transaction = connection.begin()

        try:
            if engine.dialect.name == "mysql":
                connection.execute(text("SET SESSION MAX_EXECUTION_TIME = 15000"))
            connection.execute(text("SET TRANSACTION READ ONLY"))
            if engine.dialect.name == "postgresql":
                connection.execute(text("SET LOCAL statement_timeout = '15s'"))

            return pd.read_sql_query(text(query), connection)

        finally:
            transaction.rollback()


@tool
def get_schema(table_name: str | None = None, offset: int = 0, keywords: list[str] | None = None) -> str:
    """List table names, optionally matching ANY keywords across all tables, or columns of one exact table_name. Use next_offset for pages."""

    try:
        if table_name is None:
            tables = get_db_tables()
            terms = [keyword.strip().casefold() for keyword in keywords or [] if keyword.strip()]
            if terms:
                tables = [name for name in tables if any(term in name.casefold() for term in terms)]
                tables.sort(key=lambda name: (
                    name.casefold() not in terms,
                    -sum(term in name.casefold() for term in terms),
                    name.casefold(),
                ))
            return _schema_page(tables, offset, "tables")
        columns = get_db_schema([table_name])[table_name]
        return _schema_page(columns, offset, "columns")
    except ValueError as error:
        return json.dumps({"error": str(error)})


@tool(response_format="content_and_artifact")
def run_sql_query(query: str):
    """Execute a single read-only SELECT query and return the rows."""

    try:
        df = execute_sql(query)
    except Exception as e:
        return f"Error: {e}", None

    return _data_preview(df), df


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

    if engine.dialect.name != "postgresql":
        return json.dumps({"message": "Routine tools are PostgreSQL-only. Use get_schema and run_sql_query."})

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

    if engine.dialect.name != "postgresql":
        return "Error: MySQL routine execution is not supported. Use read-only SELECT queries.", None

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

    return _data_preview(df), df


TOOLS = (
    [get_schema, list_procedures, call_procedure, run_sql_query]
    if engine.dialect.name == "postgresql"
    else [get_schema, run_sql_query]
)
