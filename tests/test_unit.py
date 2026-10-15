from datetime import date

import pytest

from sor.aggregate import Attempt, StateOutcome, aggregate
from sor.breaker import CircuitBreaker
from sor.matching import MatchConfig, ScoredRecord, dob_is_near, score_record, score_records, split_candidates
from sor.models import AttemptStatus, Coverage, NormalizedPatient, OverallStatus, ReviewStatus
from sor.providers.base import build_record
from sor.textutil import clean, epoch_ms_to_date, parse_date, parse_full_name, split_suffix

CFG = MatchConfig()


def patient(first="JOHN", last="DOE", dob=date(1980, 5, 15), middle="", suffix="", city="DES MOINES",
            zip_code="50309", state="IA"):
    return NormalizedPatient(first, middle, last, suffix, dob, "", city, state, zip_code)


def record(first="JOHN", last="DOE", dob=date(1980, 5, 15), middle="", aliases=None, zip_code="50309",
           city="DES MOINES", suffix=""):
    return build_record(registry_id="X-1", first=first, middle=middle, last=last, suffix=suffix,
                        aliases=aliases, dob=dob, city=city, state="IA", zip_code=zip_code,
                        source_state="IA", source_provider="t")


# ---------------------------------------------------------------- text


def test_clean_strips_special_chars_and_uppercases():
    assert clean("  o'Brien-Smith, jr. ") == "OBRIEN-SMITH JR"
    assert clean("José  Núñez") == "JOSE NUNEZ"
    assert clean(None) == ""


@pytest.mark.parametrize("raw", ["1980-05-15", "05/15/1980", "1980/05/15", "19800515", "1980-05-15T00:00:00Z"])
def test_parse_date_formats(raw):
    assert parse_date(raw) == date(1980, 5, 15)


def test_parse_date_rejects_garbage():
    assert parse_date("not a date") is None
    assert parse_date("13/45/1980") is None


def test_epoch_ms_handles_negative_pre_1970():
    assert epoch_ms_to_date(_ms(date(1965, 12, 25))) == date(1965, 12, 25)


def _ms(d):
    from datetime import datetime, timezone
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def test_suffix_and_full_name_parsing():
    assert split_suffix("VALDEZ JR") == ("VALDEZ", "JR")
    assert parse_full_name("robert j doe") == ("ROBERT", "J", "DOE")
    assert parse_full_name("BOB SMITH") == ("BOB", "", "SMITH")


# ---------------------------------------------------------------- matching


def test_exact_match_is_flagged():
    s = score_record(patient(), record(), CFG)
    assert s.band == "FLAGGED" and s.score >= 0.95


def test_nickname_with_dob_is_flagged():
    s = score_record(patient(first="BOB", last="SMITH", dob=date(1975, 3, 2)),
                     record(first="ROBERT", last="SMITH", dob=date(1975, 3, 2)), CFG)
    assert s.band == "FLAGGED"


def test_alias_is_used():
    s = score_record(patient(first="BOB", last="SMITH", dob=date(1975, 3, 2)),
                     record(first="ROBERT", last="SMITH", dob=date(1975, 3, 2), aliases=["BOB SMITH"]), CFG)
    assert s.name == 1.0


def test_same_name_different_dob_is_inconclusive_not_clear():
    s = score_record(patient(dob=date(1990, 1, 1)), record(), CFG)
    assert s.band == "INCONCLUSIVE"


def test_different_name_same_dob_and_zip_is_not_a_match():
    s = score_record(patient(first="ALICE", last="WONDERLAND"), record(), CFG)
    assert s.band == "NONE"  # the name floor stops DOB+zip from rescuing a different name


def test_different_dob_keeps_only_near_exact_names():
    same = score_record(patient(dob=date(1990, 1, 1)), record(), CFG)  # same name, other DOB
    similar = score_record(patient(first="JOHN", last="DOE", dob=date(1990, 1, 1)),
                           record(first="JOHN", middle="MICHAEL", last="DOW"), CFG)  # similar, not same
    assert same.band == "INCONCLUSIVE" and similar.band == "NONE"


