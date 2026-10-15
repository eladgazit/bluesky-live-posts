import os

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "bluesky-posts")

JETSTREAM_URL = os.getenv(
    "JETSTREAM_URL",
    "wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post",
)

# Host port 5433 (see docker-compose.yml)
POSTGRES_DSN = os.getenv(
    "POSTGRES_DSN", "postgresql://bluesky:bluesky@localhost:5433/bluesky"
)
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "bluesky-consumer")
