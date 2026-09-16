"""
CFTC TFF (Traders in Financial Futures) — Supabase loader.
Reads all XLS files found under XLS_DIR and upserts into cot_financials_raw.

Usage:
  python load_to_supabase.py <xls_dir>

Required env vars:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
"""

import os
import sys
import glob
import time
import numpy as np
import pandas as pd
from supabase import create_client

XLS_DIR    = sys.argv[1] if len(sys.argv) > 1 else "."
TABLE_NAME = "cot_financials_raw"
CHUNK_SIZE = 500

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

# Column lowercased from CFTC: As_of_Date_In_Form_YYMMDD → rename to match DB
RENAME_MAP = {
    "as_of_date_in_form_yymmdd": "as_of_date_in_form_yyyymmdd",
}

SKIP_COLS = {
    "market_and_exchange_names",
    "report_date_as_mm_dd_yyyy",
    "as_of_date_in_form_yyyymmdd",
    "cftc_contract_market_code",
    "cftc_market_code",
    "cftc_region_code",
    "cftc_commodity_code",
    "contract_units",
    "cftc_subgroup_code",
    "futonly_or_combined",
}

# These columns have decimal values — keep as float (NUMERIC in DB)
FLOAT_COLS = {
    "pct_of_open_interest_all",
    "pct_of_oi_dealer_long_all",    "pct_of_oi_dealer_short_all",    "pct_of_oi_dealer_spread_all",
    "pct_of_oi_asset_mgr_long_all", "pct_of_oi_asset_mgr_short_all", "pct_of_oi_asset_mgr_spread_all",
    "pct_of_oi_lev_money_long_all", "pct_of_oi_lev_money_short_all", "pct_of_oi_lev_money_spread_all",
    "pct_of_oi_other_rept_long_all","pct_of_oi_other_rept_short_all","pct_of_oi_other_rept_spread_all",
    "pct_of_oi_tot_rept_long_all",  "pct_of_oi_tot_rept_short_all",
    "pct_of_oi_nonrept_long_all",   "pct_of_oi_nonrept_short_all",
    "conc_gross_le_4_tdr_long_all", "conc_gross_le_4_tdr_short_all",
    "conc_gross_le_8_tdr_long_all", "conc_gross_le_8_tdr_short_all",
    "conc_net_le_4_tdr_long_all",   "conc_net_le_4_tdr_short_all",
    "conc_net_le_8_tdr_long_all",   "conc_net_le_8_tdr_short_all",
}

# ─── Find files ───────────────────────────────────────────────────────────────

files = sorted(
    glob.glob(f"{XLS_DIR}/**/*.xls",  recursive=True) +
    glob.glob(f"{XLS_DIR}/**/*.xlsx", recursive=True)
)

if not files:
    print(f"ERROR: No XLS files found in {XLS_DIR}")
    sys.exit(1)

print(f"Found {len(files)} XLS file(s)")

# ─── Load ─────────────────────────────────────────────────────────────────────

all_frames = []
for filepath in files:
    print(f"Reading: {filepath}")
    try:
        df = pd.read_excel(filepath, dtype=str, engine="xlrd")
        df.columns = df.columns.str.strip().str.lower()
        df = df.rename(columns=RENAME_MAP)
        all_frames.append(df)
        print(f"  {len(df)} rows")
    except Exception as e:
        print(f"  ERROR reading {filepath}: {e}")

if not all_frames:
    print("ERROR: No data loaded")
    sys.exit(1)

df = pd.concat(all_frames, ignore_index=True)
print(f"\nTOTAL ROWS: {len(df)}")

# ─── Dedup ────────────────────────────────────────────────────────────────────

df = df.drop_duplicates(subset=["market_and_exchange_names", "report_date_as_mm_dd_yyyy"])
print(f"AFTER DEDUP: {len(df)}")

# ─── Whitelist — solo instrumentos históricos conocidos ───────────────────────
# El CFTC amplió fut_fin_xls a partir de 2022 con cientos de instrumentos nuevos.
# Solo cargamos los que siempre han estado en la tabla.

