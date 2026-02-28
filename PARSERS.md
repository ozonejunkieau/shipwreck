# Shipwreck Parser Specifications

This document contains the complete specification for every parser in Shipwreck. It is designed to be self-contained — an implementer (human or AI agent) should be able to build any parser from this document alone, without needing to reference the main spec for parsing logic.

---

## Common Types

All parsers produce `ImageReference` objects. These are the shared types across all parsers.

### ImageReference

```python
class ImageReference(BaseModel):
    """A single image reference discovered in a file."""

    # The raw image string as found in the file (before any resolution)
    raw: str  # e.g. "{{ app_registry }}/myapp:{{ version }}" or "python:3.12-slim"

    # Parsed components (None if raw contains unresolvable templates)
    registry: str | None  # e.g. "registry.example.com", "docker.io"
    name: str | None  # e.g. "myapp", "library/python"
    tag: str | None  # e.g. "3.12-slim", "0.2.0", "latest"

    # Where this reference was found
    source: SourceLocation

    # What kind of relationship this represents
    relationship: EdgeType  # builds_from | produces | consumes

    # How confident we are in this extraction
    confidence: Confidence  # high | medium | low

    # For unresolved templates — the variable names that need resolution
    unresolved_variables: list[str] = []

    # Parser-specific metadata
    metadata: dict[str, Any] = {}


class SourceLocation(BaseModel):
    """Where in the source tree a reference was found."""

    repo: str  # repository name
    file: str  # relative file path within the repo
    line: int  # 1-indexed line number
    parser: str  # which parser found this ("dockerfile", "bake", etc.)


class EdgeType(str, Enum):
    BUILDS_FROM = "builds_from"
    PRODUCES = "produces"
    CONSUMES = "consumes"


class Confidence(str, Enum):
    HIGH = "high"  # Parsed from a well-defined field
    MEDIUM = "medium"  # Parsed with some inference (e.g. variable substitution)
    LOW = "low"  # Best-effort regex match
```

### Parser Protocol

Every parser implements:

```python
class Parser(Protocol):
    """Protocol that all parsers must implement."""

    @property
    def name(self) -> str:
        """Unique parser identifier (e.g. 'dockerfile', 'bake')."""
        ...

    def can_handle(self, file_path: Path) -> bool:
        """Return True if this parser should process the given file."""
        ...

    def parse(self, file_path: Path, repo_name: str) -> list[ImageReference]:
        """Parse the file and return all discovered image references."""
        ...
```

### Image String Parsing

All parsers share a common utility for parsing an image string into registry/name/tag components:

```
Input: "registry.example.com/namespace/image:tag"
  → registry = "registry.example.com"
  → name = "namespace/image"
  → tag = "tag"

Input: "python:3.12-slim"
  → registry = "docker.io"  (implicit)
  → name = "library/python"  (implicit library/ prefix)
  → tag = "3.12-slim"

Input: "myapp"
  → registry = "docker.io"
  → name = "library/myapp"
  → tag = "latest"  (implicit)

Input: "{{ registry }}/myapp:{{ version }}"
  → registry = None  (unresolvable)
  → name = None
  → tag = None
  → unresolved_variables = ["registry", "version"]
```

Rules:
- If the first component contains a `.` or `:` (port), it's a registry. Otherwise it's part of the name.
- If no tag after `:`, default to `latest`.
- If the image string contains `{{`, `$`, or `${` template markers, mark as unresolved and extract variable names.

---

## Parser 1: Dockerfile

### File Matching

```python
def can_handle(self, file_path: Path) -> bool:
    name = file_path.name.lower()
    return (
        name == "dockerfile"
        or name.startswith("dockerfile.")  # Dockerfile.prod, Dockerfile.dev
        or name.endswith(".dockerfile")
    )
```

### Extraction Rules

#### FROM statements

The primary extraction target. Each `FROM` line produces a `builds_from` reference.

```dockerfile
FROM python:3.12-slim
FROM python:3.12-slim AS builder
FROM --platform=linux/amd64 python:3.12-slim
```

**Regex pattern:**
```
^FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?
```

Capture group 1 = image reference, capture group 2 = stage alias (optional).

#### ARG-based references

