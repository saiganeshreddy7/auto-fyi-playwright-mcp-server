from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from autofyi_mcp.config import Settings
from autofyi_mcp.errors import APIError


class AutoFYIAPI:
    """Small async client for the existing AutoFYI REST service."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        is_write: bool = False,
    ) -> Any:
        timeout = httpx.Timeout(
            self.settings.read_timeout_seconds,
            connect=self.settings.connect_timeout_seconds,
        )
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.api_base,
                headers=self.settings.request_headers(),
                timeout=timeout,
                follow_redirects=False,
            ) as client:
                response = await client.request(method, path, params=params, json=json)
        except httpx.ConnectError as exc:
            raise APIError(f"Could not connect to AutoFYI: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise APIError(
                "AutoFYI timed out. Do not retry a financial write until FYI is checked.",
                outcome_unknown=is_write,
            ) from exc
        except httpx.HTTPError as exc:
            raise APIError(f"AutoFYI transport failed: {exc}", outcome_unknown=is_write) from exc

        try:
            body = response.json()
        except ValueError:
            body = {"detail": response.text[:2000]}

        if not response.is_success:
            detail = body.get("detail", body) if isinstance(body, dict) else body
            raise APIError(
                f"AutoFYI returned HTTP {response.status_code}: {detail}",
                status_code=response.status_code,
                body=body,
                outcome_unknown=is_write and response.status_code >= 500,
            )
        return body

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/health")

    async def openapi(self) -> dict[str, Any]:
        return await self._request("GET", "/openapi.json")

    async def search_clients(self, query: str, limit: int = 5) -> dict[str, Any]:
        return await self._request("GET", "/clients/search", params={"q": query, "limit": limit})

    async def get_client(self, client_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/clients/{quote(client_id, safe='')}")

    async def catalog_status(self) -> dict[str, Any]:
        return await self._request("GET", "/catalog/status")

    async def catalog_schema(
        self, column: str | None = None, include_common_values: bool = False
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"include_common_values": include_common_values}
        if column:
            params["column"] = column
        return await self._request("GET", "/catalog/schema", params=params)

    async def catalog_query(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/catalog/query", json=payload)

    async def post_read(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, json=payload)

    async def post_write(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, json=payload, is_write=True)
