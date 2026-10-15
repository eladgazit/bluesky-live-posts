"""Reads events from Kafka, parses them, and inserts posts into Postgres in batches."""
import json
import logging
import signal

from kafka import KafkaConsumer

from config import KAFKA_BOOTSTRAP, KAFKA_GROUP_ID, KAFKA_TOPIC
from db import get_conn, insert_posts
from parsing import parse_event

MAX_BATCH = 500

log = logging.getLogger("consumer")


def parse_batch(messages) -> tuple:
    """Returns (posts, ignored, malformed) for a list of Kafka messages."""
    posts, ignored, malformed = [], 0, 0
    for msg in messages:
        try:
            post = parse_event(json.loads(msg.value))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            malformed += 1
            log.warning("Skipping malformed message at offset %d: %r", msg.offset, exc)
            continue
        if post is None:
            ignored += 1
        else:
            posts.append(post)
    return posts, ignored, malformed


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    consumer = KafkaConsumer(
        KAFKA_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=KAFKA_GROUP_ID,
        auto_offset_reset="earliest",
        enable_auto_commit=False,  # we commit only after the DB insert succeeds
    )
    conn = get_conn()
    signal.signal(signal.SIGTERM, signal.default_int_handler)  # same as Ctrl-C

    totals = {"read": 0, "inserted": 0, "ignored": 0, "malformed": 0}
    try:
        while True:
            batches = consumer.poll(timeout_ms=1000, max_records=MAX_BATCH)
            messages = [m for batch in batches.values() for m in batch]
            if not messages:
                continue

            posts, ignored, malformed = parse_batch(messages)
            inserted = insert_posts(conn, posts)
            consumer.commit()

            totals["read"] += len(messages)
            totals["inserted"] += inserted
            totals["ignored"] += ignored
            totals["malformed"] += malformed
            log.info(
                "Batch of %d: %d inserted, %d duplicate, %d ignored, %d malformed | totals %s",
                len(messages), inserted, len(posts) - inserted, ignored, malformed, totals,
            )
    except KeyboardInterrupt:
        log.info("Stopping")
    finally:
        consumer.close()
        conn.close()


if __name__ == "__main__":
    main()
