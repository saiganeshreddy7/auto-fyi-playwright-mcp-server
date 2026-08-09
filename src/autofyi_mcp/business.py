from __future__ import annotations

import calendar
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from autofyi_mcp.errors import BusinessRuleError

CENT = Decimal("0.01")
AMOUNT_TOLERANCE = Decimal("0.011")
SUBTOTAL_WARNING_TOLERANCE = Decimal("0.02")
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def money(value: Any, *, label: str = "amount") -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BusinessRuleError(f"{label} is not a valid monetary amount: {value!r}") from exc
    if not amount.is_finite():
        raise BusinessRuleError(f"{label} must be a finite monetary amount.")
    return amount


def amount_float(value: Decimal) -> float:
    return float(value.quantize(CENT, rounding=ROUND_HALF_UP))


def amounts_equal(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) <= AMOUNT_TOLERANCE


def normalized(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def legal_name_key(value: Any) -> str:
    """Canonical comparison key; scores still rank candidates but do not decide identity."""
    text = normalized(value).replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    aliases = {
        "limited": "ltd",
        "ltd": "ltd",
        "company": "co",
        "co": "co",
    }
    tokens = [aliases.get(token, token) for token in text.split()]
    return " ".join(tokens)


def name_relation(query: Any, candidate: Any) -> str:
    if normalized(query) == normalized(candidate):
        return "strict_exact"
    if legal_name_key(query) == legal_name_key(candidate):
        return "legal_suffix_equivalent"
    return "ai_review_candidate"


def allocation_candidates(client_info: dict[str, Any]) -> list[dict[str, Any]]:
    allocations = client_info.get("allocations")
    if not allocations:
        return []
    if isinstance(allocations, list):
        return [item for item in allocations if isinstance(item, dict)]
    if isinstance(allocations, dict) and isinstance(allocations.get("lines"), list):
        return [allocations]
    if isinstance(allocations, dict):
        return [
            item
            for item in allocations.values()
            if isinstance(item, dict) and isinstance(item.get("lines"), list)
        ]
    return []


def plan_identity(plan: dict[str, Any], index: int = 0) -> str:
    return str(plan.get("key") or plan.get("reference") or f"allocation-{index + 1}").strip()


def plan_automation_blocker(plan: dict[str, Any]) -> str | None:
    """Mirror the frontend's statusBlocksAutomation and archive behavior."""
    status = normalized(plan.get("status"))
    if status and status != "approved":
        return (
            f"Prepared allocation status is {plan.get('status')!r}; the frontend excludes "
            "manager, review, ignored, and unknown non-approved statuses from automation."
        )
    if plan.get("archivedAt"):
        return "This prepared allocation is archived and cannot be automated."
    return None


def select_allocation_plan(
    client_info: dict[str, Any], invoice_key: str | None = None
) -> dict[str, Any]:
    plans = allocation_candidates(client_info)
    if not plans:
        raise BusinessRuleError(
            "This client has no prepared allocation plan. Use the direct-invoice workflow or prepare allocations first."
        )
    if invoice_key:
        wanted = normalized(invoice_key)
        matches = [
            plan
            for index, plan in enumerate(plans)
            if wanted
            in {
                normalized(plan_identity(plan, index)),
                normalized(plan.get("key")),
                normalized(plan.get("reference")),
            }
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise BusinessRuleError(
                f"No allocation plan matches {invoice_key!r}. Available plans: "
                + ", ".join(plan_identity(plan, i) for i, plan in enumerate(plans))
            )
        raise BusinessRuleError(f"Allocation key {invoice_key!r} is not unique.")
    if len(plans) > 1:
        raise BusinessRuleError(
            "This client has multiple allocation plans. Choose invoice_key from: "
            + ", ".join(plan_identity(plan, i) for i, plan in enumerate(plans))
        )
    return plans[0]


def _line_net(line: dict[str, Any], line_amount_types: str) -> Decimal:
    if line.get("net") is not None:
        return money(line["net"], label="allocation line net")
    line_amount = money(line.get("xeroAmount", 0), label="Xero line amount")
    if line_amount_types == "Inclusive":
        return line_amount - money(line.get("taxAmount", 0), label="Xero tax amount")
    return line_amount


def service_lines(plan: dict[str, Any]) -> tuple[dict[str, float], list[str]]:
    lines = plan.get("lines")
    if not isinstance(lines, list) or not lines:
        raise BusinessRuleError("The prepared allocation has no service lines.")
    result: dict[str, float] = {}
    seen: set[str] = set()
    warnings: list[str] = []
    line_amount_types = str(plan.get("lineAmountTypes") or "Exclusive")
    total = Decimal("0")
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            raise BusinessRuleError(f"Allocation line {index + 1} is not an object.")
        name = str(line.get("description") or "").strip()
        if not name:
            raise BusinessRuleError(f"Allocation line {index + 1} has an empty description.")
        key = normalized(name)
        if key in seen:
            raise BusinessRuleError(
                f"Duplicate service line name {name!r}. Rename prepared lines so every description is unique."
            )
        seen.add(key)
        net = _line_net(line, line_amount_types)
        if net <= 0:
            raise BusinessRuleError(f"Service line {name!r} must have a positive net amount.")
        result[name] = amount_float(net)
        total += net

    subtotal_value = plan.get("subTotal")
    if subtotal_value is not None:
        subtotal = money(subtotal_value, label="prepared subtotal")
        if abs(total - subtotal) > SUBTOTAL_WARNING_TOLERANCE:
            warnings.append(
                f"Prepared service lines total {amount_float(total):.2f}, while the recorded Xero subtotal is "
                f"{amount_float(subtotal):.2f}. Prepared lines remain the operational source of truth; review this difference before confirming."
            )
    if line_amount_types == "Inclusive":
        warnings.append("Tax-inclusive template: service net amounts exclude line tax.")
    return result, warnings


def find_service_line(plan: dict[str, Any], selector: str) -> dict[str, Any]:
    wanted = normalized(selector)
    lines = [line for line in plan.get("lines", []) if isinstance(line, dict)]
    exact = [
        line
        for line in lines
        if wanted
        in {
            normalized(line.get("id")),
            normalized(line.get("description")),
        }
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise BusinessRuleError(f"Service selector {selector!r} matches multiple lines.")
    tagged = [line for line in lines if normalized(line.get("tag")) == wanted]
    if len(tagged) == 1:
        return tagged[0]
    if len(tagged) > 1:
        choices = ", ".join(str(line.get("description") or line.get("id")) for line in tagged)
        raise BusinessRuleError(
            f"Tag {selector!r} matches multiple service lines. Select one exact description or id: {choices}"
        )
    choices = ", ".join(str(line.get("description") or line.get("id")) for line in lines)
    raise BusinessRuleError(f"No service line matches {selector!r}. Available lines: {choices}")


def service_line_amount(plan: dict[str, Any], line: dict[str, Any]) -> Decimal:
    return _line_net(line, str(plan.get("lineAmountTypes") or "Exclusive"))


def repeat_day(plan: dict[str, Any], client_info: dict[str, Any]) -> int:
    raw = plan.get("dd")
    if raw is not None and str(raw).strip():
        try:
            day = int(str(raw).strip())
        except ValueError as exc:
            raise BusinessRuleError(f"Prepared repeat day is invalid: {raw!r}") from exc
        if 1 <= day <= 31:
            return day

    identity = normalized(plan.get("key"))
    invoices = client_info.get("xero_invoices") or []
    if isinstance(invoices, list):
        ordered = sorted(
            (invoice for invoice in invoices if isinstance(invoice, dict)),
            key=lambda invoice: normalized(invoice.get("RepeatingInvoiceID")) != identity,
        )
        for invoice in ordered:
            value = (invoice.get("Schedule") or {}).get("NextScheduledDateString")
            if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                return int(value[-2:])
    raise BusinessRuleError(
        "The repeating day is missing from allocations and the Xero schedule. It cannot be guessed safely."
    )


def job_date(day: int, month: int, year: int) -> tuple[str, list[str]]:
    if month < 1 or month > 12:
        raise BusinessRuleError("Month must be between 1 and 12.")
    if year < 2000 or year > 2100:
        raise BusinessRuleError("Year must be between 2000 and 2100.")
    final_day = min(day, calendar.monthrange(year, month)[1])
    warnings: list[str] = []
    if final_day != day:
        warnings.append(
            f"Repeating day {day} does not exist in {MONTH_NAMES[month - 1]} {year}; using month-end day {final_day}. Verify the FYI interim date."
        )
    return f"{final_day:02d} {MONTH_NAMES[month - 1]} {year}", warnings


def parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def interim_rows_for_date(interims: Iterable[dict[str, Any]], wanted: str) -> list[dict[str, Any]]:
    target = parse_date(wanted)
    if target is None:
        return []
    return [row for row in interims if parse_date(row.get("date")) == target]


def interim_rows_for_month(
    interims: Iterable[dict[str, Any]], year: int, month: int
) -> list[dict[str, Any]]:
    """FYI interim rows whose date falls in one calendar month (day may differ from the invoice)."""
    result: list[dict[str, Any]] = []
    for row in interims:
        parsed = parse_date(row.get("date"))
        if parsed is not None and parsed.year == year and parsed.month == month:
            result.append(row)
    return result


def invoice_service_lines(invoice: dict[str, Any]) -> tuple[dict[str, float], list[str]]:
    """Turn one Xero invoice's lines into the {description: net} shape the split logic expects.

    Duplicate descriptions are summed (with a warning) so a preview never silently drops money.
    """
    lines = invoice.get("lines")
    if not isinstance(lines, list) or not lines:
        raise BusinessRuleError("Each preview invoice needs at least one service line.")
    result: dict[str, float] = {}
    warnings: list[str] = []
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            raise BusinessRuleError(f"Invoice line {index + 1} is not an object.")
        name = str(line.get("description") or "").strip()
        if not name:
            raise BusinessRuleError(f"Invoice line {index + 1} has an empty description.")
        net = money(line.get("net"), label=f"invoice line {name!r} net")
        if net <= 0:
            raise BusinessRuleError(f"Invoice service line {name!r} must have a positive net amount.")
        if name in result:
            warnings.append(
                f"Invoice repeats service line {name!r}; the preview sums the duplicate net amounts."
            )
            result[name] = amount_float(money(result[name]) + net)
        else:
            result[name] = amount_float(net)
    return result, warnings


# Curated service keywords keep job suggestions predictable instead of fuzzy token soup.
_SERVICE_MATCH_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rct", ("rct",)),
    ("vat", ("vat",)),
    ("xero", ("xero",)),
    ("subscription", ("xero", "subscription")),
    ("payroll", ("payroll", "wages")),
    ("wages", ("payroll", "wages")),
    ("income tax", ("income tax", "tax", "accounts")),
    ("bookkeeping", ("bookkeeping", "vat")),
    ("accounts", ("accounts",)),
    ("client care", ("client care",)),
)


def service_search_terms(description: str) -> set[str]:
    """Job-name search terms for one invoice service line, from a curated accounting map."""
    norm = normalized(description)
    terms: set[str] = set()
    for key, mapped in _SERVICE_MATCH_TERMS:
        if key in norm:
            terms.update(mapped)
    if not terms:
        terms = {word for word in norm.split() if len(word) > 3}
    return terms


def suggest_jobs_for_service(
    description: str, jobs: Iterable[dict[str, Any]], month: int, year: int
) -> list[dict[str, Any]]:
    """Suggest (never assign) live FYI jobs a service line could map to, period matches first."""
    terms = service_search_terms(description)
    month_token = MONTH_NAMES[month - 1].casefold() if 1 <= month <= 12 else ""
    year_token = str(year)
    candidates: list[dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict) or job.get("is_billing_job"):
            continue
        name = str(job.get("job_name") or job.get("name") or "").strip()
        if not name:
            continue
        normalized_name = normalized(name)
        if not any(term in normalized_name for term in terms):
            continue
        month_match = bool(month_token and month_token in normalized_name)
        year_match = year_token in normalized_name
        candidates.append(
            {
                "job_name": name,
                "work_amount": job.get("work_amount"),
                "period_match": month_match or year_match,
                "_month_match": month_match,
                "_year_match": year_match,
            }
        )
    # Rank exact-month matches above year-only matches above the rest, then by name.
    candidates.sort(
        key=lambda item: (
            not item["_month_match"],
            not item["_year_match"],
            normalized(item["job_name"]),
        )
    )
    for item in candidates:
        del item["_month_match"], item["_year_match"]
    return candidates


def amount_multiset(values: Iterable[Any]) -> Counter[int]:
    return Counter(int((money(value) * 100).to_integral_value()) for value in values)


def classify_month_billing_state(
    expected_service_lines: dict[str, float], live_amounts: Iterable[Any]
) -> dict[str, Any]:
    """Reconcile one FYI month's current rows against its prepared service split."""
    expected_by_cents: dict[int, list[str]] = defaultdict(list)
    for service, value in expected_service_lines.items():
        cents = int((money(value) * 100).to_integral_value())
        expected_by_cents[cents].append(service)

    live_money = [money(value) for value in live_amounts]
    expected_counter = Counter(
        {cents: len(services) for cents, services in expected_by_cents.items()}
    )
    live_counter = amount_multiset(live_money)
    total = sum((money(value) for value in expected_service_lines.values()), Decimal("0"))
    live_total = sum(live_money, Decimal("0"))

    remaining: list[dict[str, Any]] = []
    consumed: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    for cents, names in expected_by_cents.items():
        amount = cents / 100
        live_count = live_counter.get(cents, 0)
        expected_count = len(names)
        if live_count == expected_count:
            remaining.extend({"service": name, "amount": amount} for name in names)
        elif live_count == 0:
            consumed.extend({"service": name, "amount": amount} for name in names)
        elif expected_count == 1:
            remaining.append({"service": names[0], "amount": amount})
        else:
            ambiguous.append(
                {
                    "amount": amount,
                    "candidate_services": names,
                    "remaining_row_count": live_count,
                    "expected_row_count": expected_count,
                    "reason": "FYI rows contain date and amount but no service label.",
                }
            )

    if not live_money:
        state = "no_rows_missing_or_fully_consumed"
    elif live_counter == expected_counter:
        state = "fully_split_unallocated"
    elif len(live_money) == 1 and amounts_equal(live_money[0], total):
        state = "unsplit"
    elif amounts_equal(live_total, total):
        state = "partially_split_needs_completion"
    elif all(live_counter[cents] <= expected_counter.get(cents, 0) for cents in live_counter):
        state = "partially_allocated"
    else:
        state = "inconsistent"

    return {
        "state": state,
        "expected_invoice_net": amount_float(total),
        "expected_service_lines": expected_service_lines,
        "live_rows": [amount_float(value) for value in live_money],
        "live_total": amount_float(live_total),
        "remaining_services": remaining,
        "consumed_services": consumed,
        "ambiguous_same_amount_services": ambiguous,
    }


def equal_divide(total: Decimal, count: int) -> list[Decimal]:
    if count <= 0:
        raise BusinessRuleError("At least one job is required.")
    total_cents = int((total * 100).to_integral_value())
    base = total_cents // count
    cents = [base] * (count - 1) + [total_cents - base * (count - 1)]
    return [Decimal(value) / 100 for value in cents]


def allocation_case(month_count: int, job_count: int) -> str:
    if month_count == 1 and job_count == 1:
        return "case_1_monthly_1x1"
    if month_count > 1 and job_count == 1:
        return "case_2_multi_month_1_job"
    if month_count == 1 and job_count > 1:
        return "case_3_one_month_multi_job"
    return "case_4_multi_month_multi_job"


def summarize_client(wrapper: dict[str, Any]) -> dict[str, Any]:
    info = wrapper.get("client_info") if isinstance(wrapper.get("client_info"), dict) else {}
    fyi = info.get("fyi") if isinstance(info.get("fyi"), dict) else {}
    useful_terms = (
        "client id",
        "name",
        "partner",
        "manager",
        "entity",
        "vat",
        "rct",
        "payroll",
        "accounts",
        "income tax",
        "xero",
        "registered address",
    )
    operational = {
        key: value
        for key, value in fyi.items()
        if value not in (None, "", [], {}) and any(term in normalized(key) for term in useful_terms)
    }
    plans = allocation_candidates(info)
    return {
        "id": wrapper.get("id"),
        "name": wrapper.get("name"),
        "valid_json": wrapper.get("valid_json"),
        "operational_fyi": operational,
        "xero_repeating_invoice_count": len(info.get("xero_invoices") or []),
        "allocation_plan_count": len(plans),
        "allocation_plans": [
            {
                "key": plan_identity(plan, index),
                "reference": plan.get("reference"),
                "repeat_day": plan.get("dd"),
                "billing_job_name": plan.get("billingJobName"),
                "service_lines": [
                    {
                        "id": line.get("id"),
                        "description": line.get("description"),
                        "net": line.get("net"),
                        "tag": line.get("tag"),
                    }
                    for line in plan.get("lines", [])
                    if isinstance(line, dict)
                ],
            }
            for index, plan in enumerate(plans)
        ],
        "billing_mode": "xero_repeating_with_allocations" if plans else "direct_or_unprepared",
    }
