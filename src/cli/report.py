"""CLI utilities for textual reporting on collected AWS topology data."""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import click

try:
    from ..topology.schema import (
        AWSTopology,
        BaseResource,
        RelationshipType,
        ResourceType,
    )
    from ..topology.serializer import TopologyYAMLSerializer
except ImportError:  # pragma: no cover - support running without package context
    from topology.schema import (
        AWSTopology,
        BaseResource,
        RelationshipType,
        ResourceType,
    )
    from topology.serializer import TopologyYAMLSerializer


@dataclass
class ENIAttachment:
    """Simple representation of an ENI attachment to a resource."""

    account_id: str
    region: str
    eni: BaseResource
    target: Optional[BaseResource]
    target_id: str


@click.group()
@click.option(
    "--topology-file",
    "topology_path",
    type=click.Path(exists=True, path_type=Path),
    default=Path("topology.yaml"),
    show_default=True,
    help="Path to a collected topology YAML file.",
)
@click.pass_context
def cli(ctx: click.Context, topology_path: Path) -> None:
    """Report on collected AWS topology data using text output."""

    ctx.ensure_object(dict)
    ctx.obj["topology_path"] = topology_path

    try:
        serializer = TopologyYAMLSerializer()
        topology = serializer.load_from_file(topology_path)
    except Exception as error:  # pragma: no cover - defensive path
        raise click.ClickException(f"Failed to load topology file '{topology_path}': {error}") from error

    ctx.obj["topology"] = topology


@cli.command(name="eni")
@click.option(
    "--account-id",
    "account_ids",
    multiple=True,
    help="Only include ENIs from the specified AWS account ID(s).",
)
@click.option(
    "--region",
    "regions",
    multiple=True,
    help="Only include ENIs from the specified AWS region(s).",
)
@click.option(
    "--vpc-id",
    "vpc_ids",
    multiple=True,
    help="Only include ENIs associated with the specified VPC ID(s).",
)
@click.option(
    "--output-format",
    "output_format",
    type=click.Choice(["table", "csv"], case_sensitive=False),
    default="table",
    show_default=True,
    help="Render as an ASCII table (default) or CSV rows.",
)
@click.pass_context
def eni_report(
    ctx: click.Context,
    account_ids: Sequence[str],
    regions: Sequence[str],
    vpc_ids: Sequence[str],
    output_format: str,
) -> None:
    """Render a table of resources that have Elastic Network Interfaces attached."""

    topology: AWSTopology = ctx.obj["topology"]
    account_filter = set(account_ids) if account_ids else None
    region_filter = set(regions) if regions else None

    vpc_filter = set(vpc_ids) if vpc_ids else None

    attachments = list(
        _iter_eni_attachments(topology, account_filter, region_filter, vpc_filter)
    )
    if not attachments:
        click.echo("No ENI-attached resources were found in the provided topology.")
        return

    headers = [
        "Account",
        "Region",
        "Resource",
        "Resource Type",
        "ENI ID",
        "ENI Name",
        "Private IP",
        "Public IP",
    ]

    rows: List[Sequence[str]] = []
    for attachment in attachments:
        eni_props = attachment.eni.properties or {}
        association: Dict[str, str] = eni_props.get("association") or {}
        private_ip = eni_props.get("private_ip") or ""
        public_ip = association.get("PublicIp") or association.get("PublicDnsName") or ""

        target = attachment.target
        if target:
            resource_label = _format_resource_label(target)
            resource_type = target.resource_type.value
        else:
            resource_label = attachment.target_id
            resource_type = "unknown"

        rows.append(
            [
                attachment.account_id,
                attachment.region,
                resource_label,
                resource_type,
                attachment.eni.resource_id,
                attachment.eni.name or "",
                private_ip,
                public_ip,
            ]
        )

    formatter = _format_csv if output_format.lower() == "csv" else _format_table
    click.echo(formatter(headers, rows))


