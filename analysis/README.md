# BigQuery 分析

BigQuery に接続してデータ分析を行うためのスクリプト置き場。

## セットアップ

```bash
pip install -r ../requirements.txt

# 認証 (ADC)
gcloud auth application-default login
# サービスアカウントを使う場合:
# export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json

# 接続先の設定
export BQ_PROJECT_ID=your-project-id
export BQ_DATASET=your_dataset
```

## 実行

```bash
python bigquery_analysis.py
```

テーブル一覧の表示、先頭行のプレビュー、行数サマリーの取得を行います。
分析を追加する場合は `run_query()` に SQL を渡して DataFrame として受け取ってください。
