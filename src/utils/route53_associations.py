"""Helpers for associating Route 53 records with discovered resources."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set
import logging

try:
    from ..topology.schema import (
        AccountData,
        BaseResource,
        AWSTopology,
        Relationship,
        RelationshipType,
        ResourceType,
    )
except ImportError:
    from topology.schema import (
        AccountData,
        BaseResource,
        AWSTopology,
        Relationship,
        RelationshipType,
        ResourceType,
    )

logger = logging.getLogger(__name__)

GLOBAL_REGION = "aws-global"


def associate_route53_records(topology: AWSTopology) -> int:
    """
    Link Route 53 records discovered in the topology to known resources.

    Returns the number of new relationships that were created.
    """
    relationships_created = 0

    for account in topology.organization.accounts.values():
        route53_region = account.regions.get(GLOBAL_REGION)
        if not route53_region:
            continue

        record_resources = [
            res
            for res in route53_region.resources.values()
            if res.resource_type == ResourceType.ROUTE53_RECORD
        ]
        if not record_resources:
            continue

        hostname_index = _build_hostname_index(account)
        if not hostname_index:
            continue

        for record in record_resources:
            target_hostnames = _extract_target_hostnames(record)
            matched_resources: Set[str] = set()

            for hostname in target_hostnames:
                resource_id = hostname_index.get(hostname)
                if not resource_id:
                    continue
                if not _relationship_exists(
                    route53_region.relationships,
                    record.resource_id,
                    resource_id,
                ):
                    route53_region.relationships.append(
                        Relationship(
                            source_id=record.resource_id,
                            target_id=resource_id,
                            relationship_type=RelationshipType.CONNECTS_TO,
                        )
                    )
                    relationships_created += 1
                matched_resources.add(resource_id)

            if matched_resources:
                record.properties["associated_resource_ids"] = sorted(matched_resources)

    return relationships_created


def _relationship_exists(
    relationships: Iterable[Relationship],
    source_id: str,
    target_id: str,
) -> bool:
    return any(
        rel.source_id == source_id
        and rel.target_id == target_id
        and rel.relationship_type == RelationshipType.CONNECTS_TO
        for rel in relationships
    )


def _build_hostname_index(account: AccountData) -> Dict[str, str]:
    index: Dict[str, str] = {}
    for region in account.regions.values():
        for resource in region.resources.values():
            for hostname in _get_resource_hostnames(resource):
                normalized = _normalize_hostname(hostname)
                if not normalized:
                    continue
                index.setdefault(normalized, resource.resource_id)
    return index


def _get_resource_hostnames(resource: BaseResource) -> List[str]:
    props = resource.properties or {}
    hostnames: List[str] = []

    if resource.resource_type == ResourceType.CLOUDFRONT_DISTRIBUTION:
        domain = props.get("domain_name")
        if domain:
            hostnames.append(domain)
        for alias in props.get("aliases") or []:
            hostnames.append(alias)

    elif resource.resource_type == ResourceType.GLOBAL_ACCELERATOR:
        dns_name = props.get("dns_name")
        if dns_name:
            hostnames.append(dns_name)

    elif resource.resource_type == ResourceType.LOAD_BALANCER:
        dns_name = props.get("dns_name")
        if dns_name:
            hostnames.append(dns_name)

    elif resource.resource_type == ResourceType.VPC_ENDPOINT:
        for entry in props.get("dns_entries") or []:
            dns_name = entry.get("DnsName") or entry.get("dns_name")
            if dns_name:
                hostnames.append(dns_name)

    return hostnames


def _extract_target_hostnames(record: BaseResource) -> List[str]:
    props = record.properties or {}
    candidates: List[str] = []

    alias = props.get("alias_target") or {}
    dns_name = alias.get("dns_name") if alias else None
    if not dns_name and alias:
        dns_name = alias.get("DNSName")
    if dns_name:
        candidates.append(dns_name)

    target_list: Iterable[str] = props.get("target_dns_names") or []
    resource_records: Iterable = props.get("resource_records") or []
    if not target_list and props.get("type") in {"A", "AAAA", "CNAME"}:
        target_list = _extract_from_resource_records(resource_records)

    for value in target_list:
        candidates.append(value)

    normalized = [
        host for host in (_normalize_hostname(c) for c in candidates) if host
    ]
    return list(dict.fromkeys(normalized))


def _extract_from_resource_records(records: Iterable) -> List[str]:
    values: List[str] = []
    for entry in records:
        if isinstance(entry, dict):
            candidate = entry.get("Value")
        else:
            candidate = entry
        if not candidate:
            continue
        if any(ch.isalpha() for ch in candidate):
            values.append(candidate)
    return values


def _normalize_hostname(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = value.strip().lower().rstrip(".")
    return normalized or None