def _iter_eni_attachments(
    topology: AWSTopology,
    account_filter: Optional[Iterable[str]],
    region_filter: Optional[Iterable[str]],
    vpc_filter: Optional[Iterable[str]],
) -> Iterable[ENIAttachment]:
    """Yield ENI attachments that satisfy the provided filters."""

    account_filter_set = set(account_filter) if account_filter else None
    region_filter_set = set(region_filter) if region_filter else None
    vpc_filter_set = set(vpc_filter) if vpc_filter else None

    for account_id, account in topology.organization.accounts.items():
        if account_filter_set and account_id not in account_filter_set:
            continue

        for region_name, region in account.regions.items():
            if region_filter_set and region_name not in region_filter_set:
                continue

            resources = region.resources
            if not resources:
                continue

            service_index = _build_service_identifier_index(resources)

            eni_ids = {
                resource_id
                for resource_id, resource in resources.items()
                if resource.resource_type == ResourceType.NETWORK_INTERFACE
            }

            attachments_by_eni: Dict[str, Set[str]] = defaultdict(set)
            for relationship in region.relationships:
                if relationship.relationship_type not in {
                    RelationshipType.ATTACHED_TO,
                    RelationshipType.MEMBER_OF,
                }:
                    continue

                if relationship.source_id in eni_ids:
                    attachments_by_eni[relationship.source_id].add(relationship.target_id)

                if relationship.target_id in eni_ids:
                    attachments_by_eni[relationship.target_id].add(relationship.source_id)

            for resource in resources.values():
                if resource.resource_type != ResourceType.NETWORK_INTERFACE:
                    continue

                if vpc_filter_set:
                    vpc_id = (resource.properties or {}).get("vpc_id")
                    if vpc_id not in vpc_filter_set:
                        continue

                attached_ids: Set[str] = set(attachments_by_eni.get(resource.resource_id, set()))
                if not attached_ids:
                    attached_ids.update(
                        _infer_targets_from_properties(
                            resource,
                            resources,
                            service_index,
                        )
                    )

                if not attached_ids:
                    continue

                for target_id in sorted(attached_ids):
                    yield ENIAttachment(
                        account_id=account_id,
                        region=region_name,
                        eni=resource,
                        target=resources.get(target_id),
                        target_id=target_id,
                    )


def _format_resource_label(resource: BaseResource) -> str:
    """Create a friendly label for a resource using its name when available."""

    if resource.name and resource.name != resource.resource_id:
        return f"{resource.name} ({resource.resource_id})"
    return resource.resource_id


def _format_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Format rows as a simple left-aligned table."""

    column_widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            column_widths[index] = max(column_widths[index], len(cell))

    header_line = " | ".join(
        header.ljust(column_widths[index]) for index, header in enumerate(headers)
    )
    separator_line = "-+-".join("-" * width for width in column_widths)

    data_lines = [
        " | ".join(cell.ljust(column_widths[index]) for index, cell in enumerate(row))
        for row in rows
    ]

    return "\n".join([header_line, separator_line, *data_lines])


def _format_csv(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Format rows as comma-separated values."""

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().strip()


def _infer_targets_from_properties(
    eni: BaseResource,
    resources: Dict[str, BaseResource],
    service_index: Dict[str, Tuple[str, ResourceType]],
) -> Set[str]:
    """Infer attached resource identifiers from ENI properties when relationships are absent."""

    inferred: Set[str] = set()
    eni_props = eni.properties or {}

    attachment = eni_props.get("attachment") or {}
    instance_id = attachment.get("InstanceId")
    resolved = _resolve_resource_identifier(instance_id, resources)
    if resolved:
        inferred.add(resolved)

    description = str(eni_props.get("description") or "")
    lb_identifier = _extract_elb_identifier(description)
    if lb_identifier:
        lb_id = _resolve_load_balancer_identifier(lb_identifier, resources)
        if lb_id:
            inferred.add(lb_id)

    owner = str(attachment.get("InstanceOwnerId") or "").lower()
    allowed_types = OWNER_SERVICE_TYPE_HINTS.get(owner)

    tags = (eni.metadata.tags if eni.metadata else {}) or {}
    for tag_value in tags.values():
        match = _match_service_identifier(tag_value, service_index, allowed_types)
        if match:
            inferred.add(match)

    service_match = _match_service_identifier(description, service_index, allowed_types, allow_substring=True)
    if service_match:
        inferred.add(service_match)

    return inferred


def _resolve_resource_identifier(
    identifier: Optional[str],
    resources: Dict[str, BaseResource],
) -> Optional[str]:
    if not identifier:
        return None

    if identifier in resources:
        return identifier

    for resource in resources.values():
        if resource.resource_id.endswith(identifier):
            return resource.resource_id
        if resource.name and resource.name == identifier:
            return resource.resource_id

    return None


