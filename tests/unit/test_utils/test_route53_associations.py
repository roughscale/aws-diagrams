from datetime import datetime

from topology.schema import (
    AWSTopology,
    BaseResource,
    OrganizationData,
    ResourceLocation,
    ResourceMetadata,
    ResourceType,
    TopologyMetadata,
    RelationshipType,
)
from utils.route53_associations import GLOBAL_REGION, associate_route53_records


def _base_metadata():
    now = datetime.now()
    return ResourceMetadata(discovered_at=now, last_updated=now)


def test_associate_route53_records_links_to_load_balancer():
    metadata = TopologyMetadata(
        generated_at=datetime.now(),
        generator_version="test",
        last_updated=datetime.now(),
    )
    topology = AWSTopology(
        metadata=metadata,
        organization=OrganizationData(
            organization_id=None,
            management_account_id="123456789012",
        ),
    )

    account = topology.organization.add_account("123456789012")
    region = account.add_region("us-east-1")
    lb_resource = BaseResource(
        resource_id="arn:aws:elbv2:us-east-1:123456789012:loadbalancer/app/test/123456",
        resource_type=ResourceType.LOAD_BALANCER,
        name="test",
        arn="arn:aws:elbv2:us-east-1:123456789012:loadbalancer/app/test/123456",
        location=ResourceLocation(account_id="123456789012", region="us-east-1"),
        metadata=_base_metadata(),
        properties={"dns_name": "my-test-lb.elb.amazonaws.com"},
    )
    region.add_resource(lb_resource)

    global_region = account.add_region(GLOBAL_REGION)
    record_resource = BaseResource(
        resource_id="Z123:app.example.com.:A",
        resource_type=ResourceType.ROUTE53_RECORD,
        name="app.example.com.",
        arn="arn:aws:route53:::hostedzone/Z123/recordset/app.example.com.:A",
        location=ResourceLocation(
            account_id="123456789012",
            region=GLOBAL_REGION,
        ),
        metadata=_base_metadata(),
        properties={"target_dns_names": ["my-test-lb.elb.amazonaws.com."]},
    )
    global_region.add_resource(record_resource)

    created = associate_route53_records(topology)

    assert created == 1
    assert record_resource.properties["associated_resource_ids"] == [
        lb_resource.resource_id
    ]
    assert any(
        rel.source_id == record_resource.resource_id
        and rel.target_id == lb_resource.resource_id
        and rel.relationship_type == RelationshipType.CONNECTS_TO
        for rel in global_region.relationships
    )
