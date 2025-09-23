#!/usr/bin/env python3
"""
Example demonstrating draw.io XML diagram generation.

This script shows how to use the new DrawioTransformer to generate
draw.io compatible XML diagrams from AWS topology data.
"""

import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from datetime import datetime
from topology.schema import (
    AWSTopology, OrganizationData, AccountData, RegionData,
    BaseResource, NetworkResource, ResourceLocation, ResourceMetadata,
    ResourceType, Relationship, RelationshipType
)
from views.view_engine import ViewEngine, ViewDefinition, ViewFilter, FilterType
from transformers.drawio_transformer import DrawioTransformer


def create_sample_topology() -> AWSTopology:
    """Create a sample AWS topology for demonstration."""

    # Create location and metadata
    location = ResourceLocation(
        account_id="123456789012",
        region="us-east-1"
    )

    metadata = ResourceMetadata(
        discovered_at=datetime.now(),
        last_updated=datetime.now(),
        api_calls_made=1,
        collection_errors=[]
    )

    # Create VPC
    vpc = NetworkResource(
        resource_id="vpc-12345678",
        resource_type=ResourceType.VPC,
        name="demo-vpc",
        arn="arn:aws:ec2:us-east-1:123456789012:vpc/vpc-12345678",
        location=location,
        metadata=metadata,
        cidr_blocks=["10.0.0.0/16"],
        properties={
            "state": "available",
            "is_default": False,
            "instance_tenancy": "default"
        }
    )

    # Create public subnet
    public_subnet = NetworkResource(
        resource_id="subnet-11111111",
        resource_type=ResourceType.SUBNET,
        name="public-subnet-1a",
        arn="arn:aws:ec2:us-east-1:123456789012:subnet/subnet-11111111",
        location=ResourceLocation(
            account_id="123456789012",
            region="us-east-1",
            availability_zone="us-east-1a"
        ),
        metadata=metadata,
        cidr_blocks=["10.0.1.0/24"],
        properties={
            "vpc_id": "vpc-12345678",
            "state": "available",
            "map_public_ip_on_launch": True,
            "tags": {"Type": "public"}
        }
    )

    # Create private subnet
    private_subnet = NetworkResource(
        resource_id="subnet-22222222",
        resource_type=ResourceType.SUBNET,
        name="private-subnet-1a",
        arn="arn:aws:ec2:us-east-1:123456789012:subnet/subnet-22222222",
        location=ResourceLocation(
            account_id="123456789012",
            region="us-east-1",
            availability_zone="us-east-1a"
        ),
        metadata=metadata,
        cidr_blocks=["10.0.2.0/24"],
        properties={
            "vpc_id": "vpc-12345678",
            "state": "available",
            "map_public_ip_on_launch": False,
            "tags": {"Type": "private"}
        }
    )

    # Create Internet Gateway
    igw = BaseResource(
        resource_id="igw-12345678",
        resource_type=ResourceType.INTERNET_GATEWAY,
        name="demo-igw",
        arn="arn:aws:ec2:us-east-1:123456789012:internet-gateway/igw-12345678",
        location=location,
        metadata=metadata,
        properties={
            "state": "available",
            "attachments": [{"VpcId": "vpc-12345678", "State": "attached"}]
        }
    )

    # Create EC2 instance in public subnet
    web_server = BaseResource(
        resource_id="i-11111111",
        resource_type=ResourceType.EC2_INSTANCE,
        name="web-server",
        arn="arn:aws:ec2:us-east-1:123456789012:instance/i-11111111",
        location=location,
        metadata=metadata,
        properties={
            "instance_type": "t3.micro",
            "state": "running",
            "vpc_id": "vpc-12345678",
            "subnet_ids": ["subnet-11111111"],
            "security_group_ids": ["sg-12345678"],
            "tags": {"Name": "web-server", "Environment": "demo"}
        }
    )

    # Create RDS instance in private subnet
    database = BaseResource(
        resource_id="db-instance-12345",
        resource_type=ResourceType.RDS_INSTANCE,
        name="demo-database",
        arn="arn:aws:rds:us-east-1:123456789012:db:demo-database",
        location=location,
        metadata=metadata,
        properties={
            "engine": "mysql",
            "engine_version": "8.0.35",
            "instance_class": "db.t3.micro",
            "vpc_id": "vpc-12345678",
            "subnet_ids": ["subnet-22222222"],
            "security_group_ids": ["sg-87654321"]
        }
    )

    # Create Load Balancer
    load_balancer = BaseResource(
        resource_id="elbv2-12345678",
        resource_type=ResourceType.LOAD_BALANCER,
        name="demo-alb",
        arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/demo-alb/12345678",
        location=location,
        metadata=metadata,
        properties={
            "type": "application",
            "scheme": "internet-facing",
            "vpc_id": "vpc-12345678",
            "subnet_ids": ["subnet-11111111"],
            "security_group_ids": ["sg-12345678"]
        }
    )

    # Create ECS Cluster
    ecs_cluster = BaseResource(
        resource_id="arn:aws:ecs:us-east-1:123456789012:cluster/demo-cluster",
        resource_type=ResourceType.ECS_CLUSTER,
        name="demo-cluster",
        arn="arn:aws:ecs:us-east-1:123456789012:cluster/demo-cluster",
        location=location,
        metadata=metadata,
        properties={
            "status": "ACTIVE",
            "running_tasks_count": 2,
            "active_services_count": 1
        }
    )

    # Create ECS Service
    ecs_service = BaseResource(
        resource_id="arn:aws:ecs:us-east-1:123456789012:service/demo-cluster/api-service",
        resource_type=ResourceType.ECS_SERVICE,
        name="api-service",
        arn="arn:aws:ecs:us-east-1:123456789012:service/demo-cluster/api-service",
        location=location,
        metadata=metadata,
        properties={
            "clusterArn": "arn:aws:ecs:us-east-1:123456789012:cluster/demo-cluster",
            "status": "ACTIVE",
            "desired_count": 2,
            "running_count": 2,
            "subnet_ids": ["subnet-22222222"],
            "security_group_ids": ["sg-87654321"]
        }
    )

    # Create Lambda function
    lambda_func = BaseResource(
        resource_id="demo-lambda-function",
        resource_type=ResourceType.LAMBDA_FUNCTION,
        name="data-processor",
        arn="arn:aws:lambda:us-east-1:123456789012:function:data-processor",
        location=location,
        metadata=metadata,
        properties={
            "runtime": "python3.11",
            "handler": "lambda_function.lambda_handler",
            "vpc_id": "vpc-12345678",
            "subnet_ids": ["subnet-22222222"],
            "security_group_ids": ["sg-87654321"]
        }
    )

    # Create relationships
    relationships = [
        # VPC contains subnets
        Relationship(
            source_id="vpc-12345678",
            target_id="subnet-11111111",
            relationship_type=RelationshipType.CONTAINS
        ),
        Relationship(
            source_id="vpc-12345678",
            target_id="subnet-22222222",
            relationship_type=RelationshipType.CONTAINS
        ),

        # IGW attached to VPC
        Relationship(
            source_id="igw-12345678",
            target_id="vpc-12345678",
            relationship_type=RelationshipType.ATTACHED_TO
        ),

        # Load balancer targets EC2 instance
        Relationship(
            source_id="elbv2-12345678",
            target_id="i-11111111",
            relationship_type=RelationshipType.TARGETS
        ),

        # ECS service in cluster
        Relationship(
            source_id="arn:aws:ecs:us-east-1:123456789012:cluster/demo-cluster",
            target_id="arn:aws:ecs:us-east-1:123456789012:service/demo-cluster/api-service",
            relationship_type=RelationshipType.CONTAINS
        )
    ]

    # Build topology structure
    resources = {
        "vpc": {"vpc-12345678": vpc},
        "subnet": {
            "subnet-11111111": public_subnet,
            "subnet-22222222": private_subnet
        },
        "internet_gateway": {"igw-12345678": igw},
        "ec2_instance": {"i-11111111": web_server},
        "rds_instance": {"db-instance-12345": database},
        "load_balancer": {"elbv2-12345678": load_balancer},
        "ecs_cluster": {"arn:aws:ecs:us-east-1:123456789012:cluster/demo-cluster": ecs_cluster},
        "ecs_service": {"arn:aws:ecs:us-east-1:123456789012:service/demo-cluster/api-service": ecs_service},
        "lambda_function": {"demo-lambda-function": lambda_func}
    }

    region_data = RegionData(
        region="us-east-1",
        last_updated=datetime.now(),
        resources=resources,
        relationships=relationships
    )

    account_data = AccountData(
        account_id="123456789012",
        account_name="demo-account",
        last_updated=datetime.now(),
        regions={"us-east-1": region_data},
        cross_region_relationships=[]
    )

    org_data = OrganizationData(
        organization_id="o-1234567890",
        management_account_id="123456789012",
        accounts={"123456789012": account_data},
        cross_account_relationships=[]
    )

    return AWSTopology(
        metadata={
            "generated_at": datetime.now(),
            "generator_version": "1.0.0",
            "total_accounts": 1,
            "total_regions": 1,
            "total_resources": len([r for resources_by_type in resources.values() for r in resources_by_type])
        },
        organization=org_data,
        data_sources={
            "collection_method": "demo_data",
            "collectors_used": ["DemoCollector"]
        }
    )


