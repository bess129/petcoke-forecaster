"""
signal_detector.py - Claude weak-signal analyzer (Step 5 of the sprint brief).

Sends scraped headlines to Claude acting as a commodities supply-chain analyst.
Each headline gets: commodity, signal_category, price_direction (-1..+1),
urgency_score (1..5) and a one-sentence reasoning.

Results are cached in data/signals.csv, so headlines already scored are never
sent (or paid for) twice.

Needs the ANTHROPIC_API_KEY environment variable. Run on its own to test:
    py signal_detector.py
"""

import sys
# Windows logs/terminals default to cp1252, which can't print some characters
# Claude or news titles use (e.g. non-breaking hyphens). Use UTF-8 instead.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


from pathlib import Path
import os

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"


def as_id(value):
    """Headline id as an int, even if Claude sent it as text ("12")."""
    try:
        return int(str(value).strip().rstrip("."))
    except Exception:
        return None


def as_list(value):
    """Claude sometimes returns a tool list as a JSON *string* instead of a
    real list. Accept both, and keep only dict entries."""
    if isinstance(value, str):
        import json
        try:
            value = json.loads(value)
        except Exception:
            start, end = value.find("["), value.rfind("]")
            try:
                value = json.loads(value[start:end + 1]) if start != -1 else []
            except Exception:
                value = []
    if isinstance(value, dict):
        value = [value]
    return [v for v in (value or []) if isinstance(v, dict)]
SIGNALS_CSV = DATA_DIR / "signals.csv"

MODEL = "claude-sonnet-5-5"   # older names in the brief (claude-3-5-...) are retired
BATCH_SIZE = 25
TRANSLATE_PER_RUN = 200   # older headlines translated into Chinese per run

CATEGORIES = ["Supply Outage", "Export Policy", "Host Metal Cut",
              "Refinery Turnaround", "Demand Shift", "Logistics", "Noise"]

SYSTEM_PROMPT = """You are a senior commodities supply-chain analyst covering minor metals \
(gallium, germanium, antimony, cobalt, indium, molybdenum, lithium, rare earths, tin) \
and petroleum coke - above all GREEN (un-calcined, fuel-grade and anode-grade) petroleum coke (GPC).

Your job is to spot WEAK SIGNALS: early supply-chain, refinery, or policy triggers \
that could move spot prices BEFORE the move shows up in price data. Examples:
- Minor metals: smelter maintenance/outages, export licensing or quotas (especially \
China MOFCOM), cuts at host-metal producers (zinc, aluminum, copper) that yield \
minor metals as byproducts, new semiconductor/defense subsidies.
- Pet coke: refinery coker turnarounds, heavy/sour crude supply shifts, calciner \
outages, cement/steel kiln fuel-switching rules, freight or barge bottlenecks.

Scoring rules:
- price_direction: -1.0 (strong bearish, price drop) to +1.0 (strong bullish, price spike). \
Supply cuts and export restrictions are usually bullish; new supply or weaker demand bearish.
- urgency_score: 1 = routine noise, 2 = minor, 3 = worth watching, \
4 = likely to affect supply within weeks, 5 = imminent structural supply shock.
- Market-research ads, stock-price stories and general news unrelated to physical \
supply should be 'Noise' with urgency 1 and price_direction near 0.
- Be skeptical: most headlines are noise. Reserve 4-5 for concrete, specific events."""

TOOL = {
    "name": "record_signals",
    "description": "Record the weak-signal assessment for every headline.",
    "input_schema": {
        "type": "object",
        "properties": {
            "signals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "The headline's id number"},
                        "headline": {"type": "string"},
                        "commodity": {"type": "string", "enum": ["Minor Metals", "Pet Coke"]},
                        "signal_category": {"type": "string", "enum": CATEGORIES},
                        "price_direction": {"type": "number", "minimum": -1, "maximum": 1},
                        "urgency_score": {"type": "integer", "minimum": 1, "maximum": 5},
                        "reasoning": {"type": "string", "description": "One short sentence"},
                        "headline_zh": {"type": "string",
                                        "description": "The headline translated into Simplified Chinese"},
                        "reasoning_zh": {"type": "string",
                                         "description": "The reasoning sentence in Simplified Chinese"},
                    },
                    "required": ["id", "headline", "commodity", "signal_category",
                                 "price_direction", "urgency_score", "reasoning",
                                 "headline_zh", "reasoning_zh"],
                },
            }
        },
        "required": ["signals"],
    },
}


def _score_batch(client, batch: pd.DataFrame) -> list:
    lines = [f"{i}. [{r.commodity}] {r.title} ({r.source})"
             for i, r in zip(batch.index, batch.itertuples())]
    msg = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        tools=[TOOL],
        # Newer models don't allow forcing a specific tool, so let the model
        # choose ("auto") and tell it clearly to use record_signals.
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content":
                   "Assess each headline below and call the record_signals tool "
                   "ONCE with one entry per headline, using its id number. Also give "
                   "headline_zh and reasoning_zh in Simplified Chinese. "
                   "If you cannot use the tool, reply with ONLY a JSON array of "
                   "those entries.\n\n" + "\n".join(lines)}],
    )
    text = ""
    for block in msg.content:
        if block.type == "tool_use":
            return as_list(block.input.get("signals", []))
        if block.type == "text":
            text += block.text
    return _parse_json_signals(text)


