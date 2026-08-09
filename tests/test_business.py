from __future__ import annotations

from decimal import Decimal

import pytest

from autofyi_mcp.business import (
    allocation_case,
    classify_month_billing_state,
    equal_divide,
    find_service_line,
    interim_rows_for_month,
    invoice_service_lines,
    job_date,
    money,
    name_relation,
    plan_automation_blocker,
    select_allocation_plan,
    service_lines,
    suggest_jobs_for_service,
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


def test_invoice_service_lines_sums_duplicate_descriptions() -> None:
    invoice = {
        "lines": [
            {"description": "Xero Subscription", "net": 40},
            {"description": "Wages Processing", "net": 120},
            {"description": "Xero Subscription", "net": 35},
        ]
    }
    lines, warnings = invoice_service_lines(invoice)
    assert lines == {"Xero Subscription": 75.0, "Wages Processing": 120.0}
    assert any("repeats service line" in w for w in warnings)


@pytest.mark.parametrize("net", [0, -5])
def test_invoice_service_lines_rejects_nonpositive_net(net: float) -> None:
    with pytest.raises(BusinessRuleError):
        invoice_service_lines({"lines": [{"description": "VAT", "net": net}]})


def test_invoice_service_lines_requires_lines() -> None:
    with pytest.raises(BusinessRuleError):
        invoice_service_lines({"lines": []})


def test_invoice_split_reuses_billing_state_logic() -> None:
    # A €300 interim against a 3-line invoice should classify as unsplit (single lump row).
    lines, _ = invoice_service_lines(
        {
            "lines": [
                {"description": "Monthly Fee", "net": 260},
                {"description": "Xero Subscription", "net": 40},
            ]
        }
    )
    state = classify_month_billing_state(lines, [300])
    assert state["state"] == "unsplit"
    assert state["expected_invoice_net"] == 300.0


def test_interim_rows_for_month_matches_by_month_not_day() -> None:
    interims = [
        {"date": "01 Oct 2025", "amount": "300.00"},
        {"date": "15 Oct 2025", "amount": "50.00"},
        {"date": "01 Nov 2025", "amount": "300.00"},
    ]
    rows = interim_rows_for_month(interims, 2025, 10)
    assert [row["amount"] for row in rows] == ["300.00", "50.00"]


def test_suggest_jobs_prefers_period_match_and_skips_billing_job() -> None:
    jobs = [
        {"job_name": "RCT - October 2025", "work_amount": 157.5, "is_billing_job": False},
        {"job_name": "RCT - November 2025", "work_amount": 171.25, "is_billing_job": False},
        {"job_name": "VAT Return - Sept-Oct 2025", "work_amount": 215.0, "is_billing_job": False},
        {"job_name": "Billing Job - Example", "work_amount": -500.0, "is_billing_job": True},
    ]
    result = suggest_jobs_for_service("Monthly RCT Compliance", jobs, month=10, year=2025)
    names = [candidate["job_name"] for candidate in result]
    # October is an exact-month match so it ranks above the year-only November match.
    assert names == ["RCT - October 2025", "RCT - November 2025"]
    assert all(candidate["period_match"] is True for candidate in result)
    assert "_month_match" not in result[0]


def test_suggest_jobs_returns_empty_when_no_matching_service() -> None:
    jobs = [{"job_name": "RCT - October 2025", "work_amount": 157.5, "is_billing_job": False}]
    assert suggest_jobs_for_service("Income tax & accounts", jobs, month=10, year=2025) == []
