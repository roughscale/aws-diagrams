"""
Refactored AWS Labs transformer.

This module contains the refactored AWS Labs transformer implementation.
It duplicates the legacy layout logic so that v2 produces the same diagram output
while evolving independently from the original transformer.

AWS Labs diagram-as-code format: https://github.com/awslabs/diagram-as-code
"""

from typing import Dict, List, Any, Optional, Set, Tuple, Sequence
import hashlib
import math
import re
from dataclasses import dataclass

try:
    from ..views.view_engine import TopologyView
    from ..topology.schema import ResourceType, BaseResource, RelationshipType
    from ..utils.logger import get_logger
    from .base_transformer import BaseTransformer
    from .graph_model import DiagramGraph
except ImportError:
    from views.view_engine import TopologyView
    from topology.schema import ResourceType, BaseResource, RelationshipType
    from utils.logger import get_logger
    from transformers.base_transformer import BaseTransformer
    from transformers.graph_model import DiagramGraph

logger = get_logger("transformer_v2")


class _GraphBuilder(BaseTransformer):
    """Minimal BaseTransformer subclass to expose graph creation without a concrete format."""

    def transform(self) -> DiagramGraph:
        """Graph builder does not output a diagram; return the generic graph directly."""
        return self.create_graph()


@dataclass
class DiagramNode:
    """Represents a node in the diagram."""
    id: str
    type: str
    label: str
    properties: Dict[str, Any]
    parent: Optional[str] = None
    children: List[str] = None
    
    def __post_init__(self):
        if self.children is None:
            self.children = []


@dataclass
class DiagramConnection:
    """Represents a connection between nodes in the diagram."""
    source: str
    target: str
    label: Optional[str] = None
    properties: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.properties is None:
            self.properties = {}


@dataclass
class SimpleRelationship:
    source_id: str
    target_id: str
    relationship_type: RelationshipType
    properties: Dict[str, Any]


