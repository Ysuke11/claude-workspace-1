"""BigQuery 分析用スクリプト

BigQuery に接続してクエリを実行し、pandas DataFrame で分析するための雛形。

事前準備:
    1. 依存ライブラリのインストール
        pip install -r requirements.txt

    2. 認証 (ADC: Application Default Credentials)
        gcloud auth application-default login
        # またはサービスアカウントを使う場合:
        # export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json

    3. 接続先の設定 (環境変数で上書き可能)
        export BQ_PROJECT_ID=your-project-id
        export BQ_DATASET=your_dataset

実行:
    python analysis/bigquery_analysis.py
"""

import os

import pandas as pd
from google.cloud import bigquery

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
PROJECT_ID = os.environ.get("BQ_PROJECT_ID", "your-project-id")
DATASET = os.environ.get("BQ_DATASET", "your_dataset")


def get_client() -> bigquery.Client:
    """BigQuery クライアントを生成する (認証は ADC を利用)。"""
    return bigquery.Client(project=PROJECT_ID)


def run_query(client: bigquery.Client, sql: str) -> pd.DataFrame:
    """SQL を実行して DataFrame で返す。"""
    return client.query(sql).to_dataframe()


def list_tables(client: bigquery.Client, dataset: str = DATASET) -> list[str]:
    """データセット内のテーブル一覧を返す。"""
    return [t.table_id for t in client.list_tables(dataset)]


def preview_table(
    client: bigquery.Client, table: str, limit: int = 10, dataset: str = DATASET
) -> pd.DataFrame:
    """テーブルの先頭 N 行をプレビューする。"""
    sql = f"SELECT * FROM `{PROJECT_ID}.{dataset}.{table}` LIMIT {limit}"
    return run_query(client, sql)


def summarize_table(
    client: bigquery.Client, table: str, dataset: str = DATASET
) -> pd.DataFrame:
    """行数などの基本サマリーを取得する。"""
    sql = f"SELECT COUNT(*) AS row_count FROM `{PROJECT_ID}.{dataset}.{table}`"
    return run_query(client, sql)


def main() -> None:
    client = get_client()
    print(f"接続先プロジェクト: {client.project}")

    print(f"\nデータセット `{DATASET}` のテーブル一覧:")
    tables = list_tables(client)
    for name in tables:
        print(f"  - {name}")

    if not tables:
        print("  (テーブルがありません)")
        return

    # 最初のテーブルをプレビュー
    table = tables[0]
    print(f"\nテーブル `{table}` のプレビュー:")
    print(preview_table(client, table))

    print(f"\nテーブル `{table}` のサマリー:")
    print(summarize_table(client, table))


if __name__ == "__main__":
    main()
