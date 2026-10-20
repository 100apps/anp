.DEFAULT_GOAL := help
PYTHON ?= python
SERVICES ?= all
COMPONENT ?=
PLATFORM_URL ?= http://127.0.0.1:51760
INPUT ?=
RUN ?=
IMAGE ?= anp3/toolbox:local
.PHONY: help init install build build-components up storage restart down dev stop prepare test check smoke acceptance ps logs status config platform-check platform-operations debug-check image-size image-export dub-config dub-migrate dub-run dub-list
help:
	@echo make init / install / build-components COMPONENT=... / build
	@echo make up SERVICES=all / make up SERVICES=redis / storage / restart / down
	@echo make dev / stop / status / logs / check / smoke / test / acceptance / platform-check
	@echo make image-size / image-export
init install build restart down dev stop prepare test check smoke acceptance ps logs status config storage:
	$(PYTHON) scripts/manage.py $@
up:
	$(PYTHON) scripts/manage.py up --services "$(SERVICES)"
build-components:
	$(PYTHON) scripts/manage.py build-components $(COMPONENT)
platform-check:
	uv run --project business --frozen python scripts/check_platforms.py --url "$(PLATFORM_URL)" --require-ready
platform-operations:
	uv run --project business --frozen python scripts/check_platform_operations.py
debug-check:
	uv run --project business --frozen python scripts/check_debug.py
image-size:
	docker image ls --filter reference="$(IMAGE)"
image-export:
	uv run --project business --frozen python scripts/image_export.py --image "$(IMAGE)"
dub-config:
	uv run --project business --frozen python scripts/dubbing.py config
dub-migrate:
	uv run --project business --frozen python scripts/dubbing.py migrate
dub-run:
	uv run --project business --frozen python scripts/dubbing.py run "$(INPUT)"
dub-list:
	uv run --project business --frozen python scripts/dubbing.py list
