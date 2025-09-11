#!/bin/bash

# Basic VPC topology collection script
# This script collects AWS infrastructure topology for a single account/region

set -e

# Configuration - modify these values
ACCOUNT_ID="123456789012"
REGION="us-east-1"
OUTPUT_FILE="topology.yaml"
AWS_PROFILE="default"
ROLE_NAME="OrganizationAccountAccessRole"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}AWS Topology Collection${NC}"
echo "=========================="
echo "Account ID: $ACCOUNT_ID"
echo "Region: $REGION"
echo "Output File: $OUTPUT_FILE"
echo "AWS Profile: $AWS_PROFILE"
echo ""

# Check if AWS CLI is available
if ! command -v aws &> /dev/null; then
    echo -e "${RED}Error: AWS CLI not found${NC}"
    exit 1
fi

# Check if Python is available
if ! command -v python &> /dev/null; then
    echo -e "${RED}Error: Python not found${NC}"
    exit 1
fi

# Check AWS authentication
echo -e "${YELLOW}Checking AWS authentication...${NC}"
if ! aws sts get-caller-identity --profile $AWS_PROFILE > /dev/null 2>&1; then
    echo -e "${RED}Error: AWS authentication failed${NC}"
    echo "Please ensure your AWS profile is correctly configured."
    exit 1
fi

# Navigate to the project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo -e "${GREEN}✓ AWS authentication successful${NC}"
echo -e "${YELLOW}Starting topology collection...${NC}"

# Run the collection
python -m cli.main \
    --profile $AWS_PROFILE \
    --log-level INFO \
    collect account \
    --account-id $ACCOUNT_ID \
    --regions $REGION \
    --role-name $ROLE_NAME \
    --output "$SCRIPT_DIR/$OUTPUT_FILE" \
    --force

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Collection completed successfully${NC}"
    echo "Topology saved to: $SCRIPT_DIR/$OUTPUT_FILE"
    
    # Show statistics
    echo ""
    echo -e "${YELLOW}Topology Statistics:${NC}"
    python -m cli.main stats --topology "$SCRIPT_DIR/$OUTPUT_FILE"
else
    echo -e "${RED}✗ Collection failed${NC}"
    exit 1
fi