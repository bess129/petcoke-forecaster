"""
price_watch.py - Weekly automatic search for newly PUBLISHED green petroleum
coke prices (Argus free news, SMM, BigMint, ETI, S&P Global, etc.).

Claude searches the web, then records only prices that are written explicitly
in a source, each with its URL and the exact sentence containing the number.
Rows are checked before saving:
  * the price number must appear in the quoted sentence (no invented numbers)
  * the price date must be within the last 120 days and not in the future
  * the price must be in a plausible range (USD 20-1500 per tonne)
  * duplicates (same source, market, grade, date) are skipped
Saved rows are marked method = "auto-extracted" so the dashboard can label them.

Run (needs ANTHROPIC_API_KEY):  py price_watch.py
The daily GitHub Action runs it; it only searches if 3+ days since the last search.
Cost: roughly US$0.10-0.30 per search run (web searches + tokens).
"""

from datetime import datetime, timezone
import json
import os
import re
import sys

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gpc_prices import PRICES_CSV, COLUMNS, DATA_DIR
from signal_detector import MODEL, as_list

STATE = DATA_DIR / "price_watch_state.json"
MIN_DAYS_BETWEEN = 3
MAX_SEARCHES = 8

SYSTEM = """You are a commodities data researcher. Find recently PUBLISHED price figures for
green (un-calcined) petroleum coke (GPC, petcoke): fuel-grade and anode/calcining-grade.

Look for: Argus free market news (argusmedia.com), SMM (news.metal.com), BigMint (bigmint.co),
S&P Global Commodity Insights news, Energy Transit Intelligence, Mysteel, SunSirs, Reuters,
and similar reliable publishers. Priority markets: US Gulf coast and US west coast FOB,
CFR China, CFR India, CFR Turkey, Saudi Arabia, Russia, Canada, Venezuela, Colombia,
India domestic, China import/ex-warehouse prices, plus any sulphur (S%) and vanadium (ppm)
specifications.

STRICT RULES:
- Record ONLY numbers written explicitly in the source. Never estimate or derive.
- For each price give the exact sentence from the source that contains the number (quote).
- Give the date the PRICE refers to (assessment date / week), not today's date.
- Skip prices older than 120 days, ranges you cannot attribute, and freight rates.
- Price in USD per tonne. If the source gives another currency, skip it.
- If you find nothing new and reliable, record an empty list. Do not guess."""

TOOL = {
    "name": "record_prices",
    "description": "Record petroleum coke prices found in published sources.",
    "input_schema": {"type": "object", "properties": {"prices": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD the price refers to"},
            "origin": {"type": "string", "description": "Producing country, e.g. USA, Saudi Arabia"},
            "market": {"type": "string", "description": "Location, e.g. US Gulf coast, China, India"},
            "basis": {"type": "string", "enum": ["FOB", "CFR", "CIF", "Ex-warehouse", "Domestic"]},
            "sulfur_pct": {"type": ["number", "null"]},
            "vanadium_ppm": {"type": ["number", "null"]},
            "hgi": {"type": ["string", "null"]},
            "price": {"type": "number", "description": "USD per tonne"},
            "source": {"type": "string", "description": "Publisher name"},
            "source_url": {"type": "string"},
            "published": {"type": "string", "description": "YYYY-MM-DD article date"},
            "quote": {"type": "string", "description": "Exact sentence containing the price"},
        },
        "required": ["date", "market", "basis", "price", "source", "source_url", "quote"]}}},
        "required": ["prices"]},
}


def _due():
    try:
        last = json.loads(STATE.read_text())["last_run"]
        return (datetime.now(timezone.utc) - datetime.fromisoformat(last)).days >= MIN_DAYS_BETWEEN
    except Exception:
        return True


def _number_in_quote(price, quote):
    nums = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", quote or "")]
    return any(abs(n - price) < 0.51 for n in nums)


def _series_id(r):
    s = r.get("sulfur_pct")
    grade = f"{float(s):.1f}S" if isinstance(s, (int, float)) else "mixed"
    key = f"PUB_{r['basis']}_{r.get('market', '')}_{grade}"
    return re.sub(r"[^A-Za-z0-9_.]+", "_", key).upper()


