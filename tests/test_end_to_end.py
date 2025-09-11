"""
End-to-end integration tests for AWS topology discovery and diagram generation.

This module tests the complete workflow from topology creation through
view filtering to diagram generation.
"""

import pytest
import sys
from pathlib import Path
from datetime import datetime
import tempfile
import yaml

# Add src to path for imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from topology.schema import (
    AWSTopology, TopologyMetadata, OrganizationData, 
    ResourceType, create_vpc_resource, create_ec2_instance_resource,
    ResourceLocation, Relationship, RelationshipType
)
from topology.serializer import TopologyYAMLSerializer
from views.view_engine import ViewEngine, ViewFilter, FilterType, ViewDefinition
from transformers.awslabs_transformer import AWSLabsTransformer


def create_test_topology():
    """Create a test topology with multiple resources for testing."""
    
    metadata = TopologyMetadata(
        generated_at=datetime.now(),
        generator_version="0.1.0",
        last_updated=datetime.now()
    )
    
    organization = OrganizationData(
        organization_id="o-test123",
        management_account_id="123456789012"
    )
    
    topology = AWSTopology(
        metadata=metadata,
        organization=organization
    )
    
    # Add production account
    prod_account = topology.organization.add_account("123456789012", "production")
    us_east_1 = prod_account.add_region("us-east-1")
    
    # Create VPC
    vpc_location = ResourceLocation(
        account_id="123456789012",
        region="us-east-1"
    )
    
    vpc_resource = create_vpc_resource(
        vpc_id="vpc-prod123",
        cidr_block="10.0.0.0/16",
        location=vpc_location,
        name="production-vpc"
    )
    vpc_resource.metadata.tags = {"Environment": "production", "Team": "platform"}
    us_east_1.add_resource(vpc_resource)
    
    # Create public subnet
    subnet_location = ResourceLocation(
        account_id="123456789012",
        region="us-east-1",
        availability_zone="us-east-1a"
    )
    
    from topology.schema import NetworkResource, ResourceMetadata
    
    public_subnet = NetworkResource(
        resource_id="subnet-pub123",
        resource_type=ResourceType.SUBNET,
        name="production-public-1a",
        arn="arn:aws:ec2:us-east-1:123456789012:subnet/subnet-pub123",
        location=subnet_location,
        metadata=ResourceMetadata(
            discovered_at=datetime.now(),
            last_updated=datetime.now(),
            tags={"Name": "production-public-1a", "Type": "public"}
        ),
        cidr_blocks=["10.0.1.0/24"],
        properties={
            "vpc_id": "vpc-prod123",
            "availability_zone": "us-east-1a",
            "map_public_ip_on_launch": True
        }
    )
    us_east_1.add_resource(public_subnet)
    
    # Create private subnet
    private_subnet = NetworkResource(
        resource_id="subnet-priv123",
        resource_type=ResourceType.SUBNET,
        name="production-private-1a",
        arn="arn:aws:ec2:us-east-1:123456789012:subnet/subnet-priv123",
        location=subnet_location,
        metadata=ResourceMetadata(
            discovered_at=datetime.now(),
            last_updated=datetime.now(),
            tags={"Name": "production-private-1a", "Type": "private"}
        ),
        cidr_blocks=["10.0.2.0/24"],
        properties={
            "vpc_id": "vpc-prod123",
            "availability_zone": "us-east-1a",
            "map_public_ip_on_launch": False
        }
    )
    us_east_1.add_resource(private_subnet)
    
    # Create EC2 instance
    ec2_instance = create_ec2_instance_resource(
        instance_id="i-webserver123",
        instance_type="t3.medium",
        location=subnet_location,
        name="web-server-01"
    )
    ec2_instance.metadata.tags = {"Name": "web-server-01", "Role": "webserver"}
    ec2_instance.properties = {
        "subnet_id": "subnet-pub123",
        "vpc_id": "vpc-prod123",
        "security_groups": ["sg-web123"]
    }
    us_east_1.add_resource(ec2_instance)
    
    # Add relationships
    vpc_subnet_rel = Relationship(
        source_id="vpc-prod123",
        target_id="subnet-pub123",
        relationship_type=RelationshipType.CONTAINS
    )
    us_east_1.relationships.append(vpc_subnet_rel)
    
    vpc_subnet_rel2 = Relationship(
        source_id="vpc-prod123",
        target_id="subnet-priv123",
        relationship_type=RelationshipType.CONTAINS
    )
    us_east_1.relationships.append(vpc_subnet_rel2)
    
    subnet_instance_rel = Relationship(
        source_id="subnet-pub123",
        target_id="i-webserver123",
        relationship_type=RelationshipType.CONTAINS
    )
    us_east_1.relationships.append(subnet_instance_rel)
    
    return topology


