"""
Basic functionality tests for AWS topology discovery.

This module contains simple tests to verify that the core components
work correctly together.
"""

import pytest
import sys
from pathlib import Path
from datetime import datetime

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from topology.schema import (
    AWSTopology, TopologyMetadata, OrganizationData, 
    ResourceType, create_vpc_resource, ResourceLocation
)
from topology.serializer import TopologyYAMLSerializer


def test_create_basic_topology():
    """Test creating a basic topology structure."""
    
    # Create metadata
    metadata = TopologyMetadata(
        generated_at=datetime.now(),
        generator_version="0.1.0",
        last_updated=datetime.now()
    )
    
    # Create organization
    organization = OrganizationData(
        organization_id="o-test123",
        management_account_id="123456789012"
    )
    
    # Create topology
    topology = AWSTopology(
        metadata=metadata,
        organization=organization
    )
    
    assert topology.metadata.generator_version == "0.1.0"
    assert topology.organization.management_account_id == "123456789012"
    assert len(topology.organization.accounts) == 0


def test_add_account_and_resources():
    """Test adding accounts and resources to topology."""
    
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
    
    # Add account
    account = topology.organization.add_account("123456789012", "test-account")
    assert account.account_id == "123456789012"
    assert account.account_name == "test-account"
    
    # Add region
    region = account.add_region("us-east-1")
    assert region.region == "us-east-1"
    
    # Create and add VPC resource
    location = ResourceLocation(
        account_id="123456789012",
        region="us-east-1"
    )
    
    vpc_resource = create_vpc_resource(
        vpc_id="vpc-test123",
        cidr_block="10.0.0.0/16",
        location=location,
        name="test-vpc"
    )
    
    region.add_resource(vpc_resource)
    
    # Verify resource was added
    assert len(region.resources) == 1
    assert "vpc-test123" in region.resources
    assert region.resources["vpc-test123"].name == "test-vpc"
    
    # Test topology statistics
    stats = topology.get_statistics()
    assert stats["total_accounts"] == 1
    assert stats["total_regions"] == 1
    assert stats["total_resources"] == 1
    assert stats["resource_counts"]["vpc"] == 1


def test_serialization_roundtrip():
    """Test that topology can be serialized and deserialized correctly."""
    
    # Create a simple topology
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
    
    # Add some test data
    account = topology.organization.add_account("123456789012", "test-account")
    region = account.add_region("us-east-1")
    
    location = ResourceLocation(
        account_id="123456789012",
        region="us-east-1"
    )
    
    vpc_resource = create_vpc_resource(
        vpc_id="vpc-test123",
        cidr_block="10.0.0.0/16",
        location=location,
        name="test-vpc"
    )
    
    region.add_resource(vpc_resource)
    
    # Serialize and deserialize
    serializer = TopologyYAMLSerializer()
    serialized_data = serializer.serialize_topology(topology)
    
    # Verify the serialized structure
    assert "aws_topology" in serialized_data
    assert "metadata" in serialized_data["aws_topology"]
    assert "organization" in serialized_data["aws_topology"]
    
    # Deserialize back
    deserialized_topology = serializer.deserialize_topology(serialized_data)
    
    # Verify the deserialized topology
    assert deserialized_topology.metadata.generator_version == "0.1.0"
    assert deserialized_topology.organization.management_account_id == "123456789012"
    assert len(deserialized_topology.organization.accounts) == 1
    
    # Check the VPC resource
    account_data = deserialized_topology.organization.accounts["123456789012"]
    region_data = account_data.regions["us-east-1"]
    vpc = region_data.resources["vpc-test123"]
    
    assert vpc.name == "test-vpc"
    assert vpc.resource_type == ResourceType.VPC
    assert "10.0.0.0/16" in vpc.cidr_blocks


def test_example_topology_file():
    """Test loading the example topology file."""
    
    example_file = Path(__file__).parent.parent / "schemas" / "examples" / "single-vpc-topology.yaml"
    
    if example_file.exists():
        serializer = TopologyYAMLSerializer()
        topology = serializer.load_from_file(example_file)
        
        # Verify the loaded topology
        assert topology.metadata.generator_version == "0.1.0"
        assert topology.organization.management_account_id == "123456789012"
        
        # Check statistics
        stats = topology.get_statistics()
        assert stats["total_accounts"] == 1
        assert stats["total_regions"] == 1
        assert stats["total_resources"] > 0


if __name__ == "__main__":
    # Run basic tests
    test_create_basic_topology()
    print("✓ Basic topology creation test passed")
    
    test_add_account_and_resources()
    print("✓ Account and resource addition test passed")
    
    test_serialization_roundtrip()
    print("✓ Serialization roundtrip test passed")
    
    test_example_topology_file()
    print("✓ Example topology file test passed")
    
    print("\nAll basic functionality tests passed! 🎉")