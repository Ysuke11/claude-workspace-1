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

---

# 初診アップ分析エージェント（動物病院向け）

BigQuery 上のデータを AI エージェントが自律的に掘り下げ、**初診（新規来院）を増やす施策**を根拠つきで出力します。

```bash
pip install -r scripts/shoshin_agent/requirements.txt
gcloud auth application-default login          # BigQuery の認証
export ANTHROPIC_API_KEY=sk-ant-...            # Claude API の認証
export GOOGLE_CLOUD_PROJECT=your-project-id

python scripts/run_shoshin_agent.py --check    # まず接続確認とスキーマ一覧
python scripts/run_shoshin_agent.py            # 本実行
```

## 何をするか

「1回のクエリで終わり」ではなく、結果を見て次のクエリを決める**自律ループ**です。

```
① 棚卸し   list_tables / describe_table でスキーマを自動探索
           → カルテ・予約系 / GA4 / 広告 のどれがあるか判定
② 定義確定 「初診」を判定できる列を探す（無ければ 患者IDごとの最小来院日 で定義）
③ 分解     時系列・曜日・時間帯・流入経路・動物種・年齢・地域・担当医・予約枠稼働率・
           初診→再診転換率・キャンセル … 使える軸から順に掘る
④ ファネル 広告費 → クリック → セッション → 予約 → 初診 → 再診 の歩留まりを算出
           （GA4・広告データがある場合。経路別の初診CPA まで出す）
⑤ 特定     初診が伸びない原因を最大3つに絞る
⑥ 立案     施策を最低5件、想定初診増の計算根拠つきで提案
⑦ 出力     日本語レポートを生成
```

`②` を先にやるのが要点です。スキーマを推測せず実データで初診の定義を確定してから分析に入るため、
テーブル構成が分からない状態でも動きます。

## 出力

| ファイル | 内容 |
| --- | --- |
| `reports/shoshin/YYYY-MM-DD-HHMM.md` | レポート本体（結論・現状の数字・ボトルネック・施策・クエリログ） |
| `reports/shoshin/latest.md` | 最新レポートのコピー |
| `data/shoshin/YYYY-MM-DD-HHMM.json` | 機械可読アーカイブ（findings / actions / クエリログ） |

施策は「Web集客を強化する」のような抽象論を禁止し、**誰に・何を・いつ・いくらで・想定初診増◯件/月（計算根拠つき）**
まで書かせています。

## 安全設計

エージェントが生成した SQL をそのまま実行するため、多層で防御しています。

1. **構文ガード** — コメントと文字列リテラルを除去した骨格を検査し、`SELECT` / `WITH` の単一文以外を拒否
   （`INSERT` / `UPDATE` / `DELETE` / `CREATE` などは BigQuery に送る前に弾く）
2. **コストガード** — 本実行の前に必ず dry run し、スキャン見積が上限超過なら実行しない
3. **実行ガード** — `maximum_bytes_billed` とタイムアウトを常に設定
4. **出力ガード** — 返す行数・セル文字数を打ち切り、コンテキストの肥大を防ぐ

スキーマ探索は SQL ではなくメタデータ API を使うため課金されません。上限は
[`config/shoshin.yml`](config/shoshin.yml) の `bigquery.limits` で調整できます。

## 主なオプション

```bash
# 着眼点を指定して分析させる
python scripts/run_shoshin_agent.py --focus "土日の予約枠の取りこぼしを見たい"

# 短く回して動作を試す
python scripts/run_shoshin_agent.py --max-turns 15 --effort medium
```

病院名・商圏・前提条件は [`config/shoshin.yml`](config/shoshin.yml) の `business` に書くと
プロンプトへ渡ります。分析対象のデータセットを絞りたい場合は `bigquery.allowed_datasets` に列挙してください。

## テスト

BigQuery と Claude API の両方をモックしているため、認証なしで実行できます。

```bash
python -m unittest discover -s tests -v
```

SQL 安全ガード（通すべきものを通し、弾くべきものを弾く）、自律ループの制御フロー、
レポート生成をカバーしています。Push と Pull Request では
[`.github/workflows/test.yml`](.github/workflows/test.yml) が自動で実行します。