def test_complete_workflow():
    """Test the complete workflow from topology to diagram generation."""
    
    # Create test topology
    topology = create_test_topology()
    
    # Test topology statistics
    stats = topology.get_statistics()
    assert stats["total_accounts"] == 1
    assert stats["total_regions"] == 1
    assert stats["total_resources"] == 4  # VPC, 2 subnets, 1 instance
    
    # Test serialization roundtrip
    serializer = TopologyYAMLSerializer()
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.yaml', delete=False) as f:
        temp_path = Path(f.name)
    
    try:
        # Save and reload topology
        serializer.save_to_file(topology, temp_path)
        reloaded_topology = serializer.load_from_file(temp_path)
        
        # Verify reloaded topology
        reloaded_stats = reloaded_topology.get_statistics()
        assert reloaded_stats["total_resources"] == stats["total_resources"]
        
        # Test view engine
        view_engine = ViewEngine(reloaded_topology)
        
        # Create single VPC view
        vpc_view = view_engine.create_single_vpc_view("vpc-prod123")
        
        
        # Verify view contents - should include VPC + subnets + instance
        expected_resources = 4  # VPC + 2 subnets + 1 instance
        assert len(vpc_view.filtered_resources) == expected_resources
        
        # Relationships are filtered to only include those between filtered resources
        # Should have at least the VPC->subnet relationships
        assert len(vpc_view.filtered_relationships) >= 1
        
        # Test view statistics
        view_stats = vpc_view.get_statistics()
        assert view_stats["total_resources"] == expected_resources
        assert view_stats["unique_accounts"] == 1
        assert view_stats["unique_regions"] == 1
        
        # Test resource grouping
        groups = vpc_view.get_resource_groups("resource_type")
        assert "vpc" in groups
        assert "subnet" in groups
        assert "ec2_instance" in groups
        assert len(groups["subnet"]) == 2
        
        # Test AWS Labs transformer
        transformer = AWSLabsTransformer(vpc_view)
        diagram_data = transformer.transform()
        
        # Verify diagram structure
        assert "Diagram" in diagram_data
        diagram = diagram_data["Diagram"]
        
        assert "Title" in diagram
        assert "Resources" in diagram
        assert "Connections" in diagram
        assert "Groups" in diagram
        assert "Metadata" in diagram
        
        # Verify resources in diagram
        assert len(diagram["Resources"]) == 4
        assert "vpc-prod123" in diagram["Resources"]
        assert "subnet-pub123" in diagram["Resources"]
        assert "i-webserver123" in diagram["Resources"]
        
        # Verify resource properties
        vpc_resource = diagram["Resources"]["vpc-prod123"]
        assert vpc_resource["Type"] == "AWS::EC2::VPC"
        assert "CidrBlock" in vpc_resource["Properties"]
        assert vpc_resource["Properties"]["CidrBlock"] == "10.0.0.0/16"
        
        # Verify connections
        assert len(diagram["Connections"]) == 3
        
        # Verify groups
        assert len(diagram["Groups"]) > 0
        
        # Test saving diagram to file
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.yaml', delete=False) as f:
            diagram_path = Path(f.name)
        
        try:
            transformer.save_to_file(str(diagram_path))
            
            # Verify saved file
            with open(diagram_path, 'r') as f:
                saved_diagram = yaml.safe_load(f)
            
            assert "Diagram" in saved_diagram
            assert len(saved_diagram["Diagram"]["Resources"]) == 4
            
        finally:
            diagram_path.unlink()
        
    finally:
        temp_path.unlink()


