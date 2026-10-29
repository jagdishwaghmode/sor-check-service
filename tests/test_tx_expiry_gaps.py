"""QA coverage: Texas DPS file expiry edge-cases and console gap findings.

Day-2 follow-up after sharing the gap list with the team:

1. Expiry boundary tests: confirm the exact age threshold that flips
   CsvSnapshotProvider from available → ProviderUnavailable, and verify the
   max_age_hours cap can be overridden for manual QA runs.

2. Console gap reproduction: a structured, minimal test that exercises the
   ambiguous-state code path noticed in the console during manual testing.

All tests are fully offline (no network, uses tmp_path / mock data).
"""

import asyncio
import csv
import zipfile
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import pytest

from sor.importers.tx_dps import convert
from sor.models import NormalizedPatient
from sor.providers.base import ProviderUnavailable
from sor.providers.csv_snapshot import CsvSnapshotProvider
from sor.config import Settings


# ---------------------------------------------------------------------------
# shared helpers (duplicated from test_tx_import to keep this file standalone)
# ---------------------------------------------------------------------------

def _tsv(rows: list[list[str]]) -> bytes:
    return "".join("\t".join(r) + "\r\n" for r in rows).encode("latin-1")


def _make_export(path: Path, year: int = 2026) -> None:
    """Write a minimal but valid Texas DPS zip with two synthetic people."""
    tables = {
        "INDV": [["1", "00000001"], ["2", "00000002"]],
        "PERSON": [["1", "10", "M", "", "", "", "", "", ""],
                   ["2", "20", "F", "", "", "", "", "", ""]],
        "NAME": [
            ["1", "10", "B", "ZZTESTER,ROBERT JAMES JR", "ZZTESTER", "ROBERT JAMES JR"],
            ["2", "10", "S", "ZZTESTER,BOB", "ZZTESTER", "BOB"],
            ["3", "20", "B", "QQSAMPLE,ANNA", "QQSAMPLE", "ANNA"],
        ],
        "BRTHDATE": [["1", "10", "B", "1970-02-03"], ["2", "20", "B", "1985-06-07"]],
        "INDV_SOR": [["1", "00000001", "1", "", "", "LIF", "", "A"],
                     ["2", "00000002", "N", "", "", "LIF", "", "A"]],
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
    "live_max_age_hours": 336,      # 14 days — the threshold that tripped in QA
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


# ===========================================================================
# Texas file expiry – boundary tests
# ===========================================================================

class TestTexasFileExpiryBoundary:
    """Reproduce and lock down the file-age threshold observed during QA.

    Gap logged: during manual runs against a 2020 export the console showed
    ProviderUnavailable but the error message gave no indication of *which*
    file was stale or by how many days.  These tests cover the boundary and
    the override path used for manual QA investigation.
    """

    def test_file_older_than_max_age_raises_provider_unavailable(self, tmp_path):
        """An export older than live_max_age_hours (336 h = 14 days) must be rejected."""
        zp, out = tmp_path / "export.zip", tmp_path / "real.csv"
        _make_export(zp, year=2020)           # ~6 years old — well past threshold
        convert(zp, out)

        prov = CsvSnapshotProvider.from_config("tx_csv", _CFG, _settings(tmp_path))
        with pytest.raises(ProviderUnavailable):
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
        """A current-year export (within 14 days) must not raise ProviderUnavailable."""
        current_year = datetime.now(timezone.utc).year
        zp, out = tmp_path / "export.zip", tmp_path / "real.csv"
        _make_export(zp, year=current_year)
        convert(zp, out)

        # Back-date mtime to simulate a file written "today" (within threshold)
        now_ts = datetime.now(timezone.utc).timestamp()
        import os
        os.utime(out, (now_ts, now_ts))

        prov = CsvSnapshotProvider.from_config("tx_csv", _CFG, _settings(tmp_path))
        # Should not raise — file is fresh
        try:
            hits = asyncio.run(prov.search(_patient(), "TX"))
        except ProviderUnavailable:
            pytest.fail("Fresh file was incorrectly rejected by the expiry check")

    def test_file_at_exactly_max_age_boundary(self, tmp_path):
        """File aged exactly at max_age_hours should be treated as expired (exclusive bound)."""
        zp, out = tmp_path / "export.zip", tmp_path / "real.csv"
        _make_export(zp, year=2026)
        convert(zp, out)

        # Force the mtime to exactly max_age_hours seconds in the past
        max_age_hours = _CFG["live_max_age_hours"]
        expired_ts = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)
        import os
        os.utime(out, (expired_ts, expired_ts))

        prov = CsvSnapshotProvider.from_config("tx_csv", _CFG, _settings(tmp_path))
        with pytest.raises(ProviderUnavailable):
            asyncio.run(prov.search(_patient(), "TX"))


# ===========================================================================
# Console gap – ambiguous / missing state in output
# ===========================================================================

class TestConsoleGapFindings:
    """Minimal reproducers for gaps noticed in the console during Iowa/TX runs.

    Gap 1: When a provider returns zero records the console printed no line
    for that state, making it hard to confirm coverage.  Covered by aggregate
    tests in test_unit.py — referenced here for traceability.

    Gap 2: A RISK_LEVEL value absent from the CSV (empty string) was silently
    mapped to "" instead of "Not Reported".  The importer should normalise it.
    """

    def test_missing_risk_level_is_normalised_to_not_reported(self, tmp_path):
        """An empty RISK_LEVEL cell in the TX CSV must become 'Not Reported'.

        Observed in the console: records for QQSAMPLE showed blank risk level.
        The importer's convert() already handles INDV_SOR where SOR_NBR != '1'
        — this test locks that down explicitly.
        """
        zp, out = tmp_path / "export.zip", tmp_path / "tx.csv"
        _make_export(zp, year=2026)
        convert(zp, out)

        rows = {r["SID"]: r for r in csv.DictReader(out.open(newline=""))}
        # QQSAMPLE (00000002) has SOR_NBR != "1" → risk should be "Not Reported"
        assert rows["00000002"]["RISK_LEVEL"] == "Not Reported", (
            f"Empty RISK_LEVEL was not normalised: got '{rows['00000002']['RISK_LEVEL']}'"
        )

    def test_exported_csv_columns_match_expected_schema(self, tmp_path):
        """Ensure the CSV produced by convert() has all columns the provider expects.

        Gap noticed: a future schema drift between importer output and the
        provider column-map config would be silent.  This test makes it loud.
        """
        zp, out = tmp_path / "export.zip", tmp_path / "tx.csv"
        _make_export(zp, year=2026)
        convert(zp, out)

        expected_columns = {
            _CFG["columns"][k]
            for k in _CFG["columns"]
        }
        with out.open(newline="") as f:
            actual_columns = set(csv.DictReader(f).fieldnames or [])

        missing = expected_columns - actual_columns
        assert not missing, f"Columns missing from TX CSV: {missing}"

    def test_alias_column_excludes_primary_name(self, tmp_path):
        """The ALIASES column must not repeat the primary name — noted as console noise."""
        zp, out = tmp_path / "export.zip", tmp_path / "tx.csv"
        _make_export(zp, year=2026)
        convert(zp, out)

        rows = {r["SID"]: r for r in csv.DictReader(out.open(newline=""))}
        a = rows["00000001"]
        primary = f"{a['FIRST_NAME']} {a['LAST_NAME']}"
        aliases = a["ALIASES"].split("|") if a["ALIASES"] else []
        assert primary not in aliases, (
            f"Primary name '{primary}' found in ALIASES column — "
            "this was the console noise reported on 2026-10-28"
        )