class AWSLabsTransformerV2:
    """Transforms topology views into AWS Labs diagram-as-code format."""
    
    # Mapping of AWS resource types to diagram-as-code service types
    RESOURCE_TYPE_MAPPING = {
        ResourceType.VPC: "AWS::EC2::VPC",
        ResourceType.SUBNET: "AWS::EC2::Subnet", 
        ResourceType.SECURITY_GROUP: "AWS::EC2",  # avoid DAC warning
        ResourceType.INTERNET_GATEWAY: "AWS::EC2::InternetGateway",
        ResourceType.NAT_GATEWAY: "AWS::EC2::NatGateway",
        ResourceType.ROUTE_TABLE: "AWS::EC2::RouteTable",
        ResourceType.NETWORK_ACL: "AWS::EC2",  # avoid DAC warning
        ResourceType.VPC_ENDPOINT: "AWS::EC2::VPCEndpoint",
        ResourceType.EC2_INSTANCE: "AWS::EC2::Instance",
        ResourceType.LOAD_BALANCER: "AWS::ElasticLoadBalancingV2::LoadBalancer",
        ResourceType.TARGET_GROUP: "AWS::ElasticLoadBalancingV2",  # avoid TG warning
        ResourceType.RDS_INSTANCE: "AWS::RDS::DBInstance",
        ResourceType.RDS_CLUSTER: "AWS::RDS::DBCluster",
        ResourceType.ELASTICACHE_CLUSTER: "AWS::ElastiCache::CacheCluster",
        ResourceType.OPENSEARCH_DOMAIN: "AWS::OpenSearchService::Domain",
        ResourceType.REDSHIFT_CLUSTER: "AWS::Redshift::Cluster",
        ResourceType.ECS_CLUSTER: "AWS::ECS::Cluster",
        ResourceType.ECS_SERVICE: "AWS::ECS::Service",
        ResourceType.LAMBDA_FUNCTION: "AWS::Lambda::Function",
        ResourceType.TRANSIT_GATEWAY: "AWS::EC2::TransitGateway",
        ResourceType.VPC_PEERING: "AWS::EC2::VPCPeeringConnection",
        ResourceType.NETWORK_INTERFACE: "AWS::EC2::NetworkInterface",
        ResourceType.CLOUDFRONT_DISTRIBUTION: "AWS::CloudFront::Distribution",
        ResourceType.GLOBAL_ACCELERATOR: "AWS::GlobalAccelerator::Accelerator",
        ResourceType.ROUTE53_HOSTED_ZONE: "AWS::Route53::HostedZone",
        ResourceType.ROUTE53_RECORD: "AWS::Route53::RecordSet",
    }
    
    # Icons for different resource types
    RESOURCE_ICONS = {
        ResourceType.VPC: "VPC",
        ResourceType.SUBNET: "Subnet",
        ResourceType.SECURITY_GROUP: "SecurityGroup",
        ResourceType.INTERNET_GATEWAY: "InternetGateway",
        ResourceType.NAT_GATEWAY: "NATGateway",
        ResourceType.EC2_INSTANCE: "EC2Instance",
        ResourceType.LOAD_BALANCER: "ApplicationLoadBalancer",
        ResourceType.RDS_INSTANCE: "RDSInstance",
        ResourceType.LAMBDA_FUNCTION: "LambdaFunction",
        ResourceType.CLOUDFRONT_DISTRIBUTION: "CloudFrontDistribution",
        ResourceType.GLOBAL_ACCELERATOR: "GlobalAccelerator",
        ResourceType.ROUTE53_HOSTED_ZONE: "Route53HostedZone",
        ResourceType.ROUTE53_RECORD: "Route53HostedZone",
    }
    
    def __init__(self, view: TopologyView, logical_subnets_enabled: bool = True):
        self.view = view
        self.graph: Optional[DiagramGraph] = None
        self.resources: Dict[str, BaseResource] = {}
        self.relationships: List[SimpleRelationship] = []
        self.nodes: Dict[str, DiagramNode] = {}
        self.connections: List[DiagramConnection] = []
        self.groups: Dict[str, List[str]] = {}
        self.primary_vpc_id: Optional[str] = self._extract_primary_vpc_id()
        # Enable logical subnet grouping (Phase 1)
        self.logical_subnets_enabled = logical_subnets_enabled
        # Single-parent guards to avoid DAGs/cycles in DAC
        self._group_parent: Dict[str, str] = {}       # resource_id -> logical subnet node id
        self._tg_parent: Dict[str, str] = {}          # tg_id -> load_balancer_id
        # Explicit LB -> TG link intents for service-centric rendering
        self._tg_nodes_by_group: Dict[str, List[str]] = {}
        self._tg_meta: Dict[str, Tuple[str, str]] = {}  # tg_node_id -> (lb_title, tg_title)
        self._eni_instance_map: Dict[str, str] = {}
        self._vpce_eni_owner_cache: Dict[str, Tuple[BaseResource, str]] = {}
        self._vpce_eni_cache_populated = False
        self._cluster_parent: Dict[str, str] = {}
        self._cluster_badges: Dict[Tuple[str, str, str], str] = {}
        self._cluster_lb_owner: Dict[str, str] = {}
        self._cluster_lb_consumers: Set[str] = set()
        self._lb_service_links: Set[Tuple[str, str]] = set()
        self._tg_service_map: Dict[str, List[str]] = {}
        self._lb_stack_debug: Dict[str, List[str]] = {}
        self._global_resource_ids: List[str] = []
        self._resource_render_map: Dict[str, str] = {}
        self._global_service_ids: Set[str] = set()
        self._global_service_links: Set[Tuple[str, str]] = set()
        self._linked_global_service_ids: Set[str] = set()

    @staticmethod
    def _to_relationship_type(value: Optional[str]) -> Optional[RelationshipType]:
        """Convert a string relationship type into the enum, tolerating different casings."""
        if not value:
            return None
        try:
            return RelationshipType(value)
        except ValueError:
            try:
                return RelationshipType[value.upper()]
            except KeyError:
                return None

    def _initialize_from_graph(self, graph: DiagramGraph) -> None:
        """Populate resource and relationship caches from the generic diagram graph."""
        self.graph = graph
        self.resources = {}
        for node_id, node in graph.nodes.items():
            resource = getattr(node, "resource", None)
            if resource:
                self.resources[node_id] = resource

        self.relationships = []
        self._lb_service_links = set()
        self._tg_service_map = {}
        for edge in graph.edges.values():
            rel_type = self._to_relationship_type(edge.relationship_type)
            if not rel_type:
                continue
            self.relationships.append(
                SimpleRelationship(
                    source_id=edge.source_id,
                    target_id=edge.target_id,
                    relationship_type=rel_type,
                    properties=edge.properties or {},
                )
            )
            if rel_type == RelationshipType.TARGETS:
                self._lb_service_links.add((edge.source_id, edge.target_id))
                via_tg = (edge.properties or {}).get("via_tg")
                if via_tg:
                    self._tg_service_map.setdefault(str(via_tg), []).append(edge.target_id)
        
    def transform(self) -> Dict[str, Any]:
        """Transform the topology view into AWS Labs diagram-as-code format."""
        logger.info(f"Transforming view '{self.view.name}' to AWS Labs format")
        self._resource_render_map = {}
        self._global_service_ids = set()
        self._global_service_links = set()

        base_builder = _GraphBuilder(self.view)
        base_builder.logical_subnets_enabled = self.logical_subnets_enabled
        base_builder.include_load_balancer_nodes = True
        base_builder.include_target_group_nodes = True
        base_builder.include_tg_backed_services = True
        graph = base_builder.create_graph()
        self._initialize_from_graph(graph)
        
        # Create nodes for resources
        self._create_resource_nodes()
        
        # Create logical groupings
        self._create_groups()
        
        # Create connections from relationships
        self._create_connections()
        
        # Generate the final diagram structure
        diagram_data = self._generate_diagram_structure()
        
        logger.info(
            f"Transformation complete: {len(self.nodes)} nodes, "
            f"{len(self.connections)} connections, {len(self.groups)} groups"
        )
        
        return diagram_data
    
    def _create_resource_nodes(self) -> None:
        """Create diagram nodes for each resource in the view."""
        for resource_id, resource in self.resources.items():
            if self._is_render_suppressed(resource_id):
                continue
            node = self._create_node_from_resource(resource)
            self.nodes[resource_id] = node

    def _grid_stack(self, resources_map: Dict[str, Any], base_id: str, child_ids: List[str]) -> List[str]:
        """Create a near-square grid of children using HorizontalStack rows.

        Returns a list of row node IDs that can be added as Children of a container.
        """
        if not child_ids:
            return []
        n = len(child_ids)
        rows = max(1, int(math.ceil(math.sqrt(n))))
        cols = max(1, int(math.ceil(n / rows)))
        row_ids: List[str] = []
        for i in range(rows):
            start = i * cols
            end = start + cols
            row_children = child_ids[start:end]
            if not row_children:
                continue
            row_id = f"{base_id}-grid-row-{i+1}"
            resources_map[row_id] = {
                "Type": "AWS::Diagram::HorizontalStack",
                "Children": row_children,
            }
            row_ids.append(row_id)
        return row_ids

    def _is_render_suppressed(self, resource_id: str) -> bool:
        """Return True if the graph node indicates this resource should not be rendered."""
        if not self.graph:
            return False
        node = self.graph.nodes.get(resource_id)
        if not node:
            return False
        return bool((node.properties or {}).get("render") is False)
    
    def _create_node_from_resource(self, resource: BaseResource) -> DiagramNode:
        """Create a diagram node from a topology resource."""
        # Get the diagram service type
        service_type = self.RESOURCE_TYPE_MAPPING.get(
            resource.resource_type, 
            "AWS::Generic::Resource"
        )
        
        # Create a meaningful label
        label = self._generate_resource_label(resource)
        
        # Build node properties
        properties = {
            "ServiceType": service_type,
            "ResourceId": resource.resource_id,
            "ARN": resource.arn,
            "Region": resource.location.region,
            "Account": resource.location.account_id
        }
        
        # Add resource-specific properties
        properties.update(self._get_resource_specific_properties(resource))
        
        # Add icon if available
        if resource.resource_type in self.RESOURCE_ICONS:
            properties["Icon"] = self.RESOURCE_ICONS[resource.resource_type]
        
        return DiagramNode(
            id=resource.resource_id,
            type=service_type,
            label=label,
            properties=properties
        )
    
    def _generate_resource_label(self, resource: BaseResource) -> str:
        """Generate a meaningful label for a resource."""
        if resource.name:
            return resource.name
        
        # Generate label based on resource type and properties
        if resource.resource_type == ResourceType.VPC:
            cidr = resource.cidr_blocks[0] if hasattr(resource, 'cidr_blocks') and resource.cidr_blocks else ""
            return f"VPC {resource.resource_id}" + (f" ({cidr})" if cidr else "")
        
        elif resource.resource_type == ResourceType.SUBNET:
            cidr = resource.cidr_blocks[0] if hasattr(resource, 'cidr_blocks') and resource.cidr_blocks else ""
            az = resource.location.availability_zone or ""
            return f"Subnet {resource.resource_id}" + (f" ({cidr}, {az})" if cidr and az else "")
        
        elif resource.resource_type == ResourceType.EC2_INSTANCE:
            instance_type = getattr(resource, 'instance_type', None) or resource.properties.get('instance_type', '')
            return f"EC2 Instance {resource.resource_id}" + (f" ({instance_type})" if instance_type else "")
        
        elif resource.resource_type == ResourceType.RDS_INSTANCE:
            engine = getattr(resource, 'engine', None) or resource.properties.get('engine', '')
            return f"RDS {resource.resource_id}" + (f" ({engine})" if engine else "")
        
        else:
            return f"{resource.resource_type.value.replace('_', ' ').title()} {resource.resource_id}"
    
    def _get_resource_specific_properties(self, resource: BaseResource) -> Dict[str, Any]:
        """Get resource-specific properties for the diagram node."""
        properties = {}
        
        if resource.resource_type == ResourceType.VPC:
            if hasattr(resource, 'cidr_blocks') and resource.cidr_blocks:
                properties["CidrBlock"] = resource.cidr_blocks[0]
                if len(resource.cidr_blocks) > 1:
                    properties["AdditionalCidrBlocks"] = resource.cidr_blocks[1:]
        
        elif resource.resource_type == ResourceType.SUBNET:
            if hasattr(resource, 'cidr_blocks') and resource.cidr_blocks:
                properties["CidrBlock"] = resource.cidr_blocks[0]
            if resource.location.availability_zone:
                properties["AvailabilityZone"] = resource.location.availability_zone
            properties["SubnetType"] = self._determine_subnet_type(resource)
        
        elif resource.resource_type == ResourceType.EC2_INSTANCE:
            if hasattr(resource, 'instance_type'):
                properties["InstanceType"] = resource.instance_type
            if hasattr(resource, 'state'):
                properties["State"] = resource.state
            if hasattr(resource, 'private_ip'):
                properties["PrivateIP"] = resource.private_ip
            if hasattr(resource, 'public_ip'):
                properties["PublicIP"] = resource.public_ip
        
        elif resource.resource_type == ResourceType.RDS_INSTANCE:
            if hasattr(resource, 'engine'):
                properties["Engine"] = resource.engine
            if hasattr(resource, 'instance_class'):
                properties["InstanceClass"] = resource.instance_class

        elif resource.resource_type == ResourceType.ROUTE53_HOSTED_ZONE:
            properties["HostedZoneId"] = resource.resource_id
            properties["PrivateZone"] = resource.properties.get("private_zone")
            properties["RecordCount"] = resource.properties.get("resource_record_set_count")

        elif resource.resource_type == ResourceType.ROUTE53_RECORD:
            properties["RecordType"] = resource.properties.get("type")
            properties["TTL"] = resource.properties.get("ttl")
            properties["TargetDnsNames"] = resource.properties.get("target_dns_names")
    
        # Add tags as properties
        if resource.metadata.tags:
            properties["Tags"] = resource.metadata.tags
        
        return properties
    
    def _determine_subnet_type(self, subnet: BaseResource) -> str:
        """Determine if a subnet is public or private based on its properties."""
        # Check tags first
        subnet_type = subnet.metadata.tags.get('Type', '').lower()
        if subnet_type in ['public', 'private']:
            return subnet_type
        
        # Check if subnet has map_public_ip_on_launch property
        if subnet.properties.get('map_public_ip_on_launch', False):
            return 'public'
        
        # Default to private
        return 'private'
    
    def _create_groups(self) -> None:
        """Create logical groupings for diagram organization."""
        # Group by VPC
        vpc_groups = {}
        subnet_groups = {}
        
        for resource_id, resource in self.resources.items():
            # VPC grouping
            if resource.resource_type == ResourceType.VPC:
                vpc_groups[resource_id] = []
            
            # Add resources to their VPC group
            vpc_id = resource.properties.get('vpc_id')
            if vpc_id and vpc_id in vpc_groups:
                vpc_groups[vpc_id].append(resource_id)
            
            # Subnet grouping within VPCs
            if resource.resource_type == ResourceType.SUBNET:
                subnet_groups[resource_id] = []
            
            # Add resources to their subnet group if applicable
            subnet_id = resource.properties.get('subnet_id')
            if subnet_id and subnet_id in subnet_groups:
                subnet_groups[subnet_id].append(resource_id)
        
        # Store groups
        self.groups.update(vpc_groups)
        self.groups.update(subnet_groups)
        
        # Create availability zone groups
        az_groups = {}
        for resource_id, resource in self.resources.items():
            if resource.location.availability_zone:
                az = resource.location.availability_zone
                if az not in az_groups:
                    az_groups[az] = []
                az_groups[az].append(resource_id)
        
        # Only add AZ groups if they contain multiple resources
        for az, resources in az_groups.items():
            if len(resources) > 1:
                self.groups[f"AZ-{az}"] = resources
    
    def _create_connections(self) -> None:
        """Create connections from topology relationships."""
        for relationship in self.relationships:
            connection = self._create_connection_from_relationship(relationship)
            if connection:
                self.connections.append(connection)
    
    def _create_connection_from_relationship(self, relationship: SimpleRelationship) -> Optional[DiagramConnection]:
        """Create a diagram connection from a topology relationship."""
        # Only create connections if both resources are in the view
        if (relationship.source_id not in self.resources or 
            relationship.target_id not in self.resources):
            return None
        
        # Generate connection label based on relationship type
        label = self._generate_connection_label(relationship)
        
        # Set connection properties
        properties = {
            "RelationshipType": relationship.relationship_type.value
        }
        properties.update(relationship.properties)
        
        return DiagramConnection(
            source=relationship.source_id,
            target=relationship.target_id,
            label=label,
            properties=properties
        )
    
    def _generate_connection_label(self, relationship: SimpleRelationship) -> str:
        """Generate a label for a relationship connection."""
        if relationship.relationship_type == RelationshipType.CONTAINS:
            return "contains"
        elif relationship.relationship_type == RelationshipType.ATTACHED_TO:
            return "attached to"
        elif relationship.relationship_type == RelationshipType.ROUTES_TO:
            return "routes to"
        elif relationship.relationship_type == RelationshipType.ALLOWS:
            return "allows"
        elif relationship.relationship_type == RelationshipType.PEERS_WITH:
            return "peers with"
        elif relationship.relationship_type == RelationshipType.CONNECTS_TO:
            return "connects to"
        elif relationship.relationship_type == RelationshipType.TARGETS:
            return "targets"
        elif relationship.relationship_type == RelationshipType.MEMBER_OF:
            return "member of"
        else:
            return relationship.relationship_type.value.replace('_', ' ')
    
    def _generate_diagram_structure(self) -> Dict[str, Any]:
        """Generate the final AWS Labs diagram-as-code structure."""
        # Determine VPCs and Network Core (TGWs only)
        vpc_ids = list(self._get_vpc_children())
        # If single-VPC view, keep peers out of the top-level row
        primary_peers: List[str] = []
        if self.primary_vpc_id and self.primary_vpc_id in vpc_ids:
            primary_peers = [peer for _, peer in self._find_vpc_peerings(self.primary_vpc_id)
                             if peer in vpc_ids]
            vpc_ids = [vid for vid in vpc_ids if vid not in set(primary_peers)]

        network_core_children = []
        for resource_id, resource in self.resources.items():
            if resource.resource_type in [ResourceType.TRANSIT_GATEWAY]:
                network_core_children.append(resource_id)

        resources = {
            "Canvas": {
                "Type": "AWS::Diagram::Canvas",
                "Direction": "vertical",
                "Children": ["AWSCloud"]
            },
            "AWSCloud": {
                "Type": "AWS::Diagram::Cloud",
                "Direction": "vertical", 
                "Preset": "AWSCloudNoLogo",
                "Align": "center",
                "Children": []
            }
        }
        self._global_resource_ids = []

        # Create a horizontal row: VPCs (left) and Network Core (right)
        # Stack of VPCs
        resources["VPCsStack"] = {
            "Type": "AWS::Diagram::HorizontalStack",
            "Children": vpc_ids
        }

        row_children = ["VPCsStack"]
        if network_core_children:
            resources["NetworkCore"] = {
                "Type": "AWS::Diagram::HorizontalStack",
                "Title": "Network Core",
                "Children": network_core_children
            }
            row_children.append("NetworkCore")

        resources["VpcRow"] = {
            "Type": "AWS::Diagram::HorizontalStack",
            "Children": row_children
        }

        # Add VPC resources
        self._add_vpc_resources(resources)
        
        # Phase 1: Use logical subnets rather than per-AZ subnet stacks
        # self._add_az_stacks(resources)
        
        # Add other AWS resources (but skip individual subnets as they're now grouped)
        self._add_aws_resources(resources)
        self._build_global_service_links()

        aws_cloud_children: List[str] = []
        filtered_globals = [
            node_id
            for node_id in self._global_resource_ids
            if self._should_include_global_resource(node_id)
        ]
        if filtered_globals:
            resources["GlobalServicesStack"] = {
                "Type": "AWS::Diagram::HorizontalStack",
                "Title": "Global Edge Services",
                "Children": filtered_globals,
            }
            aws_cloud_children.append("GlobalServicesStack")
        aws_cloud_children.append("VpcRow")
        resources["AWSCloud"]["Children"] = aws_cloud_children
        # Create links from relationships and synthesized intents
        links = self._create_links(resources)
        
        diagram = {
            "Diagram": {
                "DefinitionFiles": [
                    {
                        "Type": "URL",
                        "Url": "https://raw.githubusercontent.com/awslabs/diagram-as-code/main/definitions/definition-for-aws-icons-light.yaml"
                    }
                ],
                "Resources": resources,
                "Links": links
            }
        }
        
        return diagram

    def _get_vpc_children(self) -> List[str]:
        """Get list of VPC resource IDs."""
        vpcs = []
        for resource_id, resource in self.resources.items():
            if resource.resource_type == ResourceType.VPC:
                vpcs.append(resource_id)
        return vpcs
    
    def _add_vpc_resources(self, resources: Dict[str, Any]) -> None:
        """Add VPC resources to the diagram."""
        for resource_id, resource in self.resources.items():
            if resource.resource_type == ResourceType.VPC:
                # Create VPC structure with proper layout
                vpc_children = []

                # Add Internet Gateway as border child (external to VPC)
                border_children = []
                igw_id = self._find_internet_gateway(resource_id)
                if igw_id:
                    border_children.append({
                        "Position": "N",
                        "Resource": igw_id
                    })

                
                # Phase 1: Create logical subnet groups per VPC
                logical_children = self._create_logical_subnet_groups(resource_id, resources)
                vpc_children.extend(logical_children)
                
                # Do not render a separate VPC Endpoints container; endpoints are shown within logical subnets
                
                resources[resource_id] = {
                    "Type": "AWS::EC2::VPC",
                    "Direction": "vertical",
                    "Title": resource.name or resource_id,
                    "Children": vpc_children
                }
                self._register_render_node(resource_id)

                if border_children:
                    resources[resource_id]["BorderChildren"] = border_children

                # If this is the primary VPC, place any peered VPC containers underneath
                if self.primary_vpc_id and resource_id == self.primary_vpc_id:
                    peer_vpcs = [peer for _, peer in self._find_vpc_peerings(resource_id)]
                    # Only include peers that exist as resources; synthetic peers should be present from the view
                    peer_vpcs = [p for p in peer_vpcs if p in self.resources]
                    if peer_vpcs:
                        # Build a vertical stack with the primary VPC on top and peers below
                        panel_id = f"{resource_id}-panel"
                        resources[panel_id] = {
                            "Type": "AWS::Diagram::VerticalStack",
                            "Children": [resource_id] + peer_vpcs
                        }
                        # Replace the VPC with panel in top-level VPCs stack
                        if "VPCsStack" in resources:
                            children = resources["VPCsStack"].get("Children", [])
                            resources["VPCsStack"]["Children"] = [panel_id if c == resource_id else c for c in children]

    def _create_logical_subnet_groups(self, vpc_id: str, resources: Dict[str, Any]) -> List[str]:
        """Synthesize logical Subnet nodes that aggregate subnets across AZs.

        Phase 1: Only place ECS services and Lambda functions under these nodes.
        """
        if not self.logical_subnets_enabled:
            return []

        # Map group_name -> list of subnet ids in this VPC
        group_to_subnets: Dict[str, List[str]] = {}
        for rid, res in self.resources.items():
            if res.resource_type == ResourceType.SUBNET and res.properties.get('vpc_id') == vpc_id:
                group = self._get_subnet_logical_group(res)
                group_to_subnets.setdefault(group, []).append(rid)

        # Map ENI ids to their owning EC2 instances
        eni_to_instance: Dict[str, str] = {}
        for rel in self.relationships:
            if rel.relationship_type != RelationshipType.ATTACHED_TO:
                continue
            src = self.resources.get(rel.source_id)
            tgt = self.resources.get(rel.target_id)
            if not src or not tgt:
                continue
            if (
                src.resource_type == ResourceType.NETWORK_INTERFACE
                and self._supports_eni_dedup_for(tgt)
            ):
                eni_to_instance[src.resource_id] = tgt.resource_id

        for rid, res in self.resources.items():
            if res.resource_type != ResourceType.NETWORK_INTERFACE:
                continue
            attachment = (res.properties or {}).get('attachment') or {}
            instance_id = (
                attachment.get('InstanceId')
                or attachment.get('instanceId')
                or attachment.get('instance_id')
            )
            if not instance_id:
                continue
            owner_resource = self.resources.get(str(instance_id))
            if owner_resource and self._supports_eni_dedup_for(owner_resource):
                eni_to_instance.setdefault(rid, str(instance_id))

        self._eni_instance_map = eni_to_instance.copy()

        synthetic_instances: Dict[str, BaseResource] = {}
        lb_owned_instances: Set[str] = set(
            value for value in eni_to_instance.values() if value
        )
        additions: List[str] = []
        # Precompute Lambda targets from all TGs to avoid multi-parenting
        tg_lambda_target_ids: set = set()
        for rid_tg, res_tg in self.resources.items():
            if res_tg.resource_type == ResourceType.TARGET_GROUP and (res_tg.properties or {}).get('target_type') == 'lambda':
                for t in res_tg.properties.get('targets') or []:
                    if t.get('id'):
                        tg_lambda_target_ids.add(str(t['id']))

        # Global ECS service tracking: precompute which ECS services will be in cluster hierarchies
        # This prevents cross-subnet IP duplication where LB is in different subnet than ECS services
        global_ecs_service_tracking = {}  # service_id -> cluster_info for all ECS services
        all_subnet_ids_in_vpc = []
        for subnet_list in group_to_subnets.values():
            all_subnet_ids_in_vpc.extend(subnet_list)
        vpc_subnet_set = set(all_subnet_ids_in_vpc)

        for svc_id, svc in self.resources.items():
            if svc.resource_type == ResourceType.ECS_SERVICE:
                svc_subnets = set(svc.properties.get('subnet_ids') or [])
                if svc_subnets & vpc_subnet_set:  # Service is in this VPC
                    cluster_arn = svc.properties.get('clusterArn')
                    if cluster_arn:
                        # Find the cluster resource
                        cluster_resource = None
                        for crid, cres in self.resources.items():
                            if (cres.resource_type == ResourceType.ECS_CLUSTER and
                                cres.resource_id == cluster_arn):
                                cluster_resource = cres
                                break

                        if cluster_resource:
                            # Determine which subnet group this service belongs to
                            service_group = None
                            for group_name, subnet_ids in group_to_subnets.items():
                                if svc_subnets & set(subnet_ids):
                                    service_group = group_name
                                    break

                            if service_group:
                                cluster_node_id = f"cluster-{cluster_resource.name}-in-{vpc_id}-{service_group}-logical-subnet"
                                global_ecs_service_tracking[svc_id] = {
                                    'cluster_arn': cluster_arn,
                                    'cluster_node_id': cluster_node_id,
                                    'group': service_group
                                }
                                # Pre-populate group_parent for cross-subnet deduplication
                                self._group_parent[svc_id] = f"cluster-owned-{cluster_node_id}"
                                logger.debug(f"Pre-tracked ECS service {svc.name} in group {service_group} for cluster {cluster_node_id}")

        logger.debug(f"Global ECS tracking: {len(global_ecs_service_tracking)} services pre-tracked across all subnet groups")

        for group_name in sorted(group_to_subnets.keys()):
            subnet_ids = group_to_subnets[group_name]
            group_node_id = f"{vpc_id}-{group_name}-logical-subnet"
            # Determine preset: public vs private
            preset = "PublicSubnet" if "public" in group_name.lower() else "PrivateSubnet"

            # Children (standalone services not in TGs)
            svc_children: List[str] = []
            # Load balancers detected in this group
            lb_children: List[str] = []
            subnet_set = set(subnet_ids)

            # Step 1: Collect ECS clusters and services (for future clustering)
            ecs_clusters_in_subnet = {}  # cluster_arn -> {cluster_resource, services: []}
            ecs_services_found = 0
            ecs_services_with_clusters = 0

            for rid, res in self.resources.items():
                if res.resource_type == ResourceType.ECS_SERVICE:
                    ecs_services_found += 1
                    cluster_arn = res.properties.get('clusterArn')  # Note: property name from collector
                    service_subnets = set(res.properties.get('subnet_ids') or [])

                    # Debug: Check if service has cluster ARN
                    if cluster_arn:
                        ecs_services_with_clusters += 1

                    # Only collect services that are in this logical subnet
                    if cluster_arn and service_subnets & subnet_set:
                        # Find the cluster resource
                        cluster_resource = None
                        for crid, cres in self.resources.items():
                            if (cres.resource_type == ResourceType.ECS_CLUSTER and
                                cres.resource_id == cluster_arn):
                                cluster_resource = cres
                                break

                        if cluster_resource:
                            if cluster_arn not in ecs_clusters_in_subnet:
                                ecs_clusters_in_subnet[cluster_arn] = {
                                    'cluster_resource': cluster_resource,
                                    'services': []
                                }
                            ecs_clusters_in_subnet[cluster_arn]['services'].append(rid)

            # Debug logging
            logger.debug(f"Found {ecs_services_found} ECS services total, {ecs_services_with_clusters} with cluster ARNs")
            if ecs_clusters_in_subnet:
                logger.debug(f"Collected {len(ecs_clusters_in_subnet)} ECS clusters in {group_node_id} with total {sum(len(data['services']) for data in ecs_clusters_in_subnet.values())} services")
            else:
                logger.debug(f"No ECS clusters collected for {group_node_id}")

            # Step 2: Store ECS cluster data for creating clusters under LBs later
            # First, create foundational ECS cluster hierarchy: Cluster -> SG -> Service
            # This ensures all ECS services get proper structure regardless of LB targeting
            cluster_hierarchies = {}  # cluster_arn -> {'node_id': str, 'services': [str]}

            for svc_id, svc in self.resources.items():
                if svc.resource_type == ResourceType.ECS_SERVICE:
                    svc_subnets = set(svc.properties.get('subnet_ids') or [])
                    if svc_subnets & subnet_set:  # Service is in this subnet group
                        cluster_arn = svc.properties.get('clusterArn')
                        cluster_data = ecs_clusters_in_subnet.get(cluster_arn) if cluster_arn else None

                        if cluster_data:
                            # Use global tracking to get the correct cluster node ID
                            global_service_info = global_ecs_service_tracking.get(svc_id)
                            if global_service_info:
                                cluster_node_id = global_service_info['cluster_node_id']

                                # Create cluster hierarchy if not already created
                                if cluster_arn not in cluster_hierarchies:
                                    cluster_resource = cluster_data['cluster_resource']

                                    cluster_hierarchies[cluster_arn] = {
                                        'node_id': cluster_node_id,
                                        'resource': cluster_resource,
                                        'services_by_sg': {}  # sg_key -> [service_info]
                                    }
                                    logger.debug(f"Will create foundational ECS cluster {cluster_node_id}")

                                # Collect service info grouped by security groups
                                cluster_info = cluster_hierarchies[cluster_arn]
                                sgs = svc.properties.get('security_group_ids', [])
                                sg_key = tuple(sorted(sgs)) if sgs else ('no-sg',)

                                if sg_key not in cluster_info['services_by_sg']:
                                    cluster_info['services_by_sg'][sg_key] = []

                                cluster_info['services_by_sg'][sg_key].append({
                                    'id': svc_id,
                                    'name': svc.name,
                                    'sgs': sgs
                                })
                                # Note: self._group_parent already set in global tracking, no need to duplicate
                                logger.debug(f"Grouped ECS service {svc.name} under SG key {sg_key}")

            # Create the actual cluster resources with SG de-duplication
            for cluster_arn, cluster_info in cluster_hierarchies.items():
                cluster_node_id = cluster_info['node_id']
                cluster_resource = cluster_info['resource']

                # Create the cluster node
                resources[cluster_node_id] = {
                    "Type": "AWS::ECS::Cluster",
                    "Title": cluster_resource.name or cluster_resource.resource_id.split('/')[-1],
                    "Children": []
                }
                self._cluster_parent[cluster_node_id] = group_node_id

                # Process each SG group within the cluster
                for sg_key, services in cluster_info['services_by_sg'].items():
                    if sg_key == ('no-sg',):
                        # Services with no security groups - add directly to cluster
                        for svc_info in services:
                            service_node_id = f"ecs-{svc_info['id']}-in-{cluster_node_id}"
                            resources[service_node_id] = {
                                "Type": "AWS::ECS::Service",
                                "Title": svc_info['name'] or svc_info['id']
                            }
                            self._register_render_node(svc_info['id'], service_node_id)
                            resources[cluster_node_id]["Children"].append(service_node_id)
                    else:
                        # Services with security groups - create shared SG containers
                        sgs = list(sg_key)

                        # Create service nodes for this SG group
                        service_nodes = []
                        for svc_info in services:
                            service_node_id = f"ecs-{svc_info['id']}-in-{cluster_node_id}"
                            resources[service_node_id] = {
                                "Type": "AWS::ECS::Service",
                                "Title": svc_info['name'] or svc_info['id']
                            }
                            self._register_render_node(svc_info['id'], service_node_id)
                            service_nodes.append(service_node_id)

                        # Wrap in shared security group containers (innermost to outermost)
                        if len(service_nodes) == 1:
                            current_container = service_nodes[0]
                        else:
                            # Multiple services - create horizontal stack
                            services_stack_id = f"services-stack-{'-'.join([s[:8] for s in sg_key])}-in-{cluster_node_id}"
                            resources[services_stack_id] = {
                                "Type": "AWS::Diagram::HorizontalStack",
                                "Children": service_nodes
                            }
                            current_container = services_stack_id

                        # Wrap in security group containers
                        for sg_id in reversed(sgs):
                            sg_container_id = f"sg-{sg_id}-shared-container-in-{cluster_node_id}"
                            sg_res = self.resources.get(sg_id)
                            sg_name = None
                            if sg_res and getattr(sg_res, 'resource_type', None) == ResourceType.SECURITY_GROUP:
                                sg_name = sg_res.name or sg_res.properties.get('group_name')

                            resources[sg_container_id] = {
                                "Type": "AWS::EC2::SecurityGroup",
                                "Title": f"SG: {sg_name or sg_id}",
                                "Children": [current_container],
                                "FillColor": "rgba(255,244,230,25)",
                                "BorderColor": "rgba(255,140,0,200)"
                            }
                            current_container = sg_container_id

                        # Add the final container to cluster
                        resources[cluster_node_id]["Children"].append(current_container)

                logger.debug(f"Created ECS cluster {cluster_node_id} with {len(cluster_info['services_by_sg'])} SG groups")

            # Now handle other services that don't belong to ECS clusters
            for rid, res in self.resources.items():
                if res.resource_type == ResourceType.ECS_SERVICE:
                    # Skip - already handled in cluster processing above
                    continue
                elif res.resource_type == ResourceType.LAMBDA_FUNCTION:
                    subnets = set(res.properties.get('subnet_ids') or [])
                    # If lambda is a TG target, let TG own it to avoid multi-parent
                    if subnets & subnet_set and res.resource_id not in tg_lambda_target_ids:
                        parent = self._group_parent.get(rid)
                        if parent is None:
                            self._group_parent[rid] = group_node_id
                            svc_children.append(rid)
                elif res.resource_type == ResourceType.EC2_INSTANCE:
                    if res.resource_id in lb_owned_instances:
                        logger.debug(f"Skipping standalone placement for LB-owned EC2 {res.resource_id}")
                        continue
                    props = res.properties or {}
                    subnet_id = props.get('subnet_id')
                    if not subnet_id:
                        # Fall back to primary ENI subnet when available
                        for eni_id in props.get('network_interface_ids', []) or []:
                            eni_res = self.resources.get(eni_id)
                            if eni_res and eni_res.properties.get('subnet_id'):
                                subnet_id = eni_res.properties.get('subnet_id')
                                break
                    if subnet_id and subnet_id in subnet_set:
                        parent = self._group_parent.get(rid)
                        if parent is None:
                            self._group_parent[rid] = group_node_id
                            svc_children.append(rid)
                elif res.resource_type == ResourceType.LOAD_BALANCER:
                    lb_subnets = set(res.properties.get('subnet_ids') or [])
                    if lb_subnets & subnet_set:
                        parent = self._group_parent.get(rid)
                        if parent is None:
                            self._group_parent[rid] = group_node_id
                            lb_children.append(rid)
                # ENI-based services that should be aggregated by logical subnet
                elif res.resource_type in [
                    ResourceType.RDS_INSTANCE,
                    ResourceType.RDS_CLUSTER,
                    ResourceType.ELASTICACHE_CLUSTER,
                    ResourceType.OPENSEARCH_DOMAIN,
                    ResourceType.REDSHIFT_CLUSTER
                ]:
                    # These services use ENIs but we want to show the service, not the ENIs
                    service_subnets = set(res.properties.get('subnet_ids') or [])
                    if service_subnets & subnet_set:
                        parent = self._group_parent.get(rid)
                        if parent is None:
                            self._group_parent[rid] = group_node_id
                            svc_children.append(rid)

            # Build per-LB stacks with TG stacks inside (code_unstable pattern)
            lb_stack_ids: List[str] = []
            clusters_reassigned: Set[str] = set()
            lb_set = set(lb_children)
            logger.debug("Group %s LB children: %s", group_node_id, lb_children)
            # map LB -> TGs (by relationship LB CONTAINS TG)
            lb_to_tgs: Dict[str, List[str]] = {lb: [] for lb in lb_children}
            for rel in self.relationships:
                if rel.relationship_type != RelationshipType.CONTAINS:
                    continue
                if rel.source_id in lb_set:
                    tgt = self.resources.get(rel.target_id)
                    if tgt and tgt.resource_type == ResourceType.TARGET_GROUP:
                        if rel.target_id not in self._tg_parent:
                            self._tg_parent[rel.target_id] = rel.source_id
                            lb_to_tgs[rel.source_id].append(rel.target_id)
            logger.debug("LB->TG map %s: %s", group_node_id, lb_to_tgs)

            group_key = group_node_id

            # NEW GENERALIZED LB/TG STRUCTURE WITH SG-FIRST HIERARCHY
            # Create clean LB vertical stacks with proper SG wrapping and target grouping

            for lb_id in lb_children:
                logger.debug("Iterating LB %s in group %s", lb_id, group_key)
                lb_res = self.resources.get(lb_id)
                lb_title = (lb_res.name if lb_res else None) or lb_id

                # Create the base LB node
                lb_node_id = f"lb-{lb_id}-in-{group_key}"
                resources[lb_node_id] = {
                    "Type": "AWS::ElasticLoadBalancingV2::LoadBalancer",
                    "Title": lb_title,
                }
                self._register_render_node(lb_id, lb_node_id)

                # Wrap LB in its security groups (SG-first principle)
                lb_wrapped_node = lb_node_id
                if lb_res and hasattr(lb_res, 'properties') and lb_res.properties.get('security_group_ids'):
                    sgs = lb_res.properties.get('security_group_ids', [])
                    current_container = lb_node_id

                    for sg_id in reversed(sgs):  # Innermost to outermost
                        sg_container_id = f"sg-{sg_id}-container-lb-{lb_id}-in-{group_key}"
                        sg_res = self.resources.get(sg_id)
                        sg_name = None
                        if sg_res and getattr(sg_res, 'resource_type', None) == ResourceType.SECURITY_GROUP:
                            sg_name = sg_res.name or sg_res.properties.get('group_name')

                        resources[sg_container_id] = {
                            "Type": "AWS::EC2::SecurityGroup",
                            "Title": f"SG: {sg_name or sg_id}",
                            "Children": [current_container],
                            "FillColor": "rgba(255,244,230,25)",
                            "BorderColor": "rgba(255,140,0,200)"
                        }
                        current_container = sg_container_id

                    lb_wrapped_node = current_container
                self._register_render_node(lb_id, lb_wrapped_node)

                # Process target groups for this LB
                lb_tg_sections: List[str] = []
                lb_tg_groups: Dict[Optional[str], List[str]] = {}
                lb_tg_group_order: List[Optional[str]] = []
                tg_entries: List[Dict[str, Any]] = []
                cluster_agg_data: Dict[str, Dict[str, Any]] = {}

                for tg_id in lb_to_tgs.get(lb_id, []):
                    logger.debug("Processing TG %s for LB %s", tg_id, lb_id)
                    tg = self.resources.get(tg_id)
                    if not tg:
                        continue

                    tg_entry = {
                        "tg_id": tg_id,
                        "tg_title": (tg.name if tg else None) or tg_id,
                        "non_cluster_sections": [],
                        "cluster_refs": [],
                        "tg_sg_sets": [],
                        "is_ip_target_group": (tg.properties or {}).get("target_type") == "ip",
                    }

                    cluster_ref_map: Dict[str, Dict[str, Any]] = {}
                    lambda_targets = []
                    ec2_targets = []
                    ip_targets: List[Tuple[dict, str]] = []
                    tg_service_ids = self._tg_service_map.get(tg_id, [])

                    for svc_id, svc in self.resources.items():
                        if svc.resource_type != ResourceType.ECS_SERVICE:
                            continue
                        tgs = set(svc.properties.get('target_group_arns') or [])
                        if tg_id not in tgs:
                            continue
                        cluster_arn = svc.properties.get('clusterArn')
                        cluster_node_id = None
                        if cluster_arn and cluster_arn in cluster_hierarchies:
                            cluster_node_id = cluster_hierarchies[cluster_arn]['node_id']
                        elif global_ecs_service_tracking.get(svc_id):
                            cluster_node_id = global_ecs_service_tracking[svc_id]['cluster_node_id']
                        if not cluster_node_id:
                            continue

                        cluster_ref = cluster_ref_map.get(cluster_node_id)
                        if not cluster_ref:
                            cluster_ref = {
                                "cluster_node_id": cluster_node_id,
                                "same_group": self._cluster_parent.get(cluster_node_id) == group_node_id,
                                "services": set(),
                                "sg_signatures": set(),
                                "tg_entry": tg_entry,
                                "aggregated": False,
                            }
                            cluster_ref_map[cluster_node_id] = cluster_ref
                            tg_entry["cluster_refs"].append(cluster_ref)
                            agg = cluster_agg_data.setdefault(
                                cluster_node_id,
                                {"refs": [], "sg_signatures": set(), "services": set()},
                            )
                            agg["refs"].append(cluster_ref)

                        cluster_ref["services"].add(svc_id)
                        sgs = svc.properties.get('security_group_ids', []) or []
                        sg_sig = tuple(sorted(sgs)) if sgs else ("no-sg",)
                        cluster_ref["sg_signatures"].add(sg_sig)
                        agg = cluster_agg_data[cluster_node_id]
                        agg["sg_signatures"].add(sg_sig)
                        agg["services"].add(svc_id)

                        if sgs:
                            tg_entry["tg_sg_sets"].append({str(sg) for sg in sgs})

                    # Ensure ECS services mapped to this TG are represented in cluster refs
                    for svc_id in tg_service_ids:
                        cluster_info = global_ecs_service_tracking.get(svc_id)
                        if not cluster_info:
                            continue
                        cluster_node_id = cluster_info['cluster_node_id']
                        cluster_ref = cluster_ref_map.get(cluster_node_id)
                        if not cluster_ref:
                            cluster_ref = {
                                "cluster_node_id": cluster_node_id,
                                "same_group": self._cluster_parent.get(cluster_node_id) == group_node_id,
                                "services": set(),
                                "sg_signatures": set(),
                                "tg_entry": tg_entry,
                                "aggregated": False,
                            }
                            cluster_ref_map[cluster_node_id] = cluster_ref
                            tg_entry["cluster_refs"].append(cluster_ref)
                            agg = cluster_agg_data.setdefault(
                                cluster_node_id,
                                {"refs": [], "sg_signatures": set(), "services": set()},
                            )
                            agg["refs"].append(cluster_ref)

                        cluster_ref["services"].add(svc_id)
                        svc = self.resources.get(svc_id)
                        sgs = (svc.properties or {}).get('security_group_ids', []) if svc else []
                        sg_sig = tuple(sorted(sgs)) if sgs else ("no-sg",)
                        cluster_ref["sg_signatures"].add(sg_sig)
                        agg = cluster_agg_data[cluster_node_id]
                        agg["sg_signatures"].add(sg_sig)
                        agg["services"].add(svc_id)
                        if sgs:
                            tg_entry["tg_sg_sets"].append({str(sg) for sg in sgs})

                    for target in tg.properties.get('targets') or []:
                        tid = target.get('id')
                        if not tid:
                            continue
                        if str(tid).startswith('arn:aws:lambda:'):
                            lambda_targets.append(tid)
                        elif str(tid).startswith('i-'):
                            ec2_targets.append(tid)
                        else:
                            ip_str, _ = self._parse_ip_target(target)
                            mapped_svc = self._find_service_id_by_ip(ip_str)
                            mapped_res = self.resources.get(mapped_svc) if mapped_svc else None
                            mapped_type = mapped_res.resource_type if mapped_res else None
                            if not mapped_type and mapped_svc and str(mapped_svc).startswith('i-'):
                                mapped_type = ResourceType.EC2_INSTANCE
                            if mapped_svc and mapped_type == ResourceType.EC2_INSTANCE:
                                if mapped_svc not in ec2_targets:
                                    ec2_targets.append(mapped_svc)
                                self._group_parent.setdefault(mapped_svc, group_node_id)
                                continue
                            if mapped_svc in self._group_parent:
                                continue
                            ip_targets.append((target, ip_str))

                    # If TG is already mapped to ECS services, do not render IP targets
                    if tg_service_ids:
                        ip_targets = []
                    if lambda_targets:
                        lambda_section_id = f"lambda-section-{tg_id}-in-{group_key}"
                        lambda_nodes = []
                        for lambda_id in lambda_targets:
                            if lambda_id in self._group_parent:
                                continue
                            lambda_res = self.resources.get(lambda_id)
                            lambda_node_id = f"lambda-{lambda_id}-in-{group_key}"
                            resources[lambda_node_id] = {
                                "Type": "AWS::Lambda::Function",
                                "Title": (lambda_res.name if lambda_res else None) or lambda_id
                            }
                            lambda_wrapped = lambda_node_id
                            if lambda_res and lambda_res.properties.get('security_group_ids'):
                                sgs = lambda_res.properties.get('security_group_ids', [])
                                current_container = lambda_node_id
                                for sg_id in reversed(sgs):
                                    sg_container_id = f"sg-{sg_id}-container-lambda-{lambda_id}-in-{group_key}"
                                    sg_res = self.resources.get(sg_id)
                                    sg_name = None
                                    if sg_res and getattr(sg_res, 'resource_type', None) == ResourceType.SECURITY_GROUP:
                                        sg_name = sg_res.name or sg_res.properties.get('group_name')
                                    resources[sg_container_id] = {
                                        "Type": "AWS::EC2::SecurityGroup",
                                        "Title": f"SG: {sg_name or sg_id}",
                                        "Children": [current_container],
                                        "FillColor": "rgba(255,244,230,25)",
                                        "BorderColor": "rgba(255,140,0,200)"
                                    }
                                    current_container = sg_container_id
                                lambda_wrapped = current_container
                                tg_entry["tg_sg_sets"].append({str(sg) for sg in sgs})
                            lambda_nodes.append(lambda_wrapped)
                            self._group_parent[lambda_id] = f"lb-tg-owned-{tg_id}"
                        if lambda_nodes:
                            if len(lambda_nodes) == 1:
                                tg_entry["non_cluster_sections"].extend(lambda_nodes)
                            else:
                                resources[lambda_section_id] = {
                                    "Type": "AWS::Diagram::HorizontalStack",
                                    "Children": lambda_nodes
                                }
                                tg_entry["non_cluster_sections"].append(lambda_section_id)

                    if ec2_targets:
                        ec2_section_id = f"ec2-section-{tg_id}-in-{group_key}"
                        ec2_nodes = []
                        target_sg_map: Dict[str, List[str]] = {}
                        sg_sets: List[Set[str]] = []
                        for ec2_id in ec2_targets:
                            ec2_res = self.resources.get(ec2_id)
                            sgs = self._extract_security_group_ids(ec2_res.properties) if ec2_res else []
                            target_sg_map[ec2_id] = sgs
                            if sgs:
                                sg_sets.append(set(sgs))
                        common_sgs: Set[str] = set.intersection(*sg_sets) if sg_sets else set()
                        common_sgs = {sg for sg in common_sgs if sg}
                        for ec2_id in ec2_targets:
                            parent = self._group_parent.get(ec2_id)
                            if parent is not None and parent != group_node_id:
                                continue
                            ec2_node_id = f"ec2-{ec2_id}-in-{group_key}"
                            resources[ec2_node_id] = {
                                "Type": "AWS::EC2::Instance",
                                "Title": ec2_id
                            }
                            ec2_res = self.resources.get(ec2_id)
                            if not ec2_res:
                                ec2_info = self._find_ec2_in_full_topology(ec2_id, vpc_id)
                                if ec2_info:
                                    ec2_res = ec2_info['resource']
                                    title = f"{ec2_res.name or ec2_id}"
                                    if ec2_info['is_cross_account']:
                                        title += f" (Cross-Account: {ec2_info['resource'].location.account_id})"
                                    elif ec2_info['is_cross_region']:
                                        title += f" (Cross-Region: {ec2_info['resource'].location.region})"
                                    elif ec2_info['is_cross_vpc']:
                                        cross_vpc = ec2_info['resource'].properties.get('vpc_id', 'unknown')
                                        title += f" (Cross-VPC: {cross_vpc})"
                                    resources[ec2_node_id]["Title"] = title
                                else:
                                    logger.warning(f"EC2 instance {ec2_id} not found in any account/region in topology")
                            ec2_wrapped = ec2_node_id
                            sgs = target_sg_map.get(ec2_id, [])
                            specific_sgs = [sg for sg in sgs if sg not in common_sgs]
                            if specific_sgs:
                                current_container = ec2_node_id
                                for sg_id in reversed(specific_sgs):
                                    sg_container_id = f"sg-{sg_id}-container-ec2-{ec2_id}-in-{group_key}"
                                    sg_res = self.resources.get(sg_id)
                                    sg_name = None
                                    if sg_res and getattr(sg_res, 'resource_type', None) == ResourceType.SECURITY_GROUP:
                                        sg_name = sg_res.name or sg_res.properties.get('group_name')
                                    resources[sg_container_id] = {
                                        "Type": "AWS::EC2::SecurityGroup",
                                        "Title": f"SG: {sg_name or sg_id}",
                                        "Children": [current_container],
                                        "FillColor": "rgba(255,244,230,25)",
                                        "BorderColor": "rgba(255,140,0,200)"
                                    }
                                    current_container = sg_container_id
                                ec2_wrapped = current_container
                            ec2_nodes.append(ec2_wrapped)
                            self._group_parent[ec2_id] = f"lb-tg-owned-{tg_id}"
                        if ec2_nodes:
                            if len(ec2_nodes) == 1:
                                nodes_to_attach = ec2_nodes
                            else:
                                resources[ec2_section_id] = {
                                    "Type": "AWS::Diagram::HorizontalStack",
                                    "Children": ec2_nodes
                                }
                                nodes_to_attach = [ec2_section_id]
                            if common_sgs:
                                tg_entry["tg_sg_sets"].append(common_sgs)
                                primary_common = sorted(common_sgs)[0]
                                sg_container_id = (
                                    f"sg-{self._sanitize_identifier(primary_common)}-common-container-"
                                    f"{self._sanitize_identifier(tg_id)}-in-{group_key}"
                                )
                                resources[sg_container_id] = {
                                    "Type": "AWS::EC2::SecurityGroup",
                                    "Title": f"SG: {primary_common}",
                                    "Children": nodes_to_attach,
                                    "FillColor": "rgba(255,244,230,25)",
                                    "BorderColor": "rgba(255,140,0,200)",
                                }
                                tg_entry["non_cluster_sections"].append(sg_container_id)
                            else:
                                tg_entry["non_cluster_sections"].extend(nodes_to_attach)

                    ip_nodes: List[str] = []
                    if ip_targets:
                        for target, ip_value in ip_targets:
                            ip, port = self._parse_ip_target(target)

                            if str(ip_value).startswith("arn:aws:elasticloadbalancing:"):
                                target_lb_id = str(ip_value)
                                target_group = self._group_parent.get(target_lb_id)
                                if target_group:
                                    target_node_id = f"lb-{target_lb_id}-in-{target_group}"
                                    continue
                            mapped_svc = self._find_service_id_by_ip(ip)
                            if mapped_svc:
                                mapped_res = self.resources.get(mapped_svc)
                                mapped_type = mapped_res.resource_type if mapped_res else None
                                if not mapped_type and str(mapped_svc).startswith("i-"):
                                    mapped_type = ResourceType.EC2_INSTANCE
                                if mapped_type == ResourceType.EC2_INSTANCE:
                                    if mapped_svc not in ec2_targets:
                                        ec2_targets.append(mapped_svc)
                                    self._group_parent.setdefault(mapped_svc, group_node_id)
                                    continue
                                if mapped_svc in self._group_parent:
                                    continue
                            ip_node_id = (
                                f"lb-ip-target-{self._sanitize_identifier(lb_id)}-"
                                f"{self._sanitize_identifier(tg_id)}-"
                                f"{self._sanitize_identifier(ip)}-{str(port) if port else 'noport'}"
                            )
                            if ip_node_id not in resources:
                                if str(ip_value).startswith("arn:aws:elasticloadbalancing:"):
                                    resources[ip_node_id] = {
                                        "Type": "AWS::ElasticLoadBalancingV2::LoadBalancer",
                                        "Title": self._format_lb_title_from_identifier(ip_value),
                                    }
                                else:
                                    title_lines = [f"IP {ip}{(':'+str(port)) if port else ''}"]
                                    tg_label = tg.name or (tg.resource_id if tg else tg_id)
                                    if tg_label:
                                        tg_display = tg_label.split("/")[-1] if "/" in tg_label else tg_label
                                        title_lines.append(f"TG: {tg_display}")
                                    title_lines.append(f"LB: {lb_title}")
                                    resources[ip_node_id] = {
                                        "Type": "AWS::EC2::Instance",
                                        "Title": "\n".join(title_lines),
                                    }
                            ip_nodes.append(ip_node_id)

                    if ip_nodes:
                        if len(ip_nodes) == 1:
                            tg_entry["non_cluster_sections"].extend(ip_nodes)
                        else:
                            stack_id = (
                                f"ip-section-{self._sanitize_identifier(tg_id)}-in-{group_key}"
                            )
                            resources[stack_id] = {
                                "Type": "AWS::Diagram::HorizontalStack",
                                "Children": ip_nodes,
                            }
                            tg_entry["non_cluster_sections"].append(stack_id)
                        logger.debug("TG %s non-cluster sections: %s", tg_id, tg_entry["non_cluster_sections"])

                tg_entries.append(tg_entry)

                aggregated_clusters: Dict[str, Dict[str, Any]] = {}
                for cluster_id, data in cluster_agg_data.items():
                    refs = data["refs"]
                    if len(refs) < 2:
                        continue
                    sg_sigs = {sig for ref in refs for sig in ref["sg_signatures"] if sig and sig != ("no-sg",)}
                    if len(sg_sigs) != 1:
                        continue
                    sg_sig = next(iter(sg_sigs))
                    if len(sg_sig) != 1:
                        continue
                    if any(
                        len(ref["services"]) != 1 or
                        len(ref["tg_entry"]["cluster_refs"]) != 1 or
                        ref["tg_entry"]["non_cluster_sections"]
                        for ref in refs
                    ):
                        continue
                    for ref in refs:
                        ref["aggregated"] = True
                    aggregated_clusters[cluster_id] = {
                        "sg_id": sg_sig[0],
                        "services": set().union(*(ref["services"] for ref in refs)),
                    }

                for tg_entry in tg_entries:
                    cluster_refs = tg_entry["cluster_refs"]
                    sections = list(tg_entry["non_cluster_sections"])
                    cluster_section_roots: List[str] = []
                    for ref in cluster_refs:
                        if ref["aggregated"]:
                            continue
                        if ref["same_group"]:
                            proj_id = self._create_cluster_projection(
                                resources,
                                ref["cluster_node_id"],
                                ref["services"],
                                lb_node_id,
                                lb_id,
                                tg_entry["tg_id"],
                                group_key,
                            )
                            cluster_section_roots.append(proj_id)
                        else:
                            # Cross-subnet backends: keep cluster/services in their own subnet.
                            # Links are provided via graph edges.
                            pass

                    filtered_sg_sets = [s for s in tg_entry["tg_sg_sets"] if s]
                    logger.debug("TG %s filtered SG sets: %s", tg_entry["tg_id"], filtered_sg_sets)
                    primary_sg_id: Optional[str] = None
                    if filtered_sg_sets:
                        common = set(filtered_sg_sets[0])
                        for s in filtered_sg_sets[1:]:
                            common &= s
                        if common:
                            primary_sg_id = sorted(common)[0]
                        else:
                            primary_sg_id = sorted(filtered_sg_sets[0])[0]

                    if tg_entry["is_ip_target_group"] and not filtered_sg_sets:
                        logger.debug("Adding IP sections %s to LB %s", sections, lb_id)
                        lb_tg_sections.extend(sections)
                        if cluster_section_roots:
                            lb_tg_sections.extend(cluster_section_roots)
                        logger.debug("lb_tg_sections now %s", lb_tg_sections)
                        continue

                    section_roots: List[str] = []
                    if len(sections) > 1:
                        stack_id = (
                            f"tg-section-{self._sanitize_identifier(tg_entry['tg_id'])}-stack-in-{group_key}"
                        )
                        resources[stack_id] = {
                            "Type": "AWS::Diagram::HorizontalStack",
                            "Children": sections,
                        }
                        section_roots.append(stack_id)
                    else:
                        section_roots.extend(sections)

                    if section_roots:
                        if primary_sg_id not in lb_tg_groups:
                            lb_tg_groups[primary_sg_id] = []
                            lb_tg_group_order.append(primary_sg_id)
                        lb_tg_groups[primary_sg_id].extend(section_roots)

                    if cluster_section_roots:
                        lb_tg_sections.extend(cluster_section_roots)

                    # Continue processing next TG entry; SG grouping and stack assembly run after the loop

                for cluster_id, agg in aggregated_clusters.items():
                    if self._cluster_parent.get(cluster_id) == group_key:
                        display_id = self._attach_cluster_display(
                            resources,
                            cluster_id,
                            agg["sg_id"],
                            agg["services"],
                            lb_node_id,
                            lb_id,
                            "aggregated",
                            group_key,
                            clusters_reassigned,
                        )
                        lb_tg_sections.append(display_id)
                    else:
                        # Cross-subnet backends are linked via graph edges only.
                        pass

                for idx, sg_id in enumerate(lb_tg_group_order, start=1):
                    tg_nodes = lb_tg_groups.get(sg_id, [])
                    if not tg_nodes:
                        continue
                    if len(tg_nodes) > 1:
                        stack_id = (
                            f"lb-{self._sanitize_identifier(lb_id)}-tg-stack-{idx}-in-{group_key}"
                        )
                        resources[stack_id] = {
                            "Type": "AWS::Diagram::HorizontalStack",
                            "Children": tg_nodes,
                        }
                        group_children = [stack_id]
                    else:
                        group_children = tg_nodes

                    if sg_id:
                        sg_res = self.resources.get(sg_id)
                        sg_name = None
                        if sg_res and getattr(sg_res, 'resource_type', None) == ResourceType.SECURITY_GROUP:
                            sg_name = sg_res.name or sg_res.properties.get('group_name')
                        container_id = (
                            f"sg-{self._sanitize_identifier(sg_id)}-tg-container-"
                            f"{self._sanitize_identifier(lb_id)}-in-{group_key}"
                        )
                        resources[container_id] = {
                            "Type": "AWS::EC2::SecurityGroup",
                            "Title": f"SG: {sg_name or sg_id}",
                            "Children": group_children,
                            "FillColor": "rgba(255,244,230,25)",
                            "BorderColor": "rgba(255,140,0,200)",
                        }
                        lb_tg_sections.append(container_id)
                    else:
                        if len(group_children) > 1:
                            no_sg_stack_id = (
                                f"lb-{self._sanitize_identifier(lb_id)}-tg-nosg-stack-"
                                f"{idx}-in-{group_key}"
                            )
                            resources[no_sg_stack_id] = {
                                "Type": "AWS::Diagram::HorizontalStack",
                                "Children": group_children,
                            }
                            lb_tg_sections.append(no_sg_stack_id)
                        else:
                            lb_tg_sections.extend(group_children)

                logger.debug("LB %s sections before fallback: %s", lb_id, lb_tg_sections)
                stack_id = self._build_lb_stack(
                    lb_id,
                    group_key,
                    lb_wrapped_node,
                    lb_tg_sections,
                    resources,
                )
                logger.debug("Stack id result for %s: %s", lb_id, stack_id)
                if stack_id:
                    lb_stack_ids.append(stack_id)
                    self._register_render_node(lb_id, stack_id)
                else:
                    logger.debug("No stack built for %s (sections=%s)", lb_id, lb_tg_sections)

            logger.debug("Group %s LB stacks: %s", group_node_id, lb_stack_ids)
            # Handle other infrastructure rows (ENI, VPCE, NAT, TGW)
            extra_row_ids: List[str] = []

            # NAT Gateways row
            nat_ids: List[str] = []
            for rid2, res2 in self.resources.items():
                if res2.resource_type == ResourceType.NAT_GATEWAY and res2.properties.get('subnet_id') in subnet_set:
                    nat_ids.append(rid2)
            if nat_ids:
                nat_row_id = f"{group_node_id}-nat-row"
                resources[nat_row_id] = {
                    "Type": "AWS::Diagram::HorizontalStack",
                    "Children": nat_ids,
                }
                extra_row_ids.append(nat_row_id)

            # TGW attachment row (synthetic nodes for subnet<->TGW attachments)
            tgw_attach_ids: List[str] = []
            for rel in self.relationships:
                if rel.relationship_type != RelationshipType.CONNECTS_TO:
                    continue
                src = self.resources.get(rel.source_id)
                tgt = self.resources.get(rel.target_id)
                if not src or not tgt:
                    continue
                if (
                    (src.resource_type == ResourceType.SUBNET and src.resource_id in subnet_set and tgt.resource_type == ResourceType.TRANSIT_GATEWAY)
                    or (tgt.resource_type == ResourceType.SUBNET and tgt.resource_id in subnet_set and src.resource_type == ResourceType.TRANSIT_GATEWAY)
                ):
                    subnet_id = src.resource_id if src.resource_type == ResourceType.SUBNET else tgt.resource_id
                    tgw_id = tgt.resource_id if tgt.resource_type == ResourceType.TRANSIT_GATEWAY else src.resource_id
                    attach_id = f"{subnet_id}-to-{tgw_id}-tgw-attach"
                    if attach_id not in resources:
                        az = None
                        sub_res = self.resources.get(subnet_id)
                        if sub_res and sub_res.location and sub_res.location.availability_zone:
                            az = sub_res.location.availability_zone
                        title = f"TGW Attachment" + (f" ({az})" if az else "")
                        resources[attach_id] = {"Type": "AWS::EC2::TransitGateway", "Title": title}
                    tgw_attach_ids.append(attach_id)
            if tgw_attach_ids:
                tgw_row_id = f"{group_node_id}-tgw-attach-row"
                resources[tgw_row_id] = {
                    "Type": "AWS::Diagram::HorizontalStack",
                    "Children": list(dict.fromkeys(tgw_attach_ids)),
                }
                extra_row_ids.append(tgw_row_id)

            # VPC Endpoints within this group's subnets -> add as service nodes (avoid VPC-level row)
            vpce_nodes: List[str] = []
            for rid2, res2 in self.resources.items():
                if res2.resource_type == ResourceType.VPC_ENDPOINT:
                    ep_subnets = set(res2.properties.get('subnet_ids') or [])
                    if not (ep_subnets & subnet_set):
                        continue
                    service_name = self._get_vpc_endpoint_service(res2)
                    owner_raw = res2.properties.get('service_owner') or ''
                    is_aws_managed = self._is_aws_managed_vpce(res2, owner_raw)
                    owner = owner_raw.lower()
                    # If AWS-owned, map to service icon; else use VPCE node with owner note
                    node_id = f"{rid2}-in-{group_node_id}"
                    if is_aws_managed:
                        mapped = self._vpce_service_icon(res2.properties.get('service_name') or '')
                        if mapped:
                            svc_type, svc_title = mapped
                            resources[node_id] = {"Type": svc_type, "Title": f"{svc_title} (via VPC Endpoint)"}
                        else:
                            title = f"VPC Endpoint" + (f"\n({service_name})" if service_name else "")
                            resources[node_id] = {"Type": "AWS::EC2::VPCEndpoint", "Title": title}
                    else:
                        title_lines = ["VPC Endpoint"]
                        sid = self._get_vpce_service_id(res2.properties.get('service_name') or '')
                        if service_name:
                            title_lines.append(f"({service_name})")
                        elif sid:
                            title_lines.append(f"({sid})")
                        if owner:
                            title_lines.append(f"Owner: {owner}")
                        resources[node_id] = {"Type": "AWS::EC2::VPCEndpoint", "Title": "\n".join(title_lines)}
                    vpce_nodes.append(node_id)

            # Orphan ENIs (exclude VPCE and ENIs member_of ECS services and LB/Lambda ENIs, NAT/TGW ENIs)
            # Collect VPCE ENI IDs
            vpce_eni_ids: set = set()
            for rid2, res2 in self.resources.items():
                if res2.resource_type == ResourceType.VPC_ENDPOINT:
                    for eni_id in res2.properties.get('network_interface_ids', []) or []:
                        vpce_eni_ids.add(eni_id)
            # Collect ENIs that are MEMBER_OF ECS services
            member_eni_ids: set = set()
            for rel in self.relationships:
                if rel.relationship_type == RelationshipType.MEMBER_OF:
                    src = self.resources.get(rel.source_id)
                    tgt = self.resources.get(rel.target_id)
                    if src and src.resource_type == ResourceType.NETWORK_INTERFACE and tgt and tgt.resource_type == ResourceType.ECS_SERVICE:
                        member_eni_ids.add(src.resource_id)
            # Collect LB SGs in this group
            lb_sg_ids: set = set()
            for lb_id in lb_children:
                lb_res = self.resources.get(lb_id)
                if not lb_res:
                    continue
                for sg in lb_res.properties.get('security_group_ids', []) or []:
                    lb_sg_ids.add(sg)

            # Collect NAT-related ENI IDs in this group's subnets (e.g., from NatGatewayAddresses)
            nat_eni_ids: set = set()
            for rid2, res2 in self.resources.items():
                if res2.resource_type == ResourceType.NAT_GATEWAY and res2.properties.get('subnet_id') in subnet_set:
                    for addr in res2.properties.get('nat_gateway_addresses', []) or []:
                        eni_id = addr.get('NetworkInterfaceId') or addr.get('NetworkInterface')
                        if eni_id:
                            nat_eni_ids.add(eni_id)

            # Collect ENI IDs that belong to aggregated services (RDS, ElastiCache, etc.)
            service_eni_ids: set = set()
            for rid2, res2 in self.resources.items():
                if res2.resource_type in [
                    ResourceType.RDS_INSTANCE,
                    ResourceType.RDS_CLUSTER,
                    ResourceType.ELASTICACHE_CLUSTER,
                    ResourceType.OPENSEARCH_DOMAIN,
                    ResourceType.REDSHIFT_CLUSTER
                ]:
                    # Get ENI IDs associated with these services
                    for eni_id in res2.properties.get('network_interface_ids', []) or []:
                        service_eni_ids.add(eni_id)
                    # Also check for ENI relationships
                    for rel in self.relationships:
                        if (rel.relationship_type == RelationshipType.MEMBER_OF and
                            rel.target_id == rid2):
                            eni = self.resources.get(rel.source_id)
                            if eni and eni.resource_type == ResourceType.NETWORK_INTERFACE:
                                service_eni_ids.add(eni.resource_id)

            eni_ids: List[str] = []
            group_instance_ids: Set[str] = set()
            for rid2, res2 in self.resources.items():
                if res2.resource_type == ResourceType.NETWORK_INTERFACE and res2.properties.get('subnet_id') in subnet_set:
                    if self._is_render_suppressed(rid2):
                        continue
                    desc = (res2.properties.get('description') or '').lower()
                    sgs = set(res2.properties.get('security_group_ids') or [])
                    iface_type = (res2.properties.get('interface_type') or '').lower()
                    if self._is_global_service_eni(res2):
                        continue
                    if rid2 in eni_to_instance:
                        group_instance_ids.add(eni_to_instance[rid2])
                        continue
                    vpce_info = self._get_vpce_for_eni(rid2)
                    if vpce_info and self._is_aws_managed_vpce(vpce_info[0], vpce_info[1]):
                        continue
                    # Heuristics: skip ENIs for VPCE, ECS-owned, LB-owned, Lambda ENIs, NAT ENIs, TGW ENIs, and aggregated services
                    if (
                        res2.resource_id in vpce_eni_ids or
                        res2.resource_id in member_eni_ids or
                        res2.resource_id in service_eni_ids or  # ENIs belonging to RDS, ElastiCache, etc.
                        'elb' in desc or 'elastic load balancer' in desc or
                        'lambda' in desc or
                        'rds' in desc or 'aurora' in desc or  # RDS/Aurora ENIs
                        'elasticache' in desc or 'redis' in desc or 'memcached' in desc or  # ElastiCache ENIs
                        'opensearch' in desc or 'elasticsearch' in desc or  # OpenSearch ENIs
                        'redshift' in desc or  # Redshift ENIs
                        (lb_sg_ids and (sgs & lb_sg_ids)) or
                        res2.resource_id in nat_eni_ids or
                        iface_type == 'nat_gateway' or 'nat gateway' in desc or
                        iface_type == 'transit_gateway' or 'transit gateway' in desc or 'tgw' in desc
                    ):
                        continue
                    eni_ids.append(rid2)

            # Ensure LB-owned EC2 instances mapped from ENIs appear in the group
            for instance_id in sorted(group_instance_ids):
                if self._group_parent.get(instance_id):
                    continue
                instance_resource = self.resources.get(instance_id)
                if not instance_resource:
                    instance_resource = synthetic_instances.get(instance_id)
                    if not instance_resource:
                        instance_info = self._find_ec2_in_full_topology(instance_id, vpc_id)
                        if instance_info:
                            instance_resource = instance_info['resource']
                            synthetic_instances[instance_id] = instance_resource
                if instance_resource:
                    # Ensure security group IDs are normalized
                    props = instance_resource.properties or {}
                    if 'security_group_ids' not in props and props.get('security_groups'):
                        sg_ids = []
                        for entry in props.get('security_groups') or []:
                            if isinstance(entry, dict):
                                sg_id = entry.get('GroupId') or entry.get('group_id') or entry.get('id')
                                if sg_id:
                                    sg_ids.append(str(sg_id))
                            elif entry:
                                sg_ids.append(str(entry))
                        if sg_ids:
                            instance_resource.properties = dict(props)
                            instance_resource.properties['security_group_ids'] = sg_ids
                    self.resources.setdefault(instance_id, instance_resource)
                    self._group_parent[instance_id] = group_node_id
                    if instance_id not in svc_children:
                        svc_children.append(instance_id)
            # Combine VPCE and ENIs into a single auxiliary row (keeps service grid clean)
            aux_nodes = vpce_nodes + eni_ids
            if aux_nodes:
                aux_row_id = f"{group_node_id}-aux-row"
                resources[aux_row_id] = {"Type": "AWS::Diagram::HorizontalStack", "Children": aux_nodes}
                extra_row_ids.append(aux_row_id)

            # LB stacks already have SG wrapping built-in from the new implementation
            wrapped_lb_stacks = lb_stack_ids[:] or [
                f"lb-{lb_id}-in-{group_node_id}" for lb_id in lb_children
            ]

            # Build ordered content for this group
            rows: List[str] = []
            # 1) LB stacks and standalone services arranged in grid
            service_nodes = wrapped_lb_stacks[:]

            # Add standalone ECS clusters (those not integrated into LB vertical stacks)
            for cluster_arn, cluster_info in cluster_hierarchies.items():
                cluster_node_id = cluster_info['node_id']
                if cluster_node_id in clusters_reassigned or cluster_node_id in self._cluster_lb_consumers:
                    continue
                # Check if this cluster was used in any LB stack
                cluster_used_in_lb_stack = False
                for stack_id in wrapped_lb_stacks:
                    # Check if cluster is referenced in any LB stack children
                    if cluster_node_id in resources.get(stack_id, {}).get('Children', []):
                        cluster_used_in_lb_stack = True
                        break

                    # Also check nested TG children within the LB stack
                    for child_id in resources.get(stack_id, {}).get('Children', []):
                        if cluster_node_id in resources.get(child_id, {}).get('Children', []):
                            cluster_used_in_lb_stack = True
                            break

                if not cluster_used_in_lb_stack:
                    # This is a standalone cluster - add it to service nodes
                    service_nodes.append(cluster_node_id)
                    logger.debug(f"Added standalone ECS cluster {cluster_node_id} to subnet group")

            aggregated_ec2_groups: Dict[Tuple[str, ...], List[str]] = {}
            remaining_services: List[str] = []

            for rid in svc_children:
                resource = self.resources.get(rid)
                if resource and resource.resource_type == ResourceType.EC2_INSTANCE:
                    sg_ids = tuple(sorted(self._extract_security_group_ids(resource.properties)))
                    if sg_ids:
                        aggregated_ec2_groups.setdefault(sg_ids, []).append(rid)
                        continue
                remaining_services.append(rid)

            for sg_ids, instance_ids in aggregated_ec2_groups.items():
                label = self._sanitize_identifier("-".join(sg_ids)) or "group"
                wrapped = self._wrap_nodes_with_security_groups(
                    resources,
                    instance_ids,
                    list(sg_ids),
                    group_node_id,
                    f"ec2-agg-{label}-{len(instance_ids)}",
                )
                service_nodes.append(wrapped)

            for rid in remaining_services:
                resource = self.resources.get(rid)
                if resource and hasattr(resource, 'properties'):
                    sgs = self._extract_security_group_ids(resource.properties)
                    if sgs:
                        wrapped = self._wrap_nodes_with_security_groups(
                            resources,
                            [rid],
                            sgs,
                            group_node_id,
                            f"svc-{self._sanitize_identifier(rid)}",
                        )
                        service_nodes.append(wrapped)
                        continue
                service_nodes.append(rid)
            grid_rows = self._grid_stack(resources, f"{group_node_id}-grid", service_nodes)
            rows.extend(grid_rows or service_nodes)
            # 3) Then aux rows (VPCE/ENIs/NAT/TGW)
            rows.extend(extra_row_ids)
            # Append CIDR ranges of member subnets to the logical subnet title for clarity
            cidrs: List[str] = []
            for sid in subnet_ids:
                sres = self.resources.get(sid)
                if not sres:
                    continue
                c = None
                try:
                    if hasattr(sres, 'cidr_blocks') and sres.cidr_blocks:
                        c = sres.cidr_blocks[0]
                    else:
                        c = sres.properties.get('cidr_block') or sres.properties.get('CidrBlock')
                except Exception:
                    c = None
                if c and c not in cidrs:
                    cidrs.append(c)
            cidr_suffix = f" ({', '.join(cidrs)})" if cidrs else ""

            resources[group_node_id] = {
                "Type": "AWS::EC2::Subnet",
                "Title": group_name.replace('-', ' ').title() + cidr_suffix,
                "Preset": preset,
                "Children": rows,
            }
            additions.append(group_node_id)

        self._prune_consumed_clusters(resources)
        return additions
    
    def _prune_consumed_clusters(self, resources: Dict[str, Any]) -> None:
        """Remove duplicate cluster renderings from logical subnet grids."""
        if not self._cluster_lb_consumers:
            return

        for cluster_id in list(self._cluster_lb_consumers):
            for parent_id, parent in list(resources.items()):
                if parent_id.startswith("lb-") and parent.get("Type") == "AWS::Diagram::VerticalStack":
                    continue
                if parent_id.startswith("cluster-display-"):
                    continue
                children = parent.get("Children")
                if not children or cluster_id not in children:
                    continue
                filtered = [child for child in children if child != cluster_id]
                if filtered:
                    parent["Children"] = filtered
                else:
                    parent.pop("Children", None)
                    self._remove_empty_container(resources, parent_id)

    def _remove_empty_container(self, resources: Dict[str, Any], node_id: str) -> None:
        """Remove empty stack containers and clean up parent references."""
        node = resources.get(node_id)
        if not node:
            return
        node_type = node.get("Type")
        if node.get("Children") or node_type not in {"AWS::Diagram::HorizontalStack", "AWS::Diagram::VerticalStack"}:
            return
        resources.pop(node_id, None)
        for parent_id, parent in list(resources.items()):
            children = parent.get("Children")
            if not children or node_id not in children:
                continue
            filtered = [child for child in children if child != node_id]
            if filtered:
                parent["Children"] = filtered
            else:
                    parent.pop("Children", None)
                    self._remove_empty_container(resources, parent_id)

    def _wrap_nodes_with_security_groups(
        self,
        resources: Dict[str, Any],
        node_ids: List[str],
        sg_ids: Sequence[str],
        group_node_id: str,
        label: str,
    ) -> str:
        """Wrap one or more nodes in nested security group containers."""
        if not node_ids:
            raise ValueError("node_ids must not be empty")

        if len(node_ids) == 1:
            current_id = node_ids[0]
        else:
            digest_source = "::".join(sorted(node_ids))
            digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:8]
            stack_id = f"{label}-stack-{digest}-in-{group_node_id}"
            resources[stack_id] = {
                "Type": "AWS::Diagram::HorizontalStack",
                "Children": node_ids,
            }
            current_id = stack_id

        for depth, sg_id in enumerate(reversed(sg_ids)):
            container_id = (
                f"sg-{self._sanitize_identifier(sg_id)}-{label}-layer-{depth}-in-{group_node_id}"
            )
            sg_res = self.resources.get(sg_id)
            sg_name = None
            if sg_res and getattr(sg_res, "resource_type", None) == ResourceType.SECURITY_GROUP:
                sg_name = sg_res.name or sg_res.properties.get("group_name")
            resources[container_id] = {
                "Type": "AWS::EC2::SecurityGroup",
                "Title": f"SG: {sg_name or sg_id}",
                "Children": [current_id],
                "FillColor": "rgba(255,244,230,25)",
                "BorderColor": "rgba(255,140,0,200)",
            }
            current_id = container_id
        return current_id

    def _add_aws_resources(self, resources: Dict[str, Any]) -> None:
        """Add AWS resources to the diagram."""
        for resource_id, resource in self.resources.items():
            node_id = self._diagram_node_id(resource)
            if node_id in resources:  # Skip if already added
                continue
                
            service_type = self.RESOURCE_TYPE_MAPPING.get(
                resource.resource_type, 
                "AWS::Generic::Resource"
            )
            
            resource_def = {
                "Type": service_type,
                "Title": resource.name or resource_id
            }
            
            # Add specific configurations for different resource types
            if resource.resource_type == ResourceType.SUBNET:
                # Skip rendering individual subnets when logical subnet grouping is enabled
                if self.logical_subnets_enabled:
                    continue

            elif resource.resource_type == ResourceType.LOAD_BALANCER:
                # Skip standalone LB node; rendered via per-LB stacks in logical subnets
                continue

            elif resource.resource_type == ResourceType.TARGET_GROUP:
                # Do not render TGs as standalone nodes in service-centric layout
                continue

            elif resource.resource_type == ResourceType.ECS_SERVICE:
                # Skip ECS services that have target groups - they should be handled by TG clustering logic
                has_tgs = bool(resource.properties.get('target_group_arns'))
                if has_tgs:
                    continue

                # Append backend ENI IPs under the service title when available
                title = resource.name or resource_id
                ips = self._get_service_backend_ips(resource_id)
                resource_def["Title"] = self._format_title_with_ips(title, ips)

            elif resource.resource_type == ResourceType.LAMBDA_FUNCTION:
                # Append guessed Lambda ENI IPs (best-effort) under the title
                title = resource.name or resource_id
                ips = self._get_service_backend_ips(resource_id)
                if not ips:
                    ips = self._guess_lambda_ips(resource)
                resource_def["Title"] = self._format_title_with_ips(title, ips)

            elif resource.resource_type == ResourceType.NETWORK_INTERFACE:
                if self._is_global_service_eni(resource):
                    continue
                owner_resource = self._find_eni_owner_resource(resource_id)
                if owner_resource and self._supports_eni_dedup_for(owner_resource):
                    continue
                vpce_info = self._get_vpce_for_eni(resource_id)
                if vpce_info and self._is_aws_managed_vpce(vpce_info[0], vpce_info[1]):
                    continue  # Service icon rendered via VPCE node; skip raw ENI
                # Show ENI private IP beneath the title
                title = resource.name or resource_id
                ip = (resource.properties or {}).get('private_ip')
                title_lines = [title]
                if ip:
                    title_lines.append(str(ip))
                if owner_resource:
                    owner_label = owner_resource.name or owner_resource.resource_id
                    owner_type = owner_resource.resource_type.value.replace("_", " ").title()
                    title_lines.append(f"Attached to: {owner_label} ({owner_type})")
                else:
                    inferred = self._infer_eni_owner_label(resource)
                    if inferred:
                        title_lines.append(f"Attached to: {inferred}")
                resource_def["Title"] = "\n".join(title_lines)
                    
            elif resource.resource_type == ResourceType.INTERNET_GATEWAY:
                # Internet Gateway styling
                resource_def["IconFill"] = {"Type": "rect"}
                
            elif resource.resource_type == ResourceType.VPC_ENDPOINT:
                # Skip top-level VPCE nodes; endpoints are rendered within logical subnets
                continue
            elif resource.resource_type == ResourceType.NAT_GATEWAY:
                # Keep default rendering; avoid unsupported preset names
                pass
            elif resource.resource_type == ResourceType.VPC_PEERING:
                # Skip standalone peering nodes; we render peering indicators under VPC panels
                continue
            elif resource.resource_type == ResourceType.ROUTE53_RECORD:
                # Route53 records are collected for linking but not shown individually
                continue
            
            resources[node_id] = resource_def
            self._register_render_node(resource_id, node_id)

            region_label = getattr(getattr(resource, "location", None), "region", "")
            if region_label and region_label.lower() in {"aws-global", "global"}:
                if node_id not in self._global_resource_ids:
                    self._global_resource_ids.append(node_id)
            if resource.resource_type in (
                ResourceType.GLOBAL_ACCELERATOR,
                ResourceType.CLOUDFRONT_DISTRIBUTION,
            ):
                self._global_service_ids.add(resource_id)
    
    def _create_links(self, resources: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Create links for the diagram.

        Service-centric gating: only draw synthesized LB -> backend target links
        to preserve the layout and avoid SG/membership link noise.
        """
        links: List[Dict[str, Any]] = []
        seen: Set[Tuple[str, str]] = set()

        for lb_id, svc_id in sorted(self._lb_service_links):
            source_id = self._resource_render_map.get(lb_id, lb_id)
            target_id = self._resource_render_map.get(svc_id, svc_id)
            if source_id not in resources or target_id not in resources:
                continue
            key = (source_id, target_id)
            if key in seen:
                continue
            seen.add(key)
            link = {
                "Source": source_id,
                "Target": target_id,
                "Type": "orthogonal",
                "SourcePosition": "S",
                "TargetPosition": "N",
            }
            links.append(link)
        for source_id, target_id in sorted(self._global_service_links):
            if source_id not in resources or target_id not in resources:
                continue
            key = (source_id, target_id)
            if key in seen:
                continue
            seen.add(key)
            links.append(
                {
                    "Source": source_id,
                    "Target": target_id,
                    "Type": "orthogonal",
                    "SourcePosition": "S",
                    "TargetPosition": "N",
                }
            )
        return links
    
    def _find_internet_gateway(self, vpc_id: str) -> Optional[str]:
        """Find the Internet Gateway attached to a VPC."""
        for relationship in self.relationships:
            if (relationship.relationship_type == RelationshipType.ATTACHED_TO and
                relationship.target_id == vpc_id):
                source_resource = self.resources.get(relationship.source_id)
                if source_resource and source_resource.resource_type == ResourceType.INTERNET_GATEWAY:
                    return relationship.source_id
        return None

    def _find_transit_gateways_for_vpc(self, vpc_id: str) -> List[str]:
        """Find Transit Gateways connected to a VPC."""
        tgws: List[str] = []
        for relationship in self.relationships:
            if relationship.relationship_type != RelationshipType.CONNECTS_TO:
                continue
            src = self.resources.get(relationship.source_id)
            tgt = self.resources.get(relationship.target_id)
            if not src or not tgt:
                continue
            # VPC <-> TGW connections
            if ((src.resource_type == ResourceType.VPC and tgt.resource_type == ResourceType.TRANSIT_GATEWAY and src.resource_id == vpc_id)):
                tgws.append(tgt.resource_id)
            elif ((tgt.resource_type == ResourceType.VPC and src.resource_type == ResourceType.TRANSIT_GATEWAY and tgt.resource_id == vpc_id)):
                tgws.append(src.resource_id)
        # De-duplicate
        return list(dict.fromkeys(tgws))

    def _find_vpc_peerings(self, vpc_id: str) -> List[Tuple[str, str]]:
        """Find VPC peering connections involving the given VPC.

        Returns a list of tuples: (peering_resource_id, peer_vpc_id)
        """
        results: List[Tuple[str, str]] = []
        for rid, res in self.resources.items():
            if res.resource_type != ResourceType.VPC_PEERING:
                continue
            req = (res.properties.get('requester_vpc') or {})
            acc = (res.properties.get('accepter_vpc') or {})
            req_vpc = req.get('VpcId')
            acc_vpc = acc.get('VpcId')
            if req_vpc == vpc_id and acc_vpc:
                results.append((rid, acc_vpc))
            elif acc_vpc == vpc_id and req_vpc:
                results.append((rid, req_vpc))
        return results

    def _extract_primary_vpc_id(self) -> Optional[str]:
        try:
            fa = self.view.metadata.get('filters_applied', [])
            for f in fa:
                if f.get('type') == 'vpc':
                    vals = f.get('values') or []
                    if vals:
                        return vals[0]
        except Exception:
            pass
        return None
    
    def _find_vpc_endpoints(self, vpc_id: str) -> List[str]:
        """Find VPC endpoints in a VPC."""
        endpoints = []
        for resource_id, resource in self.resources.items():
            if (resource.resource_type == ResourceType.VPC_ENDPOINT and
                resource.properties.get('vpc_id') == vpc_id):
                endpoints.append(resource_id)
        return endpoints
    
    def _create_az_stacks(self, vpc_id: str) -> List[str]:
        """Create availability zone stacks for subnets."""
        # Group subnets by availability zone
        az_groups = {}
        for resource_id, resource in self.resources.items():
            if (resource.resource_type == ResourceType.SUBNET and
                resource.properties.get('vpc_id') == vpc_id):
                az = resource.location.availability_zone
                if az:
                    if az not in az_groups:
                        az_groups[az] = []
                    az_groups[az].append(resource_id)
        
        # Create stacks for each AZ
        stacks = []
        if len(az_groups) > 1:
            # Multiple AZs - create horizontal stack of AZ stacks
            az_stack_ids = []
            for az, subnets in az_groups.items():
                az_stack_id = f"{vpc_id}-{az}"
                az_stack_ids.append(az_stack_id)
                # Don't add to resources here, will be handled by _add_aws_resources
            
            # Create main horizontal stack for all AZs
            main_stack_id = f"{vpc_id}-azs"
            stacks.append(main_stack_id)
            # This will be handled in _add_aws_resources method
        else:
            # Single AZ or no AZ grouping - just return subnets
            for subnets in az_groups.values():
                stacks.extend(subnets)
        
        return stacks
    
    def _get_vpc_endpoint_service(self, endpoint_resource: BaseResource) -> Optional[str]:
        """Extract service name from VPC endpoint."""
        service_name = endpoint_resource.properties.get('service_name', '')
        if service_name:
            # Extract service from com.amazonaws.region.service format
            parts = service_name.split('.')
            if len(parts) >= 3:
                return parts[-1].upper()  # e.g., 'S3', 'ECR', 'SSM'
        return None

    def _get_service_backend_ips(self, service_id: str) -> List[str]:
        """Collect backend ENI IPs for a service by scanning MEMBER_OF relationships.

        Works for ECS services (explicit MEMBER_OF from ENI -> Service). For Lambda,
        this may return empty unless ENI MEMBER_OF relationships exist; a separate
        heuristic is applied in _guess_lambda_ips.
        """
        ips: List[str] = []
        seen = set()
        for rel in self.relationships:
            if rel.relationship_type != RelationshipType.MEMBER_OF:
                continue
            if rel.target_id != service_id:
                continue
            eni = self.resources.get(rel.source_id)
            if not eni or getattr(eni, 'resource_type', None) != ResourceType.NETWORK_INTERFACE:
                continue
            ip = (eni.properties or {}).get('private_ip')
            if ip and ip not in seen:
                seen.add(ip)
                ips.append(ip)
        return ips

    def _guess_lambda_ips(self, lambda_res: BaseResource) -> List[str]:
        """Best-effort guess of Lambda ENI IPs by matching ENI descriptions and subnets.

        Uses function name/ARN substring match plus subnet filtering.
        """
        ips: List[str] = []
        seen = set()
        lname = (lambda_res.name or lambda_res.resource_id).lower()
        lsubs = set((lambda_res.properties or {}).get('subnet_ids') or [])
        for rid, res in self.resources.items():
            if getattr(res, 'resource_type', None) != ResourceType.NETWORK_INTERFACE:
                continue
            desc = (res.properties or {}).get('description') or ''
            sid = (res.properties or {}).get('subnet_id')
            if lname and lname in desc.lower() and (not lsubs or (sid in lsubs)):
                ip = (res.properties or {}).get('private_ip')
                if ip and ip not in seen:
                    seen.add(ip)
                    ips.append(ip)
        return ips

    def _format_title_with_ips(self, title: str, ips: List[str], limit: int = 5) -> str:
        if not ips:
            return title
        shown = ips[:limit]
        suffix = " …" if len(ips) > limit else ""
        return f"{title}\n{', '.join(shown)}{suffix}"

    def _find_service_id_by_ip(self, ip: str) -> Optional[str]:
        """Return the service/func resource_id that owns the ENI for this IP, if known.

        Matches ENI.private_ip == ip, then walks MEMBER_OF relationships to
        ECS_SERVICE or LAMBDA_FUNCTION.
        """
        eni_id = None
        eni_resource: Optional[BaseResource] = None
        for rid, res in self.resources.items():
            if getattr(res, 'resource_type', None) == ResourceType.NETWORK_INTERFACE and (res.properties or {}).get('private_ip') == ip:
                eni_id = rid
                eni_resource = res
                break
        if not eni_id:
            return None
        if eni_resource:
            attachment = (eni_resource.properties or {}).get('attachment') or {}
            instance_id = (
                attachment.get('InstanceId')
                or attachment.get('instance_id')
                or attachment.get('instanceId')
            )
            if instance_id:
                return instance_id
        for rel in self.relationships:
            if rel.relationship_type == RelationshipType.ATTACHED_TO and rel.source_id == eni_id:
                tgt = self.resources.get(rel.target_id)
                if tgt and tgt.resource_type == ResourceType.EC2_INSTANCE:
                    return tgt.resource_id
        for rel in self.relationships:
            if rel.relationship_type != RelationshipType.MEMBER_OF:
                continue
            if rel.source_id != eni_id:
                continue
            tgt = self.resources.get(rel.target_id)
            if tgt and tgt.resource_type in (ResourceType.ECS_SERVICE, ResourceType.LAMBDA_FUNCTION):
                return rel.target_id
        return None

    def _parse_ip_target(self, target: dict) -> tuple[str, Optional[int]]:
        """Parse target dict to extract IP and port.

        Args:
            target: Target dict with 'id' and optional 'port' keys

        Returns:
            Tuple of (ip_address, port) where port can be None
        """
        tid = target.get('id', '')
        port = target.get('port')

        # Handle IP addresses (both IPv4 and IPv6)
        if '.' in str(tid) or ':' in str(tid):
            return str(tid), port

        # Fallback for other formats
        return str(tid), port

    def _sanitize_identifier(self, value: str) -> str:
        """Create a diagram-safe identifier fragment."""
        return (
            str(value)
            .replace(':', '-')
            .replace('/', '-')
            .replace('=', '-')
            .replace(',', '-')
            .replace(' ', '-')
        )

    def _format_lb_title_from_identifier(self, identifier: str) -> str:
        """Extract a human-friendly load balancer name from an ARN or ID."""
        value = str(identifier)
        if "loadbalancer/" not in value:
            return value.split("/")[-1]
        parts = value.split("/")
        if len(parts) >= 4:
            # arn/.../loadbalancer/<type>/<name>/<id>
            return parts[-2]
        return parts[-1]

    def _ensure_cluster_badge(
        self,
        resources: Dict[str, Any],
        cluster_node_id: str,
        lb_id: str,
        tg_id: str,
    ) -> str:
        key = (cluster_node_id, lb_id, tg_id)
        cached = self._cluster_badges.get(key)
        if cached:
            return cached

        cluster_title = resources.get(cluster_node_id, {}).get("Title", cluster_node_id)
        badge_id = (
            f"lb-cluster-badge-{self._sanitize_identifier(cluster_node_id)}-"
            f"{self._sanitize_identifier(lb_id)}-{self._sanitize_identifier(tg_id)}"
        )

        if badge_id not in resources:
            resources[badge_id] = {
                "Type": "AWS::ECS::Cluster",
                "Title": f"Cluster: {cluster_title}",
            }

        self._cluster_badges[key] = badge_id
        return badge_id

    def _extract_security_group_ids(self, properties: Dict[str, Any]) -> List[str]:
        """Normalize security group identifiers from resource properties."""
        if not properties:
            return []
        sg_ids = properties.get('security_group_ids') or []
        if sg_ids:
            return list(sg_ids)

        raw_groups = properties.get('security_groups') or []
        normalized: List[str] = []
        for entry in raw_groups:
            if isinstance(entry, dict):
                sg_id = entry.get('GroupId') or entry.get('group_id') or entry.get('id')
                if sg_id:
                    normalized.append(str(sg_id))
            elif entry:
                normalized.append(str(entry))
        return normalized

    def _get_vpce_service_id(self, service_name: str) -> Optional[str]:
        """Extract the vpce service id (vpce-svc-*) from a full service name."""
        if not service_name:
            return None
        for token in service_name.split('.'):
            if token.lower().startswith('vpce-svc-'):
                return token.upper()
        return None

    def _ensure_vpce_eni_cache(self) -> None:
        if self._vpce_eni_cache_populated:
            return
        cache: Dict[str, Tuple[BaseResource, str]] = {}
        for res in self.resources.values():
            if res.resource_type != ResourceType.VPC_ENDPOINT:
                continue
            owner = res.properties.get('service_owner') or ''
            for eni_id in res.properties.get('network_interface_ids') or []:
                cache[str(eni_id)] = (res, owner)

        # Fallback: map ENIs via description/attachment if VPCE resource omitted network_interface_ids
        vpce_pattern = re.compile(r'vpce-[0-9a-f]+', re.IGNORECASE)
        for res in self.resources.values():
            if res.resource_type != ResourceType.NETWORK_INTERFACE:
                continue
            eni_id = res.resource_id
            if eni_id in cache:
                continue
            props = res.properties or {}
            desc = props.get('description') or ''
            match = vpce_pattern.search(str(desc))
            if not match:
                attachment = props.get('attachment') or {}
                for value in attachment.values():
                    if isinstance(value, str):
                        match = vpce_pattern.search(value)
                        if match:
                            break
            if not match:
                continue
            vpce_id = match.group(0)
            vpce_resource = self.resources.get(vpce_id)
            owner = ''
            if vpce_resource:
                owner = vpce_resource.properties.get('service_owner') or ''
            cache[eni_id] = (vpce_resource, owner)
        self._vpce_eni_owner_cache = cache
        self._vpce_eni_cache_populated = True

    def _get_vpce_for_eni(self, eni_id: str) -> Optional[Tuple[BaseResource, str]]:
        self._ensure_vpce_eni_cache()
        return self._vpce_eni_owner_cache.get(eni_id)

    def _is_aws_managed_vpce(self, vpce_resource: BaseResource, owner: str) -> bool:
        service_name = (vpce_resource.properties or {}).get('service_name', '')
        owner_normalized = (owner or '').strip().lower()
        if owner_normalized in {'amazon', 'aws', 'amazon web services', 'amazon web services, inc.'}:
            return True
        if service_name and service_name.lower().startswith('com.amazonaws.'):
            return True
        return False

    def _vpce_service_icon(self, service_name: str) -> Optional[Tuple[str, str]]:
        """Map a VPC endpoint AWS service name to a DAC service type and friendly title.

        Returns (service_type, title). Example: ("AWS::ECR", "ECR").
        """
        if not service_name:
            return None
        s = service_name.lower()
        tokens = s.split('.')
        if 'ecr' in tokens:
            return ("AWS::ECR", "ECR")
        if 's3' in tokens:
            return ("AWS::S3", "S3")
        if 'kms' in tokens:
            return ("AWS::KMS", "KMS")
        if 'secretsmanager' in tokens:
            return ("AWS::SecretsManager", "Secrets Manager")
        if 'ssm' in tokens or 'ec2messages' in tokens or 'ssmmessages' in tokens:
            # DAC often lacks Systems Manager; use EC2 icon as a neutral fallback
            return ("AWS::EC2", "Systems Manager")
        if 'logs' in tokens:
            return ("AWS::CloudWatch", "CloudWatch Logs")
        if 'monitoring' in tokens:
            return ("AWS::CloudWatch", "CloudWatch")
        if 'events' in tokens or 'eventbridge' in tokens:
            return ("AWS::EC2", "EventBridge")
        if 'elasticloadbalancing' in tokens:
            return ("AWS::ElasticLoadBalancingV2::LoadBalancer", "Elastic Load Balancing")
        # Fallback: show EC2 icon with last token
        return ("AWS::EC2", tokens[-1].upper())
    
    def _get_subnet_logical_group(self, subnet: BaseResource) -> str:
        """Extract logical group name from subnet name."""
        subnet_name = subnet.name or subnet.resource_id
        
        # Extract common prefix patterns
        if 'private' in subnet_name.lower():
            return 'private-subnets'
        elif 'public' in subnet_name.lower():
            return 'public-subnets' 
        elif 'tgw' in subnet_name.lower():
            return 'tgw-subnets'
        elif 'db' in subnet_name.lower() or 'database' in subnet_name.lower():
            return 'database-subnets'
        elif 'web' in subnet_name.lower():
            return 'web-subnets'
        elif 'app' in subnet_name.lower():
            return 'app-subnets'
        else:
            # Fallback: try to extract prefix before AZ designation
            parts = subnet_name.split('-')
            if len(parts) >= 2:
                # Remove AZ suffix if present (like '2a', '2b', '2c')
                import re
                if re.match(r'^[0-9][a-z]$', parts[-1]):
                    return '-'.join(parts[:-1]) + '-subnets'
                else:
                    return '-'.join(parts[:-1]) + '-subnets' if len(parts) > 1 else subnet_name
            return 'misc-subnets'
    
    def _get_subnet_group_rgba_color(self, group_name: str) -> str:
        """Get RGBA color for subnet group based on type."""
        if 'private' in group_name.lower():
            return 'rgba(232,244,253,100)'  # Light blue for private
        elif 'public' in group_name.lower():
            return 'rgba(232,248,232,100)'  # Light green for public
        elif 'tgw' in group_name.lower():
            return 'rgba(255,242,232,100)'  # Light orange for transit gateway
        elif 'database' in group_name.lower() or 'db' in group_name.lower():
            return 'rgba(240,232,255,100)'  # Light purple for database
        elif 'web' in group_name.lower():
            return 'rgba(255,232,232,100)'  # Light red for web
        elif 'app' in group_name.lower():
            return 'rgba(232,255,232,100)'  # Light green for app
        else:
            return 'rgba(245,245,245,100)'  # Light gray for others
    
    def _create_subnet_group_children(self, group_name: str, subnets: List[str], resources: Dict[str, Any]) -> List[str]:
        """Create child elements for a logical subnet group.

        - Group NAT Gateways into a single logical element under the subnet group.
        - Group Transit Gateway attachments into a single logical element under the subnet group.
        - Do not duplicate nodes under individual subnets to avoid cycles.
        """
        additions: List[str] = []

        # Determine VPC id from the first subnet to generate stable group ids
        vpc_id = None
        if subnets:
            first_subnet = self.resources.get(subnets[0])
            if first_subnet:
                vpc_id = first_subnet.properties.get('vpc_id')

        # NAT Gateways group
        nat_ids: List[str] = []
        for resource_id, resource in self.resources.items():
            if (hasattr(resource, 'properties') and
                resource.resource_type == ResourceType.NAT_GATEWAY and
                resource.properties.get('subnet_id') in subnets):
                nat_ids.append(resource_id)

        if nat_ids:
            nat_group_id = f"{vpc_id or 'vpc'}-{group_name}-nat-group"
            nat_children_stack_id = f"{nat_group_id}-children"
            resources[nat_children_stack_id] = {
                "Type": "AWS::Diagram::HorizontalStack",
                "Children": nat_ids
            }
            resources[nat_group_id] = {
                "Type": "AWS::Diagram::VerticalStack",
                "Title": "NAT Gateways",
                "Children": [nat_children_stack_id]
            }
            additions.append(nat_group_id)

        # Transit Gateway attachments group: infer from CONNECTS_TO edges between subnets and TGW
        tgw_attachment_ids: List[str] = []
        # Keep mapping of synthetic id to TGW for potential future linking
        for rel in self.relationships:
            if rel.relationship_type != RelationshipType.CONNECTS_TO:
                continue
            src = self.resources.get(rel.source_id)
            tgt = self.resources.get(rel.target_id)
            if not src or not tgt:
                continue
            # Consider only subnet <-> TGW connections
            if ((src.resource_type == ResourceType.SUBNET and tgt.resource_type == ResourceType.TRANSIT_GATEWAY and rel.source_id in subnets) or
                (tgt.resource_type == ResourceType.SUBNET and src.resource_type == ResourceType.TRANSIT_GATEWAY and rel.target_id in subnets)):
                subnet_id = rel.source_id if src.resource_type == ResourceType.SUBNET else rel.target_id
                tgw_id = rel.target_id if tgt.resource_type == ResourceType.TRANSIT_GATEWAY else rel.source_id
                # Create a stable synthetic attachment node id per (subnet, tgw)
                attach_id = f"{subnet_id}-to-{tgw_id}-tgw-attach"
                if attach_id not in resources:
                    # Try to include the subnet AZ in the title
                    az = None
                    subnet_res = self.resources.get(subnet_id)
                    if subnet_res and subnet_res.location and subnet_res.location.availability_zone:
                        az = subnet_res.location.availability_zone
                    title = f"TGW Attachment" + (f" ({az})" if az else "")
                    resources[attach_id] = {
                        "Type": "AWS::EC2::TransitGateway",
                        "Title": title
                    }
                tgw_attachment_ids.append(attach_id)

        if tgw_attachment_ids:
            tgw_group_id = f"{vpc_id or 'vpc'}-{group_name}-tgw-attach-group"
            tgw_children_stack_id = f"{tgw_group_id}-children"
            resources[tgw_children_stack_id] = {
                "Type": "AWS::Diagram::HorizontalStack",
                "Children": tgw_attachment_ids
            }
            resources[tgw_group_id] = {
                "Type": "AWS::Diagram::VerticalStack",
                "Title": "TGW Attachments",
                "Children": [tgw_children_stack_id]
            }
            additions.append(tgw_group_id)

        return additions
    
    def _add_az_stacks(self, resources: Dict[str, Any]) -> None:
        """Add availability zone stacks with logical subnet grouping."""
        # Group subnets by VPC and logical subnet groups
        vpc_subnet_groups = {}
        for resource_id, resource in self.resources.items():
            if resource.resource_type == ResourceType.SUBNET:
                vpc_id = resource.properties.get('vpc_id')
                if vpc_id:
                    if vpc_id not in vpc_subnet_groups:
                        vpc_subnet_groups[vpc_id] = {}
                    
                    # Extract logical group from subnet name
                    logical_group = self._get_subnet_logical_group(resource)
                    if logical_group not in vpc_subnet_groups[vpc_id]:
                        vpc_subnet_groups[vpc_id][logical_group] = []
                    vpc_subnet_groups[vpc_id][logical_group].append(resource_id)
        
        # Create logical subnet groups for each VPC
        for vpc_id, subnet_groups in vpc_subnet_groups.items():
            if subnet_groups:  # Only create if we have subnet groups
                group_stack_ids = []
                
                # Create logical subnet group representations
                for group_name, subnets in subnet_groups.items():
                    group_stack_id = f"{vpc_id}-{group_name}-group"
                    group_stack_ids.append(group_stack_id)
                    
                    # Create logical subnet group with proper boundary and children
                    group_children = subnets  # Show individual subnets as children
                    
                    # Add any resources that belong in these subnets (NAT gateways, instances)
                    group_children.extend(self._create_subnet_group_children(group_name, subnets, resources))

                    # Within each logical subnet group, display children horizontally
                    children_stack_id = f"{group_stack_id}-children"
                    resources[children_stack_id] = {
                        "Type": "AWS::Diagram::HorizontalStack",
                        "Children": group_children
                    }
                    
                    resources[group_stack_id] = {
                        "Type": "AWS::Diagram::VerticalStack",  # Use VerticalStack with styling for boundaries
                        "Title": group_name.replace('-', ' ').title(),
                        "FillColor": self._get_subnet_group_rgba_color(group_name),
                        "BorderColor": "rgba(0,0,0,255)",  # Solid black border
                        "Direction": "vertical",
                        "Children": [children_stack_id]
                    }
                
                # Order groups: put any 'public' logical group at the top
                def _group_sort_key(stack_id: str) -> tuple:
                    # stack_id format: f"{vpc_id}-{group_name}-group"
                    group_name = stack_id[len(f"{vpc_id}-"): -len("-group")] if stack_id.endswith("-group") else stack_id
                    is_public = 0 if 'public' in group_name.lower() else 1
                    return (is_public, group_name)

                ordered_group_stack_ids = sorted(group_stack_ids, key=_group_sort_key)

                # Create main subnet groups vertical stack with ordered groups (no re-arrangement)
                main_stack_id = f"{vpc_id}-azs"
                resources[main_stack_id] = {
                    "Type": "AWS::Diagram::VerticalStack",
                    "Children": ordered_group_stack_ids
                }
    
    def _find_ec2_in_full_topology(self, ec2_id: str, current_vpc_id: str) -> Optional[Dict[str, Any]]:
        """
        Find an EC2 instance in the full topology and determine if it's cross-account/cross-VPC.

        Returns:
            Dict with 'resource', 'is_cross_account', 'is_cross_vpc', 'location_info' if found, None otherwise
        """
        if not hasattr(self.view, 'source_topology') or not self.view.source_topology:
            return None

        # Get current VPC info for comparison
        current_vpc_resource = self.resources.get(current_vpc_id)
        if not current_vpc_resource:
            return None

        current_account = current_vpc_resource.location.account_id
        current_region = current_vpc_resource.location.region

        # Search all accounts and regions in the full topology
        for account_id, account_data in self.view.source_topology.organization.accounts.items():
            for region_name, region_data in account_data.regions.items():
                for resource_type, resources in region_data.resources.items():
                    if resource_type == 'ec2_instance':
                        for resource_id, resource in resources.items():
                            if resource_id == ec2_id:
                                # Found the EC2 instance
                                is_cross_account = account_id != current_account
                                is_cross_region = region_name != current_region

                                # Check if it's in a different VPC (same account/region)
                                is_cross_vpc = False
                                if not is_cross_account and not is_cross_region:
                                    ec2_vpc = resource.properties.get('vpc_id')
                                    is_cross_vpc = ec2_vpc != current_vpc_id

                                location_info = f"{account_id}:{region_name}"
                                if is_cross_account:
                                    location_info += f" (cross-account)"
                                elif is_cross_region:
                                    location_info += f" (cross-region)"
                                elif is_cross_vpc:
                                    ec2_vpc = resource.properties.get('vpc_id', 'unknown')
                                    location_info += f" (VPC: {ec2_vpc})"

                                return {
                                    'resource': resource,
                                    'is_cross_account': is_cross_account,
                                    'is_cross_region': is_cross_region,
                                    'is_cross_vpc': is_cross_vpc,
                                    'location_info': location_info
                                }

        return None

    def save_to_file(self, filepath: str) -> None:
        """Save the transformed diagram to a YAML file."""
        import yaml
        from pathlib import Path
        
        diagram_data = self.transform()
        
        with open(filepath, 'w') as f:
            yaml.dump(diagram_data, f, default_flow_style=False, indent=2)
        
        logger.info(f"AWS Labs diagram saved to {filepath}")
    
    @classmethod
    def transform_view(cls, view: TopologyView) -> Dict[str, Any]:
        """Convenience method to transform a view and return the diagram data."""
        transformer = cls(view)
        return transformer.transform()
    def _register_render_node(self, resource_id: Optional[str], node_id: Optional[str] = None) -> None:
        """Track which diagram node represents a given resource."""
        if not resource_id:
            return
        self._resource_render_map[resource_id] = node_id or resource_id

    def _create_cluster_projection(
        self,
        resources: Dict[str, Any],
        cluster_node_id: str,
        service_ids: Set[str],
        lb_node_id: str,
        lb_id: str,
        tg_id: str,
        group_key: str,
    ) -> str:
        """Create a synthetic cluster node containing service badges for LB rendering."""
        cluster_title = resources.get(cluster_node_id, {}).get("Title", cluster_node_id)
        proj_id = (
            f"cluster-projection-{self._sanitize_identifier(cluster_node_id)}-"
            f"lb-{self._sanitize_identifier(lb_id)}-tg-{self._sanitize_identifier(tg_id)}"
        )

        children: List[str] = []
        for svc_id in sorted(service_ids):
            svc_node_id = f"ecs-{svc_id}-in-{cluster_node_id}"
            badge_id = (
                f"svc-projection-{self._sanitize_identifier(svc_id)}-"
                f"lb-{self._sanitize_identifier(lb_id)}-tg-{self._sanitize_identifier(tg_id)}"
            )
            badge_title = resources.get(svc_node_id, {}).get("Title")
            if not badge_title:
                svc_res = self.resources.get(svc_id)
                badge_title = (svc_res.name if svc_res else None) or svc_id.split("/")[-1]
            resources[badge_id] = {
                "Type": "AWS::ECS::Service",
                "Title": badge_title,
            }
            children.append(badge_id)

        resources[proj_id] = {
            "Type": "AWS::ECS::Cluster",
            "Title": f"Cluster: {cluster_title}",
            "Children": children or None,
        }
        self._cluster_lb_consumers.add(cluster_node_id)
        return proj_id

    def _attach_cluster_display(
        self,
        resources: Dict[str, Any],
        cluster_node_id: str,
        sg_id: Optional[str],
        service_ids: Set[str],
        lb_node_id: str,
        lb_id: str,
        tg_id: str,
        group_key: str,
        clusters_reassigned: Set[str],
    ) -> str:
        owner = self._cluster_lb_owner.get(cluster_node_id)
        if owner is None or owner == lb_id:
            self._cluster_lb_owner[cluster_node_id] = lb_id
            clusters_reassigned.add(cluster_node_id)
            self._cluster_lb_consumers.add(cluster_node_id)
            display_node_id = (
                f"cluster-display-{self._sanitize_identifier(cluster_node_id)}-"
                f"lb-{self._sanitize_identifier(lb_id)}-tg-{self._sanitize_identifier(tg_id)}"
            )
            resources[display_node_id] = {
                "Type": "AWS::Diagram::VerticalStack",
                "Title": resources.get(cluster_node_id, {}).get("Title"),
                "Children": [cluster_node_id],
            }
            return display_node_id
        return self._create_cluster_projection(
            resources, cluster_node_id, service_ids, lb_node_id, lb_id, tg_id, group_key
        )
    def _collect_direct_targets(
        self, lb_node_id: str, resources: Dict[str, Any], group_key: str
    ) -> List[str]:
        """Build direct sections for targets grouped outside TG nodes."""
        suffix = f"-in-{group_key}"
        direct_targets = [
            target
            for source, target in self._lb_service_links
            if source == lb_node_id and target in resources
            and (
                target.endswith(suffix)
                or self._cluster_parent.get(target) == group_key
                or self._group_parent.get(target) == group_key
            )
        ]
        logger.debug("Collecting direct targets for %s: %s", lb_node_id, direct_targets)
        if not direct_targets:
            return []
        if len(direct_targets) == 1:
            return direct_targets
        direct_row_id = f"{lb_node_id}-direct-targets"
        resources[direct_row_id] = {
            "Type": "AWS::Diagram::HorizontalStack",
            "Children": direct_targets,
        }
        return [direct_row_id]
    def _build_lb_stack(
        self,
        lb_id: str,
        group_key: str,
        lb_node_id: str,
        sections: List[str],
        resources: Dict[str, Any],
    ) -> Optional[str]:
        effective_sections = sections or self._collect_direct_targets(lb_node_id, resources, group_key)
        logger.debug("Building stack for %s: sections=%s", lb_id, effective_sections)
        if not effective_sections:
            return None
        if len(effective_sections) > 1:
            row_id = (
                f"lb-{self._sanitize_identifier(lb_id)}-targets-row-in-{group_key}"
            )
            resources[row_id] = {
                "Type": "AWS::Diagram::HorizontalStack",
                "Children": effective_sections,
            }
            stack_children = [lb_node_id, row_id]
        else:
            stack_children = [lb_node_id] + effective_sections
        if len(stack_children) == 1:
            return None
        stack_id = f"lb-{lb_id}-stack-in-{group_key}"
        resources[stack_id] = {
            "Type": "AWS::Diagram::VerticalStack",
            "Title": self.resources.get(lb_id).name or lb_id,
            "Children": stack_children,
        }
        return stack_id

    def _diagram_node_id(self, resource: BaseResource) -> str:
        """Return the diagram node identifier used for the given resource."""
        if resource.resource_type == ResourceType.ROUTE53_HOSTED_ZONE:
            return f"route53-hz-{self._sanitize_identifier(resource.resource_id)}"
        if resource.resource_type == ResourceType.ROUTE53_RECORD:
            digest = hashlib.sha1(resource.resource_id.encode("utf-8")).hexdigest()[:12]
            return f"route53-record-{digest}"
        return resource.resource_id

    def _find_eni_owner_resource(self, eni_id: str) -> Optional[BaseResource]:
        """Return the resource that owns the given ENI, if known."""
        for rel in self.relationships:
            if rel.source_id != eni_id:
                continue
            if rel.relationship_type == RelationshipType.ATTACHED_TO:
                owner = self.resources.get(rel.target_id)
                if owner:
                    return owner
        for rel in self.relationships:
            if rel.source_id != eni_id:
                continue
            if rel.relationship_type == RelationshipType.MEMBER_OF:
                owner = self.resources.get(rel.target_id)
                if owner:
                    return owner
        return None

    def _infer_eni_owner_label(self, resource: BaseResource) -> Optional[str]:
        """Best-effort textual description of the ENI owner."""
        props = resource.properties or {}
        description = str(props.get("description") or resource.name or "").strip()
        attachment = props.get("attachment") or {}
        if not description and not attachment:
            return None
        lower_desc = description.lower()
        instance_owner = str(attachment.get("InstanceOwnerId") or "").lower()

        if instance_owner in {"amazon-elb", "amazon-elbv2"} or lower_desc.startswith("elb "):
            lb_name = self._extract_lb_name_from_description(description)
            return f"Load Balancer {lb_name}" if lb_name else description or None

        if "transit gateway" in lower_desc and "tgw-attach" in lower_desc:
            return description or "Transit Gateway Attachment"

        if "arn:aws:ecs" in lower_desc and "attachment/" in lower_desc:
            attachment_id = description.split("attachment/", 1)[-1]
            return f"ECS attachment {attachment_id}"

        if instance_owner.startswith("amazon-lambda"):
            return "Lambda managed ENI"

        return description or None

    @staticmethod
    def _extract_lb_name_from_description(description: str) -> Optional[str]:
        if not description:
            return None
        if " " in description:
            desc = description.split(" ", 1)[1]
        else:
            desc = description
        parts = desc.split("/")
        if len(parts) >= 2:
            return parts[1]
        return desc

    def _should_include_global_resource(self, node_id: str) -> bool:
        """Return True if a global resource should be rendered."""
        if node_id in self._global_service_ids:
            return node_id in self._linked_global_service_ids
        return True

    def _build_global_service_links(self) -> None:
        """Link global services (CloudFront/Global Accelerator) to their backends."""
        self._linked_global_service_ids = set()
        if not self._global_service_ids:
            return
        for service_id in sorted(self._global_service_ids):
            service = self.resources.get(service_id)
            if not service:
                continue
            source_node = self._resource_render_map.get(service_id)
            if not source_node:
                continue
            if service.resource_type == ResourceType.GLOBAL_ACCELERATOR:
                backend_ids = self._find_global_accelerator_backends(service_id)
            elif service.resource_type == ResourceType.CLOUDFRONT_DISTRIBUTION:
                backend_ids = self._find_cloudfront_backends(service)
            else:
                backend_ids = []
            for backend_id in backend_ids:
                if not self._is_resource_in_vpc(backend_id):
                    continue
                target_node = self._resource_render_map.get(backend_id)
                if not target_node or target_node == source_node:
                    continue
                self._global_service_links.add((source_node, target_node))
                self._linked_global_service_ids.add(service_id)

    def _find_global_accelerator_backends(self, accelerator_id: str) -> List[str]:
        """Return resource IDs targeted by the given Global Accelerator."""
        backends: List[str] = []
        seen: Set[str] = set()
        for relationship in self.relationships:
            if (
                relationship.relationship_type != RelationshipType.CONNECTS_TO
                or relationship.source_id != accelerator_id
            ):
                continue
            target_id = relationship.target_id
            if target_id in seen:
                continue
            if target_id in self.resources:
                seen.add(target_id)
                backends.append(target_id)
        return backends

    def _find_cloudfront_backends(self, distribution: BaseResource) -> List[str]:
        """Infer CloudFront origins that point to in-VPC load balancers."""
        origins = (distribution.properties or {}).get("origins") or []
        if not origins:
            return []
        lb_resources = [
            res
            for res in self.resources.values()
            if res.resource_type == ResourceType.LOAD_BALANCER
        ]
        matches: List[str] = []
        seen: Set[str] = set()
        for origin in origins:
            origin_type = str(origin.get("type") or origin.get("Type") or "").lower()
            if origin_type and origin_type not in {"load_balancer", "custom"}:
                continue
            domain = origin.get("domain_name") or origin.get("DomainName")
            if not domain:
                continue
            backend_id = self._match_origin_to_load_balancer(str(domain), lb_resources)
            if backend_id and backend_id not in seen:
                seen.add(backend_id)
                matches.append(backend_id)
        return matches

    def _match_origin_to_load_balancer(
        self, origin_domain: str, lb_resources: List[BaseResource]
    ) -> Optional[str]:
        normalized_domain = origin_domain.lower()
        for lb in lb_resources:
            candidates: List[str] = []
            if lb.name:
                candidates.append(str(lb.name).lower())
            dns_name = (lb.properties or {}).get("dns_name") or (lb.properties or {}).get("DNSName")
            if dns_name:
                candidates.append(str(dns_name).lower())
            arn_fragment = self._extract_lb_name_from_arn(lb.resource_id)
            if arn_fragment:
                candidates.append(arn_fragment.lower())
            candidates.append(lb.resource_id.lower())
            for candidate in candidates:
                if candidate and (candidate in normalized_domain or normalized_domain in candidate):
                    return lb.resource_id
        return None

    @staticmethod
    def _extract_lb_name_from_arn(lb_arn: str) -> Optional[str]:
        if not lb_arn or ":loadbalancer/" not in lb_arn:
            return None
        try:
            fragment = lb_arn.split(":loadbalancer/", 1)[1]
            parts = fragment.split("/")
            if len(parts) >= 2:
                return parts[1]
        except Exception:
            return None
        return None

    def _is_resource_in_vpc(self, resource_id: str) -> bool:
        resource = self.resources.get(resource_id)
        if not resource:
            return False
        props = getattr(resource, "properties", {}) or {}
        if props.get("vpc_id"):
            return True
        region = getattr(getattr(resource, "location", None), "region", "")
        return bool(region and region.lower() not in {"aws-global", "global"})

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

    def _supports_eni_dedup_for(self, resource: Optional[BaseResource]) -> bool:
        if not resource:
            return False
        return resource.resource_type in self.ENI_SUPPORTED_OWNER_TYPES

    def _is_global_service_eni(self, resource: BaseResource) -> bool:
        if resource.resource_type != ResourceType.NETWORK_INTERFACE:
            return False
        props = resource.properties or {}
        tags = {str(k).lower(): v for k, v in (resource.metadata.tags or {}).items()}
        service_name = str(tags.get("awsservicename") or "").lower()
        description = str(props.get("description") or "").lower()
        interface_type = str(props.get("interface_type") or "").lower()
        haystack = " ".join([service_name, description, interface_type])
        for keyword in ("globalaccelerator", "global accelerator", "cloudfront"):
            if keyword in haystack:
                return True
        return False
