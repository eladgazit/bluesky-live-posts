# Data quality evals for the `posts` table

The checks `src/evals.py` runs against `posts`, with the reasoning behind each threshold. Run them with `make eval`; they are tested on fixed data in `tests/test_evals.py`. If you change a threshold, change it here and in `src/evals.py`.

## How the checks work

- **Window:** every check looks only at rows *ingested* in the last **15 minutes** (configurable). The table holds 330k+ rows from earlier runs, including known defects that predate any fix, and we want to know how the pipeline is doing now, not how it did last week. Freshness is the exception: it looks at the newest row overall.
- **Two severities:**
  - **FAIL:** the pipeline is broken or the data cannot be trusted (stalled ingestion, schema guarantees violated, parsing regression).
  - **WARN:** quality is drifting or a known imperfection is present, but the data is usable.
- **Result:** one line per check (`PASS`/`WARN`/`FAIL`, the measured value and the threshold), a summary, and an exit code: `0` if no FAIL, `1` if any FAIL. WARNs never change the exit code.
- **Too little data:** rate-based checks (any "share of rows" check) are `SKIP`ped if the window has fewer than **500 rows**, because percentages on a handful of rows are noise. The volume check reports that situation on its own.
- **Read-only on `posts`:** evals only `SELECT` from it. Each run appends one row per check to `eval_results` (run time, check, status, value, passed; `passed` is false only for FAIL). The dashboard's "Data quality" panel reads that table. `make run` repeats the evals every 60 s; `python evals.py --every N` does it on any interval.

## Baseline

Thresholds are set from one 3-minute run at one time of day (2026-10-18, 7,527 rows), so they are **a starting point to calibrate**, not final truth.

| Measurement | Observed |
|---|---|
| Ingest rate | ~42 posts/s, ~2,500 per minute |
| Empty or whitespace-only text | 299 rows (4.0%) |
| No language | 711 rows (9.4%) |
| Posts with at least one hashtag | 701 rows (9.3%) |
| Language not a plain 2–3 letter code (`en-US`, `pt-BR`, …) | 28 rows (0.37%) |
| `created_at` more than 1 minute ahead of ingestion | 28 rows (0.37%); 7 rows (0.09%) more than 1 hour ahead |
| `created_at` more than 1 day before ingestion | 1 row (0.01%) |
| Hashtags with stray whitespace | 4 of 2,320 tags (0.17%) |
| Same author, same text repeated | 147 rows (2.0%) |
| Longest text | 384 characters |
| Duplicate `(did, rkey)` | 0 |

## Freshness

| ID | Check | WARN | FAIL | Why this threshold |
|---|---|---|---|---|
| F1 | **Age of the newest row** (now minus the latest `ingested_at`) | > 60 s | > 5 min | At ~42 posts/s, a quiet minute is abnormal. Five minutes means the producer, consumer or Kafka has stopped. |
| F2 | **Ingest lag**: median of `ingested_at − created_at` over the window | > 30 s | > 5 min | The median ignores backdated and wrong-clock posts. A growing median means the consumer is falling behind. |

## Volume

| ID | Check | WARN | FAIL | Why this threshold |
|---|---|---|---|---|
| V1 | **Too few posts**: rows ingested in the last 5 min | < 2,000 | 0 | Normal is ~12,000 per 5 min, so 2,000 is about 15% of that: a partial outage or a dropped connection. Zero is a full stop. |
| V2 | **Too many posts**: rows ingested in the last 5 min | > 100,000 | not used | About 6× normal. Most likely a replay or backfill, which is worth knowing about but is not an error. |

## Completeness

| ID | Check | WARN | FAIL | Why this threshold |
|---|---|---|---|---|
| C1 | **Empty text**: share with empty or whitespace-only `text` | > 10% | > 25% | Image- and video-only posts are legitimate (4% today). A jump suggests the parser is losing text. |
| C2 | **Missing language**: share with `lang` NULL | > 20% | > 40% | 9.4% today. A jump suggests `langs` extraction broke. |
| C3 | **Hashtag coverage**: share of posts with at least one hashtag | outside 3%–30% | outside 1%–50% | 9.3% today. Near 0% means facet parsing broke; far above normal means a spam wave. |
| C4 | **Required fields**: rows where `did`, `rkey`, `text` or `created_at` is NULL or `did`/`rkey` is empty | not used | any | The parser always fills these, so any hit is a bug. |

