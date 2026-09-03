# BigQuery 分析（vetstechtokyo）

接続先・データ構造・クエリルールの**正本は [`bigquery-access`](https://github.com/Ysuke11/bigquery-access) リポジトリ**
（`DATA_DICTIONARY.md` / `HANDOFF.md`）。このフォルダは分析スクリプトと結果の置き場。

## 接続情報（確定事項）

| 項目 | 値 |
|------|-----|
| プロジェクト | `vetstechtokyo` |
| ロケーション | `asia-northeast1` |
| 認証 | `gcloud auth login`（認証済みのMacならそのまま動く。追加認証不要） |
| 最重要データセット | `dataform`（マート層）、`raw`（生データ） |

## 実行

```bash
# 接続テスト + データセット一覧
python analysis/bigquery_analysis.py

# SQLファイルを実行して analysis/results/ にCSV保存
python analysis/bigquery_analysis.py analysis/queries/xxx.sql
```

## 毎回守るルール（DATA_DICTIONARY.md §4）

1. `--location=asia-northeast1` 必須（スクリプトが自動付与）
2. 日本語カラム名はバッククォート必須（例: `` t.`診療日` ``）
3. テスト病院（hospital_id 1,2,10,17）は集計から除外
4. スキャン1GB超えそうなら `dry_run()` で事前確認
5. PIIカラム（氏名・住所・電話・メール）は取得しない
6. 大きい結果はCSV保存し、会話には要約だけ
