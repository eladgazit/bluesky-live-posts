"""Reads post events from Bluesky Jetstream and sends each one to Kafka."""
import asyncio
import json
import logging
import signal

import websockets
from kafka import KafkaProducer

from config import JETSTREAM_URL, KAFKA_BOOTSTRAP, KAFKA_TOPIC

LOG_EVERY = 100
MAX_BACKOFF_SECONDS = 30

log = logging.getLogger("producer")


async def stream_to_kafka(producer: KafkaProducer) -> int:
    """Forward messages until the connection drops. Returns the count sent."""
    sent = 0
    async with websockets.connect(JETSTREAM_URL) as ws:
        log.info("Connected to Jetstream")
        async for message in ws:
            event = json.loads(message)
            # Key by DID so one author's posts always land in the same partition
            key = event.get("did", "").encode()
            producer.send(KAFKA_TOPIC, key=key, value=message.encode())
            sent += 1
            if sent % LOG_EVERY == 0:
                log.info("Sent %d messages (this connection)", sent)
    return sent


async def run(producer: KafkaProducer) -> None:
    backoff = 1
    while True:
        try:
            await stream_to_kafka(producer)
            log.warning("Jetstream closed the connection")
            backoff = 1
        except (websockets.WebSocketException, OSError) as exc:
            log.warning("Connection lost: %r", exc)
        log.info("Reconnecting in %ds", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    producer = KafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP)
    # Treat SIGTERM like Ctrl-C so we always flush before exiting
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        asyncio.run(run(producer))
    except KeyboardInterrupt:
        log.info("Stopping")
    finally:
        producer.flush()
        producer.close()


if __name__ == "__main__":
    main()