def validate(rows, existing, today=None):
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    keep, rejected = [], []
    seen = set()
    if len(existing):
        dates = pd.to_datetime(existing["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        seen = set(zip(existing["source_url"], existing["series_id"], dates))
        seen |= set(zip(existing["source_url"], dates, pd.to_numeric(existing["price"], errors="coerce")))
    for r in rows:
        try:
            price = float(r["price"])
            d = pd.Timestamp(r["date"]).normalize()
        except Exception:
            rejected.append((r, "bad price/date")); continue
        if not str(r.get("source_url", "")).startswith("http"):
            rejected.append((r, "no source link")); continue
        if not (20 <= price <= 1500):
            rejected.append((r, "implausible price")); continue
        if d > today or (today - d).days > 120:
            rejected.append((r, "date out of range")); continue
        if not _number_in_quote(price, r.get("quote", "")):
            rejected.append((r, "number not in quote")); continue
        sid = _series_id(r)
        key = (r["source_url"], sid, d.strftime("%Y-%m-%d"))
        key2 = (r["source_url"], d.strftime("%Y-%m-%d"), price)
        if key in seen or key2 in seen:
            rejected.append((r, "duplicate")); continue
        seen.update([key, key2])
        keep.append({"date": d.strftime("%Y-%m-%d"), "series_id": sid,
                     "origin": r.get("origin") or "Various", "market": r.get("market", ""),
                     "basis": r["basis"], "sulfur_pct": r.get("sulfur_pct"),
                     "vanadium_ppm": r.get("vanadium_ppm"), "hgi": r.get("hgi"),
                     "price": price, "currency": "USD", "unit": "t",
                     "price_type": "assessment" if "argus" in r["source"].lower() else "market report",
                     "source": r["source"], "source_url": r["source_url"],
                     "published": r.get("published", ""), "retrieved": today.strftime("%Y-%m-%d"),
                     "method": "auto-extracted", "note": "Quote: " + r.get("quote", "")[:300]})
    return keep, rejected


def search_prices():
    import anthropic
    client = anthropic.Anthropic()
    since = (pd.Timestamp.today() - pd.Timedelta(days=45)).strftime("%Y-%m-%d")
    messages = [{"role": "user", "content":
                 f"Search for green petroleum coke prices published since {since}. "
                 "Run several searches (e.g. 'Argus petroleum coke price', 'petcoke CFR India price', "
                 "'US Gulf coke 6.5pc sulphur fob', 'SMM petroleum coke import price', "
                 "'Saudi petcoke price', 'BigMint petcoke'), then call record_prices once with "
                 "every reliable explicit price you found."}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}, TOOL]
    for _ in range(4):                      # allow the search to continue if it pauses
        msg = client.messages.create(model=MODEL, max_tokens=8000, system=SYSTEM,
                                     tools=tools, messages=messages)
        for block in msg.content:
            if block.type == "tool_use" and block.name == "record_prices":
                return as_list(block.input.get("prices", []))
        if msg.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": msg.content})
            continue
        text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
        return as_list(text)
    return []


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set - skipping price search."); return
    if not _due():
        print(f"Price search ran less than {MIN_DAYS_BETWEEN} days ago - skipping."); return
    print("Searching the web for newly published petroleum coke prices...")
    try:
        found = search_prices()
    except Exception as e:
        print(f"Price search failed ({type(e).__name__}: {e}) - keeping existing data."); return
    existing = pd.read_csv(PRICES_CSV) if PRICES_CSV.exists() else pd.DataFrame(columns=COLUMNS)
    keep, rejected = validate(found, existing)
    print(f"  Found {len(found)} candidate prices: {len(keep)} accepted, {len(rejected)} rejected")
    for r, why in rejected[:10]:
        print(f"    rejected ({why}): {r.get('market')} {r.get('price')} {r.get('source_url', '')[:60]}")
    if keep:
        out = pd.concat([existing, pd.DataFrame(keep)], ignore_index=True)[COLUMNS]
        out.to_csv(PRICES_CSV, index=False)
        for k in keep:
            print(f"    + {k['date']} {k['market']} {k['basis']} S={k['sulfur_pct']} ${k['price']} ({k['source']})")
    STATE.write_text(json.dumps({"last_run": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                                 "accepted": len(keep), "rejected": len(rejected)}))


if __name__ == "__main__":
    main()
