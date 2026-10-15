"""End to end through the HTTP API with the mock registries."""

import os
import time

import pytest
from sqlalchemy import text

from conftest import make_request
from sor import chaos
from sor.providers.csv_snapshot import CsvSnapshotProvider


def post(client, body, actor="tester"):
    r = client.post("/v1/sor/checks", json=body, headers={"X-Actor-Id": actor})
    assert r.status_code == 200, r.text
    return r.json()


def test_exact_match_iowa_is_flagged_with_review_pending(client):
    r = post(client, make_request("John", "Doe", "1980-05-15", middle="Robert", city="Des Moines", zip_code="50309"))
    assert r["status"] == "FLAGGED" and r["reviewStatus"] == "PENDING"
    assert r["coverage"] == "COMPLETE" and r["statesChecked"] == ["IA"]
    top = r["providerResults"][0]["offendersFound"][0]
    assert top["registryId"] == "IA-55102" and top["matchConfidence"] >= 0.95
    assert "disclaimer" in r and r["disclaimer"]


def test_blank_home_state_is_treated_as_missing(client):
    body = make_request("John", "Doe", "1980-05-15", city="Des Moines", zip_code="50309")
    body["residenceState"] = ""  # the UI sends this when "Home state" is left blank
    body["patient"]["address"]["state"] = ""
    r = post(client, body)
    assert r["statesRequested"] == ["IA"]


def test_mock_results_are_labeled_synthetic(client):
    r = post(client, make_request("Zed", "Quillfeather", "1971-02-03", city="Cedar Rapids", zip_code="52401"))
    assert r["status"] == "CLEAR"  # a name that is not in the fake data comes back CLEAR
    assert "SYNTHETIC_DATA_SOURCE" in r["reasons"] and r["providerResults"][0]["synthetic"] is True
    health = {h["provider"]: h["synthetic"] for h in client.get("/v1/providers/health").json()}
    assert health["iowa_direct"] is True and health["vendor_mock"] is True


def test_real_data_only_mode_never_uses_mock_sources(tmp_path):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient
    from sor.config import Settings
    from sor.main import create_app

    s = Settings(database_url=f"sqlite:///{tmp_path / 'a.db'}", audit_key=Fernet.generate_key().decode(),
                 real_data_only=True)  # every configured source is mock here
    with TestClient(create_app(s)) as c:
        r = post(c, make_request("Alice", "Wonderland", "1991-04-12"))
    assert r["status"] == "ERROR" and "SYNTHETIC_DATA_SOURCE" not in r["reasons"]
    assert all("skipped" in a["error"] for a in r["providerResults"][0]["attempts"])


def test_live_iowa_provider_is_not_synthetic():
    from sor.providers.iowa import IowaProvider
    assert IowaProvider("x", mode="live").synthetic is False and IowaProvider("x").synthetic is True


def test_nobody_found_is_clear(client):
    r = post(client, make_request("Alice", "Wonderland", "1991-04-12", city="Des Moines", zip_code="50309"))
    assert r["status"] == "CLEAR" and r["reviewStatus"] == "NOT_REQUIRED"


def test_same_name_other_dob_is_inconclusive(client):
    r = post(client, make_request("John", "Doe", "1990-01-01", city="Des Moines", zip_code="50309"))
    assert r["status"] == "INCONCLUSIVE"


def test_dual_state_checks_both_and_flags_worst(client):
    r = post(client, make_request("Robert", "Smith", "1975-03-02", middle="James", state="IA",
                                  facility="TX", city="Cedar Rapids", zip_code="52401"))
    assert r["statesRequested"] == ["TX", "IA"] and r["status"] == "FLAGGED"
    by_state = {p["state"]: p for p in r["providerResults"]}
    assert by_state["IA"]["status"] == "MATCH_FOUND" and by_state["TX"]["providerUsed"] == "tx_csv"