# a far-off birth date, a different zip and no middle name in common: same name, different person
FAR = dict(dob=date(1990, 1, 1), zip_code="99999", city="ELSEWHERE")


def test_far_dob_same_name_is_name_only_not_a_match():
    s = score_record(patient(**FAR), record(), CFG)
    assert s.band == "NAME_ONLY" and s.score < CFG.clear_below


def test_far_dob_with_same_zip_or_middle_name_stays_inconclusive():
    same_zip = score_record(patient(dob=date(1990, 1, 1), zip_code="50309"), record(), CFG)
    same_middle = score_record(patient(middle="MICHAEL", **FAR), record(middle="MICHAEL"), CFG)
    assert same_zip.band == "INCONCLUSIVE" and same_middle.band == "INCONCLUSIVE"


def test_near_dob_stays_inconclusive():
    for dob in (date(1980, 5, 16), date(1980, 6, 15), date(1981, 5, 15), date(1980, 5, 12)):  # one part off
        assert score_record(patient(dob=dob, zip_code="99999"), record(), CFG).band == "INCONCLUSIVE", dob
    swapped = score_record(patient(dob=date(1980, 12, 5), zip_code="99999"), record(dob=date(1980, 5, 12)), CFG)
    assert swapped.band == "INCONCLUSIVE"


def test_alternate_birth_date_counts_as_a_match():
    rec = record()
    rec.alt_dobs = [date(1990, 1, 1)]  # the person also used this birth date
    s = score_record(patient(**FAR), rec, CFG)
    assert s.band == "FLAGGED" and s.matched_alt_dob and s.dob == 1.0
    primary = score_record(patient(), rec, CFG)
    assert primary.band == "FLAGGED" and not primary.matched_alt_dob


def test_alternate_birth_dates_also_count_for_typo_tolerance():
    rec = record()
    rec.alt_dobs = [date(1990, 1, 1)]
    near_alt = score_record(patient(dob=date(1990, 1, 2), zip_code="99999"), rec, CFG)
    far_from_all = score_record(patient(dob=date(1975, 9, 9), zip_code="99999"), rec, CFG)
    assert near_alt.band == "INCONCLUSIVE" and far_from_all.band == "NAME_ONLY"


def test_dob_is_near_rules():
    base = date(1980, 5, 15)
    assert dob_is_near(base, date(1980, 5, 1)) and dob_is_near(base, date(1971, 5, 15))
    assert not dob_is_near(base, date(1990, 1, 1)) and not dob_is_near(base, date(1981, 6, 15 - 1))


def test_review_policy_restores_strict_behavior():
    strict = MatchConfig(dob_far_policy="review")
    assert score_record(patient(**FAR), record(), strict).band == "INCONCLUSIVE"


def test_split_candidates_separates_notes_from_matches():
    recs = [record(), record(first="ALICE", last="ZZZ"), record(dob=date(2000, 2, 2))]
    matches, notes = split_candidates(patient(dob=date(1980, 5, 15), zip_code="99999"), recs, CFG)
    assert len(matches) == 1 and matches[0].band == "FLAGGED" and len(notes) == 1


def test_missing_record_dob_caps_at_inconclusive():
    s = score_record(patient(), record(dob=None), CFG)
    assert s.band == "INCONCLUSIVE" and s.capped


def test_alias_match_reports_which_alias():
    p = patient(first="BOB", last="SMITH", dob=date(1975, 3, 2))
    by_alias = score_record(p, record(first="ALONZO", last="JONES", dob=date(1975, 3, 2), aliases=["BOB SMITH"]), CFG)
    by_name = score_record(p, record(first="ROBERT", last="SMITH", dob=date(1975, 3, 2), aliases=["BOB SMITH"]), CFG)
    assert by_alias.matched_alias == "BOB SMITH" and by_alias.band == "FLAGGED"
    assert by_name.matched_alias is None or by_name.matched_alias == "BOB SMITH"  # a tie may report either


