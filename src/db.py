import logging

import psycopg2
from psycopg2.extras import execute_values

from config import POSTGRES_DSN

log = logging.getLogger("db")

INSERT_SQL = """
    INSERT INTO posts (did, rkey, text, lang, hashtags, created_at)
    VALUES %s
    ON CONFLICT (did, rkey) DO NOTHING
    RETURNING id
"""


def get_conn():
    return psycopg2.connect(POSTGRES_DSN)


def _to_tuple(post: dict) -> tuple:
    return (
        post["did"], post["rkey"], post["text"],
        post["lang"], post["hashtags"], post["created_at"],
    )


def insert_posts(conn, posts: list) -> int:
    """Insert posts in one batch; returns how many were new (duplicates are skipped).

    If the batch fails on bad data, retry row by row so one bad post
    doesn't take the rest of the batch down with it.
    """
    if not posts:
        return 0
    rows = [_to_tuple(p) for p in posts]
    try:
        with conn.cursor() as cur:
            inserted = execute_values(cur, INSERT_SQL, rows, fetch=True)
        conn.commit()
        return len(inserted)
    except psycopg2.DataError:
        conn.rollback()
        log.warning("Batch of %d failed on bad data; retrying row by row", len(rows))

    inserted = 0
    for row in rows:
        try:
            with conn.cursor() as cur:
                inserted += len(execute_values(cur, INSERT_SQL, [row], fetch=True))
            conn.commit()
        except psycopg2.DataError as exc:
            conn.rollback()
            log.warning("Skipping bad row %s/%s: %s", row[0], row[1], exc)
    return inserted
