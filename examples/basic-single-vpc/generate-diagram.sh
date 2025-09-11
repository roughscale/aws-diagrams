#!/bin/bash

# VPC diagram generation script
# This script generates AWS Labs diagram-as-code from collected topology

set -e

# Configuration - modify these values
TOPOLOGY_FILE="topology.yaml"
VPC_ID="vpc-REPLACE_WITH_VPC_ID"
OUTPUT_FILE="vpc-diagram.yaml"
DIAGRAM_FORMAT="awslabs"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}AWS VPC Diagram Generation${NC}"
echo "============================"
echo "Topology File: $TOPOLOGY_FILE"
echo "VPC ID: $VPC_ID"
echo "Output File: $OUTPUT_FILE"
echo "Format: $DIAGRAM_FORMAT"
echo ""

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Check if topology file exists
if [ ! -f "$SCRIPT_DIR/$TOPOLOGY_FILE" ]; then
    echo -e "${RED}Error: Topology file not found: $SCRIPT_DIR/$TOPOLOGY_FILE${NC}"
    echo "Please run collect.sh first to generate topology data."
    exit 1
fi

# Check if VPC ID is still placeholder
if [ "$VPC_ID" = "vpc-REPLACE_WITH_VPC_ID" ]; then
    echo -e "${YELLOW}Warning: VPC ID appears to be a placeholder${NC}"
    echo "Please edit this script and replace VPC_ID with your actual VPC ID."
    echo ""
    echo "Available VPCs in topology:"
    cd "$PROJECT_ROOT"
    python -c "
import sys
sys.path.insert(0, 'src')
from topology.serializer import TopologyYAMLSerializer
from pathlib import Path

try:
    serializer = TopologyYAMLSerializer()
    topology = serializer.load_from_file(Path('$SCRIPT_DIR/$TOPOLOGY_FILE'))
    
    vpcs_found = []
    for account in topology.organization.accounts.values():
        for region in account.regions.values():
            for resource in region.resources.values():
                if resource.resource_type.value == 'vpc':
                    vpcs_found.append(f'  {resource.resource_id} - {resource.name or \"(unnamed)\"} - {resource.cidr_blocks[0] if hasattr(resource, \"cidr_blocks\") and resource.cidr_blocks else \"(no CIDR)\"}')
    
    if vpcs_found:
        print('\n'.join(vpcs_found))
    else:
        print('  No VPCs found in topology')
except Exception as e:
    print(f'  Error reading topology: {e}')
"
    exit 1
fi

cd "$PROJECT_ROOT"

echo -e "${YELLOW}Generating VPC diagram...${NC}"

# Run the diagram generation
python -m cli.main \
    --log-level INFO \
    generate vpc \
    --topology "$SCRIPT_DIR/$TOPOLOGY_FILE" \
    --vpc-id $VPC_ID \
    --output "$SCRIPT_DIR/$OUTPUT_FILE" \
    --format $DIAGRAM_FORMAT

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Diagram generated successfully${NC}"
    echo "Diagram saved to: $SCRIPT_DIR/$OUTPUT_FILE"
    echo ""
    echo -e "${YELLOW}Next steps:${NC}"
    echo "1. Install AWS Labs diagram-as-code tool"
    echo "2. Generate the visual diagram:"
    echo "   diagram-as-code draw --input $SCRIPT_DIR/$OUTPUT_FILE --output vpc-diagram.png"
    echo ""
    echo "Or view the YAML structure:"
    echo "   cat $SCRIPT_DIR/$OUTPUT_FILE"
else
    echo -e "${RED}✗ Diagram generation failed${NC}"
    exit 1
fi