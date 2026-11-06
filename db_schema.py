from sqlalchemy import inspect
from database import engine


def get_db_tables():

    schema = "public" if engine.dialect.name == "postgresql" else None
    return inspect(engine).get_table_names(schema=schema)


def get_db_schema(table_names: list[str] | None = None):

    inspector = inspect(engine)

    schema_details = {}
    schema = "public" if engine.dialect.name == "postgresql" else None
    available_tables = inspector.get_table_names(schema=schema)
    selected_tables = available_tables if table_names is None else table_names

    for table in selected_tables:

        if table not in available_tables:
            raise ValueError("Unknown table. Use get_schema to list available tables first.")

        columns = inspector.get_columns(table, schema=schema)

        schema_details[table] = [
            {
                "column_name": column["name"],
                "data_type": str(column["type"]),
                "nullable": column["nullable"]
            }
            for column in columns
        ]

    return schema_details