@pytest.mark.parametrize("req_args, expected_id, provider", [
    (dict(first="Daniel", last="Row", dob="1983-02-17", state="TX", city="Houston", zip_code="77002"), "TX-77001", "tx_csv"),
    (dict(first="Anthony", middle="Ray", last="Valdez", suffix="Jr", dob="05/27/1976", state="FL", city="Tampa", zip_code="33602"), "FL-88003", "fl_csv"),
    (dict(first="Ed", last="Doe", dob="1965-12-25", state="DC", city="Washington", zip_code="20003"), "DC-3303", "dc_open_data"),
    (dict(first="Chris", last="Dummy", dob="1973-07-07", state="TN", city="Knoxville", zip_code="37902"), "TN-4403", "tn_open_data"),
])
def test_each_adapter_type_finds_its_person(client, req_args, expected_id, provider):
    r = post(client, make_request(**req_args))
    pr = r["providerResults"][0]
    assert r["status"] == "FLAGGED", r
    assert pr["providerUsed"] == provider
    assert pr["offendersFound"][0]["registryId"] == expected_id


def test_unlisted_state_uses_default_vendor_chain(client):
    r = post(client, make_request("Diego", "Synthetico", "1984-08-21", state="CA", middle="Andres",
                                  city="Los Angeles", zip_code="90001"))
    assert r["status"] == "FLAGGED" and r["providerResults"][0]["providerUsed"] == "vendor_mock"


def test_hyphenated_surname_written_with_space(client):
    r = post(client, make_request("Maria", "Garcia Lopez", "1990-11-23", city="Davenport", zip_code="52801"))
    assert r["status"] == "FLAGGED"


# ---------------------------------------------------------------- resilience


def test_primary_down_falls_back_to_vendor(client):
    chaos.set_mode("iowa_direct", "down")
    r = post(client, make_request("John", "Doe", "1980-05-15", city="Des Moines", zip_code="50309"))
    assert r["status"] == "FLAGGED"
    assert r["providerResults"][0]["providerUsed"] == "vendor_mock"
    assert "PRIMARY_FAILED_FALLBACK_USED" in r["reasons"]
    assert r["providerResults"][0]["attempts"][0]["status"] == "UNAVAILABLE"


def test_everything_down_is_error_never_clear(client):
    chaos.set_mode("iowa_direct", "down"); chaos.set_mode("vendor_mock", "down")
    r = post(client, make_request("Alice", "Wonderland", "1991-04-12"))
    assert r["status"] == "ERROR" and r["coverage"] == "NONE" and "ALL_SOURCES_FAILED" in r["reasons"]


def test_one_of_two_states_unchecked_is_inconclusive_not_clear(client):
    chaos.set_mode("tx_csv", "down"); chaos.set_mode("vendor_mock", "down")
    r = post(client, make_request("Alice", "Wonderland", "1991-04-12", state="IA", facility="TX"))
    assert r["status"] == "INCONCLUSIVE" and r["coverage"] == "PARTIAL"
    assert r["statesChecked"] == ["IA"] and "PARTIAL_COVERAGE" in r["reasons"]


def test_slow_source_times_out_then_falls_back(client, app):
    app.state.engine.timeout_s = 0.2
    app.state.engine.timeout_overrides.clear()
    chaos.set_mode("iowa_direct", "slow")
    r = post(client, make_request("John", "Doe", "1980-05-15", city="Des Moines", zip_code="50309"))
    attempts = r["providerResults"][0]["attempts"]
    assert attempts[0]["status"] == "TIMEOUT" and r["status"] == "FLAGGED"


def test_breaker_opens_after_repeated_failures_and_skips_provider(client, app):
    chaos.set_mode("iowa_direct", "down")
    req = make_request("Alice", "Wonderland", "1991-04-12")
    for _ in range(3):
        post(client, req)
    r = post(client, req)
    assert r["providerResults"][0]["attempts"][0]["status"] == "CIRCUIT_OPEN"
    health = {h["provider"]: h for h in client.get("/v1/providers/health").json()}
    assert health["iowa_direct"]["breaker"] == "OPEN"


