"""
Base transformer class for converting topology views to generic graph format.

This module provides the foundation for all diagram transformers, handling
the complex topology-to-graph conversion logic that is shared across output formats.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Set, Tuple
import logging
import math

try:
    from ..views.view_engine import TopologyView
    from ..topology.schema import (
        ResourceType,
        BaseResource,
        Relationship,
        RelationshipType,
    )
    from ..utils.logger import get_logger
    from .graph_model import (
        DiagramGraph,
        GraphNode,
        GraphEdge,
        GraphContainer,
        Style,
        Position,
        NodeType,
        LayoutType,
        DiagramMetadata,
    )
    from ..utils.eni_inference import (
        extract_elb_identifier,
        resolve_load_balancer_identifier,
    )
except ImportError:
    from views.view_engine import TopologyView
    from topology.schema import (
        ResourceType,
        BaseResource,
        Relationship,
        RelationshipType,
    )
    from utils.logger import get_logger
    from transformers.graph_model import (
        DiagramGraph,
        GraphNode,
        GraphEdge,
        GraphContainer,
        Style,
        Position,
        NodeType,
        LayoutType,
        DiagramMetadata,
    )
    from utils.eni_inference import (
        extract_elb_identifier,
        resolve_load_balancer_identifier,
    )

logger = get_logger("base_transformer")


class BaseTransformer(ABC):
    """
    Abstract base class for all diagram transformers.

    Provides common topology-to-graph conversion logic that is shared
    across all output formats (AWS Labs, draw.io, Mermaid, etc.).
    """

    # Mapping of AWS resource types to generic service types
    RESOURCE_TYPE_MAPPING = {
        ResourceType.VPC: "vpc",
        ResourceType.SUBNET: "subnet",
        ResourceType.SECURITY_GROUP: "security_group",
        ResourceType.INTERNET_GATEWAY: "internet_gateway",
        ResourceType.NAT_GATEWAY: "nat_gateway",
        ResourceType.ROUTE_TABLE: "route_table",
        ResourceType.NETWORK_ACL: "network_acl",
        ResourceType.VPC_ENDPOINT: "vpc_endpoint",
        ResourceType.EC2_INSTANCE: "ec2_instance",
        ResourceType.LOAD_BALANCER: "load_balancer",
        ResourceType.TARGET_GROUP: "target_group",
        ResourceType.RDS_INSTANCE: "rds_instance",
        ResourceType.RDS_CLUSTER: "rds_cluster",
        ResourceType.ELASTICACHE_CLUSTER: "elasticache_cluster",
        ResourceType.OPENSEARCH_DOMAIN: "opensearch_domain",
        ResourceType.REDSHIFT_CLUSTER: "redshift_cluster",
        ResourceType.ECS_CLUSTER: "ecs_cluster",
        ResourceType.ECS_SERVICE: "ecs_service",
        ResourceType.LAMBDA_FUNCTION: "lambda_function",
        ResourceType.TRANSIT_GATEWAY: "transit_gateway",
        ResourceType.VPC_PEERING: "vpc_peering",
        ResourceType.NETWORK_INTERFACE: "network_interface",
    }

    ENI_SUPPORTED_OWNER_TYPES: Set[ResourceType] = {
        ResourceType.EC2_INSTANCE,
        ResourceType.ECS_SERVICE,
        ResourceType.LAMBDA_FUNCTION,
        ResourceType.LOAD_BALANCER,
        ResourceType.VPC_ENDPOINT,
        ResourceType.NAT_GATEWAY,
        ResourceType.TRANSIT_GATEWAY,
        ResourceType.CLOUDFRONT_DISTRIBUTION,
        ResourceType.GLOBAL_ACCELERATOR,
    }

    def __init__(self, view: TopologyView):
        self.view = view
        self.graph = DiagramGraph()

        # Initialize metadata
        self.graph.metadata = DiagramMetadata(
            title=view.name,
            description=view.description,
            source_view=view.name,
            generator=self.__class__.__name__,
            version="1.0",
        )

        # Transformation state tracking
        self.primary_vpc_id: Optional[str] = self._extract_primary_vpc_id()
        self.logical_subnets_enabled: bool = True
        self._group_parent: Dict[str, str] = {}  # resource_id -> logical subnet node id
        self._processed_resources: Set[str] = set()
        self.include_load_balancer_nodes: bool = False
        self.include_target_group_nodes: bool = False
        self.include_tg_backed_services: bool = False

        logger.info(f"Initialized {self.__class__.__name__} for view '{view.name}'")

    def _extract_primary_vpc_id(self) -> Optional[str]:
        """Extract the primary VPC ID from the view."""
        vpc_ids = [
            rid
            for rid, res in self.view.filtered_resources.items()
            if res.resource_type == ResourceType.VPC
        ]
        return vpc_ids[0] if len(vpc_ids) == 1 else None

    def create_graph(self) -> DiagramGraph:
        """
        Create the generic graph representation from the topology view.

        This is the main entry point that orchestrates the topology-to-graph conversion.
        """
        logger.info(f"Creating graph from view '{self.view.name}'")

        # Step 1: Create nodes for all resources
        self._create_resource_nodes()

        # Step 2: Create hierarchical containers
        self._create_containers()

        # Step 3: Create edges from relationships
        self._create_edges()

        # Step 3.5: Add inferred edges (LB -> service, LB -> LB via TG targets)
        self._add_lb_target_edges()

        # Step 4: Apply layout optimizations
        self._optimize_layout()

        # Validate the graph
        errors = self.graph.validate()
        if errors:
            logger.warning(f"Graph validation found {len(errors)} errors: {errors}")

        stats = self.graph.get_statistics()
        logger.info(f"Graph creation complete: {stats}")

        return self.graph

    def _add_lb_target_edges(self) -> None:
        """Add deterministic LB target edges based on TG membership.

        These edges are graph-level inferences used by all exporters.
        """
        # Build LB -> TG mapping from CONTAINS relationships
        lb_to_tgs: Dict[str, List[str]] = {}
        for rel in self.view.filtered_relationships:
            if rel.relationship_type != RelationshipType.CONTAINS:
                continue
            src = self.view.filtered_resources.get(rel.source_id)
            tgt = self.view.filtered_resources.get(rel.target_id)
            if not src or not tgt:
                continue
            if src.resource_type == ResourceType.LOAD_BALANCER and tgt.resource_type == ResourceType.TARGET_GROUP:
                lb_to_tgs.setdefault(src.resource_id, []).append(tgt.resource_id)

        # Build TG -> ECS services mapping from service properties
        tg_to_services: Dict[str, List[str]] = {}
        for resource_id, resource in self.view.filtered_resources.items():
            if resource.resource_type != ResourceType.ECS_SERVICE:
                continue
            for tg_id in resource.properties.get("target_group_arns") or []:
                tg_to_services.setdefault(str(tg_id), []).append(resource_id)

        # Emit LB -> service edges (with via_tg metadata) and LB -> LB edges
        for lb_id, tg_ids in lb_to_tgs.items():
            for tg_id in tg_ids:
                # LB -> ECS service
                for svc_id in tg_to_services.get(tg_id, []):
                    if lb_id not in self.graph.nodes or svc_id not in self.graph.nodes:
                        continue
                    edge_id = f"targets:{lb_id}->{svc_id}:{tg_id}"
                    if edge_id in self.graph.edges:
                        continue
                    self.graph.add_edge(GraphEdge(
                        id=edge_id,
                        source_id=lb_id,
                        target_id=svc_id,
                        relationship_type=RelationshipType.TARGETS.value,
                        properties={"via_tg": tg_id},
                    ))

                # LB -> LB (when TG target explicitly references another LB ARN)
                tg_res = self.view.filtered_resources.get(tg_id)
                if not tg_res:
                    continue
                for target in tg_res.properties.get("targets") or []:
                    tid = target.get("id")
                    if not tid or not str(tid).startswith("arn:aws:elasticloadbalancing:"):
                        continue
                    target_lb_id = str(tid)
                    if lb_id not in self.graph.nodes or target_lb_id not in self.graph.nodes:
                        continue
                    edge_id = f"targets:{lb_id}->{target_lb_id}:{tg_id}"
                    if edge_id in self.graph.edges:
                        continue
                    self.graph.add_edge(GraphEdge(
                        id=edge_id,
                        source_id=lb_id,
                        target_id=target_lb_id,
                        relationship_type=RelationshipType.TARGETS.value,
                        properties={"via_tg": tg_id},
                    ))

    def _collect_dedup_eni_ids(self) -> Set[str]:
        """Collect ENI IDs that should be deduplicated when their owners are rendered."""
        dedup_eni_ids: Set[str] = set(self._collect_service_eni_ids())

        # Dedup ENIs that have explicit relationships to first-class owners
        for rel in self.view.filtered_relationships:
            if rel.relationship_type not in {
                RelationshipType.ATTACHED_TO,
                RelationshipType.MEMBER_OF,
            }:
                continue
            eni = self.view.filtered_resources.get(rel.source_id)
            if not eni or eni.resource_type != ResourceType.NETWORK_INTERFACE:
                continue
            owner = self.view.filtered_resources.get(rel.target_id)
            if owner and owner.resource_type in self.ENI_SUPPORTED_OWNER_TYPES:
                dedup_eni_ids.add(eni.resource_id)

        # Dedup ENIs that can be inferred to belong to first-class owners
        for resource_id, resource in self.view.filtered_resources.items():
            if resource.resource_type != ResourceType.NETWORK_INTERFACE:
                continue
            props = resource.properties or {}
            attachment = props.get("attachment") or {}

            instance_id = (
                attachment.get("InstanceId")
                or attachment.get("instance_id")
                or attachment.get("instanceId")
            )
            if instance_id:
                owner = self.view.filtered_resources.get(str(instance_id))
                if owner and owner.resource_type == ResourceType.EC2_INSTANCE:
                    dedup_eni_ids.add(resource_id)
                    continue

            owner_id = str(attachment.get("InstanceOwnerId") or "").lower()
            if owner_id in {
                "amazon-elb",
                "amazon-elbv2",
                "amazon-lambda",
                "amazon-rds",
                "amazon-elasticache",
                "amazon-opensearch",
                "amazon-elasticsearch",
                "amazon-redshift",
            }:
                dedup_eni_ids.add(resource_id)
                continue

            desc = str(props.get("description") or resource.name or "").lower()
            if (
                "arn:aws:ecs" in desc
                or "ecs attachment" in desc
                or desc.startswith("elb ")
                or "elastic load balancer" in desc
                or "aws lambda vpc eni" in desc
            ):
                dedup_eni_ids.add(resource_id)

        return dedup_eni_ids

    def _collect_service_eni_ids(self) -> Set[str]:
        """Collect ENI IDs that belong to aggregated services (RDS, ElastiCache, etc.)."""
        service_eni_ids: Set[str] = set()

        # Map VPC endpoint ownership for deterministic ENI filtering
        vpce_owner_map: Dict[str, Tuple[str, str]] = {}
        lambda_ids: Set[str] = set()
        for resource_id, resource in self.view.filtered_resources.items():
            if resource.resource_type == ResourceType.VPC_ENDPOINT:
                owner = (resource.properties.get("service_owner") or "").lower()
                service_name = (resource.properties.get("service_name") or "").lower()
                vpce_owner_map[resource_id] = (owner, service_name)
            elif resource.resource_type == ResourceType.LAMBDA_FUNCTION:
                lambda_ids.add(resource_id)

        for resource_id, resource in self.view.filtered_resources.items():
            if resource.resource_type in [
                ResourceType.RDS_INSTANCE,
                ResourceType.RDS_CLUSTER,
                ResourceType.ELASTICACHE_CLUSTER,
                ResourceType.OPENSEARCH_DOMAIN,
                ResourceType.REDSHIFT_CLUSTER,
            ]:
                # Get ENI IDs from resource properties
                for eni_id in (
                    resource.properties.get("network_interface_ids", []) or []
                ):
                    service_eni_ids.add(eni_id)

                # Also check for ENI relationships (MEMBER_OF pointing to this service)
                for rel in self.view.filtered_relationships:
                    if (
                        rel.relationship_type == RelationshipType.MEMBER_OF
                        and rel.target_id == resource_id
                    ):
                        eni = self.view.filtered_resources.get(rel.source_id)
                        if eni and eni.resource_type == ResourceType.NETWORK_INTERFACE:
                            service_eni_ids.add(eni.resource_id)

        # Deterministically include ENIs attached to AWS-managed VPC endpoints or Lambda functions
        for rel in self.view.filtered_relationships:
            if rel.relationship_type != RelationshipType.ATTACHED_TO:
                continue
            eni_resource = self.view.filtered_resources.get(rel.source_id)
            if (
                not eni_resource
                or eni_resource.resource_type != ResourceType.NETWORK_INTERFACE
            ):
                continue
            if rel.target_id in lambda_ids:
                logger.debug(
                    f"Service ENI detected via Lambda relationship: {eni_resource.resource_id} -> {rel.target_id}"
                )
                service_eni_ids.add(eni_resource.resource_id)
                continue
            vpce_info = vpce_owner_map.get(rel.target_id)
            if vpce_info:
                owner, service_name = vpce_info
                if owner in {
                    "amazon",
                    "aws",
                    "amazon web services",
                    "amazon web services, inc.",
                } or service_name.startswith("com.amazonaws."):
                    logger.debug(
                        f"Service ENI detected via VPCE relationship: {eni_resource.resource_id} -> {rel.target_id} (owner={owner}, service={service_name})"
                    )
                    service_eni_ids.add(eni_resource.resource_id)

        # Include ENIs whose metadata tags identify managed services (RDS, Lambda, etc.)
        for resource_id, resource in self.view.filtered_resources.items():
            if resource.resource_type != ResourceType.NETWORK_INTERFACE:
                continue
            attachment = (resource.properties or {}).get("attachment") or {}
            owner = str(attachment.get("InstanceOwnerId", "")).lower()
            tags = resource.metadata.tags or {}

            if owner in {"amazon-rds"}:
                logger.debug(
                    f"Service ENI detected via owner: {resource_id} owner={owner}"
                )
                service_eni_ids.add(resource_id)
                continue

            desc = (resource.properties or {}).get("description", "")
            desc_lower = str(desc).lower()
            if any(
                keyword in desc_lower
                for keyword in [
                    "rdsnetworkinterface",
                    "rds-",
                    "aurora",
                    "elasticache",
                    "redis",
                    "memcached",
                    "redshift",
                    "opensearch",
                    "elasticsearch",
                ]
            ):
                logger.debug(
                    f"Service ENI detected via description: {resource_id} desc={desc}"
                )
                service_eni_ids.add(resource_id)
            else:
                logger.debug(
                    f"Non-service ENI retained: {resource_id} owner={owner} desc={desc} tags={tags}"
                )

        return service_eni_ids

    def _create_resource_nodes(self) -> None:
        """Create nodes for all resources in the view."""
        logger.debug("Creating resource nodes")

        # Collect ENI IDs that should be deduplicated to avoid clutter
        dedup_eni_ids = self._collect_dedup_eni_ids()

        for resource_id, resource in self.view.filtered_resources.items():
            if resource_id in self._processed_resources:
                continue

            # Create nodes for resources that should be displayed
            node = self._create_node_from_resource(resource)
            if (
                resource.resource_type == ResourceType.NETWORK_INTERFACE
                and resource_id in dedup_eni_ids
            ):
                node.properties["render"] = False
                node.properties["dedup_reason"] = "owner_rendered"
            self.graph.add_node(node)
            self._processed_resources.add(resource_id)

    def _create_node_from_resource(self, resource: BaseResource) -> GraphNode:
        """Create a graph node from an AWS resource."""
        node_id = resource.resource_id
        generic_type = self.RESOURCE_TYPE_MAPPING.get(resource.resource_type, "unknown")

        label = resource.name or node_id
        if resource.resource_type == ResourceType.NETWORK_INTERFACE:
            ip = (resource.properties or {}).get("private_ip")
            label = node_id
            if ip:
                label = f"{label}\n{ip}"

        # Create base node
        node = GraphNode(
            id=node_id,
            node_type=NodeType.RESOURCE,
            label=label,
            properties={
                "arn": resource.arn,
                "account_id": resource.location.account_id,
                "region": resource.location.region,
                "availability_zone": resource.location.availability_zone,
                "generic_type": generic_type,
                **resource.properties,
            },
            resource_type=resource.resource_type,
            resource=resource,
        )

        # No styling applied - let individual transformers handle format-specific styling

        return node

    def _should_skip_resource_node(self, resource: BaseResource) -> bool:
        """
        Determine if a resource should be skipped as a standalone node.

        This implements the AWS Labs filtering logic to maintain consistency
        across all output formats (AWS Labs, draw.io, etc.).
        """
        # Skip subnets when logical grouping is enabled (they become containers)
        if (
            resource.resource_type == ResourceType.SUBNET
            and self.logical_subnets_enabled
        ):
            return True

        # Skip load balancers unless explicitly included
        if (
            resource.resource_type == ResourceType.LOAD_BALANCER
            and not self.include_load_balancer_nodes
        ):
            return True

        # Skip target groups as standalone nodes (not user-visible components)
        if (
            resource.resource_type == ResourceType.TARGET_GROUP
            and not self.include_target_group_nodes
        ):
            return True

        # Skip ECS services that have target groups (handled by LB/TG clustering)
        if resource.resource_type == ResourceType.ECS_SERVICE:
            has_tgs = bool(resource.properties.get("target_group_arns"))
            if has_tgs and not self.include_tg_backed_services:
                return True

        return False

    def _create_containers(self) -> None:
        """Create hierarchical containers (VPCs, subnets, clusters)."""
        logger.debug("Creating hierarchical containers")

        # Create subnet containers first (logical subnets need to exist before VPC containers)
        if self.logical_subnets_enabled:
            self._create_logical_subnet_containers()
        else:
            self._create_physical_subnet_containers()

        # Create VPC containers (these will reference logical subnet containers)
        self._create_vpc_containers()

        # Create ECS cluster containers
        self._create_ecs_cluster_containers()

    def _create_vpc_containers(self) -> None:
        """Create VPC containers with logical subnet organization."""
        for resource_id, resource in self.view.filtered_resources.items():
            if resource.resource_type != ResourceType.VPC:
                continue

            container = GraphContainer(
                id=f"vpc-container-{resource_id}",
                label=resource.name or resource_id,
                container_type="vpc",
                properties={
                    "resource_id": resource_id,
                    "cidr_blocks": getattr(resource, "cidr_blocks", []),
                },
                layout_type=LayoutType.VERTICAL_STACK,  # Changed to vertical stack
            )

            # Find logical subnet containers for this VPC
            logical_subnet_containers = []
            for container_id, logical_container in self.graph.containers.items():
                if (
                    logical_container.container_type == "logical_subnet"
                    and logical_container.properties.get("vpc_id") == resource_id
                ):
                    logical_subnet_containers.append(container_id)

            # Add logical subnet containers to VPC
            for logical_subnet_id in logical_subnet_containers:
                container.add_child(logical_subnet_id)

            # Add any VPC-level resources that aren't part of logical subnets
            # (like NAT gateways, internet gateways, etc.)
            for res_id, res in self.view.filtered_resources.items():
                if (
                    hasattr(res, "properties")
                    and res.properties.get("vpc_id") == resource_id
                    and res_id != resource_id
                    and res_id in self.graph.nodes
                    and not self._is_resource_in_logical_subnet(res_id)
                ):
                    container.add_child(res_id)

            self.graph.add_container(container)

    def _create_logical_subnet_containers(self) -> None:
        """Create logical subnet groupings that aggregate subnets across availability zones."""
        logger.debug("Creating logical subnet containers")

        # Group subnets by VPC and logical type based on naming patterns
        vpc_subnet_groups = {}  # vpc_id -> {group_name: [subnet_ids]}

        for resource_id, resource in self.view.filtered_resources.items():
            if resource.resource_type != ResourceType.SUBNET:
                continue

            vpc_id = resource.properties.get("vpc_id")
            if not vpc_id:
                continue

            # Extract logical group from subnet naming patterns and tags
            group_name = self._extract_subnet_logical_group(resource)

            if vpc_id not in vpc_subnet_groups:
                vpc_subnet_groups[vpc_id] = {}
            if group_name not in vpc_subnet_groups[vpc_id]:
                vpc_subnet_groups[vpc_id][group_name] = []

            vpc_subnet_groups[vpc_id][group_name].append(resource_id)

        # Precompute target group Lambda targets to prevent multi-parenting
        tg_lambda_targets = self._collect_target_group_lambda_targets()

        # Build ECS service to cluster mapping for proper hierarchy placement
        ecs_service_clusters = self._build_ecs_service_cluster_mapping(
            vpc_subnet_groups
        )

        # Create logical subnet containers with proper resource placement
        for vpc_id, groups in vpc_subnet_groups.items():
            for group_name, subnet_ids in groups.items():
                container_id = f"{vpc_id}-{group_name.replace('_', '-')}"

                container = GraphContainer(
                    id=container_id,
                    label=f"{group_name.replace('-', ' ').title()}",
                    container_type="logical_subnet",
                    properties={
                        "vpc_id": vpc_id,
                        "group_name": group_name,
                        "subnet_ids": subnet_ids,
                    },
                    layout_type=LayoutType.VERTICAL_STACK,
                )

                # Organize content in rows following V1 pattern
                subnet_set = set(subnet_ids)

                # Collect different types of resources for proper organization
                lb_stacks = []  # Load balancer vertical stacks
                service_nodes = []  # Standalone services (ECS clusters, Lambda, etc.)
                aux_resources = []  # Network resources (ENIs, NAT gateways, etc.)

                # Add ECS cluster hierarchies
                ecs_clusters = self._collect_ecs_clusters_for_logical_subnet(
                    subnet_set, ecs_service_clusters
                )
                service_nodes.extend(ecs_clusters)

                # Add standalone services (Lambda, databases)
                standalone_services = (
                    self._collect_standalone_services_for_logical_subnet(
                        subnet_set, tg_lambda_targets
                    )
                )
                service_nodes.extend(standalone_services)

                # Add auxiliary network resources
                network_resources = self._collect_network_resources_for_logical_subnet(
                    subnet_set
                )
                aux_resources.extend(network_resources)

                # Create row-based organization
                rows = []

                # Row 1: Load balancer stacks (if any)
                if lb_stacks:
                    rows.extend(lb_stacks)

                # Row 2: Service grid (ECS clusters, Lambda, databases)
                if service_nodes:
                    if len(service_nodes) > 1:
                        # Create grid for multiple services
                        grid_container_id = f"{container_id}-services-grid"
                        grid_container = self._create_service_grid(
                            grid_container_id, service_nodes
                        )
                        if grid_container:
                            rows.append(grid_container)
                    else:
                        rows.extend(service_nodes)

                # Row 3: Auxiliary resources (ENIs, NAT gateways)
                if aux_resources:
                    rows.extend(aux_resources)

                # Set the organized rows as children
                container.children_ids = rows

                # Only create container if it has content
                if container.children_ids:
                    self.graph.add_container(container)
                    logger.debug(
                        f"Created logical subnet {container_id} with {len(rows)} rows"
                    )

    def _extract_subnet_logical_group(self, subnet_resource: BaseResource) -> str:
        """Extract logical group name from subnet using naming patterns and tags."""
        subnet_name = (subnet_resource.name or subnet_resource.resource_id).lower()

        # Check tags first for explicit type designation
        tags = subnet_resource.properties.get("tags", {})
        subnet_type = tags.get("Type", "").lower()
        if subnet_type:
            if "public" in subnet_type:
                return "public-subnets"
            elif "private" in subnet_type:
                return "private-subnets"

        # Extract from common naming patterns
        if "private" in subnet_name:
            return "private-subnets"
        elif "public" in subnet_name:
            return "public-subnets"
        elif "tgw" in subnet_name:
            return "tgw-subnets"
        elif "db" in subnet_name or "database" in subnet_name:
            return "database-subnets"
        elif "web" in subnet_name:
            return "web-subnets"
        elif "app" in subnet_name:
            return "app-subnets"
        else:
            # Extract prefix before AZ designation
            parts = subnet_name.split("-")
            if len(parts) >= 2:
                # Remove AZ suffix if present (like '2a', '2b', '2c')
                import re

                if re.match(r"^[0-9][a-z]$", parts[-1]):
                    return "-".join(parts[:-1]) + "-subnets"
                else:
                    return (
                        "-".join(parts[:-1]) + "-subnets"
                        if len(parts) > 1
                        else subnet_name
                    )
            return "misc-subnets"

    def _collect_target_group_lambda_targets(self) -> Set[str]:
        """Collect Lambda function IDs that are targets of target groups to prevent multi-parenting."""
        tg_lambda_targets = set()

        for resource_id, resource in self.view.filtered_resources.items():
            if resource.resource_type == ResourceType.TARGET_GROUP:
                target_type = resource.properties.get("target_type")
                if target_type == "lambda":
                    targets = resource.properties.get("targets", [])
                    for target in targets:
                        if target.get("id"):
                            tg_lambda_targets.add(str(target["id"]))

        return tg_lambda_targets

    def _build_ecs_service_cluster_mapping(
        self, vpc_subnet_groups: Dict
    ) -> Dict[str, Dict]:
        """Build mapping of ECS services to their clusters across all VPCs."""
        ecs_service_clusters = {}

        # Get all subnet IDs across all VPCs for filtering
        all_subnet_ids = []
        for groups in vpc_subnet_groups.values():
            for subnet_list in groups.values():
                all_subnet_ids.extend(subnet_list)
        vpc_subnet_set = set(all_subnet_ids)

        # Map each ECS service to its cluster information
        for service_id, service_resource in self.view.filtered_resources.items():
            if service_resource.resource_type == ResourceType.ECS_SERVICE:
                service_subnets = set(service_resource.properties.get("subnet_ids", []))

                # Only process services in the VPCs we're working with
                if service_subnets & vpc_subnet_set:
                    cluster_arn = service_resource.properties.get("clusterArn")
                    if cluster_arn:
                        # Find the cluster resource
                        cluster_resource = None
                        for (
                            cluster_id,
                            cluster_res,
                        ) in self.view.filtered_resources.items():
                            if (
                                cluster_res.resource_type == ResourceType.ECS_CLUSTER
                                and cluster_res.resource_id == cluster_arn
                            ):
                                cluster_resource = cluster_res
                                break

                        if cluster_resource:
                            # Determine which subnet group this service belongs to
                            service_group = None
                            for vpc_id, groups in vpc_subnet_groups.items():
                                for group_name, subnet_ids in groups.items():
                                    if service_subnets & set(subnet_ids):
                                        service_group = group_name
                                        break
                                if service_group:
                                    break

                            ecs_service_clusters[service_id] = {
                                "cluster_resource": cluster_resource,
                                "cluster_arn": cluster_arn,
                                "service_group": service_group,
                                "service_subnets": service_subnets,
                            }

        return ecs_service_clusters

    def _collect_ecs_clusters_for_logical_subnet(
        self, subnet_set: Set[str], ecs_service_clusters: Dict[str, Dict]
    ) -> List[str]:
        """Collect ECS cluster container IDs for this logical subnet."""
        cluster_services = {}  # cluster_arn -> [service_ids]

        for service_id, cluster_info in ecs_service_clusters.items():
            service_subnets = cluster_info["service_subnets"]
            if service_subnets & subnet_set:  # Service is in this subnet group
                cluster_arn = cluster_info["cluster_arn"]
                if cluster_arn not in cluster_services:
                    cluster_services[cluster_arn] = []
                cluster_services[cluster_arn].append(service_id)

        cluster_container_ids = []

        # Create cluster containers with service grouping by security groups
        for cluster_arn, service_ids in cluster_services.items():
            if service_ids:  # Only create if there are services
                cluster_info = ecs_service_clusters[service_ids[0]]
                cluster_resource = cluster_info["cluster_resource"]

                # Use V1-style cluster ID pattern for consistency
                cluster_name = (
                    cluster_resource.name or cluster_resource.resource_id.split("/")[-1]
                )
                cluster_container_id = f"cluster-{cluster_name}-logical-subnet"

                # Group services by security groups for de-duplication
                services_by_sg = self._group_ecs_services_by_security_groups(
                    service_ids
                )

                cluster_container = GraphContainer(
                    id=cluster_container_id,
                    label=cluster_resource.name or cluster_resource.resource_id,
                    container_type="ecs_cluster",
                    properties={
                        "cluster_arn": cluster_arn,
                        "service_count": len(service_ids),
                    },
                    layout_type=LayoutType.VERTICAL_STACK,
                )

                # Add services or security group containers as children
                for sg_key, grouped_service_ids in services_by_sg.items():
                    if sg_key == ("no-sg",):
                        # Services without security groups - add directly
                        for service_id in grouped_service_ids:
                            cluster_container.add_child(service_id)
                    else:
                        # Services with security groups - create nested containers
                        sg_container_id = self._create_security_group_container(
                            sg_key, grouped_service_ids, cluster_container_id
                        )
                        cluster_container.add_child(sg_container_id)

                self.graph.add_container(cluster_container)
                cluster_container_ids.append(cluster_container_id)

        return cluster_container_ids

    def _group_ecs_services_by_security_groups(
        self, service_ids: List[str]
    ) -> Dict[Tuple[str, ...], List[str]]:
        """Group ECS services by their security group combinations for de-duplication."""
        services_by_sg = {}

        for service_id in service_ids:
            service_resource = self.view.filtered_resources.get(service_id)
            if service_resource:
                sgs = service_resource.properties.get("security_group_ids", [])
                sg_key = tuple(sorted(sgs)) if sgs else ("no-sg",)

                if sg_key not in services_by_sg:
                    services_by_sg[sg_key] = []
                services_by_sg[sg_key].append(service_id)

        return services_by_sg

    def _create_security_group_container(
        self, sg_key: Tuple[str, ...], service_ids: List[str], parent_id: str
    ) -> str:
        """Create nested security group containers for shared security groups."""
        container_id = f"sg-shared-{'-'.join(sg_key[:2])}-in-{parent_id}"

        # Create service stack if multiple services
        if len(service_ids) == 1:
            inner_content = service_ids[0]
        else:
            stack_id = f"services-stack-in-{parent_id}"
            services_stack = GraphContainer(
                id=stack_id,
                label="Services",
                container_type="service_stack",
                layout_type=LayoutType.HORIZONTAL_STACK,
            )
            for service_id in service_ids:
                services_stack.add_child(service_id)
            self.graph.add_container(services_stack)
            inner_content = stack_id

        # Create nested security group containers (innermost to outermost)
        current_container = inner_content
        for sg_id in reversed(list(sg_key)):
            if sg_id == "no-sg":
                continue

            sg_container_id = f"sg-{sg_id}-in-{parent_id}"
            sg_resource = self.view.filtered_resources.get(sg_id)
            sg_name = sg_resource.name if sg_resource else sg_id

            sg_container = GraphContainer(
                id=sg_container_id,
                label=f"SG: {sg_name}",
                container_type="security_group",
                layout_type=LayoutType.FREE_FORM,
            )
            sg_container.add_child(current_container)
            self.graph.add_container(sg_container)
            current_container = sg_container_id

        return current_container

    def _collect_standalone_services_for_logical_subnet(
        self, subnet_set: Set[str], tg_lambda_targets: Set[str]
    ) -> List[str]:
        """Collect standalone services (Lambda, databases) for this logical subnet."""
        service_ids = []

        for resource_id, resource in self.view.filtered_resources.items():
            if resource_id in self.graph.nodes:  # Ensure node exists
                resource_subnets = set(resource.properties.get("subnet_ids", []))

                # Check if resource belongs to this subnet group
                if resource_subnets & subnet_set:
                    # Handle different service types
                    if resource.resource_type == ResourceType.LAMBDA_FUNCTION:
                        # Skip Lambda functions that are target group targets
                        if resource_id not in tg_lambda_targets:
                            service_ids.append(resource_id)

                    elif resource.resource_type in [
                        ResourceType.RDS_INSTANCE,
                        ResourceType.RDS_CLUSTER,
                        ResourceType.ELASTICACHE_CLUSTER,
                        ResourceType.OPENSEARCH_DOMAIN,
                        ResourceType.REDSHIFT_CLUSTER,
                    ]:
                        # Database and analytics services
                        service_ids.append(resource_id)

        return service_ids

    def _collect_network_resources_for_logical_subnet(
        self, subnet_set: Set[str]
    ) -> List[str]:
        """Collect network interfaces and other network resources for this logical subnet."""
        network_resource_ids = []

        # First, collect ENI IDs that belong to aggregated services (following V1 pattern)
        service_eni_ids = self._collect_service_eni_ids()

        for resource_id, resource in self.view.filtered_resources.items():
            if resource_id in self.graph.nodes:  # Ensure node exists
                if resource.resource_type == ResourceType.NETWORK_INTERFACE:
                    subnet_id = resource.properties.get("subnet_id")
                    if subnet_id in subnet_set:
                        # Only include ENIs that are not already associated with services
                        # Check if this ENI is a member of any service or belongs to aggregated services
                        is_service_eni = False

                        # Check if ENI belongs to aggregated services (RDS, ElastiCache, etc.)
                        if resource_id in service_eni_ids:
                            is_service_eni = True
                        else:
                            # Check if ENI is a member of other services
                            for rel in self.view.filtered_relationships:
                                if (
                                    rel.source_id == resource_id
                                    and rel.relationship_type
                                    == RelationshipType.MEMBER_OF
                                ):
                                    target_resource = self.view.filtered_resources.get(
                                        rel.target_id
                                    )
                                    if (
                                        target_resource
                                        and target_resource.resource_type
                                        in [
                                            ResourceType.ECS_SERVICE,
                                            ResourceType.LAMBDA_FUNCTION,
                                        ]
                                    ):
                                        is_service_eni = True
                                        break

                        if not is_service_eni:
                            network_resource_ids.append(resource_id)

        return network_resource_ids

    def _is_resource_in_logical_subnet(self, resource_id: str) -> bool:
        """Check if a resource is already included in a logical subnet container."""
        resource = self.view.filtered_resources.get(resource_id)
        if not resource:
            return False

        # Check if resource is included in any logical subnet container
        for container in self.graph.containers.values():
            if container.container_type == "logical_subnet":
                if self._resource_belongs_to_logical_subnet(resource, container):
                    return True

        return False

    def _resource_belongs_to_logical_subnet(
        self, resource: BaseResource, logical_container: GraphContainer
    ) -> bool:
        """Check if a resource belongs to a specific logical subnet container."""
        subnet_ids = logical_container.properties.get("subnet_ids", [])
        if not subnet_ids:
            return False

        # Check if resource is in any of the logical subnet's physical subnets
        resource_subnets: Set[str] = set()

        subnet_list = resource.properties.get("subnet_ids", [])
        if isinstance(subnet_list, str):
            resource_subnets.add(subnet_list)
        else:
            resource_subnets.update(subnet_list or [])

        single_subnet = resource.properties.get("subnet_id")
        if single_subnet:
            resource_subnets.add(single_subnet)

        return bool(resource_subnets & set(subnet_ids))

    def _create_service_grid(
        self, grid_container_id: str, service_ids: List[str]
    ) -> str:
        """Create a grid layout for services following V1 pattern."""
        if not service_ids:
            return None

        # For single service, return the service ID directly
        if len(service_ids) == 1:
            return service_ids[0]

        # Calculate grid dimensions (near-square)
        n = len(service_ids)
        rows = max(1, int(math.ceil(math.sqrt(n))))
        cols = max(1, int(math.ceil(n / rows)))

        # Create the grid container
        grid_container = GraphContainer(
            id=grid_container_id,
            label="Services",
            container_type="service_grid",
            layout_type=LayoutType.VERTICAL_STACK,
        )

        for i in range(rows):
            start = i * cols
            end = start + cols
            row_services = service_ids[start:end]

            if not row_services:
                continue

            if len(row_services) == 1:
                # Single service in row - add directly
                grid_container.add_child(row_services[0])
            else:
                # Multiple services in row - create horizontal stack
                row_id = f"{grid_container_id}-row-{i+1}"
                row_container = GraphContainer(
                    id=row_id,
                    label=f"Service Row {i+1}",
                    container_type="service_row",
                    layout_type=LayoutType.HORIZONTAL_STACK,
                )

                for service_id in row_services:
                    row_container.add_child(service_id)

                self.graph.add_container(row_container)
                grid_container.add_child(row_id)

        self.graph.add_container(grid_container)
        return grid_container_id

    def _create_physical_subnet_containers(self) -> None:
        """Create individual subnet containers."""
        for resource_id, resource in self.view.filtered_resources.items():
            if resource.resource_type != ResourceType.SUBNET:
                continue

            container = GraphContainer(
                id=f"subnet-container-{resource_id}",
                label=resource.name or resource_id,
                container_type="subnet",
                properties={
                    "resource_id": resource_id,
                    "availability_zone": resource.location.availability_zone,
                    "cidr_blocks": getattr(resource, "cidr_blocks", []),
                },
                layout_type=LayoutType.FREE_FORM,
            )

            self.graph.add_container(container)

    def _create_ecs_cluster_containers(self) -> None:
        """Create ECS cluster containers with service groupings."""
        logger.debug("Creating ECS cluster containers")

        # Find all ECS clusters that exist as nodes
        clusters = {}
        for rid, res in self.view.filtered_resources.items():
            if (
                res.resource_type == ResourceType.ECS_CLUSTER
                and rid in self.graph.nodes
            ):  # Ensure cluster node exists
                clusters[rid] = res

        for cluster_id, cluster_resource in clusters.items():
            # Find services in this cluster that aren't skipped
            cluster_arn = cluster_resource.resource_id
            services = []

            for svc_id, svc_res in self.view.filtered_resources.items():
                if (
                    svc_res.resource_type == ResourceType.ECS_SERVICE
                    and svc_res.properties.get("clusterArn") == cluster_arn
                ):
                    # Only include services that exist in graph
                    if svc_id in self.graph.nodes:
                        services.append(svc_id)

            if (
                services
            ):  # Only create container if there are services that will be rendered
                container = GraphContainer(
                    id=f"ecs-cluster-container-{cluster_id}",
                    label=cluster_resource.name or cluster_id,
                    container_type="ecs_cluster",
                    properties={
                        "resource_id": cluster_id,
                        "cluster_arn": cluster_arn,
                        "service_count": len(services),
                    },
                    layout_type=LayoutType.VERTICAL_STACK,
                )

                for service_id in services:
                    container.add_child(service_id)
                    # Mark services as grouped to avoid processing them elsewhere
                    self._group_parent[service_id] = container.id

                self.graph.add_container(container)
                logger.debug(
                    f"Created ECS cluster container {container.id} with {len(services)} services"
                )
            else:
                logger.debug(
                    f"Skipping ECS cluster {cluster_id} - no valid services to render"
                )

    def _create_edges(self) -> None:
        """Create edges from topology relationships."""
        logger.debug("Creating edges from relationships")

        for relationship in self.view.filtered_relationships:
            edge = self._create_edge_from_relationship(relationship)
            if edge:
                self.graph.add_edge(edge)

        self._add_inferred_eni_edges()

    def _create_edge_from_relationship(
        self, relationship: Relationship
    ) -> Optional[GraphEdge]:
        """Create a graph edge from a topology relationship."""
        # Skip if either source or target is not in the graph
        if (
            relationship.source_id not in self.graph.nodes
            or relationship.target_id not in self.graph.nodes
        ):
            return None

        edge_id = f"{relationship.source_id}-{relationship.target_id}-{relationship.relationship_type.value}"

        edge = GraphEdge(
            id=edge_id,
            source_id=relationship.source_id,
            target_id=relationship.target_id,
            label=relationship.relationship_type.value.replace("_", " ").title(),
            properties=relationship.properties,
            relationship_type=relationship.relationship_type.value,
        )

        # No styling applied - let individual transformers handle format-specific styling

        return edge

    def _add_inferred_eni_edges(self) -> None:
        """Add inferred ENI -> resource edges when explicit relationships are absent."""

        resources = self.view.filtered_resources

        for eni_id, eni_resource in resources.items():
            if eni_resource.resource_type != ResourceType.NETWORK_INTERFACE:
                continue

            description = (eni_resource.properties or {}).get("description")
            identifier = extract_elb_identifier(description)
            if not identifier:
                continue

            target_id = resolve_load_balancer_identifier(identifier, resources)
            if not target_id:
                continue

            if eni_id not in self.graph.nodes or target_id not in self.graph.nodes:
                continue

            edge_id = f"{eni_id}-{target_id}-attached_inferred"
            if edge_id in self.graph.edges:
                continue

            self.graph.add_edge(
                GraphEdge(
                    id=edge_id,
                    source_id=eni_id,
                    target_id=target_id,
                    label=RelationshipType.ATTACHED_TO.value.replace("_", " ").title(),
                    relationship_type=RelationshipType.ATTACHED_TO.value,
                    properties={"inferred": True},
                )
            )

    def _optimize_layout(self) -> None:
        """Apply layout optimizations to the graph."""
        logger.debug("Optimizing graph layout")

        # Basic layout optimization - can be extended
        self._calculate_positions()

    def _calculate_positions(self) -> None:
        """Calculate positions for nodes using a simple layout algorithm."""
        # Simple grid layout for now - can be made more sophisticated
        grid_size = 150
        current_x, current_y = 50, 50
        nodes_per_row = 5
        node_count = 0

        for node in self.graph.nodes.values():
            if not node.position:
                node.position = Position(x=current_x, y=current_y, width=100, height=80)

                node_count += 1
                if node_count % nodes_per_row == 0:
                    current_x = 50
                    current_y += grid_size
                else:
                    current_x += grid_size

    @abstractmethod
    def transform(self) -> Any:
        """
        Transform the generic graph into the specific output format.

        This method must be implemented by each concrete transformer class.
        """
        pass
