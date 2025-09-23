"""
Tests for the draw.io transformer.

This module tests the functionality of the DrawioTransformer class,
ensuring it properly converts topology views to draw.io XML format.
"""

import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

# Add src to path for imports
import sys
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from transformers.drawio_transformer import DrawioTransformer
from transformers.graph_model import DiagramGraph, GraphNode, GraphEdge, NodeType, Style, Position
from topology.schema import ResourceType, BaseResource, ResourceLocation, ResourceMetadata
from views.view_engine import TopologyView


class TestDrawioTransformer(unittest.TestCase):
    """Test cases for DrawioTransformer."""

    def setUp(self):
        """Set up test fixtures."""
        # Create mock topology view
        self.view = Mock(spec=TopologyView)
        self.view.name = "test-vpc-view"
        self.view.description = "Test VPC topology"

        # Create mock resources
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

        # VPC resource
        self.vpc_resource = BaseResource(
            resource_id="vpc-abc123",
            resource_type=ResourceType.VPC,
            name="test-vpc",
            arn="arn:aws:ec2:us-east-1:123456789012:vpc/vpc-abc123",
            location=location,
            metadata=metadata,
            properties={"state": "available"}
        )

        # EC2 resource
        self.ec2_resource = BaseResource(
            resource_id="i-123456789",
            resource_type=ResourceType.EC2_INSTANCE,
            name="web-server",
            arn="arn:aws:ec2:us-east-1:123456789012:instance/i-123456789",
            location=location,
            metadata=metadata,
            properties={
                "state": "running",
                "instance_type": "t3.micro",
                "vpc_id": "vpc-abc123",
                "subnet_ids": ["subnet-def456"]
            }
        )

        self.view.filtered_resources = {
            "vpc-abc123": self.vpc_resource,
            "i-123456789": self.ec2_resource
        }
        self.view.filtered_relationships = []

    def test_transformer_initialization(self):
        """Test transformer initialization."""
        transformer = DrawioTransformer(self.view)

        self.assertEqual(transformer.view, self.view)
        self.assertEqual(transformer.cell_counter, 2)
        self.assertEqual(transformer.compress, False)
        self.assertIsInstance(transformer.graph, DiagramGraph)

    def test_aws_icon_mapping(self):
        """Test AWS icon mapping."""
        transformer = DrawioTransformer(self.view)

        # Test VPC mapping
        vpc_icon = transformer.AWS_ICON_MAPPING[ResourceType.VPC]
        self.assertEqual(vpc_icon["shape"], "mxgraph.aws4.group")
        self.assertIn("mxgraph.aws4.vpc", vpc_icon["style_base"])

        # Test EC2 mapping
        ec2_icon = transformer.AWS_ICON_MAPPING[ResourceType.EC2_INSTANCE]
        self.assertEqual(ec2_icon["shape"], "mxgraph.aws4.resourceIcon")
        self.assertIn("mxgraph.aws4.ec2", ec2_icon["style_base"])

    def test_cell_id_generation(self):
        """Test cell ID generation."""
        transformer = DrawioTransformer(self.view)

        # Test sequential ID generation
        id1 = transformer._get_cell_id("node1")
        id2 = transformer._get_cell_id("node2")
        id3 = transformer._get_cell_id("node1")  # Should return same as id1

        self.assertEqual(id1, 2)
        self.assertEqual(id2, 3)
        self.assertEqual(id3, 2)  # Same as first call

    def test_node_style_building(self):
        """Test node style building."""
        transformer = DrawioTransformer(self.view)
        transformer.create_graph()

        # Get nodes from graph
        vpc_node = None
        ec2_node = None
        for node in transformer.graph.nodes.values():
            if node.resource_type == ResourceType.VPC:
                vpc_node = node
            elif node.resource_type == ResourceType.EC2_INSTANCE:
                ec2_node = node

        self.assertIsNotNone(vpc_node)
        self.assertIsNotNone(ec2_node)

        # Test VPC style
        vpc_style = transformer._build_node_style(vpc_node)
        self.assertIn("mxgraph.aws4.group", vpc_style)
        self.assertIn("mxgraph.aws4.vpc", vpc_style)

        # Test EC2 style
        ec2_style = transformer._build_node_style(ec2_node)
        self.assertIn("mxgraph.aws4.resourceIcon", ec2_style)
        self.assertIn("mxgraph.aws4.ec2", ec2_style)

    def test_xml_generation(self):
        """Test XML generation."""
        transformer = DrawioTransformer(self.view)
        xml_content = transformer.transform()

        # Parse XML to verify structure
        root = ET.fromstring(xml_content)

        # Check root element
        self.assertEqual(root.tag, "mxGraphModel")

        # Check for required attributes
        self.assertIn("dx", root.attrib)
        self.assertIn("dy", root.attrib)
        self.assertIn("grid", root.attrib)

        # Find root element
        root_elem = root.find("root")
        self.assertIsNotNone(root_elem)

        # Check for default cells
        cells = root_elem.findall("mxCell")
        self.assertGreaterEqual(len(cells), 2)  # At least default cells 0 and 1

        # Verify default cells exist
        cell_0 = next((cell for cell in cells if cell.get("id") == "0"), None)
        cell_1 = next((cell for cell in cells if cell.get("id") == "1"), None)
        self.assertIsNotNone(cell_0)
        self.assertIsNotNone(cell_1)
        self.assertEqual(cell_1.get("parent"), "0")

    def test_container_creation(self):
        """Test container creation in XML."""
        transformer = DrawioTransformer(self.view)
        transformer.create_graph()

        # Check that VPC container was created
        vpc_containers = [c for c in transformer.graph.containers.values()
                         if c.container_type == "vpc"]
        self.assertGreater(len(vpc_containers), 0)

        vpc_container = vpc_containers[0]
        self.assertEqual(vpc_container.label, "test-vpc")
        self.assertIn("i-123456789", vpc_container.children_ids)

    def test_file_saving(self):
        """Test saving to file."""
        transformer = DrawioTransformer(self.view)

        # Create temporary file
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.drawio', delete=False) as tmp:
            tmp_path = tmp.name

        try:
            # Save diagram
            transformer.save_to_file(tmp_path)

            # Verify file exists and has content
            saved_file = Path(tmp_path)
            self.assertTrue(saved_file.exists())

            content = saved_file.read_text()
            self.assertIn("mxGraphModel", content)
            self.assertIn("test-vpc", content)

        finally:
            # Clean up
            Path(tmp_path).unlink(missing_ok=True)

    def test_compressed_xml(self):
        """Test compressed XML generation."""
        transformer = DrawioTransformer(self.view, compress=True)
        compressed_content = transformer.transform()

        # Compressed content should be base64 encoded
        import base64
        try:
            base64.b64decode(compressed_content)
            # If no exception, it's valid base64
            self.assertIsInstance(compressed_content, str)
        except Exception:
            self.fail("Compressed content is not valid base64")

    def test_graph_validation(self):
        """Test graph validation after creation."""
        transformer = DrawioTransformer(self.view)
        graph = transformer.create_graph()

        # Validate graph structure
        errors = graph.validate()
        self.assertEqual(len(errors), 0, f"Graph validation errors: {errors}")

        # Check statistics
        stats = graph.get_statistics()
        self.assertGreater(stats["total_nodes"], 0)
        self.assertGreaterEqual(stats["total_containers"], 0)

    def test_default_positioning(self):
        """Test default node positioning."""
        transformer = DrawioTransformer(self.view)
        transformer.create_graph()

        # Check that nodes have positions
        for node in transformer.graph.nodes.values():
            self.assertIsNotNone(node.position)
            self.assertIsInstance(node.position.x, (int, float))
            self.assertIsInstance(node.position.y, (int, float))

    def test_convenience_method(self):
        """Test convenience transformation method."""
        xml_content = DrawioTransformer.transform_view(self.view)

        self.assertIsInstance(xml_content, str)
        self.assertIn("mxGraphModel", xml_content)
        self.assertIn("test-vpc", xml_content)


class TestDrawioTransformerIntegration(unittest.TestCase):
    """Integration tests for DrawioTransformer."""

    def test_with_real_topology_data(self):
        """Test with more realistic topology data."""
        # This would be expanded with actual topology test data
        # For now, just ensure the transformer handles empty views gracefully

        view = Mock(spec=TopologyView)
        view.name = "empty-view"
        view.description = "Empty topology view"
        view.filtered_resources = {}
        view.filtered_relationships = []

        transformer = DrawioTransformer(view)
        xml_content = transformer.transform()

        # Should still produce valid XML even with no resources
        root = ET.fromstring(xml_content)
        self.assertEqual(root.tag, "mxGraphModel")


if __name__ == '__main__':
    unittest.main()