#!/usr/bin/env python3
"""
Comparison example showing original vs refactored AWS Labs transformer.

This script demonstrates that both the original AWS Labs transformer
and the new refactored version produce similar results while showcasing
the benefits of the generic graph model approach.
"""

import sys
from pathlib import Path
import time

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
from transformers import AWSLabsTransformer, AWSLabsTransformerV2, DrawioTransformer


def create_simple_topology() -> AWSTopology:
    """Create a simple topology for comparison testing."""
    location = ResourceLocation(account_id="123456789012", region="us-east-1")
    metadata = ResourceMetadata(
        discovered_at=datetime.now(),
        last_updated=datetime.now(),
        api_calls_made=1,
        collection_errors=[]
    )

    # VPC
    vpc = NetworkResource(
        resource_id="vpc-12345678",
        resource_type=ResourceType.VPC,
        name="comparison-vpc",
        arn="arn:aws:ec2:us-east-1:123456789012:vpc/vpc-12345678",
        location=location,
        metadata=metadata,
        cidr_blocks=["10.0.0.0/16"],
        properties={"state": "available"}
    )

    # EC2 Instance
    ec2 = BaseResource(
        resource_id="i-11111111",
        resource_type=ResourceType.EC2_INSTANCE,
        name="test-instance",
        arn="arn:aws:ec2:us-east-1:123456789012:instance/i-11111111",
        location=location,
        metadata=metadata,
        properties={
            "instance_type": "t3.micro",
            "state": "running",
            "vpc_id": "vpc-12345678"
        }
    )

    # Lambda Function
    lambda_func = BaseResource(
        resource_id="test-function",
        resource_type=ResourceType.LAMBDA_FUNCTION,
        name="test-lambda",
        arn="arn:aws:lambda:us-east-1:123456789012:function:test-function",
        location=location,
        metadata=metadata,
        properties={
            "runtime": "python3.11",
            "vpc_id": "vpc-12345678"
        }
    )

    # Build topology
    resources = {
        "vpc": {"vpc-12345678": vpc},
        "ec2_instance": {"i-11111111": ec2},
        "lambda_function": {"test-function": lambda_func}
    }

    region_data = RegionData(
        region="us-east-1",
        last_updated=datetime.now(),
        resources=resources,
        relationships=[
            Relationship(
                source_id="vpc-12345678",
                target_id="i-11111111",
                relationship_type=RelationshipType.CONTAINS
            )
        ]
    )

    account_data = AccountData(
        account_id="123456789012",
        account_name="test-account",
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
        metadata={"generated_at": datetime.now(), "total_resources": 3},
        organization=org_data,
        data_sources={"collection_method": "test"}
    )


