# Bluesky Streaming Pipeline: Hello World Plan

## Context
A beginner data-engineering project: stream live Bluesky posts through Kafka into Postgres and visualize them in Streamlit. The goal is to learn the core pattern (producer → broker → consumer → database → dashboard) with as few moving parts as possible.

After approval, this content is saved as `PLAN.md` in the project root. No code is written yet.

## Architecture

```
 Bluesky Jetstream (public WebSocket)
 wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post
            │  JSON events
            ▼
   ┌──────────────────┐
   │ producer (Python)│  runs on host, in venv
   └────────┬─────────┘
            │ raw event JSON
            ▼
   ┌──────────────────┐
   │ Kafka (KRaft)    │  Docker; topic: bluesky-posts
   └────────┬─────────┘
            │
            ▼
   ┌──────────────────┐
   │ consumer (Python)│  parse → insert
   └────────┬─────────┘
            ▼
   ┌──────────────────┐
   │ PostgreSQL       │  Docker; table: posts
   └────────┬─────────┘
            ▼
   ┌──────────────────┐
   │ Streamlit        │  host, http://localhost:8501
   └──────────────────┘
```

Only Kafka and Postgres run in Docker Compose. The Python apps run on the host in a venv, which is easiest to debug for learning.

## Folder structure

```
bluesky-pipeline/
├── PLAN.md
├── README.md
├── docker-compose.yml        # kafka (KRaft) + postgres
├── requirements.txt          # websockets, kafka-python, psycopg2-binary, streamlit, pandas, pytest
├── .env.example              # connection settings
├── sql/
│   └── init.sql              # CREATE TABLE posts (auto-run by Postgres on first start)
├── src/
│   ├── config.py             # env-based settings
│   ├── parsing.py            # parse_event(dict) -> post dict or None (pure function)
│   ├── db.py                 # get_conn(), insert_post()
│   ├── queries.py            # SQL for dashboard (posts/min, languages, hashtags, latest)
│   ├── producer.py           # Jetstream → Kafka
│   ├── consumer.py           # Kafka → Postgres
│   └── dashboard.py          # Streamlit app
└── tests/
    ├── conftest.py           # test DB fixture
    ├── test_parsing.py
    ├── test_db.py
    └── test_queries.py
```

## Data model

`posts(id serial PK, did text, rkey text, text text, lang text, hashtags text[], created_at timestamptz, ingested_at timestamptz default now(), UNIQUE(did, rkey))`

The `UNIQUE(did, rkey)` constraint plus `ON CONFLICT DO NOTHING` makes inserts idempotent, so Kafka redelivery is harmless.

## Step-by-step

1. **Setup.** Create the venv (`python -m venv .venv`), write `requirements.txt`, install, and add `.gitignore` (`.venv`, `.env`).
2. **Docker Compose.** Add Kafka (`apache/kafka` image, single node, KRaft combined broker+controller, port 9092) and Postgres (mounts `sql/init.sql`). Run `docker compose up -d` and check with `docker compose ps`.
3. **Topic.** Create `bluesky-posts` (1 partition) via `kafka-topics.sh` in the container, or rely on auto-create.
4. **Parsing (`parsing.py`) with TDD.**
   - Keep only events with `kind == "commit"`, `commit.operation == "create"` and `collection == "app.bsky.feed.post"`.
   - Extract `did`, `rkey`, `record.text`, `record.langs[0]` (or null), `record.createdAt`.
   - Hashtags come from `record.facets` features of type `app.bsky.richtext.facet#tag`, lowercased.
   - Return `None` for anything else.
   - Write `test_parsing.py` first with sample events: normal post, no lang, hashtags, non-post event, delete event.
5. **Producer.** Connect to Jetstream using `wantedCollections=app.bsky.feed.post`. Send each message to Kafka as bytes, keyed by DID. Add a reconnect loop with a short sleep.
6. **DB layer (`db.py`).** Add `insert_post()` with `ON CONFLICT DO NOTHING`. Write `test_db.py` against a separate `bluesky_test` database (or a transaction rolled back per test).
7. **Consumer.** Use group id `bluesky-consumer`, `auto_offset_reset="earliest"`, and call `parse_event` then `insert_post`. Commit offsets after a successful insert.
8. **Queries (`queries.py`).**
   - Posts per minute for the last 60 minutes: `date_trunc('minute', created_at)` with `count(*)`.
   - Top languages, top 10.
   - Top hashtags, top 10, via `unnest(hashtags)`.
   - 20 latest posts.
   - Write `test_queries.py` by seeding known rows and asserting the results.
9. **Dashboard.** Build `dashboard.py` with Streamlit: a line chart for posts per minute, bar charts for languages and hashtags, and a table of the latest posts. Add auto-refresh every ~5 seconds with a `st.rerun` loop or `streamlit-autorefresh`.
10. **End-to-end run.** Use 3 terminals (producer, consumer, dashboard) after `docker compose up -d`.
11. **README.** Write the run instructions and what was learned.

## Verification
- `docker compose ps` shows kafka and postgres healthy.
- Consume the topic directly (`kafka-console-consumer.sh`) to confirm messages arrive.
- `psql -c "select count(*) from posts"` grows while the consumer runs.
- `pytest` passes (parsing, db writes, queries).
- The dashboard at http://localhost:8501 shows all four panels updating.

## Out of scope (keep it simple)
Schema registry, multiple partitions or brokers, Dockerizing the Python apps, auth, and dead-letter queues. These are good follow-ups.
