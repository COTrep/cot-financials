# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A data pipeline that downloads CFTC Commitment of Traders reports and upserts them into two separate Supabase tables:

| Report type | CFTC URL pattern | Script | Supabase table |
|---|---|---|---|
| TFF (Traders in Financial Futures) | `fut_fin_xls_{YEAR}.zip` | `load_to_supabase.py` | `cot_financials_raw` |
| Disaggregated COT (commodities) | `fut_disagg_xls_{YEAR}.zip` | `load_commodities.py` | `cot_weekly_raw` |

There is no app code, build step, or test suite — just Python scripts and two scheduled GitHub Actions workflows.

## Commands

Install dependencies (no requirements.txt — install directly):
```
pip install pandas xlrd supabase numpy requests
```

Both scripts require these environment variables:
```
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY   ← service-role JWT, required; anon key is blocked by RLS
```

**Weekly financials load** — TFF data → `cot_financials_raw`:
```
python load_to_supabase.py <xls_dir>
```

**Weekly commodities load** — disaggregated COT data → `cot_weekly_raw`:
```
python load_commodities.py <xls_dir>
```

**Full historical backfill (financials)** — downloads every CFTC TFF zip 2006→current:
```
python load_historical.py
```
Note: `load_historical.py` has a hardcoded `DOWNLOAD_DIR` — update before running locally.

**Gap backfill (one-time, already executed)** — upserted 12,377 records from gap_records.json:
```
python exec_gap.py   # reads %TEMP%\cftc_tff_gap\gap_records.json
```

## Architecture

### Data flow (both pipelines follow the same pattern)
1. CFTC publishes zipped Excel files at `https://www.cftc.gov/files/dea/history/...`.
2. Scripts download/extract zips, read every `.xls`/`.xlsx` with `pandas.read_excel(..., dtype=str, engine="xlrd")`, lowercase/strip column headers.
3. Rows are de-duplicated on `(market_and_exchange_names, report_date_as_mm_dd_yyyy)`.
4. Column type coercion:
   - `SKIP_COLS` — identifier/text/date columns left untouched.
   - `FLOAT_COLS` — percentage/concentration columns coerced to float (financials only).
   - Everything else → `Int64` (nullable integer) → Postgres `bigint`.
5. `report_date_as_mm_dd_yyyy` and `as_of_date_in_form_yyyymmdd` (renamed from CFTC's `as_of_date_in_form_yymmdd`) parsed into `YYYY-MM-DD`.
6. Upserted to Supabase in chunks of 500 via `on_conflict="market_and_exchange_names,report_date_as_mm_dd_yyyy"`.

### Script inventory

- `load_to_supabase.py` — weekly financials loader (TFF → `cot_financials_raw`). Used by `cot_financials_weekly.yml`.
- `load_commodities.py` — weekly commodities loader (disaggregated → `cot_weekly_raw`). Used by `cot_commodities_weekly.yml`. Selects only the 28 columns that exist in `cot_weekly_raw` (no `FLOAT_COLS` — all numeric cols are bigint).
- `load_historical.py` — full historical backfill for financials (2006→current). Run once manually.
- `load_disagg_historical.py` — full historical backfill for commodities (2006→current, disaggregated). Needed to load pre-2013 `WHEAT - CHICAGO BOARD OF TRADE` data. Downloads to `%TEMP%/cftc_disagg_historical/`.
- `exec_gap.py` — one-time gap filler that upserts `gap_records.json` via Supabase REST API (uses `urllib.request`, no supabase-py). Already executed; kept for reference.
- `check_freshness.py` — detects instruments with no recent updates; outputs JSON; used by both freshness-check jobs.

Keep `RENAME_MAP` and `SKIP_COLS` in sync between `load_to_supabase.py` and `load_commodities.py` if either changes.

### Supabase
- Project: `jvybembfdefoqnnjnuhq` (region eu-west-1).
- **RLS is ENABLED on both tables** — the anon/publishable key is blocked. Always use `SUPABASE_SERVICE_ROLE_KEY`.
- `public.cot_financials_raw` — TFF financial futures data. Unique constraint `uq_fin_market_date` on `(market_and_exchange_names, report_date_as_mm_dd_yyyy)`.
- `public.cot_weekly_raw` — disaggregated COT commodity data (28 cols: `prod_merc_*`, `swap_*`, `m_money_*`, `traders_tot_*`). Unique constraint `uq_cot_market_date` on `(market_and_exchange_names, report_date_as_mm_dd_yyyy)`.

#### Known instrument history (audited)

**Financials (`cot_financials_raw`) — CFTC renamed ~10 instruments in Feb 2022:**
Old names (frozen at 2022-02-01) and new names (starting 2022-02-08) both exist in the table. The 4-year gap 2022-02-08 → 2026-01-06 was backfilled via `exec_gap.py` (12,377 records, completed). Affected instruments: UST 10Y, UST 2Y, UST 5Y, T-Bonds, Fed Funds, GBP, NZD, USD Index, E-mini S&P 500, Nasdaq Mini.

**Commodities (`cot_weekly_raw`) — complete, no gaps:**
Main commodities have full coverage from 2006. WHEAT history:
- Pre-dec 2013: `WHEAT - CHICAGO BOARD OF TRADE` (single contract, whitelisted)
- Post-dec 2013: `WHEAT-HRW - CHICAGO BOARD OF TRADE` + `WHEAT-SRW - CHICAGO BOARD OF TRADE`
All three names are whitelisted; backfill via `load_disagg_historical.py` populates pre-2013 wheat data.

### GitHub Actions workflows

**`.github/workflows/cot_financials_weekly.yml`** — Fridays 17:00 UTC
- Downloads `fut_fin_xls_{YEAR}.zip` → `xls_current/`
- Runs `python load_to_supabase.py xls_current`
- `freshness-check` job detects frozen instruments, raises GitHub Issues (label `instrumento-congelado`)

**`.github/workflows/cot_commodities_weekly.yml`** — Fridays 17:30 UTC
- Downloads `fut_disagg_xls_{YEAR}.zip` → `xls_commodities/`
- Runs `python load_commodities.py xls_commodities`
- `freshness-check` job raises GitHub Issues (label `instrumento-congelado-commodities`)

Both workflows use the same GitHub Secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.

### Security rules (mandatory)
- **Never hardcode tokens or keys in any script.** Always read from environment variables.
- The `sb_publishable_*` key format is NOT accepted by supabase-py — it raises `SupabaseException: Invalid API key`. Use the service-role JWT only.
- Direct Supabase REST API calls (`urllib.request`) also require the service-role JWT plus `apikey` + `Authorization` headers and `?on_conflict=` query param alongside `Prefer: resolution=merge-duplicates`.
