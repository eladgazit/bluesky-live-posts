"""Integration tests: real Postgres, but the separate bluesky_test database."""
from datetime import datetime, timezone

from db import insert_posts
from helpers import SAMPLE_EVENT
from parsing import parse_event


def fetch_all(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT did, rkey, text, lang, hashtags, created_at FROM posts ORDER BY id")
        return cur.fetchall()


def count_posts(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM posts")
        return cur.fetchone()[0]


def post(n, **overrides):
    row = {
        "did": f"did:plc:{n}", "rkey": f"rk{n}", "text": f"text {n}", "lang": "en",
        "hashtags": [], "created_at": datetime(2024, 1, 1, 12, 0, n, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def test_inserted_rows_are_stored_correctly(conn):
    posts = [
        post(1, hashtags=["ai", "python"]),
        post(2, lang=None, text="no language"),
        post(3, text="multi\nline ünïcødé 😂"),
    ]

    assert insert_posts(conn, posts) == 3

    rows = fetch_all(conn)
    assert rows == [
        ("did:plc:1", "rk1", "text 1", "en", ["ai", "python"], posts[0]["created_at"]),
        ("did:plc:2", "rk2", "no language", None, [], posts[1]["created_at"]),
        ("did:plc:3", "rk3", "multi\nline ünïcødé 😂", "en", [], posts[2]["created_at"]),
    ]


def test_ingested_at_is_filled_in_automatically(conn):
    insert_posts(conn, [post(1)])
    with conn.cursor() as cur:
        cur.execute("SELECT ingested_at IS NOT NULL FROM posts")
        assert cur.fetchone()[0] is True


def test_duplicates_are_skipped(conn):
    assert insert_posts(conn, [post(1), post(2)]) == 2
    # Same (did, rkey) again, e.g. Kafka redelivering a message
    assert insert_posts(conn, [post(1, text="changed"), post(3)]) == 1

    assert count_posts(conn) == 3
    assert fetch_all(conn)[0][2] == "text 1"  # the original row was not overwritten


def test_duplicate_inside_one_batch_is_stored_once(conn):
    assert insert_posts(conn, [post(1), post(1)]) == 1
    assert count_posts(conn) == 1


def test_empty_batch_is_a_no_op(conn):
    assert insert_posts(conn, []) == 0
    assert count_posts(conn) == 0


def test_bad_row_is_skipped_but_good_rows_are_kept(conn):
    posts = [post(1), post(2, created_at="not-a-timestamp"), post(3)]

    assert insert_posts(conn, posts) == 2

    assert [row[0] for row in fetch_all(conn)] == ["did:plc:1", "did:plc:3"]


def test_parsed_event_round_trips_through_the_database(conn):
    parsed = parse_event(SAMPLE_EVENT)

    insert_posts(conn, [parsed])

    assert fetch_all(conn) == [(
        "did:plc:abc123", "3mxwno5i2tk2y", "Learning #Kafka and #AI today",
        "en", ["kafka", "ai"], datetime(2024, 5, 1, 12, 34, 56, 789000, tzinfo=timezone.utc),
    )]
