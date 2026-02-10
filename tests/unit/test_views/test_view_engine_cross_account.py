from datetime import datetime

from topology.schema import (
    AWSTopology,
    OrganizationData,
    AccountData,
    RegionData,
    BaseResource,
    ResourceLocation,
    ResourceMetadata,
    ResourceType,
    Relationship,
    RelationshipType,
    TopologyMetadata,
)
from views import ViewEngine


def _metadata():
    return ResourceMetadata(discovered_at=datetime.now(), last_updated=datetime.now())


def _create_resource(resource_id: str, rtype: ResourceType, account: str, region: str, **props):
    return BaseResource(
        resource_id=resource_id,
        resource_type=rtype,
        name=props.pop("name", resource_id),
        arn=f"arn:aws:test:{region}:{account}:{rtype.value}/{resource_id}",
        location=ResourceLocation(account_id=account, region=region),
        metadata=_metadata(),
        properties=props,
    )


def test_single_vpc_view_includes_cross_account_member_of_targets():
    network_account = AccountData(account_id="111111111111", account_name="network")
    app_account = AccountData(account_id="222222222222", account_name="app")

    vpc = _create_resource("vpc-1", ResourceType.VPC, "111111111111", "ap-southeast-2")
    eni = _create_resource(
        "eni-1",
        ResourceType.NETWORK_INTERFACE,
        "111111111111",
        "ap-southeast-2",
        vpc_id="vpc-1",
    )

    network_region = RegionData(region="ap-southeast-2")
    network_region.resources[vpc.resource_id] = vpc
    network_region.resources[eni.resource_id] = eni
    network_account.regions[network_region.region] = network_region

    ecs_service = _create_resource(
        "arn:aws:ecs:ap-southeast-2:222222222222:service/demo",
        ResourceType.ECS_SERVICE,
        "222222222222",
        "ap-southeast-2",
    )
    app_region = RegionData(region="ap-southeast-2")
    app_region.resources[ecs_service.resource_id] = ecs_service
    app_region.relationships.append(
        Relationship(
            source_id=eni.resource_id,
            target_id=ecs_service.resource_id,
            relationship_type=RelationshipType.MEMBER_OF,
        )
    )
    app_account.regions[app_region.region] = app_region

    org = OrganizationData(organization_id=None, management_account_id="111111111111")
    org.accounts[network_account.account_id] = network_account
    org.accounts[app_account.account_id] = app_account

    topology = AWSTopology(
        metadata=TopologyMetadata(
            generated_at=datetime.now(),
            generator_version="test",
            last_updated=datetime.now(),
        ),
        organization=org,
    )

    view = ViewEngine(topology).create_single_vpc_view(
        vpc_id="vpc-1",
        account_id="111111111111",
        region="ap-southeast-2",
    )

    assert ecs_service.resource_id in view.filtered_resources
