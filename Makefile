VENV := .venv
BIN := $(VENV)/bin

.PHONY: install test

$(BIN)/activate: requirements-dev.txt
	python3.12 -m venv $(VENV)
	$(BIN)/pip install -r requirements-dev.txt
	touch $(BIN)/activate

install: $(BIN)/activate

test: install
	$(BIN)/pytest --cov=image_service --cov-report=term-missing --cov-fail-under=90
