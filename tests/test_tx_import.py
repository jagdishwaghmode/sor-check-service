"""Texas DPS export importer and the csv provider's real-file switch. Made-up people only."""

import asyncio
import csv
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from sor.config import Settings
from sor.importers.tx_dps import convert, split_given
from sor.models import NormalizedPatient
from sor.providers.base import ProviderUnavailable
from sor.providers.csv_snapshot import CsvSnapshotProvider


def _tsv(rows: list[list[str]]) -> bytes:
    return "".join("\t".join(r) + "\r\n" for r in rows).encode("latin-1")


def make_export(path: Path, drop: str | None = None, year: int = 2026) -> None:
    tables = {
        "INDV": [["1", "00000001"], ["2", "00000002"]],
        "PERSON": [["1", "10", "M", "", "", "", "", "", ""], ["2", "20", "F", "", "", "", "", "", ""]],
        "NAME": [  # NAM_IDN, PER_IDN, TYP_COD, NAM_TXT, LNA_TXT, FNA_TXT
            ["1", "10", "B", "ZZTESTER,ROBERT JAMES JR", "ZZTESTER", "ROBERT JAMES JR"],
            ["2", "10", "S", "ZZTESTER,BOB", "ZZTESTER", "BOB"],
            ["3", "10", "S", "ZZTESTER,ROBERT JAMES JR", "ZZTESTER", "ROBERT JAMES JR"],  # same as primary
            ["4", "20", "B", "QQSAMPLE,ANNA", "QQSAMPLE", "ANNA"],
        ],
        "BRTHDATE": [["1", "10", "B", "1970-02-03"], ["2", "10", "S", "1971-02-03"], ["3", "20", "B", "1985-06-07"]],
        "INDV_SOR": [["1", "00000001", "1", "", "", "LIF", "", "A"], ["2", "00000002", "N", "", "", "LIF", "", "A"]],
        "Address": [  # AddressId, IND_IDN, SNU, SNA, SUD_COD, SUD_NBR, CTY, PLC, ZIP, COU, LAT, LON
            ["1", "1", "", "", "", "", "", "YY", "", "", "", ""],  # no street or zip, loses to the next one
            ["2", "1", "12", "MADE UP ST", "APT", "4", "SPRINGFIELD", "TX", "75001", "", "", ""],
        ],
    }
    with zipfile.ZipFile(path, "w") as zf:
        for name, rows in tables.items():
            if name != drop:
                zf.writestr(zipfile.ZipInfo(f"{name}.txt", (year, 10, 14, 10, 0, 0)), _tsv(rows))


def test_split_given_handles_middle_and_suffix():
    assert split_given("robert james jr") == ("ROBERT", "JAMES", "JR")
    assert split_given("ANNA") == ("ANNA", "", "")


def test_convert_flattens_tables(tmp_path):
    zp, out = tmp_path / "export.zip", tmp_path / "out" / "tx.csv"
    make_export(zp)
    stats = convert(zp, out)
    assert stats["people"] == 2 and stats["export_date"] == "2026-10-14"
    rows = {r["SID"]: r for r in csv.DictReader(out.open(newline=""))}
    a = rows["00000001"]
    assert (a["LAST_NAME"], a["FIRST_NAME"], a["MIDDLE_NAME"], a["SUFFIX"]) == ("ZZTESTER", "ROBERT", "JAMES", "JR")
    assert a["DOB"] == "1970-02-03" and a["RISK_LEVEL"] == "High"
    assert a["ALT_DOBS"] == "1971-02-03" and stats["with_alt_dob"] == 1
    assert a["ALIASES"] == "BOB ZZTESTER"  # the alias equal to the primary name is dropped
    assert (a["STREET"], a["CITY"], a["ST"], a["ZIP"]) == ("12 MADE UP ST APT 4", "SPRINGFIELD", "TX", "75001")
    b = rows["00000002"]
    assert b["STREET"] == "" and b["RISK_LEVEL"] == "Not Reported"
    # file time is the export date, so staleness is judged from the DPS refresh
    assert datetime.fromtimestamp(out.stat().st_mtime, timezone.utc).date() in (date(2026, 10, 13), date(2026, 10, 14))


def test_convert_names_missing_table(tmp_path):
    make_export(tmp_path / "export.zip", drop="NAME")
    with pytest.raises(KeyError):
        convert(tmp_path / "export.zip", tmp_path / "tx.csv")


def _settings(tmp_path, live: bool) -> Settings:
    return Settings(base_dir=tmp_path, live_providers=frozenset({"tx_csv"}) if live else frozenset())


CFG = {"state": "TX", "path": "demo.csv", "max_age_hours": 87600, "synthetic": True, "live_path": "real.csv",
       "live_max_age_hours": 336,
       "columns": {"id": "SID", "last": "LAST_NAME", "first": "FIRST_NAME", "middle": "MIDDLE_NAME", "suffix": "SUFFIX",
                   "dob": "DOB", "street": "STREET", "city": "CITY", "state": "ST", "zip": "ZIP",
                   "aliases": "ALIASES", "tier": "RISK_LEVEL", "offense": "OFFENSE", "alt_dobs": "ALT_DOBS"}}


def test_provider_uses_real_file_only_when_listed_live(tmp_path):
    demo = CsvSnapshotProvider.from_config("tx_csv", CFG, _settings(tmp_path, live=False))
    real = CsvSnapshotProvider.from_config("tx_csv", CFG, _settings(tmp_path, live=True))
    assert demo.synthetic and demo.path.name == "demo.csv"
    assert not real.synthetic and real.path.name == "real.csv" and real.max_age_hours == 336


def test_live_without_live_path_is_an_error(tmp_path):
    cfg = {k: v for k, v in CFG.items() if k != "live_path"}
    with pytest.raises(ValueError):
        CsvSnapshotProvider.from_config("tx_csv", cfg, _settings(tmp_path, live=True))


def test_real_file_finds_alias_and_old_file_is_unavailable(tmp_path):
    zp, out = tmp_path / "export.zip", tmp_path / "real.csv"
    make_export(zp, year=2020)
    convert(zp, out)
    prov = CsvSnapshotProvider.from_config("tx_csv", CFG, _settings(tmp_path, live=True))
    patient = NormalizedPatient("BOB", "", "ZZTESTER", "", date(1970, 2, 3), "", "", "TX", "75001")
    with pytest.raises(ProviderUnavailable):  # export dated 2020 is far older than 14 days
        asyncio.run(prov.search(patient, "TX"))
    prov.max_age_hours = 10**7
    hits = asyncio.run(prov.search(patient, "TX"))
    assert [r.registry_id for r in hits] == ["TX-00000001"]
    assert hits[0].dob == date(1970, 2, 3) and hits[0].alt_dobs == [date(1971, 2, 3)]


def test_warm_up_loads_file_and_tolerates_a_missing_one(tmp_path):
    make_export(tmp_path / "export.zip", year=2020)
    convert(tmp_path / "export.zip", tmp_path / "real.csv")
    prov = CsvSnapshotProvider.from_config("tx_csv", CFG, _settings(tmp_path, live=True))
    assert prov._mtime is None
    asyncio.run(prov.warm_up())
    assert prov._mtime is not None
    (tmp_path / "real.csv").unlink()
    gone = CsvSnapshotProvider.from_config("tx_csv", CFG, _settings(tmp_path, live=True))
    asyncio.run(gone.warm_up())  # must not raise
