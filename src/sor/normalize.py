"""FR-1.2 input sanitization: PatientQuery -> NormalizedPatient."""

from sor.models import NormalizedPatient, Patient
from sor.textutil import clean, parse_date, split_suffix, zip5


def normalize_patient(p: Patient) -> NormalizedPatient:
    dob = parse_date(p.dob) if p.dob else None  # birth date is optional, validation rejects bad ones
    last, suffix = split_suffix(clean(p.lastName), clean(p.suffix))
    addr = p.address
    return NormalizedPatient(
        first=clean(p.firstName),
        middle=clean(p.middleName),
        last=last,
        suffix=suffix,
        dob=dob,
        street=clean(addr.street) if addr else "",
        city=clean(addr.city) if addr else "",
        state=clean(addr.state) if addr else "",
        zip=zip5(addr.zip) if addr else "",
    )
