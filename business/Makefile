.DEFAULT_GOAL := help
.PHONY: help install build start test
help:
	@echo 'make install / build / start / test'
install:
	uv sync --frozen
	pnpm --dir ui install --frozen-lockfile
build:
	pnpm --dir ui build
start:
	uv run --frozen python -m anp_business
test:
	uv run --frozen pytest tests -q
