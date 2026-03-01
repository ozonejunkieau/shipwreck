---
name: shipwreck
description: Scan Docker image dependencies, generate reports, and query the dependency graph using the Shipwreck CLI. Use when the user wants to scan repos, generate HTML/Mermaid/JSON reports, check for stale images, or explore the dependency graph.
argument-hint: "[command] [options]"
---

# Shipwreck — Docker Image Dependency Scanner

You are helping the user operate the Shipwreck CLI tool, which scans Git repositories for Docker image references, builds a dependency graph, and generates interactive reports.

## Prerequisites

Ensure the project is installed before running any commands:

```bash
uv sync --all-extras
```

## Available Commands

### Quick start with bundled examples (no config needed)

```bash
# Scan example data and generate all reports
just examples
open examples/output/shipwreck.html
```

### Full pipeline

| Command | Purpose | Example |
|---------|---------|---------|
| `hunt`  | Scan repos, build dependency graph | `shipwreck hunt -c shipwreck.yaml` |
| `map`   | Generate reports (HTML, Mermaid, JSON) | `shipwreck map -c shipwreck.yaml --format html` |
| `dig`   | Query the graph from CLI | `shipwreck dig --stale` |
| `lookout` | Check registries for staleness | `shipwreck lookout -c shipwreck.yaml` |
| `log`   | Compare two snapshots | `shipwreck log --before snap1.json --after snap2.json` |
| `plunder` | Auto-discover repos from GitLab | `shipwreck plunder --url https://gitlab.example.com --group my-org` |
| `sail`  | Full pipeline (hunt + lookout + map) | `shipwreck sail -c shipwreck.yaml --yes` |

All commands are available via `just` shortcuts: `just hunt`, `just map`, `just dig`, etc.

### Common workflows

**Scan and report:**
```bash
shipwreck sail -c shipwreck.yaml -o .shipwreck/output
open .shipwreck/output/shipwreck.html
```

**Query the graph:**
```bash
shipwreck dig --stale              # list stale images
shipwreck dig --critical           # rank by criticality
shipwreck dig --uses postgres      # what depends on postgres?
shipwreck dig --used-by myapp      # what does myapp depend on?
shipwreck dig --classify external  # list external images
```

**CI/cron pipeline with snapshot diffing:**
```bash
shipwreck sail -c shipwreck.yaml --snapshot --diff-from-latest --yes
```

## Configuration

If the user doesn't have a config file yet, help them create one. The minimal config is:

```yaml
repositories:
  - url: git@github.com:org/repo.git
    ref: main
  - path: /local/path/to/repo
    name: local-project
```

See `examples/shipwreck.yaml` for every available option (registries, version schemes, classification rules, aliases, ansible resolution, etc.).

Use `examples/shipwreck-minimal.yaml` as a starting template.

## When scanning the user's arguments ($ARGUMENTS)

- If `$ARGUMENTS` is empty, ask what they want to do (scan, query, report, or set up config)
- If `$ARGUMENTS` contains a command name (hunt, map, dig, sail, etc.), run that command
- If `$ARGUMENTS` mentions a config file or repo URL, use it appropriately
- If `$ARGUMENTS` says "examples" or "demo", run `just examples` and open the HTML report
- Always use `uv run shipwreck` or `just` shortcuts to run commands
- After generating an HTML report, tell the user where to find it and offer to open it