def test_view_filtering():
    """Test various view filtering scenarios."""
    
    topology = create_test_topology()
    view_engine = ViewEngine(topology)
    
    # Test filtering by resource type
    subnet_filter = ViewFilter(
        filter_type=FilterType.RESOURCE_TYPE,
        values=["subnet"],
        include=True
    )
    
    view_def = ViewDefinition(
        name="Subnets Only",
        description="Only subnet resources",
        filters=[subnet_filter]
    )
    
    subnet_view = view_engine.create_view(view_def)
    assert len(subnet_view.filtered_resources) == 2
    
    for resource in subnet_view.filtered_resources.values():
        assert resource.resource_type == ResourceType.SUBNET
    
    # Test filtering by tags
    tag_filter = ViewFilter(
        filter_type=FilterType.TAG,
        values=["Type=public"],
        include=True
    )
    
    tag_view_def = ViewDefinition(
        name="Public Resources",
        description="Resources tagged as public",
        filters=[tag_filter]
    )
    
    tag_view = view_engine.create_view(tag_view_def)
    assert len(tag_view.filtered_resources) == 1  # Only public subnet
    
    # Test filtering by availability zone
    az_filter = ViewFilter(
        filter_type=FilterType.AVAILABILITY_ZONE,
        values=["us-east-1a"],
        include=True
    )
    
    az_view_def = ViewDefinition(
        name="AZ 1a Resources",
        description="Resources in us-east-1a",
        filters=[az_filter]
    )
    
    az_view = view_engine.create_view(az_view_def)
    # Should include subnets and instance in us-east-1a
    assert len(az_view.filtered_resources) == 3


def test_cross_account_scenario():
    """Test a more complex cross-account scenario."""
    
    metadata = TopologyMetadata(
        generated_at=datetime.now(),
        generator_version="0.1.0",
        last_updated=datetime.now()
    )
    
    organization = OrganizationData(
        organization_id="o-test123",
        management_account_id="123456789012"
    )
    
    topology = AWSTopology(
        metadata=metadata,
        organization=organization
    )
    
    # Add production account
    prod_account = topology.organization.add_account("123456789012", "production")
    prod_region = prod_account.add_region("us-east-1")
    
    # Add development account
    dev_account = topology.organization.add_account("234567890123", "development")
    dev_region = dev_account.add_region("us-east-1")
    
    # Create VPCs in each account
    prod_vpc = create_vpc_resource(
        vpc_id="vpc-prod123",
        cidr_block="10.0.0.0/16",
        location=ResourceLocation("123456789012", "us-east-1"),
        name="production-vpc"
    )
    prod_region.add_resource(prod_vpc)
    
    dev_vpc = create_vpc_resource(
        vpc_id="vpc-dev123",
        cidr_block="10.1.0.0/16",
        location=ResourceLocation("234567890123", "us-east-1"),
        name="development-vpc"
    )
    dev_region.add_resource(dev_vpc)
    
    # Test cross-account connectivity view
    view_engine = ViewEngine(topology)
    cross_account_view = view_engine.create_cross_account_connectivity_view()
    
    # Should include both VPCs
    assert len(cross_account_view.filtered_resources) == 2
    
    view_stats = cross_account_view.get_statistics()
    assert view_stats["unique_accounts"] == 2
    assert view_stats["unique_regions"] == 1
    
    # Test grouping by account
    account_groups = cross_account_view.get_resource_groups("account_id")
    assert "123456789012" in account_groups
    assert "234567890123" in account_groups
    assert len(account_groups["123456789012"]) == 1
    assert len(account_groups["234567890123"]) == 1


if __name__ == "__main__":
    test_complete_workflow()
    print("✓ Complete workflow test passed")
    
    test_view_filtering()
    print("✓ View filtering test passed")
    
    test_cross_account_scenario()
    print("✓ Cross-account scenario test passed")
    
    print("\nAll end-to-end tests passed! 🎉")