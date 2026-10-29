# QA Notes – Iowa & Texas Name-Only Search

**Branch:** `qa/name-only-search-iowa-texas`  
**Dates:** 2026-10-28 → 2026-10-29  
**Role:** QA Testing  
**States checked:** Iowa (IA), Texas (TX)

---

## Summary

Ran manual and automated checks on the name-only search path (patient supplied
without a date of birth) across the Iowa live adapter and the Texas DPS CSV
snapshot provider.  Results broadly matched expectations.  Two gaps were found
and shared with the team.

---

## Day 1 – 2026-10-28

### What was tested
- Name-only queries for common surnames (DOE, SMITH) on both Iowa and Texas data.
- Verified that the matching layer caps results at **INCONCLUSIVE** (never FLAGGED)
  when no DOB is present.
- Checked nickname resolution (BOB → ROBERT) without DOB.
- Confirmed hyphenated surname normalisation (GARCIA LOPEZ ↔ GARCIA-LOPEZ) works
  in name-only mode.

### Results

| Scenario | Expected | Actual | Pass? |
|---|---|---|---|
| Exact name, no DOB | INCONCLUSIVE | INCONCLUSIVE | ✅ |
| Nickname, no DOB | INCONCLUSIVE | INCONCLUSIVE | ✅ |
| Weak partial name, no DOB | NONE (dropped) | NONE | ✅ |
| Hyphenated surname | name score ≥ 0.95 | 0.98 | ✅ |
| Common name (JOHN SMITH, TX) | All < FLAGGED | All INCONCLUSIVE | ✅ |

### Gap 1 – Console output omits state when zero records returned

When Iowa returned zero records for a query the console printed nothing for
the IA row, making it impossible to tell at a glance whether the state was
checked and found clear, or simply not reached.  The aggregate layer correctly
records `NO_MATCH`, but the output rendering does not surface it.

**Shared with team:** 2026-10-28 via Slack `#sor-dev`.  
**Expected fix:** Add a "✓ checked – no records" line per state in the console output.  
**Tests:** none yet, no automated reproducer for this one.

---

## Day 2 – 2026-10-29

### What was tested
- Texas DPS CSV file expiry logic: age threshold, boundary behaviour, override
  path for QA reruns on old exports.
- CSV column schema consistency between `convert()` output and provider config.
- ALIASES column content (primary name deduplication).
- RISK_LEVEL mapping: risk code N and a blank risk code.

### Results

| Scenario | Expected | Actual | Pass? |
|---|---|---|---|
| Export > 14 days old | ProviderUnavailable | ProviderUnavailable | ✅ |
| max_age override for QA | Search proceeds | Search proceeds | ✅ |
| Risk code N | "Not Reported" | "Not Reported" | ✅ |
| Blank risk code | "Not Reported" | empty string | ❌ (xfail in tests) |
| Primary name in ALIASES | Not present | Not present | ✅ |
| CSV columns match config | All present | All present | ✅ |

### Gap 2 – Texas file expiry error does not name the file

When the TX CSV is rejected due to age, `ProviderUnavailable` says
`snapshot is Nh old, limit is Mh`, so age and limit are there.  It does not say:
- Which file was rejected.
- Which provider triggered the error.

This made manual diagnosis during QA slow, had to add `print()` statements to
locate the file.

**Shared with team:** 2026-10-29 via Slack `#sor-dev`.  
**Suggested fix:** Add the file name and provider name to the `ProviderUnavailable`
message so it is actionable without source inspection.

### Gap 3 – Blank risk code stays blank

`convert()` maps risk code N to "Not Reported", but a blank risk code comes out
as an empty cell, which showed up as a blank risk level in the console.  Covered by
an xfail test, remove the marker once the importer is fixed.

**Suggested fix:** Map a blank risk code to "Not Reported" in `tx_dps.py`.

---

## Open Items

| # | Description | Owner | Status |
|---|---|---|---|
| 1 | Console: show checked-but-empty state rows | Dev | Pending |
| 2 | TX expiry error: add file and provider name to message | Dev | Pending |
| 3 | TX importer: map blank risk code to "Not Reported" | Dev | Pending |

---

## Test Files Added

| File | Day | Description |
|---|---|---|
| `tests/test_name_only_search.py` | Day 1 | Name-only scoring for IA + TX |
| `tests/test_tx_expiry_gaps.py` | Day 2 | TX expiry boundary + importer output |
