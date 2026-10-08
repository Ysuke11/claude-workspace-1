#!/usr/bin/env python3
"""初診アップ分析エージェントの実行スクリプト。

  python scripts/run_shoshin_agent.py
  python scripts/run_shoshin_agent.py --focus "土日の予約枠の取りこぼしを見たい"
  python scripts/run_shoshin_agent.py --check          # 接続確認とスキーマ一覧だけ
  python scripts/run_shoshin_agent.py --max-turns 15   # 短く回す（お試し）

必要な環境変数:
  ANTHROPIC_API_KEY     Claude API キー
  GOOGLE_CLOUD_PROJECT  BigQuery のプロジェクト（config/shoshin.yml で指定していれば不要）

BigQuery の認証は ADC を使う:
  gcloud auth application-default login
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# scripts/ をパッケージルートとして解決できるようにする
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import yaml

    from shoshin_agent.agent import run_agent
    from shoshin_agent.bq import BigQueryRunner, BigQueryUnavailable, Limits
    from shoshin_agent.report import write_outputs
except ModuleNotFoundError as exc:  # 依存が揃っていないときは生の traceback を見せない
    sys.exit(
        f"必要なライブラリが見つかりません（{exc.name}）。次のコマンドで導入してください:\n"
        "  pip install -r scripts/shoshin_agent/requirements.txt"
    )

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config" / "shoshin.yml"


def load_config(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"設定ファイルが見つかりません: {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_runner(config: dict) -> BigQueryRunner:
    bq_conf = config.get("bigquery") or {}
    limits_conf = bq_conf.get("limits") or {}

    project = bq_conf.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT") or None

    return BigQueryRunner(
        project_id=project,
        location=bq_conf.get("location") or None,
        allowed_datasets=bq_conf.get("allowed_datasets") or [],
        denied_datasets=bq_conf.get("denied_datasets") or [],
        limits=Limits(
            max_bytes_billed=int(limits_conf.get("max_bytes_billed", 5_000_000_000)),
            max_rows=int(limits_conf.get("max_rows", 200)),
            max_cell_chars=int(limits_conf.get("max_cell_chars", 300)),
            query_timeout_seconds=int(limits_conf.get("query_timeout_seconds", 180)),
            max_queries_per_run=int(limits_conf.get("max_queries_per_run", 60)),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="動物病院の初診アップ分析エージェント")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="設定ファイルのパス")
    parser.add_argument("--focus", type=str, default=None, help="今回とくに知りたいことを1文で")
    parser.add_argument("--max-turns", type=int, default=None, help="証拠集めの最大往復数を上書き")
    parser.add_argument("--model", type=str, default=None, help="使用モデルを上書き")
    parser.add_argument(
        "--effort",
        type=str,
        default=None,
        choices=["low", "medium", "high", "xhigh", "max"],
        help="推論の深さを上書き",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="BigQuery への接続確認とスキーマ一覧の表示だけ行い、エージェントは動かさない",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    business = config.get("business") or {}
    claude_config = dict(config.get("claude") or {})

    if args.max_turns is not None:
        claude_config["max_turns"] = args.max_turns
    if args.model:
        claude_config["model"] = args.model
    if args.effort:
        claude_config["effort"] = args.effort

    try:
        runner = build_runner(config)
    except BigQueryUnavailable as exc:
        sys.exit(str(exc))
    except Exception as exc:
        sys.exit(
            f"BigQuery に接続できませんでした: {exc}\n"
            "認証は `gcloud auth application-default login` で設定してください。\n"
            "プロジェクトは config/shoshin.yml の bigquery.project_id か "
            "環境変数 GOOGLE_CLOUD_PROJECT で指定します。"
        )

    if args.check:
        print(f"BigQuery 接続 OK（プロジェクト: {runner.project_id}）\n")
        print(runner.list_tables())
        return

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "警告: ANTHROPIC_API_KEY が未設定です。"
            "`ant auth login` のプロファイルがあればそちらを使います。",
            file=sys.stderr,
        )

    result = run_agent(
        runner=runner,
        business=business,
        claude_config=claude_config,
        focus=args.focus,
    )

    md_path, latest_path, json_path = write_outputs(result, business, args.focus)

    print()
    print("レポートを書き出しました:")
    print(f"  {md_path.relative_to(REPO_ROOT)}")
    print(f"  {latest_path.relative_to(REPO_ROOT)}")
    print(f"  {json_path.relative_to(REPO_ROOT)}")
    print()
    print(
        f"事実 {len(result.findings)} 件 / 施策 {len(result.actions)} 件 / "
        f"クエリ {len([e for e in result.query_log if e.status == 'ok'])} 本"
    )
    for w in result.warnings:
        print(f"  ! {w}")


if __name__ == "__main__":
    main()
