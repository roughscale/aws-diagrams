# Draw.io XML Export Feature

This document describes the new draw.io XML export capability that allows you to generate AWS infrastructure diagrams compatible with draw.io (diagrams.net).

## Overview

The draw.io transformer converts AWS topology data into mxGraph XML format, enabling you to:
- Visualize AWS infrastructure in draw.io
- Use proper AWS icons and styling
- Create hierarchical diagrams with containers (VPCs, clusters, etc.)
- Export diagrams for presentations and documentation

## Architecture

The implementation follows a three-layer architecture:

1. **Generic Graph Model** (`src/transformers/graph_model.py`)
   - Format-agnostic representation of diagram structure
   - Supports nodes, edges, containers, styling, and positioning
   - Foundation for multiple output formats

2. **Base Transformer** (`src/transformers/base_transformer.py`)
   - Common topology-to-graph conversion logic
   - Handles AWS-specific patterns (ECS clusters, logical subnets, etc.)
   - Shared by all transformer implementations

3. **Draw.io Transformer** (`src/transformers/drawio_transformer.py`)
   - Converts generic graph to mxGraph XML format
   - Maps AWS resources to proper draw.io icons
   - Handles hierarchical grouping and layout

## Usage

### CLI Usage

Generate draw.io diagrams using the standard CLI commands:

```bash
# Generate VPC diagram in draw.io format
aws-topology generate vpc \
  --topology topology.yaml \
  --vpc-id vpc-12345678 \
  --output diagram.drawio \
  --format drawio

# Generate compressed draw.io format
aws-topology generate vpc \
  --topology topology.yaml \
  --vpc-id vpc-12345678 \
  --output diagram.drawio \
  --format drawio \
  --compress
```

### Programmatic Usage

```python
from transformers import DrawioTransformer
from views.view_engine import ViewEngine, ViewDefinition

# Create view from topology
view_engine = ViewEngine(topology)
view = view_engine.create_single_vpc_view(vpc_id="vpc-12345678")

# Transform to draw.io
transformer = DrawioTransformer(view)
xml_content = transformer.transform()

# Save to file
transformer.save_to_file("diagram.drawio")
```

## Supported AWS Services

The draw.io transformer supports all major AWS services with proper icons:

| Service | Icon | Resource Type |
|---------|------|---------------|
| VPC | `mxgraph.aws4.vpc` | Container/Group |
| EC2 Instance | `mxgraph.aws4.ec2` | Resource Icon |
| Lambda Function | `mxgraph.aws4.lambda` | Resource Icon |
| ECS Cluster | `mxgraph.aws4.ecs` | Resource Icon |
| ECS Service | `mxgraph.aws4.ecs_service` | Resource Icon |
| Load Balancer | `mxgraph.aws4.application_load_balancer` | Resource Icon |
| RDS Instance | `mxgraph.aws4.rds_db_instance` | Resource Icon |
| RDS Cluster | `mxgraph.aws4.rds_db_cluster` | Resource Icon |
| NAT Gateway | `mxgraph.aws4.nat_gateway` | Resource Icon |
| Internet Gateway | `mxgraph.aws4.internet_gateway` | Resource Icon |
| Security Group | `mxgraph.aws4.security_group` | Container/Group |

## Features

### Hierarchical Containers

The transformer creates proper hierarchical groupings:

- **VPC Containers**: Group all resources within a VPC
- **Logical Subnet Groups**: Aggregate subnets by type (public/private)
- **ECS Cluster Containers**: Group ECS services within clusters
- **Security Group Containers**: Group resources by shared security groups

### Automatic Layout

- Grid-based positioning for resources
- Container sizing based on child elements
- Proper spacing and alignment
- Support for custom positioning via graph model

### Edge Relationships

Edges are styled based on relationship type:

- **Targets**: Red solid lines (load balancer → instance)
- **Contains**: Gray dashed lines (VPC → subnet)
- **Attached**: Blue solid lines (instance → subnet)
- **Routes**: Green dashed lines (route table → gateway)

### XML Format Options

- **Uncompressed**: Human-readable XML (default)
- **Compressed**: Base64-encoded deflate compression for smaller files

## Implementation Details

### XML Structure

The generated XML follows mxGraph format:

```xml
<mxGraphModel dx="1426" dy="750" grid="1" ...>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>

    <!-- VPC Container -->
    <mxCell id="2" value="demo-vpc" style="group;fillColor=#F58536;..."
           vertex="1" parent="1">
      <mxGeometry x="50" y="50" width="400" height="300" as="geometry"/>
    </mxCell>

    <!-- EC2 Instance -->
    <mxCell id="3" value="web-server"
           style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.ec2;..."
           vertex="1" parent="2">
      <mxGeometry x="20" y="50" width="78" height="78" as="geometry"/>
    </mxCell>

    <!-- Connection -->
    <mxCell id="4" value="targets" edge="1" source="5" target="3" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>
```

### Styling

AWS-appropriate colors and styling:
- Orange tones for VPC containers (`#FF9900`)
- Green tones for subnet groups (`#7AA116`)
- Resource-specific colors following AWS design guidelines
- Proper font sizes and alignments

## Testing

Run the test suite:

```bash
# Unit tests
python -m pytest tests/test_drawio_transformer.py

# Integration test with sample data
python examples/drawio_example.py
```

## Opening in Draw.io

1. Go to [draw.io](https://app.diagrams.net/) or use desktop app
2. File → Open from → Device
3. Select your `.drawio` file
4. The diagram will load with proper AWS icons and layout

## Troubleshooting

### Missing AWS Icons

If icons don't display properly:
1. In draw.io: More Shapes → Networking → AWS 4 (ensure enabled)
2. Verify the shape library is loaded: `mxgraph.aws4.*` shapes should be available

### Large Diagram Performance

For large topologies:
1. Use compressed format (`compress=True`)
2. Filter to specific VPCs or resource types
3. Consider breaking into multiple diagrams

### XML Validation Errors

If draw.io reports XML errors:
1. Check that resource IDs don't contain invalid characters
2. Verify all referenced parent/child relationships exist
3. Use the graph validation: `graph.validate()`

## Future Enhancements

Planned improvements:
- Auto-layout algorithms (hierarchical, force-directed)
- Custom themes and styling options
- Support for additional AWS services
- Interactive features (clickable links, metadata tooltips)
- Integration with AWS Well-Architected Framework patterns

## Contributing

When adding new AWS services:
1. Add icon mapping to `AWS_ICON_MAPPING` in `DrawioTransformer`
2. Ensure resource type is handled in base transformer
3. Add test cases for new service types
4. Update this documentation

The generic graph model makes it easy to add new output formats (Mermaid, PlantUML, Visio, etc.) by implementing additional transformers.