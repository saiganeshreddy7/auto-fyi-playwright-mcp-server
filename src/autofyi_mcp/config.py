from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    api_base: str = "https://autofyi.kellyautomations.com"
    enable_writes: bool = False
    api_token: str = ""
    cf_access_client_id: str = ""
    cf_access_client_secret: str = ""
    connect_timeout_seconds: float = 15.0
    read_timeout_seconds: float = 600.0
    confirmation_ttl_seconds: int = 600
    transport: str = "stdio"
    http_host: str = "0.0.0.0"
    http_port: int = 8000
    http_path_secret: str = ""
    auth_tokens: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        return cls(
            api_base=os.getenv("AUTOFYI_API_BASE", "https://autofyi.kellyautomations.com").rstrip(
                "/"
            ),
            enable_writes=_as_bool(os.getenv("AUTOFYI_ENABLE_WRITES"), False),
            api_token=os.getenv("AUTOFYI_API_TOKEN", "").strip(),
            cf_access_client_id=os.getenv("AUTOFYI_CF_ACCESS_CLIENT_ID", "").strip(),
            cf_access_client_secret=os.getenv("AUTOFYI_CF_ACCESS_CLIENT_SECRET", "").strip(),
            connect_timeout_seconds=float(os.getenv("AUTOFYI_CONNECT_TIMEOUT_SECONDS", "15")),
            read_timeout_seconds=float(os.getenv("AUTOFYI_READ_TIMEOUT_SECONDS", "600")),
            confirmation_ttl_seconds=int(os.getenv("AUTOFYI_CONFIRMATION_TTL_SECONDS", "600")),
            transport=os.getenv("AUTOFYI_MCP_TRANSPORT", "stdio").strip(),
            http_host=os.getenv("AUTOFYI_MCP_HOST", "0.0.0.0").strip(),
            http_port=int(os.getenv("PORT", os.getenv("AUTOFYI_MCP_PORT", "8000"))),
            http_path_secret=os.getenv("AUTOFYI_MCP_PATH_SECRET", "").strip(),
            auth_tokens=tuple(
                t.strip() for t in os.getenv("AUTOFYI_MCP_AUTH_TOKENS", "").split(",") if t.strip()
            ),
        )

    def streamable_http_path(self) -> str:
        """MCP endpoint path. The secret segment is the access control for remote hosting."""
        if not self.http_path_secret:
            return "/mcp"
        return "/mcp/" + quote(self.http_path_secret, safe="")

    def request_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        if self.cf_access_client_id and self.cf_access_client_secret:
            headers["CF-Access-Client-Id"] = self.cf_access_client_id
            headers["CF-Access-Client-Secret"] = self.cf_access_client_secret
        return headers
