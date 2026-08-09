# Tool reference

## Discovery and information

### `describe_autofyi`

Returns the business rules, safe tool order, write status, and endpoint groups intentionally
excluded from MCP. Use this when the model needs to understand the workflow.

### `autofyi_health`

Calls `GET /health`. Returns deployed-backend reachability, FYI browser state, API base URL,
and whether this local MCP has writes enabled.

### `search_clients(query, limit=5)`

Calls `GET /clients/search`.

- Search scores rank candidates; scores do not decide client identity.
- One strict identical name: its ID may be used for the next read.
- `Ltd`/`Limited`, punctuation, case, and spacing are treated as legal-name equivalents, but
  Claude still asks the user to confirm a non-identical result.
- Other fuzzy results: Claude compares the full names, proposes one plausible candidate when
  appropriate, and asks “Is this the client you mean?”.
- No match: request another spelling or identifier.

### `find_client(query, detail_level="summary")`

Combines search and information lookup only when the search has one unique exact match.
Otherwise it stops at the choices. `summary` returns operational FYI fields and allocation
summaries; `full` returns the complete client file and should be used only when needed.
It does not automatically load a legal-suffix or AI-judged candidate; after the user confirms,
Claude calls `get_client_information` with the candidate's stable ID.

### `get_client_information(client_id, detail_level="summary")`

Calls `GET /clients/{id}` using an already-selected stable ID. The main fields are:

- `fyi`: client and tax/work metadata.
- `xero_invoices`: raw repeating templates and schedules.
- `allocations`: prepared service NET amounts, tags, repeat day and billing job.

## Portfolio catalog analytics

These tools query the latest imported FYI CSV snapshot without opening the FYI browser or reading
every client JSON file. Snapshot fields do not prove that a live FYI job or remaining interim
exists; use the live FYI tools for those questions.

### `get_client_catalog_status()`

Reports whether a catalog is published plus its dataset version, import timestamp, row count and
column count.

### `describe_client_catalog(column=None, include_common_values=false)`

Without `column`, returns all safe column keys, source headers, business descriptions, semantic
types, null counts and distinct counts. Supplying one source header or key also returns at most the
10 most common non-null values and their counts. `values_truncated=true` means other values exist.
Avoid requesting values for every column unless genuinely required because it wastes model context.

### `query_client_catalog(...)`

Accepts only structured operations and allowlisted columns/operators; raw SQL cannot be supplied.

- `count`: count rows matching filters.
- `list`: return explicitly selected columns, with limit/offset.
- `distinct`: values and counts for exactly one column.
- `group_count`: counts grouped by one column.
- `summary`: count/min/max/sum/average for numeric columns.

Filters support `eq`, `not_eq`, `contains`, `starts_with`, `in`, `is_null`, `is_not_null`, and
numeric `gt`/`gte`/`lt`/`lte`. Results always include the dataset version and import timestamp.

CSV files are not imported through MCP. Upload the complete FYI CSV directly in the AutoFYI
backend Swagger UI using `POST /catalog/import`. MCP is query-only for this dataset.

## Live FYI reads

### `get_client_jobs_to_invoice(client_id)`

Calls `POST /client-jobs-to-invoice`. It reads exact available FYI job names and work amounts.
This uses browser automation and can take minutes.

### `get_jobs_and_interim_table(client_id)`

Calls `POST /jobs-and-interim-table`. It returns the same live jobs plus interim dates and
amounts from the billing job. It is read-only but browser-driven.

### `inspect_client_billing_state(client_id, months, invoice_key=None)`

Reads FYI once and reconciles selected months against prepared service lines. Each month is
classified as:

- `unsplit`
- `partially_split_needs_completion`
- `fully_split_unallocated`
- `partially_allocated`
- `no_rows_missing_or_fully_consumed`
- `inconsistent`

It also reports remaining, consumed, and same-amount ambiguous service candidates. This is the
preferred read before split or allocation.

### `preview_split_from_invoices(client_id, invoices)`

Read-only fallback for clients with **no prepared FYI allocation plan**. Where
`inspect_client_billing_state` takes the service-line breakdown from the FYI allocation plan,
this tool takes it from Xero invoices you pass in, then reuses the same reconciliation logic
against the live FYI interims and jobs.

Two-server flow (nothing here is written back):

