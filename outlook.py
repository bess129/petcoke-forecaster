"""
outlook.py - Claude "supply-shortage outlook" for every commodity.

For each commodity, Claude reads the last 30 days of relevant scored headlines
plus recent price moves, and answers: is there UPSIDE price risk from a supply
shortage, and why? Saved to data/outlook.json (shown in the dashboard).

Run after signal_detector.py (the daily GitHub Action does this):
    py outlook.py
Needs ANTHROPIC_API_KEY. Costs roughly US$0.02-0.05 per run (one request).
"""

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sys

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from forecaster import COMMODITIES, DATA_DIR, load_history
from shock import signals_for, supply_outlook, OUTLOOK_DAYS
from signal_detector import MODEL, SIGNALS_CSV

OUTLOOK_JSON = DATA_DIR / "outlook.json"
MAX_HEADLINES = 15   # per commodity, most urgent first

SYSTEM = """You are a senior commodities analyst specialising in supply chains for \
minor metals, rare earths and petroleum coke (main focus: green petroleum coke, GPC). You judge whether a commodity faces \
UPSIDE price risk because of a supply shortage in the next 1-3 months.

Rules:
- Base your view ONLY on the headlines and price data given. Do not invent events.
- Supply shortages (outages, export bans/quotas, mine or smelter cuts, refinery \
turnarounds, logistics blockages) are bullish; new supply, restarts and weak demand are bearish.
- If the price has ALREADY risen sharply, say whether the news suggests further upside \
or that the shortage is priced in.
- Be calibrated: with few or vague headlines, say 'Neutral' with 'low' confidence.
- Plain English, short. No investment advice."""

TOOL = {
    "name": "record_outlook",
    "description": "Record the supply-shortage outlook for every commodity.",
    "input_schema": {
        "type": "object",
        "properties": {"outlooks": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "commodity": {"type": "string"},
                "outlook": {"type": "string",
                            "enum": ["Upside risk", "Neutral", "Downside risk"]},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                "drivers": {"type": "array", "items": {"type": "string"},
                            "description": "1-3 short drivers taken from the headlines"},
                "summary": {"type": "string", "description": "1-2 sentences"},
                "watch_next": {"type": "string", "description": "1 sentence: what event to watch"},
            },
            "required": ["commodity", "outlook", "confidence", "drivers", "summary", "watch_next"],
        }}},
        "required": ["outlooks"],
    },
}


def build_brief(signals, histories, today):
    """Compact text brief: per commodity, price moves + top headlines."""
    ranking = supply_outlook(signals, histories, today).set_index("commodity")
    parts = []
    for commodity, hist in histories.items():
        r = ranking.loc[commodity]
        s = signals_for(signals, commodity).copy()
        s["published_date"] = pd.to_datetime(s["published_date"], errors="coerce")
        s = s[(s["published_date"] >= today - pd.Timedelta(days=OUTLOOK_DAYS))
              & (s["signal_category"] != "Noise")]
        s = s.sort_values(["urgency_score", "published_date"], ascending=False).head(MAX_HEADLINES)
        lines = [f"- {d:%Y-%m-%d} [{c}, urgency {u}, direction {p:+.1f}] {t}"
                 for d, c, u, p, t in zip(s["published_date"], s["signal_category"],
                                          s["urgency_score"], s["price_direction"], s["title"])]
        fmt = lambda v: "n/a" if pd.isna(v) else f"{v:+.0%}"
        parts.append(
            f"## {commodity}\n"
            f"Latest price {hist['y'].iloc[-1]:,.2f} ({hist['ds'].iloc[-1]:%b %Y}); "
            f"3-month change {fmt(r['price_3m'])}; 12-month change {fmt(r['price_12m'])}.\n"
            f"Quantitative supply-pressure score {r['supply_pressure']:+.2f} "
            f"from {r['supply_signals']} supply headlines.\n"
            + ("\n".join(lines) if lines else "- (no relevant headlines)"))
    return "\n\n".join(parts)


def generate_outlook():
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    if not SIGNALS_CSV.exists():
        raise RuntimeError("data/signals.csv not found - run signal_detector.py first.")

    today = pd.Timestamp.today().normalize()
    signals = pd.read_csv(SIGNALS_CSV)
    histories = {c: load_history(DATA_DIR / v["file"]) for c, v in COMMODITIES.items()
                 if (DATA_DIR / v["file"]).exists()}
    brief = build_brief(signals, histories, today)

    msg = anthropic.Anthropic().messages.create(
        model=MODEL, max_tokens=6000, system=SYSTEM,
        tools=[TOOL], tool_choice={"type": "auto"},
        messages=[{"role": "user", "content":
                   "Give the supply-shortage outlook for EVERY commodity below by calling "
                   "record_outlook once. If you cannot use the tool, reply with only the "
                   "JSON object {\"outlooks\": [...]}.\n\n" + brief}],
    )
    data = None
    for block in msg.content:
        if block.type == "tool_use":
            data = block.input
    if data is None:
        text = "".join(b.text for b in msg.content if b.type == "text")
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])

    result = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
              "model": MODEL, "outlooks": data.get("outlooks", [])}
    OUTLOOK_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def load_outlook():
    if not OUTLOOK_JSON.exists():
        return None
    try:
        return json.loads(OUTLOOK_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None


if __name__ == "__main__":
    print(f"Generating supply-shortage outlook with {MODEL}...")
    res = generate_outlook()
    for o in res["outlooks"]:
        print(f"  {o['commodity']:31s} {o['outlook']:13s} ({o['confidence']}) - {o['summary']}")
    print(f"\nSaved to {OUTLOOK_JSON}")
