#!/usr/bin/env python3
"""BigQuery への読み取り専用アクセスと安全ガード。

エージェント（LLM）が生成した SQL をそのまま実行するため、次の多層防御をかける:

  1. 構文ガード  : コメントと文字列リテラルを除去した「骨格」を検査し、
                   SELECT / WITH で始まる単一文以外を拒否する
  2. コストガード : 本実行の前に必ず dry run し、スキャン量が上限を超えたら実行しない
  3. 実行ガード   : maximum_bytes_billed とタイムアウトを必ず設定する
  4. 出力ガード   : 返す行数とセルの文字数を打ち切る（コンテキスト肥大の防止）

スキーマ探索（データセット/テーブル/カラム一覧）は SQL ではなく BigQuery の
メタデータ API を使う。課金が発生せず、INFORMATION_SCHEMA の方言差も踏まない。
"""

from __future__ import annotations

import datetime as _dt
import decimal
import re
import time
from dataclasses import dataclass, field
from typing import Any

try:  # 依存が無い環境でも import 自体は通し、実行時に案内する
    from google.cloud import bigquery
except ImportError:  # pragma: no cover
    bigquery = None  # type: ignore[assignment]


class BigQueryUnavailable(RuntimeError):
    """google-cloud-bigquery が未インストール、または認証が無い。"""


class QueryRejected(Exception):
    """安全ガードによりクエリの実行を拒否した（BigQuery には送っていない）。"""


# --- SQL 構文ガード ---------------------------------------------------------

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
# シングル/ダブルクォート文字列（エスケープ対応）。バッククォートは識別子なので残す。
_STRING_LITERAL = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")

# 読み取り以外の操作。\b で囲むため created_at や updated_at には誤反応しない。
_FORBIDDEN = re.compile(
    r"\b("
    r"insert|update|delete|merge|truncate|drop|alter|grant|revoke|"
    r"create|replace|export|load|begin|commit|rollback|call|execute"
    r")\b",
    re.IGNORECASE,
)


def _skeleton(sql: str) -> str:
    """コメントと文字列リテラルを取り除いた検査用の骨格を返す。"""
    stripped = _BLOCK_COMMENT.sub(" ", sql)
    stripped = _LINE_COMMENT.sub(" ", stripped)
    stripped = _STRING_LITERAL.sub("''", stripped)
    return stripped


def validate_sql(sql: str) -> str:
    """読み取り専用の単一 SELECT 文であることを検証し、整形した SQL を返す。

    問題があれば QueryRejected を送出する。例外メッセージはそのまま
    エージェントへのツール結果として返され、自己修正の材料になる。
    """
    if not sql or not sql.strip():
        raise QueryRejected("SQL が空です。")

    body = _skeleton(sql)

    # 末尾のセミコロンは許容し、それ以外の位置にあるものは複文とみなす
    trimmed = body.strip().rstrip(";")
    if ";" in trimmed:
        raise QueryRejected(
            "複数のステートメントは実行できません。1回のツール呼び出しにつき SELECT 文を1つだけ渡してください。"
        )

    head = trimmed.lstrip().lower()
    if not (head.startswith("select") or head.startswith("with")):
        raise QueryRejected(
            "SELECT または WITH で始まるクエリのみ実行できます（読み取り専用）。"
        )

    forbidden = _FORBIDDEN.search(trimmed)
    if forbidden:
        raise QueryRejected(
            f"書き込み系のキーワード '{forbidden.group(1)}' が含まれているため実行できません。"
            "読み取り専用の SELECT に書き換えてください。"
            "（列名がキーワードと衝突する場合はバッククォートで囲んでください）"
        )

    return sql.strip().rstrip(";")


# --- 値のシリアライズ -------------------------------------------------------


