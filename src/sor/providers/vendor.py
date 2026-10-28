"""Vendor adapter (sync style, modeled on IDScan: one POST returns profiles[]).

An async order-then-poll vendor (Certn style) would be a second class behind the same
Provider interface, see the private plan. Live mode needs base_url + VENDOR_API_KEY.
"""

from sor.models import NormalizedPatient, RegistryRecord
from sor.providers.base import (
    Provider,
    ProviderBadResponse,
    build_record,
    http_json,
    make_client,
    register_type,
)
from sor.textutil import parse_date


@register_type("vendor")
class VendorProvider(Provider):
    kind = "vendor"

    def __init__(self, name: str, base_url: str | None = None, api_key: str = "", timeout_s: float = 5.0):
        self.name = name
        self.live = base_url is not None
        self.synthetic = not self.live
        self._api_key = api_key
        self._client = make_client("live" if self.live else "mock", base_url or "", timeout_s)
        self._path = "/api/Check" if self.live else "/vendor/api/Check"

    @classmethod
    def from_config(cls, name: str, cfg: dict, settings) -> "VendorProvider":
        return cls(name, base_url=cfg.get("base_url"), api_key=settings.vendor_api_key)

    async def search(self, patient: NormalizedPatient, state: str) -> list[RegistryRecord]:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        body = {
            "firstName": patient.first,
            "middleName": patient.middle or None,
            "lastName": patient.last,
            "dateOfBirths": [patient.dob.isoformat()] if patient.dob else [],
            "state": state,
            "services": ["SOR"],
        }
        data = await http_json(self._client, "POST", self._path, json=body, headers=headers)
        if not isinstance(data, list):
            raise ProviderBadResponse("unexpected response shape")
        out: list[RegistryRecord] = []
        for service in data:
            if not service.get("success", False):
                raise ProviderBadResponse(f"vendor error: {service.get('error') or 'unknown'}")
            for prof in service.get("profiles") or []:
                aliases = [
                    " ".join(x for x in (a.get("firstName"), a.get("middleName"), a.get("lastName")) if x)
                    for a in (prof.get("aliases") or [])
                ]
                dobs = prof.get("dateOfBirths") or []
                offenses = prof.get("offenses") or []
                out.append(build_record(
                    registry_id=str(prof.get("internalId", "")),
                    first=prof.get("firstName") or "",
                    middle=prof.get("middleName") or "",
                    last=prof.get("lastName") or "",
                    aliases=aliases,
                    dob=parse_date(dobs[0]) if dobs else None,
                    street=prof.get("street1") or "",
                    city=prof.get("city") or "",
                    state=prof.get("state") or state,
                    zip_code=prof.get("zipCode") or "",
                    tier=prof.get("convictiontype"),
                    offense=(offenses[0].get("title") if offenses else None),
                    source_state=(prof.get("state") or state).upper(),
                    source_provider=self.name,
                ))
        return out

    async def aclose(self) -> None:
        await self._client.aclose()
