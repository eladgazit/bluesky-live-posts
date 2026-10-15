"""Fixed sample data shared by the tests. Nothing here is random or live."""
from datetime import datetime, timedelta, timezone

# A realistic Jetstream "create post" event, trimmed to the fields we use
SAMPLE_EVENT = {
    "did": "did:plc:abc123",
    "time_us": 1_700_000_000_000_000,  # 2023-11-14 22:13:20 UTC
    "kind": "commit",
    "commit": {
        "rev": "3mxwno5lvyk2l",
        "operation": "create",
        "collection": "app.bsky.feed.post",
        "rkey": "3mxwno5i2tk2y",
        "record": {
            "$type": "app.bsky.feed.post",
            "createdAt": "2024-05-01T12:34:56.789Z",
            "langs": ["en", "fr"],
            "text": "Learning #Kafka and #AI today",
            "facets": [
                {
                    "index": {"byteStart": 9, "byteEnd": 15},
                    "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": "Kafka"}],
                },
                {
                    "index": {"byteStart": 20, "byteEnd": 23},
                    "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": "AI"}],
                },
            ],
        },
    },
}


def make_sample_posts(now: datetime = None) -> list:
    """10 posts with known properties. Inserted in this order, so post 10 is the newest.

    Languages:  en=3, es=2, de=1, no language=4
    Hashtags:   ai=2 (posts 1, 2), data=1 (post 4), python=1 (post 1)
    Minutes:    minute-1 -> 3 posts, minute-2 -> 2, minute-5 -> 2, minute-30 -> 1
                (the last 2 are outside the 60-minute window: 2 hours ago, 2 hours ahead)
    """
    now = now or datetime.now(timezone.utc)
    base = now.replace(second=0, microsecond=0)
    spec = [
        # (lang, hashtags, created_at)
        ("en", ["ai", "python"], base - timedelta(minutes=1)),
        ("en", ["ai"], base - timedelta(minutes=1)),
        ("en", [], base - timedelta(minutes=1)),
        ("es", ["data"], base - timedelta(minutes=2)),
        ("es", [], base - timedelta(minutes=2)),
        ("de", [], base - timedelta(minutes=5)),
        (None, [], base - timedelta(minutes=5)),
        (None, [], base - timedelta(minutes=30)),
        (None, [], base - timedelta(hours=2)),
        (None, [], base + timedelta(hours=2)),
    ]
    return [
        {
            "did": f"did:plc:test{i}",
            "rkey": f"rkey{i}",
            "text": f"post {i}",
            "lang": lang,
            "hashtags": tags,
            "created_at": created_at,
        }
        for i, (lang, tags, created_at) in enumerate(spec, start=1)
    ]
