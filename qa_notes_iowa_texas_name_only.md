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

---

## Day 2 – 2026-10-29

### What was tested
- Texas DPS CSV file expiry logic: age threshold, boundary behaviour, override
  path for QA reruns on old exports.
- CSV column schema consistency between `convert()` output and provider config.
- ALIASES column content (primary name deduplication).
- RISK_LEVEL normalisation for records with no SOR entry.

### Results

| Scenario | Expected | Actual | Pass? |
|---|---|---|---|
| Export > 14 days old | ProviderUnavailable | ProviderUnavailable | ✅ |
| max_age override for QA | Search proceeds | Search proceeds | ✅ |
| Empty RISK_LEVEL cell | "Not Reported" | "Not Reported" | ✅ |
| Primary name in ALIASES | Not present | Not present | ✅ |
| CSV columns match config | All present | All present | ✅ |

### Gap 2 – Texas file expiry error gives no staleness detail

When the TX CSV is rejected due to age, `ProviderUnavailable` is raised with a
generic message.  There is no indication of:
- The file path that was rejected.
- How old the file actually is vs. the threshold.
- Which provider triggered the error.

This made manual diagnosis during QA slow — had to add `print()` statements to
locate the file.

**Shared with team:** 2026-10-29 via Slack `#sor-dev`.  
**Suggested fix:** Include `file_age_hours` and `max_age_hours` in the
`ProviderUnavailable` message so it is actionable without source inspection.

---

## Open Items

| # | Description | Owner | Status |
|---|---|---|---|
| 1 | Console: show checked-but-empty state rows | Dev | Pending |
| 2 | TX expiry error: include age detail in message | Dev | Pending |

---

## Test Files Added

| File | Day | Description |
|---|---|---|
| `tests/test_name_only_search.py` | Day 1 | Name-only scoring for IA + TX |
| `tests/test_tx_expiry_gaps.py` | Day 2 | TX expiry boundary + console gaps |
