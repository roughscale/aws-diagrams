"""CLI utilities for textual reporting on collected AWS topology data."""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple, TYPE_CHECKING

import click

try:
    from ..topology.schema import (
        AWSTopology,
        BaseResource,
        RelationshipType,
        ResourceType,
    )
    from ..topology.serializer import TopologyYAMLSerializer
    from ..views.view_engine import ViewDefinition, ViewEngine, ViewFilter, FilterType
    from ..transformers.awslabs_transformer_v2 import AWSLabsTransformerV2
    from ..utils.logger import get_logger
    from ..utils.eni_inference import (
        extract_elb_identifier,
        resolve_load_balancer_identifier,
    )
except ImportError:  # pragma: no cover - support running without package context
    from topology.schema import (
        AWSTopology,
        BaseResource,
        RelationshipType,
        ResourceType,
    )
    from topology.serializer import TopologyYAMLSerializer
    from views.view_engine import ViewDefinition, ViewEngine, ViewFilter, FilterType
    from transformers.awslabs_transformer_v2 import AWSLabsTransformerV2
    from utils.logger import get_logger
    from utils.eni_inference import (
        extract_elb_identifier,
        resolve_load_balancer_identifier,
    )

if TYPE_CHECKING:  # pragma: no cover - type checking only
    try:
        from ..transformers.graph_model import DiagramGraph, GraphNode
    except ImportError:  # pragma: no cover - fallback to direct import style
        from transformers.graph_model import DiagramGraph, GraphNode


@dataclass
class ENIAttachment:
    """Simple representation of an ENI attachment to a resource."""

    account_id: str
    region: str
    eni: BaseResource
    target: Optional[BaseResource]
    target_id: str
    component_id: Optional[str] = None
    component_hint: Optional[str] = None


@dataclass
class ComponentIndex:
    """Mapping of resources to logical components derived from the topology graph."""

    resource_to_component: Dict[str, str]
    component_labels: Dict[str, str]
    ordering: Dict[str, int]


logger = get_logger("report")


EMPTY_COMPONENT_INDEX = ComponentIndex({}, {}, {})


@dataclass
class RowEntry:
    """Renderable row for a single ENI attachment."""

    sort_key: Tuple[int, str, str, str]
    account_id: str
    region: str
    display_label: str
    resource_type: str
    eni_id: str
    eni_name: str
    private_ip: str
    public_ip: str


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
        raise click.ClickException(
            f"Failed to load topology file '{topology_path}': {error}"
        ) from error

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

    attachments = _deduplicate_unknown_targets(attachments)

    component_index = _build_component_index(
        topology, account_filter, region_filter, vpc_filter
    )
    _annotate_attachments_with_components(attachments, component_index)
    rows = _build_attachment_rows(attachments, component_index)

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
                    attachments_by_eni[relationship.source_id].add(
                        relationship.target_id
                    )

                if relationship.target_id in eni_ids:
                    attachments_by_eni[relationship.target_id].add(
                        relationship.source_id
                    )

            for resource in resources.values():
                if resource.resource_type != ResourceType.NETWORK_INTERFACE:
                    continue

                if vpc_filter_set:
                    vpc_id = (resource.properties or {}).get("vpc_id")
                    if vpc_id not in vpc_filter_set:
                        continue

                attached_ids: Set[str] = set(
                    attachments_by_eni.get(resource.resource_id, set())
                )
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

                normalized_targets: List[Tuple[str, Optional[BaseResource]]] = []
                seen_targets: Set[str] = set()

                for raw_target_id in sorted(attached_ids):
                    normalized_id = _normalize_target_identifier(
                        raw_target_id, resources
                    )
                    if normalized_id in seen_targets:
                        continue
                    seen_targets.add(normalized_id)
                    normalized_targets.append(
                        (normalized_id, resources.get(normalized_id))
                    )

                filtered_targets = []
                for target_id, target_resource in normalized_targets:
                    if target_id == resource.resource_id:
                        continue
                    if (
                        target_resource
                        and target_resource.resource_type
                        in {ResourceType.NETWORK_INTERFACE, ResourceType.SECURITY_GROUP}
                    ):
                        continue
                    filtered_targets.append((target_id, target_resource))

                targets_to_emit = filtered_targets or normalized_targets

                for target_id, target_resource in targets_to_emit:
                    yield ENIAttachment(
                        account_id=account_id,
                        region=region_name,
                        eni=resource,
                        target=target_resource,
                        target_id=target_id,
                    )


