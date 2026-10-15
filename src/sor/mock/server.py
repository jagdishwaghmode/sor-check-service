"""Mock registries. Each mimics a different real-world wire style, with synthetic data.

- /iowa/...    copies the documented Iowa API shape (starts-with name search, max 100 per page)
- /arcgis/...  ArcGIS REST query shape (SQL-ish `where`, features[].attributes, epoch-ms dates)
- /vendor/...  IDScan-style sync vendor (POST, profiles[] per service)

Mounted at /mock in the main app so you can browse it. Providers call it in-process.
"""

import re
from datetime import datetime, timezone

from fastapi import FastAPI, Query
from pydantic import BaseModel
from rapidfuzz.distance import JaroWinkler

from sor.mock.data import PEOPLE, people_for
from sor.textutil import clean, parse_full_name

mock_app = FastAPI(title="Mock registries (synthetic data only)")

# ArcGIS services. DC mirrors the real FEEDS/MPD layer 20 field names (checked live 2026-10-15).
# TN field names are still placeholders (the real service was unreachable when checked).
ARCGIS_FIELDS = {
    "dc": dict(id="SEXOFFENDERCODE", first="FIRSTNAME", last="LASTNAME", dob="BIRTHDATE",
               street="BLOCKNAME", zip="ZIPCODE", tier="MAXCLASSIFICATION", aliases="ALIASES",
               modified="DCS_LAST_MOD_DTM"),
    "tn": dict(id="TBI_ID", first="FNAME", middle="MNAME", last="LNAME", dob="BIRTH_DT",
               street="ADDR1", city="CITY_NAME", state="ST", zip="ZIP5", tier="REG_TYPE",
               offense="OFFENSE", aliases="AKA"),
}
ARCGIS_STATE = {"dc": "DC", "tn": "TN"}
ARCGIS_ALIAS_SEP = {"dc": " / ", "tn": ";"}
# DC has no middle name, city or state columns. People can have extra WORK/SCHOOL rows.
DC_EXTRA_ROWS = {"3301": [("WORK", "20005")]}


def _epoch_ms(iso: str, hour: int = 0) -> int:
    d = datetime.strptime(iso, "%Y-%m-%d").replace(hour=hour, tzinfo=timezone.utc)
    return int(d.timestamp() * 1000)


# ------------------------------------------------------------------ Iowa-style


def _iowa_record(p: dict, with_extra: bool) -> dict:
    rec = {
        "registrant": p["id"].zfill(5),
        "oci": f"OCI{p['id']}",
        "last_name": p["last"],
        "first_name": p["first"],
        "middle_name": p["middle"],
        "gender": "",
        "birthdate": p["dob"],
        "tier": p["tier"],
        "address": f"{p['street']}, {p['city']}, IA {p['zip']}",
        "line_1": p["street"],
        "city": p["city"],
        "state": "IA",
        "postal_code": p["zip"],
        "county": "SYNTHETIC",
        "last_changed": "2026-10-13T23:00:25Z",
    }
    if with_extra:  # real API only adds these when 25 or fewer results per page
        rec["aliases"] = [
            dict(zip(("first_name", "middle_name", "last_name"), parse_full_name(a)))
            for a in p["aliases"]
        ]
    return rec


