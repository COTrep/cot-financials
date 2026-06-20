"""
Detecta instrumentos sin datos recientes en una tabla COT.

Clasifica cada instrumento por su cobertura histórica (filas / semanas
transcurridas desde su primer reporte):
  - cobertura >= 90%  -> "regular": se espera todas las semanas.
    Umbral de alerta: 2 semanas sin dato nuevo (tolera un reporte tardío).
  - cobertura <  90%  -> "intermitente": instrumento de nicho/baja
    participación que puede saltarse semanas legítimamente (ej. no
    alcanza el umbral de disclosure de CFTC esa semana).
    Umbral de alerta: su propio gap histórico más largo + 2 semanas de
    margen, con piso de 6 semanas para instrumentos sin historial
    suficiente para calcular un patrón propio.

Imprime a stdout un JSON con la lista de instrumentos que superan su
propio umbral. No modifica datos ni crea nada — solo diagnóstico.
"""
import os
import sys
import json
from datetime import datetime

import requests

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
TABLE_NAME = os.environ.get("TABLE_NAME", "cot_weekly_raw")

HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
PAGE_SIZE = 1000


def fetch_all_dates():
    rows = []
    offset = 0
    while True:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}",
            headers=HEADERS,
            params={
                "select": "market_and_exchange_names,report_date_as_mm_dd_yyyy",
                "order": "report_date_as_mm_dd_yyyy.asc",
                "offset": offset,
                "limit": PAGE_SIZE,
            },
            timeout=30,
        )
        resp.raise_for_status()
        chunk = resp.json()
        if not chunk:
            break
        rows.extend(chunk)
        offset += PAGE_SIZE
        if len(chunk) < PAGE_SIZE:
            break
    return rows


def parse(d):
    return datetime.strptime(d, "%Y-%m-%d").date()


rows = fetch_all_dates()
if not rows:
    print("[]")
    sys.exit(0)

by_market = {}
for r in rows:
    name = r["market_and_exchange_names"]
    d = parse(r["report_date_as_mm_dd_yyyy"])
    by_market.setdefault(name, []).append(d)

global_max = max(d for dates in by_market.values() for d in dates)

flagged = []
for name, dates in by_market.items():
    dates = sorted(set(dates))
    max_fecha = dates[-1]
    weeks_since_last = (global_max - max_fecha).days / 7

    if len(dates) >= 2:
        gaps = [(dates[i + 1] - dates[i]).days / 7 for i in range(len(dates) - 1)]
        internal_max_gap = max(gaps)
        weeks_span = (dates[-1] - dates[0]).days / 7
        coverage = len(dates) / weeks_span if weeks_span > 0 else 1.0
    else:
        internal_max_gap = 0.0
        coverage = 0.0  # sin historial suficiente -> tratar con cautela (umbral piso)

    if coverage >= 0.9:
        tier = "regular"
        threshold = 2.0
    else:
        tier = "intermitente"
        threshold = max(internal_max_gap + 2.0, 6.0)

    if weeks_since_last > threshold:
        flagged.append({
            "market": name,
            "max_fecha": max_fecha.isoformat(),
            "weeks_since_last": round(weeks_since_last, 1),
            "threshold_weeks": round(threshold, 1),
            "tier": tier,
            "filas": len(dates),
        })

print(json.dumps(flagged))
