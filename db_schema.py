from sqlalchemy import inspect
from database import engine


def get_db_schema():

    inspector = inspect(engine)

    schema_details = {}

    for table in inspector.get_table_names(schema="public"):

        columns = inspector.get_columns(table, schema="public")

        schema_details[table] = [
            {
                "column_name": column["name"],
                "data_type": str(column["type"]),
                "nullable": column["nullable"]
            }
            for column in columns
        ]

    return schema_details
