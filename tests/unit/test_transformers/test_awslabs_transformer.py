from datetime import datetime, timezone
from typing import Dict, List

import pytest

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


pytestmark = pytest.mark.legacy


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


def _build_shared_cluster_view() -> TopologyView:
    resources: Dict[str, BaseResource] = {}
    relationships: List[Relationship] = []

    vpc = create_vpc_resource(
        vpc_id="vpc-shared",
        cidr_block="10.50.0.0/16",
        location=_location(),
        name="shared-vpc",
    )
    resources[vpc.resource_id] = vpc

    public_subnet = NetworkResource(
        resource_id="subnet-public",
        resource_type=ResourceType.SUBNET,
        name="public-subnet-a",
        arn="arn:aws:ec2:us-east-1:123456789012:subnet/subnet-public",
        location=_location("us-east-1a"),
        metadata=_metadata(),
        cidr_blocks=["10.50.0.0/24"],
        properties={"vpc_id": vpc.resource_id, "availability_zone": "us-east-1a"},
    )
    private_subnet = NetworkResource(
        resource_id="subnet-private",
        resource_type=ResourceType.SUBNET,
        name="private-subnet-a",
        arn="arn:aws:ec2:us-east-1:123456789012:subnet/subnet-private",
        location=_location("us-east-1a"),
        metadata=_metadata(),
        cidr_blocks=["10.50.1.0/24"],
        properties={"vpc_id": vpc.resource_id, "availability_zone": "us-east-1a"},
    )
    resources[public_subnet.resource_id] = public_subnet
    resources[private_subnet.resource_id] = private_subnet
    relationships.extend(
        [
            Relationship(
                source_id=vpc.resource_id,
                target_id=public_subnet.resource_id,
                relationship_type=RelationshipType.CONTAINS,
            ),
            Relationship(
                source_id=vpc.resource_id,
                target_id=private_subnet.resource_id,
                relationship_type=RelationshipType.CONTAINS,
            ),
        ]
    )

    lb = BaseResource(
        resource_id="lb-shared",
        resource_type=ResourceType.LOAD_BALANCER,
        name="shared-alb",
        arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/shared/alb",
        location=_location(),
        metadata=_metadata(),
        properties={
            "type": "application",
            "scheme": "internal",
            "subnet_ids": [public_subnet.resource_id],
        },
    )
    resources[lb.resource_id] = lb

    tg_a = BaseResource(
        resource_id="tg-a",
        resource_type=ResourceType.TARGET_GROUP,
        name="alb-tg-a",
        arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/alb-tg-a/0000000000000001",
        location=_location(),
        metadata=_metadata(),
        properties={
            "vpc_id": vpc.resource_id,
            "target_type": "instance",
            "targets": [],
        },
    )
    tg_b = BaseResource(
        resource_id="tg-b",
        resource_type=ResourceType.TARGET_GROUP,
        name="alb-tg-b",
        arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/alb-tg-b/0000000000000002",
        location=_location(),
        metadata=_metadata(),
        properties={
            "vpc_id": vpc.resource_id,
            "target_type": "instance",
            "targets": [],
        },
    )
    resources[tg_a.resource_id] = tg_a
    resources[tg_b.resource_id] = tg_b
    relationships.extend(
        [
            Relationship(
                source_id=lb.resource_id,
                target_id=tg_a.resource_id,
                relationship_type=RelationshipType.CONTAINS,
            ),
            Relationship(
                source_id=lb.resource_id,
                target_id=tg_b.resource_id,
                relationship_type=RelationshipType.CONTAINS,
            ),
        ]
    )

    cluster = BaseResource(
        resource_id="arn:aws:ecs:us-east-1:123456789012:cluster/shared-cluster",
        resource_type=ResourceType.ECS_CLUSTER,
        name="shared-cluster",
        arn="arn:aws:ecs:us-east-1:123456789012:cluster/shared-cluster",
        location=_location(),
        metadata=_metadata(),
        properties={},
    )
    resources[cluster.resource_id] = cluster

    shared_service = BaseResource(
        resource_id="arn:aws:ecs:us-east-1:123456789012:service/shared-cluster/shared-service",
        resource_type=ResourceType.ECS_SERVICE,
        name="shared-service",
        arn="arn:aws:ecs:us-east-1:123456789012:service/shared-cluster/shared-service",
        location=_location(),
        metadata=_metadata(),
        properties={
            "clusterArn": cluster.resource_id,
            "subnet_ids": [private_subnet.resource_id],
            "security_group_ids": ["sg-service"],
            "target_group_arns": [tg_a.resource_id, tg_b.resource_id],
        },
    )
    resources[shared_service.resource_id] = shared_service

    view_metadata = {
        "filters_applied": [],
        "source_accounts": ["123456789012"],
        "source_regions": ["us-east-1"],
    }

    return TopologyView(
        name="shared-cluster-view",
        description="",
        source_topology=None,
        filtered_resources=resources,
        filtered_relationships=relationships,
        metadata=view_metadata,
    )


