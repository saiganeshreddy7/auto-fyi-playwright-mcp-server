from __future__ import annotations

from dataclasses import replace

import pytest

from autofyi_mcp.errors import APIError, BusinessRuleError, ConfirmationError
from autofyi_mcp.models import AllocationJob, BillingMonth, CatalogFilter, DirectInvoiceJob
from autofyi_mcp.service import AutoFYIService


async def test_find_client_auto_selects_only_unique_exact(fake_api, read_settings) -> None:
    service = AutoFYIService(fake_api, read_settings)
    result = await service.find_client("Example Services Limited")
    assert result["resolution"] == "selected_unique_exact_match"
    fake_api.search_result["matches"] = [
        {"id": "C-100", "name": "Example Services Limited", "score": 99.9}
    ]
    result = await service.find_client("Example")
    assert result["resolution"] == "ai_candidate_review_needs_confirmation"


async def test_two_exact_names_need_user_selection(fake_api, read_settings) -> None:
    fake_api.search_result["matches"] = [
        {"id": "1", "name": "Same", "score": 100},
        {"id": "2", "name": "Same", "score": 100},
    ]
    service = AutoFYIService(fake_api, read_settings)
    result = await service.find_client("Same")
    assert result["resolution"] == "ai_candidate_review_needs_confirmation"


async def test_legal_suffix_match_is_proposed_not_auto_selected(fake_api, read_settings) -> None:
    fake_api.search_result["matches"] = [
        {"id": "C-100", "name": "Example Services Ltd.", "score": 95}
    ]
    service = AutoFYIService(fake_api, read_settings)
    result = await service.find_client("Example Services Limited")
    assert result["resolution"] == "likely_legal_name_match_needs_confirmation"
    assert result["likely_candidate"]["id"] == "C-100"


async def test_plan_repeating_and_direct_clients(fake_api, read_settings) -> None:
    service = AutoFYIService(fake_api, read_settings)
    repeating = await service.plan_client_billing("C-100", 7, 2026)
    assert repeating["mode"] == "xero_repeating_with_allocations"
    assert repeating["invoice_amount_net"] == 650
    direct = await service.plan_client_billing("C-200", 7, 2026)
    assert direct["mode"] == "direct_or_unprepared"


async def test_catalog_schema_and_structured_query_are_forwarded(fake_api, read_settings) -> None:
    service = AutoFYIService(fake_api, read_settings)
    schema = await service.describe_client_catalog("VAT Basis")
    assert schema["requested_column"] == "VAT Basis"
    assert schema["include_common_values"] is False

    result = await service.query_client_catalog(
        "count",
        filters=[CatalogFilter(column="VAT Registered?", operator="eq", value=True)],
    )
    assert result["result"]["count"] == 2
    assert fake_api.catalog_query_calls == [
        {
            "operation": "count",
            "columns": [],
            "filters": [
                {"column": "VAT Registered?", "operator": "eq", "value": True}
            ],
            "group_by": None,
            "limit": 20,
            "offset": 0,
        }
    ]


async def test_prepare_split_from_one_full_interim(fake_api, read_settings) -> None:
    service = AutoFYIService(fake_api, read_settings)
    result = await service.prepare_interim_split("C-100", 7, 2026)
    assert result["prepared"] is True
    assert result["action"] == "split_interim"
    assert result["preview"]["new_service_rows"]["Weekly Payroll"] == 400
    assert result["required_confirmation"] == "SPLIT Example Services Limited 01 Jul 2026"
    assert fake_api.write_calls == []


async def test_already_split_is_noop(fake_api, read_settings) -> None:
    fake_api.interims = [{"date": "01 Jul 2026", "amount": value} for value in [120, 80, 400, 50]]
    service = AutoFYIService(fake_api, read_settings)
    result = await service.prepare_interim_split("C-100", 7, 2026)
    assert result["state"] == "fully_split_unallocated"
    assert "confirmation_id" not in result


async def test_split_total_mismatch_is_blocked(fake_api, read_settings) -> None:
    fake_api.interims = [{"date": "01 Jul 2026", "amount": 649}]
    service = AutoFYIService(fake_api, read_settings)
    with pytest.raises(BusinessRuleError, match="cannot be safely split"):
        await service.prepare_interim_split("C-100", 7, 2026)


