"""Dashboard tests. The `seeded` fixture holds 10 fixed posts (see helpers.make_sample_posts)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import queries

DASHBOARD = str(Path(__file__).resolve().parent.parent / "src" / "dashboard.py")


# --- queries.py: exact numbers ------------------------------------------------

def test_top_languages(seeded):
    assert queries.top_languages(seeded) == [
        ("unknown", 4),  # posts without a language
        ("en", 3),
        ("es", 2),
        ("de", 1),
    ]


def test_top_languages_respects_limit(seeded):
    assert queries.top_languages(seeded, limit=2) == [("unknown", 4), ("en", 3)]


def test_top_hashtags(seeded):
    # ai appears in 2 posts; data and python once each (ties are sorted alphabetically)
    assert queries.top_hashtags(seeded) == [("ai", 2), ("data", 1), ("python", 1)]


def test_top_hashtags_respects_limit(seeded):
    assert queries.top_hashtags(seeded, limit=1) == [("ai", 2)]


def test_posts_per_minute(seeded, seed_time):
    rows = queries.posts_per_minute(seeded)
    counts = {minute: count for minute, count in rows}

    # Always exactly 60 consecutive one-minute buckets, oldest first
    assert len(rows) == 60
    minutes = [minute for minute, _ in rows]
    assert minutes == sorted(minutes)
    assert all(b - a == timedelta(minutes=1) for a, b in zip(minutes, minutes[1:]))

    # Our seed minutes, relative to the seed time. If the clock ticks over to a new minute
    # while the test runs, the window shifts by one but these offsets still fall inside it.
    base = seed_time.replace(second=0, microsecond=0)
    assert counts[base - timedelta(minutes=1)] == 3
    assert counts[base - timedelta(minutes=2)] == 2
    assert counts[base - timedelta(minutes=5)] == 2
    assert counts[base - timedelta(minutes=30)] == 1

    # 10 posts - 1 (2h ago) - 1 (2h in the future) = 8 inside the window; every other minute is 0
    assert sum(counts.values()) == 8
    assert sum(1 for n in counts.values() if n == 0) == 60 - 4


def test_latest_posts_newest_first(seeded):
    rows = queries.latest_posts(seeded, limit=3)

    # Columns: (created_at, lang, text, did). Post 10 was inserted last.
    assert [row[2] for row in rows] == ["post 10", "post 9", "post 8"]
    assert [row[1] for row in rows] == [None, None, None]
    assert [row[3] for row in rows] == ["did:plc:test10", "did:plc:test9", "did:plc:test8"]


def test_latest_posts_returns_at_most_the_limit(seeded):
    assert len(queries.latest_posts(seeded, limit=4)) == 4
    # With the default limit of 20 we only have 10 posts to return
    assert len(queries.latest_posts(seeded)) == 10


def test_queries_on_an_empty_table(conn):
    assert queries.top_languages(conn) == []
    assert queries.top_hashtags(conn) == []
    assert queries.latest_posts(conn) == []
    rows = queries.posts_per_minute(conn)
    assert len(rows) == 60 and all(count == 0 for _, count in rows)


# --- dashboard.py: loads without errors ---------------------------------------

def run_dashboard():
    return AppTest.from_file(DASHBOARD, default_timeout=30).run()


def test_dashboard_loads_with_data(seeded, dashboard_db):
    at = run_dashboard()

    assert not at.exception
    assert [s.value for s in at.subheader] == [
        "Posts per minute (last hour)",
        "Top 10 languages",
        "Top 15 hashtags",
        "20 latest posts",
    ]

    hashtags, latest = at.dataframe[0].value, at.dataframe[1].value
    assert hashtags.values.tolist() == [["ai", 2], ["data", 1], ["python", 1]]
    assert len(latest) == 10
    assert latest["text"].iloc[0] == "post 10"


def test_dashboard_loads_with_an_empty_database(conn, dashboard_db):
    at = run_dashboard()

    assert not at.exception
    assert len(at.subheader) == 4