@mock_app.get("/iowa/api/search/results.json")
def iowa_search(
    lastname: str = "",
    firstname: str = "",
    alias: int = 0,
    per_page: int = Query(100, le=100),
    page: int = 1,
):
    ln, fn = clean(lastname), clean(firstname)
    matches = []
    for p in people_for("IA"):
        lasts = [clean(p["last"])]
        firsts = [clean(p["first"])]
        if alias:
            for a in p["aliases"]:
                f, _m, l = parse_full_name(a)
                lasts.append(l)
                firsts.append(f)
        if ln and not any(x.startswith(ln) for x in lasts):  # starts-with, like the real API
            continue
        if fn and not any(x.startswith(fn) for x in firsts):
            continue
        matches.append(p)
    total = len(matches)
    pages = max(1, -(-total // per_page))
    chunk = matches[(page - 1) * per_page : page * per_page]
    with_extra = len(chunk) <= 25
    return {
        "results": total,
        "page": page,
        "pages": pages,
        "onpage": len(chunk),
        "records": [_iowa_record(p, with_extra) for p in chunk],
    }


@mock_app.get("/iowa/api/registrant/{rid}.json")
def iowa_registrant(rid: str):
    for p in people_for("IA"):
        if p["id"].zfill(5) == rid:
            return _iowa_record(p, True)
    return {"results": 0, "records": None, "error": 1, "message": "registrant not found"}


# ------------------------------------------------------------------ ArcGIS-style

_CLAUSE = re.compile(r"UPPER\((\w+)\)\s+LIKE\s+'(%?)([A-Z0-9 -]*)%'")


def _arcgis_rows(service: str, p: dict) -> list[dict]:
    """One attribute dict per location row, shaped like the real service."""
    fields = ARCGIS_FIELDS[service]
    sep = ARCGIS_ALIAS_SEP[service]
    base = {
        "id": p["id"], "first": p["first"], "middle": p["middle"], "last": p["last"],
        "dob": _epoch_ms(p["dob"], hour=4 if service == "dc" else 0), "street": p["street"],
        "city": p["city"], "state": p["state"], "zip": p["zip"],
        "tier": p["tier"].removeprefix("Class ") if service == "dc" else p["tier"],  # real DC says just A or B
        "offense": p["offense"], "aliases": sep.join(p["aliases"]),
        "modified": _epoch_ms("2026-10-13", hour=6),
    }
    locations = [("HOME", p["zip"])] + DC_EXTRA_ROWS.get(p["id"], []) if service == "dc" else [("HOME", p["zip"])]
    rows = []
    for loc_type, zip_code in locations:
        attrs = {fields[k]: v for k, v in base.items() if k in fields}
        if "zip" in fields:
            attrs[fields["zip"]] = zip_code
        if service == "dc":
            attrs["TYPE"] = loc_type
        rows.append(attrs)
    return rows


@mock_app.get("/arcgis/{service}/query")
def arcgis_query(service: str, where: str = "1=1", outFields: str = "*", f: str = "json",
                 resultRecordCount: int = 1000, resultOffset: int = 0, returnGeometry: str = "true"):
    if service not in ARCGIS_FIELDS:
        return {"error": {"code": 400, "message": "Invalid service", "details": []}}
    fields = ARCGIS_FIELDS[service]
    clauses = [_CLAUSE.search(c) for c in where.split(" OR ")]
    if not all(clauses):
        return {"error": {"code": 400, "message": "Unable to complete operation.",
                          "details": ["only UPPER(field) LIKE 'X%' or '%X%' joined by OR supported by this mock"]}}
    for m in clauses:
        if m.group(1) not in (fields["last"], fields.get("aliases")):
            return {"error": {"code": 400, "message": "Invalid field", "details": [m.group(1)]}}
    rows = []
    for p in people_for(ARCGIS_STATE[service]):
        alias_text = ARCGIS_ALIAS_SEP[service].join(p["aliases"]).upper()
        hit = False
        for m in clauses:
            field_name, contains, text = m.group(1), bool(m.group(2)), m.group(3)
            value = clean(p["last"]) if field_name == fields["last"] else alias_text
            if (text in value) if contains else value.startswith(text):
                hit = True
        if hit:
            rows.extend(_arcgis_rows(service, p))
    page = rows[resultOffset: resultOffset + resultRecordCount]
    return {"features": [{"attributes": r} for r in page],
            "exceededTransferLimit": resultOffset + resultRecordCount < len(rows) or None}


# ------------------------------------------------------------------ vendor-style


class VendorCheck(BaseModel):
    firstName: str
    lastName: str
    middleName: str | None = None
    dateOfBirths: list[str] = []
    state: str | None = None
    services: list[str] = []
    referenceId: str | None = None


@mock_app.post("/vendor/api/Check")
def vendor_check(body: VendorCheck):
    ln = clean(body.lastName)
    profiles = []
    for p in PEOPLE:  # the vendor claims nationwide coverage
        if body.state and p["state"] != body.state.upper():
            continue
        lasts = [clean(p["last"])] + [parse_full_name(a)[2] for a in p["aliases"]]
        if max(JaroWinkler.similarity(ln, x) for x in lasts) < 0.85:  # vendor-side fuzzy
            continue
        profiles.append({
            "internalId": f"V-{p['state']}-{p['id']}",
            "firstName": p["first"], "middleName": p["middle"], "lastName": p["last"],
            "fullName": " ".join(x for x in (p["first"], p["middle"], p["last"], p["suffix"]) if x),
            "dateOfBirths": [p["dob"]],
            "aliases": [dict(zip(("firstName", "middleName", "lastName"), parse_full_name(a)))
                        for a in p["aliases"]],
            "street1": p["street"], "city": p["city"], "state": p["state"], "zipCode": p["zip"],
            "convictiontype": p["tier"],
            "offenses": [{"title": p["offense"]}],
            "source": f"MOCK-VENDOR/{p['state']}",
        })
    return [{"serviceId": "SOR", "serviceName": "Sex Offender Registry", "success": True,
             "error": None, "profiles": profiles}]
