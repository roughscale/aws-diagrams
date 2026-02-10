"""Route 53 collector for hosted zones and record sets."""

from typing import Any, Dict, List, Optional, Set

try:
    from .base_collector import BaseCollector, logger
    from ..topology.schema import (
        BaseResource,
        Relationship,
        RelationshipType,
        ResourceLocation,
        ResourceType,
    )
except ImportError:
    from collectors.base_collector import BaseCollector, logger
    from topology.schema import (
        BaseResource,
        Relationship,
        RelationshipType,
        ResourceLocation,
        ResourceType,
    )


class Route53Collector(BaseCollector):
    """Collector for Route 53 hosted zones and records."""

    GLOBAL_REGION = "aws-global"
    CONTROL_PLANE_REGION = "us-east-1"

    @property
    def supported_resource_types(self) -> Set[ResourceType]:
        return {
            ResourceType.ROUTE53_HOSTED_ZONE,
            ResourceType.ROUTE53_RECORD,
        }

    @property
    def required_permissions(self) -> List[str]:
        return [
            "route53:ListHostedZones",
            "route53:ListResourceRecordSets",
            "route53:ListTagsForResource",
        ]

    def collect_resources(self) -> None:
        if self.region != self.GLOBAL_REGION:
            logger.info(
                "Route 53 is a global service; skipping regional collection for %s",
                self.region,
            )
            return

        client = self.session.client("route53", region_name=self.CONTROL_PLANE_REGION)
        marker: Optional[str] = None

        allowed_vpc_ids: Optional[Set[str]] = set(self.vpc_ids) if self.vpc_ids else None

        while True:
            kwargs: Dict[str, Any] = {}
            if marker:
                kwargs["Marker"] = marker

            response = self._make_api_call(client, "list_hosted_zones", **kwargs)
            if not response:
                break

            for zone in response.get("HostedZones", []):
                self._handle_hosted_zone(client, zone, allowed_vpc_ids)

            if not response.get("IsTruncated"):
                break
            marker = response.get("NextMarker")

    def _handle_hosted_zone(
        self,
        client: Any,
        zone: Dict[str, Any],
        allowed_vpc_ids: Optional[Set[str]],
    ) -> None:
        zone_id_raw = zone.get("Id")
        if not zone_id_raw:
            return
        zone_id = zone_id_raw.split("/")[-1]
        name = zone.get("Name")
        config = zone.get("Config") or {}
        is_private_zone = config.get("PrivateZone", False)

        associated_vpcs: List[Dict[str, Any]] = []
        if is_private_zone:
            associated_vpcs = self._get_hosted_zone_vpcs(client, zone_id_raw)
            if allowed_vpc_ids is not None:
                if not any(
                    (vpc.get("VPCId") in allowed_vpc_ids) for vpc in associated_vpcs
                ):
                    logger.debug(
                        "Skipping private hosted zone %s not associated with requested VPCs",
                        zone_id,
                    )
                    return

        tags = self._get_hosted_zone_tags(client, zone_id)
        location = ResourceLocation(
            account_id=self.account_id,
            region=self.GLOBAL_REGION,
        )
        metadata = self.create_resource_metadata(tags=tags)
        properties = {
            "name": name,
            "private_zone": is_private_zone,
            "comment": config.get("Comment"),
            "resource_record_set_count": zone.get("ResourceRecordSetCount"),
            "caller_reference": zone.get("CallerReference"),
            "associated_vpcs": associated_vpcs,
        }

        resource = BaseResource(
            resource_id=zone_id,
            resource_type=ResourceType.ROUTE53_HOSTED_ZONE,
            name=name[:-1] if name and name.endswith(".") else name,
            arn=f"arn:aws:route53:::hostedzone/{zone_id}",
            location=location,
            metadata=metadata,
            properties=properties,
        )
        self.add_resource(resource)
        logger.debug("Collected Route 53 hosted zone %s", zone_id)

        self._collect_record_sets(client, zone_id_raw, zone_id, name)

    def _collect_record_sets(
        self,
        client: Any,
        api_zone_id: str,
        zone_id: str,
        zone_name: Optional[str],
    ) -> None:
        start_name: Optional[str] = None
        start_type: Optional[str] = None
        start_identifier: Optional[str] = None

        while True:
            kwargs: Dict[str, Any] = {"HostedZoneId": api_zone_id}
            if start_name:
                kwargs["StartRecordName"] = start_name
            if start_type:
                kwargs["StartRecordType"] = start_type
            if start_identifier:
                kwargs["StartRecordIdentifier"] = start_identifier

            response = self._make_api_call(
                client, "list_resource_record_sets", **kwargs
            )
            if not response:
                break

            for record_set in response.get("ResourceRecordSets", []):
                self._handle_record_set(zone_id, record_set)

            if not response.get("IsTruncated"):
                break
            start_name = response.get("NextRecordName")
            start_type = response.get("NextRecordType")
            start_identifier = response.get("NextRecordIdentifier")

    def _handle_record_set(self, zone_id: str, record_set: Dict[str, Any]) -> None:
        name = record_set.get("Name")
        record_type = record_set.get("Type")
        if not name or not record_type:
            return

        set_identifier = record_set.get("SetIdentifier")
        record_resource_id = f"{zone_id}:{name}:{record_type}"
        if set_identifier:
            record_resource_id = f"{record_resource_id}:{set_identifier}"

        alias_target = record_set.get("AliasTarget") or {}
        alias_properties = (
            {
                "dns_name": alias_target.get("DNSName"),
                "hosted_zone_id": alias_target.get("HostedZoneId"),
                "evaluate_target_health": alias_target.get("EvaluateTargetHealth"),
            }
            if alias_target
            else None
        )

        resource_records = [
            rr.get("Value") for rr in record_set.get("ResourceRecords", []) if rr.get("Value")
        ]

        target_dns_names: List[str] = []
        if alias_properties and alias_properties.get("dns_name"):
            target_dns_names.append(alias_properties["dns_name"])

        if record_type in {"A", "AAAA", "CNAME"}:
            for value in resource_records:
                if value and any(ch.isalpha() for ch in value):
                    target_dns_names.append(value)
        if target_dns_names:
            target_dns_names = list(dict.fromkeys(target_dns_names))

        location = ResourceLocation(
            account_id=self.account_id,
            region=self.GLOBAL_REGION,
        )
        metadata = self.create_resource_metadata(tags={})
        resource = BaseResource(
            resource_id=record_resource_id,
            resource_type=ResourceType.ROUTE53_RECORD,
            name=name,
            arn=(
                f"arn:aws:route53:::hostedzone/{zone_id}/recordset/"
                f"{name}:{record_type}"
            ),
            location=location,
            metadata=metadata,
            properties={
                "hosted_zone_id": zone_id,
                "name": name,
                "type": record_type,
                "ttl": record_set.get("TTL"),
                "set_identifier": set_identifier,
                "weight": record_set.get("Weight"),
                "region": record_set.get("Region"),
                "failover": record_set.get("Failover"),
                "multi_value_answer": record_set.get("MultiValueAnswer"),
                "health_check_id": record_set.get("HealthCheckId"),
                "alias_target": alias_properties,
                "resource_records": resource_records,
                "target_dns_names": target_dns_names,
            },
        )
        self.add_resource(resource)
        self.add_relationship(
            Relationship(
                source_id=zone_id,
                target_id=record_resource_id,
                relationship_type=RelationshipType.CONTAINS,
            )
        )

    def _get_hosted_zone_tags(self, client: Any, zone_id: str) -> Dict[str, str]:
        tags: Dict[str, str] = {}
        try:
            tag_response = self._make_api_call(
                client,
                "list_tags_for_resource",
                ResourceType="hostedzone",
                ResourceId=zone_id,
            )
            if tag_response:
                for tag in tag_response.get("ResourceTagSet", {}).get("Tags", []):
                    key = tag.get("Key")
                    value = tag.get("Value")
                    if key:
                        tags[key] = value
        except Exception as exc:
            logger.debug("Unable to retrieve tags for hosted zone %s: %s", zone_id, exc)
        return tags

    def _get_hosted_zone_vpcs(self, client: Any, api_zone_id: str) -> List[Dict[str, Any]]:
        try:
            response = self._make_api_call(client, "get_hosted_zone", Id=api_zone_id)
            if not response:
                return []
            return response.get("VPCs", []) or []
        except Exception as exc:
            logger.debug("Unable to retrieve VPCs for hosted zone %s: %s", api_zone_id, exc)
            return []