`ARG` directives before `FROM` can parameterise the image:

```dockerfile
ARG BASE_IMAGE=python:3.12-slim
ARG BASE_VERSION=3.12
FROM ${BASE_IMAGE}
FROM python:${BASE_VERSION}-slim
```

**Resolution approach:**
1. Collect all `ARG` directives and their default values
2. When a `FROM` contains `${VAR}` or `$VAR`, substitute from collected ARGs
3. If an ARG has no default and no external value, mark as unresolved

#### Multi-stage filtering

Stage aliases (the `AS name` part) must be tracked. If a subsequent `FROM` references a stage alias rather than an image, it is **not** an external dependency:

```dockerfile
FROM python:3.12-slim AS builder    # → external dep: python:3.12-slim
FROM builder AS runtime              # → internal stage ref, NOT an external dep
FROM scratch                         # → special case, not a dependency
```

`scratch` is a Docker built-in and should never be emitted as a dependency.

#### Comments and disabled lines

Lines starting with `#` are comments and must be ignored:
```dockerfile
# FROM old-image:1.0
FROM new-image:2.0
```

#### Parser directives

Lines like `# syntax=docker/dockerfile:1` at the top of the file are parser directives, not comments. They should be ignored (not parsed as image refs).

### Output

| Input | Output |
|-------|--------|
| `FROM python:3.12-slim` | `ImageReference(raw="python:3.12-slim", relationship=BUILDS_FROM, confidence=HIGH)` |
| `FROM ${BASE_IMAGE}` where `ARG BASE_IMAGE=python:3.12` | `ImageReference(raw="python:3.12", relationship=BUILDS_FROM, confidence=MEDIUM)` |
| `FROM ${UNKNOWN_VAR}` | `ImageReference(raw="${UNKNOWN_VAR}", relationship=BUILDS_FROM, confidence=MEDIUM, unresolved_variables=["UNKNOWN_VAR"])` |
| `FROM builder` (where builder is a stage alias) | No output |
| `FROM scratch` | No output |

### Edge Cases

- **Multi-line:** Dockerfiles don't support line continuation for `FROM`. Each `FROM` is a single line.
- **Multiple FROM:** Each `FROM` produces a separate `ImageReference`. The last `FROM` is typically the final image.
- **ARG scope:** `ARG` before the first `FROM` is global. `ARG` after a `FROM` is scoped to that build stage. Only global ARGs affect image references.
- **Build args from bake/compose:** These come from external sources and will be resolved in the resolution phase, not during parsing. The parser should record them as unresolved.

### Test Cases

```python
# test_dockerfile_parser.py

def test_simple_from():
    """Single FROM with explicit tag."""
    # Input: "FROM python:3.12-slim\n"
    # Expected: [ImageReference(raw="python:3.12-slim", relationship=BUILDS_FROM)]

def test_from_with_alias():
    """FROM ... AS alias should not suppress the image reference."""
    # Input: "FROM python:3.12-slim AS builder\n"
    # Expected: [ImageReference(raw="python:3.12-slim", ...)]

def test_stage_ref_not_emitted():
    """FROM referencing a previous stage alias should not produce an ImageReference."""
    # Input: "FROM python:3.12 AS builder\nFROM builder AS runtime\n"
    # Expected: [ImageReference(raw="python:3.12", ...)]  # only one

def test_scratch_ignored():
    """FROM scratch is a built-in, not an external dependency."""
    # Input: "FROM scratch\n"
    # Expected: []

def test_arg_substitution():
    """ARG default values should be substituted into FROM."""
    # Input: "ARG VERSION=3.12\nFROM python:${VERSION}-slim\n"
    # Expected: [ImageReference(raw="python:3.12-slim", confidence=MEDIUM)]

def test_arg_no_default():
    """ARG without default results in unresolved reference."""
    # Input: "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n"
    # Expected: [ImageReference(raw="${BASE_IMAGE}", unresolved_variables=["BASE_IMAGE"])]

def test_platform_flag_ignored():
    """--platform flag should not affect image extraction."""
    # Input: "FROM --platform=linux/amd64 python:3.12-slim\n"
    # Expected: [ImageReference(raw="python:3.12-slim", ...)]

def test_comments_ignored():
    """Commented FROM lines should not produce references."""
    # Input: "# FROM old:1.0\nFROM new:2.0\n"
    # Expected: [ImageReference(raw="new:2.0", ...)]

def test_no_tag_defaults_to_latest():
    """Image without tag should default to latest."""
    # Input: "FROM python\n"
    # Expected: [ImageReference(raw="python", tag="latest", ...)]

def test_registry_with_port():
    """Registry with port number should be parsed correctly."""
    # Input: "FROM registry.example.com:5000/myimage:1.0\n"
    # Expected: [ImageReference(registry="registry.example.com:5000", name="myimage", tag="1.0")]

def test_multiple_from_all_emitted():
    """All FROM statements produce references (except stage refs)."""
    # Input: multi-stage Dockerfile with 3 FROM statements
    # Expected: references for each non-stage-alias FROM
```