def _build_component_index(
    topology: AWSTopology,
    account_filter: Optional[Iterable[str]],
    region_filter: Optional[Iterable[str]],
    vpc_filter: Optional[Iterable[str]],
) -> ComponentIndex:
    """Construct a component index by leveraging the existing diagram graph pipeline."""

    try:
        view_engine = ViewEngine(topology)
    except Exception as error:  # pragma: no cover - defensive path
        logger.debug(
            "Unable to create view engine for ENI report: %s", error, exc_info=True
        )
        return EMPTY_COMPONENT_INDEX

    filters: List[ViewFilter] = []

    if account_filter:
        filters.append(ViewFilter(FilterType.ACCOUNT, sorted(set(account_filter))))
    if region_filter:
        filters.append(ViewFilter(FilterType.REGION, sorted(set(region_filter))))
    if vpc_filter:
        filters.append(ViewFilter(FilterType.VPC, sorted(set(vpc_filter))))

    view_definition = ViewDefinition(
        name="ENI Report View",
        description="Scoped topology view used for ENI report grouping",
        filters=filters,
    )

    try:
        view = view_engine.create_view(view_definition)
        transformer = AWSLabsTransformerV2(view)
        graph = transformer.create_graph()
    except Exception as error:  # pragma: no cover - defensive path
        logger.debug(
            "Unable to build component graph for ENI report: %s", error, exc_info=True
        )
        return EMPTY_COMPONENT_INDEX

    try:
        return _derive_component_index_from_graph(graph)
    except Exception as error:  # pragma: no cover - defensive path
        logger.debug(
            "Failed to derive component index from graph: %s", error, exc_info=True
        )
        return EMPTY_COMPONENT_INDEX


def _derive_component_index_from_graph(graph: "DiagramGraph") -> ComponentIndex:
    """Compute connected components from the diagram graph for grouping."""

    adjacency: Dict[str, Set[str]] = defaultdict(set)

    for node_id in graph.nodes:
        adjacency.setdefault(node_id, set())

    for container_id in graph.containers:
        adjacency.setdefault(container_id, set())

    for edge in graph.edges.values():
        adjacency[edge.source_id].add(edge.target_id)
        adjacency[edge.target_id].add(edge.source_id)

    for node in graph.nodes.values():
        for child_id in node.children_ids:
            adjacency[node.id].add(child_id)
            adjacency[child_id].add(node.id)
        if node.parent_id:
            adjacency[node.id].add(node.parent_id)
            adjacency[node.parent_id].add(node.id)

    for container in graph.containers.values():
        for child_id in container.children_ids:
            adjacency[container.id].add(child_id)
            adjacency[child_id].add(container.id)

    visited: Set[str] = set()
    component_map: Dict[str, str] = {}
    component_labels: Dict[str, str] = {}

    component_counter = 0
    for start_id in sorted(adjacency.keys()):
        if start_id in visited:
            continue

        stack = [start_id]
        cluster_ids: Set[str] = set()

        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            cluster_ids.add(current)
            for neighbor in adjacency.get(
                current, ()
            ):  # pragma: no branch - simple iteration
                if neighbor not in visited:
                    stack.append(neighbor)

        resource_ids = [rid for rid in cluster_ids if rid in graph.nodes]
        if not resource_ids:
            continue

        component_counter += 1
        component_id = f"component-{component_counter}"

        for rid in resource_ids:
            component_map[rid] = component_id

        nodes = [graph.nodes[rid] for rid in resource_ids]
        component_labels[component_id] = _select_component_label(nodes)

    sorted_components = sorted(
        component_labels.items(),
        key=lambda item: (item[1].lower(), item[0]),
    )
    ordering = {
        component_id: index for index, (component_id, _) in enumerate(sorted_components)
    }

    return ComponentIndex(component_map, component_labels, ordering)


def _select_component_label(nodes: Sequence["GraphNode"]) -> str:
    """Select a human-friendly label to represent a component."""

    priority_order = [
        ResourceType.ECS_SERVICE,
        ResourceType.ECS_CLUSTER,
        ResourceType.LOAD_BALANCER,
        ResourceType.LAMBDA_FUNCTION,
        ResourceType.RDS_INSTANCE,
        ResourceType.RDS_CLUSTER,
        ResourceType.ELASTICACHE_CLUSTER,
        ResourceType.OPENSEARCH_DOMAIN,
        ResourceType.REDSHIFT_CLUSTER,
        ResourceType.EC2_INSTANCE,
    ]

    for resource_type in priority_order:
        candidates = [
            node.label
            for node in nodes
            if node.resource_type == resource_type and node.label
        ]
        if candidates:
            return sorted(candidates, key=lambda value: value.lower())[0]

    fallback_labels = [node.label for node in nodes if node.label]
    if fallback_labels:
        return sorted(fallback_labels, key=lambda value: value.lower())[0]

    return nodes[0].id if nodes else "component"


def _annotate_attachments_with_components(
    attachments: Sequence[ENIAttachment],
    component_index: ComponentIndex,
) -> None:
    """Populate component metadata on each attachment for downstream sorting."""

    for attachment in attachments:
        candidate_ids: List[str] = []
        if attachment.target:
            candidate_ids.append(attachment.target.resource_id)
        candidate_ids.append(attachment.eni.resource_id)

        for candidate_id in candidate_ids:
            component_id = component_index.resource_to_component.get(candidate_id)
            if component_id:
                attachment.component_id = component_id
                attachment.component_hint = component_index.component_labels.get(
                    component_id
                )
                break

        if not attachment.component_hint:
            if attachment.target:
                attachment.component_hint = _format_resource_label(attachment.target)
            else:
                attachment.component_hint = (
                    attachment.eni.name or attachment.eni.resource_id
                )


