# WorkBuddy evidence guide

Checked 2026-11-06 against WorkBuddy's [credits](https://www.workbuddy.ai/docs/workbuddy/credits), [usage](https://www.workbuddy.ai/docs/workbuddy/usage), and [pricing](https://www.workbuddy.ai/docs/workbuddy/pricing) pages.

## What the public documentation establishes

- Credits have several sources and validity periods. WorkBuddy says its deduction order is earliest expiration first; equal-expiry base credits precede bonus credits.
- The Profile → Usage page shows available and used credits in resource packages.
- A Pro top-up of 500 credits costs $15 and lasts one month. The monthly Pro subscription includes credits and other services; its fee must not be treated as a universal per-credit rate.

## What remains unverified

The public pages do not establish a stable export schema, a complete per-task rate formula, or a mapping from a task's credit debit to exact input/output token counts. Do not invent these fields in a parser.

You can still record **observed** credit movements in `events.csv` and before/after balances in `snapshots.csv`. Use a locally assigned event ID if the UI lacks one, and cite the original statement or screenshot in `source_ref`. If you cannot account for every movement between two snapshots, the resulting difference means the *local record is incomplete or inconsistent*; it does not establish an improper provider charge.

Leave `usage.csv` with only its header and `rules.json` as `[]` when task measurements or official rules are unavailable. The report will keep per-task checks as `insufficient_data`. Never enter a guessed model price or convert credits into tokens using a third-party API price.

## Evidence needed for a native importer

A redacted, user-authorized export or screenshot set with field names, timestamps and time zone, the events that alter balance, a before/after balance, and at least one task's observable model/operation/quantity. Remove account names, email, prompts, responses, files, access keys and payment identifiers before sharing an issue. Keep the real record local. A schema inferred from one account must be tested against another independent export before it is called stable.
