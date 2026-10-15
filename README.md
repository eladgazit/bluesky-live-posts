# Bluesky Streaming Pipeline

A real-time data pipeline that captures every new public post on [Bluesky](https://bsky.app), stores it in a database, and shows live statistics on a dashboard.

It connects to Bluesky's public [Jetstream](https://github.com/bluesky-social/jetstream) feed, buffers the events in Kafka, extracts the useful fields (text, language, hashtags, author, creation time) into PostgreSQL, and visualizes what is happening right now with Streamlit: posts per minute, top languages, top hashtags and the latest posts.

## Architecture

```mermaid
flowchart LR
    J["Bluesky Jetstream<br/>public WebSocket<br/>(posts only)"]
    P["producer.py"]
    K[("Kafka (KRaft)<br/>topic: bluesky-posts")]
    C["consumer.py<br/>parse + batch insert"]
    D[("PostgreSQL<br/>table: posts")]
    S["dashboard.py<br/>Streamlit"]
    U(["Browser<br/>localhost:8501"])
    KU["Kafka UI<br/>localhost:8080"]

    J -- "JSON events" --> P
    P -- "raw JSON, keyed by author DID" --> K
    K --> C
    C -- "batched INSERTs" --> D
    D -- "SQL queries (queries.py)" --> S
    S --> U
    K -. "inspect topic" .-> KU

    subgraph docker["Docker Compose"]
        K
        D
        KU
    end
    subgraph host["Python virtual environment"]
        P
        C
        S
    end
```

Kafka, PostgreSQL and Kafka UI run in Docker. The three Python programs run on the host in a virtual environment.

Kafka sits between ingestion and storage on purpose. The producer never waits for the database, and the consumer can fall behind, restart or be replaced without losing events, because Kafka retains them and remembers how far the consumer has read.

## Data flow

1. **Ingest:** the producer holds a WebSocket connection to Jetstream, filtered to `app.bsky.feed.post`, and publishes each raw event to Kafka.
2. **Buffer:** Kafka stores the events in the `bluesky-posts` topic.
3. **Transform and load:** the consumer reads events in batches, keeps only newly created posts, extracts the fields and inserts them into PostgreSQL.
4. **Serve:** the dashboard queries PostgreSQL every 10 seconds and redraws.

## Modules

### `docker-compose.yml`: infrastructure
| Service | Image | Host port | Purpose |
|---|---|---|---|
| `kafka` | `apache/kafka:3.9.0` | 9092 | Single-node Kafka in KRaft mode (no Zookeeper). One node is both broker and controller. |
| `kafka-ui` | `provectuslabs/kafka-ui` | 8080 | Web UI to browse topics and messages. |
| `postgres` | `postgres:16` | 5433 | The database. Runs `db/init.sql` on first start. Data is kept in the `pgdata` volume. |

Kafka exposes two listeners: `localhost:9092` for programs on your machine and `kafka:9094` for other containers (Kafka UI). Postgres is mapped to host port **5433** so it doesn't clash with a local Postgres on 5432. Kafka and PostgreSQL have health checks, and Kafka UI starts only once Kafka is healthy.

### `db/init.sql`: schema
Creates the `posts` table:

| Column | Type | Notes |
|---|---|---|
| `id` | serial | primary key |
| `did` | text | author's decentralized identifier |
| `rkey` | text | the post's record key (unique per author) |
| `text` | text | post text |
| `lang` | text | first language tag, or `NULL` |
| `hashtags` | text[] | lowercased hashtags, empty array if none |
| `created_at` | timestamptz | when the post was created |
| `ingested_at` | timestamptz | when the row was inserted (defaults to `now()`) |

`UNIQUE (did, rkey)` identifies a post and makes inserts idempotent. There is an index on `created_at`.

### `src/config.py`: settings
Reads settings from environment variables, with defaults that match `docker-compose.yml`.

| Variable | Default |
|---|---|
| `KAFKA_BOOTSTRAP` | `localhost:9092` |
| `KAFKA_TOPIC` | `bluesky-posts` |
| `KAFKA_GROUP_ID` | `bluesky-consumer` |
| `JETSTREAM_URL` | `wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post` |
| `POSTGRES_DSN` | `postgresql://bluesky:bluesky@localhost:5433/bluesky` |

### `src/producer.py`: Jetstream to Kafka
- Opens the Jetstream WebSocket and forwards every message to Kafka unchanged.
- Uses the author's DID as the Kafka message key, so one author's posts stay in order within a partition.
- Logs progress every 100 messages.
- Reconnects automatically if the connection drops, waiting 1 second and doubling up to 30 seconds between attempts.
- On Ctrl-C or SIGTERM it flushes pending messages before exiting.

The producer does no parsing. Keeping raw events in Kafka means the parsing logic can be changed later and the data re-processed.

### `src/parsing.py`: event to row
`parse_event(event)` is a pure function that turns one raw Jetstream event into a row for the `posts` table.

- Returns `None` for anything that isn't a newly created post (deletes, updates, likes, account and identity events).
- **Language:** the first entry of the post's `langs`, or `None`.
- **Hashtags:** taken from the post's rich-text facets of type `app.bsky.richtext.facet#tag`, lowercased and de-duplicated. Hashtags that appear only in the text, without a facet, are not counted.
- **Created time:** the post's own `createdAt` when it is plausible (2023 or later, and no more than a day in the future). Otherwise Jetstream's event time (`time_us`) is used, because clients sometimes send bogus clocks.
- **Text:** NUL characters are removed, since PostgreSQL rejects them.
- Raises an exception for structurally malformed events. The consumer catches this.

### `src/db.py`: database writes
- `get_conn()` opens a PostgreSQL connection.
- `insert_posts(conn, posts)` inserts a whole batch in one statement with `ON CONFLICT (did, rkey) DO NOTHING`, and returns how many rows were actually new. If the batch fails on bad data, it retries row by row, so one bad post does not discard the others.

### `src/consumer.py`: Kafka to PostgreSQL
- Reads the `bluesky-posts` topic as consumer group `bluesky-consumer`, starting from the beginning on first run.
- Polls up to 500 messages at a time, parses them and inserts them as one batch.
- **Malformed messages** (invalid JSON, missing fields) are logged and skipped without stopping the consumer.
- **Offsets are committed only after the insert succeeds.** If the consumer crashes, it re-reads uncommitted messages on restart. Because inserts ignore duplicates, this produces no duplicate rows.
- Logs each batch with counts of inserted, duplicate, ignored and malformed messages, plus running totals.

### `src/queries.py`: dashboard SQL
All SQL used by the dashboard, kept separate from the UI so it can be tested. Each function takes a connection and returns plain rows.

| Function | Returns |
|---|---|
| `posts_per_minute` | A `(minute, count)` row for each of the last 60 minutes, with 0 for quiet minutes. Posts with a future timestamp fall outside the window. |
| `top_languages` | The most used languages (default 10). Posts without a language are labelled `unknown`. |
| `top_hashtags` | The most used hashtags (default 15). |
| `latest_posts` | The newest posts (default 20), ordered by arrival time, so a post with a wrong clock cannot stick to the top. |

### `src/dashboard.py`: Streamlit app
A single page showing a line chart of posts per minute (last hour), a bar chart of the top 10 languages, a table of the top 15 hashtags and a table of the 20 latest posts. It refreshes every 10 seconds, redrawing only the data rather than reloading the page, and opens a short-lived database connection on each refresh.

### `tests/`: automated tests
Run with `pytest` (PostgreSQL must be running).

| File | Kind | Covers |
|---|---|---|
| `test_parsing.py` | Unit | Parsing a sample message, hashtag extraction, missing or malformed fields, timestamp handling, and the consumer skipping bad messages. No database needed. |
| `test_db.py` | Integration | Rows stored correctly, duplicate handling, bad rows skipped, and a parsed event surviving the round trip through PostgreSQL. |
| `test_queries.py` | Dashboard | Every function in `queries.py` against 10 fixed seed posts with exact expected numbers, plus the dashboard loading with and without data (Streamlit `AppTest`). |

Tests use a separate `bluesky_test` database, created automatically from `db/init.sql`, and refuse to run against any other database. They assert exact numbers on fixed data instead of live data, since the live stream is always changing.

## Getting started

**Prerequisites:** Docker with Docker Compose, and Python 3.9 or newer.

```bash
# 1. Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Start Kafka, Kafka UI and PostgreSQL
docker compose up -d
docker compose ps                      # wait until kafka and postgres show (healthy)

# 3. Create the topic
docker exec bluesky-kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --create --if-not-exists \
  --topic bluesky-posts --partitions 1 --replication-factor 1
```

Then start the three programs, each in its own terminal (with the virtual environment activated):

```bash
cd src && python consumer.py           # terminal 1
cd src && python producer.py           # terminal 2
streamlit run src/dashboard.py         # terminal 3, from the project root
```

Open the dashboard at <http://localhost:8501>. Kafka UI is at <http://localhost:8080>. Stop each program with Ctrl-C and the infrastructure with `docker compose down` (add `-v` to also delete the stored posts).

### Inspecting the data
```bash
docker compose exec postgres psql -U bluesky -d bluesky -c "SELECT count(*) FROM posts;"
psql postgresql://bluesky:bluesky@localhost:5433/bluesky     # from your machine
```
The database user is `bluesky` (there is no `postgres` role), and the host port is 5433.

### Running the tests
```bash
pytest                                 # everything
pytest tests/test_parsing.py           # unit tests only, no database
```

## Project structure

```
.
├── docker-compose.yml     # Kafka (KRaft), Kafka UI, PostgreSQL
├── db/init.sql            # posts table
├── requirements.txt
├── pytest.ini
├── src/
│   ├── config.py          # settings from environment variables
│   ├── producer.py        # Jetstream -> Kafka
│   ├── parsing.py         # raw event -> post row
│   ├── db.py              # connection and batch insert
│   ├── consumer.py        # Kafka -> PostgreSQL
│   ├── queries.py         # dashboard SQL
│   └── dashboard.py       # Streamlit app
└── tests/                 # unit, integration and dashboard tests
```

## Notes and limitations

- **Delivery:** Kafka consumption is at-least-once, and the unique key on `(did, rkey)` makes the database result effectively exactly-once.
- **Single node:** one Kafka broker and one partition. This is a deliberately minimal setup, not a high-availability one.
- **No cursor:** the producer does not resume from a Jetstream cursor, so events published while it is offline are not recovered.
- **Timestamps:** some posts carry a wrong client clock. These are filtered out of the posts-per-minute chart if they are in the future, but backdated posts still count in their original minute.
- **Credentials:** the database password in `docker-compose.yml` is a development default. Change it before exposing anything beyond localhost.