async def test_partially_allocated_month_is_not_split_again(fake_api, read_settings) -> None:
    fake_api.interims = [{"date": "01 Jul 2026", "amount": value} for value in [120, 80, 400]]
    service = AutoFYIService(fake_api, read_settings)
    result = await service.prepare_interim_split("C-100", 7, 2026)
    assert result["state"] == "partially_allocated"
    assert result["prepared"] is False


async def test_partial_split_can_be_completed(fake_api, read_settings) -> None:
    fake_api.interims = [{"date": "01 Jul 2026", "amount": value} for value in [120, 530]]
    service = AutoFYIService(fake_api, read_settings)
    result = await service.prepare_interim_split("C-100", 7, 2026)
    assert result["prepared"] is True
    assert result["preview"]["current_billing_state"] == ("partially_split_needs_completion")


async def test_inspect_client_billing_state_is_read_only(fake_api, read_settings) -> None:
    fake_api.interims = [{"date": "01 Jul 2026", "amount": value} for value in [120, 80, 400]]
    service = AutoFYIService(fake_api, read_settings)
    result = await service.inspect_client_billing_state("C-100", [BillingMonth(month=7, year=2026)])
    assert result["months"][0]["state"] == "partially_allocated"
    assert result["allocation_rule"].startswith("Prepare one complete request")
    assert fake_api.write_calls == []


async def test_frontend_review_status_is_blocked_for_writes(fake_api, read_settings) -> None:
    fake_api.wrappers["C-100"]["client_info"]["allocations"]["status"] = "review"
    service = AutoFYIService(fake_api, read_settings)
    with pytest.raises(BusinessRuleError, match="excludes"):
        await service.prepare_interim_split("C-100", 7, 2026)


async def test_prepared_client_id_mismatch_is_blocked(fake_api, read_settings) -> None:
    fake_api.wrappers["C-100"]["client_info"]["allocations"]["clientId"] = "WRONG"
    service = AutoFYIService(fake_api, read_settings)
    with pytest.raises(BusinessRuleError, match="does not match"):
        await service.prepare_interim_split("C-100", 7, 2026)


async def test_prepare_monthly_case_1(fake_api, read_settings) -> None:
    fake_api.interims = [{"date": "01 Jul 2026", "amount": 120}]
    service = AutoFYIService(fake_api, read_settings)
    result = await service.prepare_job_allocation(
        "C-100",
        "Monthly Payroll",
        [BillingMonth(month=7, year=2026)],
        [AllocationJob(job_name="Monthly Payroll - Jul 2026")],
    )
    assert result["preview"]["case"] == "case_1_monthly_1x1"
    assert result["preview"]["jobs"][0]["amount"] == 120


async def test_prepare_vat_case_2(fake_api, read_settings) -> None:
    fake_api.interims = [
        {"date": "01 Jul 2026", "amount": 80},
        {"date": "01 Aug 2026", "amount": 80},
    ]
    service = AutoFYIService(fake_api, read_settings)
    result = await service.prepare_job_allocation(
        "C-100",
        "VAT 2 Monthly",
        [BillingMonth(month=7, year=2026), BillingMonth(month=8, year=2026)],
        [AllocationJob(job_name="VAT Jul-Aug 2026")],
    )
    assert result["preview"]["case"] == "case_2_multi_month_1_job"
    assert result["preview"]["jobs"][0]["amount"] == 160


async def test_prepare_weekly_case_3_equal_division(fake_api, read_settings) -> None:
    fake_api.interims = [{"date": "01 Jul 2026", "amount": 400}]
    service = AutoFYIService(fake_api, read_settings)
    result = await service.prepare_job_allocation(
        "C-100",
        "Same Week",
        [BillingMonth(month=7, year=2026)],
        [
            AllocationJob(job_name="Week 1"),
            AllocationJob(job_name="Week 2"),
            AllocationJob(job_name="Week 3"),
        ],
    )
    assert result["preview"]["case"] == "case_3_one_month_multi_job"
    assert [job["amount"] for job in result["preview"]["jobs"]] == [133.33, 133.33, 133.34]


async def test_prepare_case_4_explicit_amounts(fake_api, read_settings) -> None:
    fake_api.interims = [
        {"date": "01 Jul 2026", "amount": 80},
        {"date": "01 Aug 2026", "amount": 80},
    ]
    service = AutoFYIService(fake_api, read_settings)
    with pytest.raises(BusinessRuleError, match="Do not mix"):
        await service.prepare_job_allocation(
            "C-100",
            "VAT Service",
            [BillingMonth(month=7, year=2026), BillingMonth(month=8, year=2026)],
            [
                AllocationJob(job_name="Week 1", amount=60),
                AllocationJob(job_name="Week 2", amount=100),
            ],
        )


