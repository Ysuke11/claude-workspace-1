#!/usr/bin/env python3
"""エージェントに渡すツール定義と、その実行ディスパッチャ。

ツールは4種類:
  list_tables / describe_table : スキーマ探索（BigQuery のメタデータ API。課金なし）
  run_query                    : 読み取り専用 SQL の実行（bq.BigQueryRunner が安全ガードを担う）
  record_finding               : 判明した事実を構造化して蓄積する
  propose_action               : 施策を構造化して蓄積する

record_finding / propose_action は「LLM に構造化出力を書かせるための器」であり、
副作用は蓄積のみ。レポート生成時にそのまま表として使う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .bq import BigQueryRunner

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "list_tables",
        "description": (
            "BigQuery のデータセットとテーブルの一覧を取得する。行数・サイズ・説明も返る。"
            "GA4 の events_YYYYMMDD のような日付シャードはまとめて表示される。"
            "分析の最初に必ず実行すること。課金は発生しない。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset": {
                    "type": "string",
                    "description": "特定のデータセットに絞る場合のみ指定。省略すると全データセットを一覧する。",
                }
            },
            "required": [],
        },
    },
    {
        "name": "describe_table",
        "description": (
            "テーブルのカラム定義（名前・型・モード・説明）とパーティション情報を取得する。"
            "SQL を書く前に必ずこれでカラム名を確認すること。課金は発生しない。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset": {"type": "string", "description": "データセット名"},
                "table": {
                    "type": "string",
                    "description": (
                        "テーブル名。GA4 のような日付シャードの場合は "
                        "events_20260801 のように具体的な1日分を指定する。"
                    ),
                },
            },
            "required": ["dataset", "table"],
        },
    },
    {
        "name": "run_query",
        "description": (
            "BigQuery で読み取り専用の SQL を実行する。SELECT または WITH で始まる単一文のみ。"
            "書き込み系（INSERT/UPDATE/DELETE/CREATE など）は実行前に拒否される。"
            "実行前に必ず dry run でスキャン量を見積もり、上限を超えるクエリは拒否される。"
            "返る行数には上限があるため、生ログではなく集約結果を取得すること。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "purpose": {
                    "type": "string",
                    "description": "このクエリで何を確かめたいか（1行。レポートのクエリログに残る）",
                },
                "sql": {
                    "type": "string",
                    "description": (
                        "実行する SQL。テーブルは `プロジェクト.データセット.テーブル` の形式で"
                        "バッククォート付きの完全修飾名で書くこと。"
                    ),
                },
            },
            "required": ["purpose", "sql"],
        },
    },
    {
        "name": "record_finding",
        "description": (
            "分析で判明した事実を1件記録する。数値と根拠を必ず添えること。"
            "最終レポートの「現状の数字」「ボトルネック」の材料になる。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": [
                        "データ構造",
                        "初診の定義",
                        "全体トレンド",
                        "流入経路",
                        "セグメント",
                        "地域",
                        "予約枠・稼働",
                        "サイト行動",
                        "広告",
                        "定着・再診",
                        "ボトルネック",
                    ],
                    "description": "この事実の分類",
                },
                "title": {"type": "string", "description": "事実の見出し（20〜40文字程度）"},
                "detail": {
                    "type": "string",
                    "description": "具体的な数値を含む説明。実数と率の両方を書くこと。",
                },
                "metric_value": {
                    "type": "string",
                    "description": "代表的な数値（例: 月平均42件、転換率38.2%）。無ければ空文字。",
                },
                "evidence_sql": {
                    "type": "string",
                    "description": "この事実の根拠となったクエリ（実行した SQL をそのまま）",
                },
                "implication": {
                    "type": "string",
                    "description": "この事実が初診にとって何を意味するか（1〜2文）",
                },
            },
            "required": ["category", "title", "detail", "evidence_sql", "implication"],
        },
    },
    {
        "name": "propose_action",
        "description": (
            "初診を増やすための施策を1件記録する。抽象論は不可。"
            "誰に・何を・どこで・いつ・いくらで、まで具体化すること。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "施策名（30文字程度）"},
                "target": {"type": "string", "description": "対象（誰に）。分析で特定したセグメントを書く。"},
                "hypothesis": {
                    "type": "string",
                    "description": "どの分析結果を根拠に、なぜこれが効くと考えるか",
                },
                "how": {
                    "type": "string",
                    "description": "具体的な打ち手。何を・どこで・いつ実施するか。",
                },
                "expected_new_patients_per_month": {
                    "type": "number",
                    "description": "想定される初診の増加数（件/月）",
                },
                "estimate_basis": {
                    "type": "string",
                    "description": "上記の数値をどう計算したか。分析で出した実数から逆算すること。",
                },
                "cost": {"type": "string", "description": "想定コスト（金額と、なぜその額か）"},
                "effort": {
                    "type": "string",
                    "enum": ["今週できる", "1ヶ月以内", "3ヶ月がかり"],
                    "description": "着手から実行までの重さ",
                },
                "priority": {
                    "type": "string",
                    "enum": ["高", "中", "低"],
                    "description": "優先度。効果 ÷ 工数 で判断する。",
                },
                "measurement": {
                    "type": "string",
                    "description": "効果測定の方法。見るべき指標と、次回検証に使うクエリの方針。",
                },
            },
            "required": [
                "title",
                "target",
                "hypothesis",
                "how",
                "expected_new_patients_per_month",
                "estimate_basis",
                "cost",
                "effort",
                "priority",
                "measurement",
            ],
        },
    },
]


@dataclass
class AgentContext:
    """1回の実行を通じて共有される状態。"""

    runner: BigQueryRunner
    findings: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: int = 0


def execute_tool(ctx: AgentContext, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
    """ツールを実行し (結果テキスト, エラーか) を返す。

    例外は握りつぶしてテキストとして返す。エージェントに自己修正させるため、
    ここで実行を止めない。
    """
    ctx.tool_calls += 1
    try:
        if name == "list_tables":
            return ctx.runner.list_tables(dataset=tool_input.get("dataset") or None), False

        if name == "describe_table":
            return (
                ctx.runner.describe_table(tool_input["dataset"], tool_input["table"]),
                False,
            )

        if name == "run_query":
            text = ctx.runner.run_query(
                sql=tool_input["sql"], purpose=tool_input.get("purpose", "")
            )
            return text, text.startswith("NG")

        if name == "record_finding":
            ctx.findings.append(dict(tool_input))
            return (
                f"記録しました（findings 計 {len(ctx.findings)} 件）: {tool_input.get('title', '')}",
                False,
            )

        if name == "propose_action":
            ctx.actions.append(dict(tool_input))
            return (
                f"記録しました（actions 計 {len(ctx.actions)} 件）: {tool_input.get('title', '')}",
                False,
            )

        return f"未知のツールです: {name}", True

    except KeyError as exc:
        return f"必須パラメータが不足しています: {exc}", True
    except Exception as exc:  # ツールの失敗で実行全体を落とさない
        return f"ツール実行中にエラーが発生しました: {type(exc).__name__}: {exc}", True
