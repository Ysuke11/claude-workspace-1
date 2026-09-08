# BigQuery アクセス引き継ぎ書

最終更新: 2026-08-12 / 作成者: （記入: ______） / 次担当者: （記入: ______）

この文書は **BigQuery に誰が・どうやってアクセスするか** を引き継ぐためのものです。
`（記入: ______）` の箇所は自組織の値に置き換えてください。置き換えが終わったら、この行を含む注意書きは削除して構いません。

---

## 0. 最初にこれだけやる（引き継ぎチェックリスト）

新担当者が着任日に完了させる項目です。上から順に潰せば疎通まで到達します。

- [ ] Google アカウント（会社ドメイン）が発行されている
- [ ] 対象の Google Cloud プロジェクトに **IAM ロールが付与**されている（→ 2章）
- [ ] BigQuery コンソールでテーブル一覧が見える（→ 3章 A）
- [ ] `bq query 'SELECT 1'` が手元のPCで成功する（→ 3章 B）
- [ ] 課金上限・スキャン量の運用ルールを読んだ（→ 6章）
- [ ] **前任者名義で動いているもの**の棚卸しが済んでいる（→ 8章）
  - スケジュールドクエリ / データ転送 / サービスアカウント鍵 / 外部ツールの接続設定
- [ ] 緊急時の連絡先（GCP管理者・課金管理者）を把握した（→ 1章）

---

## 1. 前提情報（環境の台帳）

| 項目 | 値 |
| --- | --- |
| Google Cloud プロジェクト名 | （記入: ______） |
| プロジェクトID | （記入: ______） |
| プロジェクト番号 | （記入: ______） |
| 主要データセット | （記入: 例 `analytics_123456789`, `mart`, `staging`） |
| データセットのロケーション | （記入: 例 `US` / `asia-northeast1`） |
| 課金アカウント | （記入: ______） |
| 課金の管理部署・承認者 | （記入: ______） |
| GCP管理者（IAM付与できる人） | （記入: 氏名 / 連絡先） |
| データオーナー（業務側の責任者） | （記入: 氏名 / 連絡先） |
| 契約形態 | （記入: オンデマンド / Editions（Standard・Enterprise 等）） |

> **ロケーションは重要です。** データセットのロケーションが違うとクエリで JOIN できず、CLI でも `--location` の指定が必要になります。必ず記入してください。

### データセット一覧（記入例）

| データセット | 中身 | 更新頻度 | 更新元 | 触ってよい範囲 |
| --- | --- | --- | --- | --- |
| `analytics_123456789` | GA4 エクスポート（生ログ） | 日次（前日分） | GA4 の BigQuery Linking | **読み取り専用**。書き換え・削除禁止 |
| `mart` | 集計済みテーブル | 日次 06:00 JST | スケジュールドクエリ | 更新可（要レビュー） |
| `staging` | 検証用 | 随時 | 手動 | 自由 |

---

## 2. アクセス権限（IAM）

### 2-1. 役割ごとの推奨ロール

BigQuery の権限は「**データを見る権限**」と「**クエリを実行する権限（＝課金が発生する）**」が分かれているのがポイントです。片方だけだと動きません。

| 想定する人 | 付与するロール | できること |
| --- | --- | --- |
| 閲覧のみ（レポートを見る人） | `roles/bigquery.dataViewer` + `roles/bigquery.jobUser` | テーブル参照とクエリ実行 |
| 分析担当（標準） | `roles/bigquery.user` + `roles/bigquery.dataViewer` | 上記 + データセット作成、ジョブ管理 |
| データ整備担当 | `roles/bigquery.dataEditor` + `roles/bigquery.user` | テーブルの作成・更新・削除 |
| 管理者 | `roles/bigquery.admin` | 全操作 + 権限管理 |

補足:

- `roles/bigquery.user` は「クエリ実行 + 自分のデータセット作成」で、**他人のデータの閲覧権限は含みません**。だから `dataViewer` と組み合わせます。
- `roles/bigquery.jobUser` は「クエリを投げる権限」だけ。閲覧権限は別途必要です。
- 閲覧範囲を絞りたい場合は、プロジェクト全体ではなく**データセット単位・テーブル単位**で `dataViewer` を付けます（最小権限の原則）。
- 個人ごとではなく **Google グループに付与**して、グループへの出し入れで管理すると引き継ぎが楽になります。運用グループ: （記入: 例 `bq-analysts@example.com`）

### 2-2. 権限付与のやり方

