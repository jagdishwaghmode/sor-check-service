# Paid Search Provider Comparison (R3)

**Date:** 2026-10-30
**Context:** FL, TN and MO state sites remain unreachable. We need a fallback vendor
that can cover those states (and any other state without a direct adapter).

Two providers from the R1 long list survived filtering: Offenders.io and Nannostomus.
RegistryRadar was dropped (no name search) and CrimeoMeter was dropped (no DOB parameter).

---

## Side-by-side

| | Offenders.io | Nannostomus |
|---|---|---|
| Search endpoint | `GET /sexoffender` (query params) | `POST /v1/search` (JSON body) |
| Required fields | firstName, lastName, key | first_name, last_name |
| Optional fields | zipcode, fuzzy | dob, state |
| Auth method | API key in query string | API key in X-Api-Key header |
| Response: DOB | Full date (YYYY-MM-DD) | Birth year only |
| Response: match score | None | None |
| Coverage claim | 184 jurisdictions | 50 states + territories + tribes |
| Refresh frequency | Continuous (vendor claim) | Monthly |
| Free tier | 50 req/month | 100 req/month |
| Volume cost | Pay-as-you-go, fuzzy +$0.02 | $0.15 → $0.01/req |
| BAA for PHI | Enterprise tier only | Not offered |
| FCRA | Not a consumer report | Not a consumer report |

---

## Integration effort

Both fit our existing adapter pattern:

- Offenders.io → sync GET, same shape as `http_json` in `providers/base.py`
- Nannostomus → sync POST, also handled by `http_json`

Adding either is a new `providers:` block in `config/states.yaml` and a thin adapter mapping
the response into `RegistryRecord`. No engine or matcher changes needed.

---

## Scoring impact

Offenders.io returns a full birth date, so our three-component matcher (name + DOB + location)
works unchanged.

Nannostomus returns only birth year, which means `dob_score` can only distinguish same-year
from different-year (no month/day). This weakens the DOB penalty for same-name, different-DOB
cases and would increase INCONCLUSIVE verdicts for common names.

---

## Recommendation

1. Contact Offenders.io for Enterprise pricing and a BAA draft
2. Keep Nannostomus as a secondary option if Offenders.io terms are not workable
3. Do not send real patient data to either vendor until a BAA is signed
4. Once a key is available, wire it as a second entry in `default_chain` after `vendor_mock`

---

## Open items

| # | Item | Owner | Status |
|---|---|---|---|
| 1 | Request Offenders.io Enterprise quote | PM | Pending |
| 2 | Check Nannostomus for BAA willingness | PM | Pending |
| 3 | Legal review of both vendors' data terms | Legal | Not started |
| 4 | Wire winning vendor into config once key received | Dev | Blocked on #1/#2 |
