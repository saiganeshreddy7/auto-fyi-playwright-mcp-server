---
description: Guided split-and-allocate for one client — Xero invoices + FYI jobs/interims, preview, then execute with confirmation.
argument-hint: [client name]
---

You are running the **prepare-split-allocate** workflow for a client. The client (if given) is:
`$ARGUMENTS`

This uses two MCP servers: **xero** (invoices) and **autofyi** (FYI jobs, interims, split, allocate).
Follow the steps in order. Never skip a confirmation. Never invent amounts, job names, or a
success result. If a tool errors, stop and report the exact error — do not work around it.

## Golden rules (do not break)
- **Split source priority:** if the client has a prepared FYI allocation plan, that plan is the
  source of truth — use `inspect_client_billing_state`. Only fall back to Xero invoices
  (`preview_split_from_invoices`) when there is no plan, OR when the user explicitly says to
  override and use the Xero invoices.
- **One service type per allocation request.** Never mix monthly, weekly, VAT, annual, or
  subscription work in a single allocation.
- **Never re-split a month that is already split or partially allocated.** Use only remaining rows.
- **Every write is prepare → user gives the exact confirmation phrase → execute.** Never call
  `execute_confirmed_action` until the user has seen the full preview and typed the exact phrase.
- **Never retry a write after a timeout or error.** Re-inspect FYI first.
- **Job matches are suggestions.** The user confirms which job each line lands on.

## Step 1 — Confirm the client
Call `find_client` with the name. If it is not a unique exact match, show the candidate(s) and
ask the user to confirm before continuing. Keep the returned stable client id.

## Step 2 — Confirm the Xero organisation, then pull invoices
1. Call xero `list-connected-organisations`. If more than one, ask the user which organisation.
2. Call xero `list-contacts` and confirm the exact contact — watch for joint/duplicate contacts
   (e.g. "Mark Kenny" vs "Mark Kenny & Richard Kenny"). Ask if there is any ambiguity.
3. Call xero `list-invoices` for that contact. Read every invoice's service lines (description +
   net). Summarise them back to the user (reference, date, lines, net, gross).

## Step 3 — Pull FYI jobs and interims
Call autofyi `get_jobs_and_interim_table` for the client id. This returns live jobs (with WIP
amounts) and the interim rows (the raised lump amounts, e.g. €300/month).

## Step 4 — Choose the split source
Call `get_client_information` (summary) to see `allocation_plan_count`.
- **Plan exists** → use `inspect_client_billing_state` for the target months (authoritative).
- **No plan** (or user override) → use `preview_split_from_invoices` with the client id and the
  Xero invoices as `invoices: [{reference, date, lines:[{description, net}]}]`.

## Step 5 — Reconcile and surface the truth (read carefully, report plainly)
Present invoices against jobs + interims. For every month, state clearly:
- **Amount check:** does the FYI interim total equal the invoice net? If `MISMATCH`, stop and ask.
- **Invoice with no FYI interim** (`invoices_without_interim`): say
  *"This invoice is not showing as an interim in FYI — it may already be allocated/consumed, or
  not synced yet. Please check it before we proceed."* (The tool cannot tell which; the user must look.)
- **Already split** (`split_state: fully_split_unallocated`): say *"This month is already split —
  do not split it again."*
- **Partially allocated** (`split_state: partially_allocated`, with `remaining_services`): say
  *"This month is already split and some rows are allocated. Only €X remains; it may be for this
  job — confirm before allocating."*
- **Service line with no matching job** (`service_lines_without_job`): flag it as money invoiced
  with nothing on the jobs side; do not force a match.
- Also list `interims_without_invoice` and `unmatched_jobs`.

Then show the proposed **split → job map**: each service line amount → its suggested FYI job,
one service type at a time. Ask the user to confirm the mapping. If anything is ambiguous
(equal amounts, no job, mismatch), ask rather than guess.

## Step 6 — Execute only with a plan and explicit confirmation
Executing a real split/allocation requires a prepared FYI allocation plan (the backend writes
against the Xero repeating-invoice template).
- **No plan:** tell the user Steps 1–5 are a preview only. To post, a plan must be set up in FYI
  first (or a direct invoice, which is dangerous for repeating clients — only on explicit request,
  with the double-billing risk stated at confirmation). Stop here unless they direct otherwise.
- **Plan exists:** for each month that needs it, `prepare_interim_split` → show the preview →
  user types the exact confirmation phrase → `execute_confirmed_action`. Then, one service type at
  a time, `prepare_job_allocation` (all required months/jobs for that service, per the allocation
  cases) → show preview → user confirms exact phrase → `execute_confirmed_action`. Draft invoice
  by default; approval needs the stronger phrase.

## Step 7 — Report
Summarise what was previewed vs. actually written (with confirmation ids and draft/approved
status), and list anything the user still needs to resolve (e.g. uncovered service lines,
unsynced invoices, clients with no plan).
