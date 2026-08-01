# AIニュース自動収集システム

世界最先端のAI企業（OpenAI、Anthropic、Google DeepMind など）の情報を毎日自動で収集する仕組みです。

## 最新のダイジェスト

👉 [news/latest.md](news/latest.md)（毎朝7時 JST に自動更新）

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
