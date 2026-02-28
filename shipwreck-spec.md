# Shipwreck 🏴‍☠️

**Mapping the buried treasure in your container stack.**

A standalone CLI tool that analyses git repositories, discovers all Docker image references, resolves their dependency graph, and produces beautiful interactive HTML reports, markdown-embeddable Mermaid diagrams, and AI-parseable metadata — with version tracking, staleness detection, and snapshot diffing.

---

## 1. Core Concepts

### 1.1 Image Node

Every unique image reference discovered across all repos becomes a node. Nodes are deduplicated by canonical name (registry/namespace/image) and carry metadata about where they're defined, where they're consumed, what tags/versions exist, and registry metadata (size, build date) when available.

### 1.2 Edge Types

| Edge Type | Meaning | Report Label | Example |
|-----------|---------|-------------|---------|
| `builds_from` | Dockerfile `FROM` or bake `contexts` | builds_from | `myapp:latest` depends on `python:3.12-slim` |
| `produces` | A build file outputs this image | produces | `docker-bake.hcl` produces `myapp:0.2.0` |
| `consumes` | A deployment/compose/ansible/CI references this image | requires | `docker-compose.yml` requires `postgres:16` |

In the interactive HTML report, `consumes` edges are displayed with reversed direction and labelled "requires" for readability. A visual `variant_of` edge style (purple dashed) is used for alias/variant relationships derived from `produces` edges.

### 1.3 Image Classification

Images are classified by how and where they appear:

| Class | Heuristic | Visual Style |
|-------|-----------|-------------|
| **base** | Internal base images, built from external sources | Grey badge |
| **application** | Application images that are built and deployed | Blue badge |
| **middleware** | Infrastructure services (databases, caches, queues) | Purple badge |
| **utility** | Operational tooling and helpers | Green badge |
| **external** | Third-party images from public registries | Orange badge, dashed border |
| **test** | Test and CI-only images | Amber badge, dashed border |
| **unknown** | Unclassified | Dim badge |

Classification is overridable via config (path/image pattern → class mapping).

### 1.4 Criticality Score

Computed per-node as a weighted fan-out:

```
criticality = direct_dependents + (0.5 * transitive_dependents)
```

Displayed as a heat indicator on each node (colour intensity or badge). Higher score = more things break if this image has issues.

### 1.5 Image Aliases & Variants

Container images that are conceptually the same but exist as different tags (e.g. post-build flattening/optimisation: `thingy:1` → `thingy:1-clean`) are handled via alias rules. Aliased images resolve to a single canonical node with variants shown as metadata. This prevents graph explosion from build pipeline transformations.

```yaml
aliases:
  # Regex-based: capture groups map to canonical form
  - pattern: "^(.+):(.+)-clean$"
    canonical: "{1}:{2}"
    variant: "optimised"

  # Simple suffix stripping
  - pattern: "^(.+):(.+)-flat$"
    canonical: "{1}:{2}"
    variant: "flattened"

  # Explicit mapping
  - from: "registry.example.com/releases/myapp"
    canonical: "registry.example.com/builds/myapp"
    variant: "release"
```

On the node, variants appear as additional port labels with a distinct visual indicator (e.g. a small badge or colour shift) showing they're derived from the same logical image.

### 1.6 Version Schemes

Not all images use semver. Shipwreck supports configurable version ordering per registry/image pattern:

```yaml
version_schemes:
  # Default: semver comparison
  - image_pattern: "*"
    type: semver

  # Wolfi images tagged with linux epoch timestamps
  - image_pattern: "registry.example.com/wolfi/*"
    type: numeric
    # Treats tags as plain numbers, higher = newer

  # Date-based tags (YYYYMMDD or YYYY-MM-DD)
  - image_pattern: "registry.example.com/snapshots/*"
    type: date
    format: "%Y%m%d"

  # Regex extraction: pull a comparable value from a complex tag
  - image_pattern: "registry.example.com/custom/*"
    type: regex
    extract: "^v?(\\d+\\.\\d+\\.\\d+)"  # extract semver from tags like "v1.2.3-alpine"
    compare: semver  # then compare extracted value as semver
```

