from __future__ import annotations

import logging
import secrets
import sys
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from autofyi_mcp.api import AutoFYIAPI
from autofyi_mcp.config import Settings
from autofyi_mcp.models import (
    AllocationJob,
    BillingMonth,
    CatalogFilter,
    DirectInvoiceJob,
    PreviewInvoice,
)
from autofyi_mcp.service import AutoFYIService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("autofyi_mcp")

settings = Settings.from_env()
service = AutoFYIService(AutoFYIAPI(settings), settings)

INSTRUCTIONS = """
AutoFYI manages financial operations in FYI using client information and Xero repeating-invoice allocations.
Always search for a client first. Search scores rank candidates only; they do not decide identity. Compare names yourself, treating Ltd/Limited and punctuation as equivalent. If one non-exact candidate is plausible, propose its name and id and ask the user "Is this the client you mean?". After confirmation, use that stable id.
Read client information, current FYI jobs, and interims before recommending a financial operation.
For portfolio questions across many clients, use the imported client catalog. Call describe_client_catalog before filtering unfamiliar columns. A catalog result describes the last imported FYI CSV snapshot; it does not prove that a live FYI job or remaining interim exists. Never submit or invent SQL.
Clarify ambiguous portfolio wording such as "clients with VAT jobs": VAT registered, VAT processing, VAT allocation service, live VAT job, and remaining VAT interim are different questions. The CSV catalog can answer only fields present in its imported schema.
Value profiles contain at most 10 common values. values_truncated=true means the list is not exhaustive. Prefer profiling one relevant column instead of requesting values for every column. CSV import is performed directly in the AutoFYI backend Swagger UI, not through MCP.
For repeating clients: split the complete monthly interim using all prepared allocation service lines, then allocate the selected split service row(s) to exact FYI job names.
Before split or allocation, inspect live billing state: unsplit, fully split, partially allocated, missing/consumed, ambiguous, or inconsistent. Never re-split a partially allocated month.
Every allocation request must cover exactly ONE service/job type. Merge all required months or jobs for that service into one request: Case 1 is one month/one job; Case 2 is all required months/one job (VAT, annual, yearly subscription); Case 3 is one month/all required jobs (weekly). Do not create one allocation call per weekly job or per VAT month, and do not mix monthly, weekly, VAT, annual, or subscription services in one call.
When identical remaining amounts could represent multiple services, ask which service remains before preparing allocation. Never pretend FYI provides a service label when it provides only date and amount.
For non-repeating clients: a direct invoice may be prepared. Direct invoicing a repeating client can duplicate billing.
Never call execute_confirmed_action until the user has seen the complete preview and supplied the exact required_confirmation phrase.
Never retry a write after a timeout, error, or partial result. Draft invoices are the default; approval requires a stronger confirmation phrase.
""".strip()

mcp = FastMCP(
    "AutoFYI",
    instructions=INSTRUCTIONS,
    json_response=True,
    host=settings.http_host,
    port=settings.http_port,
    streamable_http_path=settings.streamable_http_path(),
)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    """Liveness probe for the hosting platform. Reports nothing about FYI or clients."""
    return JSONResponse({"status": "ok"})

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
PREPARE_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=True
)
LOCAL_STATE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)


@mcp.tool(annotations=READ_ONLY)
async def describe_autofyi() -> dict:
    """Explain the AutoFYI business rules, safe tool order, and deliberately excluded admin endpoints."""
    return await service.describe()


@mcp.tool(annotations=READ_ONLY)
async def autofyi_health() -> dict:
    """Check whether the deployed backend and its FYI browser session are reachable. Makes no changes."""
    return await service.health()


@mcp.tool(annotations=READ_ONLY)
async def get_client_catalog_status() -> dict:
    """Check whether an FYI CSV catalog is published and report its version, age, rows, and columns."""
    return await service.get_client_catalog_status()


@mcp.tool(annotations=READ_ONLY)
async def describe_client_catalog(
    column: str | None = None, include_common_values: bool = False
) -> dict:
    """Describe queryable FYI CSV columns. Request one column for up to 10 common values; truncated is not exhaustive."""
    return await service.describe_client_catalog(column, include_common_values)


