"""Generic ArcGIS REST provider for states that publish registry data as a GIS service (DC, TN).

A new ArcGIS state = one `providers:` block with a layer URL and a field map. No code.

What real services taught us (DC, tested live 2026-10-15):
- `where` is SQL. We only ever put letters into it, so patient input cannot alter the query.
- dates come back as epoch milliseconds
- one person can have several rows (home, work, school locations), so rows are merged by id
- a layer returns at most `maxRecordCount` rows and says so with `exceededTransferLimit`, we page
  with `resultOffset`, and if there is still more after `max_pages` we raise (fail closed)
- aliases can be one text field with a separator, searched with `LIKE '%XXX%'`
"""

import re
from datetime import datetime, timezone

from sor.models import NormalizedPatient, RegistryRecord
from sor.providers.base import (
    Provider,
    ProviderBadResponse,
    build_record,
    http_json,
    make_client,
    register_type,
)
from sor.textutil import clean, epoch_ms_to_date, parse_date, state_code, zip5

_REQUIRED = ("id", "first", "last")


@register_type("arcgis")
class ArcGisProvider(Provider):
    kind = "open_data"

    def __init__(
        self,
        name: str,
        state: str,
        service: str,
        fields: dict[str, str],
        live_url: str | None = None,
        prefix_len: int = 2,
        alias_search: bool = False,
        alias_prefix_len: int = 3,
        alias_sep: str = ";",
        defaults: dict[str, str] | None = None,
        tier_prefix: str = "",
        page_size: int = 1000,
        max_pages: int = 3,
        timeout_s: float = 8.0,
    ):
        missing = [k for k in _REQUIRED if k not in fields]
        if missing:
            raise ValueError(f"{name}: fields map missing {missing}")
        self.name, self.state, self.service, self.fields = name, state, service, fields
        self.prefix_len, self.alias_search, self.alias_prefix_len = prefix_len, alias_search, alias_prefix_len
        self.alias_sep, self.defaults, self.tier_prefix = alias_sep, defaults or {}, tier_prefix
        self.page_size, self.max_pages = page_size, max_pages
        self.live = live_url is not None
        self.synthetic = not self.live
        self._client = make_client("live" if self.live else "mock", live_url or "", timeout_s)
        # a live url points at the layer itself, e.g. .../FeatureServer/20
        self._path = "/query" if self.live else f"/arcgis/{service}/query"
        self._as_of: datetime | None = None

    @classmethod
    def from_config(cls, name: str, cfg: dict, settings) -> "ArcGisProvider":
        go_live = name in settings.live_providers
        if go_live and not cfg.get("live_url"):
            raise ValueError(f"{name}: listed in LIVE_PROVIDERS but has no live_url in config")
        return cls(
            name=name,
            state=cfg["state"],
            service=cfg.get("service", ""),
            fields=cfg["fields"],
            live_url=cfg["live_url"] if go_live else None,
            prefix_len=cfg.get("prefix_len", 2),
            alias_search=cfg.get("alias_search", False),
            alias_prefix_len=cfg.get("alias_prefix_len", 3),
            alias_sep=cfg.get("alias_sep", ";"),
            defaults=cfg.get("defaults"),
            tier_prefix=cfg.get("tier_prefix", ""),
            page_size=cfg.get("page_size", 1000),
            max_pages=cfg.get("max_pages", 3),
        )

    # ------------------------------------------------------------------ query

    def _where(self, patient: NormalizedPatient) -> str:
        letters = re.sub(r"[^A-Z]", "", patient.last)
        if not letters:
            raise ProviderBadResponse("surname has no letters to search with")
        clauses = [f"UPPER({self.fields['last']}) LIKE '{letters[: self.prefix_len]}%'"]
        if self.alias_search and "aliases" in self.fields:
            clauses.append(f"UPPER({self.fields['aliases']}) LIKE '%{letters[: self.alias_prefix_len]}%'")
        return " OR ".join(clauses)

    async def _fetch(self, where: str) -> list[dict]:
        out_fields = ",".join(dict.fromkeys(self.fields.values()))
        features: list[dict] = []
        for page in range(self.max_pages):
            data = await http_json(
                self._client, "GET", self._path,
                params={"where": where, "outFields": out_fields, "returnGeometry": "false",
                        "f": "json", "resultRecordCount": self.page_size,
                        "resultOffset": page * self.page_size},
            )
            if not isinstance(data, dict):
                raise ProviderBadResponse("unexpected response shape")
            if "error" in data:
                raise ProviderBadResponse(f"ArcGIS error: {data['error'].get('message', 'unknown')}")
            features.extend(data.get("features") or [])
            if not data.get("exceededTransferLimit"):
                return features
        raise ProviderBadResponse("too many rows to check completely")

    # ------------------------------------------------------------------ parse

    @staticmethod
    def _date(value) -> "object":
        if isinstance(value, (int, float)):
            return epoch_ms_to_date(value)
        return parse_date(value)

    async def search(self, patient: NormalizedPatient, state: str) -> list[RegistryRecord]:
        features = await self._fetch(self._where(patient))
        f, d = self.fields, self.defaults
        people: dict[str, RegistryRecord] = {}
        for feat in features:
            a = feat.get("attributes", {})

            def get(key: str) -> str:
                return str(a.get(f[key]) or "") if key in f else ""

            rid = get("id").strip()
            if not rid:
                continue
            address = (
                clean(get("street")),
                clean(get("city") or d.get("city", "")),
                state_code(get("state") or d.get("state", self.state)),
                zip5(get("zip")),
            )
            modified = a.get(f["modified"]) if "modified" in f else None
            as_of = (
                datetime.fromtimestamp(modified / 1000, tz=timezone.utc)
                if isinstance(modified, (int, float)) else None
            )
            if rid in people:  # same person, another location row (work, school)
                rec = people[rid]
                if any(address) and address not in rec.addresses:
                    rec.addresses.append(address)
                if as_of and (rec.source_as_of is None or as_of > rec.source_as_of):
                    rec.source_as_of = as_of
                continue
            tier = get("tier")
            rec = build_record(
                registry_id=f"{self.state}-{rid}",
                first=get("first"), middle=get("middle"), last=get("last"),
                aliases=[x for x in get("aliases").split(self.alias_sep) if x.strip()],
                dob=self._date(a.get(f.get("dob", ""))),
                tier=f"{self.tier_prefix}{tier}" if tier else None,
                offense=get("offense") or None,
                source_state=self.state, source_provider=self.name, source_as_of=as_of,
            )
            rec.addresses = [address] if any(address) else []
            people[rid] = rec
        stamps = [r.source_as_of for r in people.values() if r.source_as_of]
        if stamps:
            self._as_of = max(stamps)
        return list(people.values())

    def data_as_of(self) -> datetime | None:
        return self._as_of

    async def aclose(self) -> None:
        await self._client.aclose()
