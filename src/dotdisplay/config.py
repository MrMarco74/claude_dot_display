"""Settings, derived from the environment.

Nothing identifying is baked in: the panel address, any server URL and the
setup key come from the environment so they never reach the repository.
"""

import os
import pathlib
from dataclasses import dataclass, field

DEFAULT_POLL_S = 5.0
DEFAULT_CCUSAGE_REFRESH_S = 300.0
DEFAULT_STALE_AFTER_S = 900.0


def _default_state_dir() -> pathlib.Path:
    base = os.environ.get("XDG_STATE_HOME") or (pathlib.Path.home() / ".local/state")
    return pathlib.Path(base) / "dotdisplay" / "sessions"


@dataclass
class Config:
    mac: str = ""
    hwmon_url: str = ""
    setup_key: str = ""          # secret; environment only, never the repo
    poll_s: float = DEFAULT_POLL_S
    ccusage_refresh_s: float = DEFAULT_CCUSAGE_REFRESH_S
    stale_after_s: float = DEFAULT_STALE_AFTER_S
    state_dir: pathlib.Path = field(default_factory=_default_state_dir)

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        state = env("DOTDISPLAY_STATE_DIR")
        return cls(
            mac=env("DOTDISPLAY_MAC", "").strip(),
            hwmon_url=env("DOTDISPLAY_HWMON_URL", "").strip().rstrip("/"),
            setup_key=env("DOTDISPLAY_HWMON_SETUP_KEY", "").strip(),
            poll_s=float(env("DOTDISPLAY_POLL_S", DEFAULT_POLL_S)),
            ccusage_refresh_s=float(
                env("DOTDISPLAY_CCUSAGE_REFRESH_S", DEFAULT_CCUSAGE_REFRESH_S)),
            stale_after_s=float(
                env("DOTDISPLAY_STALE_AFTER_S", DEFAULT_STALE_AFTER_S)),
            state_dir=pathlib.Path(state) if state else _default_state_dir(),
        )