async def test_missing_split_month_is_blocked(fake_api, read_settings) -> None:
    fake_api.interims = [{"date": "01 Jul 2026", "amount": 80}]
    service = AutoFYIService(fake_api, read_settings)
    with pytest.raises(BusinessRuleError, match="no_rows_missing_or_fully_consumed"):
        await service.prepare_job_allocation(
            "C-100",
            "VAT Service",
            [BillingMonth(month=7, year=2026), BillingMonth(month=8, year=2026)],
            [AllocationJob(job_name="VAT Jul-Aug 2026")],
        )


async def test_ambiguous_same_amount_requires_user_confirmation(fake_api, read_settings) -> None:
    plan = fake_api.wrappers["C-100"]["client_info"]["allocations"]
    plan["lines"][3]["net"] = 80
    fake_api.interims = [{"date": "01 Jul 2026", "amount": 80}]
    service = AutoFYIService(fake_api, read_settings)
    with pytest.raises(BusinessRuleError, match="confirm_ambiguous_remaining=true"):
        await service.prepare_job_allocation(
            "C-100",
            "VAT Service",
            [BillingMonth(month=7, year=2026)],
            [AllocationJob(job_name="VAT Jul-Aug 2026")],
        )
    result = await service.prepare_job_allocation(
        "C-100",
        "VAT Service",
        [BillingMonth(month=7, year=2026)],
        [AllocationJob(job_name="VAT Jul-Aug 2026")],
        confirm_ambiguous_remaining=True,
    )
    assert result["preview"]["allocation_scope"] == "one_complete_service_type_only"
    assert any("user confirmed" in warning for warning in result["preview"]["warnings"])


async def test_unknown_job_is_blocked(fake_api, read_settings) -> None:
    fake_api.interims = [{"date": "01 Jul 2026", "amount": 120}]
    service = AutoFYIService(fake_api, read_settings)
    with pytest.raises(BusinessRuleError, match="Available jobs"):
        await service.prepare_job_allocation(
            "C-100",
            "Monthly Payroll",
            [BillingMonth(month=7, year=2026)],
            [AllocationJob(job_name="Invented Job")],
        )


async def test_mixed_allocation_amounts_are_blocked(fake_api, read_settings) -> None:
    fake_api.interims = [{"date": "01 Jul 2026", "amount": 400}]
    service = AutoFYIService(fake_api, read_settings)
    with pytest.raises(BusinessRuleError, match="every allocation job"):
        await service.prepare_job_allocation(
            "C-100",
            "Weekly Payroll",
            [BillingMonth(month=7, year=2026)],
            [AllocationJob(job_name="Week 1", amount=100), AllocationJob(job_name="Week 2")],
        )


async def test_direct_invoice_three_cases(fake_api, read_settings) -> None:
    service = AutoFYIService(fake_api, read_settings)
    explicit = await service.prepare_direct_invoice(
        "C-200", [DirectInvoiceJob(job_name="Direct Accounts Job", amount=500)]
    )
    assert explicit["preview"]["case"] == "amounts_given"
    distributed = await service.prepare_direct_invoice(
        "C-200", [DirectInvoiceJob(job_name="Direct Accounts Job")], invoice_amount=500
    )
    assert distributed["preview"]["case"] == "invoice_amount_given"
    automatic = await service.prepare_direct_invoice(
        "C-200", [DirectInvoiceJob(job_name="Direct Accounts Job")]
    )
    assert automatic["preview"]["case"] == "fyi_auto"


async def test_repeating_direct_invoice_requires_override(fake_api, read_settings) -> None:
    service = AutoFYIService(fake_api, read_settings)
    with pytest.raises(BusinessRuleError, match="duplicate billing"):
        await service.prepare_direct_invoice(
            "C-100", [DirectInvoiceJob(job_name="Direct Accounts Job", amount=500)]
        )
    result = await service.prepare_direct_invoice(
        "C-100",
        [DirectInvoiceJob(job_name="Direct Accounts Job", amount=500)],
        allow_repeating_client=True,
    )
    assert any("duplicate billing" in warning for warning in result["preview"]["warnings"])


