# Independent check of the synthetic example

Every row below is fabricated. The operation rate is 12 credits per second for Gen-4.5 in the [Runway credit guide](https://help.runwayml.com/hc/en-us/articles/15124877443219-How-do-credits-work), read on 2026-11-06. The [purchased-credit guide](https://help.runwayml.com/hc/en-us/articles/23987639973267-Will-unused-credits-carry-over-after-my-billing-day) states $0.01 per purchased credit; this example assumes a single separate purchase and no subscription pool. This worksheet checks arithmetic only.

| Event | Formula | Result |
| --- | --- | ---: |
| Opening | Input record | 0 credits |
| Purchase | 0 + 1,000 | 1,000 credits |
| Five-second task | 5 × 12 = 60; 1,000 − 60 | 940 credits |
| Ten-second task | 10 × 12 = 120; 940 − 120 | 820 credits |
| Closing snapshot | Provider-like synthetic observation | 820 credits |
| Purchase-only unit rate | $10 ÷ 1,000 | $0.01 per purchased credit |

Recompute directly from `examples/synthetic-runway/events.csv`, `usage.csv`, `rules.json`, and `snapshots.csv` without running the tool. Deliberately change a debit from `-60` to `-61`: the debit check and final snapshot check should both show differences. Remove its usage line: the debit check should become `insufficient_data`, not a fabricated zero.
