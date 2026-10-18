"""Data quality evals for the posts table. EVALS.md explains each check and threshold.

Run with:  python src/evals.py [--window MINUTES] [--every SECONDS]   (exit code 1 if any check FAILs)

Every run is saved to the eval_results table (the dashboard reads it). With --every the
script keeps running and repeats the evals on that interval.
"""
import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from db import get_conn

WINDOW_MINUTES = 15          # rows ingested in this many minutes are checked
VOLUME_WINDOW_MINUTES = 5    # the volume checks always look at the last 5 minutes
MIN_ROWS_FOR_RATES = 500     # below this, percentages are noise, so rate checks are skipped

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"

# Everything the window-based checks need, computed in one pass over the window.
# `w` is the posts ingested in the window; all times come from the parameters, not now().
WINDOW_SQL = r"""
WITH w AS (
    SELECT * FROM posts
    WHERE ingested_at > %(now)s - make_interval(mins => %(window)s)
      AND ingested_at <= %(now)s
)
SELECT
    count(*)                                                              AS total,
    count(*) FILTER (WHERE btrim(text, E' \t\r\n') = '')                  AS empty_text,
    count(*) FILTER (WHERE lang IS NULL)                                  AS no_lang,
    count(lang)                                                           AS has_lang,
    count(*) FILTER (WHERE lang !~ '^[a-z]{2,3}$')                        AS bad_lang,
    count(*) FILTER (WHERE cardinality(hashtags) > 0)                     AS with_tags,
    count(*) FILTER (WHERE did IS NULL OR did = '' OR rkey IS NULL OR rkey = ''
                        OR text IS NULL OR created_at IS NULL)            AS missing_required,
    count(*) FILTER (WHERE created_at > ingested_at + interval '5 minutes')  AS future_created,
    count(*) FILTER (WHERE created_at < timestamptz '2023-01-01')         AS before_launch,
    count(*) FILTER (WHERE created_at < ingested_at - interval '1 day')   AS backdated,
    count(*) FILTER (WHERE length(text) > 3000)                           AS too_long,
    count(*) FILTER (WHERE did !~ '^did:(plc|web):' OR length(rkey) > 512) AS bad_ids,
    percentile_cont(0.5) WITHIN GROUP (
        ORDER BY extract(epoch FROM ingested_at - created_at))            AS median_lag_s,
    (SELECT count(*) FROM (SELECT 1 FROM w GROUP BY did, rkey
                           HAVING count(*) > 1) d)                        AS duplicate_groups,
    (SELECT coalesce(sum(n), 0)::bigint FROM (
        SELECT count(*) AS n FROM w WHERE btrim(text, E' \t\r\n') <> ''
        GROUP BY did, text HAVING count(*) > 1) r)                        AS repeated_rows,
    (SELECT count(*) FROM w, unnest(hashtags) AS tag)                     AS total_tags,
    (SELECT count(*) FROM w, unnest(hashtags) AS tag
      WHERE tag = '' OR tag ~ '^#' OR tag ~ '\s' OR length(tag) > 64)     AS bad_tags
FROM w
"""


@dataclass
class Result:
    id: str
    name: str
    status: str
    value: str
    rule: str


def _query(conn, sql: str, **params) -> dict:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [col[0] for col in cur.description]
        return dict(zip(columns, cur.fetchone()))


def _pct(part, whole) -> float:
    return 100.0 * part / whole if whole else 0.0


def _rule(label, low, high, unit) -> Optional[str]:
    pieces = []
    if low is not None:
        pieces.append(f"<{low:g}{unit}")
    if high is not None:
        pieces.append(f">{high:g}{unit}")
    return f"{label} " + " or ".join(pieces) if pieces else None


def _check(id, name, value, unit="", *, warn_hi=None, fail_hi=None, warn_lo=None, fail_lo=None):
    """Judge a number against its thresholds. "Any" means warn_hi/fail_hi = 0."""
    def breached(lo, hi):
        return (lo is not None and value < lo) or (hi is not None and value > hi)

    if breached(fail_lo, fail_hi):
        status = FAIL
    elif breached(warn_lo, warn_hi):
        status = WARN
    else:
        status = PASS
    rule = "; ".join(r for r in (_rule("warn", warn_lo, warn_hi, unit),
                                 _rule("fail", fail_lo, fail_hi, unit)) if r)
    shown = f"{int(value):,}" if float(value).is_integer() else f"{value:,.2f}"
    return Result(id, name, status, f"{shown}{unit}", rule)


def _skipped(id, name, reason) -> Result:
    return Result(id, name, SKIP, reason, "")


