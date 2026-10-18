"""Tests for evals.py on fixed data. Time is fixed too: every eval gets an explicit `now`.

The `healthy` fixture holds 2,500 clean posts ingested in the last 4 minutes. Each test breaks
one thing in a known way and checks that exactly the right check changes status.
"""
from datetime import datetime, timedelta, timezone

import pytest

import evals

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
HEALTHY_ROWS = 2500


def seed(conn, n, now=NOW):
    """n clean posts, ingested 1-240 s before `now`, created 3 s before they were ingested.
    Every 10th post has one hashtag. Each post has its own author, so nothing repeats."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO posts (did, rkey, text, lang, hashtags, created_at, ingested_at)
            SELECT 'did:plc:u' || i, 'rk' || i, 'post ' || i, 'en',
                   CASE WHEN i %% 10 = 0 THEN ARRAY['tag'] ELSE '{}'::text[] END,
                   ts - interval '3 seconds', ts
            FROM (SELECT i, %(now)s::timestamptz - make_interval(secs => 1 + (i %% 240)) AS ts
                  FROM generate_series(1, %(n)s) AS i) s
        """, {"now": now, "n": n})
    conn.commit()


def damage(conn, set_clause, count):
    """Apply `SET ...` to the first `count` posts (by id)."""
    with conn.cursor() as cur:
        cur.execute(f"UPDATE posts SET {set_clause} "
                    f"WHERE id IN (SELECT id FROM posts ORDER BY id LIMIT {count})")
    conn.commit()


def statuses(conn, now=NOW, **kwargs):
    return {r.id: r.status for r in evals.run_evals(conn, now=now, **kwargs)}


@pytest.fixture
def healthy(conn):
    seed(conn, HEALTHY_ROWS)
    return conn


# --- the baseline -----------------------------------------------------------------

def test_healthy_data_passes_everything(healthy):
    results = evals.run_evals(healthy, now=NOW)
    assert {r.id: r.status for r in results if r.status != evals.PASS} == {}
    assert len(results) == 17
    assert evals.exit_code(results) == 0


def test_results_are_formatted_for_humans(healthy):
    text = evals.format_results(evals.run_evals(healthy, now=NOW))
    assert "17 PASS  0 WARN  0 FAIL  0 SKIP" in text
    assert "F1" in text and "Age of newest row" in text


# --- each check flips at its threshold: (check id, SET clause, rows hit, expected status) ---

DAMAGE = [
    # Completeness
    ("C1", "text = ''", 276, evals.WARN),                       # 11.0% > 10%
    ("C1", "text = ''", 650, evals.FAIL),                       # 26.0% > 25%
    ("C2", "lang = NULL", 525, evals.WARN),                     # 21% > 20%
    ("C2", "lang = NULL", 1025, evals.FAIL),                    # 41% > 40%
    ("C3", "hashtags = '{}'", 2500, evals.FAIL),                # 0% < 1%
    ("C3", "hashtags = ARRAY['t']", 875, evals.WARN),           # 10% + 25% = ~35% > 30%
    ("C3", "hashtags = ARRAY['t']", 1400, evals.FAIL),          # >50%
    ("C4", "text = NULL", 1, evals.FAIL),
    ("C4", "created_at = NULL", 1, evals.FAIL),
    ("C4", "did = ''", 1, evals.FAIL),
    # Uniqueness
    ("U2", "did = 'did:plc:spam', text = 'buy now'", 150, evals.WARN),   # 6% > 5%
    # Validity
    ("Va1", "lang = 'en-US'", 5, evals.WARN),                   # 0.2% > 0.1%
    ("Va1", "lang = 'en-US'", 50, evals.FAIL),                  # 2% > 1%
    ("Va1", "lang = 'EN'", 5, evals.WARN),                      # uppercase is invalid too
    ("Va2", "created_at = ingested_at + interval '10 minutes'", 5, evals.WARN),
    ("Va2", "created_at = ingested_at + interval '10 minutes'", 50, evals.FAIL),
    ("Va3", "created_at = timestamptz '2020-01-01'", 1, evals.FAIL),
    ("Va4", "created_at = ingested_at - interval '2 days'", 30, evals.WARN),   # 1.2% > 1%
    ("Va5", "text = repeat('x', 3001)", 1, evals.WARN),
    ("Va6", "hashtags = ARRAY['bad tag']", 1, evals.WARN),      # 1 of ~250 tags = 0.4%
    ("Va6", "hashtags = ARRAY['bad tag']", 100, evals.FAIL),
    ("Va6", "hashtags = ARRAY['#lead']", 1, evals.WARN),
    ("Va6", "hashtags = ARRAY['']", 1, evals.WARN),
    ("Va6", "hashtags = ARRAY[repeat('a', 65)]", 1, evals.WARN),
    ("Va7", "did = 'did:web:example.com'", 1, evals.PASS),     # did:web is valid too
    ("Va7", "did = 'not-a-did'", 1, evals.FAIL),
    ("Va7", "rkey = repeat('k', 513)", 1, evals.FAIL),
    # Freshness
    ("F2", "created_at = ingested_at - interval '45 seconds'", 2500, evals.WARN),
    ("F2", "created_at = ingested_at - interval '400 seconds'", 2500, evals.FAIL),
]


