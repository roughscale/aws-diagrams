"""Helpers for inferring ENI ownership from descriptive metadata."""

from __future__ import annotations

from typing import Dict, Optional

try:
    from ..topology.schema import BaseResource, ResourceType
except ImportError:  # pragma: no cover - support running outside package
    from topology.schema import BaseResource, ResourceType


def extract_elb_identifier(description: Optional[str]) -> Optional[str]:
    """Return the ELB identifier fragment from an ENI description, if present."""

    if not description:
        return None

    prefix = "ELB "
    if description.startswith(prefix):
        return description[len(prefix) :].strip()
    return None


def resolve_load_balancer_identifier(
    identifier: str,
    resources: Dict[str, BaseResource],
) -> Optional[str]:
    """Map an ELB identifier fragment to the canonical load balancer resource ID."""

    normalized = identifier.strip()
    if not normalized:
        return None

    parts = normalized.split("/")
    primary_path = "/".join(parts[:3]) if len(parts) >= 3 else normalized

    for resource in resources.values():
        if resource.resource_type != ResourceType.LOAD_BALANCER:
            continue

        resource_id = resource.resource_id or ""
        if "loadbalancer/" not in resource_id:
            continue

        _, _, path = resource_id.partition("loadbalancer/")
        lb_path = path.strip()
        if not lb_path:
            continue

        if primary_path == lb_path:
            return resource_id

        if normalized.startswith(lb_path + "/"):
            return resource_id

    return None
