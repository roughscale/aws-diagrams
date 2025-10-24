"""
Draw.io XML transformer for converting generic graph format to draw.io diagrams.

This module transforms generic graph representations into draw.io XML format
with proper AWS icon support, hierarchical grouping, and automatic layout.
"""

# Use the standard library ElementTree for XML generation. The defusedxml
# variant does not expose construction helpers like Element/SubElement which
# this module relies on when building diagrams from scratch.
import xml.etree.ElementTree as ET

try:
    from defusedxml import minidom  # type: ignore
except ImportError:
    from xml.dom import minidom

import base64
import zlib
from typing import Dict, List, Any, Optional, Tuple
import logging
import math

try:
    from ..views.view_engine import TopologyView
    from ..topology.schema import ResourceType
    from ..utils.logger import get_logger
    from .base_transformer import BaseTransformer
    from .graph_model import DiagramGraph, GraphNode, GraphEdge, GraphContainer, NodeType, LayoutType
except ImportError:
    from views.view_engine import TopologyView
    from topology.schema import ResourceType
    from utils.logger import get_logger
    from transformers.base_transformer import BaseTransformer
    from transformers.graph_model import DiagramGraph, GraphNode, GraphEdge, GraphContainer, NodeType, LayoutType

logger = get_logger("drawio_transformer")


