"""Turns a raw Jetstream event (dict) into a row for the posts table."""
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

POST_COLLECTION = "app.bsky.feed.post"
TAG_FEATURE = "app.bsky.richtext.facet#tag"
# Bluesky launched in 2023; anything earlier in createdAt is bogus client data
EARLIEST_VALID_YEAR = 2023


def _parse_created_at(value, fallback_us: int) -> datetime:
    """Use the post's createdAt if it looks sane, else Jetstream's event time."""
    try:
        # fromisoformat (py3.9) wants 'Z' as +00:00 and a 3- or 6-digit fraction
        text = value.replace("Z", "+00:00")
        text = re.sub(r"\.(\d+)", lambda m: "." + m.group(1)[:6].ljust(6, "0"), text)
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        too_far_ahead = datetime.now(timezone.utc) + timedelta(days=1)
        if EARLIEST_VALID_YEAR <= parsed.year and parsed <= too_far_ahead:
            return parsed
    except (AttributeError, ValueError):
        pass
    return datetime.fromtimestamp(fallback_us / 1_000_000, tz=timezone.utc)


def _extract_hashtags(record: dict) -> list:
    tags = []
    for facet in record.get("facets") or []:
        for feature in facet.get("features") or []:
            if feature.get("$type") == TAG_FEATURE and feature.get("tag"):
                tag = feature["tag"].lower().replace("\x00", "")
                if tag not in tags:
                    tags.append(tag)
    return tags


def parse_event(event: dict) -> Optional[dict]:
    """Return a post dict, or None if the event isn't a new post.

    Raises ValueError/KeyError/TypeError/AttributeError on malformed events;
    the consumer catches these and skips the message.
    """
    if event.get("kind") != "commit":
        return None
    commit = event["commit"]
    if commit.get("operation") != "create" or commit.get("collection") != POST_COLLECTION:
        return None

    record = commit["record"]
    langs = record.get("langs") or []
    return {
        "did": event["did"],
        "rkey": commit["rkey"],
        # Postgres text columns reject NUL characters
        "text": (record.get("text") or "").replace("\x00", ""),
        "lang": langs[0] if langs else None,
        "hashtags": _extract_hashtags(record),
        "created_at": _parse_created_at(record.get("createdAt"), event["time_us"]),
    }
