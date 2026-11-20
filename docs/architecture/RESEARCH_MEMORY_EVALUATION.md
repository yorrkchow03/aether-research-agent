# Research memory and Jev evaluation

Two end-to-end comparisons used the same three ETH research windows in 2026. Each pair used the same report request and frozen source archive. Codex supplied model responses through a local bridge while Aether ran the research tools, tracked progress, reviewed sources and published reports. A report counted as a valid delivery once Aether published substantive findings and verified the report against its audit records. Model graders assessed report quality without knowing which setup produced each report.

## Single-agent and two-layer memory

In round 5, the two-layer setup used a four-stage plan, a question ledger, and a dedicated reader. The initial source survey could list eligible source titles, dates, and identifiers while keeping the existing limits on full-text reading. The July, August, and October surveys surfaced 28, 23, and 24 candidates, up from 6, 5, and 5 in round 4.

Both setups published **3 of 3 complete reports** that passed independent source review. The main agent received 58.1% to 67.4% fewer input bytes with two-layer memory. The figures sum the inputs across all main-agent requests, including context sent again on subsequent turns.

| UTC window, end date excluded | Main-agent input bytes, single / two-layer | Input reduction | Blind overall score, single / two-layer | Coverage, single / two-layer |
| --- | ---: | ---: | ---: | ---: |
| July 23–30 | 3,087,734 / 1,005,470 | 67.4% | 88 / 83 | 4/5 / 4/5 |
| August 21–28 | 2,630,837 / 1,102,537 | 58.1% | 84 / 86 | 2/5 / 3/5 |
| October 16–24 | 2,294,742 / 866,465 | 62.2% | 79 / 85 | 2/5 / 3/5 |

Overall score and coverage improved in two windows. In July, the two-layer report passed source review and published, and blind review found one major error in its attribution of a later revision to the report period. Input dropped in all three windows, while delivery rates were equal and quality varied. Five of the six reports published after the 15-minute observation point. Elapsed time included bridge queueing and response waits.

## Jev on and off

Both arms used round 5 two-layer memory and the same source-review workflow. The optional TypeSafe integration scored candidate passages during targeted archive search. Both arms published **3 of 3 complete reports**. Jev returned 254 successful responses, of which 42 met the confidence and category criteria to influence ranking.

| UTC window | Successful Jev responses | Met ranking criteria | Blind overall score, off / on | Coverage, off / on |
| --- | ---: | ---: | ---: | ---: |
| July 23–30 | 64 | 7 | 87 / 83 | 4/5 / 3/5 |
| August 21–28 | 94 | 12 | 91 / 87 | 5/5 / 3/5 |
| October 16–24 | 96 | 23 | 89 / 86 | 4/5 / 3/5 |

Jev changed intermediate candidate rankings once in July and once in October. Replaying each of those searches with Jev off, using the same query and frozen candidates, produced the same selected passages and documents in the same order. No source entered the selected set because of either ranking change.

The three pairs showed no report-quality gain from enabling Jev. Because the replays preserved the selected material, the cause of the score differences remains undetermined. The small sample and local model bridge limit what these comparisons can establish for other requests and model configurations.
