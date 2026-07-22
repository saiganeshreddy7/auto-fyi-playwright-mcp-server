from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from autofyi_mcp.config import Settings


def recurring_wrapper() -> dict[str, Any]:
    return {
        "id": "C-100",
        "name": "Example Services Limited",
        "valid_json": True,
        "client_info": {
            "fyi": {
                "FYI Client ID": "C-100",
                "Name": "Example Services Limited",
                "Manager": "Example Manager",
                "VAT Registered": True,
            },
            "xero_invoices": [
                {
                    "RepeatingInvoiceID": "RI-1",
                    "SubTotal": 650,
                    "Schedule": {"NextScheduledDateString": "2026-07-01"},
                }
            ],
            "allocations": {
                "key": "RI-1",
                "clientName": "Example Services Limited",
                "clientId": "C-100",
                "reference": "MONTHLY-1",
                "dd": "01",
                "lineAmountTypes": "Exclusive",
                "subTotal": 650,
                "billingJobName": "Billing Job - Example Services Limited",
                "lines": [
                    {
                        "id": "L1",
                        "description": "Monthly Payroll",
                        "net": 120,
                        "tag": "Monthly Same Month",
                    },
                    {"id": "L2", "description": "VAT Service", "net": 80, "tag": "VAT 2 Monthly"},
                    {"id": "L3", "description": "Weekly Payroll", "net": 400, "tag": "Same Week"},
                    {"id": "L4", "description": "Annual Accounts", "net": 50, "tag": "Accounts"},
                ],
            },
            "merged_at": "2026-07-01T00:00:00Z",
        },
    }


def direct_wrapper() -> dict[str, Any]:
    wrapper = recurring_wrapper()
    wrapper["id"] = "C-200"
    wrapper["name"] = "Direct Example Limited"
    wrapper["client_info"]["fyi"]["FYI Client ID"] = "C-200"
    wrapper["client_info"]["fyi"]["Name"] = "Direct Example Limited"
    wrapper["client_info"]["xero_invoices"] = []
    wrapper["client_info"]["allocations"] = None
    return wrapper


class FakeAPI:
    def __init__(self) -> None:
        self.wrappers = {
            "C-100": recurring_wrapper(),
            "C-200": direct_wrapper(),
        }
        self.search_result = {
            "query": "Example Services Limited",
            "count": 1,
            "matches": [{"id": "C-100", "name": "Example Services Limited", "score": 100}],
        }
        self.jobs = [
            {"job_name": "Monthly Payroll - Jul 2026", "work_amount": 120},
            {"job_name": "VAT Jul-Aug 2026", "work_amount": 160},
            {"job_name": "Week 1", "work_amount": 100},
            {"job_name": "Week 2", "work_amount": 100},
            {"job_name": "Week 3", "work_amount": 100},
            {"job_name": "Annual Accounts 2026", "work_amount": 600},
            {"job_name": "Direct Accounts Job", "work_amount": 500},
        ]
        self.interims = [{"date": "01 Jul 2026", "amount": 650}]
        self.write_calls: list[tuple[str, dict]] = []
        self.catalog_query_calls: list[dict] = []
        self.write_result: Any = {
            "status": "success",
            "message": "completed",
            "data": {"invoice": {"created": True, "approved": False}},
        }

    async def health(self) -> dict:
        return {"status": "ok", "browser": "running"}

    async def openapi(self) -> dict:
        return {"paths": {}}

    async def search_clients(self, query: str, limit: int = 5) -> dict:
        result = deepcopy(self.search_result)
        result["query"] = query
        return result

    async def get_client(self, client_id: str) -> dict:
        return deepcopy(self.wrappers[client_id])

    async def catalog_status(self) -> dict:
        return {"available": True, "row_count": 3, "column_count": 4}

    async def catalog_schema(
        self, column: str | None = None, include_common_values: bool = False
    ) -> dict:
        return {
            "available": True,
            "requested_column": column,
            "include_common_values": include_common_values,
            "columns": [{"column_key": column or "name"}],
        }

    async def catalog_query(self, payload: dict) -> dict:
        self.catalog_query_calls.append(deepcopy(payload))
        return {"operation": payload["operation"], "result": {"count": 2}}

    async def post_read(self, path: str, payload: dict) -> dict:
        data = {"jobs": deepcopy(self.jobs)}
        if path == "/jobs-and-interim-table":
            data["interims"] = deepcopy(self.interims)
        return {"status": "success", "message": "read", "data": data}

    async def post_write(self, path: str, payload: dict) -> dict:
        self.write_calls.append((path, deepcopy(payload)))
        if isinstance(self.write_result, Exception):
            raise self.write_result
        return deepcopy(self.write_result)


@pytest.fixture
def fake_api() -> FakeAPI:
    return FakeAPI()


@pytest.fixture
def read_settings() -> Settings:
    return Settings(api_base="https://example.test", enable_writes=False)
