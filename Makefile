VENV := .venv
BIN := $(VENV)/bin
# samlocal shells out to `sam`, so put the venv first on PATH.
export PATH := $(CURDIR)/$(BIN):$(PATH)


$(BIN)/activate: requirements-dev.txt
	python3.12 -m venv $(VENV)
	$(BIN)/pip install -r requirements-dev.txt
	touch $(BIN)/activate

install: $(BIN)/activate

test: install
	$(BIN)/pytest --cov=image_service --cov-report=term-missing --cov-fail-under=90

ENV ?= local
LOCAL_AWS := AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1

.PHONY: install test up down validate deploy smoke

up:
	@test -n "$$LOCALSTACK_AUTH_TOKEN" || (echo "export LOCALSTACK_AUTH_TOKEN first" && exit 1)
	docker compose up -d --wait

down:
	docker compose down

validate: install
	sam validate --lint --region us-east-1

deploy: install
ifeq ($(ENV),local)
	$(LOCAL_AWS) samlocal build
	$(LOCAL_AWS) samlocal deploy --config-env local
else
	@test -n "$(ALLOWED_ORIGIN)" || (echo "ALLOWED_ORIGIN is required for ENV=$(ENV)" && exit 1)
	sam build
	sam deploy --config-env $(ENV) --parameter-overrides AllowedOrigin=$(ALLOWED_ORIGIN)
endif

smoke: install
	$(LOCAL_AWS) $(BIN)/python scripts/smoke.py
