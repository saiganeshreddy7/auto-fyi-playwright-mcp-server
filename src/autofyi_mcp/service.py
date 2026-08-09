from __future__ import annotations

import asyncio
import re
from copy import deepcopy
from decimal import Decimal
from typing import Any

from autofyi_mcp.api import AutoFYIAPI
from autofyi_mcp.business import (
    allocation_candidates,
    allocation_case,
    amount_float,
    amounts_equal,
    classify_month_billing_state,
    equal_divide,
    find_service_line,
    interim_rows_for_date,
    interim_rows_for_month,
    invoice_service_lines,
    job_date,
    money,
    name_relation,
    normalized,
    parse_date,
    plan_automation_blocker,
    plan_identity,
    repeat_day,
    select_allocation_plan,
    service_line_amount,
    service_lines,
    suggest_jobs_for_service,
    summarize_client,
)
from autofyi_mcp.config import Settings
from autofyi_mcp.confirmations import ConfirmationStore
from autofyi_mcp.errors import APIError, BusinessRuleError
from autofyi_mcp.models import (
    AllocationJob,
    BillingMonth,
    CatalogFilter,
    DirectInvoiceJob,
    PreviewInvoice,
)


def _unwrap_job_response(response: dict[str, Any], operation: str) -> dict[str, Any]:
    if response.get("status") == "error":
        raise BusinessRuleError(
            f"AutoFYI could not {operation}: {response.get('message', 'unknown error')}"
        )
    data = response.get("data")
    if not isinstance(data, dict):
        raise BusinessRuleError(f"AutoFYI returned no structured data while trying to {operation}.")
    return data


