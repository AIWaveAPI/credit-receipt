# Credit Receipt

An **offline credit-ledger reconciler** for AI tools. Give it your own credit events, usage facts, balance snapshots, and dated rate rules. It tells you which arithmetic checks match, which differ, and which cannot be checked from the available evidence.

It does **not** convert every credit into a fixed dollar or token amount. A difference is a question to investigate, not proof that a provider made a hidden charge.

## Try it in one minute

Python 3.10 or newer; no runtime dependencies:

```bash
python -m credit_receipt audit examples/synthetic-runway --output audit-report.json
```

Expected summary: `reconciled: 2 debits, 0 differences, 0 incomplete checks`. Open the JSON report to see each transaction, rule URL, source row, and SHA-256 of the inputs. You can also [check the math by hand](docs/manual-check.md). The example is **fabricated**. Its 12-credit-per-video-second rate for Gen-4.5 comes from [Runway's official credit guide](https://help.runwayml.com/hc/en-us/articles/15124877443219-How-do-credits-work), checked on 2026-11-06. The example's 1,000-credit / $10 purchase matches [Runway's published purchased-credit rate](https://help.runwayml.com/hc/en-us/articles/23987639973267-Will-unused-credits-carry-over-after-my-billing-day); it is not a real invoice.

For an installed command:

```bash
python -m pip install .
credit-receipt audit /path/to/your-local-records --output /path/to/private-report.json
```

Nothing is uploaded. The CLI does not log in, fetch rules, inspect browser storage, or use API keys. Keep your own inputs and output **outside the Git repository**: source references and task IDs in a report may still be sensitive.

## What goes in

Place these files in one directory. See [the complete data contract](docs/design.md) and [the synthetic example](examples/synthetic-runway).

| File | Purpose |
| --- | --- |
| `opening.json` | Provider name, opening timestamp and credit balance |
| `events.csv` | Purchases, grants, debits, refunds, expirations and adjustments, each with a unique ID and signed credit delta |
| `usage.csv` | Measured task quantities linked to debit IDs; multiple operations per task are allowed |
| `rules.json` | Dated, sourced credit rates for matching model + operation + unit |
| `snapshots.csv` | Optional provider balance observations; a snapshot after the last event is required for a fully closed reconciliation |

The current version accepts **normalized user-owned records**. It does not claim to parse any provider's native export. If a provider supplies only a balance or a task-level credit deduction, leave unavailable usage or rules blank. The tool will say `insufficient_data`, not invent a token count. Inputs have [explicit size and precision limits](docs/design.md) so malformed files cannot silently expand into implausible calculations.

## Three answers, with evidence

1. **Where did the balance go?** It recomputes the balance from the opening snapshot and every signed event, then compares provider snapshots. A timestamp tie is marked ambiguous because event order is not known.
2. **What did a task cost in credits?** For each debit, it finds the effective dated rule for each linked usage line, applies the explicit rounding rule, and compares the sum with the observed debit.
3. **What was that worth in money or tokens?** A purchase's paid amount divided by its purchased credits is shown for that purchase only. Nonterminating ratios retain an exact fraction rather than a misleading rounded decimal. Subscription, promotional and purchased credits cannot be treated as a universal cash rate; task cash allocation needs a provider-confirmed spending bucket. Token counts require original model and token-usage records.

Every calculation is conditional on the **completeness and accuracy of the files you supply**. The project checks arithmetic and rule matching; it does not authenticate a screenshot, verify the provider's complete history, certify that a user-entered rule URL proves its rate, or determine whether an expiry/refund event was permitted by the provider's policy. Keep a copy of the official statement and dated rate page when investigating a difference.

## Result meanings

| Status | Meaning |
| --- | --- |
| `matched` | Supplied usage and an effective rule reproduce a debit exactly |
| `difference` | Complete supplied inputs produce a different number |
| `insufficient_data` | Usage or an effective rule is missing; no zero-cost assumption |
| `ambiguous` | A snapshot shares an exact timestamp with an event |

Process exit codes: `0` fully reconciled, `1` one or more arithmetic differences, `2` invalid input, `3` incomplete checks. Differences take priority if both difference and incomplete checks exist. A report's `status=reconciled` means **reconciled against supplied evidence**, not an independent provider audit.

## WorkBuddy: what can be audited today

[WorkBuddy's credit guide](https://www.workbuddy.ai/docs/workbuddy/credits) documents earliest-expiry-first deduction across credit packs, and its [Usage guide](https://www.workbuddy.ai/docs/workbuddy/usage) says users can inspect available and used credits. Its public documentation, checked 2026-11-06, does not establish a stable export schema or a complete formula for every task's credit debit. Thus the current tool can reconcile manually normalized WorkBuddy credit events and snapshots; it cannot independently reconstruct every WorkBuddy task's expected credit cost without more raw data. We will add a native importer only after validating a permitted, redacted real export.

See the [WorkBuddy evidence guide](docs/workbuddy.md) for the fields needed and the honest partial-audit path.

## Why this is different

Projects such as [Token Ledger](https://github.com/DiamondNit3/token-ledger) estimate token and public API-equivalent costs from local coding-session records. Credit Receipt starts with **provider-observed credit movements and balance snapshots**, and refuses to turn missing usage into a zero. It does not claim to replace a provider's invoice or an LLM observability stack.

## Development

```bash
python -m unittest discover -s tests -v
python -m compileall -q credit_receipt
```

The only included ledger is synthetic. Do not attach real billing exports to public issues. For security reports, use GitHub's private vulnerability reporting when available; see [SECURITY.md](SECURITY.md).

Licensed under MIT. Built by AIWaveAPI as a supplier-neutral accounting tool; AIWave is not a preferred input or required service.