**コンソールから:** Google Cloud コンソール → 「IAM と管理」→ 「アクセスを許可」→ プリンシパルにメールアドレス、ロールを選択して保存。

**gcloud から（プロジェクト単位）:**

```bash
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="user:taro@example.com" \
  --role="roles/bigquery.jobUser"
```

**データセット単位で絞る場合:**

```bash
# 現在の設定を取り出して編集し、書き戻す
bq show --format=prettyjson PROJECT_ID:DATASET > /tmp/ds.json
# /tmp/ds.json の "access" 配列に以下を追記
#   {"role": "READER", "userByEmail": "taro@example.com"}
bq update --source=/tmp/ds.json PROJECT_ID:DATASET
```

### 2-3. 現在の権限保有者（棚卸し表）

| プリンシパル | 種別 | ロール | 用途 | 棚卸し日 |
| --- | --- | --- | --- | --- |
| （記入） | ユーザー / グループ / サービスアカウント | （記入） | （記入） | （記入） |

権限一覧はコマンドでも取得できます。引き継ぎ時に実行して、上表を最新化してください。

```bash
gcloud projects get-iam-policy PROJECT_ID --format=json \
  | jq '.bindings[] | select(.role | startswith("roles/bigquery"))'
```

---

## 3. アクセス方法（4通り）

### A. Web コンソール ― 最も手軽。まずはここで疎通確認

1. https://console.cloud.google.com/bigquery を開く
2. 画面上部でプロジェクト（記入: ______）を選択
3. 左側のエクスプローラでデータセット → テーブルを展開
4. 「クエリを新規作成」で SQL を実行

覚えておくと良い点:

- テーブルの **「プレビュー」タブは課金されません**。中身をちょっと見たいだけなら `SELECT *` ではなくプレビューを使う。
- クエリエディタの右上に「このクエリを実行すると **N GB** が処理されます」と出ます。**実行前に必ずこの数字を見る習慣**をつけてください。
- 保存したクエリはプロジェクト内で共有できます（個人保存にすると引き継げないので、共有保存にすること）。

### B. コマンドライン（`bq` / `gcloud`）

**インストール（Google Cloud CLI に `bq` が同梱されています）**

```bash
# macOS (Homebrew)
brew install --cask google-cloud-sdk

# その他OS・手動インストール
# https://cloud.google.com/sdk/docs/install
```

**初期設定と認証**

```bash
gcloud auth login                      # ブラウザが開くので会社アカウントでログイン
gcloud config set project PROJECT_ID   # 既定プロジェクトを設定
gcloud auth list                       # 今どのアカウントで認証されているか確認
```

**疎通確認**

```bash
bq query --use_legacy_sql=false 'SELECT 1 AS ok'
```

**よく使うコマンド**

```bash
bq ls                                     # データセット一覧
bq ls DATASET                             # テーブル一覧
bq show DATASET.TABLE                     # スキーマとサイズを確認
bq head -n 20 DATASET.TABLE               # 先頭20行（課金なし）

# スキャン量だけ見積もる（実行しない・課金されない）
bq query --use_legacy_sql=false --dry_run 'SELECT ...'

# スキャン量に上限をかけて実行（10GB超なら実行前にエラーで止まる）
bq query --use_legacy_sql=false --maximum_bytes_billed=10000000000 'SELECT ...'

# ロケーションが US 以外のデータセットを扱うとき
bq --location=asia-northeast1 ls DATASET

# 結果をCSVに落とす
bq query --use_legacy_sql=false --format=csv 'SELECT ...' > result.csv
```

### C. Python から

```bash
pip install google-cloud-bigquery
gcloud auth application-default login   # ADC（アプリケーションのデフォルト認証情報）を作成
```

```python
from google.cloud import bigquery

client = bigquery.Client(project="PROJECT_ID")

sql = """
SELECT event_date, COUNT(*) AS events
FROM `PROJECT_ID.analytics_123456789.events_*`
WHERE _TABLE_SUFFIX BETWEEN '20260801' AND '20260807'
GROUP BY event_date
ORDER BY event_date
"""

# 事故防止: スキャン量に上限を設ける（超えたら実行されずエラー）
job_config = bigquery.QueryJobConfig(maximum_bytes_billed=10 * 1024**3)  # 10GiB

for row in client.query(sql, job_config=job_config).result():
    print(row.event_date, row.events)
```

