from datetime import date

import pytest

from sor.aggregate import Attempt, StateOutcome, aggregate
from sor.breaker import CircuitBreaker
from sor.matching import MatchConfig, ScoredRecord, score_record, score_records
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
