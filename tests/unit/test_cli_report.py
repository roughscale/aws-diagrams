from datetime import datetime, timezone
from pathlib import Path
import sys

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cli.report import cli as report_cli
from topology.schema import (
    AWSTopology,
    AccountData,
    BaseResource,
    ComputeResource,
    OrganizationData,
    Relationship,
    RelationshipType,
    ResourceLocation,
    ResourceMetadata,
    ResourceType,
    TopologyMetadata,
)
from topology.serializer import TopologyYAMLSerializer


def _build_sample_topology(
    path,
    *,
    extra_enis: bool = False,
    multiple_load_balancers: bool = False,
    include_unknown_target: bool = False,
):
    now = datetime.now(timezone.utc)

    metadata = TopologyMetadata(
        generated_at=now,
        generator_version="test",
        last_updated=now,
        total_accounts=1,
        total_regions=1,
        total_resources=6
        + (1 if extra_enis else 0)
        + (2 if multiple_load_balancers else 0),
    )

    organization = OrganizationData(
        organization_id="o-example",
        management_account_id="123456789012",
        accounts={},
        cross_account_relationships=[],
    )

    account = AccountData(
        account_id="123456789012",
        account_name="Test Account",
        regions={},
        cross_region_relationships=[],
        last_updated=now,
    )
    organization.accounts[account.account_id] = account

    region = account.add_region("us-east-1")

    location = ResourceLocation(account_id=account.account_id, region="us-east-1")

    instance_metadata = ResourceMetadata(
        discovered_at=now,
        last_updated=now,
        api_calls_made=0,
        collection_errors=[],
        tags={"Name": "web"},
    )

    instance = ComputeResource(
        resource_id="i-1234567890abcdef0",
        resource_type=ResourceType.EC2_INSTANCE,
        name="web",
        arn="arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890abcdef0",
        location=location,
        metadata=instance_metadata,
        properties={"network_interface_ids": ["eni-1234abcd"]},
        instance_type="t3.micro",
        state="running",
        private_ip="10.0.1.100",
        public_ip=None,
    )

    eni_metadata = ResourceMetadata(
        discovered_at=now,
        last_updated=now,
        api_calls_made=0,
        collection_errors=[],
        tags={"Name": "eni-web"},
    )

    eni = BaseResource(
        resource_id="eni-1234abcd",
        resource_type=ResourceType.NETWORK_INTERFACE,
        name="eni-web",
        arn="arn:aws:ec2:us-east-1:123456789012:network-interface/eni-1234abcd",
        location=location,
        metadata=eni_metadata,
        properties={"private_ip": "10.0.1.50", "vpc_id": "vpc-1234"},
    )

    lb = BaseResource(
        resource_id=(
            "arn:aws:elasticloadbalancing:us-east-1:123456789012:"
            "loadbalancer/app/my-load-balancer/50dc6c495c0c9188"
        ),
        resource_type=ResourceType.LOAD_BALANCER,
        name="my-load-balancer",
        arn=(
            "arn:aws:elasticloadbalancing:us-east-1:123456789012:"
            "loadbalancer/app/my-load-balancer/50dc6c495c0c9188"
        ),
        location=location,
        metadata=ResourceMetadata(
            discovered_at=now,
            last_updated=now,
            api_calls_made=0,
            collection_errors=[],
            tags={},
        ),
        properties={
            "vpc_id": "vpc-1234",
            "type": "app",
            "scheme": "internet-facing",
            "subnet_ids": ["subnet-1"],
        },
    )

    lb_eni = BaseResource(
        resource_id="eni-lb1234",
        resource_type=ResourceType.NETWORK_INTERFACE,
        name="eni-lb1",
        arn="arn:aws:ec2:us-east-1:123456789012:network-interface/eni-lb1234",
        location=location,
        metadata=ResourceMetadata(
            discovered_at=now,
            last_updated=now,
            api_calls_made=0,
            collection_errors=[],
            tags={},
        ),
        properties={
            "private_ip": "10.0.2.5",
            "vpc_id": "vpc-1234",
            "description": "ELB app/my-load-balancer/50dc6c495c0c9188/0a11b22c33d44",
        },
    )

    rds_instance = BaseResource(
        resource_id="arn:aws:rds:us-east-1:123456789012:db:db-primary",
        resource_type=ResourceType.RDS_INSTANCE,
        name="db-primary",
        arn="arn:aws:rds:us-east-1:123456789012:db:db-primary",
        location=location,
        metadata=ResourceMetadata(
            discovered_at=now,
            last_updated=now,
            api_calls_made=0,
            collection_errors=[],
            tags={},
        ),
        properties={
            "instance_id": "db-primary",
            "engine": "aurora-postgresql",
            "vpc_id": "vpc-1234",
        },
    )

    rds_eni = BaseResource(
        resource_id="eni-rds1234",
        resource_type=ResourceType.NETWORK_INTERFACE,
        name="eni-rds",
        arn="arn:aws:ec2:us-east-1:123456789012:network-interface/eni-rds1234",
        location=location,
        metadata=ResourceMetadata(
            discovered_at=now,
            last_updated=now,
            api_calls_made=0,
            collection_errors=[],
            tags={"rds:db-id": "db-primary"},
        ),
        properties={
            "private_ip": "10.0.3.10",
            "vpc_id": "vpc-1234",
            "description": "RDSNetworkInterface db-primary",
            "attachment": {"InstanceOwnerId": "amazon-rds"},
        },
    )

    region.add_resource(instance)
    region.add_resource(eni)
    region.add_resource(lb)
    region.add_resource(lb_eni)

    if multiple_load_balancers:
        other_lb = BaseResource(
            resource_id=(
                "arn:aws:elasticloadbalancing:us-east-1:123456789012:"
                "loadbalancer/net/other-lb/60aa13c3db5f9ce7"
            ),
            resource_type=ResourceType.LOAD_BALANCER,
            name="other-lb",
            arn=(
                "arn:aws:elasticloadbalancing:us-east-1:123456789012:"
                "loadbalancer/net/other-lb/60aa13c3db5f9ce7"
            ),
            location=location,
            metadata=ResourceMetadata(
                discovered_at=now,
                last_updated=now,
                api_calls_made=0,
                collection_errors=[],
                tags={},
            ),
            properties={
                "vpc_id": "vpc-1234",
                "type": "net",
                "scheme": "internal",
                "subnet_ids": ["subnet-2"],
            },
        )

        other_lb_eni = BaseResource(
            resource_id="eni-net1234",
            resource_type=ResourceType.NETWORK_INTERFACE,
            name="eni-net",
            arn="arn:aws:ec2:us-east-1:123456789012:network-interface/eni-net1234",
            location=location,
            metadata=ResourceMetadata(
                discovered_at=now,
                last_updated=now,
                api_calls_made=0,
                collection_errors=[],
                tags={},
            ),
            properties={
                "private_ip": "10.0.2.6",
                "vpc_id": "vpc-1234",
                "description": "ELB net/other-lb/60aa13c3db5f9ce7/1234abcd5678",
            },
        )

        region.add_resource(other_lb)
        region.add_resource(other_lb_eni)

    if include_unknown_target:
        region.relationships.append(
            Relationship(
                source_id="eni-lb1234",
                target_id="task-abc",
                relationship_type=RelationshipType.ATTACHED_TO,
                properties={},
            )
        )
        region.relationships.append(
            Relationship(
                source_id="eni-lb1234",
                target_id=lb.resource_id,
                relationship_type=RelationshipType.ATTACHED_TO,
                properties={},
            )
        )
    region.add_resource(rds_instance)
    region.add_resource(rds_eni)
    region.relationships.append(
        Relationship(
            source_id="eni-1234abcd",
            target_id="i-1234567890abcdef0",
            relationship_type=RelationshipType.ATTACHED_TO,
            properties={},
        )
    )

    if extra_enis:
        extra_eni_metadata = ResourceMetadata(
            discovered_at=now,
            last_updated=now,
            api_calls_made=0,
            collection_errors=[],
            tags={"Name": "eni-web-b"},
        )
        extra_eni = BaseResource(
            resource_id="eni-1234abce",
            resource_type=ResourceType.NETWORK_INTERFACE,
            name="eni-web-b",
            arn="arn:aws:ec2:us-east-1:123456789012:network-interface/eni-1234abce",
            location=location,
            metadata=extra_eni_metadata,
            properties={"private_ip": "10.0.1.51", "vpc_id": "vpc-1234"},
        )
        region.add_resource(extra_eni)
        region.relationships.append(
            Relationship(
                source_id="eni-1234abce",
                target_id="i-1234567890abcdef0",
                relationship_type=RelationshipType.ATTACHED_TO,
                properties={},
            )
        )

    topology = AWSTopology(
        metadata=metadata,
        organization=organization,
        data_sources={},
    )

    serializer = TopologyYAMLSerializer()
    serializer.save_to_file(topology, path)


