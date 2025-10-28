from datetime import datetime, timezone
from typing import Dict, List

from transformers.awslabs_transformer import AWSLabsTransformer
from topology.schema import (
    BaseResource,
    NetworkResource,
    ResourceLocation,
    ResourceMetadata,
    ResourceType,
    Relationship,
    RelationshipType,
    create_ec2_instance_resource,
    create_vpc_resource,
)
from views.view_engine import TopologyView


def _metadata() -> ResourceMetadata:
    now = datetime.now(timezone.utc)
    return ResourceMetadata(discovered_at=now, last_updated=now)


def _location(az: str | None = None) -> ResourceLocation:
    return ResourceLocation(account_id="123456789012", region="us-east-1", availability_zone=az)


def _build_view() -> TopologyView:
    resources: Dict[str, BaseResource] = {}
    relationships: List[Relationship] = []

    vpc = create_vpc_resource(
        vpc_id="vpc-1",
        cidr_block="10.0.0.0/16",
        location=_location(),
        name="primary",
    )
    resources[vpc.resource_id] = vpc

    subnet = NetworkResource(
        resource_id="subnet-1",
        resource_type=ResourceType.SUBNET,
        name="public-az1",
        arn="arn:aws:ec2:us-east-1:123456789012:subnet/subnet-1",
        location=_location("us-east-1a"),
        metadata=_metadata(),
        cidr_blocks=["10.0.1.0/24"],
        properties={"vpc_id": vpc.resource_id, "availability_zone": "us-east-1a"},
    )
    resources[subnet.resource_id] = subnet
    relationships.append(
        Relationship(
            source_id=vpc.resource_id,
            target_id=subnet.resource_id,
            relationship_type=RelationshipType.CONTAINS,
        )
    )

    ec2 = create_ec2_instance_resource(
        instance_id="i-123",
        instance_type="t3.micro",
        location=_location("us-east-1a"),
        name="app-instance",
        subnet_id=subnet.resource_id,
        vpc_id=vpc.resource_id,
        security_group_ids=["sg-1"],
    )
    resources[ec2.resource_id] = ec2

    eni = BaseResource(
        resource_id="eni-1",
        resource_type=ResourceType.NETWORK_INTERFACE,
        name="eni-for-app",
        arn="arn:aws:ec2:us-east-1:123456789012:network-interface/eni-1",
        location=_location("us-east-1a"),
        metadata=_metadata(),
        properties={
            "private_ip": "10.0.1.10",
            "subnet_id": subnet.resource_id,
            "attachment": {"InstanceId": ec2.resource_id},
        },
    )
    resources[eni.resource_id] = eni
    relationships.append(
        Relationship(
            source_id=subnet.resource_id,
            target_id=eni.resource_id,
            relationship_type=RelationshipType.CONTAINS,
        )
    )

    lb = BaseResource(
        resource_id="lb-1",
        resource_type=ResourceType.LOAD_BALANCER,
        name="nlb",
        arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/net/nlb",
        location=_location(),
        metadata=_metadata(),
        properties={
            "type": "network",
            "scheme": "internal",
            "subnet_ids": [subnet.resource_id],
        },
    )
    resources[lb.resource_id] = lb

    tg = BaseResource(
        resource_id="tg-1",
        resource_type=ResourceType.TARGET_GROUP,
        name="nlb-tg",
        arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/nlb-tg",
        location=_location(),
        metadata=_metadata(),
        properties={
            "vpc_id": vpc.resource_id,
            "target_type": "ip",
            "targets": [{"id": "10.0.1.10", "port": 443}],
        },
    )
    resources[tg.resource_id] = tg
    relationships.append(
        Relationship(
            source_id=lb.resource_id,
            target_id=tg.resource_id,
            relationship_type=RelationshipType.CONTAINS,
        )
    )

    vpce = BaseResource(
        resource_id="vpce-1",
        resource_type=ResourceType.VPC_ENDPOINT,
        name="ssm-endpoint",
        arn="arn:aws:ec2:us-east-1:123456789012:vpc-endpoint/vpce-1",
        location=_location(),
        metadata=_metadata(),
        properties={
            "vpc_id": vpc.resource_id,
            "subnet_ids": [subnet.resource_id],
            "service_name": "com.amazonaws.us-east-1.ssm",
            "service_owner": "AWS",
            "network_interface_ids": [eni.resource_id],
        },
    )
    resources[vpce.resource_id] = vpce
    relationships.append(
        Relationship(
            source_id=vpc.resource_id,
            target_id=vpce.resource_id,
            relationship_type=RelationshipType.CONTAINS,
        )
    )

    # Additional AWS-managed VPCE with numeric owner account (CloudWatch Logs)
    vpce_logs = BaseResource(
        resource_id="vpce-logs",
        resource_type=ResourceType.VPC_ENDPOINT,
        name="logs-endpoint",
        arn="arn:aws:ec2:us-east-1:123456789012:vpc-endpoint/vpce-logs",
        location=_location(),
        metadata=_metadata(),
        properties={
            "vpc_id": vpc.resource_id,
            "subnet_ids": [subnet.resource_id],
            "service_name": "com.amazonaws.us-east-1.logs",
            "service_owner": "123456789012",
            "network_interface_ids": ["eni-logs"],
        },
    )
    resources[vpce_logs.resource_id] = vpce_logs
    relationships.append(
        Relationship(
            source_id=vpc.resource_id,
            target_id=vpce_logs.resource_id,
            relationship_type=RelationshipType.CONTAINS,
        )
    )

    eni_logs = BaseResource(
        resource_id="eni-logs",
        resource_type=ResourceType.NETWORK_INTERFACE,
        name="eni-for-logs",
        arn="arn:aws:ec2:us-east-1:123456789012:network-interface/eni-logs",
        location=_location("us-east-1a"),
        metadata=_metadata(),
        properties={
            "private_ip": "10.0.1.11",
            "subnet_id": subnet.resource_id,
        },
    )
    resources[eni_logs.resource_id] = eni_logs
    relationships.append(
        Relationship(
            source_id=subnet.resource_id,
            target_id=eni_logs.resource_id,
            relationship_type=RelationshipType.CONTAINS,
        )
    )

    view_metadata = {
        "filters_applied": [
            {"type": "vpc", "values": [vpc.resource_id]},
        ]
    }

    return TopologyView(
        name="test-view",
        description="",
        source_topology=None,
        filtered_resources=resources,
        filtered_relationships=relationships,
        metadata=view_metadata,
    )


