CREATE TABLE IF NOT EXISTS posts (
    id          SERIAL PRIMARY KEY,
    did         TEXT        NOT NULL,
    rkey        TEXT        NOT NULL,
    text        TEXT,
    lang        TEXT,
    hashtags    TEXT[]      NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (did, rkey)
);

CREATE INDEX IF NOT EXISTS idx_posts_created_at ON posts (created_at);

-- One row per check per eval run (see src/evals.py). All rows of a run share run_at.
-- passed is false only for FAIL; WARN and SKIP count as passed, like the exit code.
CREATE TABLE IF NOT EXISTS eval_results (
    id        SERIAL PRIMARY KEY,
    run_at    TIMESTAMPTZ NOT NULL,
    check_id  TEXT        NOT NULL,
    check_name TEXT       NOT NULL,
    status    TEXT        NOT NULL,
    value     TEXT        NOT NULL,
    passed    BOOLEAN     NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_eval_results_run_at ON eval_results (run_at);
