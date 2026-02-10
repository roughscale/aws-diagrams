from datetime import datetime, timezone

from transformers import AWSLabsTransformer, AWSLabsTransformerV2
from topology.schema import (
    BaseResource,
    ResourceLocation,
    ResourceMetadata,
    ResourceType,
    Relationship,
    RelationshipType,
)

from .test_awslabs_transformer import (
    _build_view,
    _build_shared_cluster_view,
    _build_lb_with_shared_ip_targets_view,
    _build_lb_with_alb_target_view,
    _build_ec2_aggregation_view,
    _build_complex_lb_view,
)


def _has_cycle(resources: dict, root: str, seen=None, stack=None) -> bool:
    if seen is None:
        seen = set()
    if stack is None:
        stack = set()
    if root in stack:
        return True
    if root in seen:
        return False
    seen.add(root)
    stack.add(root)
    children = resources.get(root, {}).get("Children", []) or []
    for child in children:
        if child in resources and _has_cycle(resources, child, seen, stack):
            return True
    stack.remove(root)
    return False


def test_awslabs_transformer_v2_preserves_v1_resources():
    view = _build_view()

    v1_diagram = AWSLabsTransformer(view).transform()
    v2_diagram = AWSLabsTransformerV2(view).transform()

    v1_resources = v1_diagram["Diagram"]["Resources"]
    v2_resources = v2_diagram["Diagram"]["Resources"]

    preserved_types = {
        "AWS::Diagram::Canvas",
        "AWS::Diagram::Cloud",
        "AWS::Diagram::HorizontalStack",
        "AWS::EC2::VPC",
    }

    for key, value in v1_resources.items():
        if value.get("Type") == "AWS::Diagram::HorizontalStack" and "logical-subnet-grid-grid-row" in key:
            continue
        if value.get("Type") in preserved_types:
            assert key in v2_resources
            assert v2_resources[key]["Type"] == value["Type"]


def test_awslabs_transformer_v2_aggregates_shared_cluster():
    view = _build_shared_cluster_view()

    v2_diagram = AWSLabsTransformerV2(view).transform()
    resources = v2_diagram["Diagram"]["Resources"]
    links = v2_diagram["Diagram"]["Links"]

    stack_id = next(
        rid
        for rid, res in resources.items()
        if res.get("Type") == "AWS::Diagram::VerticalStack" and res.get("Title") == "shared-alb"
    )
    stack_children = resources[stack_id]["Children"]
    cluster_id = "cluster-shared-cluster-in-vpc-shared-private-subnets-logical-subnet"

    def descendants(node_id: str) -> set[str]:
        kids = resources.get(node_id, {}).get("Children") or []
        nested = set(kids)
        for kid in kids:
            nested |= descendants(kid)
        return nested

    cluster_display = next(
        child_id
        for child_id in stack_children
        if cluster_id in descendants(child_id)
    )
    assert resources[cluster_display]["Type"] == "AWS::Diagram::VerticalStack"
    assert resources[cluster_display].get("Children") == [cluster_id]

    tg_container_nodes = [
        rid
        for rid in resources
        if rid.startswith("sg-") and "-tg-container-" in rid
    ]
    for node_id in tg_container_nodes:
        assert cluster_id not in descendants(node_id)

    parents = [
        rid
        for rid, res in resources.items()
        if cluster_id in (res.get("Children") or [])
    ]
    assert parents == [cluster_display]

    display_parents = [
        rid
        for rid, res in resources.items()
        if cluster_display in (res.get("Children") or [])
    ]
    assert display_parents == [stack_id]

    lb_node_id = "lb-lb-shared-in-vpc-shared-public-subnets-logical-subnet"
    service_node_id = (
        "ecs-arn:aws:ecs:us-east-1:123456789012:service/"
        "shared-cluster/shared-service-in-cluster-shared-cluster-"
        "in-vpc-shared-private-subnets-logical-subnet"
    )
    assert any(
        link["Source"] == lb_node_id and link["Target"] == service_node_id
        for link in links
    )


def test_awslabs_transformer_v2_does_not_emit_target_group_nodes():
    view = _build_shared_cluster_view()

    v2_diagram = AWSLabsTransformerV2(view).transform()
    resources = v2_diagram["Diagram"]["Resources"]

    tg_nodes = [
        rid
        for rid, res in resources.items()
        if res.get("Type") == "AWS::ElasticLoadBalancingV2" and rid.startswith("tg-")
    ]
    assert not tg_nodes


