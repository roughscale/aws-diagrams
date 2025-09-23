"""
AWS Labs diagram-as-code transformer.

This module transforms filtered topology views into the YAML format
used by the AWS Labs diagram-as-code tool for generating architectural diagrams.

AWS Labs diagram-as-code format: https://github.com/awslabs/diagram-as-code
"""

from typing import Dict, List, Any, Optional, Set, Tuple
import math
from dataclasses import dataclass
import logging

try:
    from ..views.view_engine import TopologyView
    from ..topology.schema import ResourceType, BaseResource, Relationship, RelationshipType
    from ..utils.logger import get_logger
except ImportError:
    from views.view_engine import TopologyView
    from topology.schema import ResourceType, BaseResource, Relationship, RelationshipType
    from utils.logger import get_logger

logger = get_logger("transformer")


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


class AWSLabsTransformer:
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
        ResourceType.NETWORK_INTERFACE: "AWS::EC2::NetworkInterface"
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
        ResourceType.LAMBDA_FUNCTION: "LambdaFunction"
    }
    
    def __init__(self, view: TopologyView):
        self.view = view
        self.nodes: Dict[str, DiagramNode] = {}
        self.connections: List[DiagramConnection] = []
        self.groups: Dict[str, List[str]] = {}
        self.primary_vpc_id: Optional[str] = self._extract_primary_vpc_id()
        # Enable logical subnet grouping (Phase 1)
        self.logical_subnets_enabled: bool = True
        # Single-parent guards to avoid DAGs/cycles in DAC
        self._group_parent: Dict[str, str] = {}       # resource_id -> logical subnet node id
        self._tg_parent: Dict[str, str] = {}          # tg_id -> load_balancer_id
        # Explicit LB -> TG link intents for service-centric rendering
        self._lb_tg_links: Set[Tuple[str, str]] = set()
        self._tg_nodes_by_group: Dict[str, List[str]] = {}
        self._tg_meta: Dict[str, Tuple[str, str]] = {}  # tg_node_id -> (lb_title, tg_title)
        
    def transform(self) -> Dict[str, Any]:
        """Transform the topology view into AWS Labs diagram-as-code format."""
        logger.info(f"Transforming view '{self.view.name}' to AWS Labs format")
        
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
        for resource_id, resource in self.view.filtered_resources.items():
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
        
        for resource_id, resource in self.view.filtered_resources.items():
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
        for resource_id, resource in self.view.filtered_resources.items():
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
        for relationship in self.view.filtered_relationships:
            connection = self._create_connection_from_relationship(relationship)
            if connection:
                self.connections.append(connection)
    
    def _create_connection_from_relationship(self, relationship: Relationship) -> Optional[DiagramConnection]:
        """Create a diagram connection from a topology relationship."""
        # Only create connections if both resources are in the view
        if (relationship.source_id not in self.view.filtered_resources or 
            relationship.target_id not in self.view.filtered_resources):
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
    
    def _generate_connection_label(self, relationship: Relationship) -> str:
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
        for resource_id, resource in self.view.filtered_resources.items():
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

        resources["AWSCloud"]["Children"].append("VpcRow")
        
        # Add VPC resources
        self._add_vpc_resources(resources)
        
        # Phase 1: Use logical subnets rather than per-AZ subnet stacks
        # self._add_az_stacks(resources)
        
        # Add other AWS resources (but skip individual subnets as they're now grouped)
        self._add_aws_resources(resources)
        
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
        for resource_id, resource in self.view.filtered_resources.items():
            if resource.resource_type == ResourceType.VPC:
                vpcs.append(resource_id)
        return vpcs
    
    def _add_vpc_resources(self, resources: Dict[str, Any]) -> None:
        """Add VPC resources to the diagram."""
        for resource_id, resource in self.view.filtered_resources.items():
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

                if border_children:
                    resources[resource_id]["BorderChildren"] = border_children

                # If this is the primary VPC, place any peered VPC containers underneath
                if self.primary_vpc_id and resource_id == self.primary_vpc_id:
                    peer_vpcs = [peer for _, peer in self._find_vpc_peerings(resource_id)]
                    # Only include peers that exist as resources; synthetic peers should be present from the view
                    peer_vpcs = [p for p in peer_vpcs if p in self.view.filtered_resources]
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
        for rid, res in self.view.filtered_resources.items():
            if res.resource_type == ResourceType.SUBNET and res.properties.get('vpc_id') == vpc_id:
                group = self._get_subnet_logical_group(res)
                group_to_subnets.setdefault(group, []).append(rid)

        additions: List[str] = []
        # Precompute Lambda targets from all TGs to avoid multi-parenting
        tg_lambda_target_ids: set = set()
        for rid_tg, res_tg in self.view.filtered_resources.items():
            if res_tg.resource_type == ResourceType.TARGET_GROUP and (res_tg.properties or {}).get('target_type') == 'lambda':
                for t in res_tg.properties.get('targets') or []:
                    if t.get('id'):
                        tg_lambda_target_ids.add(str(t['id']))

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

            for rid, res in self.view.filtered_resources.items():
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
                        for crid, cres in self.view.filtered_resources.items():
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

            for svc_id, svc in self.view.filtered_resources.items():
                if svc.resource_type == ResourceType.ECS_SERVICE:
                    svc_subnets = set(svc.properties.get('subnet_ids') or [])
                    if svc_subnets & subnet_set:  # Service is in this subnet group
                        cluster_arn = svc.properties.get('clusterArn')
                        cluster_data = ecs_clusters_in_subnet.get(cluster_arn) if cluster_arn else None

                        if cluster_data:
                            # Create cluster hierarchy if not already created
                            if cluster_arn not in cluster_hierarchies:
                                cluster_resource = cluster_data['cluster_resource']
                                cluster_node_id = f"cluster-{cluster_resource.name}-in-{group_key}"

                                resources[cluster_node_id] = {
                                    "Type": "AWS::ECS::Cluster",
                                    "Title": cluster_resource.name or cluster_resource.resource_id.split('/')[-1],
                                    "Children": []
                                }

                                cluster_hierarchies[cluster_arn] = {
                                    'node_id': cluster_node_id,
                                    'services': []
                                }
                                logger.debug(f"Created foundational ECS cluster {cluster_node_id}")

                            # Add service to cluster with SG wrapping
                            cluster_info = cluster_hierarchies[cluster_arn]
                            cluster_node_id = cluster_info['node_id']

                            service_node_id = f"ecs-{svc_id}-in-{cluster_node_id}"
                            resources[service_node_id] = {"Type": "AWS::ECS::Service", "Title": svc.name or svc_id}

                            # Wrap in security group containers
                            sgs = svc.properties.get('security_group_ids', [])
                            current_container = service_node_id

                            if sgs:
                                for sg_id in reversed(sgs):
                                    sg_container_id = f"sg-{sg_id}-container-ecs-{svc_id}-in-{cluster_node_id}"
                                    sg_res = self.view.filtered_resources.get(sg_id)
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

                            # Add service to cluster
                            resources[cluster_node_id]["Children"].append(current_container)
                            cluster_info['services'].append(svc_id)
                            self._group_parent[svc_id] = f"cluster-owned-{cluster_node_id}"
                            logger.debug(f"Added ECS service {svc.name} to foundational cluster {cluster_node_id}")

            # Now handle other services that don't belong to ECS clusters
            for rid, res in self.view.filtered_resources.items():
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
            lb_set = set(lb_children)
            # map LB -> TGs (by relationship LB CONTAINS TG)
            lb_to_tgs: Dict[str, List[str]] = {lb: [] for lb in lb_children}
            for rel in self.view.filtered_relationships:
                if rel.relationship_type != RelationshipType.CONTAINS:
                    continue
                if rel.source_id in lb_set:
                    tgt = self.view.filtered_resources.get(rel.target_id)
                    if tgt and tgt.resource_type == ResourceType.TARGET_GROUP:
                        if rel.target_id not in self._tg_parent:
                            self._tg_parent[rel.target_id] = rel.source_id
                            lb_to_tgs[rel.source_id].append(rel.target_id)

            group_key = group_node_id

            # Prepare per-group TG registry
            self._tg_nodes_by_group.setdefault(group_node_id, [])
            for lb_id in lb_children:
                lb_res = self.view.filtered_resources.get(lb_id)
                lb_title = (lb_res.name if lb_res else None) or lb_id
                lb_icon_id = f"lb-icon-{lb_id}-in-{group_key}"
                if lb_icon_id not in resources:
                    resources[lb_icon_id] = {
                        "Type": "AWS::ElasticLoadBalancingV2::LoadBalancer",
                        "Title": lb_title,
                    }
                same_group_tg_nodes: List[str] = []
                for tg_id in lb_to_tgs.get(lb_id, []):
                    tg = self.view.filtered_resources.get(tg_id)
                    if not tg:
                        continue
                    svc_grand_children: List[str] = []
                    target_subnets: List[str] = []
                    # ECS backends - check if any targeted services are in pre-created clusters
                    targeted_clusters_for_tg = set()
                    for svc_id, svc in self.view.filtered_resources.items():
                        if svc.resource_type == ResourceType.ECS_SERVICE:
                            tgs = set(svc.properties.get('target_group_arns') or [])
                            if tg_id in tgs:
                                cluster_arn = svc.properties.get('clusterArn')
                                # Find if this service's cluster was pre-created
                                for c_arn, c_info in cluster_hierarchies.items():
                                    if c_arn == cluster_arn:
                                        cluster_node_id = c_info['node_id']
                                        targeted_clusters_for_tg.add(cluster_node_id)
                                        logger.debug(f"Found targeted ECS service {svc.name} in cluster {cluster_node_id} for TG {tg_id}")
                                        break

                                # Always collect subnets for placement
                                for s in (svc.properties or {}).get('subnet_ids') or []:
                                    target_subnets.append(s)

                    # Add the targeted clusters to this TG's children
                    for cluster_node_id in targeted_clusters_for_tg:
                        if cluster_node_id not in svc_grand_children:
                            svc_grand_children.append(cluster_node_id)
                            logger.debug(f"Added foundational ECS cluster {cluster_node_id} to TG {tg_id}")
                    # Lambda/EC2/IP backends
                    for t in tg.properties.get('targets') or []:
                        tid = t.get('id')
                        if not tid:
                            continue
                        if str(tid).startswith('arn:aws:lambda:'):
                            child_id = f"lambda-{tid}-in-{group_key}"
                            lf = self.view.filtered_resources.get(tid)
                            # Check if this Lambda is already placed elsewhere to prevent cycles
                            if child_id not in resources and tid not in self._group_parent:
                                resources[child_id] = {"Type": "AWS::Lambda::Function", "Title": (lf.name if lf else None) or tid}
                                self._group_parent[tid] = f"tg-owned-{tg_id}"
                                svc_grand_children.append(child_id)
                            if lf:
                                for s in (lf.properties or {}).get('subnet_ids') or []:
                                    target_subnets.append(s)
                        elif str(tid).startswith('i-'):
                            child_id = f"ec2-{tid}-in-{group_key}"
                            # Check if this EC2 instance is already placed elsewhere to prevent cycles
                            if child_id not in resources and tid not in self._group_parent:
                                resources[child_id] = {"Type": "AWS::EC2::Instance", "Title": tid}
                                self._group_parent[tid] = f"tg-owned-{tg_id}"
                                svc_grand_children.append(child_id)
                            inst = self.view.filtered_resources.get(tid)
                            if inst:
                                s = (inst.properties or {}).get('subnet_id')
                                if s:
                                    target_subnets.append(s)
                        elif '.' in str(tid):
                            ip = str(tid)
                            port = t.get('port')
                            # Deduplicate IP if it maps to a known service already included
                            mapped_svc = self._find_service_id_by_ip(ip)
                            if mapped_svc:
                                ecs_child = f"ecs-{mapped_svc}-in-{group_key}"
                                lam_child = f"lambda-{mapped_svc}-in-{group_key}"
                                if ecs_child in svc_grand_children or lam_child in svc_grand_children:
                                    # Skip adding IP node
                                    pass
                                else:
                                    # Fallback: still show IP if mapped service node wasn't added
                                    ip_child = f"ip-{ip}{('-'+str(port)) if port else ''}-in-{group_key}"
                                    if ip_child not in resources:
                                        title = f"IP {ip}{(':'+str(port)) if port else ''}"
                                        resources[ip_child] = {"Type": "AWS::EC2::Instance", "Title": title}
                                    svc_grand_children.append(ip_child)
                            else:
                                ip_child = f"ip-{ip}{('-'+str(port)) if port else ''}-in-{group_key}"
                                if ip_child not in resources:
                                    title = f"IP {ip}{(':'+str(port)) if port else ''}"
                                    resources[ip_child] = {"Type": "AWS::EC2::Instance", "Title": title}
                                svc_grand_children.append(ip_child)
                            # map IP to ENI to get subnet
                            for rid, res in self.view.filtered_resources.items():
                                if getattr(res, 'resource_type', None) == ResourceType.NETWORK_INTERFACE and (res.properties or {}).get('private_ip') == ip:
                                    s = (res.properties or {}).get('subnet_id')
                                    if s:
                                        target_subnets.append(s)
                    # Decide placement group for TG by majority of target subnets
                    place_group_node = group_node_id
                    if target_subnets:
                        counts: Dict[str, int] = {}
                        for s in target_subnets:
                            for gname, subs in group_to_subnets.items():
                                if s in subs:
                                    gnode = f"{vpc_id}-{gname}-logical-subnet"
                                    counts[gnode] = counts.get(gnode, 0) + 1
                                    break
                        if counts:
                            place_group_node = max(counts, key=counts.get)
                    # Build TG stack vertical with row; register under placement group
                    tg_node_id = f"tg-{tg_id}-in-{place_group_node}"
                    targets_row_id = f"{tg_node_id}-targets-row"
                    resources[targets_row_id] = {"Type": "AWS::Diagram::HorizontalStack", "Children": svc_grand_children or []}
                    tg_title = f"TG: {(tg.name or 'Target Group')}"
                    resources[tg_node_id] = {"Type": "AWS::Diagram::VerticalStack", "Title": tg_title, "Children": [targets_row_id]}
                    if place_group_node == group_node_id:
                        # Same logical subnet: show TG stack under LB stack (vertical)
                        same_group_tg_nodes.append(tg_node_id)
                    else:
                        # Cross-subnet: place TG in its target group and link LB->TG
                        self._tg_nodes_by_group.setdefault(place_group_node, []).append(tg_node_id)
                        self._lb_tg_links.add((lb_icon_id, tg_node_id))
                    # Record meta for ordering in target groups
                    self._tg_meta[tg_node_id] = (lb_title, tg.name or tg_id)
                # Build LB stack
                lb_stack_id = f"lb-{lb_id}-box-in-{group_key}"
                lb_children_nodes = [lb_icon_id]
                if same_group_tg_nodes:
                    tg_row_id = f"{lb_stack_id}-tg-row"
                    resources[tg_row_id] = {"Type": "AWS::Diagram::HorizontalStack", "Children": same_group_tg_nodes}
                    lb_children_nodes.append(tg_row_id)
                resources[lb_stack_id] = {"Type": "AWS::Diagram::VerticalStack", "Title": lb_title, "Children": lb_children_nodes}
                lb_stack_ids.append(lb_stack_id)

            # Phase 2+: Add rows for NAT Gateways, TGW attachments, and orphan ENIs
            extra_row_ids: List[str] = []

            # NAT Gateways row
            nat_ids: List[str] = []
            for rid2, res2 in self.view.filtered_resources.items():
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
            for rel in self.view.filtered_relationships:
                if rel.relationship_type != RelationshipType.CONNECTS_TO:
                    continue
                src = self.view.filtered_resources.get(rel.source_id)
                tgt = self.view.filtered_resources.get(rel.target_id)
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
                        sub_res = self.view.filtered_resources.get(subnet_id)
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
            for rid2, res2 in self.view.filtered_resources.items():
                if res2.resource_type == ResourceType.VPC_ENDPOINT:
                    ep_subnets = set(res2.properties.get('subnet_ids') or [])
                    if not (ep_subnets & subnet_set):
                        continue
                    service_name = self._get_vpc_endpoint_service(res2)
                    owner = (res2.properties.get('service_owner') or '').lower()
                    # If AWS-owned, map to service icon; else use VPCE node with owner note
                    node_id = f"{rid2}-in-{group_node_id}"
                    if owner == 'amazon':
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
            for rid2, res2 in self.view.filtered_resources.items():
                if res2.resource_type == ResourceType.VPC_ENDPOINT:
                    for eni_id in res2.properties.get('network_interface_ids', []) or []:
                        vpce_eni_ids.add(eni_id)
            # Collect ENIs that are MEMBER_OF ECS services
            member_eni_ids: set = set()
            for rel in self.view.filtered_relationships:
                if rel.relationship_type == RelationshipType.MEMBER_OF:
                    src = self.view.filtered_resources.get(rel.source_id)
                    tgt = self.view.filtered_resources.get(rel.target_id)
                    if src and src.resource_type == ResourceType.NETWORK_INTERFACE and tgt and tgt.resource_type == ResourceType.ECS_SERVICE:
                        member_eni_ids.add(src.resource_id)
            # Collect LB SGs in this group
            lb_sg_ids: set = set()
            for lb_id in lb_children:
                lb_res = self.view.filtered_resources.get(lb_id)
                if not lb_res:
                    continue
                for sg in lb_res.properties.get('security_group_ids', []) or []:
                    lb_sg_ids.add(sg)

            # Collect NAT-related ENI IDs in this group's subnets (e.g., from NatGatewayAddresses)
            nat_eni_ids: set = set()
            for rid2, res2 in self.view.filtered_resources.items():
                if res2.resource_type == ResourceType.NAT_GATEWAY and res2.properties.get('subnet_id') in subnet_set:
                    for addr in res2.properties.get('nat_gateway_addresses', []) or []:
                        eni_id = addr.get('NetworkInterfaceId') or addr.get('NetworkInterface')
                        if eni_id:
                            nat_eni_ids.add(eni_id)

            # Collect ENI IDs that belong to aggregated services (RDS, ElastiCache, etc.)
            service_eni_ids: set = set()
            for rid2, res2 in self.view.filtered_resources.items():
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
                    for rel in self.view.filtered_relationships:
                        if (rel.relationship_type == RelationshipType.MEMBER_OF and
                            rel.target_id == rid2):
                            eni = self.view.filtered_resources.get(rel.source_id)
                            if eni and eni.resource_type == ResourceType.NETWORK_INTERFACE:
                                service_eni_ids.add(eni.resource_id)

            eni_ids: List[str] = []
            for rid2, res2 in self.view.filtered_resources.items():
                if res2.resource_type == ResourceType.NETWORK_INTERFACE and res2.properties.get('subnet_id') in subnet_set:
                    desc = (res2.properties.get('description') or '').lower()
                    sgs = set(res2.properties.get('security_group_ids') or [])
                    iface_type = (res2.properties.get('interface_type') or '').lower()
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
            # Combine VPCE and ENIs into a single auxiliary row (keeps service grid clean)
            aux_nodes = vpce_nodes + eni_ids
            if aux_nodes:
                aux_row_id = f"{group_node_id}-aux-row"
                resources[aux_row_id] = {"Type": "AWS::Diagram::HorizontalStack", "Children": aux_nodes}
                extra_row_ids.append(aux_row_id)

            # Instead of BorderChildren overlays, create security group parent containers
            # First, wrap load balancer stacks in their security group containers
            wrapped_lb_stacks: List[str] = []
            for lb_stack_id in lb_stack_ids:
                # Find the corresponding load balancer ID
                lb_id = None
                for lb_id_candidate in lb_children:
                    if f"lb-{lb_id_candidate}-box-in-{group_key}" == lb_stack_id:
                        lb_id = lb_id_candidate
                        break

                if lb_id:
                    lb = self.view.filtered_resources.get(lb_id)
                    if lb and lb.properties.get('security_group_ids'):
                        # Create security group container for this load balancer
                        sgs = lb.properties.get('security_group_ids', [])
                        current_container = lb_stack_id

                        # Wrap in security group containers (innermost to outermost)
                        for sg_id in reversed(sgs):  # Reverse to create proper nesting
                            sg_container_id = f"sg-{sg_id}-container-{lb_id}-in-{group_node_id}"
                            sg_res = self.view.filtered_resources.get(sg_id)
                            sg_name = None
                            if sg_res and getattr(sg_res, 'resource_type', None) == ResourceType.SECURITY_GROUP:
                                sg_name = sg_res.name or sg_res.properties.get('group_name')

                            resources[sg_container_id] = {
                                "Type": "AWS::EC2::SecurityGroup",
                                "Title": f"SG: {sg_name or sg_id}",
                                "Children": [current_container],
                                "FillColor": "rgba(255,244,230,25)",  # Light orange background
                                "BorderColor": "rgba(255,140,0,200)"  # Orange border
                            }
                            current_container = sg_container_id

                        wrapped_lb_stacks.append(current_container)
                    else:
                        # No security groups, use original stack
                        wrapped_lb_stacks.append(lb_stack_id)
                else:
                    # Fallback if we can't find the LB ID
                    wrapped_lb_stacks.append(lb_stack_id)

            # Security groups for standalone services (ECS, Lambda, EC2) are handled
            # when creating their respective nodes - no additional overlays needed here

            # Build ordered content for this group
            rows: List[str] = []
            # 1) Cross-subnet TG stacks come first (left-justified row)
            tg_nodes_here = self._tg_nodes_by_group.get(group_node_id, [])
            if tg_nodes_here:
                tg_sorted = sorted(tg_nodes_here, key=lambda nid: self._tg_meta.get(nid, ("", nid)))
                tg_row_id = f"{group_node_id}-tg-cross-row"
                resources[tg_row_id] = {"Type": "AWS::Diagram::HorizontalStack", "Children": tg_sorted}
                rows.append(tg_row_id)
            # 2) Then LB stacks (wrapped in security groups) and standalone services arranged in grid
            service_nodes = wrapped_lb_stacks[:]

            # Add standalone ECS clusters (those not integrated into LB vertical stacks)
            for cluster_arn, cluster_info in cluster_hierarchies.items():
                cluster_node_id = cluster_info['node_id']
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

            # Wrap standalone services in their security groups too
            for rid in svc_children:
                resource = self.view.filtered_resources.get(rid)
                if resource and hasattr(resource, 'properties') and resource.properties.get('security_group_ids'):
                    # Wrap this service in security group containers
                    sgs = resource.properties.get('security_group_ids', [])
                    current_container = rid

                    # Wrap in security group containers (innermost to outermost)
                    for sg_id in reversed(sgs):  # Reverse to create proper nesting
                        sg_container_id = f"sg-{sg_id}-container-{rid}-in-{group_node_id}"
                        sg_res = self.view.filtered_resources.get(sg_id)
                        sg_name = None
                        if sg_res and getattr(sg_res, 'resource_type', None) == ResourceType.SECURITY_GROUP:
                            sg_name = sg_res.name or sg_res.properties.get('group_name')

                        resources[sg_container_id] = {
                            "Type": "AWS::EC2::SecurityGroup",
                            "Title": f"SG: {sg_name or sg_id}",
                            "Children": [current_container],
                            "FillColor": "rgba(255,244,230,25)",  # Light orange background
                            "BorderColor": "rgba(255,140,0,200)"  # Orange border
                        }
                        current_container = sg_container_id

                    service_nodes.append(current_container)
                else:
                    # No security groups, use original service
                    service_nodes.append(rid)
            grid_rows = self._grid_stack(resources, f"{group_node_id}-grid", service_nodes)
            rows.extend(grid_rows or service_nodes)
            # 3) Then aux rows (VPCE/ENIs/NAT/TGW)
            rows.extend(extra_row_ids)
            # Append CIDR ranges of member subnets to the logical subnet title for clarity
            cidrs: List[str] = []
            for sid in subnet_ids:
                sres = self.view.filtered_resources.get(sid)
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

        return additions
    
    def _add_aws_resources(self, resources: Dict[str, Any]) -> None:
        """Add AWS resources to the diagram."""
        for resource_id, resource in self.view.filtered_resources.items():
            if resource_id in resources:  # Skip if already added
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
                # Show ENI private IP beneath the title
                title = resource.name or resource_id
                ip = (resource.properties or {}).get('private_ip')
                if ip:
                    resource_def["Title"] = f"{title}\n{ip}"
                    
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
            
            resources[resource_id] = resource_def
    
    def _create_links(self, resources: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Create links for the diagram.

        Service-centric gating: only draw synthesized LB -> backend target links
        to preserve the layout and avoid SG/membership link noise.
        """
        links: List[Dict[str, Any]] = []

        # Synthesize LB -> TG links for service-centric view only (cross-group allowed)
        seen = set()
        for lb_id, tg_id in sorted(self._lb_tg_links):
            if lb_id not in resources or tg_id not in resources:
                continue
            key = (lb_id, tg_id)
            if key in seen:
                continue
            seen.add(key)
            link = {
                "Source": lb_id,
                "Target": tg_id,
                "Type": "orthogonal",
                "SourcePosition": "S",
                "TargetPosition": "N",
            }
            links.append(link)
        return links
    
    def _find_internet_gateway(self, vpc_id: str) -> Optional[str]:
        """Find the Internet Gateway attached to a VPC."""
        for relationship in self.view.filtered_relationships:
            if (relationship.relationship_type == RelationshipType.ATTACHED_TO and
                relationship.target_id == vpc_id):
                source_resource = self.view.filtered_resources.get(relationship.source_id)
                if source_resource and source_resource.resource_type == ResourceType.INTERNET_GATEWAY:
                    return relationship.source_id
        return None

    def _find_transit_gateways_for_vpc(self, vpc_id: str) -> List[str]:
        """Find Transit Gateways connected to a VPC."""
        tgws: List[str] = []
        for relationship in self.view.filtered_relationships:
            if relationship.relationship_type != RelationshipType.CONNECTS_TO:
                continue
            src = self.view.filtered_resources.get(relationship.source_id)
            tgt = self.view.filtered_resources.get(relationship.target_id)
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
        for rid, res in self.view.filtered_resources.items():
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
        for resource_id, resource in self.view.filtered_resources.items():
            if (resource.resource_type == ResourceType.VPC_ENDPOINT and
                resource.properties.get('vpc_id') == vpc_id):
                endpoints.append(resource_id)
        return endpoints
    
    def _create_az_stacks(self, vpc_id: str) -> List[str]:
        """Create availability zone stacks for subnets."""
        # Group subnets by availability zone
        az_groups = {}
        for resource_id, resource in self.view.filtered_resources.items():
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
        for rel in self.view.filtered_relationships:
            if rel.relationship_type != RelationshipType.MEMBER_OF:
                continue
            if rel.target_id != service_id:
                continue
            eni = self.view.filtered_resources.get(rel.source_id)
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
        for rid, res in self.view.filtered_resources.items():
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
        for rid, res in self.view.filtered_resources.items():
            if getattr(res, 'resource_type', None) == ResourceType.NETWORK_INTERFACE and (res.properties or {}).get('private_ip') == ip:
                eni_id = rid
                break
        if not eni_id:
            return None
        for rel in self.view.filtered_relationships:
            if rel.relationship_type != RelationshipType.MEMBER_OF:
                continue
            if rel.source_id != eni_id:
                continue
            tgt = self.view.filtered_resources.get(rel.target_id)
            if tgt and tgt.resource_type in (ResourceType.ECS_SERVICE, ResourceType.LAMBDA_FUNCTION):
                return rel.target_id
        return None

    def _get_vpce_service_id(self, service_name: str) -> Optional[str]:
        """Extract the vpce service id (vpce-svc-*) from a full service name."""
        if not service_name:
            return None
        for token in service_name.split('.'):
            if token.lower().startswith('vpce-svc-'):
                return token.upper()
        return None

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
            return ("AWS::EventBridge", "EventBridge")
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
            first_subnet = self.view.filtered_resources.get(subnets[0])
            if first_subnet:
                vpc_id = first_subnet.properties.get('vpc_id')

        # NAT Gateways group
        nat_ids: List[str] = []
        for resource_id, resource in self.view.filtered_resources.items():
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
        for rel in self.view.filtered_relationships:
            if rel.relationship_type != RelationshipType.CONNECTS_TO:
                continue
            src = self.view.filtered_resources.get(rel.source_id)
            tgt = self.view.filtered_resources.get(rel.target_id)
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
                    subnet_res = self.view.filtered_resources.get(subnet_id)
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
        for resource_id, resource in self.view.filtered_resources.items():
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