@pytest.mark.parametrize("check, set_clause, count, expected", DAMAGE,
                         ids=[f"{c}-{e}-{s[:22]}" for c, s, _, e in DAMAGE])
def test_check_flips_at_its_threshold(healthy, check, set_clause, count, expected):
    damage(healthy, set_clause, count)

    assert statuses(healthy)[check] == expected


def test_a_failed_check_makes_exit_code_1_but_warn_does_not(healthy):
    damage(healthy, "lang = 'en-US'", 5)            # WARN only
    assert evals.exit_code(evals.run_evals(healthy, now=NOW)) == 0

    damage(healthy, "did = 'not-a-did'", 1)         # FAIL
    assert evals.exit_code(evals.run_evals(healthy, now=NOW)) == 1


def test_damage_to_one_check_does_not_trip_the_others(healthy):
    damage(healthy, "did = 'not-a-did'", 1)

    assert {k: v for k, v in statuses(healthy).items() if v != evals.PASS} == {"Va7": evals.FAIL}


# --- uniqueness: needs the table constraint removed, so it runs in a rolled-back transaction ---

def test_duplicate_posts_are_detected(healthy):
    with healthy.cursor() as cur:
        cur.execute("ALTER TABLE posts DROP CONSTRAINT posts_did_rkey_key")
        cur.execute("""INSERT INTO posts (did, rkey, text, lang, created_at, ingested_at)
                       VALUES ('did:plc:u1', 'rk1', 'again', 'en', %(t)s, %(t)s)""", {"t": NOW})

    assert statuses(healthy)["U1"] == evals.FAIL

    healthy.rollback()  # DDL is transactional in Postgres: this restores the constraint


# --- time: freshness and volume are relative to `now` -----------------------------------

@pytest.mark.parametrize("seconds_later, expected", [(30, evals.PASS), (90, evals.WARN), (400, evals.FAIL)])
def test_freshness_grows_as_time_passes_without_new_posts(healthy, seconds_later, expected):
    # The newest seeded row is 1 s before NOW
    assert statuses(healthy, now=NOW + timedelta(seconds=seconds_later))["F1"] == expected


def test_volume_too_low(conn):
    seed(conn, 300)  # 300 posts in 5 minutes
    assert statuses(conn)["V1"] == evals.WARN


def test_volume_zero_is_a_failure(conn):
    seed(conn, 300)
    # 10 minutes later nothing has arrived in the last 5
    assert statuses(conn, now=NOW + timedelta(minutes=10))["V1"] == evals.FAIL


def test_volume_too_high(conn):
    seed(conn, 100_001)
    assert statuses(conn)["V2"] == evals.WARN


# --- window and small samples -----------------------------------------------------------

def test_rows_outside_the_window_are_ignored(healthy):
    # A terrible row, but ingested an hour ago: outside the 15-minute window
    with healthy.cursor() as cur:
        cur.execute("""INSERT INTO posts (did, rkey, text, lang, created_at, ingested_at)
                       VALUES ('garbage', 'k', 'x', 'EN', '2019-01-01', %s)""",
                    (NOW - timedelta(hours=1),))
    healthy.commit()

    assert statuses(healthy)["Va7"] == evals.PASS
    assert statuses(healthy)["Va3"] == evals.PASS
    # ...but a wider window sees it
    assert statuses(healthy, window_minutes=120)["Va7"] == evals.FAIL


def test_rate_checks_are_skipped_on_small_samples(conn):
    seed(conn, 300)
    damage(conn, "text = ''", 300)  # 100% empty, but only 300 rows

    result = statuses(conn)

    assert result["C1"] == evals.SKIP
    assert result["Va1"] == evals.SKIP
    assert result["Va6"] == evals.SKIP
    # Non-rate checks still run
    assert result["C4"] == evals.PASS
    assert result["U1"] == evals.PASS


def test_empty_table(conn):
    result = statuses(conn)

    assert result["F1"] == evals.FAIL
    assert result["V1"] == evals.FAIL
    assert result["F2"] == evals.SKIP
    assert result["C1"] == evals.SKIP
    assert evals.exit_code(evals.run_evals(conn, now=NOW)) == 1
