# Credit Receipt: audit scope and data contract

Date: 2026-11-06. Scope: import user-supplied records, reconcile balances, and explain each debit against dated rules.

## Decision

Build an offline Python CLI with no runtime dependencies. A user supplies four local files: `opening.json`, `events.csv`, `usage.csv`, and `rules.json`, plus optional `snapshots.csv`. The CLI never logs in, scrapes, uploads, or discovers credentials. It emits a local JSON report and a short console summary.

We considered (1) a universal credit-to-token calculator, (2) a hosted cost dashboard, and (3) an offline evidence-based reconciler. Only (3) can distinguish confirmed arithmetic from missing information without implying that a platform's credits equal its supplier's API cost. A provider-specific importer needs a real, permitted export format and is outside this first release.

## Canonical input

- `opening.json`: `as_of` (timezone-aware ISO 8601), `balance_credits` (decimal string), `provider`.
- `events.csv`: `event_id,occurred_at,kind,delta_credits,source_ref`. Optional: `task_id,lot_id,paid_amount,paid_currency`. Kinds: `purchase`, `grant`, `debit`, `refund`, `expiry`, `adjustment`. Deltas carry the sign; positive for purchase/grant/refund, negative for debit/expiry, either sign for adjustment. `paid_amount` is an actual separately paid amount, allowed only on a purchase; it is never used to value subscription or promotional credits.
- `usage.csv`: `usage_id,event_id,model,operation,quantity,unit,source_ref`. Multiple usage lines may point to one debit, allowing a task to contain several operations. Quantity must be positive. A debit without lines is `insufficient_data`.
- `rules.json`: array of `{model,operation,unit,credits_per_unit,effective_from,effective_to,rounding,source_url,verified_at}`. All fields are required except `effective_to`; interval end is exclusive. `rounding` is `exact`, `ceil`, or `floor`; the rule is applied per usage line. Do not invent a historical effective date from a currently viewed page.
- `snapshots.csv` (optional): `snapshot_at,balance_credits,source_ref`. A snapshot applies after all events at or before its timestamp. Equal-timestamp ordering must be explicitly reviewed by the user, so the CLI reports that case as `ambiguous`.

All identifiers are opaque local strings. Files must be UTF-8. Decimal numbers use a dot and never binary floating-point. Timestamps must include a UTC offset. Each file is limited to 20 MiB; CSV inputs to 100,000 data rows; rule files to 2,000 rules; decimal inputs to 30 digits and an exponent between -18 and +18. Invalid data, duplicate IDs, unlinked usage, or contradictory rule intervals fail closed with a clear local error. Source hashes cover the exact bytes parsed, even if a file changes during an audit. There is no automatic price update.

## Computation and claims

`balance = opening + sum(event deltas)`. Each snapshot compares its observed balance against the computed balance. A mismatch is an *unexplained difference*, never proof of misconduct. A debit is `matched` only if every linked usage line finds exactly one effective rule and the sum of expected credits equals its absolute observed debit. It is `difference` if complete rules give a different total; `insufficient_data` if data or rules are missing. Every row includes the input row number and source reference. The report includes SHA-256 of every input file and rule source URLs.

Purchased-credit cash rate is reported as `paid_amount / purchased_credits` for that *purchase only*. Nonterminating rates are given as exact fractions, not rounded decimals. Without a provider-confirmed lot allocation, the CLI does not assign that dollar rate to an arbitrary task. The project does not infer tokens from credits. A token-denominated rule can be checked only when the user supplies actual token counts and the relevant price rule. A rule verified later than the event is not back-applied; historical billing requires dated historical evidence.

## Verification

Synthetic fixtures include a closed balance, a snapshot discrepancy, matched and mismatched debit, missing usage, dated rule transition, multiple usage lines, duplicate ID, timezone error, and numeric precision. An independent reviewer must be able to recompute the sample report with a spreadsheet from the published fixture files. Public repository contents must contain only synthetic examples and official source links, never live billing exports or customer identifiers.

## Known limit

WorkBuddy documents balance history and earliest-expiry spending, but its public documentation does not establish a complete per-task export and deterministic consumption formula. Runway documents credit rates for named operations and a 30-day usage table, but a stable machine-readable export is unverified. First release therefore accepts normalized user-owned CSV only and makes no native-import promise.
