.PHONY: install db generate init etl test test-sql status benchmark dashboard all

PYTHON ?= python
SCALE ?= 1.0

install:  ## install Python dependencies
	$(PYTHON) -m pip install -r requirements-dev.txt

db:  ## start PostgreSQL in Docker
	docker compose up -d --wait postgres

generate:  ## write 36 months of synthetic source files to data/raw (SCALE=1.0 is about 1.5M transactions)
	$(PYTHON) -m data_generator.generate_data --scale $(SCALE) --out data/raw

init:  ## create or update the warehouse schema
	$(PYTHON) -m etl.run_pipeline init

etl:  ## load every batch that is not loaded yet
	$(PYTHON) -m etl.run_pipeline run

test:  ## lint and run the Python tests (integration tests need FINSIGHT_TEST_DATABASE_URL)
	ruff check .
	pytest

test-sql:  ## run the SQL assertions against the loaded warehouse
	$(PYTHON) -m etl.run_pipeline test

status:  ## what is loaded, what was rejected
	$(PYTHON) -m etl.run_pipeline status

benchmark:  ## time key queries with and without their index or mart
	$(PYTHON) -m etl.benchmark

dashboard:  ## build the web preview at site/index.html
	$(PYTHON) -m dashboard.build --out site/index.html

all: generate etl test-sql dashboard  ## the whole flow
