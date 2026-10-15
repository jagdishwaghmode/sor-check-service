"""Low-level text and date helpers. No imports from the rest of the package."""

import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%m-%d-%Y", "%Y%m%d")


def clean(value: str | None) -> str:
    """FR-1.2: strip accents and special characters, trim, uppercase.

    Apostrophes are dropped (O'BRIEN -> OBRIEN), hyphens are kept, other punctuation
    becomes a space.
    """
    if not value:
        return ""
    s = unicodedata.normalize("NFKD", value)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper()
    s = re.sub(r"[’'`]", "", s)
    s = re.sub(r"[^A-Z0-9\s-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_date(value: object) -> date | None:
    """Parse the date formats registries use into a date. Returns None if unparseable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    candidates = [text, text[:10]] if "T" in text else [text]
    for cand in candidates:
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(cand, fmt).date()
            except ValueError:
                continue
    return None


def plausible_dob(d: date) -> bool:
    return date(1900, 1, 1) <= d <= date.today()


def epoch_ms_to_date(ms: int | float | None) -> date | None:
    """ArcGIS REST returns date fields as epoch milliseconds (can be negative)."""
    if ms is None:
        return None
    try:
        return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms)).date()
    except (OverflowError, TypeError, ValueError):
        return None


def split_suffix(last: str, suffix: str = "") -> tuple[str, str]:
    """Move a trailing JR/SR/II... from the last name into the suffix field."""
    parts = last.split(" ")
    if len(parts) > 1 and parts[-1] in SUFFIXES:
        return " ".join(parts[:-1]), suffix or parts[-1]
    return last, suffix


def parse_full_name(full: str) -> tuple[str, str, str]:
    """'ROBERT J DOE' -> (first, middle, last). Input should already be cleaned."""
    parts = clean(full).split(" ")
    parts = [p for p in parts if p]
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return "", "", parts[0]
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def zip5(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")[:5]


_STATE_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA",
    "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL",
    "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA",
    "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
    "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA",
    "WASHINGTON": "WA", "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
    "AMERICAN SAMOA": "AS", "GUAM": "GU", "NORTHERN MARIANA ISLANDS": "MP", "PUERTO RICO": "PR",
    "VIRGIN ISLANDS": "VI", "U S VIRGIN ISLANDS": "VI",
}


def state_code(value: str | None) -> str:
    """'Iowa' / 'IOWA' / 'ia' -> 'IA'. Unknown values become '' so they never fake a mismatch."""
    v = clean(value)
    if len(v) == 2 and v.isalpha():
        return v
    return _STATE_NAMES.get(v, "")
