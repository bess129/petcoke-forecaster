"""
comtrade_to_csv.py - Turn UN Comtrade downloads into monthly price CSVs.

The "unit-value method" from the sprint brief:
    implied price (USD/kg) = trade value (primaryValue) / net weight (netWgt)

Put every UNComtrade-*.csv download in /data, then run:
    py comtrade_to_csv.py

Writes antimony.csv, gallium.csv, petcoke_fuel.csv, petcoke_calcined.csv (date, price).
Only rows with Partner = World are used (total trade with all countries).
"""

from pathlib import Path
import sys

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA_DIR = Path(__file__).parent / "data"

# HS code -> (output file, multiplier). Price = value / kg x multiplier.
OUTPUTS = {
    811010: ("antimony.csv", 1),             # Antimony, unwrought; powders      -> USD/kg
    811292: ("gallium.csv", 1),              # Ga, Ge, In, Nb, V (mixed group)   -> USD/kg
    271311: ("petcoke_fuel.csv", 1000),      # Petroleum coke, not calcined      -> USD/tonne
    271312: ("petcoke_calcined.csv", 1000),  # Petroleum coke, calcined          -> USD/tonne
}
MIN_KG = 1000   # ignore months with tiny shipments (sample lots give silly prices)


def main():
    files = sorted(DATA_DIR.glob("UNComtrade-*.csv"))
    if not files:
        raise SystemExit("No UNComtrade-*.csv files found in data/.")

    frames = [pd.read_csv(f, encoding="utf-8-sig") for f in files]
    df = pd.concat(frames, ignore_index=True)
    print(f"Read {len(files)} files, {len(df)} rows")

    df = df[(df["partnerCode"] == 0) & (df["cmdCode"].isin(OUTPUTS))]
    df = df.drop_duplicates(subset=["period", "reporterCode", "flowCode", "cmdCode"], keep="last")
    df = df.dropna(subset=["netWgt", "primaryValue"])

    for code, (fname, mult) in OUTPUTS.items():
        sub = df[df["cmdCode"] == code]
        if sub.empty:
            continue
        g = sub.groupby("period")[["primaryValue", "netWgt"]].sum()
        g = g[g["netWgt"] >= MIN_KG]
        out = pd.DataFrame({
            "date": pd.to_datetime(g.index.astype(str), format="%Y%m").strftime("%Y-%m-%d"),
            "price": (g["primaryValue"] / g["netWgt"] * mult).round(2).values,
        }).sort_values("date")
        out.to_csv(DATA_DIR / fname, index=False)
        print(f"\nHS {code} -> data/{fname}: {len(out)} months "
              f"({out['date'].min()} to {out['date'].max()}), USD/{'tonne' if mult == 1000 else 'kg'}")
        print(out.tail(6).to_string(index=False))


if __name__ == "__main__":
    main()
