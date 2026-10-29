"""QA coverage: Texas DPS file expiry edge-cases and importer output checks.

Day-2 follow-up after sharing the gap list with the team:

1. Expiry boundary tests: confirm the age threshold that flips
   CsvSnapshotProvider from available → ProviderUnavailable, and verify the
   max_age_hours cap can be overridden for manual QA runs.

2. Importer output checks on the CSV that convert() writes: risk level,
   column schema and the ALIASES column. Console gap 1 (no line for a state
   with zero records) has no automated reproducer yet, see the QA notes.

All tests are fully offline (no network, uses tmp_path / mock data).
"""

import asyncio
import csv
import os
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from sor.config import Settings
from sor.importers.tx_dps import convert
from sor.models import NormalizedPatient
from sor.providers.base import ProviderUnavailable
from sor.providers.csv_snapshot import CsvSnapshotProvider

# ---------------------------------------------------------------------------
# shared helpers (duplicated from test_tx_import to keep this file standalone)
# ---------------------------------------------------------------------------

def _tsv(rows: list[list[str]]) -> bytes:
    return "".join("\t".join(r) + "\r\n" for r in rows).encode("latin-1")


def _make_export(path: Path, year: int = 2026, risk_codes: tuple[str, str] = ("1", "N")) -> None:
    """Write a minimal but valid Texas DPS zip with two synthetic people.

    risk_codes are the INDV_SOR risk codes of the two people. ZZTESTER also gets a
    second NAME row identical to the primary name, so the importer has to drop it.
    """
    tables = {
        "INDV": [["1", "00000001"], ["2", "00000002"]],
        "PERSON": [["1", "10", "M", "", "", "", "", "", ""],
                   ["2", "20", "F", "", "", "", "", "", ""]],
        "NAME": [
            ["1", "10", "B", "ZZTESTER,ROBERT JAMES JR", "ZZTESTER", "ROBERT JAMES JR"],
            ["2", "10", "S", "ZZTESTER,BOB", "ZZTESTER", "BOB"],
            ["3", "20", "B", "QQSAMPLE,ANNA", "QQSAMPLE", "ANNA"],
            ["4", "10", "S", "ZZTESTER,ROBERT JAMES JR", "ZZTESTER", "ROBERT JAMES JR"],
        ],
        "BRTHDATE": [["1", "10", "B", "1970-02-03"], ["2", "20", "B", "1985-06-07"]],
        "INDV_SOR": [["1", "00000001", risk_codes[0], "", "", "LIF", "", "A"],
                     ["2", "00000002", risk_codes[1], "", "", "LIF", "", "A"]],
        "Address": [
            ["1", "1", "12", "MADE UP ST", "APT", "4", "SPRINGFIELD", "TX", "75001", "", "", ""],
        ],
    }
    with zipfile.ZipFile(path, "w") as zf:
        for name, rows in tables.items():
            zf.writestr(zipfile.ZipInfo(f"{name}.txt", (year, 10, 14, 10, 0, 0)), _tsv(rows))


_CFG = {
    "state": "TX",
    "path": "demo.csv",
    "max_age_hours": 87600,
    "synthetic": True,
    "live_path": "real.csv",
    "live_max_age_hours": 336,      # 14 days, the threshold that tripped in QA
    "columns": {
        "id": "SID", "last": "LAST_NAME", "first": "FIRST_NAME",
        "middle": "MIDDLE_NAME", "suffix": "SUFFIX", "dob": "DOB",
        "street": "STREET", "city": "CITY", "state": "ST", "zip": "ZIP",
        "aliases": "ALIASES", "tier": "RISK_LEVEL", "offense": "OFFENSE",
        "alt_dobs": "ALT_DOBS",
    },
}


def _settings(tmp_path: Path, live: bool = True) -> Settings:
    return Settings(base_dir=tmp_path,
                    live_providers=frozenset({"tx_csv"}) if live else frozenset())


def _patient(first="BOB", last="ZZTESTER", dob=date(1970, 2, 3)) -> NormalizedPatient:
    return NormalizedPatient(first, "", last, "", dob, "", "", "TX", "75001")


def _age_file(path: Path, hours: float) -> None:
    """Set the file time to `hours` ago (the provider judges staleness by file time)."""
    ts = datetime.now(UTC).timestamp() - hours * 3600
    os.utime(path, (ts, ts))


# ===========================================================================
# Texas file expiry – boundary tests
# ===========================================================================

