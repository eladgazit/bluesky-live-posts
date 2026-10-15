"""SQL for the dashboard. Each function takes a psycopg2 connection and returns plain rows."""


def _fetch(conn, sql: str) -> list:
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def posts_per_minute(conn) -> list:
    """(minute, count) for each of the last 60 minutes, including minutes with 0 posts.

    Posts with a createdAt in the future (wrong client clocks) fall outside the window.
    """
    return _fetch(conn, """
        SELECT m.minute, count(p.id)
        FROM generate_series(
                 date_trunc('minute', now()) - interval '59 minutes',
                 date_trunc('minute', now()),
                 interval '1 minute') AS m(minute)
        LEFT JOIN posts p ON date_trunc('minute', p.created_at) = m.minute
        GROUP BY m.minute
        ORDER BY m.minute
    """)


def top_languages(conn, limit: int = 10) -> list:
    """(lang, count) most used first; posts without a language are labelled 'unknown'."""
    return _fetch(conn, f"""
        SELECT coalesce(lang, 'unknown') AS lang, count(*) AS posts
        FROM posts
        GROUP BY 1
        ORDER BY posts DESC, lang
        LIMIT {int(limit)}
    """)


def top_hashtags(conn, limit: int = 15) -> list:
    """(hashtag, count) most used first."""
    return _fetch(conn, f"""
        SELECT tag, count(*) AS posts
        FROM posts, unnest(hashtags) AS tag
        GROUP BY tag
        ORDER BY posts DESC, tag
        LIMIT {int(limit)}
    """)


def latest_posts(conn, limit: int = 20) -> list:
    """(created_at, lang, text, did) newest first, by arrival order rather than createdAt."""
    return _fetch(conn, f"""
        SELECT created_at, lang, text, did
        FROM posts
        ORDER BY ingested_at DESC, id DESC
        LIMIT {int(limit)}
    """)
