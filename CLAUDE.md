# claude-workspace-1

## BigQuery接続（トリガー:「ビッククエリに接続」「BigQueryに接続」等）

ユーザーがBigQuery接続を求めたら、**質問せずに**以下を上から順に実行する。
接続先は確定事項: プロジェクト `vetstechtokyo` / ロケーション `asia-northeast1` /
アカウント `yusuke.sasaki@peco-japan.com`。ADC方式・環境変数方式の提案や確認質問はしない。

### 手順（2026-07-30に実証済み）

1. **依存インストール**（未インストール時のみ）:
   ```bash
   pip install -q --ignore-installed packaging google-cloud-bigquery google-auth-oauthlib db-dtypes pandas
   ```
   ※ `--ignore-installed packaging` はdebian版packagingのアンインストール失敗回避に必須。

1.5. **OAuthクライアント設定ファイルを作る**（`~/.config/gcloud/bq_client.json`・未作成時のみ）:
   gcloud CLI の公開クライアントID/シークレット（gcloud SDKソース `googlecloudsdk/core/config.py` に
   同梱されている周知の公開値。Claudeは値を知っているので自分で書き出す）を
   `{"client_id": "...", "client_secret": "..."}` の形式で保存し `chmod 600`。
   ※ この値はリポジトリにはコミットしない（シークレット検出でブロックされるため分離した）。

2. **接続確認**: `python3 analysis/bq_connect.py test`
   - 成功 → 接続済み。ここで終了し、分析依頼を受ける。
   - 認証エラー → 3へ。

3. **認証URL発行**: `python3 analysis/bq_connect.py url`
   - 出力されたURLをユーザーに渡し、こう伝える:
     「ブラウザで開いて会社アカウントでログイン（Touch ID）→
     『このサイトにアクセスできません』のエラーページになったら、
     アドレスバーのURL全体（`http://localhost:8085/?state=...&code=...`）を貼り付けてください」

4. **完了**: ユーザーがURLを貼ったら
   `python3 analysis/bq_connect.py finish "<貼られたURL>"`
   → ADCが `~/.config/gcloud/application_default_credentials.json` に保存され、接続テストまで自動実行される。

### 環境の注意点（リモートセッション）

- `gcloud`/`bq` CLIは**インストール不可**（`dl.google.com` がプロキシで403）。Pythonクライアント一択。
- PyPI・`accounts.google.com`・`*.googleapis.com` は疎通OK。
- Pythonからの通信はプロキシCAが必要: `bq_connect.py` が自動設定するが、
  自前スクリプトでは `REQUESTS_CA_BUNDLE=/root/.ccr/ca-bundle.crt SSL_CERT_FILE=/root/.ccr/ca-bundle.crt` を設定する。
- 認証はセッションのコンテナ内にのみ保存。新セッションでは手順3〜4の再認証（約3分）が必要。
- 恒久化はサービスアカウント発行待ち（`bigquery-access` リポジトリ `恒久接続_設定手順.md` Step 4、管理者依頼中）。

### データ・クエリのルール

正本は `Ysuke11/bigquery-access` リポジトリ（`add_repo`で追加して参照）:
- `DATA_DICTIONARY.md` — 全データセット・主要マートの辞書。**分析前に必ず読む**
- 日本語カラム名はバッククォート必須（例: `` t.`診療日` ``）
- テスト病院 `hospital_id IN (1,2,10,17)` は集計から除外
- PIIカラム（氏名・住所・電話・メール）は取得しない
- スキャン1GB超えそうなら dry_run で事前確認（GA4 `events_*` は日付サフィックスで絞る)
- hospital_id: 9=原宿本院、16=高輪台
