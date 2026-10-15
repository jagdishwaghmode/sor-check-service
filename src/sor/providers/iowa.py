"""Iowa adapter (reference). Built from the official guide at iowasexoffender.gov/feed/info.

Source rules honored here:
- no more than 50 requests per hour (we enforce a lower local quota in live mode)
- name search is starts-with, so we query by the first token of the surname
- aliases only come back when 25 or fewer results are on a page. Verified live: SMITH alone gives
  87 results with no aliases, and 28 of them match only through an alias, so aliases matter.
  Pages of 25 are also slow (about 8s, Iowa's own limit is 10s) because Iowa builds the extra
  fields per record.
- so we narrow by first initial instead, including nickname variants (BOB also queries R for
  ROBERT). Live: SMITH + J = 15 results in one page with aliases, 2 to 5 seconds.
- if a narrowed query still has more than page_size * max_pages results we cannot check it
  completely, so we raise and the router falls back to another source (fail closed)
Not done yet: registrant cache (max 14 days), see the private plan.
"""

import asyncio
import re
import time
from collections import deque
from typing import Any

from sor.matching import first_name_variants
from sor.models import NormalizedPatient, RegistryRecord
from sor.providers.base import (
    Provider,
    ProviderBadResponse,
    ProviderRateLimited,
    http_json,
    make_client,
    register_type,
)
from sor.providers.base import build_record
from sor.textutil import parse_date

LIVE_BASE_URL = "https://www.iowasexoffender.gov"


@register_type("iowa")
class IowaProvider(Provider):
    kind = "live"

    def __init__(self, name: str, mode: str = "mock", hourly_quota: int = 45, timeout_s: float = 9.0,
                 page_size: int = 25, max_pages: int = 4):
        self.name = name
        self.mode = mode
        self.synthetic = mode != "live"
        self.hourly_quota = hourly_quota
        self.page_size = page_size
        self.max_pages = max_pages
        self._calls: deque[float] = deque()
        self._client = make_client(mode, LIVE_BASE_URL, timeout_s)
        self._prefix = "" if mode == "live" else "/iowa"

    @classmethod
    def from_config(cls, name: str, cfg: dict, settings) -> "IowaProvider":
        mode = "live" if name in settings.live_providers else (settings.iowa_mode or cfg.get("mode", "mock"))
        return cls(name, mode=mode, hourly_quota=cfg.get("hourly_quota", 45),
                   page_size=cfg.get("page_size", 25), max_pages=cfg.get("max_pages", 4))

    def _check_quota(self) -> None:
        if self.mode != "live":
            return
        now = time.monotonic()
        while self._calls and now - self._calls[0] > 3600:
            self._calls.popleft()
        if len(self._calls) >= self.hourly_quota:
            raise ProviderRateLimited(f"local Iowa quota of {self.hourly_quota}/hour used up")
        self._calls.append(now)

    async def _query(self, params: dict[str, Any]) -> dict:
        self._check_quota()
        data = await http_json(
            self._client, "GET", f"{self._prefix}/api/search/results.json", params=params
        )
        if not isinstance(data, dict):
            raise ProviderBadResponse("unexpected response shape")
        return data

    async def _page(self, surname: str, initial: str, page: int) -> dict:
        data = await self._query({
            "lastname": surname, "firstname": initial, "alias": 1,
            "per_page": self.page_size, "page": page,
        })
        if data.get("error"):
            raise ProviderBadResponse(f"Iowa API error: {data.get('message', 'unknown')}")
        return data

    async def _search_initial(self, surname: str, initial: str) -> list[dict]:
        first = await self._page(surname, initial, 1)
        total = int(first.get("results") or 0)
        if total > self.page_size * self.max_pages:
            raise ProviderBadResponse(
                f"{total} results for {surname} + {initial}, too many to check completely"
            )
        records = list(first.get("records") or [])
        pages = int(first.get("pages") or 1)
        if pages > 1:
            rest = await asyncio.gather(*(self._page(surname, initial, n) for n in range(2, pages + 1)))
            for page_data in rest:
                records.extend(page_data.get("records") or [])
        return records

    async def search(self, patient: NormalizedPatient, state: str) -> list[RegistryRecord]:
        surname = re.split(r"[\s-]", patient.last)[0]
        initials = sorted({v[0] for v in first_name_variants(patient.first)})
        batches = await asyncio.gather(*(self._search_initial(surname, i) for i in initials))
        seen: set[str] = set()
        out: list[RegistryRecord] = []
        for batch in batches:
            for raw in batch:
                rid = str(raw.get("registrant", ""))
                if rid not in seen:
                    seen.add(rid)
                    out.append(self._to_record(raw))
        return out

    def _to_record(self, r: dict) -> RegistryRecord:
        rid = str(r.get("registrant", ""))
        aliases = [
            " ".join(x for x in (a.get("first_name"), a.get("middle_name"), a.get("last_name")) if x)
            for a in (r.get("aliases") or [])
        ]
        return build_record(
            registry_id=f"IA-{rid}",
            first=r.get("first_name") or "",
            middle=r.get("middle_name") or "",
            last=r.get("last_name") or "",
            aliases=aliases,
            dob=parse_date(r.get("birthdate")),
            street=r.get("line_1") or r.get("address") or "",
            city=r.get("city") or "",
            state=r.get("state") or "",  # live API sends the full word ("Iowa"), mapped to a code
            zip_code=r.get("postal_code") or "",
            tier=r.get("tier"),
            detail_url=f"{LIVE_BASE_URL}/registrant/registrantdetail?id={rid.lstrip('0') or rid}",
            source_state="IA",
            source_provider=self.name,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
