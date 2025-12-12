from __future__ import annotations

from datetime import datetime, timezone

from cli.main import _merge_account_data
from topology.schema import (
    AWSTopology,
    CrossAccountRelationship,
    NetworkResource,
    Relationship,
    RelationshipType,
    ResourceLocation,
    ResourceMetadata,
    ResourceType,
    TopologyMetadata,
    OrganizationData,
    create_vpc_resource,
)


def _metadata() -> ResourceMetadata:
    now = datetime.now(timezone.utc)
    return ResourceMetadata(discovered_at=now, last_updated=now)


def _location(region: str = "us-east-1") -> ResourceLocation:
    return ResourceLocation(account_id="123456789012", region=region, availability_zone=f"{region}a")


def _new_topology() -> AWSTopology:
    now = datetime.now(timezone.utc)
    return AWSTopology(
        metadata=TopologyMetadata(
            generated_at=now,
            generator_version="test",
            last_updated=now,
        ),
        organization=OrganizationData(organization_id=None, management_account_id="111111111111"),
    )


def test_merge_account_data_replaces_only_target_vpcs():
    existing = _new_topology()
    existing_account = existing.organization.add_account("123456789012")
    existing_region = existing_account.add_region("us-east-1")

    vpc_one_old = create_vpc_resource(
        vpc_id="vpc-1",
        cidr_block="10.0.0.0/16",
        location=_location(),
        name="old-vpc-one",
    )
    vpc_two = create_vpc_resource(
        vpc_id="vpc-2",
        cidr_block="10.0.1.0/16",
        location=_location(),
        name="vpc-two",
    )
    subnet_two = NetworkResource(
        resource_id="subnet-2",
        resource_type=ResourceType.SUBNET,
        name="subnet-two",
        arn="arn:aws:ec2:us-east-1:123456789012:subnet/subnet-2",
        location=_location(),
        metadata=_metadata(),
        cidr_blocks=["10.0.1.0/24"],
        properties={"vpc_id": "vpc-2"},
    )

    existing_region.add_resource(vpc_one_old)
    existing_region.add_resource(vpc_two)
    existing_region.add_resource(subnet_two)
    existing_region.relationships.append(
        Relationship(source_id="vpc-2", target_id="subnet-2", relationship_type=RelationshipType.CONTAINS)
    )
    existing_account.cross_region_relationships.append(
        Relationship(source_id="vpc-1", target_id="subnet-x", relationship_type=RelationshipType.CONNECTS_TO)
    )
    existing.organization.cross_account_relationships.append(
        CrossAccountRelationship(
            relationship_type=RelationshipType.CONNECTS_TO,
            source_account="123456789012",
            source_resource="vpc-1",
            target_account="999999999999",
            target_resource="other",
        )
    )

    new = _new_topology()
    new_account = new.organization.add_account("123456789012")
    new_region = new_account.add_region("us-east-1")
    vpc_one_new = create_vpc_resource(
        vpc_id="vpc-1",
        cidr_block="10.0.0.0/16",
        location=_location(),
        name="new-vpc-one",
    )
    subnet_one = NetworkResource(
        resource_id="subnet-1",
        resource_type=ResourceType.SUBNET,
        name="subnet-one",
        arn="arn:aws:ec2:us-east-1:123456789012:subnet/subnet-1",
        location=_location(),
        metadata=_metadata(),
        cidr_blocks=["10.0.0.0/24"],
        properties={"vpc_id": "vpc-1"},
    )
    new_region.add_resource(vpc_one_new)
    new_region.add_resource(subnet_one)
    new_region.relationships.append(
        Relationship(source_id="vpc-1", target_id="subnet-1", relationship_type=RelationshipType.CONTAINS)
    )
    new_account.cross_region_relationships.append(
        Relationship(source_id="vpc-1", target_id="subnet-y", relationship_type=RelationshipType.CONNECTS_TO)
    )
    new.organization.cross_account_relationships.append(
        CrossAccountRelationship(
            relationship_type=RelationshipType.CONNECTS_TO,
            source_account="123456789012",
            source_resource="vpc-1",
            target_account="888888888888",
            target_resource="updated",
        )
    )

    _merge_account_data(existing, new, "123456789012", {"vpc-1"})

    merged_region = existing.organization.accounts["123456789012"].regions["us-east-1"]
    assert merged_region.resources["vpc-1"].name == "new-vpc-one"
    assert "vpc-2" in merged_region.resources
    assert merged_region.resources["subnet-2"].name == "subnet-two"
    assert any(rel.target_id == "subnet-1" for rel in merged_region.relationships)
    assert all(rel.target_id != "subnet-2" for rel in merged_region.relationships if rel.source_id == "vpc-1")

    account = existing.organization.accounts["123456789012"]
    assert all(
        rel.source_id != "vpc-1" for rel in account.cross_region_relationships if rel.target_id == "subnet-x"
    )
    assert any(rel.target_id == "subnet-y" for rel in account.cross_region_relationships)

    assert all(
        rel.source_resource != "vpc-1" or rel.target_resource != "other"
        for rel in existing.organization.cross_account_relationships
    )
    assert any(
        rel.source_resource == "vpc-1" and rel.target_resource == "updated"
        for rel in existing.organization.cross_account_relationships
    )


def test_merge_account_data_replace_all_when_no_filter():
    existing = _new_topology()
    existing_account = existing.organization.add_account("123456789012")
    existing_region = existing_account.add_region("us-west-2")
    existing_region.add_resource(
        create_vpc_resource(
            vpc_id="vpc-old",
            cidr_block="10.0.0.0/16",
            location=_location("us-west-2"),
            name="old",
        )
    )

    new = _new_topology()
    new_account = new.organization.add_account("123456789012")
    new_region = new_account.add_region("us-west-2")
    new_region.add_resource(
        create_vpc_resource(
            vpc_id="vpc-new",
            cidr_block="10.0.0.0/16",
            location=_location("us-west-2"),
            name="new",
        )
    )

    _merge_account_data(existing, new, "123456789012", set())

    merged_region = existing.organization.accounts["123456789012"].regions["us-west-2"]
    assert set(merged_region.resources.keys()) == {"vpc-new"}
