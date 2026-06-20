# cot-financials
CFTC TFF weekly pipeline

## GitHub Actions — permisos del workflow

El ajuste de repo **Settings → Actions → General → Workflow permissions**
está en **"Read and write permissions"** (no el default de GitHub, que es
"Read repository contents permission"). Se cambió así para que el job de
`cot_financials_weekly.yml` pueda crear/actualizar/cerrar Issues
automáticamente (alertas de fallo de pipeline e instrumentos congelados —
ver el job `freshness-check` y el step "Report failure as GitHub Issue").

**Esto sube el techo de permisos del `GITHUB_TOKEN` por defecto para
TODO el repo, no solo para ese workflow.** Si agregas un workflow nuevo,
no asumas que el default sigue siendo "read" como protección automática —
declara explícitamente el bloque `permissions:` que necesites (ej.
`permissions: { contents: read }` si no necesita escribir nada), igual
que se hizo en `cot_financials_weekly.yml`, para no heredar más alcance
del que tu workflow realmente requiere.
