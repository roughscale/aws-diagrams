"""
Core topology schema classes for AWS infrastructure representation.

This module defines the data structure for storing AWS infrastructure topology
in a standardized YAML format that supports multi-account, multi-region scenarios.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Union
from datetime import datetime
from enum import Enum


class ResourceType(Enum):
    """Enumeration of supported AWS resource types."""
    VPC = "vpc"
    SUBNET = "subnet"
    SECURITY_GROUP = "security_group"
    NETWORK_ACL = "network_acl"
    ROUTE_TABLE = "route_table"
    INTERNET_GATEWAY = "internet_gateway"
    NAT_GATEWAY = "nat_gateway"
    VPC_ENDPOINT = "vpc_endpoint"
    EC2_INSTANCE = "ec2_instance"
    LOAD_BALANCER = "load_balancer"
    TARGET_GROUP = "target_group"
    RDS_INSTANCE = "rds_instance"
    RDS_CLUSTER = "rds_cluster"
    ELASTICACHE_CLUSTER = "elasticache_cluster"
    ECS_CLUSTER = "ecs_cluster"
    ECS_SERVICE = "ecs_service"
    LAMBDA_FUNCTION = "lambda_function"
    TRANSIT_GATEWAY = "transit_gateway"
    VPC_PEERING = "vpc_peering"


class RelationshipType(Enum):
    """Types of relationships between resources."""
    CONTAINS = "contains"              # VPC contains subnets
    ATTACHED_TO = "attached_to"        # Instance attached to subnet
    ROUTES_TO = "routes_to"            # Route table routes to gateway
    ALLOWS = "allows"                  # Security group allows traffic
    PEERS_WITH = "peers_with"          # VPC peers with another VPC
    CONNECTS_TO = "connects_to"        # Transit Gateway connects VPCs
    TARGETS = "targets"                # Load balancer targets instances
    MEMBER_OF = "member_of"            # Instance member of security group


@dataclass
class ResourceMetadata:
    """Metadata for tracking resource discovery and updates."""
    discovered_at: datetime
    last_updated: datetime
    api_calls_made: int = 0
    collection_errors: List[str] = field(default_factory=list)
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class ResourceLocation:
    """Location information for AWS resources."""
    account_id: str
    region: str
    availability_zone: Optional[str] = None
    
    def __str__(self) -> str:
        location = f"{self.account_id}:{self.region}"
        if self.availability_zone:
            location += f":{self.availability_zone}"
        return location


@dataclass
class BaseResource:
    """Base class for all AWS resources."""
    resource_id: str
    resource_type: ResourceType
    name: Optional[str]
    arn: str
    location: ResourceLocation
    metadata: ResourceMetadata
    properties: Dict[str, Any] = field(default_factory=dict)
    
    def get_unique_id(self) -> str:
        """Generate a unique identifier for this resource."""
        return f"{self.location}:{self.resource_type.value}:{self.resource_id}"


@dataclass
class NetworkResource(BaseResource):
    """Network-specific resources with CIDR blocks."""
    cidr_blocks: List[str] = field(default_factory=list)
    
    
@dataclass
class ComputeResource(BaseResource):
    """Compute resources like EC2 instances."""
    instance_type: Optional[str] = None
    state: Optional[str] = None
    private_ip: Optional[str] = None
    public_ip: Optional[str] = None


@dataclass
class DatabaseResource(BaseResource):
    """Database resources like RDS instances."""
    engine: Optional[str] = None
    engine_version: Optional[str] = None
    instance_class: Optional[str] = None
    endpoint: Optional[str] = None


@dataclass
class Relationship:
    """Represents a relationship between two resources."""
    source_id: str
    target_id: str
    relationship_type: RelationshipType
    properties: Dict[str, Any] = field(default_factory=dict)
    
    def __str__(self) -> str:
        return f"{self.source_id} {self.relationship_type.value} {self.target_id}"


@dataclass
class CrossAccountRelationship:
    """Represents relationships that span AWS accounts."""
    relationship_type: RelationshipType
    source_account: str
    source_resource: str
    target_account: str
    target_resource: str
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RegionData:
    """Data for a specific AWS region within an account."""
    region: str
    resources: Dict[str, BaseResource] = field(default_factory=dict)
    relationships: List[Relationship] = field(default_factory=list)
    last_updated: datetime = field(default_factory=datetime.now)
    
    def add_resource(self, resource: BaseResource) -> None:
        """Add a resource to this region."""
        self.resources[resource.resource_id] = resource
        
    def get_resources_by_type(self, resource_type: ResourceType) -> List[BaseResource]:
        """Get all resources of a specific type."""
        return [r for r in self.resources.values() if r.resource_type == resource_type]


@dataclass
class AccountData:
    """Data for a specific AWS account."""
    account_id: str
    account_name: Optional[str]
    regions: Dict[str, RegionData] = field(default_factory=dict)
    cross_region_relationships: List[Relationship] = field(default_factory=list)
    last_updated: datetime = field(default_factory=datetime.now)
    
    def add_region(self, region: str) -> RegionData:
        """Add a region to this account."""
        if region not in self.regions:
            self.regions[region] = RegionData(region=region)
        return self.regions[region]
    
    def get_all_resources(self) -> List[BaseResource]:
        """Get all resources across all regions in this account."""
        resources = []
        for region_data in self.regions.values():
            resources.extend(region_data.resources.values())
        return resources


@dataclass
class OrganizationData:
    """Data for an AWS Organization."""
    organization_id: Optional[str]
    management_account_id: str
    accounts: Dict[str, AccountData] = field(default_factory=dict)
    cross_account_relationships: List[CrossAccountRelationship] = field(default_factory=list)
    
    def add_account(self, account_id: str, account_name: Optional[str] = None) -> AccountData:
        """Add an account to the organization."""
        if account_id not in self.accounts:
            self.accounts[account_id] = AccountData(
                account_id=account_id,
                account_name=account_name
            )
        return self.accounts[account_id]


@dataclass
class TopologyMetadata:
    """Metadata about the topology collection process."""
    generated_at: datetime
    generator_version: str
    last_updated: datetime
    total_accounts: int = 0
    total_regions: int = 0
    total_resources: int = 0
    collection_duration_seconds: float = 0.0
    api_calls_made: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class AWSTopology:
    """Complete AWS infrastructure topology representation."""
    metadata: TopologyMetadata
    organization: OrganizationData
    data_sources: Dict[str, Any] = field(default_factory=dict)
    
    def get_resource_by_id(self, resource_id: str, account_id: str, region: str) -> Optional[BaseResource]:
        """Retrieve a specific resource by its identifiers."""
        if account_id in self.organization.accounts:
            account = self.organization.accounts[account_id]
            if region in account.regions:
                region_data = account.regions[region]
                return region_data.resources.get(resource_id)
        return None
    
    def get_resources_by_type(self, resource_type: ResourceType) -> List[BaseResource]:
        """Get all resources of a specific type across all accounts and regions."""
        resources = []
        for account in self.organization.accounts.values():
            for region_data in account.regions.values():
                resources.extend(region_data.get_resources_by_type(resource_type))
        return resources
    
    def get_account_resources(self, account_id: str) -> List[BaseResource]:
        """Get all resources for a specific account."""
        if account_id in self.organization.accounts:
            return self.organization.accounts[account_id].get_all_resources()
        return []
    
    def add_cross_account_relationship(self, relationship: CrossAccountRelationship) -> None:
        """Add a cross-account relationship to the topology."""
        self.organization.cross_account_relationships.append(relationship)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Generate statistics about the topology."""
        total_resources = sum(
            len(region.resources) 
            for account in self.organization.accounts.values()
            for region in account.regions.values()
        )
        
        resource_counts = {}
        for resource_type in ResourceType:
            resource_counts[resource_type.value] = len(self.get_resources_by_type(resource_type))
        
        return {
            "total_accounts": len(self.organization.accounts),
            "total_regions": sum(len(account.regions) for account in self.organization.accounts.values()),
            "total_resources": total_resources,
            "resource_counts": resource_counts,
            "cross_account_relationships": len(self.organization.cross_account_relationships),
            "last_updated": max(
                account.last_updated 
                for account in self.organization.accounts.values()
            ) if self.organization.accounts else self.metadata.last_updated
        }


