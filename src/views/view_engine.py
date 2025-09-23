"""
View engine for filtering and extracting topology subsets.

This module provides the capability to create specific views of the AWS topology
based on various filtering criteria such as account, region, VPC, resource types,
and custom tag-based filters.
"""

from typing import Dict, List, Set, Optional, Any, Union
from dataclasses import dataclass
from enum import Enum
import logging

try:
    from ..topology.schema import (
        AWSTopology, AccountData, RegionData, BaseResource, Relationship,
        ResourceType, RelationshipType
    )
except ImportError:
    from topology.schema import (
        AWSTopology, AccountData, RegionData, BaseResource, Relationship,
        ResourceType, RelationshipType
    )

logger = logging.getLogger(__name__)


class FilterType(Enum):
    """Types of filters that can be applied to topology views."""
    ACCOUNT = "account"
    REGION = "region"
    RESOURCE_TYPE = "resource_type"
    VPC = "vpc"
    TAG = "tag"
    AVAILABILITY_ZONE = "availability_zone"
    RESOURCE_ID = "resource_id"


@dataclass
class ViewFilter:
    """Represents a single filter criteria for topology views."""
    filter_type: FilterType
    values: List[str]
    include: bool = True  # True for include, False for exclude
    
    def matches(self, resource: BaseResource) -> bool:
        """Check if a resource matches this filter."""
        if self.filter_type == FilterType.ACCOUNT:
            match = resource.location.account_id in self.values
        elif self.filter_type == FilterType.REGION:
            match = resource.location.region in self.values
        elif self.filter_type == FilterType.RESOURCE_TYPE:
            match = resource.resource_type.value in self.values
        elif self.filter_type == FilterType.VPC:
            # Check if resource is in specified VPC or IS the VPC
            if resource.resource_type == ResourceType.VPC:
                match = resource.resource_id in self.values
            else:
                vpc_id = resource.properties.get('vpc_id')
                match = vpc_id in self.values if vpc_id else False
        elif self.filter_type == FilterType.TAG:
            # Check if resource has any of the specified tag key=value pairs
            match = False
            for tag_spec in self.values:
                if '=' in tag_spec:
                    key, value = tag_spec.split('=', 1)
                    if resource.metadata.tags.get(key) == value:
                        match = True
                        break
                else:
                    # Just check for tag key existence
                    if tag_spec in resource.metadata.tags:
                        match = True
                        break
        elif self.filter_type == FilterType.AVAILABILITY_ZONE:
            match = resource.location.availability_zone in self.values
        elif self.filter_type == FilterType.RESOURCE_ID:
            match = resource.resource_id in self.values
        else:
            match = False
        
        return match if self.include else not match


@dataclass
class ViewDefinition:
    """Defines a view with filtering criteria and output options."""
    name: str
    description: str
    filters: List[ViewFilter]
    include_resource_types: Optional[Set[ResourceType]] = None
    include_relationship_types: Optional[Set[RelationshipType]] = None
    grouping_criteria: List[str] = None  # e.g., ['vpc_id', 'availability_zone']
    
    def __post_init__(self):
        if self.grouping_criteria is None:
            self.grouping_criteria = []