---

## Parser 2: Docker Bake (HCL)

### File Matching

```python
def can_handle(self, file_path: Path) -> bool:
    name = file_path.name
    return name in ("docker-bake.hcl", "docker-bake.override.hcl")
```

### Extraction Rules

Docker Bake uses HCL2 syntax. The `python-hcl2` library parses HCL into a Python dict structure.

#### Variable blocks

```hcl
variable "REGISTRY" {
  default = "registry.example.com"
}
variable "VERSION" {
  default = "0.2.0"
}
```

Collect all variables and their defaults into a resolution context.

#### Target blocks — tags (produces)

```hcl
target "myapp" {
  tags = [
    "${REGISTRY}/myapp:${VERSION}",
    "${REGISTRY}/myapp:latest"
  ]
}
```

Each tag produces a `produces` edge. Variable interpolation (`${VAR}`) should be resolved using collected variables.

#### Target blocks — contexts (builds_from)

```hcl
target "myapp" {
  contexts = {
    base = "docker-image://registry.example.com/base/python:3.12"
  }
}
```

Context values prefixed with `docker-image://` are image references → `builds_from` edge. Strip the `docker-image://` prefix.

Other context types (`target:`, local paths) are not image references.

#### Target blocks — dockerfile + args

```hcl
target "myapp" {
  dockerfile = "Dockerfile.prod"
  args = {
    BASE_VERSION = "3.12"
  }
}
```

The `dockerfile` field links this target to a Dockerfile. The `args` provide ARG values for that Dockerfile. This information should be recorded in metadata so the Dockerfile parser can use it for ARG resolution, but the bake parser itself does not parse the Dockerfile.

#### Group blocks

```hcl
group "all" {
  targets = ["myapp", "worker", "db-init"]
}
```

Groups reference targets but don't introduce new image references. They can be used to understand which targets are built together.

#### Target inheritance

```hcl
target "base" {
  dockerfile = "Dockerfile.base"
  tags = ["registry.example.com/base:1.0"]
}

target "myapp" {
  inherits = ["base"]
  tags = ["registry.example.com/myapp:1.0"]
}
```

`inherits` means the target extends another. Inherited fields apply unless overridden. For tag extraction, only the final resolved tags matter.

### Output

| Input | Output |
|-------|--------|
| `tags = ["registry.example.com/myapp:0.2.0"]` | `ImageReference(relationship=PRODUCES, confidence=HIGH)` |
| `contexts = { base = "docker-image://base:1.0" }` | `ImageReference(raw="base:1.0", relationship=BUILDS_FROM, confidence=HIGH)` |
| `tags = ["${REGISTRY}/myapp:${VERSION}"]` with variables resolved | `ImageReference(relationship=PRODUCES, confidence=MEDIUM)` |
| `tags = ["${UNKNOWN}/myapp:latest"]` | `ImageReference(relationship=PRODUCES, unresolved_variables=["UNKNOWN"])` |

### Edge Cases

- **Variable interpolation within interpolation:** e.g. `"${REGISTRY}/${PROJECT}:${VERSION}"` — all variables should be resolved independently.
- **No default on variable:** If a variable has no `default` block, it's expected to come from the environment or CLI. Mark as unresolved.
- **Multiple tags per target:** Each tag is a separate `ImageReference` with `PRODUCES`.
- **Inherits chain:** Resolve the full inheritance chain before extracting tags.
- **HCL functions:** `python-hcl2` doesn't evaluate HCL functions. If tags contain function calls, extract what we can and flag.