| Scheme | Ordering Logic | Example Tags |
|--------|---------------|--------------|
| `semver` | Standard semver comparison | `1.2.3`, `0.1.0-rc1` |
| `numeric` | Integer/float comparison | `1709251200`, `42` |
| `date` | Parsed date comparison | `20250227`, `2025-02-27` |
| `regex` | Extract + compare with another scheme | `v1.2.3-alpine` → `1.2.3` → semver |

When no scheme matches or parsing fails, the tag is treated as opaque and staleness is `unknown`.

---

## 2. Input Sources & Parsers

> **Note:** Detailed parser specifications including file patterns, extraction rules, edge cases, and test expectations are in the companion document `PARSERS.md`.

### 2.1 Repository Discovery

Repos are defined in a YAML config file:

```yaml
# shipwreck.yaml
registries:
  - name: internal
    url: registry.example.com
    auth_env: REGISTRY_AUTH_TOKEN
    # Mark as internal — never query external registries for these images without approval
    internal: true

  - name: dockerhub
    url: docker.io
    internal: false

# Control external registry behaviour
registry_policy:
  # Prompt before querying registries not marked as internal
  prompt_external: true
  # Or whitelist specific external registries
  external_allowlist:
    - docker.io
    - ghcr.io

repositories:
  - url: git@gitlab.example.com:infra/base-images.git
    ref: main
    name: base-images

  - url: git@gitlab.example.com:apps/myapp.git
    ref: main

  - path: /local/path/to/repo
    name: local-project

# Auto-discover repos from a GitLab group
discovery:
  - type: gitlab_group
    url: https://gitlab.example.com
    group: my-org/containers
    auth_env: GITLAB_TOKEN
    include_subgroups: true
    include_pattern: "^(infra|apps)/.*"
    exclude_pattern: ".*-archive$"

# Ansible variable resolution — uses real ansible for evaluation
ansible:
  inventory: /path/to/inventory
  # Optional: vault password file for encrypted vars
  vault_password_file: /path/to/.vault_pass
  # Optional: limit to specific host/group for evaluation context
  limit: production

# Resolve image tags from current environment variables
resolve_env_vars: true  # default: false

# Image aliases for build pipeline transforms
aliases:
  - pattern: "^(.+):(.+)-clean$"
    canonical: "{1}:{2}"
    variant: "optimised"
  - pattern: "^(.+):(.+)-flat$"
    canonical: "{1}:{2}"
    variant: "flattened"

# Version comparison schemes
version_schemes:
  - image_pattern: "registry.example.com/wolfi/*"
    type: numeric
  - image_pattern: "*"
    type: semver

# Image classification overrides
classification:
  rules:
    - path_pattern: "**/ansible/**"
      class: product
    - path_pattern: "**/test/**"
      class: test
    - path_pattern: "**/.gitlab-ci*"
      class: test
    - path_pattern: "**/.github/workflows/**"
      class: test
    - image_pattern: "registry.example.com/base/*"
      class: base
```

### 2.2 Parser Summary

| Parser | Files | Relationship Types |
|--------|-------|-------------------|
| Dockerfile | `Dockerfile`, `Dockerfile.*`, `*.dockerfile` | `builds_from` |
| Docker Bake | `docker-bake.hcl`, `docker-bake.override.hcl` | `produces`, `builds_from` |
| Docker Compose | `compose.yml`, `docker-compose.yml` + variants | `consumes` |
| Ansible | `*.yml`/`*.yaml` in ansible structures | `consumes` |
| GitLab CI | `.gitlab-ci.yml`, includes | `consumes`, `produces` (best-effort) |
| GitHub Actions | `.github/workflows/*.yml` | `consumes`, `produces` (best-effort) |
| Fallback | Any YAML with `image:`, any file with `FROM` | `consumes` / `builds_from` (heuristic) |

Full parser specifications are in `PARSERS.md`.

### 2.3 Ansible Variable Resolution Strategy

Simple Jinja2 substitution is insufficient for real Ansible environments that use `lookup()` functions, complex filters, and layered variable precedence. Shipwreck uses **Ansible itself** to resolve image references.

