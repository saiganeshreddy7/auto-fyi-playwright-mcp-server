# Edge and corner cases

## Client selection

- Typo or fuzzy name: scores rank only; Claude proposes a plausible candidate and asks the user.
- `Ltd`, `Limited`, punctuation, case, and spacing differences: mark as legal-name equivalent,
  but confirm before using the non-identical result.
- Abbreviations such as `Accuracy` and `ACG`: AI may suggest the relationship but cannot claim
  it is proven; ask once and then use the confirmed ID.
- Two clients with the same exact name: ask for an ID.
- Search result changes between calls: use the selected stable ID.
- Client index exists but JSON is missing/malformed: stop with a structured-data error.
- Client name changes after preparation: the stored ID/payload remain fixed, but the short
  token lifetime limits stale execution.

## Xero and prepared allocations

- No allocation: use direct/unprepared planning; never invent a split.
- Multiple repeating templates: require `invoice_key`/reference.
- Allocation key does not match a raw Xero template: warn through client information; do not
  silently substitute another template.
- Edited prepared subtotal differs from raw Xero subtotal: preview the warning. Prepared lines
  remain authoritative, matching the frontend.
- Inclusive tax: use NET = line amount minus tax.
- Exclusive/NoTax: line amount is already NET.
- Empty description, duplicate description or non-positive net: block splitting.
- A tag matches several lines: require an exact description or line ID.
- Missing repeat day: stop; do not guess.
- Status `manager`, `review`, `ignored`, or another non-approved value: preserve the
  frontend's block and refuse split/allocation preparation.
- Archived prepared allocation: block.
- Prepared allocation client ID differs from selected client ID: block.
- NaN or infinite amount: block as non-financial data.

## Dates

- Month outside `1..12` or year outside `2000..2100`: reject.
- Repeating day 31 in April: use April 30 and warn for verification.
- Repeating day 29/30/31 in February: use the actual month end and warn.
- Leap-year February is calculated from the requested year.
- FYI date format differs: common ISO, day-month-name and numeric formats are normalized.
- No live interim on calculated date: stop.

## Splitting

- Rows already exactly match the prepared service amount multiset: return
  `fully_split_unallocated` and create no split confirmation.
- Expected service rows are a strict subset because some were consumed: return
  `partially_allocated`; never re-split the month.
- Partially split rows whose total still matches: backend split algorithm may complete them;
  show current rows in the preview.
- Same-date rows total differs from prepared lines: block.
- Row values cannot be parsed: block.
- User changes any service amount after preview: prepare again; execute cannot accept edits.
- Split succeeds but a later allocation fails: report the permanent split and do not undo it.

## Allocation

- Selected split amount missing for one requested month: block the entire preparation.
- Exact job absent: return current job choices.
- Duplicate job names in the request: reject.
- FYI returns an available job with zero work: reject allocation to it.
- Every job amount omitted: equal divide.
- Every amount provided but totals differ by more than one cent tolerance: reject.
- Mixed provided/omitted amounts: reject.
- Cent rounding: last job receives the remainder.
- Two service rows have the same amount on the same day: the backend ultimately verifies/ticks
  rows; the preview cannot claim a unique semantic row merely from its amount. If only some
  identical rows remain, require `confirm_ambiguous_remaining=true` after the user identifies
  the intended service.
- One service type per request: include all relevant months or all relevant jobs at once.
- Case 4 (`N months × N jobs`) is rejected by MCP so separate service scopes cannot be mixed.
- Changed current allocation versus older live amounts: classify as inconsistent and ask the
  user; do not silently apply the new amount historically.

## Direct invoices

- Repeating client: blocked unless `allow_repeating_client=true`.
- Some job amounts supplied: missing amounts become explicit zero, matching backend behavior.
- `invoice_amount` plus job amounts: job amounts win; preview warns.
- No amounts and no total: FYI auto mode is clearly shown.
- Duplicate job name: reject.
- Invoice approval: requires the stronger approval phrase.

## Confirmation and failures

- Writes disabled: executor sends nothing and leaves a clear message.
- Wrong phrase: sends nothing.
- Expired token: sends nothing; re-read and prepare again.
- Cancelled token: sends nothing.
- Replayed token: sends nothing.
- Payload mutation/integrity failure: sends nothing.
- Two operations for one client: execution is serialized locally.
- Network timeout during write: token remains consumed; result is
  `outcome_unknown_do_not_retry`.
- HTTP 5xx or backend `status=error`: inspect FYI and partial result data; never auto-retry.
- MCP restarts: in-memory confirmations disappear by design, forcing a fresh read.