def _to_jsonable(value: Any, max_chars: int) -> Any:
    """BigQuery の値を JSON 化できる形に落とす。長い値は切り詰める。"""
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, int) or isinstance(value, float):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return f"<bytes len={len(value)}>"
    if isinstance(value, dict):
        return {k: _to_jsonable(v, max_chars) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v, max_chars) for v in value]

    text = str(value)
    if len(text) > max_chars:
        return text[:max_chars] + f"…(以下{len(text) - max_chars}文字を省略)"
    return text


# --- 実行結果 ---------------------------------------------------------------


@dataclass
class QueryResult:
    sql: str
    purpose: str
    columns: list[str]
    rows: list[list[Any]]
    total_rows: int
    truncated: bool
    bytes_processed: int
    elapsed_seconds: float
    cache_hit: bool = False

    def to_text(self) -> str:
        """エージェントに返すテキスト表現。"""
        gb = self.bytes_processed / 1_000_000_000
        lines = [
            f"OK: {self.total_rows} 行 / スキャン {gb:.3f} GB / {self.elapsed_seconds:.1f} 秒"
            + ("（キャッシュヒット）" if self.cache_hit else ""),
        ]
        if self.truncated:
            lines.append(
                f"※ 表示は先頭 {len(self.rows)} 行のみです（全 {self.total_rows} 行）。"
                "全件が必要なら集約するか LIMIT を付けて絞り込んでください。"
            )
        if not self.rows:
            lines.append("（該当行なし）")
            return "\n".join(lines)

        lines.append("")
        lines.append(" | ".join(self.columns))
        lines.append(" | ".join(["---"] * len(self.columns)))
        for row in self.rows:
            lines.append(" | ".join("" if v is None else str(v) for v in row))
        return "\n".join(lines)


@dataclass
class QueryLogEntry:
    purpose: str
    sql: str
    status: str  # "ok" | "rejected" | "error"
    detail: str = ""
    rows: int = 0
    bytes_processed: int = 0
    elapsed_seconds: float = 0.0


# --- ランナー ---------------------------------------------------------------


@dataclass
class Limits:
    max_bytes_billed: int = 5_000_000_000
    max_rows: int = 200
    max_cell_chars: int = 300
    query_timeout_seconds: int = 180
    max_queries_per_run: int = 60


