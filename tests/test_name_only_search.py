"""QA coverage: name-only search behaviour on Iowa and Texas data.

Validates that when only a name is supplied (no DOB, no ZIP) the providers
and matching layer behave consistently:
- A strong name match without a DOB caps at INCONCLUSIVE (never FLAGGED):
  score_record() clamps the score below flag_at and sets capped=True.
- NAME_ONLY needs a known-wrong DOB (d == 0.0), so a no-DOB patient can
  never get it. The asserts use == "INCONCLUSIVE" and s.capped.
- A weak / partial name with no DOB is dropped (NONE).

All tests use the mock registry, no network calls are made.
"""

import asyncio
from datetime import date

from sor.matching import MatchConfig, score_record, score_records
from sor.models import NormalizedPatient
from sor.providers.base import build_record
from sor.providers.iowa import IowaProvider

CFG = MatchConfig()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _patient(first, last, *, middle="", dob=None, state="IA", zip_code=""):
    """Build a NormalizedPatient; dob=None simulates a name-only lookup."""
    return NormalizedPatient(first, middle, last, "", dob, "", "", state, zip_code)


def _record(first, last, *, middle="", dob=date(1980, 5, 15), aliases=None,
            zip_code="50309", registry_id="IA-TEST-1", source_state="IA"):
    return build_record(
        registry_id=registry_id,
        first=first, middle=middle, last=last, suffix="",
        aliases=aliases or [],
        dob=dob, city="DES MOINES", state="IA",
        zip_code=zip_code,
        source_state=source_state,
        source_provider="t",
    )


def run(coro):
    return asyncio.run(coro)


# ===========================================================================
# Iowa – name-only matching rules
# ===========================================================================

class TestIowaNameOnlyMatching:
    """Verify Iowa scoring when the patient has no DOB (name-only lookup)."""

    def test_exact_name_no_dob_is_inconclusive_and_capped(self):
        """Exact first+last match without a DOB must be INCONCLUSIVE and capped.

        score_record() clamps the score below flag_at when d is None, setting
        capped=True.  NAME_ONLY is impossible here (requires d == 0.0).
        """
        patient = _patient("JOHN", "DOE")
        rec = _record("JOHN", "DOE")
        s = score_record(patient, rec, CFG)
        assert s.band == "INCONCLUSIVE", (
            f"Expected INCONCLUSIVE for name-only hit, got {s.band}"
        )
        assert s.capped, "score_record must set capped=True when DOB is absent"
        assert s.band != "FLAGGED", "Name-only hit must never be FLAGGED"

    def test_partial_name_no_dob_is_not_a_match(self):
        """A weak fuzzy name and no DOB should produce NONE — not surfaced."""
        patient = _patient("JON", "DOE")          # slight misspelling, no DOB
        rec = _record("JONATHAN", "DOERINGER")    # clearly different person
        s = score_record(patient, rec, CFG)
        assert s.band == "NONE", (
            f"Weak name without DOB should be dropped (NONE), got {s.band}"
        )

    def test_nickname_match_no_dob_is_inconclusive_and_capped(self):
        """Nickname resolution (BOB→ROBERT) with no DOB should be INCONCLUSIVE."""
        patient = _patient("BOB", "SMITH")
        rec = _record("ROBERT", "SMITH")
        s = score_record(patient, rec, CFG)
        assert s.band == "INCONCLUSIVE", (
            f"Nickname match without DOB should be INCONCLUSIVE, got {s.band}"
        )
        assert s.capped, "Nickname hit without DOB must be capped"

    def test_score_records_drops_noise_in_name_only_mode(self):
        """score_records should suppress low-confidence records in a name-only pass.

        score_records returns ScoredRecord objects — registry_id is on .record.
        Also confirms that the matching hits (IA-1, IA-3) are kept.
        """
        patient = _patient("JOHN", "DOE")
        records = [
            _record("JOHN", "DOE", registry_id="IA-1"),
            _record("ALICE", "ZZZUNRELATED", registry_id="IA-2"),   # must be dropped
            _record("JOHNNY", "DOE", registry_id="IA-3"),            # similar name, kept
        ]
        out = score_records(patient, records, CFG)
        ids = [r.record.registry_id for r in out]
        assert "IA-2" not in ids, "Completely different name must be dropped by score_records"
        assert "IA-1" in ids, "Exact name match (IA-1) must be kept"
        assert "IA-3" in ids, "Similar name (IA-3) must be kept as INCONCLUSIVE"

    def test_hyphenated_last_name_no_dob_scores_correctly(self):
        """Garcia-Lopez vs GARCIA LOPEZ should still score high on name alone."""
        patient = _patient("MARIA", "GARCIA LOPEZ")
        rec = _record("MARIA", "GARCIA-LOPEZ")
        s = score_record(patient, rec, CFG)
        assert s.name >= 0.95, (
            f"Hyphen/space normalised name should score ≥0.95, got {s.name:.3f}"
        )


