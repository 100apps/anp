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
IMAGE ?=
NATIVE_TARGET ?= $(shell $(PYTHON) -c "import platform; print(('macos' if platform.system() == 'Darwin' else platform.system().lower()) + '-' + ('amd64' if platform.system() == 'Linux' and platform.machine() == 'x86_64' else platform.machine()))")
.PHONY: help init install sources build dist docker-build native-sources native-check build-multi pull up storage restart down dev stop prepare test check smoke acceptance ps logs status config platform-check platform-operations realtime-check langfuse-trace-check debug-check image-size image-export doctor context
help:
	@echo make build / native-check / dist
	@echo make native-sources
	@echo make init / pull / up / dev
	@echo make sources / docker-build / build-multi
	@echo make up SERVICES=all / make up SERVICES=redis / storage / restart / down
	@echo make dev / stop / status / logs / check / smoke / test / acceptance / platform-check
	@echo make image-size / image-export
init install pull restart down dev stop prepare test check smoke acceptance ps logs status config storage:
	$(PYTHON) scripts/manage.py $@
up:
	$(PYTHON) scripts/manage.py up --services "$(SERVICES)"
sources:
	git -c core.longpaths=true submodule update --init --recursive --depth 1
build: native-sources
	$(PYTHON) native/build.py build --target "$(NATIVE_TARGET)" --profile "$(or $(NATIVE_PROFILE),platform)"
dist:
	$(PYTHON) native/build.py dist --target "$(NATIVE_TARGET)"
docker-build:
	$(PYTHON) scripts/manage.py build $(if $(PLATFORMS),--platforms "$(PLATFORMS)",) --target "$(TARGET)" $(if $(IMAGE),--image "$(IMAGE)",)
native-sources:
	git submodule update --init --depth 1 $(if $(filter linux-amd64,$(NATIVE_TARGET)),platform/sources/components/auth platform/sources/langfuse,platform/sources/components/redis platform/sources/components/auth platform/sources/components/dbx platform/sources/langfuse)
native-check:
	$(PYTHON) $(if $(filter linux-amd64,$(NATIVE_TARGET)),native/linux_check.py,native/check.py)
build-multi:
	$(PYTHON) scripts/manage.py build --platforms linux/amd64,linux/arm64 $(if $(IMAGE),--image "$(IMAGE)",) --push
doctor:
	$(PYTHON) scripts/doctor.py
context:
	$(PYTHON) scripts/doctor.py --context
platform-check:
	uv run --project business --frozen python scripts/check_platforms.py --url "$(PLATFORM_URL)" --require-ready
langfuse-trace-check:
	uv run --project business --frozen python scripts/check_langfuse_trace.py
realtime-check:
	uv run --project business --frozen python scripts/check_realtime.py
platform-operations:
	uv run --project business --frozen python scripts/check_platform_operations.py
debug-check:
	uv run --project business --frozen python scripts/check_debug.py
image-size:
	$(PYTHON) scripts/manage.py image-size $(if $(IMAGE),--image "$(IMAGE)",)
image-export:
	uv run --project business --frozen python scripts/image_export.py $(if $(IMAGE),--image "$(IMAGE)",)
