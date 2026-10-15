"""Regenerate the synthetic CSV snapshot files from sor.mock.data.

    .venv/bin/python scripts/make_mock_data.py

TX and FL use different column names, casing and date formats on purpose, to prove the
column map in config/states.yaml does the work.
"""

import csv
from datetime import datetime
from pathlib import Path

from sor.mock.data import people_for

DATA = Path(__file__).resolve().parents[1] / "data"


def write_tx() -> None:
    cols = ["SID", "LAST_NAME", "FIRST_NAME", "MIDDLE_NAME", "SUFFIX", "DOB", "STREET", "CITY",
            "ST", "ZIP", "ALIASES", "RISK_LEVEL", "OFFENSE"]
    with (DATA / "tx_registry.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for p in people_for("TX"):
            w.writerow([p["id"], p["last"], p["first"], p["middle"], p["suffix"], p["dob"],
                        p["street"], p["city"], "TX", p["zip"], ";".join(p["aliases"]),
                        p["tier"], p["offense"]])


def write_fl() -> None:
    cols = ["Offender Number", "Last Name", "First Name", "Middle Name", "Suffix", "Birth Date",
            "Address", "City", "State", "Zip Code", "Aliases", "Designation", "Offense"]
    with (DATA / "fl_registry.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for p in people_for("FL"):
            mdy = datetime.strptime(p["dob"], "%Y-%m-%d").strftime("%m/%d/%Y")
            w.writerow([p["id"], p["last"].title(), p["first"].title(), p["middle"].title(),
                        p["suffix"], mdy, p["street"].title(), p["city"].title(), "FL",
                        p["zip"], ";".join(a.title() for a in p["aliases"]), p["tier"],
                        p["offense"]])


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    write_tx()
    write_fl()
    print("wrote", sorted(f.name for f in DATA.glob("*.csv")))