class DrawioTransformer(BaseTransformer):
    """Transforms generic graph representation into draw.io XML format."""

    # AWS icon mapping to draw.io shapes
    AWS_ICON_MAPPING = {
        ResourceType.VPC: {
            "shape": "mxgraph.aws4.group",
            "style_base": "grIcon=mxgraph.aws4.vpc",
            "width": 300,
            "height": 200
        },
        ResourceType.SUBNET: {
            "shape": "mxgraph.aws4.group",
            "style_base": "grIcon=mxgraph.aws4.vpc",
            "width": 200,
            "height": 150
        },
        ResourceType.EC2_INSTANCE: {
            "shape": "mxgraph.aws4.resourceIcon",
            "style_base": "resIcon=mxgraph.aws4.ec2",
            "width": 78,
            "height": 78
        },
        ResourceType.LAMBDA_FUNCTION: {
            "shape": "mxgraph.aws4.resourceIcon",
            "style_base": "resIcon=mxgraph.aws4.lambda",
            "width": 78,
            "height": 78
        },
        ResourceType.ECS_CLUSTER: {
            "shape": "mxgraph.aws4.resourceIcon",
            "style_base": "resIcon=mxgraph.aws4.ecs",
            "width": 78,
            "height": 78
        },
        ResourceType.ECS_SERVICE: {
            "shape": "mxgraph.aws4.resourceIcon",
            "style_base": "resIcon=mxgraph.aws4.ecs_service",
            "width": 78,
            "height": 78
        },
        ResourceType.LOAD_BALANCER: {
            "shape": "mxgraph.aws4.resourceIcon",
            "style_base": "resIcon=mxgraph.aws4.application_load_balancer",
            "width": 78,
            "height": 78
        },
        ResourceType.RDS_INSTANCE: {
            "shape": "mxgraph.aws4.resourceIcon",
            "style_base": "resIcon=mxgraph.aws4.rds_db_instance",
            "width": 78,
            "height": 78
        },
        ResourceType.RDS_CLUSTER: {
            "shape": "mxgraph.aws4.resourceIcon",
            "style_base": "resIcon=mxgraph.aws4.rds_db_cluster",
            "width": 78,
            "height": 78
        },
        ResourceType.NAT_GATEWAY: {
            "shape": "mxgraph.aws4.resourceIcon",
            "style_base": "resIcon=mxgraph.aws4.nat_gateway",
            "width": 78,
            "height": 78
        },
        ResourceType.INTERNET_GATEWAY: {
            "shape": "mxgraph.aws4.resourceIcon",
            "style_base": "resIcon=mxgraph.aws4.internet_gateway",
            "width": 78,
            "height": 78
        },
        ResourceType.SECURITY_GROUP: {
            "shape": "mxgraph.aws4.group",
            "style_base": "grIcon=mxgraph.aws4.security_group",
            "width": 150,
            "height": 100
        }
    }

    # Default icon for unmapped resources
    DEFAULT_ICON = {
        "shape": "ellipse",
        "style_base": "",
        "width": 60,
        "height": 40
    }

    def __init__(self, view: TopologyView, compress: bool = False):
        super().__init__(view)
        self.compress = compress
        self.cell_counter = 2  # Start from 2 (0 and 1 are reserved)
        self.cell_mapping: Dict[str, int] = {}  # graph_id -> mxCell id

    def transform(self) -> str:
        """Transform the generic graph into draw.io XML format."""
        logger.info(f"Transforming graph to draw.io XML format (compress={self.compress})")

        # Create the generic graph first
        self.create_graph()

        # Generate the XML
        xml_content = self._generate_xml()

        # Optionally compress
        if self.compress:
            xml_content = self._compress_xml(xml_content)

        logger.info(f"Draw.io transformation complete: {len(xml_content)} characters")
        return xml_content

    def _generate_xml(self) -> str:
        """Generate the mxGraph XML structure."""
        # Create root mxGraphModel
        root = ET.Element("mxGraphModel")
        root.set("dx", "1426")
        root.set("dy", "750")
        root.set("grid", "1")
        root.set("gridSize", "10")
        root.set("guides", "1")
        root.set("tooltips", "1")
        root.set("connect", "1")
        root.set("arrows", "1")
        root.set("fold", "1")
        root.set("page", "1")
        root.set("pageScale", "1")
        root.set("pageWidth", "827")
        root.set("pageHeight", "1169")
        root.set("math", "0")
        root.set("shadow", "0")

        # Create root element
        root_elem = ET.SubElement(root, "root")

        # Add default cells (required by draw.io)
        default_cell_0 = ET.SubElement(root_elem, "mxCell")
        default_cell_0.set("id", "0")

        default_cell_1 = ET.SubElement(root_elem, "mxCell")
        default_cell_1.set("id", "1")
        default_cell_1.set("parent", "0")

        # Add containers first (VPCs, clusters, etc.)
        self._add_containers_to_xml(root_elem)

        # Add nodes
        self._add_nodes_to_xml(root_elem)

        # Add edges
        self._add_edges_to_xml(root_elem)

        # Convert to string with proper formatting
        xml_str = ET.tostring(root, encoding='unicode')
        dom = minidom.parseString(xml_str)
        return dom.toprettyxml(indent="  ")

    def _add_containers_to_xml(self, parent: ET.Element) -> None:
        """Add container elements to the XML."""
        for container in self.graph.containers.values():
            self._add_container_to_xml(parent, container)

    def _add_container_to_xml(self, parent: ET.Element, container: GraphContainer) -> None:
        """Add a single container to the XML."""
        cell_id = self._get_cell_id(container.id)

        # Determine parent (containers are top-level by default)
        parent_id = "1"

        # Create the container cell
        cell = ET.SubElement(parent, "mxCell")
        cell.set("id", str(cell_id))
        cell.set("value", container.label)

        if container.container_type == "vpc":
            # VPC as a group with AWS styling
            cell.set("style", self._build_vpc_style())
        elif container.container_type == "ecs_cluster":
            # ECS cluster group
            cell.set("style", self._build_cluster_style())
        elif container.container_type == "logical_subnet":
            # Logical subnet group
            cell.set("style", self._build_subnet_style())
        else:
            # Generic group
            cell.set("style", "group")

        cell.set("vertex", "1")
        cell.set("parent", parent_id)

        # Add geometry
        geometry = ET.SubElement(cell, "mxGeometry")
        if container.position:
            geometry.set("x", str(container.position.x))
            geometry.set("y", str(container.position.y))
            geometry.set("width", str(container.position.width or 400))
            geometry.set("height", str(container.position.height or 300))
        else:
            # Calculate size based on children
            width, height = self._calculate_container_size(container)
            x, y = self._calculate_container_position(container)
            geometry.set("x", str(x))
            geometry.set("y", str(y))
            geometry.set("width", str(width))
            geometry.set("height", str(height))

        geometry.set("as", "geometry")

    def _build_vpc_style(self) -> str:
        """Build style string for VPC container."""
        return (
            "group;"
            "fillColor=#F58536;fillOpacity=10;strokeColor=#FF9900;"
            "strokeWidth=2;dashed=0;dashPattern=8 5;fontColor=#000000;"
            "whiteSpace=wrap;html=1;verticalAlign=top;"
        )

    def _build_cluster_style(self) -> str:
        """Build style string for ECS cluster container."""
        return (
            "group;"
            "fillColor=#FF9900;fillOpacity=10;strokeColor=#CC6600;"
            "strokeWidth=1.5;dashed=1;dashPattern=3 3;fontColor=#000000;"
            "whiteSpace=wrap;html=1;verticalAlign=top;"
        )

    def _build_subnet_style(self) -> str:
        """Build style string for subnet container."""
        return (
            "group;"
            "fillColor=#7AA116;fillOpacity=10;strokeColor=#5A7D0F;"
            "strokeWidth=1;dashed=0;fontColor=#000000;"
            "whiteSpace=wrap;html=1;verticalAlign=top;"
        )

    def _calculate_container_size(self, container: GraphContainer) -> Tuple[float, float]:
        """Calculate container size based on children."""
        # Default minimum size
        min_width, min_height = 200, 150

        if not container.children_ids:
            return min_width, min_height

        # Calculate based on child positions
        child_positions = []
        for child_id in container.children_ids:
            node = self.graph.get_node(child_id)
            if node and node.position:
                child_positions.append((
                    node.position.x + (node.position.width or 78),
                    node.position.y + (node.position.height or 78)
                ))

        if child_positions:
            max_x = max(pos[0] for pos in child_positions) + 50  # padding
            max_y = max(pos[1] for pos in child_positions) + 50  # padding
            return max(min_width, max_x), max(min_height, max_y)

        # Grid layout estimation
        child_count = len(container.children_ids)
        cols = math.ceil(math.sqrt(child_count))
        rows = math.ceil(child_count / cols)

        width = max(min_width, cols * 120 + 50)
        height = max(min_height, rows * 100 + 80)

        return width, height

    def _calculate_container_position(self, container: GraphContainer) -> Tuple[float, float]:
        """Calculate container position."""
        # Simple positioning logic - can be improved
        if container.container_type == "vpc":
            return 50, 50
        elif container.container_type == "logical_subnet":
            return 100, 150
        elif container.container_type == "ecs_cluster":
            return 150, 200
        return 200, 250

    def _add_nodes_to_xml(self, parent: ET.Element) -> None:
        """Add node elements to the XML."""
        for node in self.graph.nodes.values():
            self._add_node_to_xml(parent, node)

    def _add_node_to_xml(self, parent: ET.Element, node: GraphNode) -> None:
        """Add a single node to the XML."""
        cell_id = self._get_cell_id(node.id)

        # Determine parent container
        parent_id = self._find_node_parent(node)

        # Create the cell
        cell = ET.SubElement(parent, "mxCell")
        cell.set("id", str(cell_id))
        cell.set("value", node.label)
        cell.set("style", self._build_node_style(node))
        cell.set("vertex", "1")
        cell.set("parent", parent_id)

        # Add geometry
        geometry = ET.SubElement(cell, "mxGeometry")

        if node.position:
            geometry.set("x", str(node.position.x))
            geometry.set("y", str(node.position.y))
            width = node.position.width or self._get_default_width(node)
            height = node.position.height or self._get_default_height(node)
        else:
            # Calculate position automatically
            x, y = self._calculate_node_position(node)
            geometry.set("x", str(x))
            geometry.set("y", str(y))
            width = self._get_default_width(node)
            height = self._get_default_height(node)

        geometry.set("width", str(width))
        geometry.set("height", str(height))
        geometry.set("as", "geometry")

    def _find_node_parent(self, node: GraphNode) -> str:
        """Find the appropriate parent for a node."""
        # Check if node is in any container
        for container in self.graph.containers.values():
            if node.id in container.children_ids:
                return str(self._get_cell_id(container.id))

        # Default to root
        return "1"

    def _build_node_style(self, node: GraphNode) -> str:
        """Build style string for a node."""
        if node.resource_type and node.resource_type in self.AWS_ICON_MAPPING:
            icon_info = self.AWS_ICON_MAPPING[node.resource_type]

            base_style = (
                f"sketch=0;points=[[0,0,0],[0.25,0,0],[0.5,0,0],[0.75,0,0],"
                f"[1,0,0],[0,1,0],[0.25,1,0],[0.5,1,0],[0.75,1,0],[1,1,0],"
                f"[0,0.25,0],[0,0.5,0],[0,0.75,0],[1,0.25,0],[1,0.5,0],"
                f"[1,0.75,0]];outlineConnect=0;fontColor=#232F3E;"
                f"gradientColor=none;fillColor=#D05C17;strokeColor=#ffffff;"
                f"dashed=0;verticalLabelPosition=bottom;verticalAlign=top;"
                f"align=center;html=1;fontSize=12;fontStyle=0;aspect=fixed;"
                f"shape={icon_info['shape']};{icon_info['style_base']}"
            )
        else:
            # Default style for unmapped resources
            base_style = (
                "ellipse;whiteSpace=wrap;html=1;fillColor=#dae8fc;"
                "strokeColor=#6c8ebf;fontColor=#000000;"
            )

        # Apply any custom styling from the node
        if node.style:
            if node.style.fill_color:
                base_style += f";fillColor={node.style.fill_color}"
            if node.style.border_color:
                base_style += f";strokeColor={node.style.border_color}"
            if node.style.font_size:
                base_style += f";fontSize={node.style.font_size}"

        return base_style

    def _get_default_width(self, node: GraphNode) -> float:
        """Get default width for a node."""
        if node.resource_type and node.resource_type in self.AWS_ICON_MAPPING:
            return self.AWS_ICON_MAPPING[node.resource_type]["width"]
        return self.DEFAULT_ICON["width"]

    def _get_default_height(self, node: GraphNode) -> float:
        """Get default height for a node."""
        if node.resource_type and node.resource_type in self.AWS_ICON_MAPPING:
            return self.AWS_ICON_MAPPING[node.resource_type]["height"]
        return self.DEFAULT_ICON["height"]

    def _calculate_node_position(self, node: GraphNode) -> Tuple[float, float]:
        """Calculate position for a node."""
        # Simple grid layout within containers
        base_x, base_y = 20, 50

        # Offset based on node index
        node_index = len([n for n in self.graph.nodes.values()
                         if self._get_cell_id(n.id) < self._get_cell_id(node.id)])

        grid_cols = 4
        col = node_index % grid_cols
        row = node_index // grid_cols

        x = base_x + col * 120
        y = base_y + row * 100

        return x, y

    def _add_edges_to_xml(self, parent: ET.Element) -> None:
        """Add edge elements to the XML."""
        for edge in self.graph.edges.values():
            self._add_edge_to_xml(parent, edge)

    def _add_edge_to_xml(self, parent: ET.Element, edge: GraphEdge) -> None:
        """Add a single edge to the XML."""
        cell_id = self._get_cell_id(edge.id)
        source_id = self._get_cell_id(edge.source_id)
        target_id = self._get_cell_id(edge.target_id)

        # Create the edge cell
        cell = ET.SubElement(parent, "mxCell")
        cell.set("id", str(cell_id))
        cell.set("value", edge.label or "")
        cell.set("style", self._build_edge_style(edge))
        cell.set("edge", "1")
        cell.set("source", str(source_id))
        cell.set("target", str(target_id))
        cell.set("parent", "1")

        # Add geometry
        geometry = ET.SubElement(cell, "mxGeometry")
        geometry.set("relative", "1")
        geometry.set("as", "geometry")

        # Add waypoints if specified
        if edge.waypoints:
            array = ET.SubElement(geometry, "Array")
            array.set("as", "points")
            for waypoint in edge.waypoints:
                point = ET.SubElement(array, "mxPoint")
                point.set("x", str(waypoint.x))
                point.set("y", str(waypoint.y))

    def _build_edge_style(self, edge: GraphEdge) -> str:
        """Build style string for an edge."""
        base_style = (
            "endArrow=classic;html=1;rounded=0;fontSize=12;fontColor=#000000;"
        )

        # Style based on relationship type
        if edge.relationship_type == "targets":
            base_style += "strokeColor=#CC0000;strokeWidth=2;"
        elif edge.relationship_type == "contains":
            base_style += "strokeColor=#666666;dashed=1;dashPattern=5 5;"
        elif edge.relationship_type == "attached_to":
            base_style += "strokeColor=#0066CC;strokeWidth=1.5;"
        else:
            base_style += "strokeColor=#999999;"

        # Apply custom styling
        if edge.style:
            if edge.style.color:
                base_style += f"strokeColor={edge.style.color};"
            if edge.style.dashed:
                base_style += "dashed=1;dashPattern=5 5;"

        return base_style

    def _get_cell_id(self, graph_id: str) -> int:
        """Get or create mxCell ID for a graph element."""
        if graph_id not in self.cell_mapping:
            self.cell_mapping[graph_id] = self.cell_counter
            self.cell_counter += 1
        return self.cell_mapping[graph_id]

    def _compress_xml(self, xml_content: str) -> str:
        """Compress XML content using deflate compression."""
        logger.debug("Compressing XML content")
        compressed = zlib.compress(xml_content.encode('utf-8'))
        encoded = base64.b64encode(compressed).decode('ascii')
        return encoded

    def save_to_file(self, filepath: str) -> None:
        """Save the draw.io diagram to a file."""
        xml_content = self.transform()

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(xml_content)

        logger.info(f"Draw.io diagram saved to {filepath}")

    @classmethod
    def transform_view(cls, view: TopologyView, compress: bool = False) -> str:
        """Convenience method to transform a view and return the XML content."""
        transformer = cls(view, compress=compress)
        return transformer.transform()
