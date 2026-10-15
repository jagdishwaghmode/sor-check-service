"""Iowa adapter paging, quota and field-mapping rules (mock registry, no network)."""

import asyncio
from datetime import date

import pytest

from sor.models import NormalizedPatient
from sor.providers.base import ProviderBadResponse, ProviderRateLimited
from sor.providers.iowa import IowaProvider
from sor.textutil import state_code


def doe():
    return NormalizedPatient("JOHN", "", "DOE", "", date(1980, 5, 15), "", "DES MOINES", "IA", "50309")


def run(coro):
    return asyncio.run(coro)


def test_pages_are_fetched_and_merged():
    # the mock has 2 Iowa people named DOE, page_size 1 forces 2 pages
    p = IowaProvider("t", page_size=1, max_pages=2)
    recs = run(p.search(doe(), "IA"))
    assert sorted(r.registry_id for r in recs) == ["IA-55102", "IA-55105"]


def test_too_many_results_raises_so_router_falls_back():
    p = IowaProvider("t", page_size=1, max_pages=1)
    with pytest.raises(ProviderBadResponse, match="too many"):
        run(p.search(doe(), "IA"))


def test_nickname_queries_every_initial_and_dedupes():
    # BOB is in the ROBERT group, so Iowa is asked for R and B. Both hit the same person once.
    p = IowaProvider("t")
    seen = []
    original = p._query

    async def spy(params):
        seen.append(params["firstname"])
        return await original(params)

    p._query = spy
    bob = NormalizedPatient("BOB", "", "SMITH", "", date(1975, 3, 2), "", "", "IA", "")
    recs = run(p.search(bob, "IA"))
    assert sorted(seen) == ["B", "R"]
    assert [r.registry_id for r in recs] == ["IA-55103"]


def test_aliases_are_returned_when_page_is_small():
    p = IowaProvider("t")
    smith = NormalizedPatient("BOB", "", "SMITH", "", date(1975, 3, 2), "", "", "IA", "")
    rec = run(p.search(smith, "IA"))[0]
    assert ("BOB", "", "SMITH") in rec.aliases


def test_state_is_always_a_code():
    p = IowaProvider("t")
    rec = run(p.search(doe(), "IA"))[0]
    assert rec.addresses[0][2] == "IA"


def test_hyphenated_surname_queries_first_token():
    p = IowaProvider("t")
    maria = NormalizedPatient("MARIA", "", "GARCIA LOPEZ", "", date(1990, 11, 23), "", "", "IA", "")
    assert run(p.search(maria, "IA"))[0].last == "GARCIA-LOPEZ"


def test_local_quota_blocks_before_calling_the_source():
    p = IowaProvider("t", mode="live", hourly_quota=2)
    p._calls.extend([10**12, 10**12])  # two calls already used this hour (far-future timestamps)
    with pytest.raises(ProviderRateLimited):
        run(p.search(doe(), "IA"))


@pytest.mark.parametrize("raw, code", [("Iowa", "IA"), ("IOWA", "IA"), ("ia", "IA"),
                                       ("New York", "NY"), ("Nebraska", "NE"), ("Atlantis", ""), (None, "")])
def test_state_code(raw, code):
    assert state_code(raw) == code