def test_awslabs_transformer_shared_cluster_targets_render_badges():
    view = _build_shared_cluster_view()
    transformer = AWSLabsTransformer(view)
    diagram = transformer.transform()

    resources = diagram["Diagram"]["Resources"]

    cluster_nodes = {
        rid
        for rid, res in resources.items()
        if res.get("Type") == "AWS::ECS::Cluster" and res.get("Title") == "shared-cluster"
    }
    assert cluster_nodes, "Expected cluster node to exist"

    lb_stack_id = next(
        rid
        for rid, res in resources.items()
        if res.get("Type") == "AWS::Diagram::VerticalStack" and res.get("Title") == "shared-alb"
    )
    def _descendants(node_id: str) -> List[str]:
        children = resources.get(node_id, {}).get("Children") or []
        nested = []
        for child in children:
            nested.append(child)
            nested.extend(_descendants(child))
        return nested

    lb_children = resources[lb_stack_id].get("Children", []) or []
    assert not cluster_nodes.intersection(lb_children)

    descendant_ids = _descendants(lb_stack_id)
    badge_ids = [cid for cid in descendant_ids if cid.startswith("lb-cluster-badge-")]
    assert len(badge_ids) == 2
    for badge_id in badge_ids:
        badge = resources[badge_id]
        assert badge.get("Type") == "AWS::ECS::Cluster"
        assert badge.get("Children") in (None, [])
        assert "Cluster: shared-cluster" in badge.get("Title", "")


