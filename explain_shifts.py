"""
explain_shifts.py - For every major price shift (shifts.detect_shifts), collect
the Google News headlines from 2 months before to just after the move, and ask
Claude: what most likely caused it, and which headlines were early warnings?

Claude may only cite the headlines it is given (checked by id), so every cause
on the dashboard links to a real article. Results are cached in
data/shift_events.json - each shift is analysed once, so after the first run the
daily update only analyses NEW shifts (normally none, so it costs nothing).

Run (needs internet + ANTHROPIC_API_KEY):  py explain_shifts.py
First run: ~40-50 shifts, 5-10 minutes, roughly US$1-3 of Claude usage.
"""

from datetime import datetime, timezone
from urllib.parse import quote_plus
import json
import os
import re
import socket
import sys
import time

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from forecaster import COMMODITIES, DATA_DIR, load_history
from shifts import (detect_shifts, shift_key, load_events, EVENTS_JSON, SHIFT_CATEGORIES)
from signal_detector import MODEL, as_list, as_id

socket.setdefaulttimeout(20)
MAX_PER_RUN = 30          # shifts analysed per run (the rest continue next run)
TIME_BUDGET = 15 * 60     # seconds
MAX_HEADLINES = 70

BASE = {
    "Green Pet Coke (GPC)": '("petroleum coke" OR petcoke)',
    "Calcined Pet Coke (CPC)": '("calcined petroleum coke" OR "calcined coke" OR "anode grade coke" OR petcoke)',
    "Pet Coke PPI index (broad)": '("petroleum coke" OR petcoke)',
    "Cobalt": "cobalt",
    "Molybdenum": "molybdenum",
    "Lithium": "lithium",
    "Rare Earths": '("rare earth" OR "rare earths")',
    "Tin": '(tin price OR tin smelter OR tin mine OR "tin market")',
    "Antimony": "antimony",
    "Gallium/Germanium (HS 811292)": "(gallium OR germanium)",
}
ANGLES = [
    "",
    "(price OR prices OR shortage OR surplus OR export OR ban OR quota OR outage OR strike)",
    "(environmental OR pollution OR emission OR inspection OR flood OR hurricane OR typhoon OR drought OR weather)",
]

SYSTEM = """You are a senior commodity market historian. You are given a MAJOR price move of one
commodity and the news headlines published from about 2 months before the move started until
just after it ended. Explain the move using ONLY these headlines.

Rules:
- cite headlines only by their [id]; never invent events, numbers or articles
- 'drivers': up to 5 headline ids that best show the CAUSE of the move
- 'warnings': up to 5 headline ids published BEFORE or at the very start of the move that,
  read at the time, hinted that this move was coming (early warning signs). Empty if none.
- categories: the kinds of cause (one or more of the allowed list). Use "Environment & Weather"
  for environmental policy/inspections/pollution or emission rules and for weather/climate events
  (hurricanes, floods, drought, typhoons, power rationing in heatwaves).
- if the headlines do not explain the move, say so and use confidence "low"
- Chinese fields must be natural Simplified Chinese for business readers (not word-by-word)"""

TOOL = {
    "name": "record_shift",
    "description": "Record the explanation of one price shift.",
    "input_schema": {"type": "object", "properties": {
        "cause_en": {"type": "string", "description": "Main cause in <= 12 words"},
        "cause_zh": {"type": "string", "description": "Main cause in <= 25 Chinese characters"},
        "explanation_en": {"type": "string", "description": "2-3 sentences"},
        "explanation_zh": {"type": "string", "description": "2-3 sentences in Chinese"},
        "categories": {"type": "array", "items": {"type": "string", "enum": SHIFT_CATEGORIES}},
        "drivers": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "integer"}, "title_zh": {"type": "string"}}, "required": ["id", "title_zh"]}},
        "warnings": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "integer"}, "title_zh": {"type": "string"}}, "required": ["id", "title_zh"]}},
        "lesson_en": {"type": "string", "description": "What to watch for next time, 1 sentence"},
        "lesson_zh": {"type": "string", "description": "Same, in Chinese"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    }, "required": ["cause_en", "cause_zh", "explanation_en", "explanation_zh", "categories",
                    "drivers", "warnings", "lesson_en", "lesson_zh", "confidence"]},
}


def _rss(query, after, before):
    q = f"{query} after:{after:%Y-%m-%d} before:{before:%Y-%m-%d}"
    return "https://news.google.com/rss/search?q=" + quote_plus(q) + "&hl=en-US&gl=US&ceid=US:en"