def test_hyphen_and_space_surname_match():
    s = score_record(patient(first="MARIA", last="GARCIA LOPEZ", dob=date(1990, 11, 23)),
                     record(first="MARIA", last="GARCIA-LOPEZ", dob=date(1990, 11, 23)), CFG)
    assert s.name >= 0.99


def test_score_records_drops_noise_and_sorts():
    recs = [record(first="ALICE", last="ZZZ"), record(), record(dob=date(1990, 1, 1))]
    out = score_records(patient(), recs, CFG)
    assert len(out) == 2 and out[0].score >= out[1].score


# ---------------------------------------------------------------- breaker


def test_breaker_opens_then_half_opens_then_closes():
    t = [0.0]
    b = CircuitBreaker(failure_threshold=2, cooldown_s=10, clock=lambda: t[0])
    assert b.allow()
    b.record_failure(); b.record_failure()
    assert b.state == "OPEN" and not b.allow()
    t[0] = 11
    assert b.state == "HALF_OPEN"
    assert b.allow() and not b.allow()  # exactly one probe
    b.record_success()
    assert b.state == "CLOSED" and b.allow()


def test_breaker_failed_probe_reopens():
    t = [0.0]
    b = CircuitBreaker(failure_threshold=1, cooldown_s=5, clock=lambda: t[0])
    b.record_failure()
    t[0] = 6
    assert b.allow()
    b.record_failure()
    assert b.state == "OPEN"


# ---------------------------------------------------------------- aggregate (safety rules)


def _scored(band, score=0.9, capped=False):
    return ScoredRecord(record(), 1.0, 1.0, 1.0, score, band, capped)


def _outcome(state, checked=True, scored=None, attempts=None):
    return StateOutcome(state, checked, "p" if checked else None,
                        attempts or [Attempt("p", "live", AttemptStatus.NO_MATCH, 1)],
                        scored or [], 1)


def test_all_checked_no_records_is_clear():
    v = aggregate([_outcome("IA")])
    assert v.status == OverallStatus.CLEAR and v.coverage == Coverage.COMPLETE


def test_name_only_notes_do_not_block_clear():
    o = _outcome("IA")
    o.name_only = [_scored("NAME_ONLY", 0.39)]
    v = aggregate([o])
    assert v.status == OverallStatus.CLEAR and "SAME_NAME_DIFFERENT_DOB" in v.reasons
    assert v.review == ReviewStatus.NOT_REQUIRED


def test_flag_wins_over_everything():
    v = aggregate([_outcome("IA", scored=[_scored("FLAGGED")]), _outcome("TX", checked=False, attempts=[])])
    assert v.status == OverallStatus.FLAGGED and v.coverage == Coverage.PARTIAL
    assert v.review == ReviewStatus.PENDING


def test_partial_coverage_is_never_clear():
    v = aggregate([_outcome("IA"), _outcome("TX", checked=False, attempts=[])])
    assert v.status == OverallStatus.INCONCLUSIVE and "PARTIAL_COVERAGE" in v.reasons


def test_all_unchecked_is_error():
    v = aggregate([_outcome("IA", checked=False, attempts=[])])
    assert v.status == OverallStatus.ERROR and v.coverage == Coverage.NONE


def test_fallback_is_reported():
    fail = Attempt("a", "live", AttemptStatus.TIMEOUT, 5)
    ok = Attempt("b", "vendor", AttemptStatus.NO_MATCH, 5)
    v = aggregate([_outcome("IA", attempts=[fail, ok])])
    assert v.status == OverallStatus.CLEAR and "PRIMARY_FAILED_FALLBACK_USED" in v.reasons