def test_eni_report_outputs_expected_table(tmp_path):
    topology_file = tmp_path / "topology.yaml"
    _build_sample_topology(topology_file)

    runner = CliRunner()
    result = runner.invoke(report_cli, ["--topology-file", str(topology_file), "eni"])

    assert result.exit_code == 0
    output = result.output
    assert "ENI ID" in output
    assert "eni-1234abcd" in output
    assert "web (i-1234567890abcdef0)" in output
    assert "10.0.1.50" in output
    assert "eni-lb1234" in output
    assert "my-load-balancer" in output
    assert "eni-rds1234" in output
    assert "db-primary" in output


def test_eni_report_deduplicates_multiple_enis(tmp_path):
    topology_file = tmp_path / "topology.yaml"
    _build_sample_topology(topology_file, extra_enis=True)

    runner = CliRunner()
    result = runner.invoke(report_cli, ["--topology-file", str(topology_file), "eni"])

    assert result.exit_code == 0
    output_lines = result.output.splitlines()
    eni_lines = [line for line in output_lines if "eni-1234abc" in line]

    assert len(eni_lines) == 2
    assert "eni-1234abcd" in eni_lines[0]
    assert "eni-1234abce" in eni_lines[1]
    assert "10.0.1.50" in eni_lines[0]
    assert "10.0.1.51" in eni_lines[1]