def _extract_elb_identifier(description: str) -> Optional[str]:
    if not description:
        return None

    prefix = "ELB "
    if description.startswith(prefix):
        return description[len(prefix):].strip()

    return None


def _resolve_load_balancer_identifier(
    identifier: str,
    resources: Dict[str, BaseResource],
) -> Optional[str]:
    for resource in resources.values():
        if resource.resource_type != ResourceType.LOAD_BALANCER:
            continue

        if identifier in resource.resource_id:
            return resource.resource_id

        name = resource.name or ""
        if name and identifier.endswith(name):
            return resource.resource_id

        lb_type = (resource.properties or {}).get("type")
        if lb_type and identifier.startswith(f"{lb_type}/") and name and name in identifier:
            return resource.resource_id

    return None


OWNER_SERVICE_TYPE_HINTS: Dict[str, Set[ResourceType]] = {
    "amazon-rds": {ResourceType.RDS_INSTANCE, ResourceType.RDS_CLUSTER},
    "amazon-elasticache": {ResourceType.ELASTICACHE_CLUSTER},
    "amazon-opensearch-service": {ResourceType.OPENSEARCH_DOMAIN},
    "amazon-redshift": {ResourceType.REDSHIFT_CLUSTER},
}


SERVICE_PROPERTY_KEYS: Dict[ResourceType, Sequence[str]] = {
    ResourceType.RDS_INSTANCE: ["instance_id", "db_instance_identifier"],
    ResourceType.RDS_CLUSTER: ["cluster_id", "db_cluster_identifier"],
    ResourceType.ELASTICACHE_CLUSTER: ["cluster_id", "replication_group_id"],
    ResourceType.OPENSEARCH_DOMAIN: ["domain_name"],
    ResourceType.REDSHIFT_CLUSTER: ["cluster_identifier"],
    ResourceType.LOAD_BALANCER: ["load_balancer_name", "loadbalancername"],
}


def _build_service_identifier_index(
    resources: Dict[str, BaseResource],
) -> Dict[str, Tuple[str, ResourceType]]:
    """Create lookup of known service identifiers to resource IDs."""

    index: Dict[str, Tuple[str, ResourceType]] = {}

    for resource_id, resource in resources.items():
        values: Set[str] = set()
        values.add(resource.resource_id)
        if resource.name:
            values.add(resource.name)

        props = resource.properties or {}
        for key in SERVICE_PROPERTY_KEYS.get(resource.resource_type, ()):  # type: ignore[arg-type]
            value = props.get(key)
            if value:
                values.add(str(value))

        if resource.arn:
            values.add(resource.arn)

        for value in values:
            for normalized in _normalize_identifier_variants(value):
                index[normalized] = (resource_id, resource.resource_type)

    return index


def _match_service_identifier(
    value: Optional[str],
    service_index: Dict[str, Tuple[str, ResourceType]],
    allowed_types: Optional[Set[ResourceType]] = None,
    *,
    allow_substring: bool = False,
) -> Optional[str]:
    if not value:
        return None

    if allow_substring:
        text = value.lower()
        for token in re.split(r"[^a-z0-9:_/-]+", text):
            if not token:
                continue
            for normalized in _normalize_identifier_variants(token):
                match = service_index.get(normalized)
                if match and _is_allowed_type(match[1], allowed_types):
                    return match[0]
        for normalized, match in service_index.items():
            if len(normalized) >= 4 and normalized in text and _is_allowed_type(match[1], allowed_types):
                return match[0]
        return None

    for normalized in _normalize_identifier_variants(value):
        match = service_index.get(normalized)
        if match and _is_allowed_type(match[1], allowed_types):
            return match[0]
    return None


def _normalize_identifier_variants(value: str) -> Set[str]:
    cleaned = value.strip().lower()
    variants: Set[str] = set()
    if not cleaned:
        return variants

    candidates = {cleaned}
    for sep in (":", "/", " ", "|"):
        parts = cleaned.split(sep)
        for part in parts:
            part = part.strip()
            if part:
                candidates.add(part)
    if "db:" in cleaned:
        candidates.add(cleaned.split("db:", 1)[-1])

    for candidate in candidates:
        candidate = candidate.strip()
        if len(candidate) >= 3:
            variants.add(candidate)

    return variants


def _is_allowed_type(
    resource_type: ResourceType,
    allowed_types: Optional[Set[ResourceType]],
) -> bool:
    if not allowed_types:
        return True
    return resource_type in allowed_types


__all__ = [
    "cli",
]
