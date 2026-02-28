"""GitHub Actions workflow parser for Shipwreck."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from shipwreck.models import Confidence, EdgeType, ImageReference, SourceLocation
from shipwreck.parsers.base import _GH_ACTIONS_VAR_RE, is_template_string, parse_image_string, validate_image_ref

# An image token may contain ${{ expr }} segments (which contain spaces).
# This pattern matches: a sequence of non-space chars OR a ${{ ... }} block.
_IMAGE_TOKEN = r"(?:\$\{\{[^}]*\}\}|[^\s])+"

# Matches `docker build -t IMAGE` — group 1 is the image reference.
_DOCKER_BUILD_RE = re.compile(r"docker\s+build\s+.*?-t\s+(" + _IMAGE_TOKEN + r")")

# Matches `docker push IMAGE` — group 1 is the image reference.
_DOCKER_PUSH_RE = re.compile(r"docker\s+push\s+(" + _IMAGE_TOKEN + r")")

# Matches `docker pull IMAGE` — group 1 is the image reference.
_DOCKER_PULL_RE = re.compile(r"docker\s+pull\s+(" + _IMAGE_TOKEN + r")")


def _resolve_gh_expr(value: str, env: dict[str, str]) -> tuple[str, list[str]]:
    """Resolve ``${{ env.VAR }}`` expressions in a string.

    - ``${{ env.VAR }}`` — resolved from *env* if present.
    - ``${{ secrets.* }}`` — never resolvable; left unresolved.
    - ``${{ github.* }}`` — runtime variable; left unresolved.
    - ``${{ inputs.* }}`` — resolved from *env* under the bare input name if
      present (callers populate *env* with workflow_dispatch defaults).
    - Any other expression is left unresolved.

    Args:
        value: The raw string that may contain ``${{ … }}`` expressions.
        env: Mapping of known variable names to their values.

    Returns:
        A tuple of (resolved_string, list_of_unresolved_variable_names).
    """
    unresolved: list[str] = []

    def _replace(m: re.Match[str]) -> str:
        expr = m.group(1).strip()
        # env.VAR
        if expr.startswith("env."):
            var_name = expr[len("env."):]
            v = env.get(var_name)
            if v is not None:
                return v
            unresolved.append(expr)
            return m.group(0)
        # inputs.VAR — check env for the bare input name as well
        if expr.startswith("inputs."):
            var_name = expr[len("inputs."):]
            v = env.get(var_name) or env.get(expr)
            if v is not None:
                return v
            unresolved.append(expr)
            return m.group(0)
        # secrets.* — never resolvable
        if expr.startswith("secrets."):
            unresolved.append(expr)
            return m.group(0)
        # github.* — runtime
        if expr.startswith("github."):
            unresolved.append(expr)
            return m.group(0)
        # Unknown expression — leave unresolved
        unresolved.append(expr)
        return m.group(0)

    resolved = _GH_ACTIONS_VAR_RE.sub(_replace, value)
    return resolved, unresolved


def _find_line(raw_text: str, pattern: str, start_line: int = 1) -> int:
    """Return the 1-indexed line number of the first occurrence of *pattern*.

    Searches from *start_line* (1-indexed) onwards.  Returns *start_line* if
    not found so that callers always get a plausible line number.

    Args:
        raw_text: Full file text to search.
        pattern: Literal string to look for.
        start_line: Search from this line (1-indexed).

    Returns:
        1-indexed line number of the first match.
    """
    for lineno, line in enumerate(raw_text.splitlines(), start=1):
        if lineno < start_line:
            continue
        if pattern in line:
            return lineno
    return start_line


def _scan_run_block(
    run_text: str,
    env: dict[str, str],
    repo_name: str,
    file_path: Path,
    parser_name: str,
    raw_text: str,
    base_line: int,
) -> list[ImageReference]:
    """Scan a ``run:`` block for best-effort docker command references.

    Looks for ``docker build -t``, ``docker push``, and ``docker pull``
    patterns.  All results are ``LOW`` confidence.

    Args:
        run_text: The content of the ``run:`` field (may be multi-line).
        env: Environment mapping used for expression resolution.
        repo_name: Repository name for ``SourceLocation``.
        file_path: Absolute path to the workflow file.
        parser_name: Name of the parser (``"github_actions"``).
        raw_text: Full raw file text (for line number lookup).
        base_line: Line number to start searching from.

    Returns:
        List of ``ImageReference`` objects (may be empty).
    """
    refs: list[ImageReference] = []

    for line in run_text.splitlines():
        stripped = line.strip()

        # docker build -t IMAGE
        m = _DOCKER_BUILD_RE.search(stripped)
        if m:
            raw_image = m.group(1)
            resolved, unresolved = _resolve_gh_expr(raw_image, env)
            if validate_image_ref(resolved) or is_template_string(resolved):
                registry, name, tag, parse_unresolved = parse_image_string(resolved)
                all_unresolved = list(dict.fromkeys(unresolved + parse_unresolved))
                confidence = Confidence.LOW
                lineno = _find_line(raw_text, stripped, base_line)
                refs.append(
                    ImageReference(
                        raw=raw_image,
                        registry=registry,
                        name=name,
                        tag=tag,
                        source=SourceLocation(
                            repo=repo_name,
                            file=str(file_path),
                            line=lineno,
                            parser=parser_name,
                        ),
                        relationship=EdgeType.PRODUCES,
                        confidence=confidence,
                        unresolved_variables=all_unresolved,
                        metadata={"source": "docker_build"},
                    )
                )
            continue

        # docker push IMAGE
        m = _DOCKER_PUSH_RE.search(stripped)
        if m:
            raw_image = m.group(1)
            resolved, unresolved = _resolve_gh_expr(raw_image, env)
            if validate_image_ref(resolved) or is_template_string(resolved):
                registry, name, tag, parse_unresolved = parse_image_string(resolved)
                all_unresolved = list(dict.fromkeys(unresolved + parse_unresolved))
                confidence = Confidence.LOW
                lineno = _find_line(raw_text, stripped, base_line)
                refs.append(
                    ImageReference(
                        raw=raw_image,
                        registry=registry,
                        name=name,
                        tag=tag,
                        source=SourceLocation(
                            repo=repo_name,
                            file=str(file_path),
                            line=lineno,
                            parser=parser_name,
                        ),
                        relationship=EdgeType.PRODUCES,
                        confidence=confidence,
                        unresolved_variables=all_unresolved,
                        metadata={"source": "docker_push"},
                    )
                )
            continue

        # docker pull IMAGE
        m = _DOCKER_PULL_RE.search(stripped)
        if m:
            raw_image = m.group(1)
            resolved, unresolved = _resolve_gh_expr(raw_image, env)
            if validate_image_ref(resolved) or is_template_string(resolved):
                registry, name, tag, parse_unresolved = parse_image_string(resolved)
                all_unresolved = list(dict.fromkeys(unresolved + parse_unresolved))
                confidence = Confidence.LOW
                lineno = _find_line(raw_text, stripped, base_line)
                refs.append(
                    ImageReference(
                        raw=raw_image,
                        registry=registry,
                        name=name,
                        tag=tag,
                        source=SourceLocation(
                            repo=repo_name,
                            file=str(file_path),
                            line=lineno,
                            parser=parser_name,
                        ),
                        relationship=EdgeType.CONSUMES,
                        confidence=confidence,
                        unresolved_variables=all_unresolved,
                        metadata={"source": "docker_pull"},
                    )
                )

    return refs


class GitHubActionsParser:
    """Parser that extracts image references from GitHub Actions workflow files.

    Handles:

    - Job ``container:`` field (object form with ``image:`` and plain string form)
    - Job ``services:`` entries (``image:`` fields)
    - Step ``uses: docker://IMAGE`` references
    - Best-effort scanning of ``run:`` blocks for ``docker build -t``,
      ``docker push``, and ``docker pull`` commands
    - ``${{ env.VAR }}`` expression resolution from top-level and job-level
      ``env:`` blocks and ``workflow_dispatch`` input defaults
    - ``${{ secrets.* }}`` and ``${{ github.* }}`` marked as unresolved
    """

    @property
    def name(self) -> str:
        """Unique parser identifier."""
        return "github_actions"

    def can_handle(self, file_path: Path) -> bool:
        """Return True if this parser should process the given file.

        Matches ``.yml`` / ``.yaml`` files inside a ``.github/workflows/``
        directory anywhere in the path.

        Args:
            file_path: Path to the candidate file.

        Returns:
            True when the file is inside ``.github/workflows/`` and has a
            ``.yml`` or ``.yaml`` extension.
        """
        return (
            ".github" in file_path.parts
            and "workflows" in file_path.parts
            and file_path.suffix in (".yml", ".yaml")
        )

    def parse(self, file_path: Path, repo_name: str) -> list[ImageReference]:
        """Parse a GitHub Actions workflow file and return all image references.

        Extraction order:

        1. Collect top-level ``env:`` and ``workflow_dispatch`` input defaults
           into a resolution environment.
        2. For each job, merge job-level ``env:`` values.
        3. Emit references for ``container:`` (object or string), ``services:``,
           step ``uses: docker://…``, and ``run:`` docker commands.

        Args:
            file_path: Absolute path to the workflow YAML file.
            repo_name: Repository identifier stored in ``SourceLocation``.

        Returns:
            List of ``ImageReference`` objects, one per discovered image.
        """
        raw_text = file_path.read_text(encoding="utf-8")

        try:
            data: Any = yaml.safe_load(raw_text)
        except yaml.YAMLError:
            return []

        if not isinstance(data, dict):
            return []

        # --- Build top-level resolution environment ---
        global_env: dict[str, str] = {}

        top_env = data.get("env")
        if isinstance(top_env, dict):
            for k, v in top_env.items():
                if v is not None:
                    global_env[str(k)] = str(v)

        # workflow_dispatch inputs defaults
        on_block = data.get("on") or data.get(True)  # `on:` parsed as True by PyYAML
        if isinstance(on_block, dict):
            wd = on_block.get("workflow_dispatch")
            if isinstance(wd, dict):
                inputs = wd.get("inputs")
                if isinstance(inputs, dict):
                    for inp_name, inp_conf in inputs.items():
                        if isinstance(inp_conf, dict):
                            default = inp_conf.get("default")
                            if default is not None:
                                global_env[str(inp_name)] = str(default)

        refs: list[ImageReference] = []
        jobs: Any = data.get("jobs")
        if not isinstance(jobs, dict):
            return refs

        for _job_id, job_conf in jobs.items():
            if not isinstance(job_conf, dict):
                continue

            # Merge job-level env over global env
            job_env = dict(global_env)
            job_env_block = job_conf.get("env")
            if isinstance(job_env_block, dict):
                for k, v in job_env_block.items():
                    if v is not None:
                        job_env[str(k)] = str(v)

            # --- container: ---
            container = job_conf.get("container")
            if container is not None:
                if isinstance(container, dict):
                    raw_image = container.get("image")
                    if raw_image is not None:
                        raw_image = str(raw_image)
                        lineno = _find_line(raw_text, raw_image)
                        refs += self._make_image_ref(
                            raw_image,
                            job_env,
                            repo_name,
                            file_path,
                            lineno,
                            EdgeType.CONSUMES,
                            Confidence.HIGH,
                            {},
                        )
                elif isinstance(container, str):
                    lineno = _find_line(raw_text, container)
                    refs += self._make_image_ref(
                        container,
                        job_env,
                        repo_name,
                        file_path,
                        lineno,
                        EdgeType.CONSUMES,
                        Confidence.HIGH,
                        {},
                    )

            # --- services: ---
            services = job_conf.get("services")
            if isinstance(services, dict):
                for _svc_name, svc_conf in services.items():
                    if not isinstance(svc_conf, dict):
                        continue
                    raw_image = svc_conf.get("image")
                    if raw_image is not None:
                        raw_image = str(raw_image)
                        lineno = _find_line(raw_text, raw_image)
                        refs += self._make_image_ref(
                            raw_image,
                            job_env,
                            repo_name,
                            file_path,
                            lineno,
                            EdgeType.CONSUMES,
                            Confidence.HIGH,
                            {},
                        )

            # --- steps ---
            steps = job_conf.get("steps")
            if not isinstance(steps, list):
                continue

            for step in steps:
                if not isinstance(step, dict):
                    continue

                # uses: docker://IMAGE
                uses = step.get("uses")
                if isinstance(uses, str) and uses.startswith("docker://"):
                    raw_image = uses[len("docker://"):]
                    lineno = _find_line(raw_text, uses)
                    refs += self._make_image_ref(
                        raw_image,
                        job_env,
                        repo_name,
                        file_path,
                        lineno,
                        EdgeType.CONSUMES,
                        Confidence.HIGH,
                        {"uses": uses},
                    )

                # run: blocks (best-effort docker commands)
                run_block = step.get("run")
                if isinstance(run_block, str):
                    lineno = _find_line(raw_text, "run:")
                    refs += _scan_run_block(
                        run_block,
                        job_env,
                        repo_name,
                        file_path,
                        self.name,
                        raw_text,
                        lineno,
                    )

        return refs

    def _make_image_ref(
        self,
        raw_image: str,
        env: dict[str, str],
        repo_name: str,
        file_path: Path,
        lineno: int,
        relationship: EdgeType,
        base_confidence: Confidence,
        metadata: dict[str, Any],
    ) -> list[ImageReference]:
        """Build an ``ImageReference`` from a raw image string.

        Resolves ``${{ env.* }}`` expressions, validates the result, and
        returns a single-element list, or an empty list if the image string
        fails validation (and is not a template).

        Args:
            raw_image: The image string as it appears in the file.
            env: Resolution environment (merged global + job env).
            repo_name: Repository name for ``SourceLocation``.
            file_path: Absolute path to the workflow file.
            lineno: 1-indexed line number where the reference appears.
            relationship: ``CONSUMES`` or ``PRODUCES``.
            base_confidence: Starting confidence level (before resolution).
            metadata: Extra key-value pairs to attach to the reference.

        Returns:
            A list containing one ``ImageReference``, or empty if invalid.
        """
        originally_templated = is_template_string(raw_image)
        resolved, unresolved = _resolve_gh_expr(raw_image, env)

        still_has_template = is_template_string(resolved)
        still_unresolved = bool(unresolved)

        if not validate_image_ref(resolved) and not still_has_template:
            return []

        registry, name, tag, parse_unresolved = parse_image_string(resolved)
        all_unresolved = list(dict.fromkeys(unresolved + parse_unresolved))

        if still_unresolved:
            confidence = Confidence.LOW
        elif originally_templated:
            confidence = Confidence.MEDIUM
        else:
            confidence = base_confidence

        source = SourceLocation(
            repo=repo_name,
            file=str(file_path),
            line=lineno,
            parser=self.name,
        )

        return [
            ImageReference(
                raw=raw_image,
                registry=registry,
                name=name,
                tag=tag,
                source=source,
                relationship=relationship,
                confidence=confidence,
                unresolved_variables=all_unresolved,
                metadata=metadata,
            )
        ]
