# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A small data pipeline that downloads CFTC "Traders in Financial Futures" (TFF) Commitment of Traders reports and upserts them into a Supabase Postgres table (`cot_financials_raw`). There is no app code, build step, or test suite — just two Python scripts and a scheduled GitHub Actions workflow.

## Commands

Install dependencies (no requirements.txt — install directly):
```
pip install pandas xlrd supabase numpy
```

Both scripts require these environment variables:
```
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
```

**Weekly/incremental load** — reads all `.xls`/`.xlsx` files found recursively under a directory and upserts them:
```
python load_to_supabase.py <xls_dir>
```

**Full historical backfill** — downloads every CFTC TFF zip from 2006 to the current year, extracts, and upserts everything:
```
python load_historical.py
```
Note: `load_historical.py` has a hardcoded `DOWNLOAD_DIR` (`C:/Users/Jesús/AppData/Local/Temp/cftc_tff/historical`) — update this path for the local machine before running.

## Architecture

### Data flow
1. CFTC publishes zipped Excel files of TFF reports at `https://www.cftc.gov/files/dea/history/...` (bulk 2006-2016 zip, then one annual zip per year from 2017+).
2. The scripts download/extract these zips, read every `.xls`/`.xlsx` with `pandas.read_excel(..., dtype=str, engine="xlrd")`, lowercase/strip column headers, and concatenate all frames.
3. Rows are de-duplicated on `(market_and_exchange_names, report_date_as_mm_dd_yyyy)`.
4. Column type coercion is driven by two sets defined at the top of each script:
   - `SKIP_COLS` — identifier/text/date columns left untouched (handled separately).
   - `FLOAT_COLS` — percentage/concentration columns coerced to numeric float (Postgres `numeric`).
   - Everything else is coerced to `Int64` (nullable integer) for Postgres `bigint` columns.
5. `report_date_as_mm_dd_yyyy` and `as_of_date_in_form_yyyymmdd` (renamed from CFTC's `as_of_date_in_form_yymmdd`) are parsed into `YYYY-MM-DD` date strings.
6. Records are cleaned (NaN → `None`, numpy scalars → plain `int`) and upserted to Supabase in chunks of 500 via `on_conflict="market_and_exchange_names,report_date_as_mm_dd_yyyy"`.

### Two entry points, same target table
- `load_to_supabase.py` — generic loader over a directory of XLS files; this is what the GitHub Actions workflow runs against the freshly-downloaded current-year zip.
- `load_historical.py` — standalone backfill script; duplicates the same column-handling logic but additionally drives its own download/extract loop across all years (2006 bulk + annual 2017→current).

Keep `RENAME_MAP`, `SKIP_COLS`, and `FLOAT_COLS` in sync between the two scripts if either changes — they are independent copies, not shared imports.

### Supabase
- Project: "COT WEEKLY RAW" (`jvybembfdefoqnnjnuhq`, region eu-west-1).
- Target table: `public.cot_financials_raw` — `id bigint` PK, plus a `UNIQUE (market_and_exchange_names, report_date_as_mm_dd_yyyy)` constraint (`uq_fin_market_date`) that the upsert's `on_conflict` relies on.
- A second, unrelated table `public.cot_weekly_raw` also exists in the same project (different COT report format — disaggregated/legacy, with `prod_merc_*`/`swap_*`/`m_money_*` columns). Don't confuse it with `cot_financials_raw`.
- **Both tables currently have RLS disabled** — they are fully readable/writable by the anon key. Flag this before enabling anything that exposes the anon key client-side.

### GitHub Actions (`.github/workflows/cot_financials_weekly.yml`)
- Runs Fridays 17:00 UTC (CFTC releases ~16:30 UTC) plus `workflow_dispatch`.
- Downloads the current year's annual zip, unzips to `xls_current/`, and runs `python load_to_supabase.py xls_current`.
- Secrets required: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.
