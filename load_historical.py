"""
CFTC TFF historical loader — downloads ALL years 2006-current and upserts
into cot_financials_raw.

URL pattern:
  Bulk 2006-2016: https://www.cftc.gov/files/dea/history/fin_fut_xls_2006_2016.zip
  Annual 2017+:   https://www.cftc.gov/files/dea/history/fut_fin_xls_{YEAR}.zip

Usage:
  python load_historical.py

Required env vars:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
"""

import os
import sys
import glob
import time
import zipfile
import urllib.request
import urllib.error
import numpy as np
import pandas as pd
from pathlib import Path
from supabase import create_client

# ─── Config ───────────────────────────────────────────────────────────────────

DOWNLOAD_DIR = Path("C:/Users/Jesús/AppData/Local/Temp/cftc_tff/historical")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

TABLE_NAME = "cot_financials_raw"
CHUNK_SIZE = 500

UA      = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
REFERER = "https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed/index.htm"

BULK_URL  = "https://www.cftc.gov/files/dea/history/fin_fut_xls_2006_2016.zip"
ANNUAL_URL = "https://www.cftc.gov/files/dea/history/fut_fin_xls_{year}.zip"

import datetime
CURRENT_YEAR = datetime.date.today().year
ANNUAL_YEARS = list(range(2017, CURRENT_YEAR + 1))

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

# ─── Column handling ──────────────────────────────────────────────────────────

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

# ─── Download helper ──────────────────────────────────────────────────────────

def download(url: str, dest: Path) -> bool:
    if dest.exists():
        print(f"  Already downloaded: {dest.name}")
        return True
    print(f"  Downloading {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REFERER})
        with urllib.request.urlopen(req, timeout=90) as resp, open(dest, "wb") as f:
            f.write(resp.read())
        print(f"  Saved {dest.stat().st_size / 1024 / 1024:.1f} MB")
        time.sleep(1)
        return True
    except Exception as e:
        print(f"  ERROR: {e}")
        return False

# ─── Build list of (zip_path, extract_dir) pairs to process ──────────────────

sources = []

# Bulk 2006-2016
bulk_zip = DOWNLOAD_DIR / "tff_2006_2016.zip"
bulk_dir = DOWNLOAD_DIR / "xls_2006_2016"
if download(BULK_URL, bulk_zip):
    bulk_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(bulk_zip) as z:
        z.extractall(bulk_dir)
    sources.append(bulk_dir)

# Annual 2017-current
for year in ANNUAL_YEARS:
    zip_path = DOWNLOAD_DIR / f"tff_{year}.zip"
    xls_dir  = DOWNLOAD_DIR / f"xls_{year}"
    if download(ANNUAL_URL.format(year=year), zip_path):
        xls_dir.mkdir(exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(xls_dir)
            sources.append(xls_dir)
        except Exception as e:
            print(f"  ERROR extracting {year}: {e}")

# ─── Load all XLS files ───────────────────────────────────────────────────────

all_frames = []
for src_dir in sources:
    files = (
        list(src_dir.glob("**/*.xls")) +
        list(src_dir.glob("**/*.xlsx"))
    )
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
print(f"\nTOTAL COMBINED: {len(df)}")

df = df.drop_duplicates(subset=["market_and_exchange_names", "report_date_as_mm_dd_yyyy"])
print(f"AFTER GLOBAL DEDUP: {len(df)}")
print(f"UNIQUE MARKETS: {df['market_and_exchange_names'].nunique()}")

df.replace([".", "..", "...", ""], np.nan, inplace=True)

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
    if col in FLOAT_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    else:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

df = df.astype(object).where(pd.notnull(df), None)
records = df.to_dict(orient="records")

def clean(v):
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    if hasattr(v, "item"):
        return int(v)
    return v

records = [{k: clean(v) for k, v in r.items()} for r in records]
print(f"RECORDS TO UPSERT: {len(records)}")

for i in range(0, len(records), CHUNK_SIZE):
    chunk = records[i : i + CHUNK_SIZE]
    try:
        supabase.table(TABLE_NAME).upsert(
            chunk,
            on_conflict="market_and_exchange_names,report_date_as_mm_dd_yyyy",
        ).execute()
        print(f"UPSERTED {i}–{i + len(chunk)}")
    except Exception as e:
        print(f"ERROR IN CHUNK {i}: {e}")
        sys.exit(1)

print("\nHISTORICAL LOAD COMPLETE")