class TopologyView:
    """Represents a filtered view of the topology."""
    
    def __init__(
        self,
        name: str,
        description: str,
        source_topology: AWSTopology,
        filtered_resources: Dict[str, BaseResource],
        filtered_relationships: List[Relationship],
        metadata: Dict[str, Any]
    ):
        self.name = name
        self.description = description
        self.source_topology = source_topology
        self.filtered_resources = filtered_resources
        self.filtered_relationships = filtered_relationships
        self.metadata = metadata
    
    def get_resources_by_type(self, resource_type: ResourceType) -> List[BaseResource]:
        """Get all resources of a specific type in this view."""
        return [
            resource for resource in self.filtered_resources.values()
            if resource.resource_type == resource_type
        ]
    
    def get_resource_groups(self, group_by: str) -> Dict[str, List[BaseResource]]:
        """Group resources by a specific property."""
        groups = {}
        
        for resource in self.filtered_resources.values():
            if group_by == 'vpc_id':
                group_key = resource.properties.get('vpc_id', 'no-vpc')
            elif group_by == 'availability_zone':
                group_key = resource.location.availability_zone or 'no-az'
            elif group_by == 'account_id':
                group_key = resource.location.account_id
            elif group_by == 'region':
                group_key = resource.location.region
            elif group_by == 'resource_type':
                group_key = resource.resource_type.value
            else:
                # Try to get from properties or tags
                group_key = (
                    resource.properties.get(group_by) or 
                    resource.metadata.tags.get(group_by) or 
                    'unknown'
                )
            
            if group_key not in groups:
                groups[group_key] = []
            groups[group_key].append(resource)
        
        return groups
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about this view."""
        resource_counts = {}
        for resource_type in ResourceType:
            count = len(self.get_resources_by_type(resource_type))
            if count > 0:
                resource_counts[resource_type.value] = count
        
        return {
            'view_name': self.name,
            'total_resources': len(self.filtered_resources),
            'total_relationships': len(self.filtered_relationships),
            'resource_counts': resource_counts,
            'unique_accounts': len(set(r.location.account_id for r in self.filtered_resources.values())),
            'unique_regions': len(set(r.location.region for r in self.filtered_resources.values())),
            'metadata': self.metadata
        }


class ViewEngine:
    """Engine for creating filtered views of AWS topology."""
    
    def __init__(self, topology: AWSTopology):
        self.topology = topology
        
    def create_view(self, view_definition: ViewDefinition) -> TopologyView:
        """Create a filtered view based on the view definition."""
        logger.info(f"Creating view: {view_definition.name}")
        
        # Get all resources from topology
        all_resources = {}
        all_relationships = []
        
        for account in self.topology.organization.accounts.values():
            for region in account.regions.values():
                all_resources.update(region.resources)
                all_relationships.extend(region.relationships)
        
        # Apply filters to resources
        filtered_resources = self._filter_resources(all_resources, view_definition.filters)
        
        # Filter by resource types if specified
        if view_definition.include_resource_types:
            filtered_resources = {
                rid: resource for rid, resource in filtered_resources.items()
                if resource.resource_type in view_definition.include_resource_types
            }
        
        # Filter relationships to only include those between filtered resources
        filtered_relationships = self._filter_relationships(
            all_relationships, 
            filtered_resources,
            view_definition.include_relationship_types
        )
        
        # Add cross-account relationships if relevant
        cross_account_relationships = self._filter_cross_account_relationships(
            self.topology.organization.cross_account_relationships,
            filtered_resources
        )
        
        # Create metadata about the view
        metadata = {
            'source_accounts': list(set(r.location.account_id for r in filtered_resources.values())),
            'source_regions': list(set(r.location.region for r in filtered_resources.values())),
            'filters_applied': [
                {
                    'type': f.filter_type.value,
                    'values': f.values,
                    'include': f.include
                } for f in view_definition.filters
            ],
            'cross_account_relationships': len(cross_account_relationships)
        }
        
        logger.info(
            f"View created: {len(filtered_resources)} resources, "
            f"{len(filtered_relationships)} relationships"
        )
        
        return TopologyView(
            name=view_definition.name,
            description=view_definition.description,
            source_topology=self.topology,
            filtered_resources=filtered_resources,
            filtered_relationships=filtered_relationships,
            metadata=metadata
        )
    
    def _filter_resources(
        self, 
        resources: Dict[str, BaseResource], 
        filters: List[ViewFilter]
    ) -> Dict[str, BaseResource]:
        """Apply filters to resources."""
        if not filters:
            return resources
        
        filtered = {}
        
        for resource_id, resource in resources.items():
            include_resource = True
            
            for view_filter in filters:
                if not view_filter.matches(resource):
                    include_resource = False
                    break
            
            if include_resource:
                filtered[resource_id] = resource
        
        return filtered
    
    def _filter_relationships(
        self,
        relationships: List[Relationship],
        filtered_resources: Dict[str, BaseResource],
        include_relationship_types: Optional[Set[RelationshipType]] = None
    ) -> List[Relationship]:
        """Filter relationships to only include those between filtered resources."""
        filtered = []
        
        for relationship in relationships:
            # Check if both source and target are in filtered resources
            if (relationship.source_id in filtered_resources and 
                relationship.target_id in filtered_resources):
                
                # Check relationship type filter if specified
                if (include_relationship_types is None or 
                    relationship.relationship_type in include_relationship_types):
                    filtered.append(relationship)
        
        return filtered
    
    def _filter_cross_account_relationships(
        self,
        cross_account_relationships,
        filtered_resources: Dict[str, BaseResource]
    ) -> List:
        """Filter cross-account relationships relevant to the view."""
        # This is a simplified implementation
        # In a full implementation, you'd need to check if the relationships
        # involve any of the filtered resources
        return []
    
    def _get_all_resources(self) -> Dict[str, BaseResource]:
        """Get all resources from the topology."""
        all_resources = {}
        for account_data in self.topology.organization.accounts.values():
            for region_data in account_data.regions.values():
                all_resources.update(region_data.resources)
        return all_resources
    
    def _get_all_relationships(self) -> List[Relationship]:
        """Get all relationships from the topology."""
        all_relationships = []
        for account_data in self.topology.organization.accounts.values():
            for region_data in account_data.regions.values():
                all_relationships.extend(region_data.relationships)
        return all_relationships
    
    def create_single_vpc_view(
        self, 
        vpc_id: str, 
        account_id: Optional[str] = None,
        region: Optional[str] = None
    ) -> TopologyView:
        """Create a view focused on a single VPC and its resources.

        This implementation avoids cross-account ID collisions by first
        locating the canonical VPC resource (and its owning account), then
        collecting VPC membership resources only from that account. External
        nodes (TGW, peered VPCs) are added explicitly regardless of account.
        """

        # Get a full map of resources for cross-account lookups (e.g., peers/PCX)
        all_resources = self._get_all_resources()

        # Locate the VPC and determine its owning account/region
        selected_account_id: Optional[str] = None
        selected_vpc_resource: Optional[BaseResource] = None
        selected_region_name: Optional[str] = None

        for acct_id, acct in self.topology.organization.accounts.items():
            if account_id and acct_id != account_id:
                continue
            for region_name, region_data in acct.regions.items():
                res = region_data.resources.get(vpc_id)
                if res and res.resource_type == ResourceType.VPC:
                    selected_account_id = acct_id
                    selected_vpc_resource = res
                    selected_region_name = region_name
                    break
            if selected_vpc_resource:
                break

        if not selected_vpc_resource:
            # Fallback: use any resource with matching id (prevents empty view)
            res = all_resources.get(vpc_id)
            if res:
                selected_vpc_resource = res
                selected_account_id = res.location.account_id
                selected_region_name = res.location.region

        vpc_resources: Dict[str, BaseResource] = {}
        if selected_vpc_resource:
            vpc_resources[vpc_id] = selected_vpc_resource

        # Collect membership resources from the owning account only
        if selected_account_id:
            acct = self.topology.organization.accounts[selected_account_id]
            for region_name, region_data in acct.regions.items():
                for rid, resource in region_data.resources.items():
                    if resource.properties.get('vpc_id') == vpc_id:
                        vpc_resources[rid] = resource

        # Add resources attached to the VPC within the owning account (via relationships)
        all_relationships = self._get_all_relationships()
        if selected_account_id:
            acct = self.topology.organization.accounts[selected_account_id]
            for region_name, region_data in acct.regions.items():
                for relationship in region_data.relationships:
                    if (
                        relationship.relationship_type == RelationshipType.ATTACHED_TO and
                        relationship.target_id == vpc_id
                    ):
                        src = region_data.resources.get(relationship.source_id)
                        if src:
                            vpc_resources[src.resource_id] = src

        # Include one-hop contained children from known VPC resources (e.g., subnets contain services/lambdas)
        for relationship in all_relationships:
            if relationship.relationship_type == RelationshipType.CONTAINS and relationship.source_id in vpc_resources:
                child = self._get_all_resources().get(relationship.target_id)
                if child:
                    vpc_resources[child.resource_id] = child

        # Include VPC peering connection resources that involve this VPC
        for resource_id, resource in all_resources.items():
            try:
                rtype = resource.resource_type
            except Exception:
                rtype = None
            if rtype == ResourceType.VPC_PEERING:
                req_vpc = (resource.properties.get('requester_vpc') or {}).get('VpcId')
                acc_vpc = (resource.properties.get('accepter_vpc') or {}).get('VpcId')
                if vpc_id in {req_vpc, acc_vpc}:
                    vpc_resources[resource_id] = resource

        # Include Transit Gateways connected to this VPC
        for relationship in all_relationships:
            if relationship.relationship_type == RelationshipType.CONNECTS_TO:
                if relationship.source_id == vpc_id:
                    other_id = relationship.target_id
                elif relationship.target_id == vpc_id:
                    other_id = relationship.source_id
                else:
                    other_id = None
                if other_id and other_id in all_resources:
                    other_res = all_resources[other_id]
                    if other_res.resource_type == ResourceType.TRANSIT_GATEWAY:
                        vpc_resources[other_id] = other_res

        # Include peered VPCs; if missing from topology, create a bare synthetic one
        from datetime import datetime
        if True:
            for relationship in all_relationships:
                if relationship.relationship_type == RelationshipType.PEERS_WITH:
                    if relationship.source_id == vpc_id:
                        peer_id = relationship.target_id
                    elif relationship.target_id == vpc_id:
                        peer_id = relationship.source_id
                    else:
                        peer_id = None
                    if not peer_id:
                        continue
                    if peer_id in all_resources:
                        peer_res = all_resources[peer_id]
                        if peer_res.resource_type == ResourceType.VPC:
                            vpc_resources[peer_id] = peer_res
                    else:
                        # Create a bare VPC container using info from VPC Peering resource
                        # Find the peering resource to extract account/region if available
                        for rid, res in all_resources.items():
                            if getattr(res, 'resource_type', None) == ResourceType.VPC_PEERING:
                                req = (res.properties.get('requester_vpc') or {})
                                acc = (res.properties.get('accepter_vpc') or {})
                                if req.get('VpcId') == peer_id or acc.get('VpcId') == peer_id:
                                    # Build a synthetic VPC BaseResource
                                    from topology.schema import ResourceLocation, ResourceMetadata, BaseResource, ResourceType as RT
                                    # Prefer the matching side for account/region
                                    side = req if req.get('VpcId') == peer_id else acc
                                    account = side.get('OwnerId', 'unknown')
                                    region = side.get('Region', 'unknown')
                                    location = ResourceLocation(account_id=str(account), region=str(region))
                                    meta = ResourceMetadata(discovered_at=datetime.now(), last_updated=datetime.now())
                                    synthetic = BaseResource(
                                        resource_id=peer_id,
                                        resource_type=RT.VPC,
                                        name=peer_id,
                                        arn="",
                                        location=location,
                                        metadata=meta,
                                        properties={}
                                    )
                                    vpc_resources[peer_id] = synthetic
                                    break

        # Include ECS clusters when their services are in the VPC
        for resource_id, resource in all_resources.items():
            if resource.resource_type == ResourceType.ECS_CLUSTER:
                # Check if any ECS services in this VPC belong to this cluster
                cluster_arn = resource.arn
                for vpc_resource_id, vpc_resource in vpc_resources.items():
                    if (vpc_resource.resource_type == ResourceType.ECS_SERVICE and
                        vpc_resource.properties.get('clusterArn') == cluster_arn):
                        # Found a service in this VPC that belongs to this cluster
                        vpc_resources[resource_id] = resource
                        logger.debug(f"Including ECS cluster {resource.name} because service {vpc_resource.name} is in VPC {vpc_id}")
                        break

        # Apply optional explicit filters for account/region without cross-account expansion
        if account_id:
            vpc_resources = {rid: res for rid, res in vpc_resources.items() if res.location.account_id == account_id}
        if region:
            vpc_resources = {rid: res for rid, res in vpc_resources.items() if res.location.region == region}
        
        # Filter relationships to only include those between VPC resources
        vpc_relationships = []
        for relationship in all_relationships:
            if (
                relationship.source_id in vpc_resources and
                relationship.target_id in vpc_resources
            ):
                vpc_relationships.append(relationship)

        # If no explicit PEERS_WITH relationship was collected, synthesize it from peering resources
        has_peer_rel = any(
            rel.relationship_type == RelationshipType.PEERS_WITH and 
            (rel.source_id == vpc_id or rel.target_id == vpc_id)
            for rel in vpc_relationships
        )
        if not has_peer_rel:
            for rid, res in vpc_resources.items():
                try:
                    rtype = res.resource_type
                except Exception:
                    rtype = None
                if rtype == ResourceType.VPC_PEERING:
                    req_vpc = (res.properties.get('requester_vpc') or {}).get('VpcId')
                    acc_vpc = (res.properties.get('accepter_vpc') or {}).get('VpcId')
                    if vpc_id in {req_vpc, acc_vpc}:
                        peer_id = req_vpc if acc_vpc == vpc_id else acc_vpc
                        if peer_id and peer_id in vpc_resources:
                            vpc_relationships.append(Relationship(
                                source_id=vpc_id,
                                target_id=peer_id,
                                relationship_type=RelationshipType.PEERS_WITH,
                                properties={'inferred': True}
                            ))
                            break
        
        # Create metadata
        metadata = {
            'source_accounts': list(set(r.location.account_id for r in vpc_resources.values())),
            'source_regions': list(set(r.location.region for r in vpc_resources.values())),
            'filters_applied': [
                {'type': 'vpc', 'values': [vpc_id], 'include': True}
            ],
            'cross_account_relationships': 0
        }
        
        logger.info(
            f"VPC view created: {len(vpc_resources)} resources, "
            f"{len(vpc_relationships)} relationships"
        )
        
        return TopologyView(
            name=f"Single VPC View: {vpc_id}",
            description=f"All resources within VPC {vpc_id}",
            source_topology=self.topology,
            filtered_resources=vpc_resources,
            filtered_relationships=vpc_relationships,
            metadata=metadata
        )
    
    def create_cross_account_connectivity_view(self) -> TopologyView:
        """Create a view showing cross-account connectivity."""
        view_definition = ViewDefinition(
            name="Cross-Account Connectivity",
            description="VPCs and cross-account connections",
            filters=[],  # No filters - include all accounts
            include_resource_types={
                ResourceType.VPC, ResourceType.TRANSIT_GATEWAY, ResourceType.VPC_PEERING
            },
            include_relationship_types={
                RelationshipType.PEERS_WITH, RelationshipType.CONNECTS_TO
            },
            grouping_criteria=['account_id', 'region']
        )
        
        return self.create_view(view_definition)
    
    def create_security_view(
        self,
        account_ids: Optional[List[str]] = None,
        regions: Optional[List[str]] = None
    ) -> TopologyView:
        """Create a view focused on security groups and their relationships."""
        filters = []
        
        if account_ids:
            filters.append(ViewFilter(FilterType.ACCOUNT, account_ids))
        
        if regions:
            filters.append(ViewFilter(FilterType.REGION, regions))
        
        view_definition = ViewDefinition(
            name="Security Groups View",
            description="Security groups and their relationships to resources",
            filters=filters,
            include_resource_types={
                ResourceType.SECURITY_GROUP, ResourceType.EC2_INSTANCE,
                ResourceType.LOAD_BALANCER, ResourceType.RDS_INSTANCE,
                ResourceType.VPC, ResourceType.SUBNET
            },
            include_relationship_types={
                RelationshipType.MEMBER_OF, RelationshipType.ALLOWS,
                RelationshipType.CONTAINS, RelationshipType.ATTACHED_TO
            },
            grouping_criteria=['vpc_id', 'security_group']
        )
        
        return self.create_view(view_definition)
    
    def create_compute_view(
        self,
        account_ids: Optional[List[str]] = None,
        regions: Optional[List[str]] = None
    ) -> TopologyView:
        """Create a view focused on compute resources."""
        filters = []
        
        if account_ids:
            filters.append(ViewFilter(FilterType.ACCOUNT, account_ids))
        
        if regions:
            filters.append(ViewFilter(FilterType.REGION, regions))
        
        view_definition = ViewDefinition(
            name="Compute Resources View", 
            description="EC2 instances, ECS services, and Lambda functions",
            filters=filters,
            include_resource_types={
                ResourceType.EC2_INSTANCE, ResourceType.ECS_CLUSTER,
                ResourceType.ECS_SERVICE, ResourceType.LAMBDA_FUNCTION,
                ResourceType.LOAD_BALANCER, ResourceType.TARGET_GROUP,
                ResourceType.VPC, ResourceType.SUBNET, ResourceType.SECURITY_GROUP
            },
            grouping_criteria=['vpc_id', 'availability_zone', 'resource_type']
        )
        
        return self.create_view(view_definition)
