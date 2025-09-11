"""
AWS Labs diagram-as-code transformer.

This module transforms filtered topology views into the YAML format
used by the AWS Labs diagram-as-code tool for generating architectural diagrams.

AWS Labs diagram-as-code format: https://github.com/awslabs/diagram-as-code
"""

from typing import Dict, List, Any, Optional, Set, Tuple
from dataclasses import dataclass
import logging

try:
    from ..views.view_engine import TopologyView
    from ..topology.schema import ResourceType, BaseResource, Relationship, RelationshipType
except ImportError:
    from views.view_engine import TopologyView
    from topology.schema import ResourceType, BaseResource, Relationship, RelationshipType

logger = logging.getLogger(__name__)


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
        ResourceType.SECURITY_GROUP: "AWS::EC2::SecurityGroup",
        ResourceType.INTERNET_GATEWAY: "AWS::EC2::InternetGateway",
        ResourceType.NAT_GATEWAY: "AWS::EC2::NatGateway",
        ResourceType.ROUTE_TABLE: "AWS::EC2::RouteTable",
        ResourceType.NETWORK_ACL: "AWS::EC2::NetworkAcl",
        ResourceType.VPC_ENDPOINT: "AWS::EC2::VPCEndpoint",
        ResourceType.EC2_INSTANCE: "AWS::EC2::Instance",
        ResourceType.LOAD_BALANCER: "AWS::ElasticLoadBalancingV2::LoadBalancer",
        ResourceType.TARGET_GROUP: "AWS::ElasticLoadBalancingV2::TargetGroup",
        ResourceType.RDS_INSTANCE: "AWS::RDS::DBInstance",
        ResourceType.RDS_CLUSTER: "AWS::RDS::DBCluster",
        ResourceType.ELASTICACHE_CLUSTER: "AWS::ElastiCache::CacheCluster",
        ResourceType.ECS_CLUSTER: "AWS::ECS::Cluster",
        ResourceType.ECS_SERVICE: "AWS::ECS::Service",
        ResourceType.LAMBDA_FUNCTION: "AWS::Lambda::Function",
        ResourceType.TRANSIT_GATEWAY: "AWS::EC2::TransitGateway",
        ResourceType.VPC_PEERING: "AWS::EC2::VPCPeeringConnection"
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
        # Create the main canvas and cloud structure
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
                "Children": self._get_vpc_children()
            }
        }
        
        # Add VPC resources
        self._add_vpc_resources(resources)
        
        # Add AZ stacks 
        self._add_az_stacks(resources)
        
        # Add other AWS resources (but skip individual subnets as they're now grouped)
        self._add_aws_resources(resources)
        
        # Create links from relationships
        links = self._create_links()
        
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
                
                # Create availability zone stacks
                az_stacks = self._create_az_stacks(resource_id)
                vpc_children.extend(az_stacks)
                
                # Add VPC endpoints as children
                vpc_endpoints = self._find_vpc_endpoints(resource_id)
                if vpc_endpoints:
                    # Group VPC endpoints
                    if len(vpc_endpoints) > 1:
                        endpoint_stack_id = f"{resource_id}-vpc-endpoints"
                        vpc_children.append(endpoint_stack_id)
                        resources[endpoint_stack_id] = {
                            "Type": "AWS::Diagram::HorizontalStack",
                            "Children": vpc_endpoints
                        }
                    else:
                        vpc_children.extend(vpc_endpoints)
                
                resources[resource_id] = {
                    "Type": "AWS::EC2::VPC",
                    "Direction": "vertical",
                    "Title": resource.name or resource_id,
                    "Children": vpc_children
                }
                
                if border_children:
                    resources[resource_id]["BorderChildren"] = border_children
    
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
                subnet_type = self._determine_subnet_type(resource)
                if subnet_type == 'public':
                    resource_def["Preset"] = "PublicSubnet"
                else:
                    resource_def["Preset"] = "PrivateSubnet"
                    
                # Find NAT Gateways and other resources in this subnet
                subnet_children = []
                for child_id, child in self.view.filtered_resources.items():
                    if child.properties.get('subnet_id') == resource_id:
                        if child.resource_type in [ResourceType.NAT_GATEWAY, ResourceType.EC2_INSTANCE]:
                            subnet_children.append(child_id)
                if subnet_children:
                    resource_def["Children"] = subnet_children
                    
            elif resource.resource_type == ResourceType.INTERNET_GATEWAY:
                # Internet Gateway styling
                resource_def["IconFill"] = {"Type": "rect"}
                
            elif resource.resource_type == ResourceType.VPC_ENDPOINT:
                # Determine VPC endpoint service for better labeling
                service_name = self._get_vpc_endpoint_service(resource)
                if service_name:
                    resource_def["Title"] = f"VPC Endpoint\n({service_name})"
                    
            elif resource.resource_type == ResourceType.NAT_GATEWAY:
                # NAT Gateway preset
                resource_def["Preset"] = "NAT Gateway"
            
            resources[resource_id] = resource_def
    
    def _create_links(self) -> List[Dict[str, Any]]:
        """Create links from topology relationships."""
        links = []
        
        for relationship in self.view.filtered_relationships:
            # Skip certain relationship types that are represented visually by layout
            if (relationship.relationship_type == RelationshipType.CONTAINS or
                relationship.relationship_type == RelationshipType.ATTACHED_TO or
                relationship.source_id not in self.view.filtered_resources or
                relationship.target_id not in self.view.filtered_resources):
                continue
                
            link = {
                "Source": relationship.source_id,
                "Target": relationship.target_id,
                "Type": "orthogonal",
                "SourcePosition": "N",
                "TargetPosition": "S"
            }
            
            # Add label based on relationship type
            if relationship.relationship_type == RelationshipType.ATTACHED_TO:
                link["Labels"] = {
                    "SourceLeft": {"Title": "attached"}
                }
            elif relationship.relationship_type == RelationshipType.ROUTES_TO:
                link["Labels"] = {
                    "SourceLeft": {"Title": "routes"}
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
        """Create child elements for subnet group (like NAT gateways, instances)."""
        children = []
        
        # Find resources that belong in these subnets
        for resource_id, resource in self.view.filtered_resources.items():
            if (hasattr(resource, 'properties') and 
                resource.properties.get('subnet_id') in subnets):
                # Add NAT gateways, instances, etc. that are in these subnets
                if resource.resource_type in [ResourceType.NAT_GATEWAY, ResourceType.EC2_INSTANCE]:
                    children.append(resource_id)
        
        return children
    
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
                    
                    resources[group_stack_id] = {
                        "Type": "AWS::Diagram::VerticalStack",  # Use VerticalStack with styling for boundaries
                        "Title": group_name.replace('-', ' ').title(),
                        "FillColor": self._get_subnet_group_rgba_color(group_name),
                        "BorderColor": "rgba(0,0,0,255)",  # Solid black border
                        "Direction": "vertical",
                        "Children": group_children
                    }
                
                # Create main subnet groups horizontal stack
                main_stack_id = f"{vpc_id}-azs"
                resources[main_stack_id] = {
                    "Type": "AWS::Diagram::HorizontalStack",
                    "Children": group_stack_ids
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