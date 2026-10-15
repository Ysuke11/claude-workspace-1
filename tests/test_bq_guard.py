#!/usr/bin/env python3
"""SQL 安全ガードのテスト。

エージェントが生成した SQL をそのまま BigQuery に流すため、ここが最後の砦になる。
「通すべきものを通す」テストと「弾くべきものを弾く」テストの両方を置く。
特に、文字列リテラルやコメントの中に書き込み系キーワードが現れても
誤検知しないことを担保する（分析クエリで実際に起きうるため）。
"""

from __future__ import annotations

import datetime
import decimal
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from shoshin_agent.bq import QueryRejected, _to_jsonable, validate_sql  # noqa: E402


class ValidateSqlAcceptsReadOnly(unittest.TestCase):
    def test_plain_select(self):
        self.assertEqual(validate_sql("SELECT 1"), "SELECT 1")

    def test_with_clause(self):
        sql = "with t as (select 1 as a) select * from t"
        self.assertEqual(validate_sql(sql), sql)

    def test_leading_whitespace_and_newlines(self):
        self.assertEqual(validate_sql("\n\n  SELECT 1\n"), "SELECT 1")

    def test_trailing_semicolon_is_stripped(self):
        self.assertEqual(validate_sql("SELECT 1;"), "SELECT 1")

    def test_columns_that_merely_contain_keywords(self):
        """created_at / updated_at が \\bcreate\\b や \\bupdate\\b に誤反応しないこと。"""
        sql = "SELECT created_at, updated_at, deleted_flag FROM `p.d.t`"
        self.assertEqual(validate_sql(sql), sql)

    def test_keyword_inside_string_literal(self):
        """文字列リテラル内の DELETE は書き込みではない。"""
        sql = "SELECT * FROM `p.d.t` WHERE memo = 'please DELETE this row'"
        self.assertEqual(validate_sql(sql), sql)

    def test_semicolon_inside_string_literal(self):
        """文字列内のセミコロンを複文と誤認しないこと。"""
        sql = "SELECT * FROM `p.d.t` WHERE note = 'a;b'"
        self.assertEqual(validate_sql(sql), sql)

    def test_keyword_inside_line_comment(self):
        sql = "SELECT 1 -- drop table if this were real\n"
        self.assertEqual(validate_sql(sql), sql.strip())

    def test_keyword_inside_block_comment(self):
        sql = "SELECT COUNT(*) FROM `p.d.t` /* insert 用の元テーブル */"
        self.assertEqual(validate_sql(sql), sql)


class ValidateSqlRejectsWrites(unittest.TestCase):
    def assert_rejected(self, sql: str):
        with self.assertRaises(QueryRejected):
            validate_sql(sql)

    def test_empty(self):
        self.assert_rejected("")
        self.assert_rejected("   \n  ")

    def test_dml(self):
        for sql in (
            "DELETE FROM `p.d.t`",
            "INSERT INTO `p.d.t` VALUES (1)",
            "UPDATE `p.d.t` SET a = 1",
            "MERGE `p.d.t` USING x ON TRUE",
            "TRUNCATE TABLE `p.d.t`",
        ):
            with self.subTest(sql=sql):
                self.assert_rejected(sql)

    def test_ddl(self):
        for sql in (
            "CREATE TABLE x AS SELECT 1",
            "CREATE OR REPLACE VIEW v AS SELECT 1",
            "DROP TABLE `p.d.t`",
            "ALTER TABLE `p.d.t` ADD COLUMN a INT64",
        ):
            with self.subTest(sql=sql):
                self.assert_rejected(sql)

    def test_multiple_statements(self):
        self.assert_rejected("SELECT 1; SELECT 2")
        self.assert_rejected("SELECT 1; DROP TABLE t")

    def test_write_hidden_after_valid_select(self):
        """先頭が SELECT でも、後続に書き込み文が続くものは弾く。"""
        self.assert_rejected("select * from t union all select * from u; update t set a = 1")

    def test_script_keywords(self):
        self.assert_rejected("BEGIN SELECT 1; END")
        self.assert_rejected("CALL my_proc()")


class ToJsonable(unittest.TestCase):
    def test_passthrough_scalars(self):
        self.assertEqual(_to_jsonable(None, 10), None)
        self.assertEqual(_to_jsonable(3, 10), 3)
        self.assertEqual(_to_jsonable(2.5, 10), 2.5)
        self.assertIs(_to_jsonable(True, 10), True)

    def test_decimal_becomes_float(self):
        self.assertEqual(_to_jsonable(decimal.Decimal("1.5"), 10), 1.5)

    def test_dates_become_iso(self):
        self.assertEqual(_to_jsonable(datetime.date(2026, 8, 3), 30), "2026-08-03")

    def test_nested_containers(self):
        self.assertEqual(_to_jsonable({"a": [1, 2]}, 10), {"a": [1, 2]})

    def test_long_string_is_truncated(self):
        out = _to_jsonable("x" * 50, 10)
        self.assertTrue(out.startswith("x" * 10))
        self.assertIn("省略", out)

    def test_bytes_are_summarized(self):
        self.assertEqual(_to_jsonable(b"abc", 10), "<bytes len=3>")


if __name__ == "__main__":
    unittest.main()
