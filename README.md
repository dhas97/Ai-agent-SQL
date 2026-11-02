# AI Data Agent

A Streamlit app where you ask questions about a PostgreSQL database in natural language. A LangChain agent (built with `create_agent`) uses tools to call existing stored procedures/functions or to write and run read-only SQL, then answers with the result.

## Files

| File | Purpose |
|---|---|
| [app.py](app.py) | Streamlit UI. Takes a question, calls `run_agent`, shows the answer, SQL/call and result table. |
| [agent.py](agent.py) | Creates the agent (`ChatOpenAI` + `TOOLS` + system prompt) and `run_agent()`, which extracts answer, SQL and data from the agent's messages. |
| [tools.py](tools.py) | LangChain `@tool` functions the agent can call (see below). |
| [sql_prompt.py](sql_prompt.py) | `SYSTEM_PROMPT` that tells the agent how to use the tools and which rules to follow. |
| [db_schema.py](db_schema.py) | `get_db_schema()` reads tables and columns of the `public` schema with SQLAlchemy inspector. |
| [database.py](database.py) | SQLAlchemy `engine` created from `DATABASE_URL`. |
| [config.py](config.py) | Loads `.env` settings: `OPENAI_API_KEY`, `DATABASE_URL`, `OPENAI_MODEL` (default `gpt-4o-mini`). |
| [requirements.txt](requirements.txt) | Python dependencies (`langchain>=1.0` is required for `create_agent`). |

## Tools

| Tool | What it does |
|---|---|
| `get_schema` | Returns tables, columns, types and nullability as JSON. |
| `list_procedures` | Lists functions/procedures in `public` with arguments, return type and description. |
| `call_procedure(name, args)` | Runs a registered function (`SELECT * FROM fn(...)`) or procedure (`CALL proc(...)`) with positional args. |
| `run_sql_query(query)` | Runs a single `SELECT` query. |

`run_sql_query` and `call_procedure` return a short JSON preview (max 100 rows) to the LLM and the full DataFrame as a tool *artifact*, which `run_agent` passes to the UI.

## How it works

```mermaid
flowchart TD
    U[User question in Streamlit] --> A[run_agent]
    A --> L[LangChain agent + LLM]
    L -->|1| LP[list_procedures]
    L -->|call if a procedure fits| CP[call_procedure]
    L -->|otherwise| GS[get_schema]
    L --> SQL[run_sql_query]
    LP --> DB[(PostgreSQL)]
    CP --> DB
    GS --> DB
    SQL --> DB
    L --> R[Final answer]
    R --> A
    A --> UI[answer + SQL + table]
```

1. `app.py` sends the question to `run_agent()`.
2. The agent decides which tools to call. The prompt tells it to prefer a matching stored procedure, otherwise inspect the schema and write SQL. If a call returns an error, it can fix and retry.
3. `run_agent()` scans the message history and returns the final `answer`, the last executed `sql` (or `CALL name(args)`), and the result `data`.

## Safety

- `run_sql_query` accepts exactly one statement and it must be a `SELECT` (checked with `sqlparse`).
- All database calls run in a `READ ONLY` transaction with a 15 s `statement_timeout`, and are always rolled back.
- `call_procedure` only runs names found in `pg_proc` with matching argument count. Arguments are bound parameters cast to the declared types.
- Consequently, procedures that write data will fail. Use a read-only database user for extra protection.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Create a `.env` file:
   ```env
   OPENAI_API_KEY=sk-...
   DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/dbname
   OPENAI_MODEL=gpt-4o-mini
   ```
3. Run:
   ```bash
   streamlit run app.py
   ```

## Notes

- Only the `public` schema is scanned (schema and procedure listing). Change `'public'` in [db_schema.py](db_schema.py) and [tools.py](tools.py) for other schemas.
- Add descriptions to procedures so the agent can pick the right one: `COMMENT ON FUNCTION my_fn(...) IS 'Monthly sales by region';`
- Procedures only return rows if they are functions returning a table/set, or procedures with `INOUT` parameters.
# Ai-agent-SQL
