from __future__ import annotations

import argparse
import asyncio
import json

from autofyi_mcp.api import AutoFYIAPI
from autofyi_mcp.config import Settings


async def run(query: str | None) -> dict:
    settings = Settings.from_env()
    api = AutoFYIAPI(settings)
    health = await api.health()
    contract = await api.openapi()
    required = {
        "/clients/search",
        "/clients/{key}",
        "/client-jobs-to-invoice",
        "/jobs-and-interim-table",
        "/split",
        "/allocate",
        "/create-invoice",
    }
    paths = set(contract.get("paths", {}))
    result: dict = {
        "health": health,
        "contract_ok": required <= paths,
        "missing_paths": sorted(required - paths),
        "writes_sent": False,
    }
    if query:
        result["client_search"] = await api.search_clients(query, 5)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only AutoFYI deployment smoke test")
    parser.add_argument("--query", help="Optional client search; never invokes a write endpoint")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.query)), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
