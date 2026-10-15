"""BigQuery (vetstechtokyo) 接続ヘルパー — リモートセッション用

2026-07-30 に成功した手順をそのまま自動化したもの。使い方は CLAUDE.md を参照。

    python3 analysis/bq_connect.py test            # 接続確認（済みならこれだけでOK）
    python3 analysis/bq_connect.py url             # 認証URLを発行 → ユーザーに渡す
    python3 analysis/bq_connect.py finish "<URL>"  # ユーザーが貼ったリダイレクトURLで完了
"""

import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# リモート環境のプロキシCA（存在する場合のみ設定）
_CA = "/root/.ccr/ca-bundle.crt"
if os.path.exists(_CA):
    os.environ.setdefault("REQUESTS_CA_BUNDLE", _CA)
    os.environ.setdefault("SSL_CERT_FILE", _CA)

PROJECT_ID = "vetstechtokyo"
LOCATION = "asia-northeast1"
ACCOUNT = "yusuke.sasaki@peco-japan.com"

SCOPES = ["https://www.googleapis.com/auth/bigquery"]
REDIRECT_URI = "http://localhost:8085/"

GCLOUD_DIR = Path.home() / ".config" / "gcloud"
ADC_PATH = GCLOUD_DIR / "application_default_credentials.json"
STATE_PATH = GCLOUD_DIR / "bq_oauth_state.json"

# OAuthクライアント設定はリポジトリに含めない（Gitにシークレット文字列を置かないため）。
# gcloud CLI の公開クライアントID/シークレットを以下のJSONに置く（CLAUDE.md参照）:
#   {"client_id": "...", "client_secret": "..."}
CLIENT_FILE = GCLOUD_DIR / "bq_client.json"


def _client_config() -> dict:
    if not CLIENT_FILE.exists():
        sys.exit(
            f"{CLIENT_FILE} がありません。gcloud CLIの公開クライアントID/シークレットを"
            '{"client_id": "...", "client_secret": "..."} の形式で保存してください（CLAUDE.md参照）。'
        )
    c = json.loads(CLIENT_FILE.read_text())
    return {
        "installed": {
            "client_id": c["client_id"],
            "client_secret": c["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def cmd_url() -> None:
    """認証URLを発行し、PKCE検証コードを保存する。"""
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = REDIRECT_URI
    auth_url, state = flow.authorization_url(
        access_type="offline", prompt="consent", login_hint=ACCOUNT
    )
    GCLOUD_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps({"state": state, "code_verifier": flow.code_verifier})
    )
    print(auth_url)


def cmd_finish(pasted: str) -> None:
    """ユーザーが貼ったリダイレクトURL（またはcode）でトークン交換し、ADCを保存する。"""
    from google_auth_oauthlib.flow import Flow

    if pasted.startswith("http"):
        code = parse_qs(urlparse(pasted).query)["code"][0]
    else:
        code = pasted

    saved = json.loads(STATE_PATH.read_text())
    config = _client_config()
    flow = Flow.from_client_config(config, scopes=SCOPES, state=saved["state"])
    flow.redirect_uri = REDIRECT_URI
    flow.code_verifier = saved["code_verifier"]
    flow.fetch_token(code=code)

    adc = {
        "type": "authorized_user",
        "client_id": config["installed"]["client_id"],
        "client_secret": config["installed"]["client_secret"],
        "refresh_token": flow.credentials.refresh_token,
    }
    GCLOUD_DIR.mkdir(parents=True, exist_ok=True)
    ADC_PATH.write_text(json.dumps(adc))
    ADC_PATH.chmod(0o600)
    STATE_PATH.unlink(missing_ok=True)
    print("認証情報を保存しました:", ADC_PATH)
    cmd_test()


def cmd_test() -> None:
    """接続テスト。成功すればデータセット一覧を表示する。"""
    from google.cloud import bigquery

    client = bigquery.Client(project=PROJECT_ID, location=LOCATION)
    ok = list(client.query("SELECT 1 AS ok").result())[0].ok
    datasets = [ds.dataset_id for ds in client.list_datasets()]
    print(f"接続OK (SELECT 1 -> {ok}) / データセット{len(datasets)}個: {', '.join(datasets)}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"
    if cmd == "url":
        cmd_url()
    elif cmd == "finish":
        cmd_finish(sys.argv[2])
    elif cmd == "test":
        cmd_test()
    else:
        sys.exit(f"不明なコマンド: {cmd} (url / finish / test)")


if __name__ == "__main__":
    main()