def _parse_json_signals(text):
    """Backup: pull a JSON array out of a plain-text reply."""
    import json
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        print("  (reply had no JSON array)")
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        print(f"  (could not parse reply as JSON: {e})")
        return []
    if isinstance(data, dict):
        data = data.get("signals", [])
    return [d for d in data if isinstance(d, dict)]


def analyze_headlines(news: pd.DataFrame, use_cache=True) -> pd.DataFrame:
    """Score headlines with Claude. Returns news columns + signal columns."""
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set. See README / setup steps.")

    cached = pd.DataFrame()
    if use_cache and SIGNALS_CSV.exists():
        cached = pd.read_csv(SIGNALS_CSV, parse_dates=["published_date"])
        todo = news[~news["link"].isin(cached["link"])].reset_index(drop=True)
        print(f"  {len(news) - len(todo)} headlines already scored (cached), {len(todo)} new")
    else:
        todo = news.reset_index(drop=True)

    client = anthropic.Anthropic()
    results = []
    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo.iloc[start:start + BATCH_SIZE]
        try:
            scored = _score_batch(client, batch)
        except Exception as e:
            print(f"  Batch {start // BATCH_SIZE + 1} failed: {type(e).__name__}: {e}")
            continue
        for s in scored:
            i = as_id(s.get("id"))
            if i in batch.index:
                row = batch.loc[i].to_dict()
                row.update({k: s[k] for k in ["signal_category", "price_direction",
                                              "urgency_score", "reasoning",
                                              "headline_zh", "reasoning_zh"] if k in s})
                results.append(row)
        print(f"  Batch {start // BATCH_SIZE + 1}: scored {len(scored)} of {len(batch)} headlines")

    new = pd.DataFrame(results)
    out = pd.concat([cached, new], ignore_index=True) if not cached.empty else new
    if out.empty:
        return out
    out = out.drop_duplicates(subset="link", keep="last")
    out["published_date"] = pd.to_datetime(out["published_date"], errors="coerce")
    out = out.sort_values(["urgency_score", "published_date"], ascending=False)
    out.to_csv(SIGNALS_CSV, index=False)
    try:
        out = translate_missing(client, out)
    except Exception as e:      # translation is a nice-to-have: never stop the refresh
        print(f"  Translation skipped: {type(e).__name__}: {e}")
    return out.reset_index(drop=True)


TRANSLATE_TOOL = {
    "name": "record_translations",
    "description": "Record Simplified Chinese translations.",
    "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "integer"},
                       "headline_zh": {"type": "string"},
                       "reasoning_zh": {"type": "string"}},
        "required": ["id", "headline_zh", "reasoning_zh"]}}},
        "required": ["items"]},
}


def translate_missing(client, df, limit=TRANSLATE_PER_RUN):
    """Add Chinese translations to older signals that don't have them yet
    (a few batches per run, most important first). Saves signals.csv."""
    for c in ["headline_zh", "reasoning_zh"]:
        if c not in df:
            df[c] = pd.NA
    need = df[df["headline_zh"].isna() & (df["signal_category"] != "Noise")].head(limit)
    if need.empty:
        return df
    print(f"  Translating {len(need)} older headlines into Chinese...")
    done = 0
    for start in range(0, len(need), 20):
        part = need.iloc[start:start + 20]
        lines = [f"{i}. HEADLINE: {r.title}\n   REASONING: {r.reasoning}"
                 for i, r in zip(part.index, part.itertuples())]
        try:
            msg = client.messages.create(
                model=MODEL, max_tokens=8000, tools=[TRANSLATE_TOOL], tool_choice={"type": "auto"},
                system="You translate commodity-market news into concise, professional Simplified Chinese.",
                messages=[{"role": "user", "content":
                           "Translate each HEADLINE and REASONING into Simplified Chinese and call "
                           "record_translations once, keeping each id.\n\n" + "\n".join(lines)}])
        except Exception as e:
            print(f"  Translation batch failed: {type(e).__name__}: {e}")
            continue
        for block in msg.content:
            if block.type == "tool_use":
                for it in as_list(block.input.get("items", [])):
                    i = as_id(it.get("id"))
                    if i in part.index:
                        df.loc[i, "headline_zh"] = it.get("headline_zh")
                        df.loc[i, "reasoning_zh"] = it.get("reasoning_zh")
                        done += 1
    print(f"  Chinese translations added: {done} of {len(need)}")
    df.to_csv(SIGNALS_CSV, index=False)
    return df


if __name__ == "__main__":
    news_csv = DATA_DIR / "news.csv"
    if not news_csv.exists():
        raise SystemExit("data/news.csv not found - run  py scraper.py  first.")
    news = pd.read_csv(news_csv, parse_dates=["published_date"])
    print(f"Scoring {len(news)} headlines with {MODEL}...")

    signals = analyze_headlines(news)
    if signals.empty:
        raise SystemExit("No signals returned.")

    print(f"\nScored: {len(signals)} headlines")
    print(signals["signal_category"].value_counts().to_string())

    print("\nTop weak signals (urgency >= 3):")
    top = signals[signals["urgency_score"] >= 3]
    for _, r in top.head(15).iterrows():
        arrow = "UP  " if r["price_direction"] > 0.1 else "DOWN" if r["price_direction"] < -0.1 else "FLAT"
        print(f"  [{r['urgency_score']}] {arrow} {r['price_direction']:+.1f} "
              f"[{r['commodity']}] {r['title'][:80]}")
        print(f"        -> {r['reasoning']}")
    print(f"\nSaved to {SIGNALS_CSV}")
