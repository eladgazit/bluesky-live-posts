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