def _build_complex_lb_view(simple: bool = False) -> TopologyView:
    resources: Dict[str, BaseResource] = {}
    relationships: List[Relationship] = []

    vpc = create_vpc_resource(
        vpc_id="vpc-ip",
        cidr_block="10.2.0.0/16",
        location=_location(),
        name="ip-vpc",
    )
    resources[vpc.resource_id] = vpc

    subnet = NetworkResource(
        resource_id="subnet-ip-public",
        resource_type=ResourceType.SUBNET,
        name="public-ip-subnet-a",
        arn="arn:aws:ec2:us-east-1:123456789012:subnet/subnet-ip-public",
        location=_location("us-east-1a"),
        metadata=_metadata(),
        cidr_blocks=["10.2.0.0/24"],
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

    lb = BaseResource(
        resource_id="lb-ip",
        resource_type=ResourceType.LOAD_BALANCER,
        name="shared-ip-alb",
        arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/ip/shared",
        location=_location(),
        metadata=_metadata(),
        properties={
            "type": "application",
            "scheme": "internal",
            "subnet_ids": [subnet.resource_id],
        },
    )
    resources[lb.resource_id] = lb

    tg_one = BaseResource(
        resource_id="tg-ip-1",
        resource_type=ResourceType.TARGET_GROUP,
        name="ip-tg-one",
        arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/ip-tg-one/0000000000000001",
        location=_location(),
        metadata=_metadata(),
        properties={
            "vpc_id": vpc.resource_id,
            "target_type": "ip",
            "targets": [{"id": "10.99.0.10", "port": 443}],
        },
    )
    backend_lb = BaseResource(
        resource_id="arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/backend/lb",
        resource_type=ResourceType.LOAD_BALANCER,
        name="backend-alb",
        arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/backend/lb",
        location=_location(),
        metadata=_metadata(),
        properties={
            "type": "application",
            "scheme": "internal",
            "subnet_ids": [subnet.resource_id],
        },
    )
    resources[backend_lb.resource_id] = backend_lb

    tg_two = BaseResource(
        resource_id="tg-ip-2",
        resource_type=ResourceType.TARGET_GROUP,
        name="ip-tg-two",
        arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/ip-tg-two/0000000000000002",
        location=_location(),
        metadata=_metadata(),
        properties={
            "vpc_id": vpc.resource_id,
            "target_type": "ip",
            "targets": [
                {"id": "10.99.0.10", "port": 443},
                {"id": backend_lb.resource_id, "port": 443},
            ],
        },
    )
    resources[tg_one.resource_id] = tg_one
    resources[tg_two.resource_id] = tg_two
    relationships.extend(
        [
            Relationship(
                source_id=lb.resource_id,
                target_id=tg_one.resource_id,
                relationship_type=RelationshipType.CONTAINS,
            ),
            Relationship(
                source_id=lb.resource_id,
                target_id=tg_two.resource_id,
                relationship_type=RelationshipType.CONTAINS,
            ),
        ]
    )

    if not simple:
        extra_lb = BaseResource(
            resource_id="lb-extra",
            resource_type=ResourceType.LOAD_BALANCER,
            name="shared-extra",
            arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/extra/shared",
            location=_location(),
            metadata=_metadata(),
            properties={
                "type": "application",
                "scheme": "internal",
                "subnet_ids": [subnet.resource_id],
                "security_group_ids": ["sg-extra"],
            },
        )
        resources[extra_lb.resource_id] = extra_lb

        sg_extra = BaseResource(
            resource_id="sg-extra",
            resource_type=ResourceType.SECURITY_GROUP,
            name="sg-extra",
            arn="arn:aws:ec2:us-east-1:123456789012:security-group/sg-extra",
            location=_location(),
            metadata=_metadata(),
            properties={"vpc_id": vpc.resource_id},
        )
        resources[sg_extra.resource_id] = sg_extra

        tg_three = BaseResource(
            resource_id="tg-ip-3",
            resource_type=ResourceType.TARGET_GROUP,
            name="ip-tg-three",
            arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/ip-tg-three/0000000000000003",
            location=_location(),
            metadata=_metadata(),
            properties={
                "vpc_id": vpc.resource_id,
                "target_type": "ip",
                "targets": [{"id": "10.99.0.20", "port": 80}],
            },
        )
        resources[tg_three.resource_id] = tg_three
        relationships.extend(
            [
                Relationship(
                    source_id=extra_lb.resource_id,
                    target_id=tg_three.resource_id,
                    relationship_type=RelationshipType.CONTAINS,
                ),
                Relationship(
                    source_id=lb.resource_id,
                    target_id=extra_lb.resource_id,
                    relationship_type=RelationshipType.CONNECTS_TO,
                ),
            ]
        )

    view_metadata = {
        "filters_applied": [],
        "source_accounts": ["123456789012"],
        "source_regions": ["us-east-1"],
    }

    return TopologyView(
        name="shared-ip-target-view",
        description="",
        source_topology=None,
        filtered_resources=resources,
        filtered_relationships=relationships,
        metadata=view_metadata,
    )


def _build_lb_with_shared_ip_targets_view() -> TopologyView:
    return _build_complex_lb_view(simple=True)


def _build_ec2_aggregation_view() -> TopologyView:
    resources: Dict[str, BaseResource] = {}
    relationships: List[Relationship] = []

    vpc = create_vpc_resource(
        vpc_id="vpc-agg",
        cidr_block="10.50.0.0/16",
        location=_location(),
        name="agg-vpc",
    )
    resources[vpc.resource_id] = vpc

    subnet = NetworkResource(
        resource_id="subnet-agg-private",
        resource_type=ResourceType.SUBNET,
        name="agg-private-a",
        arn="arn:aws:ec2:us-east-1:123456789012:subnet/subnet-agg-private",
        location=_location("us-east-1a"),
        metadata=_metadata(),
        cidr_blocks=["10.50.1.0/24"],
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

    sg_shared = BaseResource(
        resource_id="sg-shared-ec2",
        resource_type=ResourceType.SECURITY_GROUP,
        name="shared-ec2",
        arn="arn:aws:ec2:us-east-1:123456789012:security-group/sg-shared-ec2",
        location=_location(),
        metadata=_metadata(),
        properties={"vpc_id": vpc.resource_id},
    )
    sg_other = BaseResource(
        resource_id="sg-other-ec2",
        resource_type=ResourceType.SECURITY_GROUP,
        name="other-ec2",
        arn="arn:aws:ec2:us-east-1:123456789012:security-group/sg-other-ec2",
        location=_location(),
        metadata=_metadata(),
        properties={"vpc_id": vpc.resource_id},
    )
    resources[sg_shared.resource_id] = sg_shared
    resources[sg_other.resource_id] = sg_other

    ec2_a = create_ec2_instance_resource(
        instance_id="i-shared-a",
        instance_type="t3.micro",
        location=_location("us-east-1a"),
        name="app-a",
        subnet_id=subnet.resource_id,
        vpc_id=vpc.resource_id,
        security_group_ids=[sg_shared.resource_id],
    )
    ec2_b = create_ec2_instance_resource(
        instance_id="i-shared-b",
        instance_type="t3.micro",
        location=_location("us-east-1a"),
        name="app-b",
        subnet_id=subnet.resource_id,
        vpc_id=vpc.resource_id,
        security_group_ids=[sg_shared.resource_id],
    )
    ec2_c = create_ec2_instance_resource(
        instance_id="i-other",
        instance_type="t3.micro",
        location=_location("us-east-1a"),
        name="other-app",
        subnet_id=subnet.resource_id,
        vpc_id=vpc.resource_id,
        security_group_ids=[sg_other.resource_id],
    )
    for ec2 in (ec2_a, ec2_b, ec2_c):
        resources[ec2.resource_id] = ec2
        relationships.append(
            Relationship(
                source_id=subnet.resource_id,
                target_id=ec2.resource_id,
                relationship_type=RelationshipType.CONTAINS,
            )
        )

    view_metadata = {
        "filters_applied": [],
        "source_accounts": ["123456789012"],
        "source_regions": ["us-east-1"],
    }

    return TopologyView(
        name="ec2-aggregation-view",
        description="",
        source_topology=None,
        filtered_resources=resources,
        filtered_relationships=relationships,
        metadata=view_metadata,
    )


def _build_lb_with_alb_target_view() -> TopologyView:
    resources: Dict[str, BaseResource] = {}
    relationships: List[Relationship] = []

    vpc = create_vpc_resource(
        vpc_id="vpc-alb-hop",
        cidr_block="10.20.0.0/16",
        location=_location(),
        name="alb-hop",
    )
    resources[vpc.resource_id] = vpc

    subnet = NetworkResource(
        resource_id="subnet-alb-hop",
        resource_type=ResourceType.SUBNET,
        name="private-hop-a",
        arn="arn:aws:ec2:us-east-1:123456789012:subnet/subnet-alb-hop",
        location=_location("us-east-1a"),
        metadata=_metadata(),
        cidr_blocks=["10.20.0.0/24"],
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

    upstream_lb = BaseResource(
        resource_id="lb-front-alb",
        resource_type=ResourceType.LOAD_BALANCER,
        name="frontdoor-alb",
        arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/frontdoor/alb",
        location=_location(),
        metadata=_metadata(),
        properties={
            "type": "application",
            "scheme": "internal",
            "subnet_ids": [subnet.resource_id],
            "security_group_ids": ["sg-front"],
        },
    )
    resources[upstream_lb.resource_id] = upstream_lb

    downstream_lb = BaseResource(
        resource_id="arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/downstream/alb",
        resource_type=ResourceType.LOAD_BALANCER,
        name="downstream-alb",
        arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/downstream/alb",
        location=_location(),
        metadata=_metadata(),
        properties={
            "type": "application",
            "scheme": "internal",
            "subnet_ids": [subnet.resource_id],
        },
    )
    resources[downstream_lb.resource_id] = downstream_lb

    sg_front = BaseResource(
        resource_id="sg-front",
        resource_type=ResourceType.SECURITY_GROUP,
        name="frontdoor-sg",
        arn="arn:aws:ec2:us-east-1:123456789012:security-group/sg-front",
        location=_location(),
        metadata=_metadata(),
        properties={"vpc_id": vpc.resource_id},
    )
    resources[sg_front.resource_id] = sg_front

    tg = BaseResource(
        resource_id="tg-alb-hop",
        resource_type=ResourceType.TARGET_GROUP,
        name="alb-hop-tg",
        arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/alb-hop/0000000000000004",
        location=_location(),
        metadata=_metadata(),
        properties={
            "vpc_id": vpc.resource_id,
            "target_type": "alb",
            "targets": [
                {"id": downstream_lb.resource_id, "port": 443},
            ],
        },
    )
    resources[tg.resource_id] = tg
    relationships.append(
        Relationship(
            source_id=upstream_lb.resource_id,
            target_id=tg.resource_id,
            relationship_type=RelationshipType.CONTAINS,
        )
    )

    view_metadata = {
        "filters_applied": [],
        "source_accounts": ["123456789012"],
        "source_regions": ["us-east-1"],
    }

    return TopologyView(
        name="alb-hop-view",
        description="",
        source_topology=None,
        filtered_resources=resources,
        filtered_relationships=relationships,
        metadata=view_metadata,
    )


def test_awslabs_transformer_unique_ip_nodes_per_target_group():
    view = _build_lb_with_shared_ip_targets_view()
    transformer = AWSLabsTransformer(view)
    diagram = transformer.transform()

    resources = diagram["Diagram"]["Resources"]

    ip_node_ids = [rid for rid in resources if rid.startswith("lb-ip-target-")]
    assert len(ip_node_ids) == 2
    assert len(set(ip_node_ids)) == 2

    titles = [resources[rid]["Title"] for rid in ip_node_ids]
    assert all("TG:" in title and "LB:" in title for title in titles)

    lb_stack_id = next(
        rid
        for rid, res in resources.items()
        if res.get("Type") == "AWS::Diagram::VerticalStack" and res.get("Title") == "shared-ip-alb"
    )
    def _descendants(node_id: str) -> List[str]:
        children = resources.get(node_id, {}).get("Children") or []
        nested = []
        for child in children:
            nested.append(child)
            nested.extend(_descendants(child))
        return nested

    lb_descendants = set(_descendants(lb_stack_id))
    for node_id in ip_node_ids:
        assert node_id in lb_descendants
