import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from sor import chaos
from sor.config import Settings
from sor.main import create_app


@pytest.fixture
def app(tmp_path):
    chaos.reset()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'audit.db'}",
        audit_key=Fernet.generate_key().decode(),
        enable_chaos=True,
    )
    yield create_app(settings)
    chaos.reset()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def make_request(first, last, dob, *, state="IA", facility=None, middle=None, suffix=None,
                 city=None, zip_code=None, residence=None):
    return {
        "facilityState": facility or state,
        "residenceState": residence,
        "patient": {
            "patientId": "PAT-TEST", "firstName": first, "middleName": middle, "lastName": last,
            "suffix": suffix, "dob": dob,
            "address": {"street": None, "city": city, "state": state, "zip": zip_code},
        },
    }
