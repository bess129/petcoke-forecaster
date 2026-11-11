"""
import_argus.py - Import petroleum coke prices from an Argus subscription
export (Excel or CSV) or from your own company quotes, into data/gpc_prices.csv.

HOW TO USE (in the future, if you subscribe to Argus):
  1. Download the price history from Argus Direct / Argus Metals as Excel or CSV.
  2. Put the file in the folder  data/argus/   (any file name, .xlsx or .csv).
  3. Run:  py import_argus.py      (or let the daily GitHub Action run it).
The script reads each price name (e.g. "Petroleum coke 6.5% sulphur HGI 40-45
fob US Gulf coast") and fills in sulphur %, HGI, vanadium, basis and market.
Already-imported rows are skipped, so running it twice is safe.

COMPANY QUOTES: fill in data/gpc_prices_template.csv (one row per quote) and
save it into data/argus/ - it is imported the same way, labelled as your source.

Recognised columns (names are matched loosely, any language case):
  date | assessment date | 日期
  description | price name | name | 描述 | 品名
  price | mid | value | 价格     (or)   low + high (the middle is used)
  optional: origin, market, basis, sulfur_pct, vanadium_ppm, hgi, source, source_url
"""

from pathlib import Path
import re
import sys

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gpc_prices import PRICES_CSV, COLUMNS, DATA_DIR

IMPORT_DIR = DATA_DIR / "argus"

ALIASES = {
    "date": ["date", "assessment date", "price date", "日期", "time"],
    "description": ["description", "price name", "name", "assessment", "code description", "描述", "品名", "规格"],
    "price": ["price", "mid", "value", "close", "价格", "均价"],
    "low": ["low", "最低"], "high": ["high", "最高"],
}
MARKETS = [  # (regex, market, origin)
    (r"us ?gulf|usgc|gulf coast", "US Gulf coast", "USA"),
    (r"saudi|ras tanura|jubail|yanbu", "Saudi Arabia", "Saudi Arabia"),
    (r"russia|baltic|black sea", "Russia", "Russia"),
    (r"canada|vancouver", "Canada", "Canada"),
    (r"venezuela", "Venezuela", "Venezuela"),
    (r"colombia", "Colombia", "Colombia"),
    (r"west coast india|wc india", "West coast India", None),
    (r"india", "India", None),
    (r"us ?west|uswc|west coast", "US west coast", "USA"),
    (r"china", "China", None),
    (r"turkey|türkiye", "Turkey", None),
    (r"brazil", "Brazil", None),
    (r"europe|nwe|ara", "Europe", None),
]


def _find(cols, key):
    low = {str(c).strip().lower(): c for c in cols}
    for a in ALIASES[key]:
        if a in low:
            return low[a]
    return None


def parse_description(text):
    """Pull sulphur %, vanadium ppm, HGI, basis and market from a price name."""
    t = str(text or "").lower()
    out = {}
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|pc|per ?cent)\s*(?:s\b|sulph|sulf)", t) or \
        re.search(r"(?:sulph\w*|sulf\w*|s)\s*(\d+(?:\.\d+)?)\s*(?:%|pc)", t)
    if m:
        out["sulfur_pct"] = float(m.group(1))
    m = re.search(r"(?:v|vanadium)\s*(\d{2,4})\s*ppm|(\d{2,4})\s*ppm\s*(?:v\b|vanad)", t)
    if m:
        out["vanadium_ppm"] = float(m.group(1) or m.group(2))
    m = re.search(r"hgi\s*(\d{2,3}(?:\s*-\s*\d{2,3})?)", t)
    if m:
        out["hgi"] = m.group(1).replace(" ", "")
    for b, name in [("fob", "FOB"), ("cfr", "CFR"), ("cif", "CIF"), ("delivered", "Delivered"),
                    ("del ", "Delivered"), ("ex-works", "Ex-works")]:
        if b in t:
            out["basis"] = name
            break
    for pat, market, origin in MARKETS:
        if re.search(pat, t):
            out["market"] = market
            if origin:
                out["origin"] = origin
            break
    if "anode" in t or "calcining" in t:
        out["note"] = "Anode/calcining grade"
    return out


