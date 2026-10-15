"""ArcGIS adapter rules learned from the real DC service (mock mirrors its shape)."""

import asyncio
from datetime import date

import pytest

from sor.config import Settings
from sor.models import NormalizedPatient
from sor.providers.arcgis import ArcGisProvider
from sor.providers.base import ProviderBadResponse

DC_FIELDS = dict(id="SEXOFFENDERCODE", first="FIRSTNAME", last="LASTNAME", dob="BIRTHDATE",
                 street="BLOCKNAME", zip="ZIPCODE", tier="MAXCLASSIFICATION", aliases="ALIASES",
                 modified="DCS_LAST_MOD_DTM")


def dc(**kw):
    args = dict(name="dc", state="DC", service="dc", fields=DC_FIELDS, alias_search=True,
                alias_sep=" / ", defaults={"city": "WASHINGTON", "state": "DC"}, tier_prefix="Class ")
    args.update(kw)
    return ArcGisProvider(**args)


def pt(first, last, dob=date(1979, 1, 30)):
    return NormalizedPatient(first, "", last, "", dob, "", "WASHINGTON", "DC", "20001")


def run(coro):
    return asyncio.run(coro)


def test_rows_for_one_person_are_merged_into_one_record_with_all_locations():
    recs = run(dc().search(pt("MARCUS", "FICTIONAL"), "DC"))
    assert len(recs) == 1
    assert sorted(a[3] for a in recs[0].addresses) == ["20001", "20005"]  # HOME and WORK rows


def test_dob_epoch_at_4am_utc_is_the_right_calendar_date():
    rec = run(dc().search(pt("MARCUS", "FICTIONAL"), "DC"))[0]
    assert rec.dob == date(1979, 1, 30)


def test_defaults_fill_missing_city_and_state_and_tier_prefix():
    rec = run(dc().search(pt("MARCUS", "FICTIONAL"), "DC"))[0]
    assert rec.addresses[0][1] == "WASHINGTON" and rec.addresses[0][2] == "DC"
    assert rec.tier == "Class A" and rec.middle == ""


def test_alias_only_match_is_found_when_alias_search_is_on():
    # ED DOE is only an alias of EDWARD PAUL DOE, but a surname search for another prefix still
    # finds the person through the ALIASES column (alias text contains DOE)
    recs = run(dc().search(pt("ED", "DOE", date(1965, 12, 25)), "DC"))
    assert [r.registry_id for r in recs] == ["DC-3303"]
    assert ("ED", "", "DOE") in recs[0].aliases


def test_alias_search_off_misses_alias_only_surnames():
    # surname "TESTCASE" prefix TE does not match; with alias search off nothing links by alias text
    assert run(dc(alias_search=False).search(pt("X", "ZZ"), "DC")) == []


def test_paging_merges_pages_and_too_many_rows_fails_closed():
    # MARCUS FICTIONAL has 2 rows. page_size 1 forces 2 pages.
    assert len(run(dc(page_size=1, max_pages=2).search(pt("MARCUS", "FICTIONAL"), "DC"))) == 1
    with pytest.raises(ProviderBadResponse, match="too many"):
        run(dc(page_size=1, max_pages=1).search(pt("MARCUS", "FICTIONAL"), "DC"))


def test_surname_without_letters_is_rejected_not_matched_to_everything():
    with pytest.raises(ProviderBadResponse):
        run(dc().search(pt("A", "123"), "DC"))


def test_where_clause_only_ever_contains_letters():
    where = dc()._where(NormalizedPatient("A", "", "O'BRIEN; DROP--", "", date(1980, 1, 1), "", "", "", ""))
    assert where == "UPPER(LASTNAME) LIKE 'OB%' OR UPPER(ALIASES) LIKE '%OBR%'"


def test_live_switch_needs_a_live_url_and_marks_provider_real():
    cfg = {"state": "DC", "service": "dc", "fields": DC_FIELDS}
    s = Settings(live_providers={"dc_open_data"})
    with pytest.raises(ValueError, match="live_url"):
        ArcGisProvider.from_config("dc_open_data", cfg, s)
    live = ArcGisProvider.from_config("dc_open_data", {**cfg, "live_url": "https://example.invalid/x/FeatureServer/20"}, s)
    assert live.synthetic is False
    assert ArcGisProvider.from_config("dc_open_data", cfg, Settings()).synthetic is True
