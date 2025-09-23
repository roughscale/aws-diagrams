"""
Base transformer class for converting topology views to generic graph format.

This module provides the foundation for all diagram transformers, handling
the complex topology-to-graph conversion logic that is shared across output formats.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Set, Tuple
import logging

try:
    from ..views.view_engine import TopologyView
    from ..topology.schema import ResourceType, BaseResource, Relationship, RelationshipType
    from ..utils.logger import get_logger
    from .graph_model import (
        DiagramGraph, GraphNode, GraphEdge, GraphContainer, Style, Position,
        NodeType, LayoutType, DiagramMetadata
    )
except ImportError:
    from views.view_engine import TopologyView
    from topology.schema import ResourceType, BaseResource, Relationship, RelationshipType
    from utils.logger import get_logger
    from graph_model import (
        DiagramGraph, GraphNode, GraphEdge, GraphContainer, Style, Position,
        NodeType, LayoutType, DiagramMetadata
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
        ResourceType.NETWORK_INTERFACE: "network_interface"
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
            version="1.0"
        )

        # Transformation state tracking
        self.primary_vpc_id: Optional[str] = self._extract_primary_vpc_id()
        self.logical_subnets_enabled: bool = True
        self._group_parent: Dict[str, str] = {}  # resource_id -> logical subnet node id
        self._processed_resources: Set[str] = set()

        logger.info(f"Initialized {self.__class__.__name__} for view '{view.name}'")

    def _extract_primary_vpc_id(self) -> Optional[str]:
        """Extract the primary VPC ID from the view."""
        vpc_ids = [rid for rid, res in self.view.filtered_resources.items()
                  if res.resource_type == ResourceType.VPC]
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

        # Step 4: Apply layout optimizations
        self._optimize_layout()

        # Validate the graph
        errors = self.graph.validate()
        if errors:
            logger.warning(f"Graph validation found {len(errors)} errors: {errors}")

        stats = self.graph.get_statistics()
        logger.info(f"Graph creation complete: {stats}")

        return self.graph

    def _create_resource_nodes(self) -> None:
        """Create nodes for all resources in the view."""
        logger.debug("Creating resource nodes")

        for resource_id, resource in self.view.filtered_resources.items():
            if resource_id in self._processed_resources:
                continue

            node = self._create_node_from_resource(resource)
            self.graph.add_node(node)
            self._processed_resources.add(resource_id)

    def _create_node_from_resource(self, resource: BaseResource) -> GraphNode:
        """Create a graph node from an AWS resource."""
        node_id = resource.resource_id
        generic_type = self.RESOURCE_TYPE_MAPPING.get(resource.resource_type, "unknown")

        # Create base node
        node = GraphNode(
            id=node_id,
            node_type=NodeType.RESOURCE,
            label=resource.name or node_id,
            properties={
                "arn": resource.arn,
                "account_id": resource.location.account_id,
                "region": resource.location.region,
                "availability_zone": resource.location.availability_zone,
                "generic_type": generic_type,
                **resource.properties
            },
            resource_type=resource.resource_type
        )

        # Apply resource-specific styling
        node.style = self._get_resource_style(resource.resource_type)

        return node

    def _get_resource_style(self, resource_type: ResourceType) -> Style:
        """Get default styling for a resource type."""
        # Default AWS color scheme
        aws_colors = {
            ResourceType.VPC: Style(fill_color="#FF9900", border_color="#FF6600"),
            ResourceType.SUBNET: Style(fill_color="#7AA116", border_color="#5A7D0F"),
            ResourceType.SECURITY_GROUP: Style(fill_color="#FF4B4B", border_color="#CC0000"),
            ResourceType.EC2_INSTANCE: Style(fill_color="#FF9900", border_color="#CC6600"),
            ResourceType.LOAD_BALANCER: Style(fill_color="#9D5025", border_color="#7A3E1C"),
            ResourceType.RDS_INSTANCE: Style(fill_color="#3F48CC", border_color="#2E3799"),
            ResourceType.LAMBDA_FUNCTION: Style(fill_color="#FF9900", border_color="#CC6600"),
            ResourceType.ECS_CLUSTER: Style(fill_color="#FF9900", border_color="#CC6600"),
            ResourceType.ECS_SERVICE: Style(fill_color="#FF9900", border_color="#CC6600")
        }

        return aws_colors.get(resource_type, Style(fill_color="#CCCCCC", border_color="#999999"))

    def _create_containers(self) -> None:
        """Create hierarchical containers (VPCs, subnets, clusters)."""
        logger.debug("Creating hierarchical containers")

        # Create VPC containers
        self._create_vpc_containers()

        # Create subnet containers if logical subnets are enabled
        if self.logical_subnets_enabled:
            self._create_logical_subnet_containers()
        else:
            self._create_physical_subnet_containers()

        # Create ECS cluster containers
        self._create_ecs_cluster_containers()

    def _create_vpc_containers(self) -> None:
        """Create VPC containers."""
        for resource_id, resource in self.view.filtered_resources.items():
            if resource.resource_type != ResourceType.VPC:
                continue

            container = GraphContainer(
                id=f"vpc-container-{resource_id}",
                label=resource.name or resource_id,
                container_type="vpc",
                properties={
                    "resource_id": resource_id,
                    "cidr_blocks": getattr(resource, 'cidr_blocks', [])
                },
                layout_type=LayoutType.FREE_FORM,
                style=Style(
                    fill_color="rgba(255, 153, 0, 0.1)",
                    border_color="#FF9900",
                    border_width=2.0
                )
            )

            # Find all resources in this VPC
            for res_id, res in self.view.filtered_resources.items():
                if (hasattr(res, 'properties') and
                    res.properties.get('vpc_id') == resource_id and
                    res_id != resource_id):
                    container.add_child(res_id)

            self.graph.add_container(container)

    def _create_logical_subnet_containers(self) -> None:
        """Create logical subnet groupings that span availability zones."""
        logger.debug("Creating logical subnet containers")

        # Group subnets by VPC and logical type (public/private)
        vpc_subnet_groups = {}  # vpc_id -> {group_name: [subnet_ids]}

        for resource_id, resource in self.view.filtered_resources.items():
            if resource.resource_type != ResourceType.SUBNET:
                continue

            vpc_id = resource.properties.get('vpc_id')
            if not vpc_id:
                continue

            # Determine logical group (public/private)
            group_name = self._get_subnet_logical_group(resource)

            if vpc_id not in vpc_subnet_groups:
                vpc_subnet_groups[vpc_id] = {}
            if group_name not in vpc_subnet_groups[vpc_id]:
                vpc_subnet_groups[vpc_id][group_name] = []

            vpc_subnet_groups[vpc_id][group_name].append(resource_id)

        # Create containers for each logical group
        for vpc_id, groups in vpc_subnet_groups.items():
            for group_name, subnet_ids in groups.items():
                container_id = f"{vpc_id}-{group_name}-logical-subnet"

                container = GraphContainer(
                    id=container_id,
                    label=f"{group_name.replace('_', ' ').title()} Subnets",
                    container_type="logical_subnet",
                    properties={
                        "vpc_id": vpc_id,
                        "group_name": group_name,
                        "subnet_ids": subnet_ids
                    },
                    layout_type=LayoutType.HORIZONTAL_STACK,
                    style=Style(
                        fill_color="rgba(122, 161, 22, 0.1)",
                        border_color="#7AA116",
                        border_width=1.0
                    )
                )

                # Find resources in these subnets
                for res_id, res in self.view.filtered_resources.items():
                    if (hasattr(res, 'properties') and
                        res.properties.get('subnet_ids')):
                        res_subnets = set(res.properties.get('subnet_ids', []))
                        if res_subnets & set(subnet_ids):
                            container.add_child(res_id)
                            self._group_parent[res_id] = container_id

                self.graph.add_container(container)

    def _get_subnet_logical_group(self, subnet_resource: BaseResource) -> str:
        """Determine the logical group for a subnet (public/private)."""
        subnet_name = (subnet_resource.name or "").lower()

        # Check tags first
        tags = subnet_resource.properties.get('tags', {})
        subnet_type = tags.get('Type', '').lower()
        if 'public' in subnet_type:
            return 'public'
        elif 'private' in subnet_type:
            return 'private'

        # Check name patterns
        if 'public' in subnet_name:
            return 'public'
        elif 'private' in subnet_name:
            return 'private'
        elif 'dmz' in subnet_name:
            return 'dmz'
        elif 'db' in subnet_name or 'data' in subnet_name:
            return 'data'

        return 'unknown'

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
                    "cidr_blocks": getattr(resource, 'cidr_blocks', [])
                },
                layout_type=LayoutType.FREE_FORM
            )

            self.graph.add_container(container)

    def _create_ecs_cluster_containers(self) -> None:
        """Create ECS cluster containers with service groupings."""
        logger.debug("Creating ECS cluster containers")

        # Find all ECS clusters
        clusters = {rid: res for rid, res in self.view.filtered_resources.items()
                   if res.resource_type == ResourceType.ECS_CLUSTER}

        for cluster_id, cluster_resource in clusters.items():
            # Find services in this cluster
            cluster_arn = cluster_resource.resource_id
            services = []

            for svc_id, svc_res in self.view.filtered_resources.items():
                if (svc_res.resource_type == ResourceType.ECS_SERVICE and
                    svc_res.properties.get('clusterArn') == cluster_arn):
                    services.append(svc_id)

            if services:  # Only create container if there are services
                container = GraphContainer(
                    id=f"ecs-cluster-container-{cluster_id}",
                    label=cluster_resource.name or cluster_id,
                    container_type="ecs_cluster",
                    properties={
                        "resource_id": cluster_id,
                        "service_count": len(services)
                    },
                    layout_type=LayoutType.VERTICAL_STACK,
                    style=Style(
                        fill_color="rgba(255, 153, 0, 0.1)",
                        border_color="#FF9900",
                        border_width=1.5
                    )
                )

                for service_id in services:
                    container.add_child(service_id)

                self.graph.add_container(container)

    def _create_edges(self) -> None:
        """Create edges from topology relationships."""
        logger.debug("Creating edges from relationships")

        for relationship in self.view.filtered_relationships:
            edge = self._create_edge_from_relationship(relationship)
            if edge:
                self.graph.add_edge(edge)

    def _create_edge_from_relationship(self, relationship: Relationship) -> Optional[GraphEdge]:
        """Create a graph edge from a topology relationship."""
        # Skip if either source or target is not in the graph
        if (relationship.source_id not in self.graph.nodes or
            relationship.target_id not in self.graph.nodes):
            return None

        edge_id = f"{relationship.source_id}-{relationship.target_id}-{relationship.relationship_type.value}"

        edge = GraphEdge(
            id=edge_id,
            source_id=relationship.source_id,
            target_id=relationship.target_id,
            label=relationship.relationship_type.value.replace('_', ' ').title(),
            properties=relationship.properties,
            relationship_type=relationship.relationship_type.value
        )

        # Style edges based on relationship type
        edge.style = self._get_edge_style(relationship.relationship_type)

        return edge

    def _get_edge_style(self, relationship_type: RelationshipType) -> Style:
        """Get styling for an edge based on relationship type."""
        edge_styles = {
            RelationshipType.CONTAINS: Style(color="#666666", dashed=False),
            RelationshipType.ATTACHED_TO: Style(color="#0066CC", dashed=False),
            RelationshipType.ROUTES_TO: Style(color="#009900", dashed=True),
            RelationshipType.ALLOWS: Style(color="#FF6600", dashed=True),
            RelationshipType.TARGETS: Style(color="#CC0000", dashed=False),
            RelationshipType.CONNECTS_TO: Style(color="#9900CC", dashed=False)
        }

        return edge_styles.get(relationship_type, Style(color="#CCCCCC"))

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
                node.position = Position(
                    x=current_x,
                    y=current_y,
                    width=100,
                    height=80
                )

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