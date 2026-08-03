#!/usr/bin/env python3
"""レポート生成のテスト。"""

from __future__ import annotations

import json
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from shoshin_agent.bq import QueryLogEntry  # noqa: E402
from shoshin_agent.report import build_json, build_markdown  # noqa: E402


@dataclass
class FakeResult:
    report_markdown: str = "## 結論\n本文"
    findings: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    query_log: list = field(default_factory=list)
    turns: int = 5
    tool_calls: int = 12
    input_tokens: int = 1000
    output_tokens: int = 200
    stopped_reason: str = "end_turn"
    warnings: list = field(default_factory=list)


BUSINESS = {"name": "さくら動物病院", "type": "動物病院", "goal": "初診増"}


def action(title, priority, effort, expected):
    return {
        "title": title,
        "target": "対象",
        "hypothesis": "仮説",
        "how": "打ち手",
        "expected_new_patients_per_month": expected,
        "estimate_basis": "根拠",
        "cost": "コスト",
        "effort": effort,
        "priority": priority,
        "measurement": "測定",
    }


class ActionOrdering(unittest.TestCase):
    def test_sorted_by_priority_then_effort_then_impact(self):
        result = FakeResult(
            actions=[
                action("低優先", "低", "今週できる", 100),
                action("高・重い", "高", "3ヶ月がかり", 30),
                action("高・軽い・小", "高", "今週できる", 5),
                action("高・軽い・大", "高", "今週できる", 20),
            ]
        )
        titles = [a["title"] for a in build_json(result, BUSINESS)["actions"]]
        self.assertEqual(
            titles, ["高・軽い・大", "高・軽い・小", "高・重い", "低優先"]
        )

    def test_missing_expected_value_does_not_crash(self):
        a = action("値なし", "中", "1ヶ月以内", None)
        md = build_markdown(FakeResult(actions=[a]), BUSINESS)
        self.assertIn("値なし", md)


class MarkdownStructure(unittest.TestCase):
    def setUp(self):
        self.result = FakeResult(
            report_markdown="## 結論\n土曜午前の枠不足が最大の機会損失。",
            findings=[
                {
                    "category": "初診の定義",
                    "title": "患者IDの最小来院日で定義",
                    "detail": "直近12ヶ月で512件",
                    "metric_value": "月平均42.7件",
                    "evidence_sql": "SELECT COUNT(*) FROM t",
                    "implication": "分析の土台を確定した",
                }
            ],
            actions=[action("土曜午前の枠増設", "高", "今週できる", 8)],
            query_log=[
                QueryLogEntry(
                    "初診数の確認",
                    "SELECT 1",
                    "ok",
                    rows=12,
                    bytes_processed=1_500_000_000,
                    elapsed_seconds=2.1,
                ),
                QueryLogEntry("誤SQL", "DELETE FROM t", "rejected", detail="読み取り専用のみ"),
            ],
            warnings=["往復数の上限に達しました。"],
        )
        self.md = build_markdown(self.result, BUSINESS, focus="土日の取りこぼし")

    def test_header_contains_business_and_focus(self):
        self.assertIn("さくら動物病院", self.md)
        self.assertIn("土日の取りこぼし", self.md)

    def test_warnings_are_surfaced(self):
        self.assertIn("往復数の上限に達しました。", self.md)

    def test_agent_body_is_embedded(self):
        self.assertIn("土曜午前の枠不足が最大の機会損失。", self.md)

    def test_sections_present(self):
        for heading in ("## 施策一覧（構造化）", "## 分析で判明した事実", "## 実行クエリログ"):
            self.assertIn(heading, self.md)

    def test_query_log_marks_status(self):
        self.assertIn("✅", self.md)
        self.assertIn("⛔", self.md)
        self.assertIn("読み取り専用のみ", self.md)

    def test_scan_volume_reported(self):
        self.assertIn("1.50 GB", self.md)

    def test_evidence_sql_is_fenced(self):
        self.assertIn("```sql", self.md)
        self.assertIn("SELECT COUNT(*) FROM t", self.md)


class EmptyRun(unittest.TestCase):
    def test_no_findings_no_actions_still_renders(self):
        md = build_markdown(FakeResult(), BUSINESS)
        self.assertIn("初診アップ分析レポート", md)
        self.assertIn("クエリは実行されませんでした", md)


class JsonArchive(unittest.TestCase):
    def test_is_serializable_and_has_expected_keys(self):
        result = FakeResult(
            actions=[action("a", "高", "今週できる", 3)],
            query_log=[QueryLogEntry("p", "SELECT 1", "ok", rows=1)],
        )
        payload = build_json(result, BUSINESS, focus="f")
        # dataclass を含んでいても JSON 化できること
        text = json.dumps(payload, ensure_ascii=False)
        self.assertIn("初診増", text)
        for key in ("generated_at", "business", "stats", "findings", "actions", "query_log"):
            self.assertIn(key, payload)
        self.assertEqual(payload["stats"]["queries_ok"], 1)
        self.assertEqual(payload["query_log"][0]["purpose"], "p")


if __name__ == "__main__":
    unittest.main()
