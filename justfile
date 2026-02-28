# Default recipe
default: all

# Install dependencies
install:
    uv sync --all-extras

# Run all tests
test:
    uv run pytest

# Run unit tests only
test-unit:
    uv run pytest tests/unit

# Run integration tests only
test-int:
    uv run pytest tests/integration

# Lint with ruff
lint:
    uv run ruff check src tests

# Format with ruff
fmt:
    uv run ruff format src tests

# Type check
check:
    uv run basedpyright

# Coverage report
coverage:
    uv run pytest --cov=shipwreck --cov-report=term-missing --cov-report=html

# Run everything (lint + check + test)
all: lint check test

# Run shipwreck CLI
run *ARGS:
    uv run shipwreck {{ARGS}}

# CLI command shortcuts
hunt *ARGS:
    uv run shipwreck hunt {{ARGS}}

map *ARGS:
    uv run shipwreck map {{ARGS}}

dig *ARGS:
    uv run shipwreck dig {{ARGS}}

lookout *ARGS:
    uv run shipwreck lookout {{ARGS}}

log *ARGS:
    uv run shipwreck log {{ARGS}}

plunder *ARGS:
    uv run shipwreck plunder {{ARGS}}

sail *ARGS:
    uv run shipwreck sail {{ARGS}}