def test_awslabs_transformer_v2_links_ip_target_groups():
    view = _build_lb_with_shared_ip_targets_view()

    v2_diagram = AWSLabsTransformerV2(view).transform()
    resources = v2_diagram["Diagram"]["Resources"]
    links = v2_diagram["Diagram"]["Links"]

    ip_nodes = [rid for rid in resources if rid.startswith("lb-ip-target-")]
    assert len(ip_nodes) == 2

    ip_tg_nodes = [
        rid
        for rid, res in resources.items()
        if rid.startswith("tg-") and res.get("Title", "").startswith("ip-tg")
    ]
    assert not ip_tg_nodes

    lb_node_id = next(
        rid
        for rid, res in resources.items()
        if res.get("Type") == "AWS::ElasticLoadBalancingV2::LoadBalancer"
        and res.get("Title") == "shared-ip-alb"
        and rid.startswith("lb-")
    )

    for ip_node in ip_nodes:
        assert any(
            link["Source"] == lb_node_id and link["Target"] == ip_node for link in links
        )


def test_awslabs_transformer_v2_formats_lb_targets_for_ip_groups():
    view = _build_lb_with_shared_ip_targets_view()
    remote_lb_arn = (
        "arn:aws:elasticloadbalancing:us-east-1:123456789012:"
        "loadbalancer/app/downstream-alb/abc123456789"
    )
    view.filtered_resources["tg-ip-2"].properties["targets"].append(
        {"id": remote_lb_arn, "port": 443}
    )

    v2_diagram = AWSLabsTransformerV2(view).transform()
    resources = v2_diagram["Diagram"]["Resources"]
    links = v2_diagram["Diagram"]["Links"]

    lb_icon_id = next(
        rid
        for rid, res in resources.items()
        if rid.startswith("lb-ip-target-")
        and res.get("Type") == "AWS::ElasticLoadBalancingV2::LoadBalancer"
        and res.get("Title") == "downstream-alb"
    )

    assert "arn:" not in resources[lb_icon_id]["Title"]

    lb_node_id = next(
        rid
        for rid, res in resources.items()
        if res.get("Type") == "AWS::ElasticLoadBalancingV2::LoadBalancer"
        and res.get("Title") == "shared-ip-alb"
        and rid.startswith("lb-")
    )

    assert any(
        link["Source"] == lb_node_id and link["Target"] == lb_icon_id for link in links
    )


def test_awslabs_transformer_v2_renders_lb_stack_for_ip_only_targets():
    view = _build_lb_with_shared_ip_targets_view()

    v2_diagram = AWSLabsTransformerV2(view).transform()
    resources = v2_diagram["Diagram"]["Resources"]

    stack_id = next(
        rid
        for rid, res in resources.items()
        if res.get("Type") == "AWS::Diagram::VerticalStack" and res.get("Title") == "shared-ip-alb"
    )

    # Ensure the stack contains the LB plus the flattened IP nodes
    stack_children = resources[stack_id]["Children"]
    assert stack_children
    def descendants(node_id: str) -> list[str]:
        kids = resources.get(node_id, {}).get("Children", []) or []
        return kids + [d for child in kids for d in descendants(child)]

    stack_descendants = descendants(stack_id)
    assert any(node.startswith("lb-ip-target-") for node in stack_descendants)


def test_awslabs_transformer_v2_deduplicates_lb_targets():
    view = _build_lb_with_shared_ip_targets_view()

    v2_diagram = AWSLabsTransformerV2(view).transform()
    resources = v2_diagram["Diagram"]["Resources"]
    links = v2_diagram["Diagram"]["Links"]

    lb_node_id = next(
        rid
        for rid, res in resources.items()
        if res.get("Type") == "AWS::ElasticLoadBalancingV2::LoadBalancer"
        and res.get("Title") == "shared-ip-alb"
        and rid.startswith("lb-")
    )
    backend_lb_node_id = next(
        rid
        for rid, res in resources.items()
        if res.get("Type") == "AWS::ElasticLoadBalancingV2::LoadBalancer"
        and res.get("Title") == "backend-alb"
        and rid.startswith("lb-")
    )

    assert any(
        link["Source"] == lb_node_id and link["Target"] == backend_lb_node_id
        for link in links
    )

    assert not any(
        rid.startswith("lb-ip-target-")
        and resources[rid].get("Title", "").endswith("backend-alb")
        for rid in resources
    )


def test_awslabs_transformer_v2_has_no_resource_cycles():
    view = _build_lb_with_shared_ip_targets_view()
    diagram = AWSLabsTransformerV2(view).transform()
    resources = diagram["Diagram"]["Resources"]
    assert not _has_cycle(resources, "Canvas")