def _number_from_ui(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float | Decimal):
        return money(value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if cleaned in {"", "-", ".", "-."}:
        return None
    try:
        return money(cleaned)
    except BusinessRuleError:
        return None


class AutoFYIService:
    def __init__(
        self,
        api: AutoFYIAPI,
        settings: Settings,
        confirmations: ConfirmationStore | None = None,
    ) -> None:
        self.api = api
        self.settings = settings
        self.confirmations = confirmations or ConfirmationStore(settings.confirmation_ttl_seconds)
        self._client_locks: dict[str, asyncio.Lock] = {}
        self._completed: dict[str, dict[str, Any]] = {}

    async def describe(self) -> dict[str, Any]:
        return {
            "server": "AutoFYI MCP",
            "api_base": self.settings.api_base,
            "writes_enabled": self.settings.enable_writes,
            "business_rules": [
                "Search scores rank names only. Claude reviews candidates and asks the user to confirm a plausible non-exact match.",
                "Ltd/Limited and punctuation are equivalent, but abbreviations such as Accuracy/ACG still require confirmation.",
                "Prepared allocations are the operational source of truth for service NET amounts.",
                "Split is permanent and never creates an invoice.",
                "Never re-split a partially allocated month; inspect and use only remaining service rows.",
                "One allocation request handles exactly one complete service/job type.",
                "Case 1: one month/one job. Case 2: all service months/one job. Case 3: one month/all service jobs.",
                "Allocate consumes split interims and creates a draft invoice by default.",
                "Direct invoice bypasses interims and is dangerous for repeating clients.",
                "Every financial write requires prepare, exact user confirmation, and single-use execute.",
                "Never retry an uncertain write after a timeout; inspect FYI first.",
                "Catalog analytics describe the last imported FYI CSV snapshot, not live jobs or interims.",
                "Clarify whether 'VAT jobs' means VAT registered, VAT processing, an allocation service, a live FYI job, or a remaining interim.",
                "With no prepared FYI plan, preview_split_from_invoices reconciles Xero invoice lines against live interims and jobs as a read-only preview; it never posts a split, allocation, or invoice.",
            ],
            "tools_by_stage": {
                "discover": [
                    "search_clients",
                    "find_client",
                    "get_client_information",
                ],
                "catalog_analytics": [
                    "get_client_catalog_status",
                    "describe_client_catalog",
                    "query_client_catalog",
                ],
                "inspect_fyi": [
                    "get_client_jobs_to_invoice",
                    "get_jobs_and_interim_table",
                    "inspect_client_billing_state",
                    "preview_split_from_invoices",
                ],
                "plan": [
                    "plan_client_billing",
                    "prepare_interim_split",
                    "prepare_job_allocation",
                    "prepare_direct_invoice",
                ],
                "confirm": [
                    "get_prepared_action",
                    "cancel_prepared_action",
                    "execute_confirmed_action",
                ],
            },
            "intentionally_not_exposed": {
                "client_registry_writes": "Could replace/delete client source data.",
                "email_endpoints": "Send messages externally.",
                "ui_state_endpoints": "Belong to the existing frontend.",
                "batch_financial_writes": "Single-client workflows must be proven first.",
            },
        }

    async def health(self) -> dict[str, Any]:
        result = await self.api.health()
        return {
            "reachable": result.get("status") == "ok",
            "backend": result,
            "api_base": self.settings.api_base,
            "writes_enabled": self.settings.enable_writes,
        }

    async def get_client_catalog_status(self) -> dict[str, Any]:
        return await self.api.catalog_status()

    async def describe_client_catalog(
        self, column: str | None = None, include_common_values: bool = False
    ) -> dict[str, Any]:
        if column is not None and not column.strip():
            raise BusinessRuleError("Catalog column cannot be blank.")
        return await self.api.catalog_schema(
            column.strip() if column else None, include_common_values
        )

    async def query_client_catalog(
        self,
        operation: str,
        columns: list[str] | None = None,
        filters: list[CatalogFilter] | None = None,
        group_by: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        allowed_operations = {"count", "list", "distinct", "group_count", "summary"}
        if operation not in allowed_operations:
            raise BusinessRuleError(
                "operation must be count, list, distinct, group_count, or summary"
            )
        if not 1 <= limit <= 100:
            raise BusinessRuleError("Catalog query limit must be between 1 and 100.")
        if not 0 <= offset <= 1_000_000:
            raise BusinessRuleError("Catalog query offset must be between 0 and 1,000,000.")
        clean_columns = [column.strip() for column in (columns or [])]
        if any(not column for column in clean_columns):
            raise BusinessRuleError("Catalog query columns cannot be blank.")
        payload = {
            "operation": operation,
            "columns": clean_columns,
            "filters": [item.model_dump() for item in (filters or [])],
            "group_by": group_by.strip() if group_by else None,
            "limit": limit,
            "offset": offset,
        }
        return await self.api.catalog_query(payload)

    async def search_clients(self, query: str, limit: int = 5) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise BusinessRuleError("Client search query cannot be empty.")
        if not 1 <= limit <= 20:
            raise BusinessRuleError("Client search limit must be between 1 and 20.")
        result = await self.api.search_clients(query, limit)
        matches = [
            {**match, "name_relation": name_relation(query, match.get("name"))}
            for match in (result.get("matches") or [])
        ]
        strict = [match for match in matches if match["name_relation"] == "strict_exact"]
        legal_equivalent = [
            match for match in matches if match["name_relation"] == "legal_suffix_equivalent"
        ]
        likely = matches[0] if matches else None
        return {
            **result,
            "matches": matches,
            "resolution": (
                "unique_exact_match"
                if len(strict) == 1
                else "likely_legal_name_match_needs_confirmation"
                if len(legal_equivalent) == 1
                else "ai_candidate_review_needs_confirmation"
                if matches
                else "no_match"
            ),
            "likely_candidate": likely,
            "instruction": (
                "Use the one strict exact match's id for the next read tool."
                if len(strict) == 1
                else "Scores rank candidates only. Compare the requested and returned names yourself, treat Ltd/Limited and punctuation as equivalent, propose the most plausible candidate, and ask 'Is this the client you mean?'. After the user confirms, use that candidate's stable id."
                if matches
                else "Ask for another spelling or identifier."
            ),
        }

    async def find_client(self, query: str, detail_level: str = "summary") -> dict[str, Any]:
        search = await self.search_clients(query, 5)
        exact = [
            match
            for match in search.get("matches", [])
            if match.get("name_relation") == "strict_exact"
        ]
        if len(exact) != 1:
            return search
        client = await self.get_client_information(str(exact[0]["id"]), detail_level)
        return {
            "resolution": "selected_unique_exact_match",
            "selected_match": exact[0],
            "client": client,
        }

    async def _client_wrapper(self, client_id: str) -> dict[str, Any]:
        wrapper = await self.api.get_client(client_id.strip())
        if not wrapper.get("valid_json") or not isinstance(wrapper.get("client_info"), dict):
            raise BusinessRuleError(
                f"Client {client_id!r} does not have valid structured JSON information."
            )
        return wrapper

    async def get_client_information(
        self, client_id: str, detail_level: str = "summary"
    ) -> dict[str, Any]:
        if detail_level not in {"summary", "full"}:
            raise BusinessRuleError("detail_level must be 'summary' or 'full'.")
        wrapper = await self._client_wrapper(client_id)
        if detail_level == "full":
            return wrapper
        return summarize_client(wrapper)

    def _client_ref(
        self, wrapper: dict[str, Any], plan: dict[str, Any] | None = None
    ) -> dict[str, str]:
        info = wrapper["client_info"]
        fyi = info.get("fyi") if isinstance(info.get("fyi"), dict) else {}
        group_id = ""
        for key, value in fyi.items():
            if normalized(key) in {"client group id", "fyi client group id", "group id"}:
                group_id = str(value or "").strip()
                break
        client_name = str(wrapper.get("name") or (plan or {}).get("clientName") or "").strip()
        billing_job = str((plan or {}).get("billingJobName") or "").strip()
        if not billing_job:
            billing_job = f"Billing Job - {client_name}"
        return {
            "client_name": client_name,
            "client_id": str(wrapper.get("id") or "").strip(),
            "client_group_id": group_id,
            "billing_job_name": billing_job,
        }

    @staticmethod
    def _validate_plan_target(wrapper: dict[str, Any], plan: dict[str, Any]) -> None:
        blocker = plan_automation_blocker(plan)
        if blocker:
            raise BusinessRuleError(blocker)
        prepared_id = str(plan.get("clientId") or "").strip()
        selected_id = str(wrapper.get("id") or "").strip()
        if prepared_id and prepared_id != selected_id:
            raise BusinessRuleError(
                f"Prepared allocation client ID {prepared_id!r} does not match selected client "
                f"ID {selected_id!r}. Refuse the financial operation until the data is corrected."
            )

    async def get_client_jobs_to_invoice(self, client_id: str) -> dict[str, Any]:
        wrapper = await self._client_wrapper(client_id)
        plans = allocation_candidates(wrapper["client_info"])
        plan = plans[0] if len(plans) == 1 else None
        response = await self.api.post_read(
            "/client-jobs-to-invoice", self._client_ref(wrapper, plan)
        )
        data = _unwrap_job_response(response, "read jobs to invoice")
        return {
            "client": {"id": wrapper["id"], "name": wrapper["name"]},
            "message": response.get("message"),
            **data,
        }

    async def _jobs_interims(
        self, wrapper: dict[str, Any], plan: dict[str, Any] | None
    ) -> tuple[dict[str, Any], dict[str, str]]:
        ref = self._client_ref(wrapper, plan)
        response = await self.api.post_read("/jobs-and-interim-table", ref)
        return _unwrap_job_response(response, "read FYI jobs and interims"), ref

    async def get_jobs_and_interim_table(self, client_id: str) -> dict[str, Any]:
        wrapper = await self._client_wrapper(client_id)
        plans = allocation_candidates(wrapper["client_info"])
        plan = plans[0] if len(plans) == 1 else None
        data, _ = await self._jobs_interims(wrapper, plan)
        return {
            "client": {"id": wrapper["id"], "name": wrapper["name"]},
            **data,
        }

    async def preview_split_from_invoices(
        self,
        client_id: str,
        invoices: list[PreviewInvoice],
    ) -> dict[str, Any]:
        """Read-only preview: split live FYI interims using Xero invoice service lines.

        For clients with no prepared FYI allocation plan, the Xero invoice lines stand in as the
        service-line source of truth. Nothing is split, allocated, or invoiced. Job matches are
        suggestions only. When a prepared FYI plan already exists, the split flow is authoritative.
        """
        invoice_models = [PreviewInvoice.model_validate(invoice) for invoice in invoices]
        if not invoice_models:
            raise BusinessRuleError("Provide at least one Xero invoice to preview.")

        wrapper = await self._client_wrapper(client_id)
        info = wrapper["client_info"]
        plans = allocation_candidates(info)
        data, _ = await self._jobs_interims(wrapper, None)
        interims = data.get("interims") if isinstance(data.get("interims"), list) else []
        jobs = data.get("jobs") if isinstance(data.get("jobs"), list) else []

        warnings: list[str] = []
        months: list[dict[str, Any]] = []
        matched_interim_months: set[tuple[int, int]] = set()
        suggested_job_names: set[str] = set()

        for invoice in invoice_models:
            service_line_map, line_warnings = invoice_service_lines(
                {"lines": [line.model_dump() for line in invoice.lines]}
            )
            warnings.extend(line_warnings)
            invoice_net = amount_float(
                sum((money(value) for value in service_line_map.values()), Decimal("0"))
            )
            parsed = parse_date(invoice.date)
            entry: dict[str, Any] = {
                "invoice_reference": invoice.reference or None,
                "invoice_date": invoice.date,
                "invoice_net_total": invoice_net,
                "service_lines": service_line_map,
            }

            if parsed is None:
                entry["split_state"] = "unreadable_invoice_date"
                entry["note"] = "Invoice date could not be parsed; cannot match an FYI interim."
                months.append(entry)
                continue

            matched_interim_months.add((parsed.year, parsed.month))
            rows = interim_rows_for_month(interims, parsed.year, parsed.month)
            readable = [
                amount
                for row in rows
                if (amount := _number_from_ui(row.get("amount"))) is not None
            ]
            state = classify_month_billing_state(service_line_map, readable)
            interim_total = state["live_total"]
            entry.update(
                {
                    "matched_interim": {
                        "found": bool(rows),
                        "rows": state["live_rows"],
                        "total": interim_total,
                    },
                    "split_state": state["state"],
                    "amount_check": (
                        "matches"
                        if not rows or amounts_equal(money(interim_total), money(invoice_net))
                        else f"MISMATCH: interim total {interim_total:.2f} vs invoice net {invoice_net:.2f}"
                    ),
                    "remaining_services": state["remaining_services"],
                    "consumed_services": state["consumed_services"],
                }
            )

            allocation: list[dict[str, Any]] = []
            for service, amount in service_line_map.items():
                candidates = suggest_jobs_for_service(service, jobs, parsed.month, parsed.year)
                for candidate in candidates:
                    suggested_job_names.add(normalized(candidate["job_name"]))
                allocation.append(
                    {
                        "service": service,
                        "amount": amount,
                        "suggested_jobs": candidates,
                        "matched": bool(candidates),
                    }
                )
            entry["allocation_suggestions"] = allocation
            entry["service_lines_without_job"] = [
                row["service"] for row in allocation if not row["matched"]
            ]
            months.append(entry)

        invoiced_months = {
            (parsed.year, parsed.month)
            for invoice in invoice_models
            if (parsed := parse_date(invoice.date)) is not None
        }
        interims_without_invoice = [
            {"date": row.get("date"), "amount": row.get("amount")}
            for row in interims
            if (parsed := parse_date(row.get("date"))) is not None
            and (parsed.year, parsed.month) not in invoiced_months
        ]
        invoices_without_interim = [
            {"invoice_reference": entry["invoice_reference"], "invoice_date": entry["invoice_date"]}
            for entry in months
            if isinstance(entry.get("matched_interim"), dict)
            and not entry["matched_interim"]["found"]
        ]
        unmatched_jobs = [
            {"job_name": job.get("job_name"), "work_amount": job.get("work_amount")}
            for job in jobs
            if isinstance(job, dict)
            and not job.get("is_billing_job")
            and normalized(job.get("job_name")) not in suggested_job_names
        ]

        if plans:
            warnings.insert(
                0,
                "This client already has a prepared FYI allocation plan. That plan is the "
                "operational source of truth; use inspect_client_billing_state and "
                "prepare_interim_split instead of this Xero-invoice preview to make changes.",
            )

        return {
            "client": {"id": wrapper["id"], "name": wrapper["name"]},
            "mode": "preview_from_xero_invoices",
            "binding": False,
            "note": (
                "Read-only preview. Nothing was split, allocated, or invoiced. Service-line "
                "amounts come from the supplied Xero invoices; job matches are suggestions only. "
                "Posting a real split/allocation still requires a prepared FYI allocation plan."
            ),
            "has_fyi_allocation_plan": bool(plans),
            "months": months,
            "invoices_without_interim": invoices_without_interim,
            "interims_without_invoice": interims_without_invoice,
            "unmatched_jobs": unmatched_jobs,
            "warnings": warnings,
        }

    async def inspect_client_billing_state(
        self,
        client_id: str,
        months: list[BillingMonth],
        invoice_key: str | None = None,
    ) -> dict[str, Any]:
        """Classify live FYI rows without preparing or executing a financial write."""
        month_models = [BillingMonth.model_validate(month) for month in months]
        if not month_models:
            raise BusinessRuleError("At least one month is required for billing-state inspection.")
        keys = [(item.month, item.year) for item in month_models]
        if len(set(keys)) != len(keys):
            raise BusinessRuleError("The same inspection month was supplied more than once.")

        wrapper = await self._client_wrapper(client_id)
        info = wrapper["client_info"]
        plan = select_allocation_plan(info, invoice_key)
        lines, plan_warnings = service_lines(plan)
        live, _ = await self._jobs_interims(wrapper, plan)
        interims = live.get("interims") if isinstance(live.get("interims"), list) else []
        day = repeat_day(plan, info)
        states: list[dict[str, Any]] = []
        warnings = list(plan_warnings)
        next_actions = {
            "unsplit": "Prepare the complete monthly split before allocating any service.",
            "partially_split_needs_completion": "Prepare the complete split so all service amounts exist.",
            "fully_split_unallocated": "Choose one service type and prepare its complete allocation request.",
            "partially_allocated": "Use only remaining rows. If equal amounts are ambiguous, ask the user which service remains.",
            "no_rows_missing_or_fully_consumed": "Ask whether the invoice is missing or all service rows were already allocated.",
            "inconsistent": "Stop and ask the user; do not apply the newest allocation plan automatically.",
        }
        for item in month_models:
            date_text, date_warnings = job_date(day, item.month, item.year)
            warnings.extend(date_warnings)
            rows = interim_rows_for_date(interims, date_text)
            readable = [
                amount for row in rows if (amount := _number_from_ui(row.get("amount"))) is not None
            ]
            state = classify_month_billing_state(lines, readable)
            state.update(
                {
                    "job_month": date_text,
                    "raw_row_count": len(rows),
                    "unreadable_row_count": len(rows) - len(readable),
                    "next_action": next_actions[state["state"]],
                }
            )
            states.append(state)

        return {
            "client": {"id": wrapper["id"], "name": wrapper["name"]},
            "allocation_plan": plan_identity(plan),
            "automation_blocker": plan_automation_blocker(plan),
            "allocation_rule": (
                "Prepare one complete request for one service/job type only. "
                "Do not mix monthly, weekly, VAT, annual, or subscription services."
            ),
            "months": states,
            "available_jobs": live.get("jobs", []),
            "warnings": warnings,
        }

    async def plan_client_billing(
        self,
        client_id: str,
        target_month: int,
        target_year: int,
        invoice_key: str | None = None,
    ) -> dict[str, Any]:
        wrapper = await self._client_wrapper(client_id)
        info = wrapper["client_info"]
        plans = allocation_candidates(info)
        client = {"id": wrapper["id"], "name": wrapper["name"]}
        if not plans:
            return {
                "client": client,
                "mode": "direct_or_unprepared",
                "reason": "No prepared allocation plan exists.",
                "recommended_flow": [
                    "Read jobs available to invoice.",
                    "Ask the user which exact jobs and amount mode to use.",
                    "Prepare a direct invoice; warn that no interim is consumed.",
                ],
            }
        if len(plans) > 1 and not invoice_key:
            return {
                "client": client,
                "mode": "needs_allocation_plan_selection",
                "available_invoice_keys": [
                    plan_identity(plan, index) for index, plan in enumerate(plans)
                ],
                "instruction": "Ask the user which Xero repeating template/reference to use.",
            }
        plan = select_allocation_plan(info, invoice_key)
        blocker = plan_automation_blocker(plan)
        lines, warnings = service_lines(plan)
        date_text, date_warnings = job_date(repeat_day(plan, info), target_month, target_year)
        return {
            "client": client,
            "mode": "xero_repeating_with_allocations",
            "allocation_plan": plan_identity(plan),
            "invoice_date": date_text,
            "invoice_amount_net": amount_float(
                sum((money(v) for v in lines.values()), Decimal("0"))
            ),
            "service_lines": lines,
            "warnings": [*warnings, *date_warnings],
            "automation_blocker": blocker,
            "recommended_flow": [
                "Inspect the live FYI billing state for every required month.",
                "Split the full monthly interim into every prepared service line if not already split.",
                "Choose one service type, all its required months or jobs, and exact FYI job names.",
                "Prepare one complete allocation request for that service and create a draft after explicit confirmation.",
            ],
            "next_safe_tools": [
                "inspect_client_billing_state",
                "prepare_interim_split",
            ],
        }

    async def prepare_interim_split(
        self,
        client_id: str,
        target_month: int,
        target_year: int,
        invoice_key: str | None = None,
    ) -> dict[str, Any]:
        wrapper = await self._client_wrapper(client_id)
        info = wrapper["client_info"]
        plan = select_allocation_plan(info, invoice_key)
        self._validate_plan_target(wrapper, plan)
        lines, warnings = service_lines(plan)
        date_text, date_warnings = job_date(repeat_day(plan, info), target_month, target_year)
        data, ref = await self._jobs_interims(wrapper, plan)
        interims = data.get("interims") if isinstance(data.get("interims"), list) else []
        rows = interim_rows_for_date(interims, date_text)
        if not rows:
            raise BusinessRuleError(
                f"No FYI interim row exists for {date_text}. Nothing can be split safely."
            )
        available = [
            amount for row in rows if (amount := _number_from_ui(row.get("amount"))) is not None
        ]
        expected = [money(value) for value in lines.values()]
        if not available:
            raise BusinessRuleError(f"FYI rows for {date_text} do not contain readable amounts.")
        billing_state = classify_month_billing_state(lines, available)
        if billing_state["state"] == "fully_split_unallocated":
            return {
                "prepared": False,
                "state": "fully_split_unallocated",
                "client": {"id": wrapper["id"], "name": wrapper["name"]},
                "job_month": date_text,
                "billing_state": billing_state,
                "message": (
                    "The live FYI rows already match the prepared service amounts. "
                    "Do not split again; allocate one service/job type at a time."
                ),
            }
        if billing_state["state"] == "partially_allocated":
            return {
                "prepared": False,
                "state": "partially_allocated",
                "client": {"id": wrapper["id"], "name": wrapper["name"]},
                "job_month": date_text,
                "billing_state": billing_state,
                "message": (
                    "This month was already split and some service rows were allocated. "
                    "Do not split again. Decide which remaining service and FYI jobs should be handled next."
                ),
            }
        if billing_state["state"] not in {"unsplit", "partially_split_needs_completion"}:
            raise BusinessRuleError(
                f"FYI rows for {date_text} are {billing_state['state']} and cannot be safely split. "
                "Inspect the billing state and ask the user before proceeding."
            )
        invoice_total = sum(expected, Decimal("0"))
        payload = {
            **ref,
            "job_month": date_text,
            "invoice_amount": amount_float(invoice_total),
            "service_lines": lines,
        }
        phrase = f"SPLIT {wrapper['name']} {date_text}"
        preview = {
            "effect": "Permanently split the existing FYI interim rows. No invoice is created.",
            "job_month": date_text,
            "current_rows": [amount_float(value) for value in available],
            "current_billing_state": billing_state["state"],
            "new_service_rows": lines,
            "invoice_amount_net": amount_float(invoice_total),
            "warnings": [
                "This split is permanent even if a later invoice popup is cancelled.",
                *warnings,
                *date_warnings,
            ],
        }
        pending = self.confirmations.create(
            action="split_interim",
            endpoint="/split",
            client_id=wrapper["id"],
            client_name=wrapper["name"],
            payload=payload,
            required_confirmation=phrase,
            preview=preview,
        )
        return {"prepared": True, **pending.public()}

    @staticmethod
    def _canonical_jobs(
        available_jobs: list[dict[str, Any]], requested_names: list[str], *, require_work: bool
    ) -> tuple[list[str], list[str]]:
        canonical: list[str] = []
        warnings: list[str] = []
        seen: set[str] = set()
        for requested in requested_names:
            key = normalized(requested)
            if key in seen:
                raise BusinessRuleError(f"Duplicate FYI job name: {requested!r}")
            seen.add(key)
            matches = [job for job in available_jobs if normalized(job.get("job_name")) == key]
            if len(matches) != 1:
                choices = ", ".join(str(job.get("job_name")) for job in available_jobs)
                raise BusinessRuleError(
                    f"FYI job {requested!r} is not one unique available job. Available jobs: {choices}"
                )
            job = matches[0]
            canonical.append(str(job.get("job_name")).strip())
            work = _number_from_ui(job.get("work_amount"))
            if require_work and work is not None and work <= 0:
                raise BusinessRuleError(
                    f"FYI job {canonical[-1]!r} has no positive work amount and cannot receive an interim allocation."
                )
            if work is None:
                warnings.append(
                    f"Could not independently parse the work amount for FYI job {canonical[-1]!r}; the backend will verify it."
                )
        return canonical, warnings

    async def prepare_job_allocation(
        self,
        client_id: str,
        service_line: str,
        months: list[BillingMonth],
        jobs: list[AllocationJob],
        invoice_key: str | None = None,
        invoice_type: str = "Final",
        theme: str = "Standard",
        approve_invoice: bool = False,
        confirm_ambiguous_remaining: bool = False,
    ) -> dict[str, Any]:
        if invoice_type not in {"Progress", "Final"}:
            raise BusinessRuleError("invoice_type must be Progress or Final.")
        if theme not in {"Standard", "Practice Ignition"}:
            raise BusinessRuleError("theme must be Standard or Practice Ignition.")
        month_models = [BillingMonth.model_validate(month) for month in months]
        job_models = [AllocationJob.model_validate(job) for job in jobs]
        if not month_models:
            raise BusinessRuleError("At least one month is required.")
        if not job_models:
            raise BusinessRuleError("At least one FYI job is required.")
        if len(month_models) > 1 and len(job_models) > 1:
            raise BusinessRuleError(
                "Do not mix a multi-month period and multiple jobs in one allocation. "
                "Use one complete service-type request: Case 1 (one month/one job), "
                "Case 2 (all required months/one job), or Case 3 (one month/all required jobs)."
            )
        month_keys = [(item.month, item.year) for item in month_models]
        if len(set(month_keys)) != len(month_keys):
            raise BusinessRuleError("The same allocation month was supplied more than once.")

        wrapper = await self._client_wrapper(client_id)
        info = wrapper["client_info"]
        plan = select_allocation_plan(info, invoice_key)
        self._validate_plan_target(wrapper, plan)
        line = find_service_line(plan, service_line)
        all_service_lines, plan_warnings = service_lines(plan)
        per_month = service_line_amount(plan, line)
        if per_month <= 0:
            raise BusinessRuleError("The selected service line must have a positive NET amount.")
        day = repeat_day(plan, info)
        warnings: list[str] = list(plan_warnings)
        month_amounts: dict[str, float] = {}
        for item in month_models:
            text, date_warnings = job_date(day, item.month, item.year)
            month_amounts[text] = amount_float(per_month)
            warnings.extend(date_warnings)

        live, ref = await self._jobs_interims(wrapper, plan)
        interims = live.get("interims") if isinstance(live.get("interims"), list) else []
        selected_description = str(line.get("description") or service_line).strip()
        same_amount_services = [
            name
            for name, value in all_service_lines.items()
            if amounts_equal(money(value), per_month)
        ]
        month_states: list[dict[str, Any]] = []
        for date_text, expected_value in month_amounts.items():
            rows = interim_rows_for_date(interims, date_text)
            amounts = [
                amount for row in rows if (amount := _number_from_ui(row.get("amount"))) is not None
            ]
            billing_state = classify_month_billing_state(all_service_lines, amounts)
            month_states.append({"job_month": date_text, **billing_state})
            if billing_state["state"] in {
                "unsplit",
                "partially_split_needs_completion",
            }:
                raise BusinessRuleError(
                    f"FYI month {date_text} is {billing_state['state']}. Complete that month's full split before allocating {selected_description!r}."
                )
            if billing_state["state"] in {
                "inconsistent",
                "no_rows_missing_or_fully_consumed",
            }:
                raise BusinessRuleError(
                    f"FYI month {date_text} is {billing_state['state']}. Do not allocate automatically; ask the user whether the row is missing, already consumed, or based on an older allocation amount."
                )
            matching_count = sum(
                1 for amount in amounts if amounts_equal(amount, money(expected_value))
            )
            if matching_count == 0:
                visible = [amount_float(amount) for amount in amounts]
                raise BusinessRuleError(
                    f"FYI does not contain a {expected_value:.2f} split interim on {date_text}. Visible amounts: {visible}. "
                    f"The {selected_description!r} service may already be allocated or may use a historical amount. Ask the user."
                )
            if len(same_amount_services) > 1 and matching_count < len(same_amount_services):
                ambiguity = (
                    f"On {date_text}, one or more remaining {expected_value:.2f} rows could represent: "
                    + ", ".join(repr(name) for name in same_amount_services)
                    + ". FYI provides date and amount but no service label."
                )
                if not confirm_ambiguous_remaining:
                    raise BusinessRuleError(
                        ambiguity
                        + f" Ask the user whether a remaining row belongs to {selected_description!r}; "
                        "only after they confirm, call again with confirm_ambiguous_remaining=true."
                    )
                warnings.append(
                    ambiguity
                    + f" The user confirmed that a remaining row belongs to {selected_description!r}."
                )

        available_jobs = live.get("jobs") if isinstance(live.get("jobs"), list) else []
        canonical_names, job_warnings = self._canonical_jobs(
            available_jobs,
            [job.job_name for job in job_models],
            require_work=True,
        )
        warnings.extend(job_warnings)
        total = per_month * len(month_models)
        given = [job.amount for job in job_models]
        if all(value is None for value in given):
            resolved_amounts = equal_divide(total, len(job_models))
            payload_jobs = [{"job_name": name} for name in canonical_names]
            division = "backend_equal_division_last_job_takes_rounding"
        elif all(value is not None for value in given):
            resolved_amounts = [money(value) for value in given]
            if not amounts_equal(sum(resolved_amounts, Decimal("0")), total):
                raise BusinessRuleError(
                    f"Job amounts total {amount_float(sum(resolved_amounts, Decimal('0'))):.2f}, while selected interims total "
                    f"{amount_float(total):.2f}. They must match."
                )
            payload_jobs = [
                {"job_name": name, "amount": amount_float(amount)}
                for name, amount in zip(canonical_names, resolved_amounts, strict=True)
            ]
            division = "explicit_amounts"
        else:
            raise BusinessRuleError(
                "Provide amounts for every allocation job, or omit all amounts for equal division."
            )

        payload = {
            **ref,
            "months": month_amounts,
            "jobs": payload_jobs,
            "invoice_type": invoice_type,
            "theme": theme,
            "approve_invoice": approve_invoice,
        }
        case = allocation_case(len(month_models), len(job_models))
        phrase = (
            f"APPROVE {selected_description} ALLOCATION FOR {wrapper['name']}"
            if approve_invoice
            else f"CREATE DRAFT {selected_description} ALLOCATION FOR {wrapper['name']}"
        )
        preview = {
            "effect": "Allocate existing split interims to FYI jobs and create an invoice.",
            "case": case,
            "allocation_scope": "one_complete_service_type_only",
            "instruction": (
                "This request contains every selected month/job needed for this one service. "
                "Do not combine it with another service type or split it into per-job calls."
            ),
            "service_line": {
                "id": line.get("id"),
                "description": line.get("description"),
                "tag": line.get("tag"),
                "net_per_month": amount_float(per_month),
            },
            "months": month_amounts,
            "month_billing_states": month_states,
            "jobs": [
                {"job_name": name, "amount": amount_float(amount)}
                for name, amount in zip(canonical_names, resolved_amounts, strict=True)
            ],
            "total": amount_float(total),
            "division": division,
            "invoice_type": invoice_type,
            "theme": theme,
            "invoice_result": "approved" if approve_invoice else "draft",
            "warnings": warnings,
        }
        pending = self.confirmations.create(
            action="allocate_interims",
            endpoint="/allocate",
            client_id=wrapper["id"],
            client_name=wrapper["name"],
            payload=payload,
            required_confirmation=phrase,
            preview=preview,
        )
        return {"prepared": True, **pending.public()}

    async def prepare_direct_invoice(
        self,
        client_id: str,
        jobs: list[DirectInvoiceJob],
        invoice_amount: float | None = None,
        invoice_type: str = "Final",
        theme: str = "Standard",
        approve_invoice: bool = False,
        allow_repeating_client: bool = False,
    ) -> dict[str, Any]:
        if invoice_type not in {"Progress", "Final"}:
            raise BusinessRuleError("invoice_type must be Progress or Final.")
        if theme not in {"Standard", "Practice Ignition"}:
            raise BusinessRuleError("theme must be Standard or Practice Ignition.")
        job_models = [DirectInvoiceJob.model_validate(job) for job in jobs]
        if not job_models:
            raise BusinessRuleError("At least one FYI job is required.")
        wrapper = await self._client_wrapper(client_id)
        info = wrapper["client_info"]
        repeating = bool(allocation_candidates(info) or info.get("xero_invoices"))
        if repeating and not allow_repeating_client:
            raise BusinessRuleError(
                "This client has Xero repeating-invoice information. A direct invoice bypasses the existing interim and may duplicate billing. "
                "Use the split/allocation workflow, or explicitly set allow_repeating_client=true after explaining the risk to the user."
            )

        plans = allocation_candidates(info)
        plan = plans[0] if len(plans) == 1 else None
        ref = self._client_ref(wrapper, plan)
        response = await self.api.post_read("/client-jobs-to-invoice", ref)
        live = _unwrap_job_response(response, "read FYI jobs before direct invoicing")
        available_jobs = live.get("jobs") if isinstance(live.get("jobs"), list) else []
        canonical_names, warnings = self._canonical_jobs(
            available_jobs, [job.job_name for job in job_models], require_work=False
        )
        supplied = [job.amount for job in job_models]
        if any(value is not None for value in supplied):
            resolved = [money(value if value is not None else 0) for value in supplied]
            payload_jobs = [
                {"job_name": name, "amount": amount_float(amount)}
                for name, amount in zip(canonical_names, resolved, strict=True)
            ]
            case = "amounts_given"
            if invoice_amount is not None:
                warnings.append(
                    "Job amounts take precedence. The backend will only read, not write, the requested invoice_amount."
                )
        else:
            payload_jobs = [{"job_name": name} for name in canonical_names]
            resolved = []
            case = "invoice_amount_given" if invoice_amount is not None else "fyi_auto"

        if invoice_amount is not None and money(invoice_amount) <= 0:
            raise BusinessRuleError("invoice_amount must be positive when provided.")
        if repeating:
            warnings.append(
                "Repeating-client override is active: this direct invoice will not consume the Xero/FYI interim and may duplicate billing."
            )
        payload: dict[str, Any] = {
            **ref,
            "jobs": payload_jobs,
            "invoice_type": invoice_type,
            "theme": theme,
            "approve_invoice": approve_invoice,
        }
        if invoice_amount is not None:
            payload["invoice_amount"] = amount_float(money(invoice_amount))
        phrase = (
            f"APPROVE DIRECT INVOICE FOR {wrapper['name']}"
            if approve_invoice
            else f"CREATE DRAFT DIRECT INVOICE FOR {wrapper['name']}"
        )
        preview = {
            "effect": "Create an invoice directly from FYI jobs without reading, splitting, or consuming interims.",
            "case": case,
            "jobs": payload_jobs,
            "invoice_amount": payload.get("invoice_amount"),
            "invoice_type": invoice_type,
            "theme": theme,
            "invoice_result": "approved" if approve_invoice else "draft",
            "warnings": warnings,
        }
        pending = self.confirmations.create(
            action="create_direct_invoice",
            endpoint="/create-invoice",
            client_id=wrapper["id"],
            client_name=wrapper["name"],
            payload=payload,
            required_confirmation=phrase,
            preview=preview,
        )
        return {"prepared": True, **pending.public()}

    async def get_prepared_action(self, confirmation_id: str) -> dict[str, Any]:
        completed = self._completed.get(confirmation_id)
        if completed:
            return {"state": "completed", **deepcopy(completed)}
        return {
            "state": "awaiting_confirmation",
            **self.confirmations.get(confirmation_id).public(),
        }

    async def cancel_prepared_action(self, confirmation_id: str) -> dict[str, Any]:
        return self.confirmations.cancel(confirmation_id)

    async def execute_confirmed_action(
        self, confirmation_id: str, user_confirmation: str
    ) -> dict[str, Any]:
        if not self.settings.enable_writes:
            pending = self.confirmations.get(confirmation_id)
            return {
                "executed": False,
                "state": "writes_disabled",
                "action": pending.action,
                "message": (
                    "No API write was sent. Set AUTOFYI_ENABLE_WRITES=true and restart the MCP only after backend access is protected and testing is approved."
                ),
            }
        pending = self.confirmations.consume(confirmation_id, user_confirmation)
        lock = self._client_locks.setdefault(pending.client_id, asyncio.Lock())
        async with lock:
            try:
                response = await self.api.post_write(pending.endpoint, pending.payload)
            except APIError as exc:
                result = {
                    "executed": True,
                    "state": "outcome_unknown_do_not_retry"
                    if exc.outcome_unknown
                    else "backend_rejected_or_failed",
                    "confirmation_id": confirmation_id,
                    "action": pending.action,
                    "client": {"id": pending.client_id, "name": pending.client_name},
                    "payload_hash": pending.payload_hash,
                    "message": str(exc),
                    "backend_body": exc.body,
                    "next_step": "Inspect FYI and the AutoFYI logs before preparing a new action.",
                }
                self._completed[confirmation_id] = result
                return result

        state = "success" if response.get("status") == "success" else "error_or_partial"
        result = {
            "executed": True,
            "state": state,
            "confirmation_id": confirmation_id,
            "action": pending.action,
            "client": {"id": pending.client_id, "name": pending.client_name},
            "payload_hash": pending.payload_hash,
            "backend_response": response,
            "retry_allowed": False,
            "next_step": (
                "Report the result to the user."
                if state == "success"
                else "Inspect FYI and backend partial-result data before doing anything else."
            ),
        }
        self._completed[confirmation_id] = result
        return result