def fetch_headlines(commodity, shift):
    import feedparser
    after = shift["start"] - pd.DateOffset(months=2)
    before = shift["end"] + pd.Timedelta(days=20)
    rows = []
    for angle in ANGLES:
        q = f"{BASE.get(commodity, commodity)} {angle}".strip()
        try:
            feed = feedparser.parse(_rss(q, after, before), agent="Mozilla/5.0 (commodity-forecaster)")
        except Exception as e:
            print(f"    news query failed: {e}")
            continue
        for e in feed.entries:
            src = e.get("source", {}).get("title", "") if e.get("source") else ""
            title = e.get("title", "")
            if src and title.endswith(" - " + src):
                title = title[: -len(" - " + src)]
            rows.append({"title": title.strip(), "date": e.get("published", ""), "source": src,
                         "link": e.get("link", "")})
        time.sleep(1.0)
    df = pd.DataFrame(rows, columns=["title", "date", "source", "link"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True).dt.tz_localize(None)
    df = df[(df["date"] >= after) & (df["date"] < before)]
    key = df["title"].str.lower().str.replace(r"[^a-z0-9 ]", "", regex=True).str.strip()
    df = df.loc[~key.duplicated()].sort_values("date")
    if len(df) > MAX_HEADLINES:                      # keep an even spread over the window
        idx = pd.Series(range(len(df))).sample(MAX_HEADLINES, random_state=1).sort_values()
        df = df.iloc[idx.values]
    return df.reset_index(drop=True)


def ask_claude(client, commodity, shift, heads):
    lines = "\n".join(f"[{i}] {r.date:%Y-%m-%d} | {r.source} | {r.title}" for i, r in heads.iterrows())
    move = (f"{commodity}: price {'ROSE' if shift['change'] > 0 else 'FELL'} {shift['change']:+.0%} "
            f"from {shift['start']:%b %Y} ({shift['price_start']:.4g}) to {shift['end']:%b %Y} "
            f"({shift['price_end']:.4g}). Monthly price data.")
    msg = client.messages.create(
        model=MODEL, max_tokens=3000, system=SYSTEM, tools=[TOOL], tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": f"{move}\n\nHeadlines:\n{lines}\n\nCall record_shift."}])
    for b in msg.content:
        if b.type == "tool_use" and b.name == "record_shift":
            return b.input
    text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


def _strings(value):
    """A list of strings, even if Claude returned it as a JSON string."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = [v.strip(" '\"") for v in value.strip("[]").split(",")]
    return [str(v) for v in (value or []) if isinstance(v, (str, int))]


def _cite(items, heads, move_start=None):
    out = []
    for it in as_list(items):
        i = as_id(it.get("id")) if isinstance(it, dict) else as_id(it)
        try:
            i = int(i)
        except (TypeError, ValueError):
            continue
        if i not in heads.index:
            continue                                  # Claude cited a headline that doesn't exist
        r = heads.loc[i]
        d = {"title": r["title"], "title_zh": it.get("title_zh", "") if isinstance(it, dict) else "",
             "date": r["date"].strftime("%Y-%m-%d"), "source": r["source"], "link": r["link"]}
        if move_start is not None:
            lead = int((move_start + pd.DateOffset(months=1) - r["date"]).days)
            if lead < 0:
                continue                              # not an early warning if it came after
            d["lead_days"] = int((move_start - r["date"]).days)
        out.append(d)
    return out


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set - skipping shift explanations."); return
    import anthropic
    client = anthropic.Anthropic()
    events = load_events()
    known = events.setdefault("shifts", {})
    current, todo = set(), []
    for c, cfg in COMMODITIES.items():
        path = DATA_DIR / cfg["file"]
        if not path.exists():
            continue
        for s in detect_shifts(load_history(path)):
            k = shift_key(c, s)
            current.add(k)
            if k not in known:
                todo.append((c, s, k))
    for k in [k for k in known if k not in current]:   # shifts no longer detected (data revised)
        known.pop(k)
    print(f"{len(current)} major price shifts found; {len(todo)} new to explain.")
    t0, done = time.time(), 0
    for c, s, k in todo[:MAX_PER_RUN]:
        if time.time() - t0 > TIME_BUDGET:
            print("Time budget reached - the rest continue in the next run."); break
        heads = fetch_headlines(c, s)
        print(f"  {k} ({s['change']:+.0%}): {len(heads)} headlines")
        base = {"commodity": c, "start": f"{s['start']:%Y-%m-%d}", "end": f"{s['end']:%Y-%m-%d}",
                "change": s["change"], "direction": s["direction"], "price_start": s["price_start"],
                "price_end": s["price_end"], "n_headlines": int(len(heads))}
        if len(heads) == 0:
            print("    no headlines (news search may have failed) - will retry next run"); continue
        if len(heads) < 3:
            known[k] = {**base, "cause_en": "Not enough news found for this period",
                        "cause_zh": "该时期新闻太少，无法判断原因", "explanation_en": "", "explanation_zh": "",
                        "categories": [], "drivers": [], "warnings": [], "lesson_en": "", "lesson_zh": "",
                        "confidence": "low"}
            continue
        try:
            r = ask_claude(client, c, s, heads)
        except Exception as e:
            print(f"    Claude failed ({type(e).__name__}: {e}) - will retry next run"); continue
        if not r:
            continue
        known[k] = {**base, **{f: r.get(f, "") for f in ["cause_en", "cause_zh", "explanation_en",
                                                         "explanation_zh", "lesson_en", "lesson_zh"]},
                    "categories": [x for x in _strings(r.get("categories")) if x in SHIFT_CATEGORIES],
                    "drivers": _cite(r.get("drivers"), heads),
                    "warnings": _cite(r.get("warnings"), heads, move_start=s["start"]),
                    "confidence": r.get("confidence", "low")}
        done += 1
        EVENTS_JSON.write_text(json.dumps({**events, "shifts": known}, ensure_ascii=False, indent=1),
                               encoding="utf-8")       # save progress
    events.update({"generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                   "model": MODEL, "shifts": known})
    EVENTS_JSON.write_text(json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8")
    left = sum(1 for _, _, k in todo if k not in known)
    print(f"Explained {done} shifts; {left} still to do. Saved {EVENTS_JSON.name}.")


if __name__ == "__main__":
    main()
