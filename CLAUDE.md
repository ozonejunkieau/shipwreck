# Shipwreck — Claude Code Instructions

Docker image dependency graph CLI tool. Scans Git repositories for Docker image references, builds a dependency graph, and generates interactive HTML reports, Mermaid diagrams, and JSON.

## Setup

```bash
uv sync --all-extras
```

## Development Commands

Use `just` for all development tasks:

```bash
just test        # run all tests (612 tests)
just test-unit   # unit tests only
just test-int    # integration tests only
just lint        # ruff check
just fmt         # ruff format
just check       # basedpyright type check
just coverage    # pytest with coverage report
just all         # lint + check + test
just examples    # generate example output from examples/ directory
```

## Project Layout

```
src/shipwreck/
├── cli.py              # Typer commands (hunt, map, dig, lookout, log, plunder, sail)
├── config.py           # Pydantic config models
├── models.py           # Graph, GraphNode, GraphEdge, ImageReference
├── scanner.py          # Orchestrator: clone + parse + build graph
├── parsers/            # dockerfile, bake, compose, ansible, gitlab_ci, github_actions, fallback
├── registry/           # Registry HTTP v2 client, staleness detection, version schemes
├── resolution/         # Ansible playbook generation, env var and bake/compose variable resolution
├── graph/              # Builder, alias resolution, classifier, criticality scoring
├── discovery/          # GitLab group discovery
├── output/             # HTML (Jinja2), Mermaid, JSON export, snapshot diff
└── query/              # Query engine backing the dig command
```

## Key Conventions

- Python 3.12+, src layout, hatchling build backend
- Tests in `tests/unit/` and `tests/integration/`
- Fixtures in `tests/fixtures/` (bake fixtures must be named `docker-bake.hcl`)
- Use Conventional Commits format for git messages
- Never `git add .` — stage specific files
- Run `just all` before committing to catch lint, type, and test issues

## Custom Skill

The `/shipwreck` slash command (`.claude/skills/shipwreck/SKILL.md`) helps users operate the CLI — scanning repos, generating reports, querying the graph.
