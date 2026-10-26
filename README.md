# AIニュース自動収集システム

世界最先端のAI企業（OpenAI、Anthropic、Google DeepMind など）の情報を毎日自動で収集する仕組みです。

## 最新のダイジェスト

👉 [news/latest.md](news/latest.md)（毎朝7時 JST に自動更新）

## 共通プロジェクト「Free」

全端末から使う自由な作業スペースは [`free/`](free/) にあります。

## 仕組み

```
GitHub Actions (毎日 7:00 JST)
  └─ scripts/collect_ai_news.py
       ├─ config/feeds.yml のフィードから記事を収集
       │    ├─ 公式ブログRSS (OpenAI / DeepMind / Google / Meta / Microsoft / Hugging Face)
       │    ├─ arXiv cs.AI (最新論文)
       │    └─ Google News RSS検索 (Anthropic / OpenAI / xAI / 日本語の生成AIニュース)
       ├─ 重複排除 (data/seen.json に既読URLを記録)
       ├─ news/YYYY-MM-DD.md   … 日次Markdownダイジェスト
       ├─ news/latest.md       … 最新ダイジェストのコピー
       └─ data/news/YYYY-MM-DD.json … 機械可読なJSONアーカイブ
  └─ 変更を自動コミット & プッシュ
```

- Anthropic は公式RSSを提供していないため、Google News の RSS 検索経由で収集しています。
- 個別フィードの取得に失敗しても収集全体は継続し、ダイジェスト末尾にエラーとして記録されます。
- 48時間より古い記事は新着として扱いません（`config/feeds.yml` の `max_age_hours` で変更可能）。

## 手動実行

GitHub の **Actions → Collect AI News → Run workflow** からいつでも手動実行できます。

ローカルで実行する場合:

```bash
pip install -r scripts/requirements.txt
python scripts/collect_ai_news.py
```

## 収集先の追加・変更

[`config/feeds.yml`](config/feeds.yml) にフィードを追記するだけで収集対象を増やせます。

```yaml
# RSSフィードの例
- name: 新しいブログ
  source: 企業名
  type: rss
  url: https://example.com/feed.xml
  category: official

# Google News検索の例（公式RSSがない場合）
- name: 〇〇社 (Google News)
  source: 〇〇社
  type: google_news
  query: 検索キーワード
  category: news
```

## Codex 連携（Claude Code × OpenAI Codex）

Claude Code で作った成果物を OpenAI Codex にレビューさせる仕組みです。

| コマンド（Claude Code 内） | 内容 |
|---|---|
| `/codex review` | コード差分を Codex がレビュー |
| `/codex check path/to/file.md` | 資料のファクトチェック |
| `/codex ask "質問"` | 設計などの相談 |
| `/codex rescue "指示"` | 修正を Codex に任せる |

初回のみ、クラウド環境の設定（セッションのタイトルバーの環境メニュー → Edit）で次を行います。

1. 環境変数 `OPENAI_API_KEY` に OpenAI の API キーを追加
2. Network access を Custom にし、Allowed domains に `api.openai.com` を追加（パッケージマネージャーの既定リストは残す）

OpenAI 公式の Codex プラグイン（`codex@openai-codex`）も `.claude/settings.json` で有効化済みです。
`/codex:setup`・`/codex:review`・`/codex:adversarial-review`・`/codex:rescue` が使えます。

Codex CLI はセッション開始時に `.claude/hooks/install-codex.sh` が自動でインストールします。
レビュー結果は `.codex-reports/`（Git 管理外）にも保存されます。