def test_awslabs_transformer_v2_handles_alb_targets_without_cycles():
    view = _build_lb_with_alb_target_view()

    diagram = AWSLabsTransformerV2(view).transform()
    resources = diagram["Diagram"]["Resources"]
    links = diagram["Diagram"]["Links"]

    upstream_lb_node = next(
        rid
        for rid, res in resources.items()
        if res.get("Type") == "AWS::ElasticLoadBalancingV2::LoadBalancer"
        and res.get("Title") == "frontdoor-alb"
    )
    downstream_lb_node = next(
        rid
        for rid, res in resources.items()
        if res.get("Type") == "AWS::ElasticLoadBalancingV2::LoadBalancer"
        and res.get("Title") == "downstream-alb"
    )
    assert any(
        link["Source"] == upstream_lb_node and link["Target"] == downstream_lb_node
        for link in links
    )

    assert not _has_cycle(resources, "Canvas")


def test_awslabs_transformer_v2_does_not_wrap_clusters_in_sg_containers():
    view = _build_shared_cluster_view()
    diagram = AWSLabsTransformerV2(view).transform()
    resources = diagram["Diagram"]["Resources"]

    for rid, res in resources.items():
        if res.get("Type") != "AWS::EC2::SecurityGroup":
            continue
        for child in res.get("Children") or []:
            child_type = resources.get(child, {}).get("Type")
            assert child_type != "AWS::ECS::Cluster"


def test_awslabs_transformer_v2_does_not_nest_identical_sg_containers():
    view = _build_shared_cluster_view()
    diagram = AWSLabsTransformerV2(view).transform()
    resources = diagram["Diagram"]["Resources"]

    def _sg_base(node_id: str) -> str | None:
        if not node_id.startswith("sg-"):
            return None
        parts = node_id.split("-")
        if len(parts) < 3:
            return None
        return parts[1]

    for rid, res in resources.items():
        if res.get("Type") != "AWS::EC2::SecurityGroup":
            continue
        base = _sg_base(rid)
        for child in res.get("Children") or []:
            if resources.get(child, {}).get("Type") == "AWS::EC2::SecurityGroup":
                assert _sg_base(child) != base


def test_awslabs_transformer_v2_aggregates_ec2_instances_by_security_group():
    view = _build_ec2_aggregation_view()
    diagram = AWSLabsTransformerV2(view).transform()
    resources = diagram["Diagram"]["Resources"]

    shared_sg_nodes = [
        rid
        for rid, res in resources.items()
        if res.get("Type") == "AWS::EC2::SecurityGroup" and res.get("Title") == "SG: shared-ec2"
    ]
    assert len(shared_sg_nodes) == 1
    sg_node = shared_sg_nodes[0]
    sg_children = resources[sg_node].get("Children") or []
    assert len(sg_children) == 1
    stack_id = sg_children[0]
    stack_children = resources[stack_id].get("Children") or []
    assert set(stack_children) == {
        "i-shared-a",
        "i-shared-b",
    }


def test_awslabs_transformer_v2_renders_global_services_stack():
    view = _build_view()

    now = datetime.now(timezone.utc)
    location = ResourceLocation(account_id="123456789012", region="aws-global")
    lb_resource = view.filtered_resources["lb-1"]
    lb_resource.properties["dns_name"] = "demo-alb-123456.us-east-1.elb.amazonaws.com"
    lb_resource.properties["vpc_id"] = "vpc-1"

    cf_resource = BaseResource(
        resource_id="cf-dist-1234",
        resource_type=ResourceType.CLOUDFRONT_DISTRIBUTION,
        name="edge.example.com",
        arn="arn:aws:cloudfront::123456789012:distribution/cf-dist-1234",
        location=location,
        metadata=ResourceMetadata(discovered_at=now, last_updated=now),
        properties={
            "origins": [
                {
                    "id": "alb-origin",
                    "domain_name": "demo-alb-123456.us-east-1.elb.amazonaws.com",
                    "type": "load_balancer",
                }
            ]
        },
    )
    ga_resource = BaseResource(
        resource_id="arn:aws:globalaccelerator::123456789012:accelerator/ga-1",
        resource_type=ResourceType.GLOBAL_ACCELERATOR,
        name="ga-primary",
        arn="arn:aws:globalaccelerator::123456789012:accelerator/ga-1",
        location=location,
        metadata=ResourceMetadata(discovered_at=now, last_updated=now),
        properties={},
    )
    view.filtered_resources[cf_resource.resource_id] = cf_resource
    view.filtered_resources[ga_resource.resource_id] = ga_resource
    view.filtered_relationships.append(
        Relationship(
            source_id=ga_resource.resource_id,
            target_id=lb_resource.resource_id,
            relationship_type=RelationshipType.CONNECTS_TO,
        )
    )

    diagram = AWSLabsTransformerV2(view).transform()
    resources = diagram["Diagram"]["Resources"]

    assert "GlobalServicesStack" in resources["AWSCloud"]["Children"]
    global_stack = resources["GlobalServicesStack"]
    assert set(global_stack["Children"]) == {
        cf_resource.resource_id,
        ga_resource.resource_id,
    }
