.DEFAULT_GOAL := help
PYTHON ?= python
COMPONENT ?=
PLATFORM_URL ?= http://127.0.0.1:20240
INPUT ?=
RUN ?=
.PHONY: help init install prepare storage up down ps logs config build build-platform platform test smoke check dev stop frontend bootstrap test-integration
help:
	@echo make init / install / storage / dev / stop / build / up / down
	@echo make test / smoke / smoke-docker / test-integration / check / ps / logs / config
	@echo make build-platform COMPONENT=langfuse-web
	@echo make platform-check PLATFORM_URL=http://127.0.0.1:52024
	@echo make bootstrap / platform / build-production / up-production
	@echo make dub-config / dub-migrate / dub-worker / dub-run INPUT=... / dub-retry RUN=... / dub-list / dub-test
init install prepare storage up down ps logs config build build-production up-production platform test smoke smoke-docker check dev stop frontend bootstrap test-integration:
	$(PYTHON) scripts/manage.py $@
build-platform:
	$(PYTHON) scripts/manage.py build-platform $(COMPONENT)

.PHONY: platform-check
platform-check:
	uv run --project business --frozen python scripts/check_platforms.py --url "$(PLATFORM_URL)" --require-ready

.PHONY: dub-config dub-migrate dub-run dub-retry dub-list dub-show dub-test dub-worker dub-check
dub-config dub-migrate dub-list dub-worker:
	uv run --project business --frozen python scripts/dubbing.py $(subst dub-,,$@)
dub-run:
	uv run --project business --frozen python scripts/dubbing.py run "$(INPUT)"
dub-retry:
	uv run --project business --frozen python scripts/dubbing.py retry "$(RUN)"
dub-show:
	uv run --project business --frozen python scripts/dubbing.py show "$(RUN)"
dub-test:
	uv run --project business --frozen pytest business/tests/test_dubbing.py -q
dub-check:
	uv run --project business --frozen python scripts/dubbing_http_check.py "$(RUN)"
