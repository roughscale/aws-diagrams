"""
Generic graph model for diagram transformations.

This module provides format-agnostic classes for representing diagram structure,
enabling multiple output formats (AWS Labs, draw.io, Mermaid, etc.) from a
single graph representation.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Set, Tuple, Union
from enum import Enum
from datetime import datetime
import logging

try:
    from ..topology.schema import ResourceType
    from ..utils.logger import get_logger
except ImportError:
    from topology.schema import ResourceType
    from utils.logger import get_logger

logger = get_logger("graph_model")


class LayoutType(Enum):
    """Types of layout arrangements for containers."""
    HORIZONTAL_STACK = "horizontal_stack"
    VERTICAL_STACK = "vertical_stack"
    GRID = "grid"
    ABSOLUTE = "absolute"
    GROUP = "group"
    FREE_FORM = "free_form"


class NodeType(Enum):
    """Types of nodes in the diagram."""
    RESOURCE = "resource"           # AWS resource (EC2, VPC, etc.)
    CONTAINER = "container"         # Grouping container (VPC, subnet)
    LAYOUT = "layout"              # Layout helper (stacks, grids)
    LABEL = "label"                # Text labels
    LOGICAL = "logical"            # Logical groupings


@dataclass
class Position:
    """2D position with optional size."""
    x: float = 0.0
    y: float = 0.0
    width: Optional[float] = None
    height: Optional[float] = None

    def __post_init__(self):
        # Ensure non-negative values
        self.x = max(0.0, self.x)
        self.y = max(0.0, self.y)
        if self.width is not None:
            self.width = max(0.0, self.width)
        if self.height is not None:
            self.height = max(0.0, self.height)


@dataclass
class Style:
    """Visual styling properties for nodes and edges."""
    color: Optional[str] = None
    fill_color: Optional[str] = None
    border_color: Optional[str] = None
    border_width: Optional[float] = None
    font_size: Optional[int] = None
    font_family: Optional[str] = None
    font_weight: Optional[str] = None
    opacity: Optional[float] = None
    dashed: Optional[bool] = None
    rounded: Optional[bool] = None
    shadow: Optional[bool] = None
    custom_properties: Dict[str, Any] = field(default_factory=dict)

    def merge(self, other: Style) -> Style:
        """Merge with another style, other takes precedence."""
        return Style(
            color=other.color or self.color,
            fill_color=other.fill_color or self.fill_color,
            border_color=other.border_color or self.border_color,
            border_width=other.border_width or self.border_width,
            font_size=other.font_size or self.font_size,
            font_family=other.font_family or self.font_family,
            font_weight=other.font_weight or self.font_weight,
            opacity=other.opacity or self.opacity,
            dashed=other.dashed if other.dashed is not None else self.dashed,
            rounded=other.rounded if other.rounded is not None else self.rounded,
            shadow=other.shadow if other.shadow is not None else self.shadow,
            custom_properties={**self.custom_properties, **other.custom_properties}
        )


@dataclass
class GraphNode:
    """Represents a node in the diagram graph."""
    id: str
    node_type: NodeType
    label: str
    properties: Dict[str, Any] = field(default_factory=dict)
    style: Optional[Style] = None
    position: Optional[Position] = None
    parent_id: Optional[str] = None
    children_ids: List[str] = field(default_factory=list)

    # AWS-specific properties
    resource_type: Optional[ResourceType] = None
    aws_service_type: Optional[str] = None  # e.g., "AWS::EC2::Instance"

    # Layout hints
    layout_type: Optional[LayoutType] = None
    layout_properties: Dict[str, Any] = field(default_factory=dict)

    def add_child(self, child_id: str) -> None:
        """Add a child node ID if not already present."""
        if child_id not in self.children_ids:
            self.children_ids.append(child_id)

    def remove_child(self, child_id: str) -> None:
        """Remove a child node ID if present."""
        if child_id in self.children_ids:
            self.children_ids.remove(child_id)


@dataclass
class GraphEdge:
    """Represents an edge (connection) between two nodes."""
    id: str
    source_id: str
    target_id: str
    label: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)
    style: Optional[Style] = None

    # Edge routing hints
    waypoints: List[Position] = field(default_factory=list)
    curved: Optional[bool] = None

    # Relationship metadata
    relationship_type: Optional[str] = None  # "contains", "targets", etc.


@dataclass
class GraphContainer:
    """Represents a hierarchical container (VPC, subnet, cluster)."""
    id: str
    label: str
    container_type: str  # "vpc", "subnet", "cluster", "security_group"
    children_ids: List[str] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)
    style: Optional[Style] = None
    position: Optional[Position] = None
    layout_type: LayoutType = LayoutType.FREE_FORM
    layout_properties: Dict[str, Any] = field(default_factory=dict)

    def add_child(self, child_id: str) -> None:
        """Add a child ID if not already present."""
        if child_id not in self.children_ids:
            self.children_ids.append(child_id)


@dataclass
class DiagramMetadata:
    """Metadata about the diagram."""
    title: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    source_view: Optional[str] = None
    generator: Optional[str] = None
    version: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)


class DiagramGraph:
    """
    Root container for the complete diagram graph representation.

    This class provides a format-agnostic representation that can be
    transformed into various output formats (AWS Labs, draw.io, Mermaid, etc.).
    """

    def __init__(self, metadata: Optional[DiagramMetadata] = None):
        self.metadata = metadata or DiagramMetadata()
        self.nodes: Dict[str, GraphNode] = {}
        self.edges: Dict[str, GraphEdge] = {}
        self.containers: Dict[str, GraphContainer] = {}
        self.root_nodes: List[str] = []  # Top-level nodes

        # Layout and styling
        self.global_style: Optional[Style] = None
        self.themes: Dict[str, Dict[str, Style]] = {}  # theme_name -> {node_type: style}

        logger.debug("Created new DiagramGraph")

    def add_node(self, node: GraphNode) -> None:
        """Add a node to the graph."""
        self.nodes[node.id] = node

        # Update parent-child relationships
        if node.parent_id:
            parent = self.nodes.get(node.parent_id)
            if parent:
                parent.add_child(node.id)
        else:
            # Top-level node
            if node.id not in self.root_nodes:
                self.root_nodes.append(node.id)

        logger.debug(f"Added node {node.id} of type {node.node_type}")

    def add_edge(self, edge: GraphEdge) -> None:
        """Add an edge to the graph."""
        self.edges[edge.id] = edge
        logger.debug(f"Added edge {edge.id}: {edge.source_id} -> {edge.target_id}")

    def add_container(self, container: GraphContainer) -> None:
        """Add a container to the graph."""
        self.containers[container.id] = container
        logger.debug(f"Added container {container.id} of type {container.container_type}")

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        """Get a node by ID."""
        return self.nodes.get(node_id)

    def get_edge(self, edge_id: str) -> Optional[GraphEdge]:
        """Get an edge by ID."""
        return self.edges.get(edge_id)

    def get_container(self, container_id: str) -> Optional[GraphContainer]:
        """Get a container by ID."""
        return self.containers.get(container_id)

    def get_children(self, node_id: str) -> List[GraphNode]:
        """Get all child nodes of a given node."""
        node = self.get_node(node_id)
        if not node:
            return []

        return [self.nodes[child_id] for child_id in node.children_ids if child_id in self.nodes]

    def get_parent(self, node_id: str) -> Optional[GraphNode]:
        """Get the parent node of a given node."""
        node = self.get_node(node_id)
        if not node or not node.parent_id:
            return None

        return self.get_node(node.parent_id)

    def get_root_nodes(self) -> List[GraphNode]:
        """Get all root (top-level) nodes."""
        return [self.nodes[node_id] for node_id in self.root_nodes if node_id in self.nodes]

    def get_nodes_by_type(self, node_type: NodeType) -> List[GraphNode]:
        """Get all nodes of a specific type."""
        return [node for node in self.nodes.values() if node.node_type == node_type]

    def get_edges_for_node(self, node_id: str) -> List[GraphEdge]:
        """Get all edges connected to a node (incoming or outgoing)."""
        return [edge for edge in self.edges.values()
                if edge.source_id == node_id or edge.target_id == node_id]

    def remove_node(self, node_id: str) -> bool:
        """Remove a node and all associated edges."""
        if node_id not in self.nodes:
            return False

        node = self.nodes[node_id]

        # Remove from parent's children
        if node.parent_id:
            parent = self.get_node(node.parent_id)
            if parent:
                parent.remove_child(node_id)

        # Remove from root nodes
        if node_id in self.root_nodes:
            self.root_nodes.remove(node_id)

        # Remove associated edges
        edges_to_remove = [edge_id for edge_id, edge in self.edges.items()
                          if edge.source_id == node_id or edge.target_id == node_id]
        for edge_id in edges_to_remove:
            del self.edges[edge_id]

        # Remove the node
        del self.nodes[node_id]
        logger.debug(f"Removed node {node_id}")
        return True

    def validate(self) -> List[str]:
        """Validate the graph structure and return any errors."""
        errors = []

        # Check for orphaned parent references
        for node in self.nodes.values():
            if node.parent_id and node.parent_id not in self.nodes:
                errors.append(f"Node {node.id} references non-existent parent {node.parent_id}")

        # Check for invalid edge references
        for edge in self.edges.values():
            if edge.source_id not in self.nodes:
                errors.append(f"Edge {edge.id} references non-existent source {edge.source_id}")
            if edge.target_id not in self.nodes:
                errors.append(f"Edge {edge.id} references non-existent target {edge.target_id}")

        # Check for cycles in parent-child relationships
        visited = set()
        rec_stack = set()

        def has_cycle(node_id: str) -> bool:
            if node_id in rec_stack:
                return True
            if node_id in visited:
                return False

            visited.add(node_id)
            rec_stack.add(node_id)

            node = self.get_node(node_id)
            if node:
                for child_id in node.children_ids:
                    if has_cycle(child_id):
                        return True

            rec_stack.remove(node_id)
            return False

        for root_id in self.root_nodes:
            if has_cycle(root_id):
                errors.append(f"Cycle detected starting from root node {root_id}")

        return errors

    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about the graph."""
        node_types = {}
        for node in self.nodes.values():
            node_types[node.node_type.value] = node_types.get(node.node_type.value, 0) + 1

        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "total_containers": len(self.containers),
            "root_nodes": len(self.root_nodes),
            "node_types": node_types,
            "validation_errors": len(self.validate())
        }

    def to_dict(self) -> Dict[str, Any]:
        """Export the graph to a dictionary representation."""
        return {
            "metadata": {
                "title": self.metadata.title,
                "description": self.metadata.description,
                "created_at": self.metadata.created_at.isoformat() if self.metadata.created_at else None,
                "source_view": self.metadata.source_view,
                "generator": self.metadata.generator,
                "version": self.metadata.version,
                "properties": self.metadata.properties
            },
            "nodes": {
                node_id: {
                    "id": node.id,
                    "node_type": node.node_type.value,
                    "label": node.label,
                    "properties": node.properties,
                    "parent_id": node.parent_id,
                    "children_ids": node.children_ids,
                    "resource_type": node.resource_type.value if node.resource_type else None,
                    "aws_service_type": node.aws_service_type,
                    "layout_type": node.layout_type.value if node.layout_type else None,
                    "layout_properties": node.layout_properties,
                    "position": {
                        "x": node.position.x,
                        "y": node.position.y,
                        "width": node.position.width,
                        "height": node.position.height
                    } if node.position else None,
                    "style": node.style.__dict__ if node.style else None
                }
                for node_id, node in self.nodes.items()
            },
            "edges": {
                edge_id: {
                    "id": edge.id,
                    "source_id": edge.source_id,
                    "target_id": edge.target_id,
                    "label": edge.label,
                    "properties": edge.properties,
                    "relationship_type": edge.relationship_type,
                    "curved": edge.curved,
                    "waypoints": [{"x": p.x, "y": p.y, "width": p.width, "height": p.height}
                                 for p in edge.waypoints],
                    "style": edge.style.__dict__ if edge.style else None
                }
                for edge_id, edge in self.edges.items()
            },
            "containers": {
                container_id: {
                    "id": container.id,
                    "label": container.label,
                    "container_type": container.container_type,
                    "children_ids": container.children_ids,
                    "properties": container.properties,
                    "layout_type": container.layout_type.value,
                    "layout_properties": container.layout_properties,
                    "position": {
                        "x": container.position.x,
                        "y": container.position.y,
                        "width": container.position.width,
                        "height": container.position.height
                    } if container.position else None,
                    "style": container.style.__dict__ if container.style else None
                }
                for container_id, container in self.containers.items()
            },
            "root_nodes": self.root_nodes,
            "statistics": self.get_statistics()
        }