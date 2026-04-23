import tomllib
from pathlib import Path

import tomli_w

CONFIG_DIR = Path.home() / ".config" / "serverless-core"
CONFIG_PATH = CONFIG_DIR / "config.toml"
DEFAULT_API_URL = "http://localhost:8000"


class Config:
    def __init__(self, api_url: str = DEFAULT_API_URL, jwt: str = "") -> None:
        self.api_url = api_url
        self.jwt = jwt

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            return cls()
        data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return cls(
            api_url=str(data.get("api_url", DEFAULT_API_URL)),
            jwt=str(data.get("jwt", "")),
        )

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"api_url": self.api_url, "jwt": self.jwt}
        CONFIG_PATH.write_text(tomli_w.dumps(payload), encoding="utf-8")
        try:
            CONFIG_PATH.chmod(0o600)
        except (OSError, NotImplementedError):
            pass
