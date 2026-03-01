"""Git operations for Shipwreck — clone and pull with local caching."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()


class GitError(Exception):
    """Raised when a git subprocess operation fails."""


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a git command, raising GitError on failure.

    Args:
        args: Command and arguments to run.
        cwd: Working directory for the command.

    Returns:
        CompletedProcess result.

    Raises:
        GitError: If the process exits with a non-zero return code.
    """
    result = subprocess.run(  # noqa: S603
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        cmd = " ".join(args)
        raise GitError(f"Command failed: {cmd}\nstdout: {result.stdout}\nstderr: {result.stderr}")
    return result


def clone_repo(url: str, dest: Path, ref: str = "main") -> None:
    """Clone a git repository to a destination directory.

    Args:
        url: Git remote URL.
        dest: Local destination path (must not exist yet).
        ref: Branch or ref to check out after cloning.

    Raises:
        GitError: If the clone fails.
    """
    logger.info("Git clone: %s → %s (ref=%s)", url, dest, ref)
    console.print(f"[blue]Cloning[/blue] {url} → {dest}")
    _run(["git", "clone", "--depth=1", "--branch", ref, url, str(dest)])


def pull_repo(repo_path: Path, ref: str = "main") -> None:
    """Pull the latest changes for an already-cloned repository.

    Args:
        repo_path: Local path to the git repository.
        ref: Branch to pull.

    Raises:
        GitError: If the pull fails.
    """
    logger.info("Git pull: %s (ref=%s)", repo_path, ref)
    console.print(f"[blue]Pulling[/blue] {repo_path}")
    _run(["git", "fetch", "--depth=1", "origin", ref], cwd=repo_path)
    _run(["git", "checkout", ref], cwd=repo_path)
    _run(["git", "reset", "--hard", f"origin/{ref}"], cwd=repo_path)


def ensure_repo(
    url: str,
    cache_dir: Path,
    name: str,
    ref: str = "main",
    no_pull: bool = False,
) -> Path:
    """Ensure a git repository is cloned and up to date in the cache directory.

    If the repo already exists locally and ``no_pull`` is False, it will be
    updated. If it does not exist, it will be cloned.

    Args:
        url: Git remote URL.
        cache_dir: Parent directory for all cached repos.
        name: Short name for this repository (becomes a subdirectory).
        ref: Branch or ref to check out.
        no_pull: If True, skip pulling even if the repo exists.

    Returns:
        Path to the local repository checkout.

    Raises:
        GitError: If any git operation fails.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    repo_path = cache_dir / name

    if repo_path.exists():
        if not no_pull:
            pull_repo(repo_path, ref=ref)
        else:
            console.print(f"[yellow]Using cached[/yellow] {repo_path} (--no-pull)")
    else:
        clone_repo(url, repo_path, ref=ref)

    return repo_path
