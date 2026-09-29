#!/usr/bin/env python3
"""分析結果を Markdown レポートと JSON アーカイブに書き出す。"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

JST = timezone(timedelta(hours=9))

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_DIR = REPO_ROOT / "reports" / "shoshin"
DATA_DIR = REPO_ROOT / "data" / "shoshin"

_EFFORT_ORDER = {"今週できる": 0, "1ヶ月以内": 1, "3ヶ月がかり": 2}
_PRIORITY_ORDER = {"高": 0, "中": 1, "低": 2}


def _sort_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """優先度 → 工数の軽さ → 想定効果の大きさ の順に並べる。"""
    return sorted(
        actions,
        key=lambda a: (
            _PRIORITY_ORDER.get(a.get("priority", "中"), 1),
            _EFFORT_ORDER.get(a.get("effort", "1ヶ月以内"), 1),
            -float(a.get("expected_new_patients_per_month") or 0),
        ),
    )


def _fence(sql: str) -> str:
    return "```sql\n" + (sql or "").strip() + "\n```"


def build_markdown(result: Any, business: dict[str, Any], focus: str | None = None) -> str:
    """AgentResult から人が読むレポートを組み立てる。"""
    now = datetime.now(JST)
    name = business.get("name") or business.get("type", "動物病院")

    ok_queries = [e for e in result.query_log if e.status == "ok"]
    scanned_gb = sum(e.bytes_processed for e in result.query_log) / 1_000_000_000

    out: list[str] = [
        f"# 初診アップ分析レポート — {name}",
        "",
        f"- 生成日時: {now:%Y-%m-%d %H:%M} JST",
        f"- ゴール: {business.get('goal', '初診（新規来院）件数の増加')}",
    ]
    if focus:
        out.append(f"- 今回の着眼点: {focus}")
    out += [
        f"- 分析ループ: {result.turns} 往復 / ツール呼び出し {result.tool_calls} 回",
        f"- 実行クエリ: {len(ok_queries)} 本（スキャン {scanned_gb:.2f} GB）",
        f"- 記録した事実: {len(result.findings)} 件 / 施策: {len(result.actions)} 件",
        "",
    ]

    if result.warnings:
        out.append("> **注意**")
        out.extend(f"> - {w}" for w in result.warnings)
        out.append("")

    out.append("---")
    out.append("")
    out.append(result.report_markdown.strip())
    out.append("")

    # --- 施策一覧（構造化データからの表） ---
    if result.actions:
        out += ["---", "", "## 施策一覧（構造化）", ""]
        out.append("| 優先度 | 工数 | 施策 | 対象 | 想定初診増(件/月) |")
        out.append("| --- | --- | --- | --- | ---: |")
        for a in _sort_actions(result.actions):
            expected = a.get("expected_new_patients_per_month")
            expected_text = f"{float(expected):g}" if expected is not None else "-"
            out.append(
                f"| {a.get('priority', '-')} | {a.get('effort', '-')} | {a.get('title', '')} "
                f"| {a.get('target', '')} | {expected_text} |"
            )
        out.append("")

        for i, a in enumerate(_sort_actions(result.actions), 1):
            out += [
                f"### {i}. {a.get('title', '')}",
                "",
                f"- **対象**: {a.get('target', '')}",
                f"- **仮説（根拠）**: {a.get('hypothesis', '')}",
                f"- **打ち手**: {a.get('how', '')}",
                f"- **想定初診増**: {a.get('expected_new_patients_per_month', '-')} 件/月",
                f"- **算出根拠**: {a.get('estimate_basis', '')}",
                f"- **コスト**: {a.get('cost', '')}",
                f"- **工数**: {a.get('effort', '')} / **優先度**: {a.get('priority', '')}",
                f"- **効果測定**: {a.get('measurement', '')}",
                "",
            ]

    # --- 事実 ---
    if result.findings:
        out += ["---", "", "## 分析で判明した事実", ""]
        by_category: dict[str, list[dict[str, Any]]] = {}
        for f in result.findings:
            by_category.setdefault(f.get("category", "その他"), []).append(f)

        for category, items in by_category.items():
            out.append(f"### {category}")
            out.append("")
            for f in items:
                out.append(f"**{f.get('title', '')}**")
                if f.get("metric_value"):
                    out.append(f"- 数値: {f['metric_value']}")
                out.append(f"- {f.get('detail', '')}")
                out.append(f"- 示唆: {f.get('implication', '')}")
                if f.get("evidence_sql"):
                    out.append("")
                    out.append("<details><summary>根拠クエリ</summary>")
                    out.append("")
                    out.append(_fence(f["evidence_sql"]))
                    out.append("")
                    out.append("</details>")
                out.append("")

    # --- クエリログ ---
    out += ["---", "", "## 実行クエリログ", ""]
    if not result.query_log:
        out.append("（クエリは実行されませんでした）")
    for i, e in enumerate(result.query_log, 1):
        mark = {"ok": "✅", "rejected": "⛔", "error": "❌"}.get(e.status, "・")
        head = f"{mark} **{i}. {e.purpose or '(目的の記載なし)'}**"
        if e.status == "ok":
            head += f" — {e.rows} 行 / {e.bytes_processed / 1_000_000_000:.3f} GB / {e.elapsed_seconds:.1f} 秒"
        elif e.detail:
            head += f" — {e.detail}"
        out.append(head)
        out.append("")
        out.append("<details><summary>SQL</summary>")
        out.append("")
        out.append(_fence(e.sql))
        out.append("")
        out.append("</details>")
        out.append("")

    out += [
        "---",
        "",
        f"_トークン使用量: 入力 {result.input_tokens:,} / 出力 {result.output_tokens:,}_",
        "",
    ]
    return "\n".join(out)


def build_json(result: Any, business: dict[str, Any], focus: str | None = None) -> dict[str, Any]:
    """機械可読なアーカイブ。"""
    now = datetime.now(JST)
    return {
        "generated_at": now.isoformat(),
        "business": business,
        "focus": focus,
        "stopped_reason": result.stopped_reason,
        "warnings": result.warnings,
        "stats": {
            "turns": result.turns,
            "tool_calls": result.tool_calls,
            "queries_ok": len([e for e in result.query_log if e.status == "ok"]),
            "bytes_processed": sum(e.bytes_processed for e in result.query_log),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        },
        "findings": result.findings,
        "actions": _sort_actions(result.actions),
        "query_log": [dataclasses.asdict(e) for e in result.query_log],
        "report_markdown": result.report_markdown,
    }


def write_outputs(
    result: Any, business: dict[str, Any], focus: str | None = None
) -> tuple[Path, Path, Path]:
    """Markdown / latest.md / JSON を書き出し、そのパスを返す。"""
    now = datetime.now(JST)
    stamp = f"{now:%Y-%m-%d-%H%M}"

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    markdown = build_markdown(result, business, focus)

    md_path = REPORT_DIR / f"{stamp}.md"
    md_path.write_text(markdown, encoding="utf-8")

    latest_path = REPORT_DIR / "latest.md"
    latest_path.write_text(markdown, encoding="utf-8")

    json_path = DATA_DIR / f"{stamp}.json"
    json_path.write_text(
        json.dumps(build_json(result, business, focus), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return md_path, latest_path, json_path