pandas に読み込む場合は `client.query(sql, job_config=job_config).to_dataframe()`（`pip install google-cloud-bigquery[pandas]` が必要）。

### D. スプレッドシート / Looker Studio から（非エンジニア向け）

- **コネクテッドシート**: Google スプレッドシート → データ → データコネクタ → BigQuery に接続。SQL を書かずにピボットで集計できます。閲覧者にも BigQuery の権限が必要な点に注意（＝シートを共有しても、権限がない人はデータを更新できません）。
- **Looker Studio**: データソース追加 → BigQuery。「オーナーの認証情報」で作ると**作成者が退職したときに壊れます**。引き継ぎ時は必ず認証情報の持ち主を確認してください（→ 8章）。

現在使っている接続: （記入: 例 「週次レポートシート」 https://... / オーナー: ______）

---

## 4. 認証方式の使い分け

| 方式 | 用途 | 設定方法 | 注意 |
| --- | --- | --- | --- |
| ユーザー認証（ADC） | 手元PCでの分析・アドホック調査 | `gcloud auth application-default login` | 個人に紐づく。**本番の自動処理には使わない**（退職で止まる） |
| サービスアカウント + Workload Identity | 本番の自動処理（推奨） | GKE/Cloud Run/GitHub Actions の OIDC 連携 | **鍵ファイルを作らずに済む＝最も安全** |
| サービスアカウント鍵（JSON） | 上記が使えない環境のみ | 鍵を発行し `GOOGLE_APPLICATION_CREDENTIALS` に設定 | 漏洩リスク大。下記ルール必須 |

**サービスアカウント鍵（JSON）を扱う場合のルール**

- **絶対に Git にコミットしない**（`.gitignore` に追加、`*.json` の誤コミットに注意）
- Slack やメールで平文共有しない。共有はパスワードマネージャ経由
- 引き継ぎのタイミングで**ローテーション（新規発行 → 差し替え → 旧鍵削除）**する
- 権限は必要最小限のデータセットのみに絞る

現在有効なサービスアカウント: （記入: メールアドレス / 用途 / 鍵の保管場所 / 最終ローテーション日）

---

## 5. GA4 エクスポートデータを読む（該当する場合）

GA4 と BigQuery を連携している場合、`analytics_<プロパティID>` データセットに日次でエクスポートされます。

- `events_YYYYMMDD` … 確定した日次テーブル（前日分が翌日に生成）
- `events_intraday_YYYYMMDD` … 当日の暫定テーブル（連携設定で「ストリーミング」を有効にした場合のみ。**確定値ではない**）
- 1行 = 1イベント。GA4 の管理画面の数値とは**必ずしも一致しません**（画面側はサンプリングやモデリング、しきい値処理が入るため）

**日付範囲を指定する基本形**（`events_*` にワイルドカードを使い、`_TABLE_SUFFIX` で絞る＝スキャン量を必要な日数分だけに抑えられます）

```sql
SELECT
  event_date,
  event_name,
  COUNT(*) AS events,
  COUNT(DISTINCT user_pseudo_id) AS users
FROM `PROJECT_ID.analytics_123456789.events_*`
WHERE _TABLE_SUFFIX BETWEEN '20260801' AND '20260807'
GROUP BY event_date, event_name
ORDER BY event_date, events DESC
```

**イベントパラメータの取り出し**（`event_params` はネストしているので `UNNEST` します）

```sql
SELECT
  (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location') AS page_location,
  COUNT(*) AS page_views
FROM `PROJECT_ID.analytics_123456789.events_*`
WHERE _TABLE_SUFFIX BETWEEN '20260801' AND '20260807'
  AND event_name = 'page_view'
GROUP BY page_location
ORDER BY page_views DESC
LIMIT 50
```

> `WHERE _TABLE_SUFFIX ...` を書き忘れると**全期間をスキャン**します。GA4 の生ログは巨大になりがちなので、ここが最大の事故ポイントです。

---

## 6. コストとクォータ（事故を防ぐ）

### 課金の仕組み（オンデマンドの場合）

- **クエリしたデータ量（スキャン量）に対して課金**されます。結果の行数ではありません。
- 月あたり一定量までは無料枠があり、超過分はスキャン量に応じた従量課金です。単価はリージョンで異なり改定もあるため、**最新の金額は必ず公式の料金ページで確認**してください → https://cloud.google.com/bigquery/pricing
- ストレージ料金は別途かかります（長期間更新のないテーブルは自動的に安くなります）。

### 事故を防ぐ実務ルール

