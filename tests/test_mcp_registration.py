from autofyi_mcp.server import mcp


def test_expected_mcp_tools_are_registered() -> None:
    names = set(mcp._tool_manager._tools)  # intentional protocol-registration smoke check
    assert names == {
        "describe_autofyi",
        "autofyi_health",
        "get_client_catalog_status",
        "describe_client_catalog",
        "query_client_catalog",
        "search_clients",
        "find_client",
        "get_client_information",
        "get_client_jobs_to_invoice",
        "get_jobs_and_interim_table",
        "inspect_client_billing_state",
        "plan_client_billing",
        "prepare_interim_split",
        "prepare_job_allocation",
        "prepare_direct_invoice",
        "get_prepared_action",
        "cancel_prepared_action",
        "execute_confirmed_action",
    }
