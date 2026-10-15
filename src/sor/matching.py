"""Matching engine (FR-4). One provider-agnostic scorer: adapters never score.

Composite = w_name*Name + w_dob*DOB + w_loc*Location. A component that is missing on either
side is dropped and the remaining weights are renormalized.

Guards (fail safe, see the private plan section 5):
- name below `name_floor` can never be rescued by DOB or location, record is dropped
- birth date known on both sides and different: only a near-exact name stays (same name, other DOB
  is worth a human look, a merely similar name is noise). Found live: 17 of 19 rows for a common
  surname were this noise.
- if the registry record has no DOB, the result is capped at INCONCLUSIVE (needs a human)
"""

from dataclasses import dataclass

from rapidfuzz.distance import JaroWinkler

from sor.models import NormalizedPatient, RegistryRecord

_NICKNAME_GROUPS = [
    {"ROBERT", "BOB", "ROB", "BOBBY", "ROBBIE", "BERT"},
    {"WILLIAM", "BILL", "WILL", "BILLY", "WILLY"},
    {"JAMES", "JIM", "JIMMY", "JAMIE"},
    {"JOHN", "JACK", "JOHNNY", "JON"},
    {"RICHARD", "RICK", "DICK", "RICH", "RICKY"},
    {"MICHAEL", "MIKE", "MICKEY"},
    {"THOMAS", "TOM", "TOMMY"},
    {"CHARLES", "CHUCK", "CHARLIE", "CHAS"},
    {"JOSEPH", "JOE", "JOEY"},
    {"EDWARD", "ED", "EDDIE", "TED", "NED"},
    {"ELIZABETH", "LIZ", "BETH", "BETTY", "LIZZIE", "ELIZA"},
    {"MARGARET", "MAGGIE", "PEGGY", "MEG", "MARGE"},
    {"KATHERINE", "KATE", "KATHY", "KATIE", "KAT"},
    {"JENNIFER", "JEN", "JENNY"},
    {"PATRICIA", "PAT", "PATTY", "TRISH"},
    {"DANIEL", "DAN", "DANNY"},
    {"DAVID", "DAVE", "DAVEY"},
    {"ANTHONY", "TONY"},
    {"STEVEN", "STEPHEN", "STEVE"},
    {"CHRISTOPHER", "CHRIS"},
    {"MATTHEW", "MATT"},
    {"ANDREW", "ANDY", "DREW"},
    {"SAMUEL", "SAM", "SAMMY"},
    {"ALEXANDER", "ALEX"},
    {"NICHOLAS", "NICK"},
    {"BENJAMIN", "BEN"},
    {"GREGORY", "GREG"},
    {"TIMOTHY", "TIM"},
    {"DONALD", "DON", "DONNY"},
    {"RONALD", "RON", "RONNY"},
    {"KENNETH", "KEN", "KENNY"},
    {"LAWRENCE", "LARRY"},
    {"JEFFREY", "JEFF"},
]
_NICK_INDEX: dict[str, int] = {}
for _i, _group in enumerate(_NICKNAME_GROUPS):
    for _n in _group:
        _NICK_INDEX[_n] = _i


@dataclass(frozen=True)
class MatchConfig:
    w_name: float = 0.60
    w_dob: float = 0.30
    w_loc: float = 0.10
    clear_below: float = 0.40
    flag_at: float = 0.75
    name_floor: float = 0.60
    dob_mismatch_min_name: float = 0.95  # a different birth date only keeps a near-exact name


@dataclass
class ScoredRecord:
    record: RegistryRecord
    name: float
    dob: float | None
    location: float | None
    score: float
    band: str  # FLAGGED | INCONCLUSIVE
    capped: bool = False
    matched_alias: str | None = None  # set when an alias, not the registered name, gave the name score


def first_name_variants(name: str) -> set[str]:
    """The name itself plus every nickname in its group (BOB -> ROBERT, ROB, BOBBY ...)."""
    group = _NICK_INDEX.get(name)
    return {name} | (set(_NICKNAME_GROUPS[group]) if group is not None else set())


def _jw(a: str, b: str) -> float:
    return JaroWinkler.similarity(a, b)


def _variants(s: str) -> set[str]:
    return {v for v in (s, s.replace("-", " "), s.replace("-", "")) if v}


def _same_nickname_group(a: str, b: str) -> bool:
    ia, ib = _NICK_INDEX.get(a), _NICK_INDEX.get(b)
    return ia is not None and ia == ib


