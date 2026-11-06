import json
import os
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd


with patch.dict(os.environ, {
    "OPENAI_API_KEY": "test-key-not-real",
    "DATABASE_URL": "mysql+pymysql://test:test@localhost:3306/test_database",
}):
    import db_schema
    import tools
    import sql_prompt


class DatabaseSupportTests(unittest.TestCase):
    def test_read_only_query_setup_and_rollback(self):
        for dialect in ("mysql", "postgresql"):
            with self.subTest(dialect=dialect):
                fake_engine = MagicMock()
                fake_engine.dialect.name = dialect
                connection = fake_engine.connect.return_value.__enter__.return_value
                frame = pd.DataFrame({"value": [1]})
                with patch.object(tools, "engine", fake_engine), patch.object(
                    tools.pd, "read_sql_query", return_value=frame
                ):
                    self.assertIs(tools.execute_sql("SELECT 1"), frame)
                statements = [str(call.args[0]) for call in connection.execute.call_args_list]
                expected = (
                    ["SET SESSION MAX_EXECUTION_TIME = 15000", "SET TRANSACTION READ ONLY"]
                    if dialect == "mysql"
                    else ["SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = '15s'"]
                )
                self.assertEqual(statements, expected)
                connection.begin.return_value.rollback.assert_called_once()

    def test_failed_queries_are_rolled_back(self):
        for dialect in ("mysql", "postgresql"):
            with self.subTest(dialect=dialect):
                fake_engine = MagicMock()
                fake_engine.dialect.name = dialect
                connection = fake_engine.connect.return_value.__enter__.return_value
                with patch.object(tools, "engine", fake_engine), patch.object(
                    tools.pd, "read_sql_query", side_effect=RuntimeError("query failed")
                ):
                    with self.assertRaises(RuntimeError):
                        tools.execute_sql("SELECT 1")
                connection.begin.return_value.rollback.assert_called_once()

    def test_schema_selection(self):
        for dialect in ("mysql", "postgresql"):
            with self.subTest(dialect=dialect):
                fake_engine = MagicMock()
                fake_engine.dialect.name = dialect
                with patch.object(db_schema, "engine", fake_engine), patch.object(
                    db_schema, "inspect"
                ) as inspect:
                    inspector = inspect.return_value
                    inspector.get_table_names.return_value = ["example"]
                    inspector.get_columns.return_value = [
                        {"name": "id", "type": "INTEGER", "nullable": False}
                    ]
                    self.assertEqual(db_schema.get_db_schema(), {
                        "example": [{"column_name": "id", "data_type": "INTEGER", "nullable": False}]
                    })
                    schema = "public" if dialect == "postgresql" else None
                    inspector.get_table_names.assert_called_once_with(schema=schema)
                    inspector.get_columns.assert_called_once_with("example", schema=schema)

    def test_unsafe_queries_are_rejected_before_connecting(self):
        queries = (
            "", "DELETE FROM example", "UPDATE example SET id = 1",
            "CREATE TABLE example (id INT)", "SELECT 1; SELECT 2",
            "SELECT 1 INTO OUTFILE '/tmp/example'", "SELECT 1 INTO DUMPFILE '/tmp/example'",
            "SELECT 1 INTO @value",
        )
        fake_engine = MagicMock()
        fake_engine.dialect.name = "mysql"
        with patch.object(tools, "engine", fake_engine):
            for query in queries:
                with self.subTest(query=query), self.assertRaises(ValueError):
                    tools.execute_sql(query)
        fake_engine.connect.assert_not_called()

    def test_mysql_select_with_into_in_literal_is_allowed(self):
        fake_engine = MagicMock()
        fake_engine.dialect.name = "mysql"
        with patch.object(tools, "engine", fake_engine), patch.object(tools.pd, "read_sql_query"):
            tools.execute_sql("SELECT 'INTO' AS example")
        fake_engine.connect.assert_called_once()

    def test_mysql_tools_and_prompt(self):
        self.assertEqual([tool.name for tool in tools.TOOLS], ["get_schema", "run_sql_query"])
        self.assertIn("expert MySQL", sql_prompt.SYSTEM_PROMPT)
        self.assertNotIn("call_procedure", sql_prompt.SYSTEM_PROMPT)

    def test_mysql_routine_tools_do_not_connect(self):
        fake_engine = MagicMock()
        fake_engine.dialect.name = "mysql"
        with patch.object(tools, "engine", fake_engine):
            self.assertIn("PostgreSQL-only", tools.list_procedures.invoke({}))
            message, artifact = tools.call_procedure.func("example")
            self.assertIn("not supported", message)
            self.assertIsNone(artifact)
        fake_engine.connect.assert_not_called()

    def test_schema_lists_tables_without_loading_columns(self):
        tables = [f"table_{index}" for index in range(133)]
        with patch.object(tools, "get_db_tables", return_value=tables), patch.object(
            tools, "get_db_schema"
        ) as get_db_schema:
            page = json.loads(tools.get_schema.invoke({}))
        self.assertEqual(page["tables"], tables[:25])
        self.assertEqual(page["total"], 133)
        self.assertEqual(page["next_offset"], 25)
        get_db_schema.assert_not_called()

    def test_schema_columns_are_selected_and_paged(self):
        columns = [{"column_name": f"column_{index}", "data_type": "INTEGER", "nullable": False}
                   for index in range(60)]
        with patch.object(tools, "get_db_schema", return_value={"example": columns}) as get_db_schema:
            page = json.loads(tools.get_schema.invoke({"table_name": "example", "offset": 25}))
        get_db_schema.assert_called_once_with(["example"])
        self.assertEqual(page["columns"], columns[25:50])
        self.assertEqual(page["next_offset"], 50)

    def test_schema_page_has_a_size_bound(self):
        items = ["table_" + "x" * 1000 for index in range(10)]
        content = tools._schema_page(items, 0, "tables")
        self.assertLessEqual(len(content.encode("utf-8")), tools.MAX_TOOL_CONTENT_BYTES)
        page = json.loads(content)
        self.assertEqual(page["next_offset"], len(page["tables"]))
        self.assertEqual(page["tables"], items[:page["next_offset"]])
        self.assertIn("error", json.loads(tools._schema_page(["x" * 5000], 0, "tables")))

    def test_schema_page_end_and_invalid_offset(self):
        self.assertIsNone(json.loads(tools._schema_page(["example"], 0, "tables"))["next_offset"])
        self.assertEqual(json.loads(tools._schema_page(["example"], 1, "tables"))["tables"], [])
        self.assertIn("error", json.loads(tools._schema_page([], -1, "tables")))

    def test_selected_schema_reflects_only_requested_table(self):
        with patch.object(db_schema, "inspect") as inspect:
            inspector = inspect.return_value
            inspector.get_table_names.return_value = ["example", "other"]
            inspector.get_columns.return_value = []
            self.assertEqual(db_schema.get_db_schema(["example"]), {"example": []})
            inspector.get_columns.assert_called_once_with("example", schema=None)
            with self.assertRaises(ValueError):
                db_schema.get_db_schema(["missing"])

    def test_result_preview_is_bounded_and_preserves_full_artifact(self):
        frame = pd.DataFrame({"value": ["x" * 1000] * 100})
        with patch.object(tools, "execute_sql", return_value=frame):
            content, artifact = tools.run_sql_query.func("SELECT value FROM example")
        self.assertIs(artifact, frame)
        self.assertLessEqual(len(content.encode("utf-8")), tools.MAX_TOOL_CONTENT_BYTES)
        payload = json.loads(content)
        self.assertEqual(payload["total_rows"], 100)
        self.assertTrue(payload["preview_truncated"])
        self.assertGreater(len(payload["rows"]), 0)
        self.assertLess(len(payload["rows"]), 5)
        self.assertEqual(payload["rows"][0]["value"], frame.iloc[0]["value"])

    def test_empty_and_oversized_result_previews(self):
        empty = json.loads(tools._data_preview(pd.DataFrame()))
        self.assertEqual(empty["rows"], [])
        self.assertFalse(empty["preview_truncated"])
        content = tools._data_preview(pd.DataFrame({"value": ["x" * 10000]}))
        self.assertLessEqual(len(content.encode("utf-8")), tools.MAX_TOOL_CONTENT_BYTES)
        payload = json.loads(content)
        self.assertEqual(payload["rows"], [])
        self.assertEqual(payload["total_rows"], 1)
        self.assertTrue(payload["preview_truncated"])

    def test_small_result_preview_keeps_all_rows(self):
        payload = json.loads(tools._data_preview(pd.DataFrame({"value": [1, 2]})))
        self.assertEqual(payload["rows"], [{"value": 1}, {"value": 2}])
        self.assertFalse(payload["preview_truncated"])

    def test_schema_search_finds_tables_beyond_first_page(self):
        tables = [f"account_{index}" for index in range(80)] + ["products", "hair_items"]
        with patch.object(tools, "get_db_tables", return_value=tables), patch.object(
            tools, "get_db_schema"
        ) as get_db_schema:
            content = tools.get_schema.invoke({"keywords": [" PRODUCT ", "hair"]})
        page = json.loads(content)
        self.assertCountEqual(page["tables"], ["products", "hair_items"])
        self.assertEqual(page["total"], 2)
        self.assertIsNone(page["next_offset"])
        get_db_schema.assert_not_called()
        self.assertLessEqual(len(content.encode("utf-8")), tools.MAX_TOOL_CONTENT_BYTES)

    def test_schema_search_ranks_and_pages_matches(self):
        tables = [f"product_{index:02d}" for index in range(30)] + ["product", "hair_product"]
        with patch.object(tools, "get_db_tables", return_value=tables):
            first = json.loads(tools.get_schema.invoke({"keywords": ["product", "hair"]}))
            second = json.loads(tools.get_schema.invoke({
                "keywords": ["product", "hair"], "offset": first["next_offset"]
            }))
        self.assertEqual(first["tables"][:2], ["product", "hair_product"])
        self.assertEqual(first["total"], 32)
        self.assertCountEqual(first["tables"] + second["tables"], tables)

    def test_schema_search_no_matches_and_blank_keywords(self):
        with patch.object(tools, "get_db_tables", return_value=["accounts"]):
            missing = json.loads(tools.get_schema.invoke({"keywords": ["hair"]}))
            unfiltered = json.loads(tools.get_schema.invoke({"keywords": [" "]}))
        self.assertEqual(missing["tables"], [])
        self.assertEqual(missing["total"], 0)
        self.assertEqual(unfiltered["tables"], ["accounts"])

    def test_prompt_discovers_tables_instead_of_asking_user(self):
        self.assertIn("Never ask the user for table names or column names", sql_prompt.SYSTEM_PROMPT)
        self.assertIn("get_schema(keywords=", sql_prompt.SYSTEM_PROMPT)
        self.assertIn("retrieved records", sql_prompt.SYSTEM_PROMPT)
        self.assertIn("Finish once useful matching results are available", sql_prompt.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()