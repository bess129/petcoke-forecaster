"""
scraper.py - Weak-signal news scraper (Step 4 of the sprint brief).

Pulls Google News RSS headlines (no API key needed) for early supply-chain
triggers in two categories: Minor Metals and Pet Coke.

Run it on its own to test:
    py scraper.py
"""

import sys
# Windows logs/terminals default to cp1252, which can't print some characters
# Claude or news titles use (e.g. non-breaking hyphens). Use UTF-8 instead.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


from pathlib import Path
from urllib.parse import quote_plus
import re
import socket

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
socket.setdefaulttimeout(15)   # don't hang forever if Google is slow

# Subjects x weak-signal keywords, from the brief's weak-signal taxonomy.
SEARCHES = {
    "Minor Metals": {
        "subjects": ["(gallium OR germanium OR antimony OR cobalt OR indium)",
                     '(molybdenum OR lithium OR "rare earth" OR tin)'],
        "keywords": [
            "smelter outage",
            "export quota",
            "export control",
            "MOFCOM",
            "mining ban",
            "byproduct",
            "zinc smelter cut",
            "semiconductor subsidy",
            "supply shortage",
        ],
    },
    "Pet Coke": {
        "subjects": '("petroleum coke" OR petcoke OR coker)',
        "keywords": [
            "refinery turnaround",
            "calciner outage",
            "heavy crude",
            "sour crude",
            "cement kiln",
            "freight rates",
            "refinery outage",
            "anode",
            "supply shortage",
        ],
    },
}

DAYS_BACK = 30


def _rss_url(query):
    q = f"{query} when:{DAYS_BACK}d"
    return ("https://news.google.com/rss/search?q=" + quote_plus(q)
            + "&hl=en-US&gl=US&ceid=US:en")


def _clean_title(title, source):
    """Google News titles end with ' - Source Name'; strip it."""
    if source and title.endswith(" - " + source):
        return title[: -len(" - " + source)].strip()
    return re.sub(r"\s+-\s+[^-]+$", "", title).strip()


def fetch_news(categories=None, max_per_query=20) -> pd.DataFrame:
    """Return DataFrame: title, published_date, source, link, commodity, query.
    Deduplicated, newest first."""
    import feedparser

    categories = categories or list(SEARCHES)
    rows = []
    for commodity in categories:
        cfg = SEARCHES[commodity]
        subjects = cfg["subjects"] if isinstance(cfg["subjects"], list) else [cfg["subjects"]]
        for subj, kw in [(s, k) for s in subjects for k in cfg["keywords"]]:
            query = f"{subj} {kw}"
            try:
                feed = feedparser.parse(_rss_url(query),
                                        agent="Mozilla/5.0 (commodity-forecaster)")
            except Exception as e:
                print(f"  [{commodity}] '{kw}': failed ({e})")
                continue
            if feed.get("bozo") and not feed.entries:
                print(f"  [{commodity}] '{kw}': no response ({feed.get('bozo_exception')})")
                continue

            for e in feed.entries[:max_per_query]:
                source = e.get("source", {}).get("title", "") if e.get("source") else ""
                rows.append({
                    "title": _clean_title(e.get("title", ""), source),
                    "published_date": e.get("published", ""),
                    "source": source,
                    "link": e.get("link", ""),
                    "commodity": commodity,
                    "query": kw,
                })
            print(f"  [{commodity}] '{kw}': {len(feed.entries)} articles")

    df = pd.DataFrame(rows, columns=["title", "published_date", "source",
                                     "link", "commodity", "query"])
    if df.empty:
        return df

    df["published_date"] = pd.to_datetime(df["published_date"], errors="coerce",
                                          utc=True).dt.tz_localize(None)
    # Deduplicate: same headline often appears under several keywords/sources
    df["_key"] = df["title"].str.lower().str.replace(r"[^a-z0-9 ]", "", regex=True).str.strip()
    df = (df.sort_values("published_date", ascending=False)
            .drop_duplicates(subset=["_key", "commodity"])
            .drop(columns="_key")
            .reset_index(drop=True))
    return df


if __name__ == "__main__":
    print("Fetching Google News headlines...")
    news = fetch_news()
    if news.empty:
        print("\nNo articles found. Check your internet connection (can you open "
              "https://news.google.com in your browser?).")
    else:
        print(f"\nTotal unique articles: {len(news)}")
        print(news.groupby("commodity").size().to_string())
        print("\nLatest 10 headlines:")
        for _, r in news.head(10).iterrows():
            d = r["published_date"].strftime("%Y-%m-%d") if pd.notna(r["published_date"]) else "?"
            print(f"  {d} [{r['commodity']}] {r['title'][:90]}  ({r['source']})")
        out = DATA_DIR / "news.csv"
        news.to_csv(out, index=False)
        print(f"\nSaved to {out}")
