"""Provider contract. Adapters translate a source's wire format into RegistryRecord.

They never score or decide, matching lives in sor.matching.
"""

from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any, ClassVar

import httpx

from sor.models import AttemptStatus, NormalizedPatient, RegistryRecord
from sor.textutil import clean, parse_full_name, state_code, zip5


class ProviderError(Exception):
    status: ClassVar[AttemptStatus] = AttemptStatus.UNAVAILABLE
    counts_for_breaker: ClassVar[bool] = True


class ProviderTimeout(ProviderError):
    status = AttemptStatus.TIMEOUT


class ProviderUnavailable(ProviderError):
    status = AttemptStatus.UNAVAILABLE


class ProviderRateLimited(ProviderError):
    status = AttemptStatus.RATE_LIMITED
    counts_for_breaker = False  # our own or the source's quota says nothing about its health


class ProviderBadResponse(ProviderError):
    status = AttemptStatus.BAD_RESPONSE


class Provider(ABC):
    name: str
    kind: str  # live | open_data | snapshot | vendor
    synthetic: bool = False  # True when backed by mock/fake data, never a real registry

    @abstractmethod
    async def search(self, patient: NormalizedPatient, state: str) -> list[RegistryRecord]:
        """Return candidate records (empty list = nobody found, a valid answer).

        Raise a ProviderError subclass for anything that means "could not check".
        """

    def data_as_of(self) -> datetime | None:
        return None

    async def warm_up(self) -> None:
        """Optional: load anything slow at startup so the first check is not the one that pays."""
        return None

    async def aclose(self) -> None:
        return None


# type name (used in config/states.yaml) -> class with from_config(name, cfg, settings)
PROVIDER_TYPES: dict[str, Any] = {}


def register_type(type_name: str):
    def deco(cls):
        PROVIDER_TYPES[type_name] = cls
        return cls

    return deco


def make_client(mode: str, live_base_url: str, timeout_s: float = 5.0) -> httpx.AsyncClient:
    """Real HTTPS client in live mode, in-process mock registry otherwise."""
    if mode == "live":
        return httpx.AsyncClient(base_url=live_base_url, timeout=timeout_s)
    from sor.mock.server import mock_app  # imported lazily, mock is dev/test only

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mock_app), base_url="http://mock", timeout=timeout_s
    )


async def http_json(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> Any:
    """HTTP call with errors mapped to ProviderError subclasses."""
    try:
        resp = await client.request(method, url, **kwargs)
    except httpx.TimeoutException as e:
        raise ProviderTimeout(str(e) or "http timeout") from e
    except httpx.HTTPError as e:
        raise ProviderUnavailable(f"connection error: {type(e).__name__}") from e
    if resp.status_code == 429:
        raise ProviderRateLimited("source returned 429")
    if resp.status_code >= 500:
        raise ProviderUnavailable(f"source returned {resp.status_code}")
    if resp.status_code >= 400:
        raise ProviderBadResponse(f"source returned {resp.status_code}")
    try:
        return resp.json()
    except ValueError as e:
        raise ProviderBadResponse("response is not valid JSON") from e


def build_record(
    *,
    registry_id: str,
    first: str,
    middle: str = "",
    last: str,
    suffix: str = "",
    aliases: list[str] | None = None,
    dob: date | None = None,
    street: str = "",
    city: str = "",
    state: str = "",
    zip_code: str = "",
    tier: str | None = None,
    offense: str | None = None,
    detail_url: str | None = None,
    source_state: str,
    source_provider: str,
    source_as_of: datetime | None = None,
) -> RegistryRecord:
    """Normalize raw source values into a RegistryRecord (same cleaning as the patient)."""
    parsed_aliases = [parse_full_name(a) for a in (aliases or []) if a and a.strip()]
    address = (clean(street), clean(city), state_code(state), zip5(zip_code))
    return RegistryRecord(
        registry_id=registry_id,
        first=clean(first),
        middle=clean(middle),
        last=clean(last),
        suffix=clean(suffix),
        aliases=[a for a in parsed_aliases if a[2]],
        dob=dob,
        addresses=[address] if any(address) else [],
        tier=tier,
        offense=offense,
        detail_url=detail_url,
        source_state=source_state,
        source_provider=source_provider,
        source_as_of=source_as_of,
    )