class BigQueryRunner:
    """読み取り専用の BigQuery クライアント。"""

    def __init__(
        self,
        project_id: str | None = None,
        location: str | None = None,
        allowed_datasets: list[str] | None = None,
        denied_datasets: list[str] | None = None,
        limits: Limits | None = None,
    ) -> None:
        if bigquery is None:
            raise BigQueryUnavailable(
                "google-cloud-bigquery がインストールされていません。\n"
                "  pip install -r scripts/shoshin_agent/requirements.txt"
            )
        self.limits = limits or Limits()
        self.allowed_datasets = [d for d in (allowed_datasets or []) if d]
        self.denied_datasets = [d for d in (denied_datasets or []) if d]
        self.location = location or None
        self.client = bigquery.Client(project=project_id or None, location=self.location)
        self.project_id = self.client.project
        self.query_log: list[QueryLogEntry] = []

    # -- スキーマ探索（メタデータ API。課金なし） --

    def _dataset_ids(self) -> list[str]:
        ids = [ds.dataset_id for ds in self.client.list_datasets()]
        if self.allowed_datasets:
            ids = [d for d in ids if d in self.allowed_datasets]
        if self.denied_datasets:
            ids = [d for d in ids if d not in self.denied_datasets]
        return sorted(ids)

    def list_tables(self, dataset: str | None = None, max_tables: int = 300) -> str:
        """データセットとテーブルの一覧を返す。"""
        datasets = [dataset] if dataset else self._dataset_ids()
        if not datasets:
            return (
                "アクセスできるデータセットが見つかりません。"
                f"（プロジェクト: {self.project_id}）"
                "config/shoshin.yml の bigquery.project_id / allowed_datasets を確認してください。"
            )

        lines = [f"プロジェクト: {self.project_id}"]
        shown = 0
        for ds in datasets:
            try:
                tables = list(self.client.list_tables(f"{self.project_id}.{ds}"))
            except Exception as exc:  # データセット単位の失敗で全体を止めない
                lines.append(f"\n## {ds}\n  取得エラー: {exc}")
                continue

            lines.append(f"\n## {ds}  （{len(tables)} テーブル）")
            if not tables:
                lines.append("  (テーブルなし)")
                continue

            # GA4 の events_YYYYMMDD のような日付シャードは 1 行にまとめる
            shards: dict[str, list[str]] = {}
            plain: list[Any] = []
            for t in tables:
                m = re.match(r"^(.*?)(\d{8})$", t.table_id)
                if m:
                    shards.setdefault(m.group(1), []).append(m.group(2))
                else:
                    plain.append(t)

            for prefix, dates in sorted(shards.items()):
                dates.sort()
                lines.append(
                    f"  - {prefix}*  （日付シャード {len(dates)} 個: {dates[0]} 〜 {dates[-1]}）"
                    f"  → 例: `{self.project_id}.{ds}.{prefix}{dates[-1]}`"
                )
                shown += 1

            for t in plain:
                if shown >= max_tables:
                    lines.append(f"  … 以降は省略（上限 {max_tables} 件）")
                    break
                try:
                    meta = self.client.get_table(t.reference)
                    rows = f"{meta.num_rows:,} 行" if meta.num_rows is not None else "行数不明"
                    size = f"{(meta.num_bytes or 0) / 1_000_000:.1f} MB"
                    desc = f"  — {meta.description}" if meta.description else ""
                    lines.append(f"  - {t.table_id}  ({meta.table_type}, {rows}, {size}){desc}")
                except Exception as exc:
                    lines.append(f"  - {t.table_id}  (メタデータ取得エラー: {exc})")
                shown += 1

        lines.append(
            "\n※ カラム構成を見るには describe_table を使ってください（推測でカラム名を使わないこと）。"
        )
        return "\n".join(lines)

    def describe_table(self, dataset: str, table: str) -> str:
        """テーブルのカラム定義を返す。"""
        ref = f"{self.project_id}.{dataset}.{table}"
        try:
            meta = self.client.get_table(ref)
        except Exception as exc:
            return (
                f"テーブル {ref} の情報を取得できませんでした: {exc}\n"
                "list_tables でテーブル名を確認してください。"
                "（GA4 のような日付シャードは events_20260801 のように具体的な1日を指定します）"
            )

        lines = [f"# {ref}"]
        if meta.description:
            lines.append(f"説明: {meta.description}")
        rows = f"{meta.num_rows:,}" if meta.num_rows is not None else "不明"
        lines.append(f"種別: {meta.table_type} / 行数: {rows} / サイズ: {(meta.num_bytes or 0) / 1_000_000:.1f} MB")
        if meta.time_partitioning:
            lines.append(
                f"パーティション: {meta.time_partitioning.type_} on "
                f"{meta.time_partitioning.field or '_PARTITIONTIME'} "
                "（WHERE で必ず絞るとスキャン量を抑えられます）"
            )
        if meta.clustering_fields:
            lines.append(f"クラスタリング: {', '.join(meta.clustering_fields)}")

        lines.append("\n## カラム")

        def walk(fields: Any, prefix: str = "", depth: int = 0) -> None:
            for f in fields:
                name = f"{prefix}{f.name}"
                desc = f"  — {f.description}" if f.description else ""
                lines.append(f"{'  ' * depth}- {name}: {f.field_type} ({f.mode}){desc}")
                if f.field_type in ("RECORD", "STRUCT") and f.fields:
                    walk(f.fields, prefix=f"{name}.", depth=depth + 1)

        walk(meta.schema)
        return "\n".join(lines)

    # -- クエリ実行 --

    def run_query(self, sql: str, purpose: str = "") -> str:
        if len([e for e in self.query_log if e.status == "ok"]) >= self.limits.max_queries_per_run:
            msg = (
                f"このセッションのクエリ実行上限（{self.limits.max_queries_per_run} 本）に達しました。"
                "これ以上の探索はやめ、いま得られている根拠でレポートをまとめてください。"
            )
            self.query_log.append(QueryLogEntry(purpose, sql, "rejected", msg))
            return f"NG: {msg}"

        try:
            clean_sql = validate_sql(sql)
        except QueryRejected as exc:
            self.query_log.append(QueryLogEntry(purpose, sql, "rejected", str(exc)))
            return f"NG（実行前に拒否）: {exc}"

        # 1. dry run でスキャン量を見積もる
        try:
            dry = self.client.query(
                clean_sql,
                job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False),
                location=self.location,
            )
            estimated = dry.total_bytes_processed or 0
        except Exception as exc:
            detail = _short_error(exc)
            self.query_log.append(QueryLogEntry(purpose, clean_sql, "error", detail))
            return (
                f"NG（構文/参照エラー）: {detail}\n"
                "describe_table でカラム名とテーブル名を確認してから修正してください。"
            )

        if estimated > self.limits.max_bytes_billed:
            msg = (
                f"スキャン見積 {estimated / 1_000_000_000:.2f} GB が上限 "
                f"{self.limits.max_bytes_billed / 1_000_000_000:.2f} GB を超えるため実行しません。"
                "対象期間を WHERE で絞る、必要な列だけを SELECT する、"
                "パーティション列で絞る、などで軽くしてください。"
            )
            self.query_log.append(
                QueryLogEntry(purpose, clean_sql, "rejected", msg, bytes_processed=estimated)
            )
            return f"NG（コスト上限）: {msg}"

        # 2. 本実行
        started = time.monotonic()
        try:
            job = self.client.query(
                clean_sql,
                job_config=bigquery.QueryJobConfig(
                    maximum_bytes_billed=self.limits.max_bytes_billed,
                    use_query_cache=True,
                ),
                location=self.location,
            )
            iterator = job.result(timeout=self.limits.query_timeout_seconds)
        except Exception as exc:
            detail = _short_error(exc)
            self.query_log.append(
                QueryLogEntry(
                    purpose,
                    clean_sql,
                    "error",
                    detail,
                    elapsed_seconds=time.monotonic() - started,
                )
            )
            return f"NG（実行エラー）: {detail}"

        elapsed = time.monotonic() - started
        columns = [f.name for f in iterator.schema]
        rows: list[list[Any]] = []
        for i, row in enumerate(iterator):
            if i >= self.limits.max_rows:
                break
            rows.append([_to_jsonable(v, self.limits.max_cell_chars) for v in row.values()])

        total = iterator.total_rows if iterator.total_rows is not None else len(rows)
        result = QueryResult(
            sql=clean_sql,
            purpose=purpose,
            columns=columns,
            rows=rows,
            total_rows=total,
            truncated=total > len(rows),
            bytes_processed=job.total_bytes_processed or 0,
            elapsed_seconds=elapsed,
            cache_hit=bool(job.cache_hit),
        )
        self.query_log.append(
            QueryLogEntry(
                purpose,
                clean_sql,
                "ok",
                rows=total,
                bytes_processed=result.bytes_processed,
                elapsed_seconds=elapsed,
            )
        )
        return result.to_text()

    # -- 集計 --

    @property
    def total_bytes_processed(self) -> int:
        return sum(e.bytes_processed for e in self.query_log)


def _short_error(exc: Exception) -> str:
    """BigQuery の例外を1〜数行に圧縮する（スタック全体はコンテキストの無駄）。"""
    text = str(exc)
    first = text.split("\n\n")[0].strip()
    return first[:600] + ("…" if len(first) > 600 else "")
