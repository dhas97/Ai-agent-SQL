# AI Data Agent

A Streamlit app where you ask questions about a MySQL or PostgreSQL database in natural language. A LangChain agent (built with `create_agent`) inspects tables and runs read-only SQL using the connected database's dialect. On PostgreSQL it can also call existing stored procedures/functions.

## Files

| File | Purpose |
|---|---|
| [app.py](app.py) | Streamlit UI. Takes a question, calls `run_agent`, shows the answer, SQL/call and result table. |
| [agent.py](agent.py) | Creates the agent (`ChatOpenAI` + `TOOLS` + system prompt) and `run_agent()`, which extracts answer, SQL and data from the agent's messages. |
| [tools.py](tools.py) | LangChain `@tool` functions the agent can call (see below). |
| [sql_prompt.py](sql_prompt.py) | `SYSTEM_PROMPT` that tells the agent how to use the tools and which rules to follow. |
| [db_schema.py](db_schema.py) | `get_db_schema()` reads tables and columns from PostgreSQL's `public` schema or the connected MySQL database. |
| [database.py](database.py) | SQLAlchemy `engine` created from `DATABASE_URL`. |
| [config.py](config.py) | Loads `.env` settings: `OPENAI_API_KEY`, `DATABASE_URL`, `OPENAI_MODEL` (default `gpt-4o-mini`). |
| [requirements.txt](requirements.txt) | Python dependencies (`langchain>=1.0` is required for `create_agent`). |

## Tools

| Tool | What it does |
|---|---|
| `get_schema(table_name=None, offset=0, keywords=None)` | Searches all table names by keywords, or lists columns of one named table, in bounded JSON pages with `next_offset`. |
| `list_procedures` | Lists functions/procedures in `public` with arguments, return type and description. |
| `call_procedure(name, args)` | Runs a registered function (`SELECT * FROM fn(...)`) or procedure (`CALL proc(...)`) with positional args. |
| `run_sql_query(query)` | Runs a single `SELECT` query. |

`run_sql_query` and `call_procedure` return a JSON preview (at most 5 rows and
4000 bytes), the total row count, and a truncation flag to the LLM. The full
DataFrame remains a tool *artifact*, which `run_agent` passes to the UI. If a
single row exceeds the preview budget, only the count and truncation flag are sent.

Schema tool pages contain at most 25 entries and 4000 bytes. The agent lists
tables first, then inspects columns only for relevant tables, rather than
sending the complete database schema to the model. Keyword discovery searches
all table names before paging, so a product table outside the first page is
still discoverable. The agent discovers technical names itself and asks only
business clarifications when needed; product recommendations must be grounded
in retrieved records. The model output limit is
512 tokens. Provider token limits include prompts, tool definitions, tool
results, conversation history, and output reservations, not just the typed
question. Per-minute limits still apply across requests; a 429 may require
waiting for the provider's rate-limit window to reset.

`list_procedures` and `call_procedure` are PostgreSQL-only. On MySQL, the agent
uses only `get_schema` and `run_sql_query`; stored routine execution is disabled
because routines need separate safeguards against writes and implicit commits.

## How it works

```mermaid
flowchart TD
    U[User question in Streamlit] --> A[run_agent]
    A --> L[LangChain agent + LLM]
    L -->|1| LP[list_procedures]
    L -->|call if a procedure fits| CP[call_procedure]
    L -->|otherwise| GS[get_schema]
    L --> SQL[run_sql_query]
   LP --> DB[(Database)]
    CP --> DB
    GS --> DB
    SQL --> DB
    L --> R[Final answer]
    R --> A
    A --> UI[answer + SQL + table]
```

1. `app.py` sends the question to `run_agent()`.
2. The agent decides which tools to call. On PostgreSQL it prefers a matching stored procedure; otherwise it inspects the schema and writes SQL. On MySQL it inspects the schema and writes MySQL SQL directly. If a call returns an error, it can fix and retry.
3. `run_agent()` scans the message history and returns the final `answer`, the last executed `sql` (or `CALL name(args)`), and the result `data`.

## Safety