async def test_writes_disabled_sends_nothing(fake_api, read_settings) -> None:
    service = AutoFYIService(fake_api, read_settings)
    prepared = await service.prepare_direct_invoice(
        "C-200", [DirectInvoiceJob(job_name="Direct Accounts Job", amount=500)]
    )
    result = await service.execute_confirmed_action(
        prepared["confirmation_id"], prepared["required_confirmation"]
    )
    assert result["state"] == "writes_disabled"
    assert fake_api.write_calls == []


async def test_execute_success_is_single_use(fake_api, read_settings) -> None:
    service = AutoFYIService(fake_api, replace(read_settings, enable_writes=True))
    prepared = await service.prepare_direct_invoice(
        "C-200", [DirectInvoiceJob(job_name="Direct Accounts Job", amount=500)]
    )
    with pytest.raises(ConfirmationError):
        await service.execute_confirmed_action(prepared["confirmation_id"], "yes")
    result = await service.execute_confirmed_action(
        prepared["confirmation_id"], prepared["required_confirmation"]
    )
    assert result["state"] == "success"
    assert len(fake_api.write_calls) == 1
    with pytest.raises(ConfirmationError, match="already used"):
        await service.execute_confirmed_action(
            prepared["confirmation_id"], prepared["required_confirmation"]
        )


async def test_write_timeout_is_unknown_and_not_retried(fake_api, read_settings) -> None:
    fake_api.write_result = APIError("timeout", outcome_unknown=True)
    service = AutoFYIService(fake_api, replace(read_settings, enable_writes=True))
    prepared = await service.prepare_direct_invoice(
        "C-200", [DirectInvoiceJob(job_name="Direct Accounts Job", amount=500)]
    )
    result = await service.execute_confirmed_action(
        prepared["confirmation_id"], prepared["required_confirmation"]
    )
    assert result["state"] == "outcome_unknown_do_not_retry"
    assert len(fake_api.write_calls) == 1


async def test_preview_split_from_invoices_no_plan_client(fake_api, read_settings) -> None:
    service = AutoFYIService(fake_api, read_settings)
    result = await service.preview_split_from_invoices(
        "C-200",
        [
            {
                "reference": "INV-1",
                "date": "01 Jul 2026",
                "lines": [
                    {"description": "Monthly Payroll", "net": 250},
                    {"description": "VAT Service", "net": 400},
                ],
            }
        ],
    )
    assert result["mode"] == "preview_from_xero_invoices"
    assert result["binding"] is False
    assert result["has_fyi_allocation_plan"] is False
    month = result["months"][0]
    assert month["invoice_net_total"] == 650.0
    assert month["matched_interim"]["found"] is True
    assert month["matched_interim"]["total"] == 650.0
    assert month["split_state"] == "unsplit"
    assert month["amount_check"] == "matches"
    # VAT Service line should suggest the VAT job; every service line here matches a job.
    assert month["service_lines_without_job"] == []


async def test_preview_flags_invoice_without_interim_and_amount_mismatch(
    fake_api, read_settings
) -> None:
    service = AutoFYIService(fake_api, read_settings)
    result = await service.preview_split_from_invoices(
        "C-200",
        [
            {
                "reference": "INV-AUG",
                "date": "01 Aug 2026",
                "lines": [{"description": "Monthly Payroll", "net": 300}],
            }
        ],
    )
    month = result["months"][0]
    assert month["matched_interim"]["found"] is False
    assert result["invoices_without_interim"] == [
        {"invoice_reference": "INV-AUG", "invoice_date": "01 Aug 2026"}
    ]
    # The unmatched July interim (650) should surface too.
    assert {"date": "01 Jul 2026", "amount": 650} in result["interims_without_invoice"]


async def test_preview_warns_when_client_already_has_plan(fake_api, read_settings) -> None:
    service = AutoFYIService(fake_api, read_settings)
    result = await service.preview_split_from_invoices(
        "C-100",
        [
            {
                "date": "01 Jul 2026",
                "lines": [{"description": "Monthly Payroll", "net": 650}],
            }
        ],
    )
    assert result["has_fyi_allocation_plan"] is True
    assert any("operational source of truth" in w for w in result["warnings"])


async def test_preview_requires_at_least_one_invoice(fake_api, read_settings) -> None:
    service = AutoFYIService(fake_api, read_settings)
    with pytest.raises(BusinessRuleError):
        await service.preview_split_from_invoices("C-200", [])
