"""Dev-only failure injection so the UI and tests can simulate provider outages.

Modes: ok | down | slow | bad. Only honored when ENABLE_CHAOS=true.
"""

MODES = {"ok", "down", "slow", "bad"}
_modes: dict[str, str] = {}


def get(provider: str) -> str:
    return _modes.get(provider, "ok")


def set_mode(provider: str, mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {sorted(MODES)}")
    if mode == "ok":
        _modes.pop(provider, None)
    else:
        _modes[provider] = mode


def snapshot() -> dict[str, str]:
    return dict(_modes)


def reset() -> None:
    _modes.clear()
