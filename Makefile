.DEFAULT_GOAL := help
ifeq ($(OS),Windows_NT)
PYTHON ?= python
else
PYTHON ?= python3
endif
SERVICES ?= all
PLATFORMS ?=
TARGET ?= toolbox
PLATFORM_URL ?= http://127.0.0.1:51760
INPUT ?=
RUN ?=
IMAGE ?=
.PHONY: help init install sources build build-multi pull up storage restart down dev stop prepare test check smoke acceptance ps logs status config platform-check platform-operations debug-check image-size image-export dub-config dub-migrate dub-run dub-list doctor context
help:
	@echo make init / pull / up / dev
	@echo make sources / build / build-multi
	@echo make up SERVICES=all / make up SERVICES=redis / storage / restart / down
	@echo make dev / stop / status / logs / check / smoke / test / acceptance / platform-check
	@echo make image-size / image-export
init install pull restart down dev stop prepare test check smoke acceptance ps logs status config storage:
	$(PYTHON) scripts/manage.py $@
up:
	$(PYTHON) scripts/manage.py up --services "$(SERVICES)"
sources:
	git -c core.longpaths=true submodule update --init --recursive --depth 1
build:
	$(PYTHON) scripts/manage.py build $(if $(PLATFORMS),--platforms "$(PLATFORMS)",) --target "$(TARGET)" $(if $(IMAGE),--image "$(IMAGE)",)
build-multi:
	$(PYTHON) scripts/manage.py build --platforms linux/amd64,linux/arm64 $(if $(IMAGE),--image "$(IMAGE)",) --push
doctor:
	$(PYTHON) scripts/doctor.py
context:
	$(PYTHON) scripts/doctor.py --context
platform-check:
	uv run --project business --frozen python scripts/check_platforms.py --url "$(PLATFORM_URL)" --require-ready
platform-operations:
	uv run --project business --frozen python scripts/check_platform_operations.py
debug-check:
	uv run --project business --frozen python scripts/check_debug.py
image-size:
	$(PYTHON) scripts/manage.py image-size $(if $(IMAGE),--image "$(IMAGE)",)
image-export:
	uv run --project business --frozen python scripts/image_export.py $(if $(IMAGE),--image "$(IMAGE)",)
dub-config:
	uv run --project business --frozen python scripts/dubbing.py config
dub-migrate:
	uv run --project business --frozen python scripts/dubbing.py migrate
dub-run:
	uv run --project business --frozen python scripts/dubbing.py run "$(INPUT)"
dub-list:
	uv run --project business --frozen python scripts/dubbing.py list
