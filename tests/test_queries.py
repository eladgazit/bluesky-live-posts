"""Dashboard tests. The `seeded` fixture holds 10 fixed posts (see helpers.make_sample_posts)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import evals
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
    assert queries.latest_eval_run(conn) == []
    assert queries.eval_score_history(conn) == []
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
        "Data quality",
    ]

    hashtags, latest = at.dataframe[0].value, at.dataframe[1].value
    assert hashtags.values.tolist() == [["ai", 2], ["data", 1], ["python", 1]]
    assert len(latest) == 10
    assert latest["text"].iloc[0] == "post 10"


def test_dashboard_loads_with_an_empty_database(conn, dashboard_db):
    at = run_dashboard()

    assert not at.exception
    assert len(at.subheader) == 5
    assert at.info[0].value.startswith("No eval runs saved yet")


# --- eval results: saved runs, latest run, score over time ----------------------

def save_run(conn, run_at, statuses):
    """Save a fake eval run: one check per status in `statuses` (ids C1, C2, ...)."""
    results = [evals.Result(f"C{i}", f"check {i}", status, str(i), "")
               for i, status in enumerate(statuses, start=1)]
    evals.save_results(conn, results, run_at)


def test_latest_eval_run_returns_only_the_newest_run(conn):
    t = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    save_run(conn, t, [evals.FAIL, evals.PASS])
    save_run(conn, t + timedelta(minutes=1), [evals.PASS, evals.WARN, evals.SKIP])

    assert queries.latest_eval_run(conn) == [
        ("C1", "check 1", "PASS", "1"),
        ("C2", "check 2", "WARN", "2"),
        ("C3", "check 3", "SKIP", "3"),
    ]


def test_eval_score_history_counts_fail_as_not_passed_and_ignores_skip(conn):
    t = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    save_run(conn, t, [evals.FAIL, evals.PASS, evals.PASS])
    save_run(conn, t + timedelta(minutes=1), [evals.PASS, evals.WARN, evals.SKIP])

    # WARN counts as passed (like the exit code); SKIP is left out of both numbers
    assert queries.eval_score_history(conn) == [(t, 2, 3), (t + timedelta(minutes=1), 2, 2)]


def test_eval_score_history_keeps_only_the_last_runs(conn):
    t = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    for i in range(5):
        save_run(conn, t + timedelta(minutes=i), [evals.PASS])

    history = queries.eval_score_history(conn, limit=3)

    assert [row[0] for row in history] == [t + timedelta(minutes=i) for i in (2, 3, 4)]


def test_dashboard_shows_the_data_quality_panel(conn, dashboard_db):
    t = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    save_run(conn, t, [evals.PASS, evals.PASS, evals.FAIL, evals.WARN])

    at = run_dashboard()

    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics == {"Score (latest run)": "3/4", "Warnings": "1", "Failures": "1"}
    assert len(at.dataframe[-1].value) == 4
