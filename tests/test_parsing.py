"""Unit tests: no database, no Kafka. Pure parsing logic."""
import copy
from collections import namedtuple
from datetime import datetime, timedelta, timezone

import pytest

from consumer import parse_batch
from helpers import SAMPLE_EVENT
from parsing import parse_event

FALLBACK_TIME = datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)  # SAMPLE_EVENT time_us


def make_event(**record_changes):
    """A copy of the sample event with some record fields changed (None deletes a field)."""
    event = copy.deepcopy(SAMPLE_EVENT)
    for key, value in record_changes.items():
        if value is None:
            event["commit"]["record"].pop(key, None)
        else:
            event["commit"]["record"][key] = value
    return event


# --- a normal message ---------------------------------------------------------

def test_parse_sample_message():
    assert parse_event(SAMPLE_EVENT) == {
        "did": "did:plc:abc123",
        "rkey": "3mxwno5i2tk2y",
        "text": "Learning #Kafka and #AI today",
        "lang": "en",  # first of ["en", "fr"]
        "hashtags": ["kafka", "ai"],
        "created_at": datetime(2024, 5, 1, 12, 34, 56, 789000, tzinfo=timezone.utc),
    }


# --- events that are not new posts --------------------------------------------

@pytest.mark.parametrize("changes", [
    {"kind": "identity"},
    {"kind": "account"},
])
def test_non_commit_events_are_ignored(changes):
    event = copy.deepcopy(SAMPLE_EVENT)
    event.update(changes)
    assert parse_event(event) is None


@pytest.mark.parametrize("operation", ["delete", "update"])
def test_only_creates_are_kept(operation):
    event = copy.deepcopy(SAMPLE_EVENT)
    event["commit"]["operation"] = operation
    assert parse_event(event) is None


def test_other_collections_are_ignored():
    event = copy.deepcopy(SAMPLE_EVENT)
    event["commit"]["collection"] = "app.bsky.feed.like"
    assert parse_event(event) is None


# --- hashtags -----------------------------------------------------------------

def test_hashtags_are_lowercased_and_deduplicated():
    facet = lambda tag: {"features": [{"$type": "app.bsky.richtext.facet#tag", "tag": tag}]}
    event = make_event(facets=[facet("AI"), facet("ai"), facet("Python")])
    assert parse_event(event)["hashtags"] == ["ai", "python"]


def test_non_tag_facets_are_not_hashtags():
    event = make_event(facets=[
        {"features": [{"$type": "app.bsky.richtext.facet#mention", "did": "did:plc:x"}]},
        {"features": [{"$type": "app.bsky.richtext.facet#link", "uri": "https://example.com"}]},
        {"features": [{"$type": "app.bsky.richtext.facet#tag", "tag": "kept"}]},
    ])
    assert parse_event(event)["hashtags"] == ["kept"]


def test_hashtag_in_text_without_facet_is_not_extracted():
    # We only trust facets; "#nofacet" in the text alone doesn't count
    event = make_event(text="just text #nofacet", facets=None)
    assert parse_event(event)["hashtags"] == []


@pytest.mark.parametrize("facets", [None, [], [{}], [{"features": None}], [{"features": [{}]}]])
def test_missing_or_empty_facets_give_no_hashtags(facets):
    assert parse_event(make_event(facets=facets))["hashtags"] == []


# --- missing optional fields --------------------------------------------------

@pytest.mark.parametrize("langs", [None, []])
def test_missing_language_is_none(langs):
    assert parse_event(make_event(langs=langs))["lang"] is None


def test_missing_text_becomes_empty_string():
    assert parse_event(make_event(text=None))["text"] == ""


def test_nul_characters_are_stripped_from_text():
    # Postgres text columns reject NUL characters
    assert parse_event(make_event(text="bad\x00text"))["text"] == "badtext"


# --- created_at ---------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("2024-05-01T12:34:56Z", datetime(2024, 5, 1, 12, 34, 56, tzinfo=timezone.utc)),
    ("2024-05-01T12:34:56.7Z", datetime(2024, 5, 1, 12, 34, 56, 700000, tzinfo=timezone.utc)),
    ("2024-05-01T12:34:56.123456789Z", datetime(2024, 5, 1, 12, 34, 56, 123456, tzinfo=timezone.utc)),
    ("2024-05-01T14:34:56+02:00", datetime(2024, 5, 1, 12, 34, 56, tzinfo=timezone.utc)),
    ("2024-05-01T12:34:56", datetime(2024, 5, 1, 12, 34, 56, tzinfo=timezone.utc)),  # no timezone
])
def test_created_at_formats(value, expected):
    assert parse_event(make_event(createdAt=value))["created_at"] == expected


@pytest.mark.parametrize("bad_value", [
    None,                                   # missing
    "not a date",
    "",
    12345,                                  # wrong type
    "0001-01-01T00:00:00Z",                 # absurdly old
    "1970-01-01T00:00:00Z",
    (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),  # far in the future
])
def test_bad_created_at_falls_back_to_event_time(bad_value):
    assert parse_event(make_event(createdAt=bad_value))["created_at"] == FALLBACK_TIME


# --- malformed events: parse_event raises, the consumer skips them --------------

@pytest.mark.parametrize("break_event", [
    lambda e: e.pop("did"),
    lambda e: e.pop("commit"),
    lambda e: e["commit"].pop("rkey"),
    lambda e: e["commit"].pop("record"),
    lambda e: e.update(commit=None),
    lambda e: e.update(commit="oops"),
    lambda e: e["commit"].update(record=None),
    lambda e: e["commit"]["record"].update(facets="not a list"),
    lambda e: e["commit"]["record"].update(facets=[None]),
], ids=["no-did", "no-commit", "no-rkey", "no-record", "commit-none", "commit-str",
        "record-none", "facets-str", "facet-none"])
def test_malformed_event_raises(break_event):
    event = copy.deepcopy(SAMPLE_EVENT)
    break_event(event)
    with pytest.raises((KeyError, TypeError, AttributeError, ValueError)):
        parse_event(event)


# --- the consumer's batch parser: skips bad messages, never crashes -------------

Msg = namedtuple("Msg", "offset value")


def test_parse_batch_skips_malformed_and_counts_everything():
    import json

    delete = copy.deepcopy(SAMPLE_EVENT)
    delete["commit"]["operation"] = "delete"
    no_did = copy.deepcopy(SAMPLE_EVENT)
    no_did.pop("did")

    messages = [
        Msg(0, json.dumps(SAMPLE_EVENT).encode()),   # good
        Msg(1, b"this is not json"),                 # malformed: invalid JSON
        Msg(2, b"\xff\xfe"),                         # malformed: not even valid UTF-8
        Msg(3, json.dumps(delete).encode()),         # ignored: not a create
        Msg(4, json.dumps(no_did).encode()),         # malformed: missing did
        Msg(5, b"[1, 2, 3]"),                        # malformed: JSON but not an object
        Msg(6, json.dumps(SAMPLE_EVENT).encode()),   # good
    ]
    posts, ignored, malformed = parse_batch(messages)

    assert len(posts) == 2
    assert ignored == 1
    assert malformed == 4