1. Use the Xero MCP tools (`list-contacts`, `list-invoices`) to pull the client's invoices and
   their service lines. The user chooses the correct contact.
2. Pass them to this tool as `invoices`: a list of
   `{reference, date, lines: [{description, net}]}`. Duplicate descriptions are summed.
3. The tool fetches live FYI jobs and interims and returns, per month:
   - `service_lines` — the split implied by that month's invoice,
   - `matched_interim` and `split_state` (same classification as above),
   - `amount_check` — whether the FYI interim total equals the invoice net,
   - `allocation_suggestions` — candidate FYI jobs per service line (exact-month matches ranked
     first), and `service_lines_without_job` for lines with no plausible job.
4. It also lists `invoices_without_interim`, `interims_without_invoice`, and `unmatched_jobs`.

The result is a non-binding preview (`binding: false`). Job matches are suggestions, not
allocations. If the client already has a prepared FYI plan, the tool still runs but warns that
the plan — not this preview — is the operational source of truth. Posting a real split or
allocation continues to require a prepared FYI allocation plan through the prepare/confirm/execute
tools.

## No-write planning

### `plan_client_billing(client_id, target_month, target_year, invoice_key=None)`

Classifies the client:

- `xero_repeating_with_allocations`: gives the full monthly service split and next safe steps.
- `direct_or_unprepared`: explains why direct invoicing may be appropriate.
- `needs_allocation_plan_selection`: lists repeating-template keys/references to choose.

It does not open FYI or prepare an execution token.

## Prepare tools

Prepare tools never call `/split`, `/allocate`, or `/create-invoice`.

### `prepare_interim_split(...)`

Inputs: client ID, month `1..12`, year, and optional repeating-template key.

It reads current interims, derives all service rows from prepared allocations, validates the
total, and returns either:

- `state=fully_split_unallocated`: no split is needed;
- `state=partially_allocated`: do not re-split; choose one remaining service/job type; or
- a confirmation ID and preview for one `/split` call.

The preview explicitly states that splitting is permanent and creates no invoice.

### `prepare_job_allocation(...)`

Inputs include an exact service description/id/tag, calendar months and exact FYI job names.
It verifies that every required split interim and every job exists now in FYI.

One invocation must contain one complete service/job type:

- Case 1: one service month and one job.
- Case 2: all required months for that service and one job, such as VAT, annual work, or a
  yearly Xero subscription.
- Case 3: one service month and every required job, such as all weekly jobs.

Do not call once per weekly job, once per VAT month, or combine several service lines. The MCP
rejects the Case 4 multi-month/multi-job shape so the AI cannot mix allocation scopes.

Amount rules:

- Amount omitted for every job: equal division; last job absorbs cent rounding.
- Amount supplied for every job: the job total must equal the interim total.
- Mixed supplied/omitted amounts: rejected.

Case classification:

- `case_1_monthly_1x1`
- `case_2_multi_month_1_job`
- `case_3_one_month_multi_job`

If a partially allocated month has identical amounts for multiple services, preparation stops
and tells Claude to ask which service remains. Only after the user answers may Claude retry
with `confirm_ambiguous_remaining=true`. Financial execution still requires the later exact
confirmation phrase.

The prepared API call is `/allocate`. Draft is the default.

### `prepare_direct_invoice(...)`

Verifies exact live FYI jobs and prepares `/create-invoice`. It never consumes an interim.

- Any job amount supplied: missing job amounts become explicit zero; job amounts win.
- No job amounts plus `invoice_amount`: FYI distributes the requested total.
- No job amounts and no total: FYI keeps its auto-filled values.

Clients with Xero/repeating information are blocked by default. The
`allow_repeating_client=true` override is accepted only after the duplicate-billing risk is
explained.

## Confirmation tools

### `get_prepared_action(confirmation_id)`

Returns the current immutable preview, required phrase and payload hash, or the completed
result if the action already ran.

### `cancel_prepared_action(confirmation_id)`

Invalidates the token locally. It never touches AutoFYI or FYI.

### `execute_confirmed_action(confirmation_id, user_confirmation)`

The only financial executor. It runs only when:

1. Writes are enabled in the local environment.
2. The token exists and has not expired, been cancelled, or been used.
3. The exact confirmation phrase matches.
4. The stored payload hash is unchanged.

The token is consumed before the network write. A timeout or server error is therefore not
retryable: FYI and backend logs must be inspected first.