class TestTexasFileExpiryBoundary:
    """Lock down the file-age threshold and the override path used for QA reruns.

    The ProviderUnavailable message already carries the file age and the limit
    ("snapshot is Nh old, limit is Mh"). Only the file name is missing.
    """

    def test_file_older_than_max_age_raises_provider_unavailable(self, tmp_path):
        """An export older than live_max_age_hours (336 h = 14 days) must be rejected."""
        zp, out = tmp_path / "export.zip", tmp_path / "real.csv"
        _make_export(zp, year=2020)           # ~6 years old, well past threshold
        convert(zp, out)

        prov = CsvSnapshotProvider.from_config("tx_csv", _CFG, _settings(tmp_path))
        with pytest.raises(ProviderUnavailable, match=r"h old, limit is 336h"):
            asyncio.run(prov.search(_patient(), "TX"))

    def test_overriding_max_age_allows_stale_file_to_be_searched(self, tmp_path):
        """Manually raising max_age_hours lets QA engineers search the stale export.

        This is the workaround shared with the team after the expiry gap was found.
        """
        zp, out = tmp_path / "export.zip", tmp_path / "real.csv"
        _make_export(zp, year=2020)
        convert(zp, out)

        prov = CsvSnapshotProvider.from_config("tx_csv", _CFG, _settings(tmp_path))
        prov.max_age_hours = 10**7           # effectively disable expiry for this run

        hits = asyncio.run(prov.search(_patient(), "TX"))
        assert len(hits) >= 1, "Expected at least one hit after overriding max_age"
        assert hits[0].registry_id == "TX-00000001"

    def test_fresh_file_does_not_raise(self, tmp_path):
        """An export that is only hours old must still be searched."""
        zp, out = tmp_path / "export.zip", tmp_path / "real.csv"
        _make_export(zp)
        convert(zp, out)
        _age_file(out, 1)

        prov = CsvSnapshotProvider.from_config("tx_csv", _CFG, _settings(tmp_path))
        hits = asyncio.run(prov.search(_patient(), "TX"))
        assert hits, "Fresh file returned no hits"

    def test_file_just_inside_max_age_is_searched(self, tmp_path):
        """One hour under the limit: still available."""
        zp, out = tmp_path / "export.zip", tmp_path / "real.csv"
        _make_export(zp)
        convert(zp, out)
        _age_file(out, _CFG["live_max_age_hours"] - 1)

        prov = CsvSnapshotProvider.from_config("tx_csv", _CFG, _settings(tmp_path))
        assert asyncio.run(prov.search(_patient(), "TX"))

    def test_file_just_past_max_age_is_rejected(self, tmp_path):
        """One hour over the limit: ProviderUnavailable."""
        zp, out = tmp_path / "export.zip", tmp_path / "real.csv"
        _make_export(zp)
        convert(zp, out)
        _age_file(out, _CFG["live_max_age_hours"] + 1)

        prov = CsvSnapshotProvider.from_config("tx_csv", _CFG, _settings(tmp_path))
        with pytest.raises(ProviderUnavailable):
            asyncio.run(prov.search(_patient(), "TX"))


# ===========================================================================
# Importer output – risk level, schema, aliases
# ===========================================================================

class TestTxImporterOutput:
    """Checks on the CSV that convert() writes."""

    def test_not_reported_risk_code_maps_to_not_reported(self, tmp_path):
        """Risk code N in INDV_SOR must come out as 'Not Reported'."""
        zp, out = tmp_path / "export.zip", tmp_path / "tx.csv"
        _make_export(zp)
        convert(zp, out)

        rows = {r["SID"]: r for r in csv.DictReader(out.open(newline=""))}
        # QQSAMPLE (00000002) has risk code N
        assert rows["00000002"]["RISK_LEVEL"] == "Not Reported"

    @pytest.mark.xfail(strict=True, reason="convert() leaves a blank risk code as an empty string")
    def test_blank_risk_code_is_not_reported(self, tmp_path):
        """A blank risk code should also read 'Not Reported', not an empty cell.

        Seen in the console: records with a blank risk level. Remove the xfail
        once the importer maps blank to 'Not Reported'.
        """
        zp, out = tmp_path / "export.zip", tmp_path / "tx.csv"
        _make_export(zp, risk_codes=("1", ""))
        convert(zp, out)

        rows = {r["SID"]: r for r in csv.DictReader(out.open(newline=""))}
        assert rows["00000002"]["RISK_LEVEL"] == "Not Reported"

    def test_exported_csv_columns_match_expected_schema(self, tmp_path):
        """Ensure the CSV produced by convert() has all columns the provider expects.

        Gap noticed: a future schema drift between importer output and the
        provider column-map config would be silent.  This test makes it loud.
        """
        zp, out = tmp_path / "export.zip", tmp_path / "tx.csv"
        _make_export(zp)
        convert(zp, out)

        expected_columns = set(_CFG["columns"].values())
        with out.open(newline="") as f:
            actual_columns = set(csv.DictReader(f).fieldnames or [])

        missing = expected_columns - actual_columns
        assert not missing, f"Columns missing from TX CSV: {missing}"

    def test_alias_column_excludes_primary_name(self, tmp_path):
        """The ALIASES column must not repeat the primary name (console noise)."""
        zp, out = tmp_path / "export.zip", tmp_path / "tx.csv"
        _make_export(zp)
        convert(zp, out)

        rows = {r["SID"]: r for r in csv.DictReader(out.open(newline=""))}
        a = rows["00000001"]
        primary = f"{a['FIRST_NAME']} {a['MIDDLE_NAME']} {a['LAST_NAME']}"
        aliases = a["ALIASES"].split(";") if a["ALIASES"] else []
        assert primary not in aliases, f"Primary name '{primary}' found in ALIASES column"
        assert aliases == ["BOB ZZTESTER"]