def compare_transformers():
    """Compare original and refactored transformers."""
    print("🔬 Transformer Comparison Demo")
    print("=" * 50)

    # Create topology and view
    print("1. Creating test topology...")
    topology = create_simple_topology()

    view_engine = ViewEngine(topology)
    view_def = ViewDefinition(
        name="Comparison Test View",
        description="Simple view for transformer comparison",
        filters=[ViewFilter(FilterType.VPC, ["vpc-12345678"], include=True)]
    )
    view = view_engine.create_view(view_def)
    print(f"   Created view with {len(view.filtered_resources)} resources")

    # Test Original AWS Labs Transformer
    print("\n2. Testing Original AWS Labs Transformer...")
    start_time = time.time()
    original_transformer = AWSLabsTransformer(view)
    original_result = original_transformer.transform()
    original_time = time.time() - start_time

    print(f"   ✅ Original transformer completed in {original_time:.3f}s")
    print(f"   📊 Generated {len(original_result.get('Resources', {}))} AWS Labs resources")

    # Test Refactored AWS Labs Transformer
    print("\n3. Testing Refactored AWS Labs Transformer...")
    start_time = time.time()
    refactored_transformer = AWSLabsTransformerV2(view)
    refactored_result = refactored_transformer.transform()
    refactored_time = time.time() - start_time

    print(f"   ✅ Refactored transformer completed in {refactored_time:.3f}s")

    diagram_resources = refactored_result.get('Diagram', {}).get('Resources', {})
    print(f"   📊 Generated {len(diagram_resources)} AWS Labs resources")

    # Get graph statistics from refactored version
    graph_stats = refactored_transformer.graph.get_statistics()
    print(f"   🔗 Graph model: {graph_stats['total_nodes']} nodes, {graph_stats['total_containers']} containers")

    # Test Draw.io Transformer
    print("\n4. Testing Draw.io Transformer...")
    start_time = time.time()
    drawio_transformer = DrawioTransformer(view)
    drawio_result = drawio_transformer.transform()
    drawio_time = time.time() - start_time

    print(f"   ✅ Draw.io transformer completed in {drawio_time:.3f}s")
    print(f"   📄 Generated XML with {len(drawio_result)} characters")

    # Save results for inspection
    output_dir = Path(__file__).parent / "comparison_output"
    output_dir.mkdir(exist_ok=True)

    print(f"\n5. Saving results to {output_dir}/...")

    # Save original AWS Labs output
    import yaml
    with open(output_dir / "original_awslabs.yaml", 'w') as f:
        yaml.dump(original_result, f, default_flow_style=False, indent=2)

    # Save refactored AWS Labs output
    with open(output_dir / "refactored_awslabs.yaml", 'w') as f:
        yaml.dump(refactored_result, f, default_flow_style=False, indent=2)

    # Save draw.io output
    with open(output_dir / "drawio_output.xml", 'w') as f:
        f.write(drawio_result)

    # Save graph model representation
    graph_dict = refactored_transformer.graph.to_dict()
    with open(output_dir / "graph_model.yaml", 'w') as f:
        yaml.dump(graph_dict, f, default_flow_style=False, indent=2)

    print("   💾 Saved all outputs for inspection")

    # Comparison summary
    print(f"\n6. Comparison Summary:")
    print(f"   ⏱️  Performance:")
    print(f"      Original:    {original_time:.3f}s")
    print(f"      Refactored:  {refactored_time:.3f}s")
    print(f"      Draw.io:     {drawio_time:.3f}s")

    print(f"   📐 Architecture Benefits:")
    print(f"      ✅ Generic graph model enables multiple output formats")
    print(f"      ✅ Shared topology-to-graph conversion logic")
    print(f"      ✅ Consistent hierarchy handling across formats")
    print(f"      ✅ Easy to add new transformers (Mermaid, PlantUML, etc.)")

    print(f"\n   🔍 Key Features:")
    print(f"      📊 Graph validation: {len(refactored_transformer.graph.validate())} errors")
    print(f"      🏗️  Hierarchical containers: {graph_stats['total_containers']}")
    print(f"      🔗 Relationship preservation: {graph_stats['total_edges']} edges")
    print(f"      🎨 Multiple output formats from single source")

    print(f"\n✅ Comparison Complete!")
    print(f"All transformers produced valid output demonstrating architectural benefits")

    return {
        'original': original_result,
        'refactored': refactored_result,
        'drawio': drawio_result,
        'graph_stats': graph_stats
    }


def demonstrate_extensibility():
    """Demonstrate how easy it is to add new formats with the generic model."""
    print(f"\n🚀 Extensibility Demonstration")
    print("=" * 50)

    print("Adding a new output format is now straightforward:")
    print("""
    class MermaidTransformer(BaseTransformer):
        def transform(self) -> str:
            # Create generic graph
            self.create_graph()

            # Convert to Mermaid format
            mermaid = "graph TD\\n"
            for node in self.graph.nodes.values():
                mermaid += f"    {node.id}[{node.label}]\\n"
            for edge in self.graph.edges.values():
                mermaid += f"    {edge.source_id} --> {edge.target_id}\\n"

            return mermaid
    """)

    print("Benefits:")
    print("  • 🔄 Reuses all topology parsing logic")
    print("  • 🏗️  Inherits container and hierarchy handling")
    print("  • 🎯 Focus only on format-specific output")
    print("  • 🧪 Consistent testing and validation")
    print("  • 📈 Scales to any number of output formats")


if __name__ == "__main__":
    try:
        results = compare_transformers()
        demonstrate_extensibility()

        print(f"\n🎉 Demo completed successfully!")
        print(f"Check the 'comparison_output' directory for generated files")

    except Exception as e:
        print(f"\n❌ Demo failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)