#### Approach

1. **Discovery pass:** Parsers scan Ansible files and extract raw `image:` field values (including unresolved Jinja2 templates)
2. **Template generation:** Shipwreck generates a minimal Ansible playbook that outputs all discovered image templates:
   ```yaml
   # Auto-generated by shipwreck
   - hosts: localhost
     gather_facts: false
     tasks:
       - name: Resolve image references
         debug:
           msg: "SHIPWRECK_RESOLVE|{{ item.id }}|{{ item.template }}"
         loop:
           - { id: "ref_001", template: "{{ app_registry }}/{{ app_image }}:{{ app_version }}" }
           - { id: "ref_002", template: "{{ lookup('file', '/opt/versions/db.txt') }}" }
           # ... one entry per discovered image template
   ```
3. **Ansible evaluation:** Run `ansible-playbook` against the configured inventory:
   ```bash
   ansible-playbook /tmp/shipwreck_resolve.yml \
       -i /path/to/inventory \
       --limit production \
       [--vault-password-file /path/to/.vault_pass]
   ```
4. **Output parsing:** Parse the `SHIPWRECK_RESOLVE|ref_id|resolved_value` lines from stdout
5. **Merge:** Update the graph nodes with resolved image references

This approach handles:
- `lookup('file', ...)`, `lookup('env', ...)`, `lookup('pipe', ...)` etc.
- Complex Jinja2 filters and conditionals
- Layered variable precedence (inventory → group_vars → host_vars)
- Vault-encrypted variables (when vault password provided)
- Any custom Ansible plugins the user has installed

#### Fallback

If `ansible-playbook` is not available or the inventory is not configured, Shipwreck falls back to simple `{{ var }}` substitution from any flat YAML files it can find in the repo, and flags unresolved references.

### 2.4 Environment Variable Resolution

When `resolve_env_vars: true` is set in config, Shipwreck inspects the current process environment to resolve:

- Docker Compose `${VARIABLE}` and `${VARIABLE:-default}` syntax
- GitLab CI `$VARIABLE` references
- GitHub Actions `${{ env.VARIABLE }}` references
- Dockerfile `ARG` values that reference environment variables

This is opt-in because environment context matters — the same config may resolve differently in CI vs local dev. The resolved values are recorded in metadata so it's clear what environment was used.

---

## 3. Registry Integration

### 3.1 Registry Policy & Approval

To prevent accidental leakage of internal image names to external registries:

- Registries marked `internal: true` in config are always queried without prompting
- For images that would be resolved against registries **not** in the config (e.g. an image with no explicit registry defaults to Docker Hub), Shipwreck checks `registry_policy`:
  - If `prompt_external: true` — interactive prompt listing the image names and target registry, requiring `y/n` approval
  - If `external_allowlist` is set — only those registries are queried without prompting
  - In non-interactive mode (CI), external queries are **skipped** unless explicitly allow-listed, and a warning is emitted

### 3.2 Tag Resolution & Metadata

For each discovered image, query the registry (Docker Registry HTTP API v2) to determine:

- Whether the referenced tag exists
- What the latest available tag is (using the configured version scheme)
- **Image size** (compressed, from manifest)
- **Build date** (from image config `created` field)
- **Digest** (for exact version pinning visibility)

This metadata is stored on the node and displayed in tooltips/reports.

### 3.3 Staleness Detection

Compare referenced version against available tags using the appropriate version scheme:

| Status | Condition | Visual |
|--------|-----------|--------|
| **Current** | Referenced tag == latest per scheme | Green badge |
| **Behind** | Behind latest, same major (semver) or within threshold | Amber badge + delta |
| **Major behind** | Different major (semver) or beyond threshold | Red badge + delta |
| **Unknown** | Can't determine (no matching scheme, unresolved var) | Grey badge |

---

## 4. Output Formats

### 4.1 Interactive HTML Report

A single self-contained `.html` file (no external dependencies) generated via Jinja2 with embedded CSS/JS.

#### Layout

