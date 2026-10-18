import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import pytest

import db
from helpers import make_sample_posts

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://bluesky:bluesky@localhost:5433/bluesky")
TEST_DB = "bluesky_test"
TEST_DSN = os.getenv("TEST_POSTGRES_DSN", f"postgresql://bluesky:bluesky@localhost:5433/{TEST_DB}")
INIT_SQL = Path(__file__).resolve().parent.parent / "db" / "init.sql"


@pytest.fixture(scope="session")
def test_dsn():
    """Create the separate test database (once) and apply db/init.sql to it."""
    assert TEST_DSN.endswith(f"/{TEST_DB}"), "tests must never run against the real database"
    try:
        admin = psycopg2.connect(ADMIN_DSN)
    except psycopg2.OperationalError as exc:
        pytest.fail(f"Postgres is not reachable ({exc}). Run: docker compose up -d")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,))
        if not cur.fetchone():
            cur.execute(f"CREATE DATABASE {TEST_DB}")
    admin.close()

    conn = psycopg2.connect(TEST_DSN)
    with conn, conn.cursor() as cur:
        cur.execute(INIT_SQL.read_text())
    conn.close()
    return TEST_DSN


@pytest.fixture
def conn(test_dsn):
    """A connection to the test database with an empty posts table."""
    connection = psycopg2.connect(test_dsn)
    with connection.cursor() as cur:
        cur.execute("TRUNCATE posts, eval_results RESTART IDENTITY")
    connection.commit()
    yield connection
    connection.close()


@pytest.fixture
def seed_time():
    """The moment the sample posts are built around. Shared so tests agree with the seed
    even if the clock ticks over to a new minute mid-test."""
    return datetime.now(timezone.utc)


@pytest.fixture
def seeded(conn, seed_time):
    """The test database holding the 10 fixed sample posts."""
    posts = make_sample_posts(seed_time)
    assert db.insert_posts(conn, posts) == 10
    return conn


@pytest.fixture
def dashboard_db(test_dsn, monkeypatch):
    """Point the dashboard (which uses db.get_conn) at the test database."""
    monkeypatch.setattr(db, "POSTGRES_DSN", test_dsn)