### Test Cases

```python
def test_simple_target_tags():
    """Tags in a target produce PRODUCES references."""

def test_variable_interpolation():
    """Variables are resolved in tag strings."""

def test_docker_image_context():
    """docker-image:// contexts produce BUILDS_FROM references."""

def test_non_image_context_ignored():
    """target: and path contexts are not image references."""

def test_variable_no_default():
    """Variables without defaults are marked unresolved."""

def test_multiple_tags():
    """Each tag in the list is a separate ImageReference."""

def test_inherits_resolved():
    """Inherited tags from parent targets are included."""

def test_group_no_references():
    """Group blocks do not produce image references."""

def test_override_file():
    """Override file variables take precedence."""

def test_args_recorded_in_metadata():
    """Target args are recorded for Dockerfile cross-reference."""
```

---

## Parser 3: Docker Compose

### File Matching

```python
def can_handle(self, file_path: Path) -> bool:
    name = file_path.name
    return name in (
        "docker-compose.yml", "docker-compose.yaml",
        "compose.yml", "compose.yaml",
    ) or (
        name.startswith("docker-compose.") and name.endswith((".yml", ".yaml"))
    ) or (
        name.startswith("compose.") and name.endswith((".yml", ".yaml"))
    )
```

### Extraction Rules

#### Service image fields

```yaml
services:
  web:
    image: registry.example.com/myapp:0.1.1
  db:
    image: postgres:16
  redis:
    image: redis:7-alpine
```

Each `image:` field under a service produces a `consumes` reference.

#### Variable interpolation

```yaml
services:
  web:
    image: ${REGISTRY:-docker.io}/myapp:${VERSION:-latest}
```

Compose uses `${VAR}`, `${VAR:-default}`, `${VAR-default}`, `${VAR:?error}` syntax. Resolution:
1. Check for `.env` file in the same directory — load it
2. Apply default values from `:-` / `-` syntax
3. If `resolve_env_vars` is enabled, check current environment
4. Remaining unresolved variables are flagged

#### Build contexts

```yaml
services:
  web:
    build:
      context: .
      dockerfile: Dockerfile.prod
```

A `build:` block means this service is built, not pulled. Record this in metadata (links to a Dockerfile). If both `image:` and `build:` are present, the `image:` tag is the output name → this is a `produces` relationship, not `consumes`.

#### Profiles and disabled services

```yaml
services:
  debug-tools:
    image: debug:latest
    profiles: ["debug"]
```

Services with profiles are still valid references. Include them but record the profile in metadata.

#### Extends

```yaml
services:
  web:
    extends:
      service: base-web
      file: common-services.yml
```

`extends` pulls configuration from another file/service. The referenced file should also be parsed if it's within the repo.

### Output

| Input | Output |
|-------|--------|
| `image: postgres:16` | `ImageReference(relationship=CONSUMES, confidence=HIGH)` |
| `image: ${REGISTRY}/myapp:${VER}` with `.env` providing values | `ImageReference(relationship=CONSUMES, confidence=MEDIUM)` |
| `build: .` + `image: myapp:latest` | `ImageReference(relationship=PRODUCES, confidence=HIGH)` + metadata linking to Dockerfile |
| `build: .` (no `image:`) | No ImageReference (will be handled by Dockerfile parser) |

### Test Cases

```python
def test_simple_image():
    """Direct image reference produces CONSUMES."""

def test_multiple_services():
    """Each service with image: produces a reference."""

def test_env_interpolation_with_default():
    """${VAR:-default} resolves to default when VAR not set."""

def test_env_file_loaded():
    """.env file values are used for interpolation."""

def test_build_with_image_is_produces():
    """build: + image: together means PRODUCES."""

def test_build_without_image_no_ref():
    """build: without image: doesn't produce an ImageReference."""

def test_service_with_profile():
    """Profiled services are still included."""

def test_compose_override():
    """Override files are handled (separate parse, same service names)."""
```

