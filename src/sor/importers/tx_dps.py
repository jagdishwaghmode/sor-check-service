"""Flatten the Texas DPS public registry export into one CSV the generic csv provider can read.

The export (https://sor.dps.texas.gov/Home/Export, free, needs a TxDPS public account, refreshed
twice a week) is a zip of tab-delimited database tables, not a flat file. Layout learned from
its own Layout.pdf and DisplayRecord.sql:

- INDV        IND_IDN -> DPS_NBR (the state id we use as registry id)
- PERSON      PER_IDN -> IND_IDN
- NAME        per PER_IDN, TYP_COD 'B' is the primary name, other codes (S, H) are aliases.
              FNA_TXT holds "FIRST MIDDLE... [SUFFIX]", LNA_TXT the surname (never has spaces)
- BRTHDATE    per PER_IDN, TYP_COD 'B' is the primary birth date (other codes are alternates, not used)
- INDV_SOR    RSK_COD risk level (1 High, 2 Moderate, 3 Low, C civil commitment, N not reported, U unknown)
- Address     per IND_IDN, about 60 percent have street, city and zip filled

Untrusted input: members are read by exact name with zipfile (never extracted to disk).

    python -m sor.importers.tx_dps path/to/export.zip data/tx_registry_real.csv
"""

import argparse
import csv
import os
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}
RISK = {"1": "High", "2": "Moderate", "3": "Low", "C": "Civil Commitment", "N": "Not Reported", "U": "Unknown"}
FIELDS = ["SID", "LAST_NAME", "FIRST_NAME", "MIDDLE_NAME", "SUFFIX", "DOB", "STREET", "CITY", "ST", "ZIP",
          "ALIASES", "RISK_LEVEL", "OFFENSE"]
_NEEDED = ("INDV", "PERSON", "NAME", "BRTHDATE", "INDV_SOR", "Address")
_MAX_MEMBER_BYTES = 200 * 1024 * 1024  # a zip bomb guard, the real biggest table is about 22 MB


def _table(zf: zipfile.ZipFile, name: str):
    member = f"{name}.txt"
    try:
        info = zf.getinfo(member)
    except KeyError as e:
        raise ValueError(f"export is missing {member}, layout changed?") from e
    if info.file_size > _MAX_MEMBER_BYTES:
        raise ValueError(f"{member} is unexpectedly large ({info.file_size} bytes)")
    with zf.open(info) as fh:
        for raw in fh:
            yield raw.decode("latin-1").rstrip("\r\n").split("\t")


def split_given(fna: str) -> tuple[str, str, str]:
    """'JOHN MICHAEL JR' -> ('JOHN', 'MICHAEL', 'JR'). The first word is the first name."""
    parts = fna.upper().split()
    suffix = parts.pop() if len(parts) > 1 and parts[-1] in SUFFIXES else ""
    if not parts:
        return "", "", suffix
    return parts[0], " ".join(parts[1:]), suffix


def _street(r: list[str]) -> str:
    unit = f"{r[4].strip()} {r[5].strip()}".strip()
    return " ".join(x for x in (r[2].strip(), r[3].strip(), unit) if x)


def _state(code: str) -> str:
    code = code.strip().upper()
    return code if len(code) == 2 and code.isalpha() and code != "YY" else ""  # YY means unknown


def convert(zip_path: Path, out_path: Path) -> dict:
    """Write the flat CSV (atomically) and return counts. Sets the file time to the export date."""
    with zipfile.ZipFile(zip_path) as zf:
        for name in _NEEDED:
            zf.getinfo(f"{name}.txt")  # fail early with a clear message if a table is missing
        dps = {r[0]: r[1].strip() for r in _table(zf, "INDV") if len(r) >= 2}
        person_ind = {r[1]: r[0] for r in _table(zf, "PERSON") if len(r) >= 2}
        risk = {r[0]: r[2].strip() for r in _table(zf, "INDV_SOR") if len(r) >= 3}

        primary: dict[str, tuple[str, str, str, str]] = {}  # ind -> first, middle, last, suffix
        aliases: dict[str, list[str]] = defaultdict(list)
        for r in _table(zf, "NAME"):
            ind = person_ind.get(r[1]) if len(r) >= 6 else None
            if ind is None or not r[4].strip():
                continue
            first, middle, suffix = split_given(r[5])
            if r[2] == "B":
                primary[ind] = (first, middle, r[4].strip().upper(), suffix)
            else:
                aliases[ind].append(" ".join(x for x in (first, middle, r[4].strip().upper()) if x))

        dob = {person_ind[r[1]]: r[3].strip() for r in _table(zf, "BRTHDATE")
               if len(r) >= 4 and r[2] == "B" and r[1] in person_ind}

        address: dict[str, list[str]] = {}
        for r in _table(zf, "Address"):
            if len(r) < 12:
                continue
            filled = bool(r[3].strip() and r[8].strip())
            if r[1] not in address or (filled and not (address[r[1]][3].strip() and address[r[1]][8].strip())):
                address[r[1]] = r  # keep the first row that has street and zip

        as_of = datetime(*zf.getinfo("INDV_SOR.txt").date_time)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out_path.parent, suffix=".tmp")
    written = 0
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDS)
        for ind, (first, middle, last, suffix) in primary.items():
            sid = dps.get(ind, "")
            if not sid:
                continue
            a = address.get(ind)
            own = f"{first} {middle} {last}".replace("  ", " ")
            extra = sorted({x.replace("  ", " ") for x in aliases.get(ind, [])} - {own})
            writer.writerow([
                sid, last, first, middle, suffix, dob.get(ind, ""),
                _street(a) if a else "", a[6].strip().upper() if a else "", _state(a[7]) if a else "",
                a[8].strip() if a else "", ";".join(extra), RISK.get(risk.get(ind, ""), ""), "",
            ])
            written += 1
    os.replace(tmp, out_path)
    ts = as_of.timestamp()
    os.utime(out_path, (ts, ts))  # staleness is judged by the export date, not by when we converted it
    return {"people": written, "with_address": sum(1 for i in primary if i in address),
            "with_dob": sum(1 for i in primary if dob.get(i)), "export_date": as_of.date().isoformat()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("zip_path", type=Path)
    ap.add_argument("out_path", type=Path)
    args = ap.parse_args()
    print(convert(args.zip_path, args.out_path))


if __name__ == "__main__":
    main()
