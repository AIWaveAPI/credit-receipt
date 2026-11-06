# WorkBuddy evidence guide

Checked 2026-11-06 against WorkBuddy's [credits](https://www.workbuddy.ai/docs/workbuddy/credits), [usage](https://www.workbuddy.ai/docs/workbuddy/usage), and [pricing](https://www.workbuddy.ai/docs/workbuddy/pricing) pages.

## What the public documentation establishes

- Credits have several sources and validity periods. WorkBuddy says its deduction order is earliest expiration first; equal-expiry base credits precede bonus credits.
- The Profile → Usage page shows available and used credits in resource packages.
- A Pro top-up of 500 credits costs $15 and lasts one month. The monthly Pro subscription includes credits and other services; its fee must not be treated as a universal per-credit rate.

## Verified export shape (one sample, 2026-11-07)

A user-authorized local `request-usage-2026-11-06.xlsx` had one worksheet and five columns: `RequestID`, `积分消耗`, `模型`, `使用端`, `时间`. In that sample, all request IDs were distinct, credit amounts were nonnegative, and timestamps were wall-clock strings without timezone information. The original workbook was not copied into this repository, and no real request ID or task content is published. This is **N=1**; future WorkBuddy exports may differ.

`workbuddy-import` validates that exact header, rejects duplicate IDs, negative amounts, formulas, malformed timestamps, and oversized workbooks, then emits a local observed-debit summary and `events.csv`. It does not assert that zero-credit requests used zero tokens. The source offset must be provided explicitly. The offset is an interpretation of the workbook's wall-clock time, not a verified property of the file.

## What remains unverified

The public pages do not establish a stable export schema, a complete per-task rate formula, or a mapping from a task's credit debit to exact input/output token counts. The observed export does not include starting/closing balance, grants, expiries, top-ups, rate-rule inputs, or credit-lot allocation. Do not invent these fields in a parser.

You can still record **observed** credit movements in `events.csv` and before/after balances in `snapshots.csv`. Use a locally assigned event ID if the UI lacks one, and cite the original statement or screenshot in `source_ref`. If you cannot account for every movement between two snapshots, the resulting difference means the *local record is incomplete or inconsistent*; it does not establish an improper provider charge.

Leave `usage.csv` with only its header and `rules.json` as `[]` when task measurements or official rules are unavailable. The report will keep per-task checks as `insufficient_data`. Never enter a guessed model price or convert credits into tokens using a third-party API price.

## Evidence needed for a complete audit

A user-authorized balance and credit-pack movement history, verified timestamp offset, and per-task measured quantity plus dated provider rule are still needed. Remove account names, email, prompts, responses, files, access keys and payment identifiers before sharing an issue. Keep real records local. The schema inferred from one account must be tested against another independent export before it is called stable.