ALLOWED_MARKETS = {
    # Divisas — activos continuos
    "AUSTRALIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE",
    "CANADIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE",
    "EURO FX - CHICAGO MERCANTILE EXCHANGE",
    "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE",
    "MEXICAN PESO - CHICAGO MERCANTILE EXCHANGE",
    "SWISS FRANC - CHICAGO MERCANTILE EXCHANGE",
    # Divisas — nombres viejos (congelados 2022-02-01, historia válida)
    "BRITISH POUND STERLING - CHICAGO MERCANTILE EXCHANGE",
    "NEW ZEALAND DOLLAR - CHICAGO MERCANTILE EXCHANGE",
    "U.S. DOLLAR INDEX - ICE FUTURES U.S.",
    # Divisas — nombres nuevos (sucesores activos desde 2022-02-08)
    "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE",
    "NZ DOLLAR - CHICAGO MERCANTILE EXCHANGE",
    "USD INDEX - ICE FUTURES U.S.",
    # Treasuries — nombres viejos (congelados 2022-02-01)
    "10-YEAR U.S. TREASURY NOTES - CHICAGO BOARD OF TRADE",
    "2-YEAR U.S. TREASURY NOTES - CHICAGO BOARD OF TRADE",
    "5-YEAR U.S. TREASURY NOTES - CHICAGO BOARD OF TRADE",
    "30-DAY FEDERAL FUNDS - CHICAGO BOARD OF TRADE",
    "U.S. TREASURY BONDS - CHICAGO BOARD OF TRADE",
    # Treasuries — nombres nuevos (sucesores activos desde 2022-02-08)
    "UST 10Y NOTE - CHICAGO BOARD OF TRADE",
    "UST 2Y NOTE - CHICAGO BOARD OF TRADE",
    "UST 5Y NOTE - CHICAGO BOARD OF TRADE",
    "FED FUNDS - CHICAGO BOARD OF TRADE",
    "UST BOND - CHICAGO BOARD OF TRADE",
    "ULTRA UST 10Y - CHICAGO BOARD OF TRADE",
    "ULTRA UST BOND - CHICAGO BOARD OF TRADE",
    # Índices — activos continuos
    "E-MINI S&P 400 STOCK INDEX - CHICAGO MERCANTILE EXCHANGE",
    "NIKKEI STOCK AVERAGE YEN DENOM - CHICAGO MERCANTILE EXCHANGE",
    "VIX FUTURES - CBOE FUTURES EXCHANGE",
    # Índices — nombres viejos (congelados)
    "E-MINI S&P 500 STOCK INDEX - CHICAGO MERCANTILE EXCHANGE",
    "NASDAQ-100 STOCK INDEX (MINI) - CHICAGO MERCANTILE EXCHANGE",
    "NIKKEI STOCK AVERAGE - CHICAGO MERCANTILE EXCHANGE",
    # Índices — nombres nuevos (sucesores activos desde 2022-02-08)
    "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE",
    "NASDAQ MINI - CHICAGO MERCANTILE EXCHANGE",
    # Índices descontinuados (historia válida)
    "DOW JONES INDUSTRIAL AVERAGE - CHICAGO BOARD OF TRADE",
    "NASDAQ-100 STOCK INDEX - CHICAGO MERCANTILE EXCHANGE",
    "Russell 2000 Stock Index (Mini) - Chicago Mercantile Exchange",
    "Russell 2000 Stock Index Future - Chicago Mercantile Exchange",
    "S&P 400 MIDCAP STOCK INDEX - CHICAGO MERCANTILE EXCHANGE",
    "S&P 500 STOCK INDEX - CHICAGO MERCANTILE EXCHANGE",
}

before = len(df)
df = df[df["market_and_exchange_names"].isin(ALLOWED_MARKETS)]
print(f"AFTER WHITELIST: {len(df)} (dropped {before - len(df)} rows from non-core instruments)")

# ─── Cleanup ──────────────────────────────────────────────────────────────────

df.replace([".", "..", "...", ""], np.nan, inplace=True)

# ─── Dates ────────────────────────────────────────────────────────────────────

# report_date comes as "MM/DD/YYYY" string
if "report_date_as_mm_dd_yyyy" in df.columns:
    df["report_date_as_mm_dd_yyyy"] = pd.to_datetime(
        df["report_date_as_mm_dd_yyyy"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")

# as_of_date is a 6-digit YYMMDD integer in CFTC XLS (e.g. 241231 = 2024-12-31)
if "as_of_date_in_form_yyyymmdd" in df.columns:
    df["as_of_date_in_form_yyyymmdd"] = pd.to_datetime(
        df["as_of_date_in_form_yyyymmdd"].astype(str).str.split(".").str[0],
        format="%y%m%d", errors="coerce"
    ).dt.strftime("%Y-%m-%d")

# ─── Numeric ──────────────────────────────────────────────────────────────────

for col in df.columns:
    if col in SKIP_COLS:
        continue
    if col in FLOAT_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    else:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

# ─── JSON-safe serialization ──────────────────────────────────────────────────

df = df.astype(object).where(pd.notnull(df), None)
records = df.to_dict(orient="records")

def clean(v):
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    if hasattr(v, "item"):          # numpy / pandas Int64
        return int(v)
    return v

records = [{k: clean(v) for k, v in r.items()} for r in records]
print(f"RECORDS TO UPSERT: {len(records)}")

# ─── Upsert ───────────────────────────────────────────────────────────────────

MAX_RETRIES = 3
RETRY_DELAY = 30  # segundos, lineal: 30s, 60s, 90s — tolera que Supabase despierte si estaba pausado

for i in range(0, len(records), CHUNK_SIZE):
    chunk = records[i : i + CHUNK_SIZE]
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            supabase.table(TABLE_NAME).upsert(
                chunk,
                on_conflict="market_and_exchange_names,report_date_as_mm_dd_yyyy",
            ).execute()
            print(f"UPSERTED {i}–{i + len(chunk)}")
            break
        except Exception as e:
            print(f"ERROR IN CHUNK {i} (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt == MAX_RETRIES:
                sys.exit(1)
            time.sleep(RETRY_DELAY * attempt)

print("LOAD COMPLETE")
