#!/usr/bin/env python3
"""自律ループ本体。

1回の実行の中で「クエリを投げる → 結果を見て次のクエリを決める」を繰り返し、
十分な根拠が集まったところで日本語のレポートを書かせる。

ループはツールランナーではなく手書きにしている。理由は3つ:
  - 往復数の上限に達したときに、途中で打ち切るのではなく必ず締めのレポートを書かせたい
  - そのために会話履歴（messages）を自分で保持する必要がある
  - 1往復ごとの進捗を標準出力に流し、長時間の実行を人間が追えるようにしたい
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any

import anthropic

from .bq import BigQueryRunner
from .prompts import REPORT_INSTRUCTION, SYSTEM_PROMPT, build_kickoff_message
from .tools import TOOL_DEFINITIONS, AgentContext, execute_tool


@dataclass
class AgentResult:
    report_markdown: str
    findings: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    query_log: list[Any]
    turns: int
    tool_calls: int
    input_tokens: int = 0
    output_tokens: int = 0
    stopped_reason: str = "end_turn"
    warnings: list[str] = field(default_factory=list)


def _log(message: str) -> None:
    """進捗を標準エラーに流す。SHOSHIN_QUIET=1 で抑制できる（テスト・cron 用）。"""
    if os.environ.get("SHOSHIN_QUIET"):
        return
    print(message, file=sys.stderr, flush=True)


def _text_of(content: list[Any]) -> str:
    return "\n".join(b.text for b in content if getattr(b, "type", None) == "text").strip()


def run_agent(
    runner: BigQueryRunner,
    business: dict[str, Any],
    claude_config: dict[str, Any],
    focus: str | None = None,
    client: anthropic.Anthropic | None = None,
) -> AgentResult:
    """証拠集めループを回し、最後にレポートを生成して返す。"""

    client = client or anthropic.Anthropic()
    model = claude_config.get("model", "claude-opus-5")
    effort = claude_config.get("effort", "high")
    max_tokens = int(claude_config.get("max_tokens", 16000))
    max_turns = int(claude_config.get("max_turns", 40))

    ctx = AgentContext(runner=runner)
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": build_kickoff_message(business, focus)}
    ]

    warnings: list[str] = []
    input_tokens = 0
    output_tokens = 0
    turns = 0
    stopped_reason = "end_turn"

    _log(f"[agent] モデル={model} effort={effort} 最大往復={max_turns}")

    # --- フェーズ1: 証拠集め ---
    while turns < max_turns:
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
            )
        except anthropic.APIStatusError as exc:
            warnings.append(f"API エラーのため証拠集めを中断しました: {exc.status_code} {exc.message}")
            _log(f"[agent] API エラー: {exc}")
            stopped_reason = "api_error"
            break
        except anthropic.APIConnectionError as exc:
            warnings.append(f"接続エラーのため証拠集めを中断しました: {exc}")
            _log(f"[agent] 接続エラー: {exc}")
            stopped_reason = "connection_error"
            break

        turns += 1
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens

        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            category = getattr(detail, "category", None) if detail else None
            warnings.append(f"モデルが応答を拒否しました（category={category}）。")
            _log("[agent] 応答が拒否されました。証拠集めを終了します。")
            stopped_reason = "refusal"
            break

        messages.append({"role": "assistant", "content": response.content})

        # サーバー側ツールの反復上限。ユーザーメッセージを足さずに再送する。
        if response.stop_reason == "pause_turn":
            continue

        if response.stop_reason == "max_tokens":
            warnings.append(
                f"{turns} 往復目で出力トークン上限に達しました。"
                "claude.max_tokens を増やすと分析が深くなります。"
            )
            _log("[agent] 出力トークン上限に到達。証拠集めを終了します。")
            stopped_reason = "max_tokens"
            break

        if response.stop_reason != "tool_use":
            _log(f"[agent] {turns} 往復で証拠集め完了（stop_reason={response.stop_reason}）")
            break

        tool_results: list[dict[str, Any]] = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            label = block.input.get("purpose") or block.input.get("title") or ""
            _log(f"[{turns:>2}] {block.name}  {label}"[:160])

            text, is_error = execute_tool(ctx, block.name, dict(block.input))
            if is_error:
                _log(f"      ↳ {text.splitlines()[0][:140]}")

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": text,
                    "is_error": is_error,
                }
            )

        # 並列ツール呼び出しの結果は必ず1つの user メッセージにまとめて返す
        messages.append({"role": "user", "content": tool_results})
    else:
        warnings.append(
            f"往復数の上限（{max_turns}）に達したため証拠集めを打ち切りました。"
            "claude.max_turns を増やすとより深く分析します。"
        )
        _log(f"[agent] 往復上限 {max_turns} に到達。レポート生成に移ります。")
        stopped_reason = "max_turns"

    _log(
        f"[agent] 証拠集め終了: findings={len(ctx.findings)} actions={len(ctx.actions)} "
        f"クエリ={len([e for e in runner.query_log if e.status == 'ok'])}本 "
        f"スキャン={runner.total_bytes_processed / 1_000_000_000:.2f}GB"
    )

    # --- フェーズ2: レポート生成 ---
    # ツールは宣言したまま tool_choice=none で強制的に本文を書かせる。
    # ツール定義を外すと履歴の tool_use ブロックと整合しなくなるため。
    messages.append({"role": "user", "content": REPORT_INSTRUCTION})

    report = ""
    try:
        with client.messages.stream(
            model=model,
            max_tokens=max(max_tokens, 32000),
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=TOOL_DEFINITIONS,
            tool_choice={"type": "none"},
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
        ) as stream:
            final = stream.get_final_message()
        input_tokens += final.usage.input_tokens
        output_tokens += final.usage.output_tokens
        report = _text_of(final.content)
        if final.stop_reason == "max_tokens":
            warnings.append("レポート本文が出力トークン上限で途中終了した可能性があります。")
    except Exception as exc:
        warnings.append(f"レポート生成に失敗しました: {type(exc).__name__}: {exc}")
        _log(f"[agent] レポート生成エラー: {exc}")

    if not report:
        report = (
            "## 結論\n\n"
            "レポート本文を生成できませんでした。以下の findings / actions と"
            "クエリログを直接確認してください。\n"
        )

    return AgentResult(
        report_markdown=report,
        findings=ctx.findings,
        actions=ctx.actions,
        query_log=runner.query_log,
        turns=turns,
        tool_calls=ctx.tool_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stopped_reason=stopped_reason,
        warnings=warnings,
    )
