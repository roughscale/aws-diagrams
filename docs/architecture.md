# Architecture Overview

This document provides an architectural overview of the AWS Topology Discovery and Diagram Generation tool.

## System Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   CLI Interface │    │  View Engine     │    │  Transformers   │
│                 │    │                  │    │                 │
│ • Collection    │───▶│ • Filtering      │───▶│ • AWS Labs      │
│ • Generation    │    │ • Grouping       │    │ • D2 (future)   │
│ • Validation    │    │ • Statistics     │    │ • PlantUML      │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       │                       ▼
┌─────────────────┐              │              ┌─────────────────┐
│   Collectors    │              │              │ Diagram Files   │
│                 │              │              │                 │
│ • VPC Collector │              │              │ • YAML format   │
│ • EC2 Collector │              │              │ • JSON format   │
│ • RDS Collector │              │              │ • Custom format │
└─────────────────┘              │              └─────────────────┘
         │                       │
         ▼                       ▼
┌─────────────────┐    ┌──────────────────┐
│  AWS APIs       │    │ Topology Schema  │
│                 │    │                  │
│ • Cross-account │    │ • Resources      │
│ • Multi-region  │    │ • Relationships  │
│ • Rate limited  │    │ • Metadata       │
└─────────────────┘    └──────────────────┘
```

## Core Components

### 1. Authentication Framework (`src/auth/`)

**Purpose**: Manages AWS authentication and cross-account access.

**Key Classes**:
- `AWSSessionManager`: Handles boto3 sessions and credential caching
- `MultiAccountAuthenticator`: High-level interface for organization-wide access
- `CrossAccountRole`: Configuration for role assumption

**Features**:
- SSO and profile-based authentication
- Cross-account role assumption with caching
- Organization account discovery
- Permission validation

### 2. Topology Schema (`src/topology/`)

**Purpose**: Defines the data structure for AWS infrastructure representation.

**Key Classes**:
- `AWSTopology`: Root container for all topology data
- `BaseResource`: Abstract base for all AWS resources
- `Relationship`: Represents connections between resources
- `TopologyYAMLSerializer`: Handles YAML serialization/deserialization

**Features**:
- Hierarchical account/region/resource organization
- Cross-account relationship tracking
- Resource metadata and tagging
- Statistics and reporting

### 3. Collectors (`src/collectors/`)

**Purpose**: Discovers AWS resources via API calls.

**Key Classes**:
- `BaseCollector`: Abstract base with common functionality
- `VPCCollector`: Collects VPC-related resources
- Future: `EC2Collector`, `RDSCollector`, etc.

**Features**:
- Rate limiting and retry logic
- Error handling and partial collection
- Parallel API calls
- Resource relationship discovery

### 4. View Engine (`src/views/`)

**Purpose**: Filters and extracts topology subsets for diagram generation.

**Key Classes**:
- `ViewEngine`: Creates filtered views from topology
- `ViewFilter`: Defines filtering criteria
- `TopologyView`: Represents a filtered subset

**Features**:
- Multiple filter types (account, region, VPC, tags, etc.)
- Resource grouping and statistics
- Predefined view templates
- Custom view definitions

### 5. Transformers (`src/transformers/`)

**Purpose**: Converts topology views to diagram-as-code formats.

**Key Classes**:
- `AWSLabsTransformer`: Converts to AWS Labs format
- Future: `D2Transformer`, `PlantUMLTransformer`

**Features**:
- Resource type mapping
- Connection generation
- Layout hints and grouping
- Metadata preservation

### 6. CLI Interface (`cli/`)

**Purpose**: Provides command-line interface for all operations.

**Commands**:
- `collect`: Gather topology data from AWS
- `generate`: Create diagrams from topology
- `validate`: Verify topology file integrity
- `stats`: Display topology statistics

## Data Flow

### Collection Process

1. **Authentication**: Establish AWS sessions for target accounts
2. **Discovery**: Parallel collection across accounts/regions
3. **Merging**: Consolidate data into unified topology
4. **Serialization**: Save to YAML format
5. **Validation**: Verify data integrity

### Diagram Generation Process

1. **Loading**: Read topology from YAML file
2. **Filtering**: Apply view criteria to select relevant resources
3. **Grouping**: Organize resources for visualization
4. **Transformation**: Convert to target diagram format
5. **Output**: Generate diagram-as-code file

## Scalability Considerations

### Performance Optimizations

- **Parallel Collection**: Concurrent API calls across regions/accounts
- **Credential Caching**: Avoid repeated authentication overhead
- **Rate Limiting**: Respect AWS API limits with exponential backoff
- **Incremental Updates**: Support for partial topology updates

### Memory Management

- **Streaming Processing**: Handle large topologies without loading all into memory
- **Resource Cleanup**: Proper cleanup of boto3 sessions and connections
- **Efficient Serialization**: YAML streaming for large files

### Error Handling

- **Graceful Degradation**: Continue collection despite partial failures
- **Detailed Logging**: Comprehensive error reporting and debugging
- **Retry Logic**: Automatic retry with exponential backoff
- **Permission Validation**: Pre-flight checks for required permissions

## Extension Points

### Adding New Collectors

```python
class CustomCollector(BaseCollector):
    @property
    def supported_resource_types(self) -> Set[ResourceType]:
        return {ResourceType.CUSTOM_SERVICE}
    
    @property
    def required_permissions(self) -> List[str]:
        return ["custom:DescribeResources"]
    
    def collect_resources(self) -> None:
        # Implementation
        pass
```

### Adding New Transformers

```python
class CustomTransformer:
    def __init__(self, view: TopologyView):
        self.view = view
    
    def transform(self) -> Dict[str, Any]:
        # Convert view to custom format
        pass
```

### Custom View Definitions

```yaml
view_definition:
  name: "Custom View"
  filters:
    - type: "tag"
      values: ["Environment=production"]
  include_resource_types:
    - "ec2_instance"
    - "rds_instance"
```

## Security Considerations

### Authentication Security

- **Least Privilege**: Collectors use minimal required permissions
- **Credential Management**: No long-term credential storage
- **Session Rotation**: Regular session refresh for long-running operations
- **Audit Logging**: All API calls are logged for security review

### Data Security

- **Sensitive Data Filtering**: Option to exclude sensitive information
- **Encryption**: Support for encrypted topology files
- **Access Control**: File-level permissions for topology data
- **Data Retention**: Configurable retention policies

## Future Enhancements

### Planned Features

1. **Additional Collectors**: ECS, Lambda, RDS, ElastiCache
2. **More Diagram Formats**: D2, PlantUML, Mermaid
3. **Real-time Updates**: WebSocket-based live topology updates
4. **Web Interface**: Browser-based topology visualization
5. **Integration APIs**: REST API for programmatic access
6. **Cost Analysis**: Integration with AWS Cost Explorer
7. **Compliance Checking**: Built-in security and compliance validation

### Integration Opportunities

- **CI/CD Pipelines**: Automated diagram generation in build processes
- **Infrastructure as Code**: Integration with Terraform/CloudFormation
- **Monitoring Systems**: Real-time topology updates from CloudWatch events
- **CMDB Integration**: Export to configuration management databases