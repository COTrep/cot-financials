"""
CFTC Disaggregated COT — Supabase loader.
Reads all XLS files found under XLS_DIR and upserts into cot_weekly_raw.

Usage:
  python load_commodities.py <xls_dir>

Required env vars:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
"""

import os
import sys
import glob
import numpy as np
import pandas as pd
from supabase import create_client

XLS_DIR    = sys.argv[1] if len(sys.argv) > 1 else "."
TABLE_NAME = "cot_weekly_raw"
CHUNK_SIZE = 500

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

RENAME_MAP = {
    "as_of_date_in_form_yymmdd": "as_of_date_in_form_yyyymmdd",
}

# Text/date columns — left as-is
SKIP_COLS = {
    "market_and_exchange_names",
    "report_date_as_mm_dd_yyyy",
    "as_of_date_in_form_yyyymmdd",
    "cftc_contract_market_code",
}

# Columns that exist in cot_weekly_raw (28 total)
DB_COLS = [
    "market_and_exchange_names",
    "as_of_date_in_form_yyyymmdd",
    "report_date_as_mm_dd_yyyy",
    "cftc_contract_market_code",
    "open_interest_all", "open_interest_old", "open_interest_other",
    "prod_merc_positions_long_all", "prod_merc_positions_long_old", "prod_merc_positions_long_other",
    "prod_merc_positions_short_all", "prod_merc_positions_short_old", "prod_merc_positions_short_other",
    "swap_positions_long_all", "swap_positions_long_old", "swap_positions_long_other",
    "swap_positions_short_all", "swap_positions_short_old", "swap_positions_short_other",
    "m_money_positions_long_all", "m_money_positions_long_old", "m_money_positions_long_other",
    "m_money_positions_short_all", "m_money_positions_short_old", "m_money_positions_short_other",
    "traders_tot_all", "traders_tot_old", "traders_tot_other",
]

files = sorted(
    glob.glob(os.path.join(XLS_DIR, "**", "*.xls"), recursive=True) +
    glob.glob(os.path.join(XLS_DIR, "**", "*.xlsx"), recursive=True)
)
if not files:
    print(f"No XLS files found in {XLS_DIR}")
    sys.exit(1)

frames = []
for f in files:
    try:
        df = pd.read_excel(f, dtype=str, engine="xlrd")
        df.columns = df.columns.str.strip().str.lower()
        df = df.rename(columns=RENAME_MAP)
        frames.append(df)
        print(f"  Read {f}: {len(df)} rows")
    except Exception as e:
        print(f"  SKIP {f}: {e}")

if not frames:
    print("No data loaded.")
    sys.exit(1)

df = pd.concat(frames, ignore_index=True)
print(f"\nTotal rows loaded: {len(df)}")

df = df.drop_duplicates(subset=["market_and_exchange_names", "report_date_as_mm_dd_yyyy"])
print(f"After dedup: {len(df)}")

# Keep only columns that exist in the DB
present = [c for c in DB_COLS if c in df.columns]
missing = [c for c in DB_COLS if c not in df.columns]
if missing:
    print(f"WARNING: columns not in source file (will be NULL): {missing}")
df = df[present]

df.replace([".", "..", "", " "], np.nan, inplace=True)

if "report_date_as_mm_dd_yyyy" in df.columns:
    df["report_date_as_mm_dd_yyyy"] = pd.to_datetime(
        df["report_date_as_mm_dd_yyyy"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")

if "as_of_date_in_form_yyyymmdd" in df.columns:
    df["as_of_date_in_form_yyyymmdd"] = pd.to_datetime(
        df["as_of_date_in_form_yyyymmdd"].astype(str).str.split(".").str[0],
        format="%y%m%d", errors="coerce"
    ).dt.strftime("%Y-%m-%d")

for col in df.columns:
    if col in SKIP_COLS:
        continue
    df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

df = df.astype(object).where(pd.notnull(df), None)

def clean_row(row):
    out = {}
    for k, v in row.items():
        if v is None:
            out[k] = None
        elif isinstance(v, float) and np.isnan(v):
            out[k] = None
        elif hasattr(v, "item"):
            out[k] = int(v)
        else:
            out[k] = v
    return out

records = [clean_row(r) for r in df.to_dict(orient="records")]
print(f"Upserting {len(records)} records into {TABLE_NAME}...")

total, errors = 0, 0
for i in range(0, len(records), CHUNK_SIZE):
    chunk = records[i:i + CHUNK_SIZE]
    try:
        supabase.table(TABLE_NAME).upsert(
            chunk,
            on_conflict="market_and_exchange_names,report_date_as_mm_dd_yyyy"
        ).execute()
        total += len(chunk)
        print(f"  Chunk {i // CHUNK_SIZE + 1}: {len(chunk)} rows OK")
    except Exception as e:
        errors += 1
        print(f"  Chunk {i // CHUNK_SIZE + 1}: ERROR — {e}")

print(f"\nDone. {total} rows upserted, {errors} errors.")
