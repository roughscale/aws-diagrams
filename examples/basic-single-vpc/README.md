# Basic Single VPC Example

This example demonstrates how to collect AWS infrastructure topology for a single VPC and generate a diagram.

## Prerequisites

1. AWS CLI configured with appropriate credentials
2. Cross-account role access (if collecting from different account)
3. Python 3.9+ with required dependencies

## Quick Start

### 1. Collect Topology Data

```bash
# From the aws-topology-diagrams directory
python -m cli.main collect account \
  --account-id 123456789012 \
  --regions us-east-1 \
  --output single-vpc-topology.yaml
```

### 2. Generate VPC Diagram

```bash
python -m cli.main generate vpc \
  --topology single-vpc-topology.yaml \
  --vpc-id vpc-abc123 \
  --output vpc-diagram.yaml \
  --format awslabs
```

### 3. View Topology Statistics

```bash
python -m cli.main stats --topology single-vpc-topology.yaml
```

## Example Output

The generated diagram will include:

- VPC with CIDR blocks
- Subnets (public/private) with availability zones
- Security groups and their rules
- EC2 instances with instance types
- Load balancers and target groups
- RDS instances if present
- NAT gateways and internet gateways
- VPC endpoints

## Customization

You can customize the view by creating a custom view definition file:

```yaml
# custom-vpc-view.yaml
view_definition:
  name: "Custom VPC View"
  description: "VPC with only compute resources"
  filters:
    - type: "vpc"
      values: ["vpc-abc123"]
      include: true
  include_resource_types:
    - "vpc"
    - "subnet"
    - "ec2_instance"
    - "load_balancer"
  grouping_criteria:
    - "availability_zone"
    - "resource_type"
```

Then generate with custom view:

```bash
python -m cli.main generate custom \
  --topology single-vpc-topology.yaml \
  --view-definition custom-vpc-view.yaml \
  --output custom-vpc-diagram.yaml
```

## File Structure

```
examples/basic-single-vpc/
├── README.md                 # This file
├── collect.sh               # Collection script
├── generate-diagram.sh      # Diagram generation script
├── sample-topology.yaml     # Example topology file
└── sample-diagram.yaml      # Example generated diagram
```

## Common Issues

### Authentication Errors
If you get authentication errors, ensure:
1. AWS profile is correctly configured
2. Cross-account role exists and is assumable
3. Role has necessary permissions (see main README)

### Empty Topology
If topology file is empty:
1. Check account ID is correct
2. Verify region has resources
3. Check IAM permissions for discovery APIs

### Missing Resources
If some resources are missing:
1. Check resource tags and filters
2. Verify collector permissions
3. Review collection logs for errors