def test_nlb_ip_targets_promote_ec2_instances_and_render_service_icons():
    view = _build_view()
    transformer = AWSLabsTransformer(view)

    diagram = transformer.transform()
    resources = diagram["Diagram"]["Resources"]

    # EC2 instance rendered as standalone resource
    assert "i-123" in resources
    assert resources["i-123"]["Type"] == "AWS::EC2::Instance"

    # NLB IP target promoted to EC2 instance node inside target sections (no raw IP node)
    assert not any(key.startswith("ip-10.0.1.10") for key in resources)
    assert any(key.startswith("ec2-i-123-in-") for key in resources)

    # Interface endpoint ENIs suppressed in favour of service icons
    assert "eni-1" not in resources
    assert "eni-logs" not in resources

    # VPC endpoint associated with AWS service uses service icon/title
    group_node_id = "vpc-1-public-subnets-logical-subnet"
    vpce_node_id = f"vpce-1-in-{group_node_id}"
    assert vpce_node_id in resources
    assert resources[vpce_node_id]["Type"] == "AWS::EC2"
    assert "Systems Manager" in resources[vpce_node_id]["Title"]

    # Numeric-owned AWS service endpoint also maps to service icon (CloudWatch Logs)
    vpce_logs_node_id = f"vpce-logs-in-{group_node_id}"
    assert vpce_logs_node_id in resources
    assert resources[vpce_logs_node_id]["Type"] == "AWS::CloudWatch"
    assert "Logs" in resources[vpce_logs_node_id]["Title"]
