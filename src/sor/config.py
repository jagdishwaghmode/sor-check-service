"""Settings from environment, plus the routing/matching config from config/states.yaml."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from sor.matching import MatchConfig

BASE_DIR = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


@dataclass
class Settings:
    base_dir: Path = BASE_DIR
    env: str = "dev"
    database_url: str = "sqlite:///./sor_audit.db"
    audit_key: str = ""
    api_key: str = ""
    enable_chaos: bool = False
    iowa_mode: str = ""  # "" = use config file value
    real_data_only: bool = False  # skip mock/synthetic sources, so a fake CLEAR can never appear
    live_providers: frozenset = field(default_factory=frozenset)  # provider names that call the real source
    vendor_api_key: str = ""
    config_path: Path = BASE_DIR / "config" / "states.yaml"

    def __post_init__(self) -> None:
        self.live_providers = frozenset(self.live_providers)

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv(BASE_DIR / ".env")
        e = os.environ
        live = frozenset(x.strip() for x in e.get("LIVE_PROVIDERS", "").split(",") if x.strip())
        return cls(
            env=e.get("ENV", "dev"),
            database_url=e.get("DATABASE_URL", "sqlite:///./sor_audit.db"),
            audit_key=e.get("AUDIT_ENC_KEY", ""),
            api_key=e.get("API_KEY", ""),
            enable_chaos=e.get("ENABLE_CHAOS", "false").lower() == "true",
            iowa_mode=e.get("IOWA_MODE", ""),
            real_data_only=e.get("REAL_DATA_ONLY", "true" if (live or e.get("IOWA_MODE") == "live") else "false").lower() == "true",
            live_providers=live,
            vendor_api_key=e.get("VENDOR_API_KEY", ""),
            config_path=Path(e.get("SOR_CONFIG", BASE_DIR / "config" / "states.yaml")),
        )


@dataclass
class AppConfig:
    timeout_s: float
    default_chain: list[str]
    breaker_threshold: int
    breaker_cooldown_s: float
    match: MatchConfig
    providers: dict[str, dict]
    states: dict[str, list[str]]

    def chain_for(self, state: str) -> list[str]:
        return self.states.get(state, self.default_chain)


def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text())
    d = raw.get("defaults", {})
    m = raw.get("matching", {})
    w, t = m.get("weights", {}), m.get("thresholds", {})
    match = MatchConfig(
        w_name=w.get("name", 0.6), w_dob=w.get("dob", 0.3), w_loc=w.get("location", 0.1),
        clear_below=t.get("clear_below", 0.40), flag_at=t.get("flag_at_or_above", 0.75),
        name_floor=m.get("name_floor", 0.60),
        dob_mismatch_min_name=m.get("dob_mismatch_min_name", 0.95),
        no_dob_min_name=m.get("no_dob_min_name", 0.95),
        dob_far_policy=m.get("dob_far_policy", "note"),
    )
    cfg = AppConfig(
        timeout_s=d.get("timeout_ms", 3000) / 1000,
        default_chain=list(d.get("default_chain", [])),
        breaker_threshold=raw.get("breaker", {}).get("failure_threshold", 3),
        breaker_cooldown_s=raw.get("breaker", {}).get("cooldown_s", 30),
        match=match,
        providers=raw.get("providers", {}),
        states={k.upper(): list(v) for k, v in raw.get("states", {}).items()},
    )
    _validate(cfg)
    return cfg


def _validate(cfg: AppConfig) -> None:
    """Fail at startup, not on the first patient."""
    mc = cfg.match
    if abs(mc.w_name + mc.w_dob + mc.w_loc - 1.0) > 1e-6:
        raise ValueError("matching weights must sum to 1.0")
    if mc.dob_far_policy not in ("note", "review"):
        raise ValueError("matching.dob_far_policy must be 'note' or 'review'")
    if not 0 < mc.clear_below < mc.flag_at <= 1:
        raise ValueError("thresholds must satisfy 0 < clear_below < flag_at <= 1")
    if not cfg.default_chain:
        raise ValueError("defaults.default_chain must not be empty")
    for state, chain in cfg.states.items():
        if not re.fullmatch(r"[A-Z]{2}", state):
            raise ValueError(f"bad state code {state!r}")
        if not chain:
            raise ValueError(f"state {state} has an empty provider chain")
    for chain in [cfg.default_chain, *cfg.states.values()]:
        for name in chain:
            if name not in cfg.providers:
                raise ValueError(f"provider {name!r} used in a chain but not defined")
