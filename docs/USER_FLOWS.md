# Reference user flows

These examples prove the intended dialogue and decision order. Names, amounts, tags and jobs
are examples only. Tools always use current client data and live FYI rows.

## Find and explain a client

User:

> Find Example Services Limited.

Tool order:

1. `find_client("Example Services Limited")`
2. Unique exact result: return selected name/ID and client summary.
3. Non-exact result: use scores only as ranking, inspect the names, propose a plausible
   candidate, and ask whether it is correct.
4. After confirmation, call later tools using the confirmed candidate ID.

For a selected repeating client, explain the Xero template, repeat day, prepared service
lines, billing job and any FYI operational flags. Do not dump full client PII unless requested.

## Monthly service — Case 1

Assume the real allocation contains `Monthly Payroll = 120.00`, and FYI returns one exact
monthly payroll job.

1. Find/select client.
2. `plan_client_billing(client_id, 7, 2026)`.
3. `inspect_client_billing_state` for July.
4. If unsplit, `prepare_interim_split(client_id, 7, 2026)`.
5. If a token is returned, show all monthly lines and ask for the exact split phrase.
6. Execute only after that phrase, then inspect again.
7. `prepare_job_allocation` once with July and the exact monthly job.
8. Preview: one month `120.00` → one job `120.00`, draft Final invoice.
9. Ask for the service-specific returned phrase and execute once.

## VAT over two months — Case 2

Assume prepared `VAT 2 Monthly = 80.00` per repeating month.

1. Ensure both July and August full invoices have been split.
2. Verify FYI contains an `80.00` split row on each actual invoice date.
3. Select the exact FYI VAT job.
4. Prepare one complete allocation request: months `{July:80, August:80}` → VAT job `160`.
5. Create a draft only after confirmation.

If August is missing, the preparation stops. It does not allocate July alone unless the user
changes the request and prepares a new payload.

## Weekly jobs — Case 3

Assume prepared `Same Week = 400.00` and FYI currently exposes four weekly jobs.

Equal split:

```text
July interim: 400.00
Week 1: 100.00
Week 2: 100.00
Week 3: 100.00
Week 4: 100.00
```

For `100.00 / 3`, equal division becomes `33.33`, `33.33`, `33.34`; the last job takes the
rounding remainder. If jobs should follow work performed rather than equal division, the user
must provide all explicit amounts.

The MCP does not assume four jobs because a month has four calendar weeks. It uses only the
actual jobs returned by FYI and the user's selection.

All weekly jobs must be included in one Case 3 allocation request. Claude must not allocate
Week 1, then Week 2, then Week 3 through separate calls.

## Annual/accounts/income-tax job — Case 2

Assume a prepared service is `50.00` per month and the requested annual job spans 12 months.
The MCP verifies all 12 split rows. Only then can it prepare one job amount of `600.00`.

Missing, differently dated or differently valued months stop preparation and are reported.

## Xero subscription

Two supported modes are intentionally separate:

- One month → one monthly subscription job: Case 1.
- All 12 months → one yearly subscription job: Case 2.

If the available jobs do not make the period clear, ask the user. Do not mix both modes.

## Already split or partially allocated month

Run `inspect_client_billing_state` first. A fully split month goes directly to one requested
service allocation. A partially allocated month is never re-split; use only remaining rows.
If the same amount belongs to several prepared services, ask which service the remaining row
represents before preparing it.

## Direct invoice without an interim

For a client with no repeating allocation:

1. Read jobs to invoice.
2. User selects exact jobs.
3. Prepare one of three modes:
   - explicit job amounts;
   - an invoice total for FYI to distribute;
   - FYI auto-filled values.
4. Clearly say: “This creates an invoice without consuming an interim.”
5. Ask for the returned direct-invoice phrase and execute once.

For a repeating client, preparation is blocked until the user explicitly chooses the override
after seeing the duplicate-billing warning.

## Approval flow

All examples above default to draft. If `approve_invoice=true`, the required phrase changes to
an explicit `APPROVE ...` phrase. A previous draft phrase cannot authorize approval.
