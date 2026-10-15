"""Orchestrator: normalize -> plan states -> run each state's provider chain -> score -> aggregate.

Provider failures never escape. They become attempts, and the aggregate turns "could not check"
into PARTIAL/ERROR, never CLEAR.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

from sor import chaos
from sor.aggregate import Attempt, StateOutcome, aggregate
from sor.audit import AuditLog, mask_name
from sor.breaker import CircuitBreaker
from sor.config import AppConfig
from sor.matching import split_candidates
from sor.models import (
    AttemptOut,
    AttemptStatus,
    CheckRequest,
    CheckResponse,
    NormalizedPatient,
    OffenderOut,
    ProviderResultOut,
    ScoreBreakdown,
)
from sor.normalize import normalize_patient
from sor.providers.base import (
    Provider,
    ProviderBadResponse,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
)

log = logging.getLogger("sor.engine")

DISCLAIMER = (
    "Public registry data can be incomplete or out of date, and a name match is not proof of "
    "identity. This result is advisory. Verify with the state agency before taking any action."
)


class Engine:
    def __init__(self, cfg: AppConfig, providers: dict[str, Provider], audit: AuditLog,
                 chaos_enabled: bool = False, skip_synthetic: bool = False):
        self.cfg = cfg
        self.providers = providers
        self.audit = audit
        self.chaos_enabled = chaos_enabled
        self.skip_synthetic = skip_synthetic
        self.timeout_s = cfg.timeout_s
        # a provider block can set its own timeout_ms (Iowa needs more: several pages at ~1s each)
        self.timeout_overrides = {
            n: cfg.providers[n]["timeout_ms"] / 1000
            for n in providers if "timeout_ms" in cfg.providers.get(n, {})
        }
        self.breakers = {
            n: CircuitBreaker(cfg.breaker_threshold, cfg.breaker_cooldown_s) for n in providers
        }

    def reset_breakers(self) -> None:
        self.breakers = {
            n: CircuitBreaker(self.cfg.breaker_threshold, self.cfg.breaker_cooldown_s)
            for n in self.providers
        }

    # ------------------------------------------------------------------ one provider call

    def _timeout_for(self, name: str) -> float:
        return self.timeout_overrides.get(name, self.timeout_s)

    async def _call(self, prov: Provider, patient: NormalizedPatient, state: str):
        timeout = self._timeout_for(prov.name)

        async def guarded():
            if self.chaos_enabled:  # dev-only failure injection, inside the timeout on purpose
                mode = chaos.get(prov.name)
                if mode == "down":
                    raise ProviderUnavailable("chaos: provider down")
                if mode == "bad":
                    raise ProviderBadResponse("chaos: garbage response")
                if mode == "slow":
                    await asyncio.sleep(timeout + 1)
            return await prov.search(patient, state)

        try:
            return await asyncio.wait_for(guarded(), timeout=timeout)
        except asyncio.TimeoutError as e:
            raise ProviderTimeout(f"no answer within {timeout:.1f}s") from e

    # ------------------------------------------------------------------ one state

    async def _run_state(self, state: str, patient: NormalizedPatient) -> StateOutcome:
        t0 = time.perf_counter()
        attempts: list[Attempt] = []
        for name in self.cfg.chain_for(state):
            prov, breaker = self.providers[name], self.breakers[name]
            if self.skip_synthetic and prov.synthetic:  # real-data-only mode: never use fake sources
                attempts.append(Attempt(name, prov.kind, AttemptStatus.UNAVAILABLE, 0,
                                        "mock source skipped (real data only mode)"))
                continue
            if not breaker.allow():
                attempts.append(Attempt(name, prov.kind, AttemptStatus.CIRCUIT_OPEN, 0, "circuit open"))
                continue
            a0 = time.perf_counter()
            try:
                records = await self._call(prov, patient, state)
            except ProviderError as e:
                breaker.record_failure() if e.counts_for_breaker else breaker.record_neutral()
                attempts.append(Attempt(name, prov.kind, e.status, _ms(a0), str(e)))
                continue
            except Exception as e:  # adapter bug: treat as a failed source, never crash the check
                log.exception("unexpected error in provider %s", name)
                breaker.record_failure()
                attempts.append(Attempt(name, prov.kind, AttemptStatus.BAD_RESPONSE, _ms(a0),
                                        f"unexpected {type(e).__name__}"))
                continue
            breaker.record_success()
            scored, name_only = split_candidates(patient, records, self.cfg.match)
            status = AttemptStatus.MATCH_FOUND if scored else AttemptStatus.NO_MATCH
            attempts.append(Attempt(name, prov.kind, status, _ms(a0)))
            as_of = prov.data_as_of()
            return StateOutcome(state, True, name, attempts, scored, _ms(t0),
                                as_of.isoformat(timespec="seconds") if as_of else None,
                                prov.synthetic, name_only)
        return StateOutcome(state, False, None, attempts, [], _ms(t0))

    # ------------------------------------------------------------------ whole check

    async def check(self, req: CheckRequest, actor_id: str) -> CheckResponse:
        t0 = time.perf_counter()
        request_id = req.requestId or f"req_{uuid.uuid4().hex[:12]}"
        patient = normalize_patient(req.patient)
        facility = req.facilityState.upper()
        home = req.residenceState or (req.patient.address.state if req.patient.address else None)
        states = list(dict.fromkeys([facility, (home or facility).upper()]))

        outcomes = await asyncio.gather(*(self._run_state(s, patient) for s in states))
        verdict = aggregate(list(outcomes))
        mc = self.cfg.match

        response = CheckResponse(
            requestId=request_id,
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            status=verdict.status,
            compositeMatchScore=verdict.score,
            coverage=verdict.coverage,
            statesRequested=states,
            statesChecked=[o.state for o in outcomes if o.checked],
            reasons=verdict.reasons,
            reviewStatus=verdict.review,
            providerResults=[self._result_out(o) for o in outcomes],
            weights={"name": mc.w_name, "dob": mc.w_dob, "location": mc.w_loc},
            thresholds={"clearBelow": mc.clear_below, "flagAtOrAbove": mc.flag_at},
            disclaimer=DISCLAIMER,
        )

        # FR-5: no verdict leaves the service without an audit record. If this raises, the API
        # answers 503 and the admission flow can retry.
        await asyncio.to_thread(
            self.audit.append,
            request_id=request_id, actor_id=actor_id, patient_ref=req.patient.patientId,
            event_type="CHECK_COMPLETED", states=states,
            providers=[a.provider for o in outcomes for a in o.attempts],
            status=verdict.status.value, latency_ms=_ms(t0),
            summary={
                "patient": f"{mask_name(patient.first, patient.last)} ({patient.dob.year})",
                "coverage": verdict.coverage.value, "reasons": verdict.reasons,
                "score": verdict.score,
            },
            payload={"request": req.model_dump(), "response": response.model_dump(mode="json")},
        )
        return response

    @staticmethod
    def _result_out(o: StateOutcome) -> ProviderResultOut:
        def out(s) -> OffenderOut:
            return OffenderOut(
                registryId=s.record.registry_id, fullName=s.record.full_name,
                dob=s.record.dob.isoformat() if s.record.dob else None,
                matchConfidence=s.score, band=s.band,
                scoreBreakdown=ScoreBreakdown(name=s.name, dob=s.dob, location=s.location),
                capped=s.capped, tier=s.record.tier, offenseDetails=s.record.offense,
                matchedAlias=s.matched_alias,
                detailUrl=s.record.detail_url, state=s.record.source_state,
                provider=s.record.source_provider,
            )

        return ProviderResultOut(
            state=o.state, providerUsed=o.provider_used,
            status=("MATCH_FOUND" if o.scored else "NO_MATCH") if o.checked else "UNCHECKED",
            latencyMs=o.latency_ms, dataAsOf=o.data_as_of, synthetic=o.synthetic,
            attempts=[AttemptOut(provider=a.provider, kind=a.kind, status=a.status,
                                 latencyMs=a.latency_ms, error=a.error) for a in o.attempts],
            offendersFound=[out(s) for s in o.scored],
            sameNameOtherDob=[out(s) for s in o.name_only[:_NAME_ONLY_SHOWN]],
            sameNameOtherDobTotal=len(o.name_only),
        )


_NAME_ONLY_SHOWN = 20  # cap the response, the full count is reported separately


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)