---

## Parser 4: Ansible

### File Matching

```python
def can_handle(self, file_path: Path) -> bool:
    if file_path.suffix not in (".yml", ".yaml"):
        return False
    # Check if this is in an Ansible-structured path
    parts = file_path.parts
    ansible_indicators = {"tasks", "roles", "handlers", "playbooks", "plays"}
    return bool(ansible_indicators & set(parts))
```

Additionally, any YAML file containing known Ansible Docker modules in its content qualifies:
- `community.docker.docker_container`
- `community.docker.docker_compose`  (v1 module)
- `community.docker.docker_compose_v2`
- `docker_container` (old short form)
- `containers.podman.podman_container`

### Extraction Rules

#### Direct image references

```yaml
- name: Deploy app
  community.docker.docker_container:
    name: myapp
    image: registry.example.com/myapp:1.0
```

The `image:` field under a Docker module produces a `consumes` reference.

#### Jinja2 template references

```yaml
- name: Deploy app
  community.docker.docker_container:
    image: "{{ app_registry }}/{{ app_image }}:{{ app_version }}"
```

Extract the raw template string. Identify all `{{ variable }}` references and record them as `unresolved_variables`. These will be resolved in the Ansible resolution phase (see main spec §2.3).

#### Lookup functions

```yaml
- name: Deploy with lookup
  community.docker.docker_container:
    image: "registry.example.com/myapp:{{ lookup('file', '/opt/versions/myapp.txt') }}"
```

Lookup functions cannot be resolved by the parser. Record the full template as raw and mark the lookup as an unresolved variable. The Ansible resolution phase handles these via real `ansible-playbook` evaluation.

#### Loop variables

```yaml
- name: Deploy services
  community.docker.docker_container:
    image: "{{ item.image }}:{{ item.tag }}"
  loop: "{{ services }}"
```

Loop variables (`item`, `item.x`) depend on runtime data. Record the template and mark as unresolved. If the loop source is a static list in the same file, extract what we can.

#### Role defaults and vars

```yaml
# roles/myapp/defaults/main.yml
app_image: registry.example.com/myapp
app_version: "1.0"
```

Files in `defaults/` and `vars/` directories within roles provide variable values. The parser should collect these as a local variable context (lower priority than inventory variables, which are handled by the resolution phase).

#### Include/import tasks

```yaml
- name: Include deployment
  include_tasks: deploy.yml
- import_tasks: setup.yml
```

Referenced files should also be parsed if they exist within the repo.

### Output

| Input | Output |
|-------|--------|
| `image: registry.example.com/myapp:1.0` | `ImageReference(relationship=CONSUMES, confidence=HIGH)` |
| `image: "{{ registry }}/myapp:{{ ver }}"` | `ImageReference(raw="{{ registry }}/myapp:{{ ver }}", relationship=CONSUMES, confidence=LOW, unresolved_variables=["registry", "ver"])` |
| `image:` under a non-Docker module | No output |

### Edge Cases

- **Ansible module detection:** Only extract `image:` under known Docker/container modules. Don't extract `image:` from arbitrary YAML keys.
- **When conditionals:** `when: deploy_app` doesn't change whether the reference exists — still extract it.
- **Block/rescue:** Image references in `block:`, `rescue:`, `always:` are all valid.
- **Tags:** Ansible tags don't affect reference validity.
- **Collections vs short form:** Both `community.docker.docker_container` and `docker_container` should be recognised.
- **`docker_compose` module with inline compose:** If the `community.docker.docker_compose` module embeds compose YAML, parse it for image references too.

### Test Cases

```python
def test_direct_image():
    """Simple image: field under docker_container."""

def test_jinja2_template():
    """Image with Jinja2 variables produces unresolved ref."""

def test_lookup_function():
    """Lookup functions are recorded as unresolved."""

def test_role_defaults_provide_context():
    """defaults/main.yml variables resolve simple templates."""

def test_non_docker_module_ignored():
    """image: under non-Docker modules is not extracted."""

def test_loop_variable():
    """Loop item variables are marked unresolved."""

def test_when_conditional_still_extracted():
    """Conditional tasks still produce references."""

def test_module_short_form():
    """docker_container (without collection prefix) is recognised."""

def test_podman_module():
    """Podman container modules are also recognised."""

def test_include_tasks_noted():
    """Include/import references recorded in metadata for traversal."""
```