@mcp.tool(annotations=READ_ONLY)
async def query_client_catalog(
    operation: Literal["count", "list", "distinct", "group_count", "summary"],
    columns: list[str] | None = None,
    filters: list[CatalogFilter] | None = None,
    group_by: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """Safely count/filter/group the imported FYI client snapshot. Structured operations only—never raw SQL or live-job claims."""
    return await service.query_client_catalog(
        operation, columns, filters, group_by, limit, offset
    )


@mcp.tool(annotations=READ_ONLY)
async def search_clients(query: str, limit: int = 5) -> dict:
    """Rank client candidates. Claude compares names, proposes a likely non-exact match, and asks the user to confirm."""
    return await service.search_clients(query, limit)


@mcp.tool(annotations=READ_ONLY)
async def find_client(query: str, detail_level: str = "summary") -> dict:
    """Load one strict identical client name; otherwise return ranked candidates for AI review and user confirmation."""
    return await service.find_client(query, detail_level)


@mcp.tool(annotations=READ_ONLY)
async def get_client_information(client_id: str, detail_level: str = "summary") -> dict:
    """Read a selected client's FYI information, Xero templates, and prepared allocations. Use full only when necessary."""
    return await service.get_client_information(client_id, detail_level)


@mcp.tool(annotations=READ_ONLY)
async def get_client_jobs_to_invoice(client_id: str) -> dict:
    """Read the live FYI jobs available to invoice and their work amounts. Browser-driven and may take minutes."""
    return await service.get_client_jobs_to_invoice(client_id)


@mcp.tool(annotations=READ_ONLY)
async def get_jobs_and_interim_table(client_id: str) -> dict:
    """Read live FYI jobs plus billing-job interim dates and amounts. Makes no FYI changes but may take minutes."""
    return await service.get_jobs_and_interim_table(client_id)


@mcp.tool(annotations=READ_ONLY)
async def preview_split_from_invoices(
    client_id: str,
    invoices: list[PreviewInvoice],
) -> dict:
    """Read-only preview when a client has no FYI allocation plan: split live FYI interims using Xero invoice service lines and suggest job matches. Writes nothing. Fetch the invoices first with the Xero tools."""
    return await service.preview_split_from_invoices(client_id, invoices)


@mcp.tool(annotations=READ_ONLY)
async def inspect_client_billing_state(
    client_id: str,
    months: list[BillingMonth],
    invoice_key: str | None = None,
) -> dict:
    """Classify selected live FYI months as unsplit, split, partially allocated, ambiguous, missing, or inconsistent."""
    return await service.inspect_client_billing_state(client_id, months, invoice_key)


@mcp.tool(annotations=READ_ONLY)
async def plan_client_billing(
    client_id: str,
    target_month: int,
    target_year: int,
    invoice_key: str | None = None,
) -> dict:
    """Build a no-write billing plan from client/Xero/allocation information and identify the next safe live check."""
    return await service.plan_client_billing(client_id, target_month, target_year, invoice_key)


@mcp.tool(annotations=PREPARE_ONLY)
async def prepare_interim_split(
    client_id: str,
    target_month: int,
    target_year: int,
    invoice_key: str | None = None,
) -> dict:
    """Validate live FYI interims and prepare—but do not execute—a permanent full-invoice service split."""
    return await service.prepare_interim_split(client_id, target_month, target_year, invoice_key)


@mcp.tool(annotations=PREPARE_ONLY)
async def prepare_job_allocation(
    client_id: str,
    service_line: str,
    months: list[BillingMonth],
    jobs: list[AllocationJob],
    invoice_key: str | None = None,
    invoice_type: str = "Final",
    theme: str = "Standard",
    approve_invoice: bool = False,
    confirm_ambiguous_remaining: bool = False,
) -> dict:
    """Prepare one complete service-type allocation. Merge that service's months/jobs; never mix service types."""
    return await service.prepare_job_allocation(
        client_id,
        service_line,
        months,
        jobs,
        invoice_key,
        invoice_type,
        theme,
        approve_invoice,
        confirm_ambiguous_remaining,
    )


@mcp.tool(annotations=PREPARE_ONLY)
async def prepare_direct_invoice(
    client_id: str,
    jobs: list[DirectInvoiceJob],
    invoice_amount: float | None = None,
    invoice_type: str = "Final",
    theme: str = "Standard",
    approve_invoice: bool = False,
    allow_repeating_client: bool = False,
) -> dict:
    """Prepare a no-interim direct invoice. Repeating clients are blocked unless the duplicate-billing override is explicit."""
    return await service.prepare_direct_invoice(
        client_id,
        jobs,
        invoice_amount,
        invoice_type,
        theme,
        approve_invoice,
        allow_repeating_client,
    )


@mcp.tool(annotations=READ_ONLY)
async def get_prepared_action(confirmation_id: str) -> dict:
    """Re-read an unexpired prepared action or retrieve its completed result. Never executes it."""
    return await service.get_prepared_action(confirmation_id)


@mcp.tool(annotations=LOCAL_STATE)
async def cancel_prepared_action(confirmation_id: str) -> dict:
    """Cancel a prepared action locally. No request is sent to AutoFYI or FYI."""
    return await service.cancel_prepared_action(confirmation_id)


@mcp.tool(annotations=DESTRUCTIVE)
async def execute_confirmed_action(confirmation_id: str, user_confirmation: str) -> dict:
    """Execute exactly one prepared financial write. Call only after the user supplies the exact preview phrase; never retry."""
    return await service.execute_confirmed_action(confirmation_id, user_confirmation)


class BearerAuthMiddleware:
    """Reject every request except /healthz unless it carries a configured bearer token."""

    def __init__(self, app, tokens: tuple[str, ...]) -> None:
        self.app = app
        self.tokens = tokens

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("path") == "/healthz":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        if self._valid(headers.get(b"authorization", b"").decode("latin-1")):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            {"error": "unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)

    def _valid(self, authorization: str) -> bool:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer":
            return False
        token = token.strip()
        return any(secrets.compare_digest(token, known) for known in self.tokens)


def main() -> None:
    if settings.transport != "stdio" and not (settings.auth_tokens or settings.http_path_secret):
        logger.error(
            "Set AUTOFYI_MCP_AUTH_TOKENS and/or AUTOFYI_MCP_PATH_SECRET for transport=%s. "
            "Refusing to expose an unauthenticated financial MCP endpoint.",
            settings.transport,
        )
        sys.exit(1)
    logger.info(
        "Starting AutoFYI MCP transport=%s api=%s writes=%s",
        settings.transport,
        settings.api_base,
        settings.enable_writes,
    )
    if settings.transport == "stdio":
        mcp.run(transport="stdio")
        return

    import uvicorn

    logger.info(
        "Serving MCP on %s:%s path=%s auth=%s",
        settings.http_host,
        settings.http_port,
        "/mcp/<secret>" if settings.http_path_secret else "/mcp",
        f"{len(settings.auth_tokens)} bearer token(s)" if settings.auth_tokens else "path secret",
    )
    app = mcp.streamable_http_app()
    if settings.auth_tokens:
        app.add_middleware(BearerAuthMiddleware, tokens=settings.auth_tokens)
    uvicorn.run(app, host=settings.http_host, port=settings.http_port, log_level="info")


if __name__ == "__main__":
    main()
