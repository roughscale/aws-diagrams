"""Global Accelerator collector."""

from typing import Set, List, Dict, Any

try:
    from .base_collector import BaseCollector, logger
    from ..topology.schema import BaseResource, ResourceLocation, ResourceType, Relationship, RelationshipType
except ImportError:
    from collectors.base_collector import BaseCollector, logger
    from topology.schema import BaseResource, ResourceLocation, ResourceType, Relationship, RelationshipType


class GlobalAcceleratorCollector(BaseCollector):
    """Collector for AWS Global Accelerator resources."""

    GLOBAL_REGION = "aws-global"
    CONTROL_PLANE_REGION = "us-west-2"
    EDGE_REGION = "aws-global"

    @property
    def supported_resource_types(self) -> Set[ResourceType]:
        return {ResourceType.GLOBAL_ACCELERATOR}

    @property
    def required_permissions(self) -> List[str]:
        return [
            "globalaccelerator:ListAccelerators",
            "globalaccelerator:GetAccelerator",
            "globalaccelerator:ListListeners",
            "globalaccelerator:ListEndpointGroups",
            "globalaccelerator:ListEndpoints",
        ]

    def collect_resources(self) -> None:
        if self.region != self.GLOBAL_REGION:
            logger.info(
                "Global Accelerator is global; skipping regional collection for %s",
                self.region,
            )
            return

        client = self.session.client(
            "globalaccelerator", region_name=self.CONTROL_PLANE_REGION
        )
        response = self._make_api_call(client, "list_accelerators")
        if not response:
            return

        for accelerator in response.get("Accelerators", []):
            self._handle_accelerator(client, accelerator)

    def _handle_accelerator(self, client: Any, accelerator: Dict[str, Any]) -> None:
        accelerator_arn = accelerator.get("AcceleratorArn")
        if not accelerator_arn:
            return

        location = ResourceLocation(
            account_id=self.account_id,
            region=self.EDGE_REGION,
        )
        metadata = self.create_resource_metadata()

        listeners = self._make_api_call(
            client,
            "list_listeners",
            AcceleratorArn=accelerator_arn,
        ) or {}

        listener_summaries: List[Dict[str, Any]] = []
        for listener in listeners.get("Listeners", []) or []:
            listener_summary = {
                "listener_arn": listener.get("ListenerArn"),
                "port_ranges": listener.get("PortRanges"),
                "protocol": listener.get("Protocol"),
                "client_affinity": listener.get("ClientAffinity"),
                "endpoint_groups": [],
            }
            endpoint_groups = self._make_api_call(
                client,
                "list_endpoint_groups",
                ListenerArn=listener.get("ListenerArn"),
            ) or {}
            for endpoint_group in endpoint_groups.get("EndpointGroups", []) or []:
                listener_summary["endpoint_groups"].append(
                    {
                        "endpoint_group_arn": endpoint_group.get("EndpointGroupArn"),
                        "region": endpoint_group.get("EndpointGroupRegion"),
                        "endpoint_configurations": endpoint_group.get(
                            "EndpointDescriptions"
                        ),
                    }
                )
                for endpoint in endpoint_group.get("EndpointDescriptions") or []:
                    endpoint_id = endpoint.get("EndpointId")
                    if endpoint_id:
                        self.add_relationship(
                            Relationship(
                                source_id=accelerator_arn,
                                target_id=endpoint_id,
                                relationship_type=RelationshipType.CONNECTS_TO,
                            )
                        )
            listener_summaries.append(listener_summary)

        properties: Dict[str, Any] = {
            "name": accelerator.get("Name"),
            "status": accelerator.get("Status"),
            "enabled": accelerator.get("Enabled"),
            "ip_sets": accelerator.get("IpSets"),
            "dns_name": accelerator.get("DnsName"),
            "listeners": listener_summaries,
        }

        resource = BaseResource(
            resource_id=accelerator_arn,
            resource_type=ResourceType.GLOBAL_ACCELERATOR,
            name=accelerator.get("Name"),
            arn=accelerator_arn,
            location=location,
            metadata=metadata,
            properties=properties,
        )
        self.add_resource(resource)
