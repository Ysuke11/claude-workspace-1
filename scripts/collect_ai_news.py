#!/usr/bin/env python3
"""世界最先端のAI情報を自動収集するスクリプト。

config/feeds.yml に定義されたフィード（公式ブログRSS / Google News検索）から
記事を収集し、以下を生成する:

  - data/news/YYYY-MM-DD.json : その日に収集した記事のJSONアーカイブ
  - news/YYYY-MM-DD.md        : 人間が読む日次Markdownダイジェスト
  - news/latest.md            : 最新ダイジェストへのコピー
  - data/seen.json            : 重複排除用の既読URLリスト

GitHub Actions (.github/workflows/collect-ai-news.yml) から毎日実行される。
個々のフィードの取得失敗は警告に留め、収集全体は継続する。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "feeds.yml"
DATA_DIR = REPO_ROOT / "data"
NEWS_JSON_DIR = DATA_DIR / "news"
NEWS_MD_DIR = REPO_ROOT / "news"
SEEN_PATH = DATA_DIR / "seen.json"

JST = timezone(timedelta(hours=9))
# Google NewsはボットらしいUAに503を返すため、ブラウザ相当のUAで要求する
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}
# 一時的な失敗（レート制限・スロットリング）で再試行するステータス
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_seen() -> list[str]:
    if SEEN_PATH.exists():
        with open(SEEN_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_seen(seen: list[str], limit: int) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(seen[-limit:], f, ensure_ascii=False, indent=1)


def feed_url(feed: dict) -> str:
    if feed["type"] == "google_news":
        lang = feed.get("lang", "en")
        if lang == "ja":
            params = {"q": feed["query"], "hl": "ja", "gl": "JP", "ceid": "JP:ja"}
        else:
            params = {"q": feed["query"], "hl": "en-US", "gl": "US", "ceid": "US:en"}
        return f"{GOOGLE_NEWS_RSS}?{urllib.parse.urlencode(params)}"
    return feed["url"]


def entry_datetime(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


def clean_summary(entry) -> str:
    summary = getattr(entry, "summary", "") or ""
    # HTMLタグを雑に除去（表示用の短い抜粋なので厳密でなくてよい）
    import re

    text = re.sub(r"<[^>]+>", " ", summary)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:300]


def fetch_with_retry(url: str) -> requests.Response:
    """一時的な失敗を指数バックオフで再試行しつつフィードを取得する。"""
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            time.sleep(2**attempt)  # 2s, 4s, 8s
        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
        except requests.RequestException as exc:
            last_error = exc
            continue
        if resp.status_code in RETRY_STATUSES:
            last_error = requests.HTTPError(
                f"{resp.status_code} {resp.reason} (retried {attempt + 1}回)", response=resp
            )
            continue
        resp.raise_for_status()
        return resp
    raise last_error  # type: ignore[misc]


def fetch_feed(feed: dict, settings: dict, seen: set[str], now: datetime) -> list[dict]:
    url = feed_url(feed)
    max_items = feed.get("max_items", settings.get("default_max_items", 10))
    max_age = timedelta(hours=settings.get("max_age_hours", 48))

    resp = fetch_with_retry(url)
    parsed = feedparser.parse(resp.content)

    articles = []
    for entry in parsed.entries:
        link = getattr(entry, "link", None)
        title = getattr(entry, "title", "").strip()
        if not link or not title or link in seen:
            continue
        published = entry_datetime(entry)
        if published and now - published > max_age:
            continue
        articles.append(
            {
                "title": title,
                "url": link,
                "source": feed["source"],
                "feed": feed["name"],
                "category": feed.get("category", "news"),
                "published": published.isoformat() if published else None,
                "summary": clean_summary(entry),
            }
        )
        if len(articles) >= max_items:
            break
    return articles


def collect() -> tuple[list[dict], list[str]]:
    config = load_config()
    settings = config.get("settings", {})
    seen_list = load_seen()
    seen = set(seen_list)
    now = datetime.now(timezone.utc)

    all_articles: list[dict] = []
    errors: list[str] = []
    for feed in config["feeds"]:
        try:
            articles = fetch_feed(feed, settings, seen, now)
            all_articles.extend(articles)
            print(f"[ok] {feed['name']}: {len(articles)}件")
        except Exception as exc:  # フィード単位の失敗は全体を止めない
            errors.append(f"{feed['name']}: {exc}")
            print(f"[warn] {feed['name']}: {exc}", file=sys.stderr)

    # タイトル重複（同じ話題を複数媒体が報じたGoogle News記事等）を除去
    deduped: list[dict] = []
    seen_titles: set[str] = set()
    for article in all_articles:
        key = article["title"].lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        deduped.append(article)

    seen_list.extend(a["url"] for a in deduped)
    save_seen(seen_list, settings.get("seen_urls_limit", 5000))
    return deduped, errors


def write_json(articles: list[dict], today: str) -> None:
    NEWS_JSON_DIR.mkdir(parents=True, exist_ok=True)
    path = NEWS_JSON_DIR / f"{today}.json"
    existing = []
    if path.exists():  # 同日に複数回実行された場合は追記
        with open(path, encoding="utf-8") as f:
            existing = json.load(f)
    urls = {a["url"] for a in existing}
    existing.extend(a for a in articles if a["url"] not in urls)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=1)


def format_article(article: dict) -> str:
    line = f"- [{article['title']}]({article['url']})"
    if article["published"]:
        published = datetime.fromisoformat(article["published"]).astimezone(JST)
        line += f" `{published.strftime('%m/%d %H:%M')}`"
    if article["summary"] and article["category"] != "news":
        line += f"\n  - {article['summary'][:200]}"
    return line


def write_markdown(articles: list[dict], errors: list[str], today: str, now_jst: datetime) -> None:
    NEWS_MD_DIR.mkdir(parents=True, exist_ok=True)

    by_source: dict[str, list[dict]] = {}
    for article in articles:
        by_source.setdefault(article["source"], []).append(article)

    lines = [
        f"# AIニュースダイジェスト {today}",
        "",
        f"> 収集日時: {now_jst.strftime('%Y-%m-%d %H:%M')} JST / 新着 {len(articles)} 件",
        "",
    ]
    # 主要企業を先頭に、それ以外は名前順
    priority = ["OpenAI", "Anthropic", "Google DeepMind", "Google", "Meta", "Microsoft", "xAI"]
    ordered = sorted(by_source, key=lambda s: (priority.index(s) if s in priority else 99, s))
    for source in ordered:
        lines.append(f"## {source}")
        lines.append("")
        for article in by_source[source]:
            lines.append(format_article(article))
        lines.append("")

    if not articles:
        lines.append("本日の新着記事はありませんでした。")
        lines.append("")

    if errors:
        lines.append("## 取得エラー")
        lines.append("")
        lines.extend(f"- {e}" for e in errors)
        lines.append("")

    content = "\n".join(lines)
    (NEWS_MD_DIR / f"{today}.md").write_text(content, encoding="utf-8")
    (NEWS_MD_DIR / "latest.md").write_text(content, encoding="utf-8")


def main() -> int:
    now_jst = datetime.now(JST)
    today = now_jst.strftime("%Y-%m-%d")
    articles, errors = collect()
    write_json(articles, today)
    write_markdown(articles, errors, today, now_jst)
    print(f"収集完了: 新着{len(articles)}件, エラー{len(errors)}件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