---

## Parser 5: GitLab CI

### File Matching

```python
def can_handle(self, file_path: Path) -> bool:
    name = file_path.name
    parts = file_path.parts
    return (
        name == ".gitlab-ci.yml"
        or ".gitlab-ci" in parts  # files in .gitlab-ci/ directory
        or name.endswith(".gitlab-ci.yml")
    )
```

### Extraction Rules

#### Job-level image

```yaml
build:
  image: registry.example.com/ci/builder:1.2.0
```

The `image:` field at job level specifies the container the job runs in → `consumes`.

Also supports the object form:
```yaml
build:
  image:
    name: registry.example.com/ci/builder:1.2.0
    entrypoint: [""]
```

#### Default image

```yaml
default:
  image: python:3.12
```

The default image applies to all jobs that don't specify their own. Extract as `consumes`.

#### Services

```yaml
test:
  services:
    - docker:24.0-dind
    - name: postgres:16
      alias: db
```

Services can be simple strings or objects with `name:`. Both produce `consumes` references.

#### Script-based docker commands (best-effort)

```yaml
build:
  script:
    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_TAG .
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_TAG
    - docker build -t registry.example.com/myapp:latest -f Dockerfile.prod .
```

**Regex patterns for script lines:**
```
docker\s+build\s+.*-t\s+(\S+)       # docker build -t IMAGE
docker\s+push\s+(\S+)                # docker push IMAGE
docker\s+pull\s+(\S+)                # docker pull IMAGE
```

`docker build -t` and `docker push` → `produces` (best-effort, confidence=LOW)
`docker pull` → `consumes` (best-effort, confidence=LOW)

#### Predefined CI variables

These can be resolved deterministically from the repo context:

| Variable | Resolution |
|----------|-----------|
| `$CI_REGISTRY_IMAGE` | `<registry>/<namespace>/<project>` — derivable from repo URL + configured registry |
| `$CI_REGISTRY` | Registry hostname from config |
| `$CI_PROJECT_NAME` | Repo name |
| `$CI_PROJECT_NAMESPACE` | Repo namespace/group |

Variables like `$CI_COMMIT_TAG`, `$CI_COMMIT_SHA`, `$CI_PIPELINE_ID` are runtime-dependent. Record as unresolved.

#### Include directives

```yaml
include:
  - local: '/.gitlab-ci/build.yml'
  - template: 'Auto-DevOps.gitlab-ci.yml'
  - project: 'my-org/ci-templates'
    file: '/templates/build.yml'
```

`local:` includes should be followed and parsed (the file is in the same repo).
`template:` and `project:` includes are external — record them in metadata but don't attempt to fetch.

#### Variables block

```yaml
variables:
  DOCKER_IMAGE: "registry.example.com/ci/builder:1.2.0"
  APP_VERSION: "1.0.0"

build:
  script:
    - docker build -t registry.example.com/myapp:$APP_VERSION .
```

Variables defined at top level or job level provide values for resolution.

### Output

| Input | Output |
|-------|--------|
| `image: registry.example.com/ci/builder:1.2.0` | `ImageReference(relationship=CONSUMES, confidence=HIGH)` |
| `services: [docker:24.0-dind]` | `ImageReference(relationship=CONSUMES, confidence=HIGH)` |
| `docker build -t registry.example.com/myapp:1.0 .` in script | `ImageReference(relationship=PRODUCES, confidence=LOW)` |
| `docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_TAG .` | `ImageReference(relationship=PRODUCES, confidence=LOW, unresolved_variables=["CI_COMMIT_TAG"])` |

### GitLab CI Reserved Keywords

The following top-level keys are **not** job definitions and must be skipped when scanning for `image:` fields:

```python
GITLAB_CI_RESERVED = {
    "default", "include", "stages", "variables", "workflow",
    "before_script", "after_script", "image", "services", "cache",
    "interruptible", "retry", "timeout", "tags",
}
```

Note: `default` **is** scanned for `image:` and `services:` (see "Default image" above), but it is not a job.