def run_evals(conn, window_minutes: int = WINDOW_MINUTES, now: Optional[datetime] = None) -> list:
    """Run every check and return a list of Result. Read-only."""
    if now is None:
        with conn.cursor() as cur:
            cur.execute("SELECT now()")  # the database's clock, which stamped ingested_at
            now = cur.fetchone()[0]

    m = _query(conn, WINDOW_SQL, now=now, window=window_minutes)
    newest = _query(conn, "SELECT max(ingested_at) AS newest FROM posts WHERE ingested_at <= %(now)s",
                    now=now)["newest"]
    recent = _query(conn, """SELECT count(*) AS n FROM posts WHERE ingested_at <= %(now)s
                             AND ingested_at > %(now)s - make_interval(mins => %(mins)s)""",
                    now=now, mins=VOLUME_WINDOW_MINUTES)["n"]

    total = m["total"]
    enough = total >= MIN_ROWS_FOR_RATES
    too_few = f"only {total} rows in window (need {MIN_ROWS_FOR_RATES})"

    def rate(id, name, value, unit="%", **limits):
        """A share-of-rows check, skipped when there are too few rows to trust a percentage."""
        return _check(id, name, value, unit, **limits) if enough else _skipped(id, name, too_few)

    results = []

    # --- Freshness
    if newest is None:
        results.append(Result("F1", "Age of newest row", FAIL, "table has no rows", ""))
    else:
        age = (now - newest).total_seconds()
        results.append(_check("F1", "Age of newest row", age, "s", warn_hi=60, fail_hi=300))
    if m["median_lag_s"] is None:
        results.append(_skipped("F2", "Median ingest lag", "no rows in window"))
    else:
        results.append(_check("F2", "Median ingest lag", m["median_lag_s"], "s",
                              warn_hi=30, fail_hi=300))

    # --- Volume
    results.append(_check("V1", "Too few posts (last 5 min)", recent, warn_lo=2000, fail_lo=1))
    results.append(_check("V2", "Too many posts (last 5 min)", recent, warn_hi=100_000))

    # --- Completeness
    results.append(rate("C1", "Empty text", _pct(m["empty_text"], total), warn_hi=10, fail_hi=25))
    results.append(rate("C2", "Missing language", _pct(m["no_lang"], total), warn_hi=20, fail_hi=40))
    results.append(rate("C3", "Hashtag coverage", _pct(m["with_tags"], total),
                        warn_lo=3, warn_hi=30, fail_lo=1, fail_hi=50))
    results.append(_check("C4", "Required fields missing", m["missing_required"], fail_hi=0))

    # --- Uniqueness
    results.append(_check("U1", "Duplicate (did, rkey) groups", m["duplicate_groups"], fail_hi=0))
    results.append(rate("U2", "Repeated content", _pct(m["repeated_rows"], total), warn_hi=5))

    # --- Validity
    results.append(rate("Va1", "Language format", _pct(m["bad_lang"], m["has_lang"]),
                        warn_hi=0.1, fail_hi=1))
    results.append(rate("Va2", "Future timestamps (>5 min)", _pct(m["future_created"], total),
                        warn_hi=0.1, fail_hi=1))
    results.append(_check("Va3", "Posts before 2023", m["before_launch"], fail_hi=0))
    results.append(rate("Va4", "Backdated posts (>1 day)", _pct(m["backdated"], total), warn_hi=1))
    results.append(_check("Va5", "Text over 3000 chars", m["too_long"], warn_hi=0))
    results.append(rate("Va6", "Hashtag format", _pct(m["bad_tags"], m["total_tags"]),
                        warn_hi=0.1, fail_hi=2))
    results.append(_check("Va7", "Bad DID or rkey", m["bad_ids"], fail_hi=0))

    return results


def save_results(conn, results: list, run_at: Optional[datetime] = None) -> None:
    """Append one row per check to eval_results; all rows of a run share run_at."""
    if run_at is None:
        run_at = datetime.now(timezone.utc)
    rows = [(run_at, r.id, r.name, r.status, r.value, r.status != FAIL) for r in results]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO eval_results (run_at, check_id, check_name, status, value, passed) "
            "VALUES (%s, %s, %s, %s, %s, %s)", rows)
    conn.commit()


def exit_code(results: list) -> int:
    return 1 if any(r.status == FAIL for r in results) else 0


def format_results(results: list, window_minutes: int = WINDOW_MINUTES) -> str:
    lines = [f"Data quality evals (rows ingested in the last {window_minutes} min)", ""]
    for r in results:
        lines.append(f"{r.status:<5}{r.id:<5}{r.name:<30}{r.value:<24}{r.rule}")
    counts = {s: sum(r.status == s for r in results) for s in (PASS, WARN, FAIL, SKIP)}
    lines += ["", "  ".join(f"{n} {s}" for s, n in counts.items())]
    return "\n".join(lines)


def run_once(window_minutes: int, save: bool = True) -> list:
    conn = get_conn()
    try:
        results = run_evals(conn, window_minutes=window_minutes)
        if save:
            save_results(conn, results)
    finally:
        conn.rollback()  # nothing left uncommitted
        conn.close()
    print(format_results(results, window_minutes), flush=True)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Check data quality of the posts table")
    parser.add_argument("--window", type=int, default=WINDOW_MINUTES, help="minutes (default 15)")
    parser.add_argument("--every", type=int, metavar="SECONDS",
                        help="keep running and repeat the evals on this interval")
    parser.add_argument("--no-save", action="store_true", help="don't write to eval_results")
    args = parser.parse_args()

    if args.every:
        while True:  # a failed run (e.g. Postgres restarting) must not stop the loop
            try:
                run_once(args.window, save=not args.no_save)
            except Exception as exc:
                print(f"eval run failed: {exc}", file=sys.stderr, flush=True)
            time.sleep(args.every)

    sys.exit(exit_code(run_once(args.window, save=not args.no_save)))


if __name__ == "__main__":
    main()
