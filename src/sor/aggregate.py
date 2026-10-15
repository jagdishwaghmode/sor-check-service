"""Turn per-state outcomes into one verdict. This is the safety logic, see the private plan 4.4.

Rules (worst result wins, never CLEAR when something was not checked):
  all states unchecked                      -> ERROR
  any record >= flag threshold              -> FLAGGED
  else any record in the middle band        -> INCONCLUSIVE
  else some state unchecked                 -> INCONCLUSIVE (PARTIAL_COVERAGE)
  else                                      -> CLEAR
"""

from dataclasses import dataclass, field

from sor.matching import ScoredRecord
from sor.models import AttemptStatus, Coverage, OverallStatus, ReviewStatus

_OK = {AttemptStatus.NO_MATCH, AttemptStatus.MATCH_FOUND}


@dataclass
class Attempt:
    provider: str
    kind: str
    status: AttemptStatus
    latency_ms: int
    error: str | None = None


@dataclass
class StateOutcome:
    state: str
    checked: bool
    provider_used: str | None
    attempts: list[Attempt]
    scored: list[ScoredRecord]
    latency_ms: int
    data_as_of: str | None = None
    synthetic: bool = False
    name_only: list[ScoredRecord] = field(default_factory=list)  # same name, clearly another person


@dataclass
class Verdict:
    status: OverallStatus
    score: float
    coverage: Coverage
    reasons: list[str] = field(default_factory=list)
    review: ReviewStatus = ReviewStatus.NOT_REQUIRED


def aggregate(outcomes: list[StateOutcome]) -> Verdict:
    unchecked = [o for o in outcomes if not o.checked]
    scored = [s for o in outcomes for s in o.scored]
    reasons: list[str] = []

    if any(o.checked and any(a.status not in _OK for a in o.attempts) for o in outcomes):
        reasons.append("PRIMARY_FAILED_FALLBACK_USED")
    if any(o.synthetic for o in outcomes):
        reasons.append("SYNTHETIC_DATA_SOURCE")
    if any(s.capped for s in scored):
        reasons.append("DOB_MISSING_AT_SOURCE")
    if any(o.name_only for o in outcomes):
        reasons.append("SAME_NAME_DIFFERENT_DOB")  # informational, not a match

    if len(unchecked) == len(outcomes):
        return Verdict(OverallStatus.ERROR, 0.0, Coverage.NONE, reasons + ["ALL_SOURCES_FAILED"])

    coverage = Coverage.PARTIAL if unchecked else Coverage.COMPLETE
    if unchecked:
        reasons.append("PARTIAL_COVERAGE")
    top = max((s.score for s in scored), default=0.0)

    if any(s.band == "FLAGGED" for s in scored):
        return Verdict(OverallStatus.FLAGGED, top, coverage, reasons, ReviewStatus.PENDING)
    if scored:
        return Verdict(OverallStatus.INCONCLUSIVE, top, coverage,
                       reasons + ["MID_CONFIDENCE_MATCH"], ReviewStatus.PENDING)
    if unchecked:
        return Verdict(OverallStatus.INCONCLUSIVE, 0.0, coverage, reasons, ReviewStatus.PENDING)
    return Verdict(OverallStatus.CLEAR, 0.0, coverage, reasons)