def main():
    """Main demonstration function."""
    print("AWS Topology Draw.io Transformer Demo")
    print("=====================================")

    # Create sample topology
    print("1. Creating sample AWS topology...")
    topology = create_sample_topology()
    print(f"   Created topology with {topology.metadata['total_resources']} resources")

    # Create view engine and view
    print("2. Creating VPC view...")
    view_engine = ViewEngine(topology)

    # Create a view definition for the VPC
    view_def = ViewDefinition(
        name="Demo VPC View",
        description="Complete view of demo VPC infrastructure",
        filters=[
            ViewFilter(FilterType.VPC, ["vpc-12345678"], include=True)
        ]
    )

    view = view_engine.create_view(view_def)
    print(f"   Created view with {len(view.filtered_resources)} filtered resources")

    # Transform to draw.io format
    print("3. Transforming to draw.io XML format...")
    transformer = DrawioTransformer(view, compress=False)
    xml_content = transformer.transform()

    # Save to file
    output_file = Path(__file__).parent / "demo_topology.drawio"
    transformer.save_to_file(str(output_file))

    print(f"   Saved draw.io diagram to: {output_file}")
    print(f"   XML content length: {len(xml_content)} characters")

    # Also create compressed version
    print("4. Creating compressed version...")
    compressed_transformer = DrawioTransformer(view, compress=True)
    compressed_content = compressed_transformer.transform()

    compressed_file = Path(__file__).parent / "demo_topology_compressed.drawio"
    with open(compressed_file, 'w') as f:
        f.write(compressed_content)

    print(f"   Saved compressed diagram to: {compressed_file}")
    print(f"   Compressed size: {len(compressed_content)} characters")

    # Show some statistics
    stats = transformer.graph.get_statistics()
    print("\n5. Graph Statistics:")
    print(f"   Total nodes: {stats['total_nodes']}")
    print(f"   Total edges: {stats['total_edges']}")
    print(f"   Total containers: {stats['total_containers']}")
    print(f"   Root nodes: {stats['root_nodes']}")
    print(f"   Node types: {stats['node_types']}")

    print("\n✅ Demo complete!")
    print("\nTo view the generated diagrams:")
    print(f"1. Open draw.io (https://app.diagrams.net/)")
    print(f"2. File → Import from → Device")
    print(f"3. Select: {output_file}")
    print(f"\nThe diagram should display your AWS infrastructure with proper AWS icons!")


if __name__ == "__main__":
    main()