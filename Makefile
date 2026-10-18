.PHONY: help run up topic test eval down shutdown

PY := .venv/bin/python
TOPIC := bluesky-posts

help:
	@echo "make run        start everything (Docker, topic, consumer, producer, dashboard)"
	@echo "make test       run the test suite (starts Docker if needed)"
	@echo "make eval       check data quality of the posts table (exit 1 if any check fails)"
	@echo "make down       stop Docker containers only"
	@echo "make shutdown   stop everything: Python apps and Docker containers (posts are kept)"

# Creates the virtualenv and installs dependencies; reruns only when requirements.txt changes
.venv/.installed: requirements.txt
	python3 -m venv .venv
	$(PY) -m pip install -q -r requirements.txt
	@touch $@

up:
	docker compose up -d --wait

topic: up
	docker exec bluesky-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 \
		--create --if-not-exists --topic $(TOPIC) --partitions 1 --replication-factor 1

run: .venv/.installed topic
	@./scripts/run.sh

test: .venv/.installed up
	$(PY) -m pytest

eval: .venv/.installed up
	cd src && ../$(PY) evals.py

down:
	docker compose down

shutdown:
	@./scripts/shutdown.sh
