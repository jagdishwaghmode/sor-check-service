"""API models (pydantic, camelCase to match the PRD JSON) and internal dataclasses."""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from sor.textutil import parse_date, plausible_dob


class OverallStatus(str, Enum):
    CLEAR = "CLEAR"
    FLAGGED = "FLAGGED"
    INCONCLUSIVE = "INCONCLUSIVE"
    ERROR = "ERROR"


class AttemptStatus(str, Enum):
    NO_MATCH = "NO_MATCH"
    MATCH_FOUND = "MATCH_FOUND"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    BAD_RESPONSE = "BAD_RESPONSE"


class Coverage(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NONE = "NONE"


class ReviewStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    CONFIRMED_MATCH = "CONFIRMED_MATCH"
    DISMISSED = "DISMISSED"


# ---------------------------------------------------------------- request (PRD 6.1)


class Address(BaseModel):
    street: str | None = None
    city: str | None = None
    state: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    zip: str | None = None

    @field_validator("street", "city", "state", "zip", mode="before")
    @classmethod
    def _blank_is_missing(cls, v):
        return None if isinstance(v, str) and not v.strip() else v


class Patient(BaseModel):
    patientId: str = Field(min_length=1, max_length=64)
    firstName: str = Field(min_length=1, max_length=80)
    middleName: str | None = Field(default=None, max_length=80)
    lastName: str = Field(min_length=1, max_length=80)
    suffix: str | None = Field(default=None, max_length=10)
    dob: str
    address: Address | None = None

    @field_validator("dob")
    @classmethod
    def _dob_valid(cls, v: str) -> str:
        d = parse_date(v)
        if d is None or not plausible_dob(d):
            raise ValueError("dob must be a real past date, e.g. 1980-05-15 or 05/15/1980")
        return v


class CheckRequest(BaseModel):
    requestId: str | None = Field(default=None, max_length=64)
    facilityState: str = Field(pattern=r"^[A-Za-z]{2}$")
    residenceState: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    patient: Patient

    @field_validator("residenceState", mode="before")
    @classmethod
    def _blank_state_is_missing(cls, v):
        return None if isinstance(v, str) and not v.strip() else v


# ---------------------------------------------------------------- response (PRD 6.2)


class ScoreBreakdown(BaseModel):
    name: float
    dob: float | None
    location: float | None


class OffenderOut(BaseModel):
    registryId: str
    fullName: str
    dob: str | None
    matchConfidence: float
    band: str
    scoreBreakdown: ScoreBreakdown
    capped: bool = False
    tier: str | None = None
    offenseDetails: str | None = None
    matchedAlias: str | None = None  # the alias that matched, when not the registered name
    matchedAlternateDob: bool = False  # the patient's birth date equals an alternate, not the primary
    detailUrl: str | None = None
    state: str
    provider: str


class AttemptOut(BaseModel):
    provider: str
    kind: str
    status: AttemptStatus
    latencyMs: int
    error: str | None = None


class ProviderResultOut(BaseModel):
    state: str
    providerUsed: str | None
    status: str  # MATCH_FOUND | NO_MATCH | UNCHECKED
    latencyMs: int
    dataAsOf: str | None = None
    synthetic: bool = False
    attempts: list[AttemptOut]
    offendersFound: list[OffenderOut]
    sameNameOtherDob: list[OffenderOut] = []  # same name, birth date clearly different: not a match
    sameNameOtherDobTotal: int = 0


class CheckResponse(BaseModel):
    requestId: str
    timestamp: str
    status: OverallStatus
    compositeMatchScore: float
    coverage: Coverage
    statesRequested: list[str]
    statesChecked: list[str]
    reasons: list[str]
    reviewStatus: ReviewStatus
    providerResults: list[ProviderResultOut]
    weights: dict[str, float]
    thresholds: dict[str, float]
    disclaimer: str


# ---------------------------------------------------------------- internal


@dataclass
class NormalizedPatient:
    first: str
    middle: str
    last: str
    suffix: str
    dob: date
    street: str
    city: str
    state: str
    zip: str


@dataclass
class RegistryRecord:
    registry_id: str
    first: str
    middle: str
    last: str
    suffix: str
    aliases: list[tuple[str, str, str]] = field(default_factory=list)
    dob: date | None = None
    alt_dobs: list[date] = field(default_factory=list)  # other birth dates the source lists for this person
    addresses: list[tuple[str, str, str, str]] = field(default_factory=list)  # street, city, state, zip
    tier: str | None = None
    offense: str | None = None
    detail_url: str | None = None
    source_state: str = ""
    source_provider: str = ""
    source_as_of: datetime | None = None

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first, self.middle, self.last, self.suffix) if p)