# ===========================================================================
# Iowa – provider-level name-only search
# ===========================================================================

class TestIowaProviderNameOnlySearch:
    """End-to-end mock provider search with no DOB provided."""

    def test_provider_returns_records_for_known_name(self):
        """IowaProvider.search should return ≥1 records for a known name (no DOB).

        The mock registry has two DOE entries; at least one must come back.
        """
        p = IowaProvider("t")
        patient = _patient("JOHN", "DOE", state="IA")
        recs = run(p.search(patient, "IA"))
        assert len(recs) >= 1, (
            f"Expected at least one DOE record from Iowa mock, got {len(recs)}"
        )


# ===========================================================================
# Texas – name-only matching rules
# ===========================================================================

class TestTexasNameOnlyMatching:
    """Same scoring rules applied to Texas registry records."""

    def test_exact_name_no_dob_tx_is_inconclusive_and_capped(self):
        """Texas record: exact name, no DOB → INCONCLUSIVE and capped."""
        patient = _patient("ROBERT", "ZZTESTER", state="TX")
        rec = _record("ROBERT", "ZZTESTER", source_state="TX",
                      registry_id="TX-00000001", zip_code="75001")
        s = score_record(patient, rec, CFG)
        assert s.band == "INCONCLUSIVE", (
            f"TX name-only hit must be INCONCLUSIVE, got {s.band}"
        )
        assert s.capped, "TX name-only hit must be capped (no DOB evidence)"

    def test_alias_match_no_dob_tx_is_inconclusive_with_alias_reported(self):
        """Match via alias (BOB ZZTESTER) with no DOB → INCONCLUSIVE, alias reported."""
        patient = _patient("BOB", "ZZTESTER", state="TX")
        rec = _record("ROBERT", "ZZTESTER", aliases=["BOB ZZTESTER"],
                      source_state="TX", registry_id="TX-00000001")
        s = score_record(patient, rec, CFG)
        assert s.band == "INCONCLUSIVE", (
            f"Alias match without DOB must be INCONCLUSIVE, got {s.band}"
        )
        assert s.matched_alias == "BOB ZZTESTER", (
            f"Expected matched_alias='BOB ZZTESTER', got {s.matched_alias!r}"
        )

    def test_common_name_many_tx_records_all_stay_below_flagged(self):
        """Multiple TX records for a common name (no DOB) — none should be FLAGGED."""
        patient = _patient("JOHN", "SMITH", state="TX")
        tx_records = [
            _record("JOHN", "SMITH", dob=date(1970 + i, 1, 1),
                    registry_id=f"TX-{i:05d}", source_state="TX")
            for i in range(5)
        ]
        scored = score_records(patient, tx_records, CFG)
        flagged = [r for r in scored if r.band == "FLAGGED"]
        assert not flagged, (
            f"Common name without DOB produced FLAGGED hits: "
            f"{[r.record.registry_id for r in flagged]}"
        )
