"""Generic CSV snapshot provider for states that publish a downloadable file (TX, FL ...).

A new CSV state = one `providers:` block in config/states.yaml with a column map. No code.

The file is loaded into an in-memory index keyed by surname. Search narrows to surnames that are
close to the patient's (Jaro-Winkler >= `block_cutoff`) so typos in the surname still find people.
A file older than `max_age_hours` counts as UNAVAILABLE, so stale data can never produce a CLEAR.
"""

import asyncio
import csv
from datetime import datetime, timezone
from pathlib import Path

from rapidfuzz import process
from rapidfuzz.distance import JaroWinkler

from sor.models import NormalizedPatient, RegistryRecord
from sor.providers.base import Provider, ProviderUnavailable, build_record, register_type
from sor.textutil import parse_date

_REQUIRED = ("id", "first", "last")


@register_type("csv")
class CsvSnapshotProvider(Provider):
    kind = "snapshot"

    def __init__(
        self,
        name: str,
        state: str,
        path: Path,
        columns: dict[str, str],
        max_age_hours: float,
        detail_url: str | None = None,
        block_cutoff: float = 0.80,
        synthetic: bool = False,
    ):
        missing = [k for k in _REQUIRED if k not in columns]
        if missing:
            raise ValueError(f"{name}: columns map missing {missing}")
        self.name, self.state, self.path = name, state, Path(path)
        self.columns = columns
        self.max_age_hours = max_age_hours
        self.detail_url = detail_url
        self.block_cutoff = block_cutoff
        self.synthetic = synthetic
        self._mtime: float | None = None
        self._by_surname: dict[str, list[RegistryRecord]] = {}
        self._surnames: list[str] = []

    @classmethod
    def from_config(cls, name: str, cfg: dict, settings) -> "CsvSnapshotProvider":
        # Listed in LIVE_PROVIDERS: read the real downloaded file (live_path) instead of the demo file.
        go_live = name in settings.live_providers
        if go_live and not cfg.get("live_path"):
            raise ValueError(f"{name}: listed in LIVE_PROVIDERS but has no live_path in config")
        return cls(
            name=name,
            state=cfg["state"],
            path=settings.base_dir / (cfg["live_path"] if go_live else cfg["path"]),
            columns=cfg["columns"],
            max_age_hours=cfg.get("live_max_age_hours", 336) if go_live else cfg.get("max_age_hours", 48),
            detail_url=cfg.get("live_detail_url") if go_live else cfg.get("detail_url"),
            synthetic=False if go_live else cfg.get("synthetic", False),
        )

    def data_as_of(self) -> datetime | None:
        try:
            return datetime.fromtimestamp(self.path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return None

    def _ensure_loaded(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError as e:
            raise ProviderUnavailable(f"snapshot file missing: {self.path.name}") from e
        if mtime == self._mtime:
            return
        self._load(mtime)

    def _load(self, mtime: float) -> None:
        col = self.columns
        as_of = datetime.fromtimestamp(mtime, tz=timezone.utc)
        index: dict[str, list[RegistryRecord]] = {}
        with self.path.open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                def get(key: str) -> str:
                    return (row.get(col[key], "") if key in col else "") or ""

                rid = get("id").strip()
                if not rid:
                    continue
                aliases = [a for a in get("aliases").split(";") if a.strip()]
                rec = build_record(
                    registry_id=f"{self.state}-{rid}",
                    first=get("first"),
                    middle=get("middle"),
                    last=get("last"),
                    suffix=get("suffix"),
                    aliases=aliases,
                    dob=parse_date(get("dob")),
                    alt_dobs=[d for d in map(parse_date, get("alt_dobs").split(";")) if d],
                    street=get("street"),
                    city=get("city"),
                    state=get("state") or self.state,
                    zip_code=get("zip"),
                    tier=get("tier") or None,
                    offense=get("offense") or None,
                    detail_url=self.detail_url.format(id=rid) if self.detail_url else None,
                    source_state=self.state,
                    source_provider=self.name,
                    source_as_of=as_of,
                )
                for surname in {rec.last, *(a[2] for a in rec.aliases)}:
                    index.setdefault(surname, []).append(rec)
        self._by_surname = index
        self._surnames = list(index)
        self._mtime = mtime

    def _search_sync(self, patient: NormalizedPatient) -> list[RegistryRecord]:
        self._ensure_loaded()
        as_of = self.data_as_of()
        if as_of is not None:
            age_h = (datetime.now(timezone.utc) - as_of).total_seconds() / 3600
            if age_h > self.max_age_hours:
                raise ProviderUnavailable(
                    f"snapshot is {age_h:.0f}h old, limit is {self.max_age_hours:.0f}h"
                )
        hits = process.extract(
            patient.last, self._surnames, scorer=JaroWinkler.similarity,
            score_cutoff=self.block_cutoff, limit=None,
        )
        seen: set[str] = set()
        out: list[RegistryRecord] = []
        for surname, _score, _idx in hits:
            for rec in self._by_surname[surname]:
                if rec.registry_id not in seen:
                    seen.add(rec.registry_id)
                    out.append(rec)
        return out

    async def warm_up(self) -> None:
        try:  # a missing or broken file is reported on the first check as UNAVAILABLE, not at startup
            await asyncio.to_thread(self._ensure_loaded)
        except ProviderUnavailable:
            pass

    async def search(self, patient: NormalizedPatient, state: str) -> list[RegistryRecord]:
        return await asyncio.to_thread(self._search_sync, patient)