def read_file(path):
    if path.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(path)
    for enc in ("utf-8-sig", "gbk", "latin-1"):
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path, encoding="latin-1", errors="replace")


def convert(raw, file_name):
    dcol, ncol = _find(raw.columns, "date"), _find(raw.columns, "description")
    pcol, lcol, hcol = _find(raw.columns, "price"), _find(raw.columns, "low"), _find(raw.columns, "high")
    if dcol is None or (pcol is None and not (lcol and hcol)):
        print(f"  {file_name}: could not find date/price columns - skipped "
              f"(columns: {list(raw.columns)[:8]})")
        return pd.DataFrame(columns=COLUMNS)
    is_template = "sulfur_pct" in raw.columns or "series_id" in raw.columns
    rows = []
    for _, r in raw.iterrows():
        d = pd.to_datetime(r[dcol], errors="coerce")
        price = pd.to_numeric(r[pcol], errors="coerce") if pcol is not None else \
            (pd.to_numeric(r[lcol], errors="coerce") + pd.to_numeric(r[hcol], errors="coerce")) / 2
        if pd.isna(d) or pd.isna(price):
            continue
        if "example" in " ".join(str(v).lower() for v in r.values):
            continue                      # skip the template's example row
        desc = r[ncol] if ncol is not None else ""
        info = parse_description(desc)
        for k in ["origin", "market", "basis", "sulfur_pct", "vanadium_ppm", "hgi", "note",
                  "source", "source_url", "currency", "unit"]:   # explicit columns win
            if k in raw.columns and pd.notna(r.get(k)) and str(r.get(k)).strip():
                info[k] = r[k]
        src = info.get("source") or ("Company quote" if is_template else "Argus (subscription)")
        s = info.get("sulfur_pct")
        grade = f"{float(s):.1f}S" if pd.notna(pd.to_numeric(s, errors="coerce")) else "MIXED"
        sid = r.get("series_id") if is_template and pd.notna(r.get("series_id")) else \
            f"IMP_{src}_{info.get('basis', '')}_{info.get('market', desc)}_{grade}"
        rows.append({"date": d.strftime("%Y-%m-%d"),
                     "series_id": re.sub(r"[^A-Za-z0-9_.]+", "_", str(sid)).upper()[:60],
                     "origin": info.get("origin", "Various"), "market": info.get("market", str(desc)[:40]),
                     "basis": info.get("basis", ""), "sulfur_pct": s,
                     "vanadium_ppm": info.get("vanadium_ppm"), "hgi": info.get("hgi"),
                     "price": float(price), "currency": info.get("currency", "USD"),
                     "unit": info.get("unit", "t"),
                     "price_type": "company quote" if is_template else "assessment",
                     "source": src, "source_url": info.get("source_url", ""),
                     "published": d.strftime("%Y-%m-%d"),
                     "retrieved": pd.Timestamp.today().strftime("%Y-%m-%d"),
                     "method": "imported", "note": f"{info.get('note', '')} [{file_name}] {desc}".strip()[:300]})
    return pd.DataFrame(rows, columns=COLUMNS)


def main():
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in IMPORT_DIR.iterdir() if p.suffix.lower() in (".csv", ".xlsx", ".xls"))
    if not files:
        print("No files in data/argus/ - nothing to import.")
        return
    existing = pd.read_csv(PRICES_CSV) if PRICES_CSV.exists() else pd.DataFrame(columns=COLUMNS)
    new = pd.concat([convert(read_file(p), p.name) for p in files], ignore_index=True)
    if new.empty:
        print("No usable rows found.")
        return
    key = lambda df: df["series_id"].astype(str) + "|" + df["date"].astype(str) + "|" + \
        pd.to_numeric(df["price"], errors="coerce").round(2).astype(str)
    new = new[~key(new).isin(set(key(existing)))].drop_duplicates()
    if new.empty:
        print(f"Checked {len(files)} file(s): everything already imported.")
        return
    out = pd.concat([existing, new], ignore_index=True)[COLUMNS]
    out.to_csv(PRICES_CSV, index=False)
    print(f"Imported {len(new)} new price rows from {len(files)} file(s), "
          f"{new['series_id'].nunique()} price series, latest date {new['date'].max()}.")


if __name__ == "__main__":
    main()