# Factory functions for creating specific resource types

def create_vpc_resource(
    vpc_id: str,
    cidr_block: str,
    location: ResourceLocation,
    name: Optional[str] = None,
    **properties
) -> NetworkResource:
    """Factory function for creating VPC resources."""
    return NetworkResource(
        resource_id=vpc_id,
        resource_type=ResourceType.VPC,
        name=name,
        arn=f"arn:aws:ec2:{location.region}:{location.account_id}:vpc/{vpc_id}",
        location=location,
        metadata=ResourceMetadata(
            discovered_at=datetime.now(),
            last_updated=datetime.now()
        ),
        cidr_blocks=[cidr_block],
        properties=properties
    )


def create_ec2_instance_resource(
    instance_id: str,
    instance_type: str,
    location: ResourceLocation,
    name: Optional[str] = None,
    **properties
) -> ComputeResource:
    """Factory function for creating EC2 instance resources."""
    return ComputeResource(
        resource_id=instance_id,
        resource_type=ResourceType.EC2_INSTANCE,
        name=name,
        arn=f"arn:aws:ec2:{location.region}:{location.account_id}:instance/{instance_id}",
        location=location,
        metadata=ResourceMetadata(
            discovered_at=datetime.now(),
            last_updated=datetime.now()
        ),
        instance_type=instance_type,
        properties=properties
    )


def create_rds_instance_resource(
    db_instance_id: str,
    engine: str,
    location: ResourceLocation,
    name: Optional[str] = None,
    **properties
) -> DatabaseResource:
    """Factory function for creating RDS instance resources."""
    return DatabaseResource(
        resource_id=db_instance_id,
        resource_type=ResourceType.RDS_INSTANCE,
        name=name,
        arn=f"arn:aws:rds:{location.region}:{location.account_id}:db:{db_instance_id}",
        location=location,
        metadata=ResourceMetadata(
            discovered_at=datetime.now(),
            last_updated=datetime.now()
        ),
        engine=engine,
        properties=properties
    )