When iterating top-level keys, any key not in `GITLAB_CI_RESERVED` and not starting with `.` (hidden/template jobs) is treated as a job definition.

Hidden jobs (`.template_name`) should still be scanned for image references since they may be extended by real jobs.

### Test Cases

```python
def test_job_image():
    """Job-level image: produces CONSUMES."""

def test_job_image_object_form():
    """image: with name: field works."""

def test_default_image():
    """default.image produces CONSUMES."""

def test_services_string():
    """String service entries produce CONSUMES."""

def test_services_object():
    """Object service entries with name: produce CONSUMES."""

def test_docker_build_in_script():
    """docker build -t in script produces PRODUCES (low confidence)."""

def test_docker_push_in_script():
    """docker push in script produces PRODUCES (low confidence)."""

def test_ci_variable_resolution():
    """CI variables defined in variables: block are substituted."""

def test_predefined_ci_vars():
    """$CI_REGISTRY etc are resolved from repo context."""

def test_runtime_ci_vars_unresolved():
    """$CI_COMMIT_TAG etc are marked unresolved."""

def test_local_include_followed():
    """local: includes are noted for parsing."""

def test_external_include_metadata_only():
    """project: includes are recorded but not followed."""

def test_reserved_keys_not_treated_as_jobs():
    """stages, variables etc are not scanned as job definitions."""

def test_hidden_jobs_still_scanned():
    """.template_name jobs are scanned for image references."""
```

---

## Parser 6: GitHub Actions

### File Matching

```python
def can_handle(self, file_path: Path) -> bool:
    return (
        ".github" in file_path.parts
        and "workflows" in file_path.parts
        and file_path.suffix in (".yml", ".yaml")
    )
```

### Extraction Rules

#### Job container

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    container:
      image: registry.example.com/ci/tester:2.0
```

Also supports the short form:
```yaml
jobs:
  test:
    container: registry.example.com/ci/tester:2.0
```

Both produce `consumes` references.

#### Job services

```yaml
jobs:
  test:
    services:
      redis:
        image: redis:7
      postgres:
        image: postgres:16
```

Each service `image:` produces `consumes`.

#### Docker action references

```yaml
steps:
  - uses: docker://alpine:3.18
```

`uses: docker://IMAGE` produces `consumes`.

#### Script-based docker commands

Same as GitLab CI — scan `run:` blocks for `docker build -t`, `docker push`, `docker pull`:

```yaml
steps:
  - run: docker build -t myapp:latest .
  - run: |
      docker build -t ${{ env.REGISTRY }}/myapp:${{ github.sha }} .
      docker push ${{ env.REGISTRY }}/myapp:${{ github.sha }}
```

#### GitHub Actions expression syntax

Actions use `${{ expression }}` syntax:
- `${{ env.VARIABLE }}` — environment variable (resolvable from `env:` blocks)
- `${{ secrets.TOKEN }}` — secret (never resolvable, skip)
- `${{ github.sha }}` — runtime variable (unresolved)
- `${{ inputs.image }}` — workflow input (check `workflow_dispatch` inputs for defaults)

#### Environment variables

```yaml
env:
  REGISTRY: registry.example.com
  IMAGE_NAME: myapp

jobs:
  build:
    env:
      VERSION: "1.0.0"
```

Top-level and job-level `env:` blocks provide variable values.

### Output

| Input | Output |
|-------|--------|
| `container: { image: "tester:2.0" }` | `ImageReference(relationship=CONSUMES, confidence=HIGH)` |
| `services: { redis: { image: "redis:7" } }` | `ImageReference(relationship=CONSUMES, confidence=HIGH)` |
| `uses: docker://alpine:3.18` | `ImageReference(raw="alpine:3.18", relationship=CONSUMES, confidence=HIGH)` |
| `docker build -t ${{ env.REGISTRY }}/myapp:latest .` | `ImageReference(relationship=PRODUCES, confidence=LOW)` |

### Test Cases

