.PHONY: help dev check lint type test test-rm cov clean

help:  ## list targets
	@grep -hE '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-9s %s\n", $$1, $$2}'

dev:  ## install pinned dependencies and the package in editable mode
	pip install -r requirements-dev.lock
	pip install -e . --no-deps

check: lint type test  ## everything CI runs

lint:  ## ruff
	ruff check src tests

type:  ## mypy
	mypy

test:  ## unit and integration tests (no network; needs git)
	pytest

test-rm:  ## also run against the real RefactoringMiner ($$DDE_REFACTORINGMINER)
	pytest -m refactoringminer

cov:  ## tests with coverage
	pytest --cov=dde --cov-report=term-missing

clean:  ## remove caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage build dist src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
