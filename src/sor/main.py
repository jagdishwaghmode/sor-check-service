"""FastAPI app: the check endpoint, small admin/health endpoints, and the demo UI."""

import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sor import chaos
from sor.audit import AuditLog
from sor.config import Settings, load_config
from sor.engine import Engine
from sor.mock.server import mock_app
from sor.models import CheckRequest, CheckResponse
from sor.providers import PROVIDER_TYPES

STATIC_DIR = Path(__file__).parent / "static"
log = logging.getLogger("sor")


class ChaosSet(BaseModel):
    provider: str
    mode: str


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    cfg = load_config(settings.config_path)

    providers = {}
    for name, pcfg in cfg.providers.items():
        ptype = PROVIDER_TYPES.get(pcfg.get("type"))
        if ptype is None:
            raise ValueError(f"provider {name!r} has unknown type {pcfg.get('type')!r}")
        providers[name] = ptype.from_config(name, pcfg, settings)

    audit = AuditLog(settings.database_url, settings.audit_key, settings.env)
    engine = Engine(cfg, providers, audit, chaos_enabled=settings.enable_chaos,
                    skip_synthetic=settings.real_data_only)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        for p in providers.values():
            try:
                await p.warm_up()
            except Exception:  # startup must not fail because one source is unhappy
                log.exception("warm-up failed for %s", p.name)
        yield
        for p in providers.values():
            await p.aclose()

    app = FastAPI(title="SOR Check Service", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine
    app.state.settings = settings

    def auth(x_api_key: Annotated[str | None, Header()] = None) -> None:
        if settings.api_key and not hmac.compare_digest(x_api_key or "", settings.api_key):
            raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.post("/v1/sor/checks", response_model=CheckResponse, dependencies=[Depends(auth)])
    async def run_check(
        req: CheckRequest, x_actor_id: Annotated[str | None, Header()] = None
    ) -> CheckResponse:
        try:
            return await engine.check(req, x_actor_id or "anonymous")
        except Exception as e:  # audit write failed: do not return an unaudited verdict
            log.exception("check failed before it could be audited")
            raise HTTPException(
                status_code=503, detail="check could not be audited, please retry"
            ) from e

    @app.get("/v1/states", dependencies=[Depends(auth)])
    def states():
        def chain(names):
            return [{"provider": n, "kind": providers[n].kind} for n in names]

        return {
            "states": {s: chain(c) for s, c in sorted(cfg.states.items())},
            "defaultChain": chain(cfg.default_chain),
        }

    @app.get("/v1/providers/health", dependencies=[Depends(auth)])
    def health():
        out = []
        for name, p in providers.items():
            as_of = p.data_as_of()
            out.append({
                "provider": name, "kind": p.kind, "synthetic": p.synthetic, "breaker": engine.breakers[name].state,
                "dataAsOf": as_of.isoformat(timespec="seconds") if as_of else None,
                "chaos": chaos.get(name) if settings.enable_chaos else None,
            })
        return out

    @app.get("/v1/audit/recent", dependencies=[Depends(auth)])
    async def audit_recent(limit: int = 15):
        import asyncio

        return await asyncio.to_thread(audit.recent, min(max(limit, 1), 100))

    if settings.enable_chaos:

        @app.post("/v1/admin/chaos", dependencies=[Depends(auth)])
        def set_chaos(body: ChaosSet):
            if body.provider not in providers:
                raise HTTPException(404, "unknown provider")
            try:
                chaos.set_mode(body.provider, body.mode)
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
            return chaos.snapshot()

        @app.post("/v1/admin/chaos/reset", dependencies=[Depends(auth)])
        def reset_chaos():
            chaos.reset()
            engine.reset_breakers()
            return {}

    app.mount("/mock", mock_app)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def app_factory() -> FastAPI:  # uvicorn --factory sor.main:app_factory
    return create_app()