def test_stale_snapshot_is_unavailable_so_cannot_say_clear(client, app):
    prov = app.state.engine.providers["tx_csv"]
    assert isinstance(prov, CsvSnapshotProvider)
    old = time.time() - 3600 * 24 * 365 * 20
    os.utime(prov.path, (old, old))
    try:
        prov.max_age_hours = 48
        chaos.set_mode("vendor_mock", "down")
        r = post(client, make_request("Daniel", "Roe", "1983-02-17", state="TX"))
        assert r["status"] == "ERROR"
        assert "snapshot is" in r["providerResults"][0]["attempts"][0]["error"]
    finally:
        os.utime(prov.path, None)


# ---------------------------------------------------------------- audit + validation


def test_audit_row_written_masked_and_payload_encrypted(client, app):
    post(client, make_request("John", "Doe", "1980-05-15", city="Des Moines", zip_code="50309"), actor="nurse-7")
    audit = app.state.engine.audit
    with audit.engine.connect() as conn:
        row = conn.execute(text("select * from audit_events")).mappings().one()
    assert row["actor_id"] == "nurse-7" and row["status"] == "FLAGGED" and row["patient_ref"] == "PAT-TEST"
    assert "JOHN" not in row["summary"].upper().replace("J***", "") and "J*** D***" in row["summary"]
    assert "Doe" not in row["payload_enc"] and "1980-05-15" not in row["payload_enc"]
    assert audit.decrypt(row["payload_enc"])["request"]["patient"]["lastName"] == "Doe"


def test_audit_log_is_append_only(client, app):
    post(client, make_request("Alice", "Wonderland", "1991-04-12"))
    with app.state.engine.audit.engine.begin() as conn:
        with pytest.raises(Exception, match="append-only"):
            conn.execute(text("delete from audit_events"))
    with app.state.engine.audit.engine.begin() as conn:
        with pytest.raises(Exception, match="append-only"):
            conn.execute(text("update audit_events set status='CLEAR'"))


def test_audit_failure_returns_503_not_an_unaudited_verdict(client, app, monkeypatch):
    def boom(**_kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(app.state.engine.audit, "append", boom)
    r = client.post("/v1/sor/checks", json=make_request("Alice", "Wonderland", "1991-04-12"))
    assert r.status_code == 503


@pytest.mark.parametrize("patch", [
    {"patient": {"dob": "not-a-date"}}, {"facilityState": "Iowa"}, {"patient": {"firstName": ""}},
])
def test_bad_input_is_422(client, patch):
    body = make_request("John", "Doe", "1980-05-15")
    for k, v in patch.items():
        body[k] = {**body[k], **v} if isinstance(v, dict) else v
    assert client.post("/v1/sor/checks", json=body).status_code == 422


def test_api_key_enforced_when_configured(tmp_path):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient
    from sor.config import Settings
    from sor.main import create_app

    s = Settings(database_url=f"sqlite:///{tmp_path / 'a.db'}", audit_key=Fernet.generate_key().decode(), api_key="s3cret")
    with TestClient(create_app(s)) as c:
        body = make_request("Alice", "Wonderland", "1991-04-12")
        assert c.post("/v1/sor/checks", json=body).status_code == 401
        assert c.post("/v1/sor/checks", json=body, headers={"X-API-Key": "s3cret"}).status_code == 200


def test_states_endpoint_and_ui_served(client):
    s = client.get("/v1/states").json()
    assert set(s["states"]) == {"IA", "TX", "FL", "DC", "TN"} and s["defaultChain"][0]["provider"] == "vendor_mock"
    page = client.get("/")
    assert page.status_code == 200 and "SOR Check Console" in page.text
