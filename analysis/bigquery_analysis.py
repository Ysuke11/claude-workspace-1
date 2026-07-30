"""BigQuery (vetstechtokyo) 分析用スクリプト

認証済みの bq コマンドをそのまま使う方式。
`gcloud auth login` が済んでいる環境なら追加の認証は一切不要。

接続先・ルールの正本: bigquery-access リポジトリの DATA_DICTIONARY.md / HANDOFF.md

実行:
    python analysis/bigquery_analysis.py            # 接続テスト + データセット一覧
    python analysis/bigquery_analysis.py query.sql  # SQLファイルを実行してCSV保存
"""

import io
import subprocess
import sys
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# 接続先（固定・正本は bigquery-access/DATA_DICTIONARY.md）
# ---------------------------------------------------------------------------
PROJECT_ID = "vetstechtokyo"
LOCATION = "asia-northeast1"

# テスト系病院（集計から必ず除外する）
TEST_HOSPITAL_IDS = (1, 2, 10, 17)

# SQL内で使う除外条件のスニペット
EXCLUDE_TEST_HOSPITALS = f"hospital_id NOT IN {TEST_HOSPITAL_IDS}"

RESULTS_DIR = Path(__file__).parent / "results"


def bq(*args: str) -> str:
    """bq コマンドを実行して標準出力を返す。"""
    cmd = ["bq", f"--location={LOCATION}", f"--project_id={PROJECT_ID}", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"bq 失敗:\n{result.stderr}")
    return result.stdout


def run_query(sql: str, max_rows: int = 1000) -> pd.DataFrame:
    """SQL を実行して DataFrame で返す。

    ルール（DATA_DICTIONARY.md §4）:
    - 日本語カラム名はバッククォート必須（例: t.`診療日`）
    - テスト病院 (1,2,10,17) は除外する
    - PIIカラム（氏名・住所・電話・メール）は取得しない
    """
    out = bq(
        "query",
        "--use_legacy_sql=false",
        "--format=csv",
        f"--max_rows={max_rows}",
        sql,
    )
    return pd.read_csv(io.StringIO(out))


def dry_run(sql: str) -> str:
    """スキャン量を事前確認する（1GB超えそうなクエリは実行前に必ず使う）。"""
    return bq("query", "--use_legacy_sql=false", "--dry_run", sql)


def run_query_file(sql_path: str, max_rows: int = 1000) -> Path:
    """SQLファイルを実行し、結果を analysis/results/ にCSV保存してパスを返す。"""
    sql = Path(sql_path).read_text()
    df = run_query(sql, max_rows=max_rows)
    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / (Path(sql_path).stem + ".csv")
    df.to_csv(out_path, index=False)
    print(f"{len(df)}行 → {out_path}")
    return out_path


def list_datasets() -> str:
    """データセット一覧。"""
    return bq("ls")


def list_tables(dataset: str) -> str:
    """データセット内のテーブル一覧（例: dataform / raw）。"""
    return bq("ls", "--max_results=1000", f"{PROJECT_ID}:{dataset}")


def show_schema(dataset: str, table: str) -> str:
    """テーブルのスキーマを表示。"""
    return bq("show", "--schema", "--format=prettyjson", f"{PROJECT_ID}:{dataset}.{table}")


def main() -> None:
    if len(sys.argv) > 1:
        run_query_file(sys.argv[1])
        return

    # 接続テスト
    print("接続テスト:")
    print(run_query("SELECT 1 AS ok"))

    print(f"\nデータセット一覧 ({PROJECT_ID}):")
    print(list_datasets())


if __name__ == "__main__":
    main()
