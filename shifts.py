"""
shifts.py - Find MAJOR price shifts in each commodity's history and link them to
world events (supervisor point 2).

Pieces:
  detect_shifts(hist)   -> the biggest 3-month price moves (non-overlapping)
  explain_shifts.py     -> collects the news around each move and asks Claude
                           what caused it and which headlines warned in advance
                           (saved to data/shift_events.json)
  patterns(...)         -> which kinds of news usually came BEFORE big rises,
                           and whether similar news is appearing now
"""

import json

import numpy as np
import pandas as pd

from forecaster import DATA_DIR

EVENTS_JSON = DATA_DIR / "shift_events.json"
WINDOW = 3            # months over which a move is measured
MAX_PER_COMMODITY = 6
MIN_MOVE = 0.15       # at least 15% in 3 months ...
TYPICAL_MULT = 2.5    # ... and 2.5x the commodity's typical 3-month move
SINCE = "2018-01-01"  # news archives are thin before this
MIN_GAP_MONTHS = 5

SHIFT_CATEGORIES = ["Supply Outage", "Export Policy", "Host Metal Cut", "Refinery Turnaround",
                    "Environment & Weather", "Demand Shift", "Logistics", "Macro & Financial"]


def detect_shifts(hist):
    """Biggest non-overlapping 3-month moves. Noisy customs series are smoothed
    with a 3-month median first so one odd shipment month is not a 'shift'."""
    s = hist.set_index("ds")["y"].astype(float)
    noisy = s.pct_change().abs().median() > 0.05
    sm = s.rolling(3, center=True, min_periods=1).median() if noisy else s
    chg = sm / sm.shift(WINDOW) - 1
    thr = max(MIN_MOVE, TYPICAL_MULT * float(chg.abs().median()))
    cand = chg[(chg.index >= SINCE) & (chg.abs() >= thr)].abs().sort_values(ascending=False)
    picked = []
    for d in cand.index:
        if all(abs((d - p).days) > MIN_GAP_MONTHS * 30 for p in picked):
            picked.append(d)
        if len(picked) >= MAX_PER_COMMODITY:
            break
    out = []
    for end in sorted(picked):
        start = end - pd.DateOffset(months=WINDOW)
        out.append({"start": start, "end": end, "change": float(chg[end]),
                    "price_start": float(s.get(start, np.nan)), "price_end": float(s[end]),
                    "direction": "up" if chg[end] > 0 else "down", "threshold": thr})
    return out


def shift_key(commodity, shift):
    return f"{commodity}|{pd.Timestamp(shift['end']):%Y-%m}"


def load_events():
    try:
        return json.loads(EVENTS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {"shifts": {}}


def patterns(events, commodity=None):
    """Across past moves: how often each kind of news appeared, separately for
    rises and falls, and how many days ahead the warning headlines came."""
    rows = []
    for key, e in events.get("shifts", {}).items():
        if commodity and e["commodity"] != commodity:
            continue
        leads = [w["lead_days"] for w in e.get("warnings", []) if w.get("lead_days") is not None]
        for cat in e.get("categories", []):
            rows.append({"commodity": e["commodity"], "category": cat, "direction": e["direction"],
                         "lead": np.median(leads) if leads else np.nan, "warned": bool(leads),
                         "key": key})
    if not rows:
        return pd.DataFrame(columns=["category", "direction", "moves", "warned_share", "median_lead"])
    df = pd.DataFrame(rows)
    return (df.groupby(["category", "direction"])
            .agg(moves=("key", "nunique"), warned_share=("warned", "mean"), median_lead=("lead", "median"))
            .reset_index().sort_values(["direction", "moves"], ascending=[False, False]))


def current_watch(events, signals, commodity, signals_for, today=None, days=30):
    """Is news of the same kinds that came before this commodity's past big
    RISES appearing now? Returns list of dicts per category."""
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    pat = patterns(events, commodity)
    up = pat[pat["direction"] == "up"]
    if up.empty or signals is None or signals.empty:
        return []
    s = signals_for(signals, commodity)
    if s.empty:
        recent = s
    else:
        s = s.copy()
        s["published_date"] = pd.to_datetime(s["published_date"], errors="coerce")
        recent = s[(s["published_date"] >= today - pd.Timedelta(days=days))
                   & (pd.to_numeric(s["price_direction"], errors="coerce") > 0)]
        if "applies_to" in recent:
            recent = recent[recent["applies_to"] != "All metals (general)"]
    out = []
    for _, r in up.iterrows():
        hits = recent[recent["signal_category"] == r["category"]] if not recent.empty else recent
        out.append({"category": r["category"], "past_moves": int(r["moves"]),
                    "median_lead": r["median_lead"], "now": int(len(hits)),
                    "examples": hits.sort_values("urgency_score", ascending=False).head(3)
                    if len(hits) else hits})
    return out
