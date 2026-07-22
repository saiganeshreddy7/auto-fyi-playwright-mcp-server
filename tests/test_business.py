from __future__ import annotations

from decimal import Decimal

import pytest

from autofyi_mcp.business import (
    allocation_case,
    classify_month_billing_state,
    equal_divide,
    find_service_line,
    job_date,
    money,
    name_relation,
    plan_automation_blocker,
    select_allocation_plan,
    service_lines,
)
from autofyi_mcp.errors import BusinessRuleError


def test_month_end_clamps_and_warns() -> None:
    value, warnings = job_date(31, 4, 2026)
    assert value == "30 Apr 2026"
    assert warnings


def test_leap_year_month_end() -> None:
    assert job_date(31, 2, 2028)[0] == "29 Feb 2028"
    assert job_date(31, 2, 2027)[0] == "28 Feb 2027"


def test_equal_division_last_job_takes_remainder() -> None:
    assert equal_divide(Decimal("100"), 3) == [
        Decimal("33.33"),
        Decimal("33.33"),
        Decimal("33.34"),
    ]


@pytest.mark.parametrize(
    ("months", "jobs", "expected"),
    [
        (1, 1, "case_1_monthly_1x1"),
        (2, 1, "case_2_multi_month_1_job"),
        (1, 4, "case_3_one_month_multi_job"),
        (2, 2, "case_4_multi_month_multi_job"),
    ],
)
def test_all_allocation_cases(months: int, jobs: int, expected: str) -> None:
    assert allocation_case(months, jobs) == expected


def test_prepared_lines_are_source_of_truth_with_subtotal_warning() -> None:
    plan = {
        "subTotal": 999,
        "lineAmountTypes": "Exclusive",
        "lines": [{"description": "Service", "net": 100}],
    }
    lines, warnings = service_lines(plan)
    assert lines == {"Service": 100.0}
    assert "operational source of truth" in warnings[0]


def test_tax_inclusive_fallback_calculates_net() -> None:
    plan = {
        "lineAmountTypes": "Inclusive",
        "lines": [{"description": "Service", "xeroAmount": 123, "taxAmount": 23}],
    }
    lines, warnings = service_lines(plan)
    assert lines == {"Service": 100.0}
    assert any("Tax-inclusive" in warning for warning in warnings)


@pytest.mark.parametrize(
    "lines",
    [
        [{"description": "", "net": 10}],
        [{"description": "A", "net": 0}],
        [{"description": "A", "net": 10}, {"description": " a ", "net": 20}],
    ],
)
def test_invalid_service_lines_are_blocked(lines: list[dict]) -> None:
    with pytest.raises(BusinessRuleError):
        service_lines({"lines": lines})


def test_ambiguous_tag_is_blocked() -> None:
    plan = {
        "lines": [
            {"id": "1", "description": "VAT A", "tag": "VAT"},
            {"id": "2", "description": "VAT B", "tag": "VAT"},
        ]
    }
    with pytest.raises(BusinessRuleError, match="multiple"):
        find_service_line(plan, "VAT")
    assert find_service_line(plan, "VAT A")["id"] == "1"


def test_multiple_allocation_plans_require_key() -> None:
    info = {
        "allocations": [
            {"key": "A", "lines": []},
            {"key": "B", "lines": []},
        ]
    }
    with pytest.raises(BusinessRuleError, match="multiple"):
        select_allocation_plan(info)
    assert select_allocation_plan(info, "B")["key"] == "B"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_money_is_blocked(value: str) -> None:
    with pytest.raises(BusinessRuleError):
        money(value)


@pytest.mark.parametrize("status", ["manager", "review", "ignored", "unexpected"])
def test_frontend_blocking_statuses_are_preserved(status: str) -> None:
    assert plan_automation_blocker({"status": status})
    assert plan_automation_blocker({"status": "approved"}) is None
    assert plan_automation_blocker({"status": ""}) is None


def test_archived_plan_is_blocked() -> None:
    assert plan_automation_blocker({"archivedAt": "2026-07-20T00:00:00Z"})


def test_legal_suffixes_are_equivalent_but_abbreviations_need_ai_review() -> None:
    assert name_relation("Example Design Limited", "Example Design Ltd.") == (
        "legal_suffix_equivalent"
    )
    assert name_relation("Accuracy Design Limited", "ACG Design Limited") == ("ai_review_candidate")


@pytest.mark.parametrize(
    ("live", "expected_state"),
    [
        ([340], "unsplit"),
        ([40, 60, 100, 40, 100], "fully_split_unallocated"),
        ([240, 100], "partially_split_needs_completion"),
        ([60, 100, 40, 100], "partially_allocated"),
        ([], "no_rows_missing_or_fully_consumed"),
        ([999], "inconsistent"),
    ],
)
def test_month_billing_state_classification(live: list[float], expected_state: str) -> None:
    expected = {
        "Weekly": 40,
        "Monthly": 60,
        "VAT": 100,
        "Xero Subscription": 40,
        "Annual": 100,
    }
    result = classify_month_billing_state(expected, live)
    assert result["state"] == expected_state


def test_partial_duplicate_amounts_are_reported_as_ambiguous() -> None:
    expected = {
        "Weekly": 40,
        "Monthly": 60,
        "VAT": 100,
        "Xero Subscription": 40,
        "Annual": 100,
    }
    result = classify_month_billing_state(expected, [60, 100, 40, 100])
    assert result["ambiguous_same_amount_services"] == [
        {
            "amount": 40.0,
            "candidate_services": ["Weekly", "Xero Subscription"],
            "remaining_row_count": 1,
            "expected_row_count": 2,
            "reason": "FYI rows contain date and amount but no service label.",
        }
    ]