## Uniqueness

| ID | Check | WARN | FAIL | Why this threshold |
|---|---|---|---|---|
| U1 | **Duplicate posts**: groups of rows sharing `(did, rkey)` | not used | any | Guards the unique constraint, in case someone drops or changes it. Expected 0. |
| U2 | **Repeated content**: share of rows whose author posted the same non-empty text more than once | > 5% | not used | 2.0% today. Higher usually means a spam or bot wave. It's not a pipeline error, so it never fails. |

## Validity

| ID | Check | WARN | FAIL | Why this threshold |
|---|---|---|---|---|
| Va1 | **Language format**: share of non-NULL `lang` not matching a lowercase 2–3 letter code | > 0.1% | > 1% | 0.37% today (`en-US`, `pt-BR`, …), so this would **WARN now** and pass once languages are normalized. |
| Va2 | **Future timestamps**: share with `created_at` more than 5 min after `ingested_at` | > 0.1% | > 1% | Wrong client clocks. Today's cap in the parser is a full day, so this would **WARN now** and pass once the cap is tightened. |
| Va3 | **Implausibly old**: any `created_at` before 2023-01-01 | not used | any | Bluesky did not exist. The parser already rejects these, so any hit is a regression. |
| Va4 | **Backdated posts**: share with `created_at` more than 1 day before `ingested_at` | > 1% | not used | 0.01% today. A jump suggests an import or replay of old data. |
| Va5 | **Text length**: any `text` over 3,000 characters | any | not used | The Bluesky limit is 300 graphemes (3,000 bytes). Longest seen is 384. Anything past 3,000 is corruption. |
| Va6 | **Hashtag format**: share of tags that are empty, start with `#`, contain whitespace, or exceed 64 characters | > 0.1% | > 2% | 0.17% today (4 tags with stray spaces or newlines), so this would **WARN now** and pass once tags are stripped. |
| Va7 | **Identifier format**: `did` not starting with `did:plc:` or `did:web:`, or `rkey` empty or over 512 characters | not used | any | These come straight from Jetstream, so any hit means corrupted input. |

## What we expect on today's data

If run now, the evals should give **FAIL: none**, and **WARN: Va1, Va2, Va6**. Those three WARNs are exactly the known defects from the quality report. They are real signals, and each disappears once the matching fix is made:

| Warning | Cleared by |
|---|---|
| Va1 language format | normalizing `en-US` → `en` in `parse_event` |
| Va2 future timestamps | capping `created_at` at about 5 minutes ahead |
| Va6 hashtag format | stripping whitespace from tags |

## Out of scope

- **Kafka-side checks** (consumer lag, topic offsets). These evals look at the table only. Lag would need the Kafka client and is a separate check.
- **Schema checks** (column types, constraints). `init.sql` and the tests own those.
- **Content quality** such as spam, language-detection accuracy or offensive text.
- **Alerting.** Evals report and exit non-zero; wiring that to notifications is a later step.

## Still open

Implemented exactly as written above (15-minute window, the listed thresholds, `src/evals.py` + `make eval`). Remaining:

1. **Window:** is 15 minutes right? Shorter reacts faster but sees less data at quiet hours; the 500-row minimum would then skip more often.
2. **Time of day:** the volume thresholds assume ~42 posts/s. Overnight traffic may be much lower, so V1 might warn on a healthy pipeline. I suggest measuring a few off-peak windows before treating V1 as final.
3. **Where it runs:** I'd suggest a function in `src/evals.py` plus `make eval`, tested on fixed seed data the same way `queries.py` is. A dashboard panel could come later.
4. **Anything to add or drop** before we write code?