1. `SELECT *` を書かない。**必要な列だけ**選ぶ（BigQuery は列指向なので、列を絞るとスキャン量が直接減ります）
2. パーティション列（GA4 なら `_TABLE_SUFFIX`、通常テーブルなら日付パーティション列）で**必ず期間を絞る**
3. 実行前にコンソールの見積もり表示、または `--dry_run` でスキャン量を確認する
4. 大きなクエリは `maximum_bytes_billed` で上限を設定する
5. 中身をちょっと見たいだけなら「プレビュー」タブや `bq head`（**課金なし**）を使う
6. 同じ集計を何度も回すなら、集計済みテーブル（`mart`）を作って参照する

### 組織として設定しておくもの

- **カスタムクォータ**（IAM と管理 → 割り当て）でプロジェクト単位・ユーザー単位の1日あたりクエリ量に上限を設定
- **予算アラート**（お支払い → 予算とアラート）で閾値超過時にメール通知
- 現在の設定値: （記入: 例 プロジェクト日次 1TB / ユーザー日次 200GB / 予算アラート ¥______）

---

## 7. トラブルシューティング

| 症状・エラー | 原因 | 対処 |
| --- | --- | --- |
| `Access Denied: Project ...: User does not have bigquery.jobs.create permission` | クエリ実行権限がない | `roles/bigquery.jobUser` または `roles/bigquery.user` を付与してもらう |
| `Access Denied: Table ...` | 閲覧権限がない | 該当データセットに `dataViewer` を付与してもらう |
| `Not found: Dataset ...` | プロジェクト違い、またはロケーション違い | `gcloud config get project` を確認。CLI では `--location` を指定 |
| `Dataset ... was not found in location US` | データセットが別リージョンにある | `bq --location=asia-northeast1 ...` のように指定 |
| `Billing has not been enabled for this project` | 課金アカウント未紐付け | 課金管理者に連絡（→1章） |
| `Quota exceeded` | カスタムクォータ上限に到達 | 翌日まで待つか、管理者に一時的な引き上げを依頼 |
| クエリが終わらない / 高額になりそう | 期間を絞れていない | ジョブをキャンセルし、`_TABLE_SUFFIX` やパーティション条件を追加 |
| `Reauthentication is needed` | 認証トークンの期限切れ | `gcloud auth login` および `gcloud auth application-default login` を再実行 |
| Looker Studio / スプレッドシートが突然エラー | 接続オーナーのアカウント失効 | 接続の認証情報を現担当者またはサービスアカウントに差し替え |

実行中のジョブの確認とキャンセル:

```bash
bq ls -j -a -n 20          # 直近のジョブ一覧
bq cancel JOB_ID           # ジョブをキャンセル
```

---

## 8. 引き継ぎ時の作業（前任者が去る前に必ず）

**「前任者の個人アカウントで動いているもの」が最大のリスクです。** アカウント停止と同時に止まります。

- [ ] **スケジュールドクエリ**の棚卸し（BigQuery → スケジュールされたクエリ）
      → 前任者名義のものは、新担当者またはサービスアカウント名義で作り直す
- [ ] **Data Transfer Service**（広告データ転送など）の所有者確認・付け替え
- [ ] **Looker Studio / コネクテッドシート**の接続認証情報の付け替え
- [ ] **サービスアカウント鍵のローテーション**（新規発行 → 各所差し替え → 旧鍵削除）
- [ ] **保存済みクエリ**を個人保存から共有保存へ移す
- [ ] **IAM の棚卸し**（2-3章の表を更新し、不要になった権限を削除）
- [ ] **GA4 側の BigQuery Linking 設定**の確認（GA4 管理 → BigQuery のリンク設定が生きているか）
- [ ] 前任者の GCP アカウント権限を**最後に**削除する（先に消すと調査ができなくなります）

移行対象の一覧: （記入: 対象 / 現オーナー / 新オーナー / 対応日）

---

## 9. 参考リンク

- BigQuery ドキュメント: https://cloud.google.com/bigquery/docs
- IAM ロール一覧（BigQuery）: https://cloud.google.com/bigquery/docs/access-control
- 料金: https://cloud.google.com/bigquery/pricing
- コスト最適化のベストプラクティス: https://cloud.google.com/bigquery/docs/best-practices-costs
- GA4 BigQuery Export スキーマ: https://support.google.com/analytics/answer/7029846
- Google Cloud CLI インストール: https://cloud.google.com/sdk/docs/install