```python
def test_job_container_object():
    """container.image produces CONSUMES."""

def test_job_container_string():
    """container: as string produces CONSUMES."""

def test_job_services():
    """Each service image produces CONSUMES."""

def test_docker_action():
    """uses: docker:// produces CONSUMES."""

def test_docker_build_in_run():
    """docker build -t in run: produces PRODUCES."""

def test_env_resolution():
    """env: block values resolve ${{ env.X }} expressions."""

def test_secrets_not_resolved():
    """${{ secrets.X }} are marked unresolved."""

def test_github_context_unresolved():
    """${{ github.sha }} etc are marked unresolved."""

def test_workflow_dispatch_inputs():
    """inputs with defaults provide resolution context."""

def test_multi_line_run():
    """Multi-line run: blocks are fully scanned."""
```

---

## Parser 7: Fallback Scanner

### File Matching

```python
def can_handle(self, file_path: Path) -> bool:
    # Only process files not already handled by a specific parser
    # This is enforced by the scanner orchestrator, not this method
    return file_path.suffix in (".yml", ".yaml", ".json", ".toml", ".cfg", ".conf", "")
```

The fallback scanner runs **after** all specific parsers and only processes files that no other parser claimed.

### Extraction Rules

#### YAML image: field

**Regex:**
```
^\s*image:\s*["']?(\S+?)["']?\s*$
```

Any `image:` field in any YAML file that wasn't caught by a specific parser. Confidence is always `LOW`.

#### FROM in non-standard Dockerfiles

**Regex:**
```
^FROM\s+(?:--platform=\S+\s+)?(\S+)
```

Applied to files that look like Dockerfiles but didn't match the Dockerfile parser's filename patterns (e.g. custom build files).

Also matches files named:
- `Containerfile`
- `Containerfile.*`
- `*.containerfile`

#### Filtering

The fallback scanner should **not** extract:
- Lines that are clearly comments (`#`, `//`)
- Lines inside code blocks in markdown files
- Values that don't look like image references (no `/` or `:`, single word that's a common YAML key)

A simple heuristic: a value looks like an image reference if it contains `/` or `:`, or is a known Docker official image name.

### Output

All references are `confidence=LOW` with `metadata={"parser": "fallback"}`.

### Test Cases

```python
def test_yaml_image_field():
    """image: in unclaimed YAML file produces low-confidence ref."""

def test_containerfile():
    """Containerfile FROM statements are caught."""

def test_comments_ignored():
    """Commented lines are not extracted."""

def test_non_image_yaml_ignored():
    """image: fields that don't look like Docker images are skipped."""

def test_already_claimed_file_skipped():
    """Files handled by specific parsers are not re-processed."""
```

---

## Cross-Parser Coordination

### File claiming

The scanner runs parsers in priority order:
1. Dockerfile
2. Docker Bake
3. Docker Compose
4. Ansible
5. GitLab CI
6. GitHub Actions
7. Fallback (only unclaimed files)

A file is "claimed" if any parser's `can_handle()` returns `True`. The fallback only sees unclaimed files.

**Exception:** A file can be claimed by multiple specific parsers if they extract different information (e.g. a Dockerfile referenced by a bake target — both parsers run). The fallback never overlaps with specific parsers.

### Dockerfile ↔ Bake cross-referencing

When a bake target specifies `dockerfile = "Dockerfile.prod"`, the scanner should:
1. Parse the bake file (getting `produces` and `builds_from` from contexts)
2. Parse the referenced Dockerfile (getting `builds_from` from FROM)
3. Link the Dockerfile's output to the bake target's tags

The bake parser records `dockerfile` and `args` in metadata. The scanner uses this to pass ARG values to the Dockerfile parser for that specific file.

### Line number tracking

All parsers must track the 1-indexed line number where each reference was found. For YAML parsed via PyYAML, use the `Loader` that preserves line info, or count lines during regex scanning.

### Image string validation

Before emitting an `ImageReference`, validate that the extracted string plausibly looks like a Docker image reference:
- Not empty
- Not a bare YAML keyword (`true`, `false`, `null`, `yes`, `no`)
- Not a file path (starts with `/`, `./`, `../`)
- Not a URL with scheme other than `docker://` (e.g. `https://...`)
- Contains reasonable characters (alphanumeric, `/`, `:`, `-`, `_`, `.`, `$`, `{`, `}`)
