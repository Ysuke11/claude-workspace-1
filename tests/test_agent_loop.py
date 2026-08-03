#!/usr/bin/env python3
"""自律ループのテスト。

Claude API と BigQuery を両方モックし、ループの制御フローだけを検証する。
とくに重要なのは次の3点:
  - 1ターンに複数のツール呼び出しがあっても、結果は必ず1つの user メッセージにまとめる
    （分割すると並列ツール呼び出しが抑制されてしまう）
  - 往復上限に達しても打ち切らず、必ず締めのレポートを生成する
  - レポート生成時は tool_choice=none でツールを封じる
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("SHOSHIN_QUIET", "1")  # 進捗ログでテスト出力を汚さない
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from shoshin_agent.agent import run_agent  # noqa: E402
from shoshin_agent.bq import QueryLogEntry  # noqa: E402


# --- テストダブル -----------------------------------------------------------


class Block:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class Usage:
    def __init__(self, input_tokens=100, output_tokens=50):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class Response:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = Usage()
        self.stop_details = None


def tool_use(id_, name, **inputs):
    return Block(type="tool_use", id=id_, name=name, input=inputs)


def text(value):
    return Block(type="text", text=value)


class FakeRunner:
    """BigQueryRunner の代役。"""

    def __init__(self):
        self.query_log: list[QueryLogEntry] = []
        self.total_bytes_processed = 0

    def list_tables(self, dataset=None):
        return "karte\n- visits"

    def describe_table(self, dataset, table):
        return f"# {dataset}.{table}\n- patient_id: STRING"

    def run_query(self, sql, purpose=""):
        self.query_log.append(
            QueryLogEntry(purpose, sql, "ok", rows=3, bytes_processed=1, elapsed_seconds=0.1)
        )
        return "OK: 3 行"


class FakeStream:
    def __init__(self, recorder, kwargs, final):
        self.recorder = recorder
        self.kwargs = kwargs
        self.final = final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        self.recorder["report_kwargs"] = self.kwargs
        return self.final


class FakeMessages:
    def __init__(self, script, recorder, report_text="## 結論\nテスト用レポート"):
        self.script = list(script)
        self.recorder = recorder
        self.report_text = report_text

    def create(self, **kwargs):
        # run_agent は messages リストを in-place で伸ばしていくため、
        # 参照のまま保持すると全呼び出しが最終状態を指してしまう。呼び出し時点で複製する。
        snapshot = dict(kwargs, messages=list(kwargs["messages"]))
        self.recorder["create_calls"].append(snapshot)
        if self.script:
            return self.script.pop(0)
        # 台本を使い切ったら以降はツールを呼び続ける（上限テスト用）
        return Response([tool_use("loop", "list_tables")], "tool_use")

    def stream(self, **kwargs):
        return FakeStream(
            self.recorder, kwargs, Response([text(self.report_text)], "end_turn")
        )


class FakeClient:
    def __init__(self, script, recorder, report_text="## 結論\nテスト用レポート"):
        self.messages = FakeMessages(script, recorder, report_text)


BUSINESS = {"type": "動物病院", "goal": "初診増"}
CONFIG = {"model": "test-model", "effort": "high", "max_tokens": 8000, "max_turns": 10}


# --- テスト -----------------------------------------------------------------


class HappyPath(unittest.TestCase):
    def setUp(self):
        self.recorder = {"create_calls": [], "report_kwargs": None}
        self.runner = FakeRunner()
        script = [
            Response(
                [
                    tool_use("t1", "list_tables"),
                    tool_use("t2", "describe_table", dataset="karte", table="visits"),
                ],
                "tool_use",
            ),
            Response(
                [
                    tool_use("t3", "run_query", purpose="月次初診数", sql="SELECT 1"),
                    tool_use(
                        "t4",
                        "record_finding",
                        category="全体トレンド",
                        title="初診は月42件",
                        detail="d",
                        evidence_sql="SELECT 1",
                        implication="i",
                    ),
                    tool_use(
                        "t5",
                        "propose_action",
                        title="枠増設",
                        target="t",
                        hypothesis="h",
                        how="w",
                        expected_new_patients_per_month=8,
                        estimate_basis="b",
                        cost="c",
                        effort="今週できる",
                        priority="高",
                        measurement="m",
                    ),
                ],
                "tool_use",
            ),
            Response([text("根拠が揃いました。")], "end_turn"),
        ]
        self.result = run_agent(
            self.runner, BUSINESS, CONFIG, client=FakeClient(script, self.recorder)
        )

    def test_stops_on_end_turn(self):
        self.assertEqual(self.result.stopped_reason, "end_turn")
        self.assertEqual(self.result.turns, 3)
        self.assertEqual(self.result.warnings, [])

    def test_accumulates_findings_and_actions(self):
        self.assertEqual(len(self.result.findings), 1)
        self.assertEqual(len(self.result.actions), 1)
        self.assertEqual(self.result.findings[0]["title"], "初診は月42件")
        self.assertEqual(self.result.tool_calls, 5)

    def test_query_log_is_recorded(self):
        self.assertEqual(len(self.result.query_log), 1)
        self.assertEqual(self.result.query_log[0].purpose, "月次初診数")

    def test_parallel_tool_results_batched_into_one_user_message(self):
        """2ターン目のリクエストで、1ターン目の2件の結果が1つの user にまとまっていること。"""
        second_request = self.recorder["create_calls"][1]["messages"]
        tool_result_messages = [
            m
            for m in second_request
            if m["role"] == "user" and isinstance(m["content"], list)
        ]
        self.assertEqual(len(tool_result_messages), 1)
        self.assertEqual(len(tool_result_messages[0]["content"]), 2)
        self.assertEqual(
            {b["tool_use_id"] for b in tool_result_messages[0]["content"]}, {"t1", "t2"}
        )

    def test_report_call_disables_tools(self):
        kwargs = self.recorder["report_kwargs"]
        self.assertEqual(kwargs["tool_choice"], {"type": "none"})
        self.assertIn("tools", kwargs)  # 履歴との整合のため定義自体は残す
        self.assertGreaterEqual(kwargs["max_tokens"], 32000)
        self.assertIn("テスト用レポート", self.result.report_markdown)

    def test_token_usage_is_summed(self):
        # 3 往復 + レポート生成1回 = 4 回分
        self.assertEqual(self.result.input_tokens, 400)
        self.assertEqual(self.result.output_tokens, 200)


class MaxTurnsCap(unittest.TestCase):
    def test_still_produces_report_and_warns(self):
        recorder = {"create_calls": [], "report_kwargs": None}
        config = dict(CONFIG, max_turns=2)
        result = run_agent(
            FakeRunner(), BUSINESS, config, client=FakeClient([], recorder)
        )
        self.assertEqual(result.stopped_reason, "max_turns")
        self.assertEqual(result.turns, 2)
        self.assertTrue(result.warnings)
        self.assertIn("上限", result.warnings[0])
        # 打ち切りでもレポートは必ず出す
        self.assertIn("テスト用レポート", result.report_markdown)


class ErrorHandling(unittest.TestCase):
    def test_unknown_tool_is_reported_as_error_not_crash(self):
        recorder = {"create_calls": [], "report_kwargs": None}
        script = [
            Response([tool_use("t1", "no_such_tool")], "tool_use"),
            Response([text("完了")], "end_turn"),
        ]
        result = run_agent(
            FakeRunner(), BUSINESS, CONFIG, client=FakeClient(script, recorder)
        )
        self.assertEqual(result.stopped_reason, "end_turn")
        results_msg = recorder["create_calls"][1]["messages"][-1]["content"]
        self.assertTrue(results_msg[0]["is_error"])
        self.assertIn("未知のツール", results_msg[0]["content"])

    def test_max_tokens_stop_reason_warns(self):
        recorder = {"create_calls": [], "report_kwargs": None}
        script = [Response([text("途中で切れた")], "max_tokens")]
        result = run_agent(
            FakeRunner(), BUSINESS, CONFIG, client=FakeClient(script, recorder)
        )
        self.assertEqual(result.stopped_reason, "max_tokens")
        self.assertTrue(any("トークン上限" in w for w in result.warnings))

    def test_refusal_is_handled(self):
        recorder = {"create_calls": [], "report_kwargs": None}
        script = [Response([], "refusal")]
        result = run_agent(
            FakeRunner(), BUSINESS, CONFIG, client=FakeClient(script, recorder)
        )
        self.assertEqual(result.stopped_reason, "refusal")
        self.assertTrue(any("拒否" in w for w in result.warnings))

    def test_pause_turn_resends_without_extra_user_message(self):
        """pause_turn は user メッセージを足さずに再送する。"""
        recorder = {"create_calls": [], "report_kwargs": None}
        script = [
            Response([text("サーバーツール実行中")], "pause_turn"),
            Response([text("完了")], "end_turn"),
        ]
        result = run_agent(
            FakeRunner(), BUSINESS, CONFIG, client=FakeClient(script, recorder)
        )
        self.assertEqual(result.turns, 2)
        second_request = recorder["create_calls"][1]["messages"]
        self.assertEqual(second_request[-1]["role"], "assistant")


if __name__ == "__main__":
    unittest.main()