def test_eni_report_preserves_load_balancer_associations(tmp_path):
    topology_file = tmp_path / "topology.yaml"
    _build_sample_topology(topology_file, multiple_load_balancers=True)

    runner = CliRunner()
    result = runner.invoke(report_cli, ["--topology-file", str(topology_file), "eni"])

    assert result.exit_code == 0

    lines = [line for line in result.output.splitlines() if line and "eni-" in line]
    assert any("my-load-balancer" in line and "eni-lb1234" in line for line in lines)
    assert any("other-lb" in line and "eni-net1234" in line for line in lines)


def test_eni_report_outputs_csv(tmp_path):
    topology_file = tmp_path / "topology.yaml"
    _build_sample_topology(topology_file)

    runner = CliRunner()
    result = runner.invoke(
        report_cli,
        [
            "--topology-file",
            str(topology_file),
            "eni",
            "--output-format",
            "csv",
        ],
    )

    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert (
        lines[0]
        == "Account,Region,Resource,Resource Type,ENI ID,ENI Name,Private IP,Public IP"
    )
    data_lines = lines[1:]
    assert len(data_lines) == 3
    assert any("eni-1234abcd" in line for line in data_lines)
    assert any("eni-lb1234" in line for line in data_lines)
    assert any("eni-rds1234" in line for line in data_lines)


def test_eni_report_filters_by_vpc(tmp_path):
    topology_file = tmp_path / "topology.yaml"
    _build_sample_topology(topology_file)

    runner = CliRunner()

    # Matching VPC returns the ENI
    match_result = runner.invoke(
        report_cli,
        [
            "--topology-file",
            str(topology_file),
            "eni",
            "--vpc-id",
            "vpc-1234",
        ],
    )

    assert match_result.exit_code == 0
    assert "eni-1234abcd" in match_result.output
    assert "eni-rds1234" in match_result.output

    # Non-matching VPC yields the empty message
    miss_result = runner.invoke(
        report_cli,
        [
            "--topology-file",
            str(topology_file),
            "eni",
            "--vpc-id",
            "vpc-9999",
        ],
    )

    assert miss_result.exit_code == 0
    assert (
        miss_result.output.strip()
        == "No ENI-attached resources were found in the provided topology."
    )


def test_eni_report_drops_unknown_when_known_target_exists(tmp_path):
    topology_file = tmp_path / "topology.yaml"
    _build_sample_topology(
        topology_file,
        multiple_load_balancers=True,
        include_unknown_target=True,
    )

    runner = CliRunner()
    result = runner.invoke(report_cli, ["--topology-file", str(topology_file), "eni"])

    assert result.exit_code == 0
    output = result.output
    assert "task-abc" not in output
    assert "eni-lb1234" in output
