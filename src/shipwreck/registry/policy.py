"""Registry access policy evaluation."""

from __future__ import annotations

import warnings

from shipwreck.config import RegistryConfig, RegistryPolicy


def should_query_registry(
    registry_url: str,
    config_registries: list[RegistryConfig],
    policy: RegistryPolicy,
    non_interactive: bool = False,
) -> bool:
    """Determine if we should query a given registry.

    Rules (evaluated in order):

    1. If the registry is listed in *config_registries* with ``internal=True``
       → always allowed (return ``True``).
    2. If the registry appears in ``policy.external_allowlist``
       → allowed (return ``True``).
    3. If *non_interactive* is ``True``
       → not allowed (emit a warning and return ``False``).
    4. If ``policy.prompt_external`` is ``True``
       → return ``True`` so the caller can prompt the user and decide.
    5. Otherwise → not allowed (return ``False``).

    Args:
        registry_url: The registry hostname being evaluated (e.g. ``"docker.io"``).
        config_registries: List of :class:`~shipwreck.config.RegistryConfig` entries
            from the shipwreck configuration file.
        policy: The active :class:`~shipwreck.config.RegistryPolicy`.
        non_interactive: When ``True``, external registries that are not
            allowlisted are skipped with a warning rather than prompting.

    Returns:
        ``True`` if the registry should be queried; ``False`` otherwise.
    """
    # Rule 1 — explicitly configured internal registries are always allowed.
    for reg in config_registries:
        if reg.url == registry_url and reg.internal:
            return True

    # Rule 2 — explicit external allowlist.
    if registry_url in policy.external_allowlist:
        return True

    # Rule 3 — non-interactive mode: skip and warn.
    if non_interactive:
        warnings.warn(
            f"Skipping external registry '{registry_url}' in non-interactive mode.",
            stacklevel=2,
        )
        return False

    # Rule 4 — caller should prompt; we signal allowed so it can proceed.
    if policy.prompt_external:
        return True

    # Rule 5 — default deny.
    return False
