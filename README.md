# AWS Topology Diagrams

Generate architectural diagrams from AWS infrastructure through automated API discovery and transformation to diagram-as-code formats.

## Overview

This tool discovers AWS infrastructure across multiple accounts and regions, creates a comprehensive topology representation in YAML format, and transforms it into visual diagrams using various diagram-as-code tools.

## Features

- **Multi-Account Discovery**: Automatically discover resources across AWS Organization accounts
- **Cross-Account Relationships**: Identify VPC peering, Transit Gateway attachments, and security group relationships
- **Flexible Views**: Generate diagrams for specific VPCs, cross-account connectivity, or complete infrastructure
- **Multiple Output Formats**: Support for AWS Labs diagram-as-code, D2, PlantUML, and custom formats
- **Incremental Updates**: Efficient data collection with caching and change detection
- **Configurable Filtering**: Create custom views based on tags, resource types, or account boundaries

## Quick Start

### Installation

```bash
git clone <repository-url>
cd aws-topology-diagrams
pip install -e .
```

### Basic Usage

1. **Configure AWS Authentication**:
   ```bash
   cp .env.example .env
   # Edit .env with your AWS configuration
   ```

2. **Collect Infrastructure Data**:
   ```bash
   aws-topology collect --account 123456789012 --region us-east-1
   ```

3. **Generate a VPC Diagram**:
   ```bash
   aws-topology generate --view single-vpc --vpc-id vpc-abc123
   ```

4. **Generate Cross-Account Connectivity**:
   ```bash
   aws-topology generate --view cross-account-connectivity
   ```

## Architecture

### Directory Structure
```
aws-topology-diagrams/
├── src/
│   ├── collectors/     # AWS API data collection
│   ├── topology/       # Data schema and merging
│   ├── views/          # Filtering and view generation
│   ├── transformers/   # Diagram format conversion
│   ├── auth/           # AWS authentication
│   └── utils/          # Utilities and helpers
├── cli/                # Command-line interface
├── schemas/            # YAML schemas and examples
├── templates/          # Resource mappings and view templates
└── tests/              # Test suite
```

### Data Flow
1. **Authentication**: Cross-account role assumption for organization-wide access
2. **Collection**: Parallel API calls to discover resources across accounts/regions
3. **Merging**: Consolidate data into unified topology with relationship detection
4. **Filtering**: Extract specific views based on user requirements
5. **Transformation**: Convert to target diagram format with positioning and styling

## Configuration

### Multi-Account Setup

For organization-wide discovery, configure cross-account roles:

```yaml
# config/accounts.yaml
accounts:
  production:
    account_id: "123456789012"
    role_arn: "arn:aws:iam::123456789012:role/TopologyDiscoveryRole"
  development:
    account_id: "234567890123"
    role_arn: "arn:aws:iam::234567890123:role/TopologyDiscoveryRole"
```

### Custom Views

Define custom views for specific diagram needs:

```yaml
# templates/views/custom-security.yaml
view_definition:
  name: "Security Group Relationships"
  filters:
    resource_types: ["security_group", "ec2_instance"]
    tags:
      Environment: ["production"]
  include_relationships:
    - security_group_rules
    - instance_security_groups
  grouping:
    - vpc_id
    - availability_zone
```

## Examples

See the `examples/` directory for:
- Basic single VPC discovery
- Multi-account organization setup
- Custom transformer implementations
- Advanced filtering scenarios

## Development

### Running Tests
```bash
pytest tests/
```

### Code Formatting
```bash
black src/ cli/ tests/
flake8 src/ cli/ tests/
```

### Type Checking
```bash
mypy src/ cli/
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## License

MIT License - see LICENSE file for details