def _build_attachment_rows(
    attachments: Sequence[ENIAttachment],
    component_index: ComponentIndex,
) -> List[Sequence[str]]:
    """Return per-ENI rows with component-aware ordering."""

    entries: List[RowEntry] = [
        _build_row_entry(attachment, component_index) for attachment in attachments
    ]
    entries.sort(key=lambda entry: entry.sort_key)

    return [
        [
            entry.account_id,
            entry.region,
            entry.display_label,
            entry.resource_type,
            entry.eni_id,
            entry.eni_name,
            entry.private_ip,
            entry.public_ip,
        ]
        for entry in entries
    ]


def _build_row_entry(
    attachment: ENIAttachment, component_index: ComponentIndex
) -> RowEntry:
    """Construct a single table row for an ENI attachment."""

    target = attachment.target
    if target:
        resource_label = _format_resource_label(target)
        resource_type = target.resource_type.value
        primary_id = target.resource_id
    else:
        resource_label = attachment.target_id or attachment.eni.resource_id
        resource_type = "unknown"
        primary_id = attachment.eni.resource_id

    component_id = (
        attachment.component_id
        or component_index.resource_to_component.get(primary_id)
        or component_index.resource_to_component.get(attachment.eni.resource_id)
    )
    component_label = (
        component_index.component_labels.get(component_id)
        if component_id
        else attachment.component_hint or resource_label
    )

    component_rank = component_index.ordering.get(
        component_id, len(component_index.ordering)
    )
    label_for_sort = (component_label or resource_label).lower()
    resource_sort = resource_label.lower()
    eni_sort = attachment.eni.resource_id

    private_ip = _attachment_private_ip(attachment)
    public_ip = _attachment_public_ip(attachment)

    return RowEntry(
        sort_key=(component_rank, label_for_sort, resource_sort, eni_sort),
        account_id=attachment.account_id,
        region=attachment.region,
        display_label=resource_label,
        resource_type=resource_type,
        eni_id=attachment.eni.resource_id,
        eni_name=attachment.eni.name or "",
        private_ip=private_ip,
        public_ip=public_ip,
    )


def _normalize_target_identifier(
    target_id: str, resources: Dict[str, BaseResource]
) -> str:
    """Return a canonical target identifier for attachment de-duplication."""

    if target_id in resources:
        return target_id

    fragment = extract_elb_identifier(target_id)
    if fragment:
        resolved = resolve_load_balancer_identifier(fragment, resources)
        if resolved:
            return resolved

    return target_id


def _deduplicate_unknown_targets(
    attachments: Sequence[ENIAttachment],
) -> List[ENIAttachment]:
    """Remove unknown targets when a canonical resource is available for the ENI."""

    grouped: Dict[str, List[ENIAttachment]] = defaultdict(list)
    for attachment in attachments:
        grouped[attachment.eni.resource_id].append(attachment)

    result: List[ENIAttachment] = []
    for eni_id, group in grouped.items():
        has_known_target = any(
            att.target
            and att.target.resource_type
            not in {ResourceType.NETWORK_INTERFACE, ResourceType.SECURITY_GROUP}
            for att in group
        )
        if not has_known_target:
            result.extend(group)
            continue

        for att in group:
            if not att.target:
                continue
            if att.target.resource_type == ResourceType.NETWORK_INTERFACE:
                continue
            result.append(att)

    return result


def _attachment_private_ip(attachment: ENIAttachment) -> str:
    """Extract the primary private IP from an attachment."""

    eni_props = attachment.eni.properties or {}
    value = eni_props.get("private_ip")
    return str(value) if value else ""


def _attachment_public_ip(attachment: ENIAttachment) -> str:
    """Extract the public IP/DNS association from an attachment."""

    eni_props = attachment.eni.properties or {}
    association: Dict[str, str] = eni_props.get("association") or {}
    value = association.get("PublicIp") or association.get("PublicDnsName")
    return str(value) if value else ""


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
    lb_identifier = extract_elb_identifier(description)
    if lb_identifier:
        lb_id = resolve_load_balancer_identifier(lb_identifier, resources)
        if lb_id:
            inferred.add(lb_id)

    owner = str(attachment.get("InstanceOwnerId") or "").lower()
    allowed_types = OWNER_SERVICE_TYPE_HINTS.get(owner)

    tags = (eni.metadata.tags if eni.metadata else {}) or {}
    for tag_value in tags.values():
        match = _match_service_identifier(tag_value, service_index, allowed_types)
        if match:
            inferred.add(match)

    service_match = _match_service_identifier(
        description, service_index, allowed_types, allow_substring=True
    )
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
                if len(normalized) < 4:
                    continue
                index.setdefault(normalized, (resource_id, resource.resource_type))

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
            if (
                len(normalized) >= 4
                and normalized in text
                and _is_allowed_type(match[1], allowed_types)
            ):
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