def last_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    best = max(_jw(x, y) for x in _variants(a) for y in _variants(b))
    ta, tb = a.replace("-", " ").split(), b.replace("-", " ").split()
    if len(ta) != len(tb):  # one name is a part of the other (GARCIA vs GARCIA LOPEZ)
        best = max(best, 0.9 * max(_jw(x, y) for x in ta for y in tb))
    return best


def first_similarity(a: str, b: str) -> float | None:
    if not a or not b:
        return None
    if a == b:
        return 1.0
    s = _jw(a, b)
    if _same_nickname_group(a, b):
        s = max(s, 0.95)
    if len(a) == 1 or len(b) == 1:  # initial only
        s = max(s, 0.7 if a[0] == b[0] else 0.0)
    return s


def middle_similarity(a: str, b: str) -> float | None:
    if not a or not b:
        return None
    if a == b:
        return 1.0
    if a[0] == b[0] and (len(a) == 1 or len(b) == 1):
        return 0.9
    return _jw(a, b)


def _combine(pf: str, pm: str, pl: str, cf: str, cm: str, cl: str) -> float:
    parts: list[tuple[float, float]] = [(0.50, last_similarity(pl, cl))]
    fs = first_similarity(pf, cf)
    if fs is not None:
        parts.append((0.35, fs))
    ms = middle_similarity(pm, cm)
    if ms is not None:
        parts.append((0.15, ms))
    total = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / total


def name_match(p: NormalizedPatient, r: RegistryRecord) -> tuple[float, str | None]:
    """Best name score and, if an alias beat the registered name, which alias."""
    best, via = 0.0, None
    for i, (cf, cm, cl) in enumerate([(r.first, r.middle, r.last)] + list(r.aliases)):
        s = max(_combine(p.first, p.middle, p.last, cf, cm, cl),
                0.9 * _combine(p.first, p.middle, p.last, cl, cm, cf))  # second: first/last swapped
        if s > best:
            best, via = s, (None if i == 0 else " ".join(x for x in (cf, cm, cl) if x))
    if p.suffix and r.suffix and p.suffix != r.suffix:
        best = max(0.0, best - 0.10)
    return best, via


def name_score(p: NormalizedPatient, r: RegistryRecord) -> float:
    return name_match(p, r)[0]


def dob_score(p: NormalizedPatient, r: RegistryRecord) -> float | None:
    if r.dob is None:
        return None
    return 1.0 if p.dob == r.dob else 0.0


def location_score(p: NormalizedPatient, r: RegistryRecord) -> float | None:
    if not (p.zip or p.city or p.state) or not r.addresses:
        return None
    best = 0.0
    for _street, city, state, zp in r.addresses:
        if p.zip and zp and p.zip == zp:
            best = max(best, 1.0)
        elif p.zip and zp and p.zip[:3] == zp[:3]:
            best = max(best, 0.6)
        if p.city and city and p.city == city:
            best = max(best, 0.5)
        if p.state and state and p.state == state:
            best = max(best, 0.2)
    return best


def score_record(p: NormalizedPatient, r: RegistryRecord, cfg: MatchConfig) -> ScoredRecord:
    (n, via), d, loc = name_match(p, r), dob_score(p, r), location_score(p, r)
    parts = [(cfg.w_name, n)]
    if d is not None:
        parts.append((cfg.w_dob, d))
    if loc is not None:
        parts.append((cfg.w_loc, loc))
    total = sum(w for w, _ in parts)
    score = sum(w * v for w, v in parts) / total
    capped = False
    if n < cfg.name_floor:
        score = min(score, cfg.clear_below - 0.01)
    if d == 0.0 and n < cfg.dob_mismatch_min_name:
        score = min(score, cfg.clear_below - 0.01)
    if d is None and score >= cfg.flag_at:
        score, capped = cfg.flag_at - 0.01, True
    band = "FLAGGED" if score >= cfg.flag_at else ("INCONCLUSIVE" if score >= cfg.clear_below else "NONE")
    return ScoredRecord(r, round(n, 3), None if d is None else round(d, 3),
                        None if loc is None else round(loc, 3), round(score, 3), band, capped, via)


def score_records(
    p: NormalizedPatient, records: list[RegistryRecord], cfg: MatchConfig
) -> list[ScoredRecord]:
    """Score all candidates, keep only those at or above the clear threshold, best first."""
    scored = [score_record(p, r, cfg) for r in records]
    kept = [s for s in scored if s.band != "NONE"]
    return sorted(kept, key=lambda s: s.score, reverse=True)