- `run_sql_query` accepts exactly one statement and it must be a `SELECT` (checked with `sqlparse`).
- Query execution runs in a `READ ONLY` transaction and is always rolled back. PostgreSQL uses a 15 s `statement_timeout`; MySQL uses a 15000 ms session `MAX_EXECUTION_TIME` for eligible SELECT statements (MySQL 5.7.8+; MySQL 8 recommended, not MariaDB).
- MySQL `SELECT INTO` is rejected, including file exports. The SQL parser is not a complete security sandbox: use a database user with only SELECT privileges, without FILE, EXECUTE, or write privileges. Prefer InnoDB tables.
- `call_procedure` only runs names found in `pg_proc` with matching argument count. Arguments are bound parameters cast to the declared types.
- Consequently, procedures that write data will fail. Use a read-only database user for extra protection.

## Setup

1. Create and activate a virtual environment (Linux/macOS), then install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   ```
2. If `.env` does not exist, create it from the template:
   ```bash
   cp .env.example .env
   ```
   For a free-tier setup, get a Groq API key at https://console.groq.com/keys
   and edit `.env`:
   ```env
   OPENAI_API_KEY=your_groq_api_key
   OPENAI_BASE_URL=https://api.groq.com/openai/v1
   OPENAI_MODEL=openai/gpt-oss-20b
   DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/dbname
   ```
   `OPENAI_API_KEY` holds the Groq key in this configuration because the app
   uses `ChatOpenAI`. The underlying OpenAI client reads `OPENAI_BASE_URL`
   automatically; no code change is required. Use a tool-calling model
   available to your Groq account.

   Groq (https://console.groq.com) is different from xAI's Grok. Groq keys
   use the Groq endpoint above; an xAI key must use its own provider endpoint
   and models. If a model returns 404, check the current models available to
   your account rather than reusing a retired model ID.

   For your local MySQL database in DBeaver, use:
   ```env
   DATABASE_URL=mysql+pymysql://your_username:your_password@localhost:3306/consumer_data_dictionary
   ```
   Use the credentials and host from DBeaver's **Edit Connection** settings.
   URL-encode special characters in the password (for example, `@` becomes
   `%40`). DBeaver is a database client, not the database server; MySQL must be
   running. The MySQL driver and MySQL 8 authentication extras are included
   in the requirements. Do not add PostgreSQL's `sslmode` option to a MySQL URL.
   Restart Streamlit after changing `.env` to reload the connection and dialect.

   For hosted PostgreSQL, [Neon](https://neon.com/pricing) has a free plan.
   Create a project and copy its connection URL, changing only the scheme
   from `postgresql://` to `postgresql+psycopg://`. Keep its SSL parameters:
   ```env
   DATABASE_URL=postgresql+psycopg://user:password@your-host/neondb?sslmode=require
   ```
   Preserve percent-encoding in the supplied URL; manually entered passwords
   with special characters must be URL-encoded. Populate your database with
   tables/data before asking questions, and preferably use a read-only role.

   Groq's free tier has usage/rate limits; Neon has storage/compute limits.
   Check their current plans before enabling paid upgrades. Database schema,
   questions, and tool-result previews are sent to the selected model provider.

   To use OpenAI instead, set `OPENAI_API_KEY` to your OpenAI key, set
   `OPENAI_MODEL=gpt-4o-mini`, and remove `OPENAI_BASE_URL`. OpenAI API usage
   is normally paid; a ChatGPT subscription does not include API credits.
   Keep `.env` private; it is already excluded by `.gitignore`.
3. Run:
   ```bash
   streamlit run app.py
   ```
   Open http://localhost:8501. In a new terminal, activate `.venv` again first.

## Notes

- PostgreSQL scans only `public` (schema and procedure listing). Change `'public'` in [db_schema.py](db_schema.py) and [tools.py](tools.py) for other PostgreSQL schemas. MySQL scans the database named in `DATABASE_URL`.
- Add descriptions to procedures so the agent can pick the right one: `COMMENT ON FUNCTION my_fn(...) IS 'Monthly sales by region';`
- Procedures only return rows if they are functions returning a table/set, or procedures with `INOUT` parameters.

## Tests

Run the offline database compatibility tests without an API key or live database:
```bash
.venv/bin/python -m unittest discover -s tests -v
```
# Ai-agent-SQL
# Ai-agent-SQL
