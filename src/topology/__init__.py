"""
Topology data structures and serialization.

This package defines the schema for representing AWS infrastructure topology
and provides serialization/deserialization capabilities for YAML storage.
"""

from .schema import (
    ResourceType, RelationshipType, BaseResource, NetworkResource, 
    ComputeResource, DatabaseResource, Relationship, CrossAccountRelationship,
    ResourceLocation, ResourceMetadata, RegionData, AccountData, 
    OrganizationData, TopologyMetadata, AWSTopology,
    create_vpc_resource, create_ec2_instance_resource, create_rds_instance_resource
)
from .serializer import TopologyYAMLSerializer

__all__ = [
    'ResourceType',
    'RelationshipType', 
    'BaseResource',
    'NetworkResource',
    'ComputeResource',
    'DatabaseResource',
    'Relationship',
    'CrossAccountRelationship',
    'ResourceLocation',
    'ResourceMetadata',
    'RegionData',
    'AccountData',
    'OrganizationData',
    'TopologyMetadata',
    'AWSTopology',
    'create_vpc_resource',
    'create_ec2_instance_resource',
    'create_rds_instance_resource',
    'TopologyYAMLSerializer'
]