- **Engine:** Dagre (horizontal LR), rendered via dagre-d3 or elkjs + d3
- **Grouping:** Nodes grouped into labelled boxes by source repository
- **Node design:**
  - Rounded rectangle with image name as title
  - Tag/version as "port" labels on right edge (Altium schematic port style)
  - Multiple tags on same image = stacked ports on same node
  - Alias variants shown as secondary ports with distinct visual (dimmed, italic)
  - Border style encodes classification (solid/dashed/dotted per §1.3)
  - Criticality as border colour intensity or heat badge
  - Staleness badge (green/amber/red/grey)
  - Image size + build date in tooltip
- **Edges:**
  - `builds_from`: solid arrow
  - `produces`: thick arrow
  - `consumes`: dashed arrow
  - Colour-coded by type
- **Interactions:**
  - Click node → highlight full dependency path (upstream + downstream)
  - Hover → tooltip with metadata (source file, line, resolved vars, staleness, size, date, digest)
  - Search/filter bar: filter by image name, repo, classification, staleness
  - Toggle: show/hide external base images
  - Toggle: show/hide test/CI-only images
  - Zoom + pan (d3-zoom)

#### Theme

- Dark mode primary (with light mode toggle)
- Modern colour palette — Vercel/Linear aesthetic
- Subtle grid background, smooth animations
- Clean sans-serif typography (Inter or system font stack)

### 4.2 Mermaid Diagram (Markdown-Embeddable)

For embedding in docs, READMEs, and wikis. Renders natively in GitLab/GitHub markdown.

