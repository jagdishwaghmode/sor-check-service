# SOR Check Service (skeleton)

Checks a patient against sex offender registries for the facility state and the patient's home
state, and returns one verdict: `CLEAR`, `FLAGGED`, `INCONCLUSIVE` or `ERROR`.

All registry data in this repo is synthetic. Plan: the private plan. Research and sources: `research.md`.

## Run

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env          # optional, set AUDIT_ENC_KEY (see file)
ENABLE_CHAOS=true .venv/bin/uvicorn sor.main:app_factory --factory --port 8000
```

Open http://localhost:8000 for the UI, http://localhost:8000/docs for the API.

```bash
.venv/bin/python -m pytest -q
```

## What works

| State | Source type | How it is wired |
|-------|-------------|-----------------|
| IA | live API (official guide shape) | `providers/iowa.py`. Mock by default. `IOWA_MODE=live` calls the real API (50 requests/hour). **Tested live on 2026-10-15**, see below |
| TX | CSV snapshot, **real and tested 2026-10-15** | generic provider. `LIVE_PROVIDERS=tx_csv` reads the real DPS export (108k people) flattened by `python -m sor.importers.tx_dps`. Demo CSV otherwise |
| FL | CSV snapshot | same provider, still mock. FDLE site unreachable from our network so far |
| DC | ArcGIS REST, **real and tested live 2026-10-15** | generic provider. `LIVE_PROVIDERS=dc_open_data` calls the real public layer (no account, CC BY 4.0). Merges home/work/school rows per person, searches aliases too |
| TN | ArcGIS REST | same provider, still mock. Real service unreachable from our network so far |
| all others | vendor | `default_chain` in config (mock vendor) |

Every source has a fallback. If a state's own source fails, the vendor is tried. If nothing
answers, the result is never `CLEAR`.

Adding a CSV or ArcGIS state is config only: one `providers:` block and one line under `states:`.

### Live mode (real registries)

```bash
LIVE_PROVIDERS=iowa_direct,dc_open_data,tx_csv ENABLE_CHAOS=true .venv/bin/uvicorn sor.main:app_factory --factory --port 8000
```

`LIVE_PROVIDERS` lists the sources that call the real registry. Any live source turns on real-data-only mode
(`REAL_DATA_ONLY`), which skips every mock source, so states with no real source come back unchecked instead of
with a fake CLEAR. Old switch `IOWA_MODE=live` still works for Iowa.

DC notes: its layer has no middle name, city or state columns, one person can have several rows (home, work,
school) that are merged, and it lists about 855 people. A search snippet says it covers only Class A and B
offenders (not verified).

Texas notes: the DPS export is free but needs a TxDPS public account (login), is refreshed twice a week and
arrives as a zip of database tables. Convert it with
`.venv/bin/python -m sor.importers.tx_dps path/to/export.zip data/tx_registry_real.csv` (about 1 second). The file
is git-ignored (real people). A file older than 14 days (`live_max_age_hours`) counts as unavailable, so a missed
refresh can never produce a stale CLEAR. Alternate birth dates in the export are not used yet.

### Iowa live mode

Real calls to iowasexoffender.gov, so use made-up names. How the adapter queries Iowa (found by
testing the real API):

- Iowa only returns aliases when a page has 25 or fewer results, and those pages are slow (up to
  about 8s). So it searches surname plus first initial, including nickname variants (Bob also
  asks for R), which keeps results to one small page. A query with more than 50 results is not
  checked completely, so the vendor fallback is used instead.
- Typical cost is 1 to 3 requests and 1 to 5 seconds per check. Iowa allows 50 requests per hour,
  and our local limit is 45. That is roughly 15 to 40 Iowa checks per hour, so volume needs a
  decision (see the private plan).

## Safety rules built in

- Could not check a state means not `CLEAR` (`INCONCLUSIVE` with `PARTIAL_COVERAGE`, or `ERROR`).
- `FLAGGED` and `INCONCLUSIVE` set `reviewStatus: PENDING`. The service never decides admission.
- A name that differs too much cannot be rescued by matching DOB and zip.
- Same name but a birth date that is far off (not a plausible typo: two of year, month, day must agree, or day and month swapped), with no same zip and no same middle name, is a different person. It is listed under "same name, different birth date" with reason `SAME_NAME_DIFFERENT_DOB` and does not need a human. A typo-sized difference, a same zip or a same middle name stays `INCONCLUSIVE`. Set `matching.dob_far_policy: review` in `config/states.yaml` for the strict version (always `INCONCLUSIVE`, many reviews for common names).
- Every check writes an audit row (insert-only, patient details encrypted). If that write fails, the API returns 503 instead of an unaudited verdict.
- A CSV snapshot older than `max_age_hours` counts as unavailable.

## Known gaps (on purpose, see the private plan)

- Mock field names for FL and TN are placeholders, not checked against the real sources yet (TX and DC are).
- Texas refresh is manual (download needs a login). A scheduled job needs a service account.
- Iowa searches by surname (starts-with), so a typo inside the surname is missed there. CSV states handle typos.
- Iowa quota counter and circuit breakers are in memory per process. Several copies need a shared store.
- No registrant cache, no hash-chained audit, no retention job, no real vendor, no auth beyond an optional API key.
- Apostrophes are dropped from names (O'BRIEN becomes OBRIEN). Right for Iowa (checked live), unverified for other sources.
- Iowa live checks take 1 to 5 seconds, above the 3 second target in the plan.
- `ENABLE_CHAOS` is a dev switch. Keep it off in real environments.
