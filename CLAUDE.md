# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

A beginner "hello world" streaming pipeline, built phase by phase from `PLAN.md`. Infrastructure, producer, consumer, dashboard and tests are implemented. The user is learning data engineering, so keep things as simple as possible and explain choices.

## Workflow

We build one phase at a time. After each phase, stop and tell the user how to verify it. Don't start the next phase until they say to continue.

## Architecture

Bluesky Jetstream WebSocket (`wantedCollections=app.bsky.feed.post`) → `src/producer.py` → Kafka topic `bluesky-posts` (KRaft, no Zookeeper) → `src/consumer.py` → PostgreSQL `posts` table → Streamlit `src/dashboard.py`.

- Only Kafka, Kafka UI and Postgres run in Docker Compose. The Python apps run on the host in `.venv`.
- Host ports: Kafka `9092`, Kafka UI `8080`, Postgres **`5433`** (5432 is often taken by another local Postgres). The Postgres role is `bluesky`, not `postgres`. Kafka has a second `INTERNAL` listener (`kafka:9094`) so Kafka UI can reach it from inside Docker.
- The producer sends raw Jetstream JSON to Kafka, keyed by DID. All parsing happens in the consumer via `parse_event()` in `src/parsing.py`, which returns a post dict, `None` for events that aren't new posts, or raises on malformed events. The consumer's `parse_batch` catches those and skips the message.
- `created_at` uses the post's `createdAt` only if it is between 2023 and now+1 day, otherwise Jetstream's `time_us`. Some posts still carry a wrong clock within that range.
- Inserts are idempotent and batched: `UNIQUE(did, rkey)` with `ON CONFLICT DO NOTHING`. If a batch hits bad data, `insert_posts` retries row by row. The consumer commits Kafka offsets only after a successful insert.
- Dashboard SQL lives in `src/queries.py`, separate from the Streamlit UI. The dashboard refreshes with `st.fragment(run_every=10)`. "Latest posts" is ordered by arrival (`ingested_at`), not `created_at`.
- Config (Kafka, Jetstream, Postgres DSN) is env-overridable in `src/config.py`.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d                       # kafka, kafka-ui, postgres
cd src && python consumer.py               # terminal 1
cd src && python producer.py               # terminal 2
streamlit run src/dashboard.py             # terminal 3, http://localhost:8501
pytest                                     # all tests (Postgres must be up)
pytest tests/test_parsing.py               # unit tests only, no database
pytest tests/test_queries.py::test_top_hashtags   # single test
docker compose exec postgres psql -U bluesky -d bluesky
```

## Tests

- Tests run against a separate `bluesky_test` database that `tests/conftest.py` creates from `db/init.sql`. Never point them at the real `bluesky` database.
- Dashboard tests seed 10 fixed posts (`tests/helpers.py`) and assert exact numbers. Don't assert on live data.
- `pytest.ini` puts `src/` on the path, so tests import modules directly (`import queries`).

## Gotchas

- On macOS the Python processes show as `Python producer.py` (capital P), so `pkill -f "python producer.py"` matches nothing. Stop them by PID.
- Producer and consumer stop cleanly on SIGTERM or Ctrl-C (flush Kafka / close the consumer).

## Out of scope

Schema registry, multiple partitions or brokers, Dockerizing the Python apps, auth and dead-letter queues. Don't add them unless asked.