Generated output includes:
- Full dependency graph as `.mermaid` file
- Per-repository subgraph variant (for each repo's own README)
- Staleness indicators as emoji badges
- Classification encoded via Mermaid `classDef` styles

Mermaid has layout limitations vs the HTML report — no ports, reduced interactivity. Intentionally simplified; the HTML report is the primary rich view.

### 4.3 JSON Metadata

Structured JSON for machine consumption — AI agents, MCP tools, CI pipelines.

```json
{
  "$schema": "https://shipwreck.dev/schema/v1.json",
  "version": "1",
  "generated_at": "2025-02-27T10:00:00Z",
  "config_hash": "sha256:abc...",
  "environment": {
    "resolved_env_vars": ["CI_REGISTRY_IMAGE", "APP_VERSION"],
    "ansible_inventory": "/path/to/inventory",
    "ansible_limit": "production"
  },
  "nodes": [
    {
      "id": "registry.example.com/myapp",
      "canonical": "registry.example.com/myapp",
      "tags_referenced": ["0.1.1", "0.2.0", "latest"],
      "latest_available": "0.2.0",
      "staleness": "behind",
      "version_scheme": "semver",
      "classification": "product",
      "criticality": 4.5,
      "registry_metadata": {
        "size_bytes": 52428800,
        "build_date": "2025-02-15T08:30:00Z",
        "digest": "sha256:abc123..."
      },
      "variants": [
        { "tag_suffix": "-clean", "variant_type": "optimised" }
      ],
      "sources": [
        {
          "repo": "base-images",
          "file": "docker-bake.hcl",
          "line": 12,
          "relationship": "produces",
          "tag": "0.2.0"
        },
        {
          "repo": "myapp-deploy",
          "file": "ansible/roles/app/tasks/main.yml",
          "line": 8,
          "relationship": "consumes",
          "tag": "0.1.1",
          "resolution": {
            "method": "ansible",
            "variables_resolved": { "app_version": "0.1.1" }
          }
        }
      ]
    }
  ],
  "edges": [...],
  "summary": {
    "total_images": 42,
    "stale_images": 7,
    "unresolved_references": 2,
    "classification_counts": { "base": 4, "application": 12, "middleware": 6, "utility": 4, "external": 8, "test": 4, "unknown": 2 }
  }
}
```

### 4.4 Snapshot Diff

```json
{
  "previous": "2025-02-20T10:00:00Z",
  "current": "2025-02-27T10:00:00Z",
  "changes": {
    "added_images": [],
    "removed_images": [],
    "version_changes": [
      {
        "image": "registry.example.com/myapp",
        "previous_tags": ["0.1.0"],
        "current_tags": ["0.1.1"],
        "consumers_affected": []
      }
    ],
    "staleness_changes": [
      { "image": "postgres", "previous": "current", "current": "behind" }
    ],
    "metadata_changes": [
      { "image": "registry.example.com/myapp", "field": "size_bytes", "previous": 50000000, "current": 52428800 }
    ]
  }
}
```

---

## 5. CLI Interface

All subcommands follow a pirate theme.

```
🏴‍☠️ shipwreck — Mapping the buried treasure in your container stack.

Usage:
  shipwreck hunt [OPTIONS]        Scan repos and discover all image references
  shipwreck map [OPTIONS]         Generate the dependency report (HTML, Mermaid, JSON)
  shipwreck dig [OPTIONS]         Query the metadata ("dig up" specific info)
  shipwreck lookout [OPTIONS]     Check registries for staleness / updates
  shipwreck plunder [OPTIONS]     Auto-discover repos from a GitLab group
  shipwreck log [OPTIONS]         Compare snapshots (the "captain's log")
  shipwreck sail [OPTIONS]        Full pipeline: hunt + lookout + map (cron/CI-friendly)

Commands:

  hunt — "Scour the seas for containers"
    -c, --config PATH             Config file (default: shipwreck.yaml)
    --cache-dir PATH              Where to cache cloned repos (default: .shipwreck/repos/)
    --no-pull                     Don't pull latest — use cached repos as-is
    --include-repo TEXT            Only scan these repos (repeatable)
    --exclude-repo TEXT            Skip these repos (repeatable)
    --resolve-env / --no-resolve-env   Override config resolve_env_vars

  map — "Chart the waters"
    -c, --config PATH             Config file
    -o, --output PATH             Output directory (default: .shipwreck/output/)
    --format [html|mermaid|json|all]  Output format (default: all)
    --snapshot                    Also save a timestamped snapshot
    --diff-from PATH              Previous snapshot JSON — overlay diff in report
    --mermaid-per-repo            Also generate per-repo Mermaid subgraphs

  dig — "What lies beneath?"
    -s, --snapshot PATH           Snapshot JSON to query (default: latest in .shipwreck/)
    --uses IMAGE                  "What uses this image?"
    --used-by IMAGE               "What does this image depend on?"
    --stale                       List all stale images
    --critical                    List images by criticality (highest first)
    --classify CLASS              Filter by classification
    --format [json|text|table]    Output format (default: table)

  lookout — "Scan the horizon"
    -c, --config PATH             Config file
    -s, --snapshot PATH           Existing snapshot to enrich with registry data
    --registry NAME               Only check this registry (default: all internal)
    --include-external            Also check external registries (respects registry_policy)
    --yes                         Skip external registry approval prompts (CI mode)

  plunder — "Raid the GitLab seas"
    --url TEXT                    GitLab instance URL
    --group TEXT                  Group path
    --token-env TEXT              Env var holding the access token
    --include-subgroups           Include nested subgroups
    --include-pattern TEXT        Regex filter for project paths
    --exclude-pattern TEXT        Regex exclude filter
    --dry-run                     Just list discovered repos
    --append-config PATH          Append discovered repos to config file

  log — "The captain's log"
    --before PATH                 Previous snapshot JSON
    --after PATH                  Current snapshot JSON (default: latest)
    -o, --output PATH             Output diff report
    --format [html|mermaid|json|all]

  sail — "Full speed ahead" (hunt + lookout + map)
    -c, --config PATH             Config file
    -o, --output PATH             Output directory
    --snapshot                    Save snapshot after run
    --diff-from-latest            Auto-diff against most recent snapshot in .shipwreck/
    --yes                         Non-interactive mode (skip all prompts, skip external registries)
```

### 5.1 Typical Workflows

**First-time setup:**
```bash
shipwreck plunder --url https://gitlab.example.com --group my-org \
    --token-env GITLAB_TOKEN --include-subgroups --append-config shipwreck.yaml
# Edit config: add registries, ansible inventory, aliases, version schemes
shipwreck sail -c shipwreck.yaml --snapshot
```

**Cron / CI (non-interactive):**
```bash
shipwreck sail -c shipwreck.yaml --snapshot --diff-from-latest --yes
```

**Ad-hoc queries:**
```bash
shipwreck dig --uses "registry.example.com/base/python"
shipwreck dig --stale
shipwreck dig --critical
```

---

## 6. Architecture

### 6.1 Package Structure

```
shipwreck/
├── pyproject.toml
├── PARSERS.md                      # Detailed parser specifications
├── src/
│   └── shipwreck/
│       ├── __init__.py
│       ├── cli.py                  # Typer app, all commands
│       ├── config.py               # Pydantic models for shipwreck.yaml
│       ├── models.py               # Core domain models (Node, Edge, Graph, ImageRef)
│       ├── scanner.py              # Orchestrator: clone repos, run parsers, build graph
│       ├── git.py                  # Git operations (subprocess-based)
│       ├── parsers/
│       │   ├── __init__.py
│       │   ├── base.py             # Abstract parser protocol
│       │   ├── dockerfile.py
│       │   ├── bake.py
│       │   ├── compose.py
│       │   ├── ansible.py
│       │   ├── gitlab_ci.py
│       │   ├── github_actions.py
│       │   └── fallback.py
│       ├── registry/
│       │   ├── __init__.py
│       │   ├── client.py           # Registry HTTP v2 client
│       │   ├── policy.py           # External registry approval logic
│       │   └── staleness.py        # Version comparison (multi-scheme)
│       ├── resolution/
│       │   ├── __init__.py
│       │   ├── ansible.py          # Ansible playbook generation + evaluation
│       │   ├── env.py              # Environment variable resolution
│       │   ├── bake.py             # HCL variable resolution
│       │   └── compose.py          # .env + interpolation
│       ├── graph/
│       │   ├── __init__.py
│       │   ├── builder.py
│       │   ├── aliases.py          # Alias/variant resolution
│       │   ├── classifier.py
│       │   └── criticality.py
│       ├── discovery/
│       │   ├── __init__.py
│       │   └── gitlab.py
│       ├── output/
│       │   ├── __init__.py
│       │   ├── html.py
│       │   ├── mermaid.py
│       │   ├── json_export.py
│       │   ├── snapshot.py
│       │   └── templates/
│       │       ├── report.html.j2
│       │       ├── css/
│       │       │   └── style.css
│       │       └── js/
│       │           ├── graph.js
│       │           ├── search.js
│       │           └── theme.js
│       └── query/
│           ├── __init__.py
│           └── engine.py
└── tests/
    ├── conftest.py
    ├── fixtures/
    │   ├── dockerfiles/
    │   ├── bake/
    │   ├── compose/
    │   ├── ansible/
    │   │   ├── inventory/
    │   │   ├── roles/
    │   │   └── playbooks/
    │   ├── gitlab_ci/
    │   └── github_actions/
    ├── unit/
    │   ├── test_parsers/           # One test file per parser
    │   ├── test_resolution/
    │   ├── test_graph/
    │   ├── test_aliases.py
    │   ├── test_staleness.py
    │   ├── test_version_schemes.py
    │   ├── test_config.py
    │   └── test_query.py
    └── integration/
        ├── test_scanner.py         # End-to-end: fixtures → graph
        ├── test_output.py          # Graph → HTML/Mermaid/JSON
        ├── test_snapshot_diff.py
        ├── test_ansible_resolve.py # Requires ansible installed
        └── test_registry.py        # Mock registry responses
```

### 6.2 Key Dependencies

| Package | Purpose |
|---------|---------|
| `typer[all]` | CLI framework |
| `pydantic>=2` | Config + domain models |
| `jinja2` | HTML report templating |
| `pyyaml` | YAML parsing |
| `python-hcl2` | HCL parsing (docker-bake) |
| `httpx` | Registry + GitLab API client |
| `semver` | Semver comparison |
| `rich` | CLI output / progress |

Dev/test:
| Package | Purpose |
|---------|---------|
| `pytest` | Test framework |
| `pytest-mock` | Mocking |
| `respx` | httpx mock transport (registry/API tests) |
| `pytest-snapshot` | Snapshot testing for output formats |

JS (inlined in HTML template):
| Library | Purpose |
|---------|---------|
| `d3.js` | SVG rendering, zoom/pan |
| `dagre-d3` or `elkjs` | Horizontal graph layout |

### 6.3 Data Flow

```
shipwreck.yaml
    │
    ├──► [plunder] GitLab API → discovered repos → append to config
    │
    ▼
┌─────────────┐
│ hunt         │──► local repo checkouts (.shipwreck/repos/)
│ git clone/pull (subprocess)
└──────┬──────┘
       │
       ▼
┌─────────────┐     ┌──────────────┐
│ File         │────►│ Parser       │──► list[ImageReference]
│ Discovery    │     │ Selection    │    (raw templates + resolved where possible)
└─────────────┘     └──────────────┘
                           │
                    ┌──────┴───────┐
                    │              │
                    ▼              ▼
             ┌───────────┐  ┌───────────┐
             │ Ansible    │  │ Env/Bake  │
             │ Resolve    │  │ Resolve   │
             │ (playbook) │  │ (simple)  │
             └─────┬─────┘  └─────┬─────┘
                   │              │
                   └──────┬───────┘
                          │
                    ┌─────┴──────┐
                    │ Alias      │──► Canonical nodes
                    │ Resolution │
                    └─────┬──────┘
                          │
                    ┌─────┴──────┐
                    │ Graph      │──► Graph(nodes, edges)
                    │ Builder    │
                    └─────┬──────┘
                          │
                   ┌──────┴───────┐
                   │              │
                   ▼              ▼
            ┌───────────┐  ┌───────────┐
            │ lookout    │  │ Classify  │
            │ Registry   │  │ + Score   │
            │ (+ policy) │  │           │
            └─────┬─────┘  └─────┬─────┘
                  │              │
                  └──────┬───────┘
                         │
                 ┌───────┼───────┐
                 │       │       │
                 ▼       ▼       ▼
           ┌────────┐ ┌──────┐ ┌─────────┐
           │ HTML   │ │ JSON │ │ Mermaid │
           └────────┘ └──────┘ └─────────┘
                         │
                    ┌────┴────┐
                    │Snapshot │──► .shipwreck/snapshots/
                    └─────────┘
```

---

## 7. Testing Strategy

### 7.1 Unit Tests

Every parser, resolver, and graph operation has dedicated unit tests with fixture files.

**Parser tests** — each parser gets a set of fixture files (realistic examples) and expected `ImageReference` outputs. Tests verify:
- Correct image name + tag extraction
- Correct edge type assignment
- Correct source file + line tracking
- Variable template preservation (before resolution)
- Multi-stage / multi-target handling
- Edge cases: comments, disabled services, conditional blocks

**Resolution tests** — mock external dependencies:
- Ansible: mock `subprocess.run` to return known resolved values
- Env vars: mock `os.environ`
- Bake/compose vars: fixture `.env` and HCL variable blocks

**Graph tests:**
- Alias resolution: verify canonical deduplication
- Classification: verify path-pattern rules
- Criticality: verify fan-out calculation on known graph shapes
- Version scheme: verify ordering for each scheme type

**Config tests** — Pydantic validation of config variants, defaults, error messages.

### 7.2 Integration Tests

**Scanner integration** — full pipeline from fixture repos (directories with realistic file structures) through to a complete `Graph` object. Verify node count, edge count, classifications.

**Output integration** — generate HTML/Mermaid/JSON from a known graph and verify:
- HTML: well-formed, contains expected node IDs, no broken JS
- Mermaid: valid syntax (can be parsed)
- JSON: validates against schema, contains expected data

**Snapshot diff** — create two snapshots from slightly different fixture sets, verify diff contains expected changes.

**Ansible resolution** — integration test that requires `ansible-playbook` to be available (marked with `pytest.mark.skipif`). Uses a fixture inventory + playbook to verify end-to-end resolution.

**Registry** — mock registry responses using `respx` (httpx mock transport). Test auth flow, tag listing, manifest fetching, staleness computation.

### 7.3 Mocking Approach

| Component | Mock Strategy |
|-----------|--------------|
| Git operations | Mock `subprocess.run` — return fixture repo paths |
| Registry API | `respx` — mock httpx responses with realistic payloads |
| Ansible resolution | Mock `subprocess.run` — return known output lines |
| Environment variables | `monkeypatch.setenv` / `mock.patch.dict(os.environ)` |
| File system | Use `tmp_path` fixtures with realistic directory structures |
| GitLab API | `respx` — mock project listing responses |

---

## 8. MCP Integration Surface

The JSON metadata format is directly consumable by MCP tools. A future `shipwreck-mcp` server could expose:

| Tool | Description |
|------|-------------|
| `shipwreck_uses` | What images/repos consume a given image? |
| `shipwreck_depends_on` | What does a given image build from? |
| `shipwreck_stale` | List all stale dependencies with version deltas |
| `shipwreck_critical` | Rank images by criticality score |
| `shipwreck_diff` | What changed between two snapshots? |
| `shipwreck_path` | Full dependency path between two images |

The `dig` CLI subcommand covers the same use cases for scripting.

---

## 9. Implementation Phases

### Phase 1 — Core Graph (MVP)

- Config loading (Pydantic)
- Git clone/pull (subprocess, with caching in `.shipwreck/repos/`)
- Dockerfile parser
- Docker Compose parser
- Docker Bake parser
- Graph builder + criticality scoring
- Alias resolution
- JSON metadata export
- Mermaid output (full graph + per-repo)
- Basic HTML report (dagre layout, click-to-highlight, dark mode)
- `hunt`, `map`, `dig` CLI commands
- Unit tests for all parsers and graph operations

### Phase 2 — CI Parsers + Ansible + Resolution

- GitLab CI parser
- GitHub Actions parser
- Ansible task parser
- Ansible playbook-based variable resolution
- Environment variable resolution
- Bake/compose variable resolution
- Classification engine (path-based rules)
- Fallback regex scanner
- Version scheme engine (semver, numeric, date, regex)
- Unit + integration tests for resolution

### Phase 3 — Registry + Staleness

- Registry API client (HTTP v2, token/basic auth)
- Registry policy enforcement (external approval prompts)
- Staleness detection (multi-scheme)
- Image metadata enrichment (size, build date, digest)
- Staleness badges in HTML + Mermaid
- `lookout` command
- Registry mock tests

### Phase 4 — Snapshots + Diff

- Snapshot save/load (`.shipwreck/snapshots/`)
- JSON diff generation
- HTML diff overlay mode
- Mermaid diff variant
- `log` command
- Snapshot integration tests

### Phase 5 — Discovery + Polish

- GitLab group auto-discovery (`plunder`)
- `sail` combined command (cron/CI-friendly)
- Search/filter bar in HTML report
- Light mode toggle
- Node port rendering for multi-tag + variants
- MCP server stub

---

## 10. Storage Layout

All Shipwreck working data lives under `.shipwreck/` in the project root:

```
.shipwreck/
├── repos/                    # Cached git clones
│   ├── base-images/
│   └── myapp/
├── snapshots/                # Timestamped JSON snapshots
│   ├── 2025-02-20T100000Z.json
│   └── 2025-02-27T100000Z.json
├── output/                   # Generated reports (default output dir)
│   ├── shipwreck.html
│   ├── shipwreck.json
│   ├── shipwreck.mermaid
│   └── per-repo/
│       ├── base-images.mermaid
│       └── myapp.mermaid
└── tmp/                      # Temporary files (ansible playbooks etc.)
```

---

## 11. Open Questions / Future Considerations

1. **GitHub org discovery** — equivalent of `plunder` for GitHub orgs.
2. **CI gate** — `--fail-if-stale` flag on `lookout` for CI pipelines that want to enforce freshness.
3. **Graph export** — DOT/Graphviz export alongside Mermaid.
4. **Image layer analysis** — deeper registry inspection showing shared layers between images.
