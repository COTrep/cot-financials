"""
CFTC Disaggregated COT — historical backfill.
Downloads all annual zips (2006-2016 bulk + 2017→current) and upserts into cot_weekly_raw.

Usage:
  python load_disagg_historical.py

Required env vars:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
"""

import os
import sys
import glob
import shutil
import tempfile
import zipfile
import urllib.request
from datetime import datetime

import numpy as np
import pandas as pd
from supabase import create_client

TABLE_NAME = "cot_weekly_raw"
CHUNK_SIZE = 500
CURRENT_YEAR = datetime.now().year

DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), "cftc_disagg_historical")

URLS = [
    # Bulk 2006-2016
    ("2006-2016", "https://www.cftc.gov/files/dea/history/fut_disagg_xls_2006_2016.zip"),
] + [
    (str(y), f"https://www.cftc.gov/files/dea/history/fut_disagg_xls_{y}.zip")
    for y in range(2017, CURRENT_YEAR + 1)
]

RENAME_MAP = {
    "as_of_date_in_form_yymmdd": "as_of_date_in_form_yyyymmdd",
}

SKIP_COLS = {
    "market_and_exchange_names",
    "report_date_as_mm_dd_yyyy",
    "as_of_date_in_form_yyyymmdd",
    "cftc_contract_market_code",
}

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

ALLOWED_MARKETS = {
    # Granos
    "CORN - CHICAGO BOARD OF TRADE",
    "SOYBEANS - CHICAGO BOARD OF TRADE",
    "SOYBEAN MEAL - CHICAGO BOARD OF TRADE",
    "SOYBEAN OIL - CHICAGO BOARD OF TRADE",
    "OATS - CHICAGO BOARD OF TRADE",
    # Wheat: nombre unificado pre-dic 2013, luego dividido en HRW/SRW
    "WHEAT - CHICAGO BOARD OF TRADE",
    "WHEAT-HRW - CHICAGO BOARD OF TRADE",
    "WHEAT-SRW - CHICAGO BOARD OF TRADE",
    # Metales
    "GOLD - COMMODITY EXCHANGE INC.",
    "SILVER - COMMODITY EXCHANGE INC.",
    "PALLADIUM - NEW YORK MERCANTILE EXCHANGE",
    "PLATINUM - NEW YORK MERCANTILE EXCHANGE",
    "COPPER- #1 - COMMODITY EXCHANGE INC.",
    "ALUMINUM MWP - COMMODITY EXCHANGE INC.",
    "STEEL-HRC - COMMODITY EXCHANGE INC.",
    "MICRO GOLD - COMMODITY EXCHANGE INC.",
    # Ganadería
    "LIVE CATTLE - CHICAGO MERCANTILE EXCHANGE",
    "FEEDER CATTLE - CHICAGO MERCANTILE EXCHANGE",
    "LEAN HOGS - CHICAGO MERCANTILE EXCHANGE",
    # Softs
    "COCOA - ICE FUTURES U.S.",
    "COFFEE C - ICE FUTURES U.S.",
    "COTTON NO. 2 - ICE FUTURES U.S.",
    "SUGAR NO. 11 - ICE FUTURES U.S.",
    "FRZN CONCENTRATED ORANGE JUICE - ICE FUTURES U.S.",
    # Energía
    "CRUDE OIL, LIGHT SWEET-WTI - ICE FUTURES EUROPE",
    "BRENT LAST DAY - NEW YORK MERCANTILE EXCHANGE",
    "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE",
    "GASOLINE RBOB - NEW YORK MERCANTILE EXCHANGE",
    "NY HARBOR ULSD - NEW YORK MERCANTILE EXCHANGE",
    "NAT GAS NYME - NEW YORK MERCANTILE EXCHANGE",
    "HENRY HUB - NEW YORK MERCANTILE EXCHANGE",
    "HENRY HUB BASIS - ICE FUTURES ENERGY DIV",
    "HENRY HUB INDEX - ICE FUTURES ENERGY DIV",
    # Madera
    "LUMBER - CHICAGO MERCANTILE EXCHANGE",
}

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed/index.htm",
}

def download(label, url):
    zip_path = os.path.join(DOWNLOAD_DIR, f"disagg_{label}.zip")
    extract_dir = os.path.join(DOWNLOAD_DIR, f"disagg_{label}")
    if os.path.exists(extract_dir):
        print(f"  [{label}] already extracted, skipping download")
        return extract_dir
    print(f"  [{label}] downloading {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, open(zip_path, "wb") as f:
            shutil.copyfileobj(resp, f)
    except Exception as e:
        print(f"  [{label}] SKIP (download failed): {e}")
        return None
    os.makedirs(extract_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(extract_dir)
    os.remove(zip_path)
    return extract_dir

all_frames = []
for label, url in URLS:
    extract_dir = download(label, url)
    if not extract_dir:
        continue
    files = sorted(
        glob.glob(os.path.join(extract_dir, "**", "*.xls"), recursive=True) +
        glob.glob(os.path.join(extract_dir, "**", "*.xlsx"), recursive=True)
    )
    for f in files:
        try:
            df = pd.read_excel(f, dtype=str, engine="xlrd")
            df.columns = df.columns.str.strip().str.lower()
            df = df.rename(columns=RENAME_MAP)
            all_frames.append(df)
            print(f"    Read {os.path.basename(f)}: {len(df)} rows")
        except Exception as e:
            print(f"    SKIP {f}: {e}")

if not all_frames:
    print("No data loaded.")
    sys.exit(1)

df = pd.concat(all_frames, ignore_index=True)
print(f"\nTotal rows loaded: {len(df)}")

df = df.drop_duplicates(subset=["market_and_exchange_names", "report_date_as_mm_dd_yyyy"])
print(f"After dedup: {len(df)}")

before = len(df)
df = df[df["market_and_exchange_names"].isin(ALLOWED_MARKETS)]
print(f"After whitelist: {len(df)} (dropped {before - len(df)} rows)")

present = [c for c in DB_COLS if c in df.columns]
missing = [c for c in DB_COLS if c not in df.columns]
if missing:
    print(f"WARNING: columns not in source (will be NULL): {missing}")
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
