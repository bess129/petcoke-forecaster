"""
imf_to_csv.py - Turn the IMF Primary Commodity Prices Excel file into price CSVs.

Download "Excel Database" from https://www.imf.org/en/research/commodity-prices,
save it as data/external-data.xlsx, then run:
    py imf_to_csv.py

Writes data/<metal>.csv (columns: date, price) for each metal below.
Columns are found by their description text, because some IMF columns
(lithium, rare earths) have no code.
"""

from pathlib import Path
import sys

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA_DIR = Path(__file__).parent / "data"
IMF_FILE = DATA_DIR / "external-data.xlsx"

# output file -> text that identifies the column (code row or description row)
SERIES = {
    "cobalt.csv":      "PCOBA",
    "molybdenum.csv":  "PLMMODY",
    "tin.csv":         "PTIN",
    "lithium.csv":     "Lithium Metal",
    "rare_earths.csv": "Rare Earth Carbonate",
}


def main():
    raw = pd.read_excel(IMF_FILE, header=None)
    codes = raw.iloc[0].astype(str)
    descs = raw.iloc[1].astype(str)
    periods = raw.iloc[4:, 0].astype(str)           # e.g. 2026M9
    dates = pd.to_datetime(periods.str.replace("M", "-") + "-01",
                           format="%Y-%m-%d", errors="coerce")

    for fname, key in SERIES.items():
        match = [i for i in range(raw.shape[1])
                 if codes.iloc[i] == key or key.lower() in descs.iloc[i].lower()]
        if not match:
            print(f"  {fname}: column '{key}' not found, skipped")
            continue
        col = match[0]
        prices = pd.to_numeric(raw.iloc[4:, col], errors="coerce")
        out = pd.DataFrame({"date": dates.values, "price": prices.values}).dropna()
        out["date"] = out["date"].dt.strftime("%Y-%m-%d")
        out["price"] = out["price"].round(2)
        out.to_csv(DATA_DIR / fname, index=False)
        print(f"  {fname}: {len(out)} months, {out['date'].iloc[0]} to {out['date'].iloc[-1]}, "
              f"last = {out['price'].iloc[-1]:,.0f}  ({descs.iloc[col][:60]})")


if __name__ == "__main__":
    main()
