"""
YAML serialization and deserialization for topology data.

This module handles converting between Python topology objects and YAML format,
with proper handling of complex data types and relationships.
"""

import yaml
import json
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path

from .schema import (
    AWSTopology, TopologyMetadata, OrganizationData, AccountData, RegionData,
    BaseResource, NetworkResource, ComputeResource, DatabaseResource,
    Relationship, CrossAccountRelationship, ResourceLocation, ResourceMetadata,
    ResourceType, RelationshipType
)


class TopologyYAMLSerializer:
    """Handles serialization and deserialization of topology data to/from YAML."""
    
    def __init__(self):
        pass
    
    def serialize_topology(self, topology: AWSTopology) -> Dict[str, Any]:
        """Convert topology object to dictionary suitable for YAML serialization."""
        return {
            "aws_topology": {
                "metadata": self._serialize_metadata(topology.metadata),
                "organization": self._serialize_organization(topology.organization),
                "data_sources": topology.data_sources
            }
        }
    
    def _serialize_metadata(self, metadata: TopologyMetadata) -> Dict[str, Any]:
        """Serialize topology metadata."""
        return {
            "generated_at": metadata.generated_at.isoformat(),
            "generator_version": metadata.generator_version,
            "last_updated": metadata.last_updated.isoformat(),
            "total_accounts": metadata.total_accounts,
            "total_regions": metadata.total_regions,
            "total_resources": metadata.total_resources,
            "collection_duration_seconds": metadata.collection_duration_seconds,
            "api_calls_made": metadata.api_calls_made,
            "errors": metadata.errors
        }
    
    def _serialize_organization(self, org: OrganizationData) -> Dict[str, Any]:
        """Serialize organization data."""
        return {
            "organization_id": org.organization_id,
            "management_account_id": org.management_account_id,
            "accounts": {
                account_id: self._serialize_account(account)
                for account_id, account in org.accounts.items()
            },
            "cross_account_relationships": [
                self._serialize_cross_account_relationship(rel)
                for rel in org.cross_account_relationships
            ]
        }
    
    def _serialize_account(self, account: AccountData) -> Dict[str, Any]:
        """Serialize account data."""
        return {
            "account_id": account.account_id,
            "account_name": account.account_name,
            "last_updated": account.last_updated.isoformat(),
            "regions": {
                region_name: self._serialize_region(region)
                for region_name, region in account.regions.items()
            },
            "cross_region_relationships": [
                self._serialize_relationship(rel)
                for rel in account.cross_region_relationships
            ]
        }
    
    def _serialize_region(self, region: RegionData) -> Dict[str, Any]:
        """Serialize region data."""
        # Group resources by type for better YAML organization
        resources_by_type = {}
        for resource in region.resources.values():
            resource_type = resource.resource_type.value
            if resource_type not in resources_by_type:
                resources_by_type[resource_type] = {}
            resources_by_type[resource_type][resource.resource_id] = self._serialize_resource(resource)
        
        return {
            "region": region.region,
            "last_updated": region.last_updated.isoformat(),
            "resources": resources_by_type,
            "relationships": [
                self._serialize_relationship(rel)
                for rel in region.relationships
            ]
        }
    
    def _serialize_resource(self, resource: BaseResource) -> Dict[str, Any]:
        """Serialize a resource object."""
        base_data = {
            "resource_id": resource.resource_id,
            "resource_type": resource.resource_type.value,
            "name": resource.name,
            "arn": resource.arn,
            "location": {
                "account_id": resource.location.account_id,
                "region": resource.location.region,
                "availability_zone": resource.location.availability_zone
            },
            "metadata": {
                "discovered_at": resource.metadata.discovered_at.isoformat(),
                "last_updated": resource.metadata.last_updated.isoformat(),
                "api_calls_made": resource.metadata.api_calls_made,
                "collection_errors": resource.metadata.collection_errors,
                "tags": resource.metadata.tags
            },
            "properties": resource.properties
        }
        
        # Add type-specific fields
        if isinstance(resource, NetworkResource):
            base_data["cidr_blocks"] = resource.cidr_blocks
        elif isinstance(resource, ComputeResource):
            base_data.update({
                "instance_type": resource.instance_type,
                "state": resource.state,
                "private_ip": resource.private_ip,
                "public_ip": resource.public_ip
            })
        elif isinstance(resource, DatabaseResource):
            base_data.update({
                "engine": resource.engine,
                "engine_version": resource.engine_version,
                "instance_class": resource.instance_class,
                "endpoint": resource.endpoint
            })
        
        return base_data
    
    def _serialize_relationship(self, relationship: Relationship) -> Dict[str, Any]:
        """Serialize a relationship object."""
        return {
            "source_id": relationship.source_id,
            "target_id": relationship.target_id,
            "relationship_type": relationship.relationship_type.value,
            "properties": relationship.properties
        }
    
    def _serialize_cross_account_relationship(self, relationship: CrossAccountRelationship) -> Dict[str, Any]:
        """Serialize a cross-account relationship object."""
        return {
            "relationship_type": relationship.relationship_type.value,
            "source_account": relationship.source_account,
            "source_resource": relationship.source_resource,
            "target_account": relationship.target_account,
            "target_resource": relationship.target_resource,
            "properties": relationship.properties
        }
    
    def save_to_file(self, topology: AWSTopology, filepath: Path) -> None:
        """Save topology to YAML file."""
        data = self.serialize_topology(topology)
        with open(filepath, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, indent=2)
    
    def load_from_file(self, filepath: Path) -> AWSTopology:
        """Load topology from YAML file."""
        with open(filepath, 'r') as f:
            data = yaml.safe_load(f)
        return self.deserialize_topology(data)
    
    def deserialize_topology(self, data: Dict[str, Any]) -> AWSTopology:
        """Convert dictionary from YAML to topology object."""
        topology_data = data["aws_topology"]
        
        metadata = self._deserialize_metadata(topology_data["metadata"])
        organization = self._deserialize_organization(topology_data["organization"])
        data_sources = topology_data.get("data_sources", {})
        
        return AWSTopology(
            metadata=metadata,
            organization=organization,
            data_sources=data_sources
        )
    
    def _deserialize_metadata(self, data: Dict[str, Any]) -> TopologyMetadata:
        """Deserialize topology metadata."""
        return TopologyMetadata(
            generated_at=datetime.fromisoformat(data["generated_at"]),
            generator_version=data["generator_version"],
            last_updated=datetime.fromisoformat(data["last_updated"]),
            total_accounts=data.get("total_accounts", 0),
            total_regions=data.get("total_regions", 0),
            total_resources=data.get("total_resources", 0),
            collection_duration_seconds=data.get("collection_duration_seconds", 0.0),
            api_calls_made=data.get("api_calls_made", 0),
            errors=data.get("errors", [])
        )
    
    def _deserialize_organization(self, data: Dict[str, Any]) -> OrganizationData:
        """Deserialize organization data."""
        accounts = {}
        for account_id, account_data in data.get("accounts", {}).items():
            accounts[account_id] = self._deserialize_account(account_data)
        
        cross_account_relationships = []
        for rel_data in data.get("cross_account_relationships", []):
            cross_account_relationships.append(
                self._deserialize_cross_account_relationship(rel_data)
            )
        
        return OrganizationData(
            organization_id=data.get("organization_id"),
            management_account_id=data["management_account_id"],
            accounts=accounts,
            cross_account_relationships=cross_account_relationships
        )
    
    def _deserialize_account(self, data: Dict[str, Any]) -> AccountData:
        """Deserialize account data."""
        regions = {}
        for region_name, region_data in data.get("regions", {}).items():
            regions[region_name] = self._deserialize_region(region_data)
        
        cross_region_relationships = []
        for rel_data in data.get("cross_region_relationships", []):
            cross_region_relationships.append(self._deserialize_relationship(rel_data))
        
        return AccountData(
            account_id=data["account_id"],
            account_name=data.get("account_name"),
            last_updated=datetime.fromisoformat(data["last_updated"]),
            regions=regions,
            cross_region_relationships=cross_region_relationships
        )
    
    def _deserialize_region(self, data: Dict[str, Any]) -> RegionData:
        """Deserialize region data."""
        region = RegionData(
            region=data["region"],
            last_updated=datetime.fromisoformat(data["last_updated"])
        )
        
        # Deserialize resources grouped by type
        for resource_type, type_resources in data.get("resources", {}).items():
            for resource_id, resource_data in type_resources.items():
                resource = self._deserialize_resource(resource_data)
                region.add_resource(resource)
        
        # Deserialize relationships
        for rel_data in data.get("relationships", []):
            region.relationships.append(self._deserialize_relationship(rel_data))
        
        return region
    
    def _deserialize_resource(self, data: Dict[str, Any]) -> BaseResource:
        """Deserialize a resource object."""
        resource_type = ResourceType(data["resource_type"])
        location = ResourceLocation(
            account_id=data["location"]["account_id"],
            region=data["location"]["region"],
            availability_zone=data["location"].get("availability_zone")
        )
        metadata = ResourceMetadata(
            discovered_at=datetime.fromisoformat(data["metadata"]["discovered_at"]),
            last_updated=datetime.fromisoformat(data["metadata"]["last_updated"]),
            api_calls_made=data["metadata"].get("api_calls_made", 0),
            collection_errors=data["metadata"].get("collection_errors", []),
            tags=data["metadata"].get("tags", {})
        )
        
        # Create appropriate resource type
        if resource_type in [ResourceType.VPC, ResourceType.SUBNET]:
            return NetworkResource(
                resource_id=data["resource_id"],
                resource_type=resource_type,
                name=data.get("name"),
                arn=data["arn"],
                location=location,
                metadata=metadata,
                properties=data.get("properties", {}),
                cidr_blocks=data.get("cidr_blocks", [])
            )
        elif resource_type == ResourceType.EC2_INSTANCE:
            return ComputeResource(
                resource_id=data["resource_id"],
                resource_type=resource_type,
                name=data.get("name"),
                arn=data["arn"],
                location=location,
                metadata=metadata,
                properties=data.get("properties", {}),
                instance_type=data.get("instance_type"),
                state=data.get("state"),
                private_ip=data.get("private_ip"),
                public_ip=data.get("public_ip")
            )
        elif resource_type in [ResourceType.RDS_INSTANCE, ResourceType.RDS_CLUSTER]:
            return DatabaseResource(
                resource_id=data["resource_id"],
                resource_type=resource_type,
                name=data.get("name"),
                arn=data["arn"],
                location=location,
                metadata=metadata,
                properties=data.get("properties", {}),
                engine=data.get("engine"),
                engine_version=data.get("engine_version"),
                instance_class=data.get("instance_class"),
                endpoint=data.get("endpoint")
            )
        else:
            return BaseResource(
                resource_id=data["resource_id"],
                resource_type=resource_type,
                name=data.get("name"),
                arn=data["arn"],
                location=location,
                metadata=metadata,
                properties=data.get("properties", {})
            )
    
    def _deserialize_relationship(self, data: Dict[str, Any]) -> Relationship:
        """Deserialize a relationship object."""
        return Relationship(
            source_id=data["source_id"],
            target_id=data["target_id"],
            relationship_type=RelationshipType(data["relationship_type"]),
            properties=data.get("properties", {})
        )
    
    def _deserialize_cross_account_relationship(self, data: Dict[str, Any]) -> CrossAccountRelationship:
        """Deserialize a cross-account relationship object."""
        return CrossAccountRelationship(
            relationship_type=RelationshipType(data["relationship_type"]),
            source_account=data["source_account"],
            source_resource=data["source_resource"],
            target_account=data["target_account"],
            target_resource=data["target_resource"],
            properties=data.get("properties", {})
        )