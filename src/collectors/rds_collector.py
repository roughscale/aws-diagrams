"""
RDS collector for discovering RDS instances and clusters.
"""

from typing import Set, List
import logging

try:
    from .base_collector import BaseCollector
    from ..topology.schema import (
        ResourceType, BaseResource, Relationship, RelationshipType
    )
except ImportError:
    from collectors.base_collector import BaseCollector
    from topology.schema import (
        ResourceType, BaseResource, Relationship, RelationshipType
    )

logger = logging.getLogger(__name__)


class RDSCollector(BaseCollector):
    """Collector for RDS instances and clusters."""

    @property
    def supported_resource_types(self) -> Set[ResourceType]:
        return {ResourceType.RDS_INSTANCE, ResourceType.RDS_CLUSTER}

    @property
    def required_permissions(self) -> List[str]:
        return [
            'rds:DescribeDBInstances',
            'rds:DescribeDBClusters',
            'rds:DescribeDBSubnetGroups'
        ]

    def collect_resources(self) -> None:
        rds = self.get_client('rds')
        allowed_vpcs = set(self.vpc_ids) if self.vpc_ids else None

        try:
            # Collect RDS instances
            instances = self._paginate_api_call(rds, 'describe_db_instances', 'DBInstances')
            for instance in instances:
                instance_id = instance['DBInstanceIdentifier']
                instance_arn = instance['DBInstanceArn']

                # Get VPC info from subnet group
                subnet_group = instance.get('DBSubnetGroup', {})
                vpc_id = subnet_group.get('VpcId')
                subnet_ids = [subnet['SubnetIdentifier'] for subnet in subnet_group.get('Subnets', [])]

                # Filter by VPC if specified
                if allowed_vpcs and vpc_id not in allowed_vpcs:
                    continue

                # Get security groups
                security_groups = [sg['VpcSecurityGroupId'] for sg in instance.get('VpcSecurityGroups', [])]

                # Create RDS instance resource
                resource = BaseResource(
                    resource_id=instance_arn,
                    resource_type=ResourceType.RDS_INSTANCE,
                    name=instance_id,
                    location=self.location,
                    properties={
                        'instance_id': instance_id,
                        'engine': instance.get('Engine'),
                        'engine_version': instance.get('EngineVersion'),
                        'instance_class': instance.get('DBInstanceClass'),
                        'vpc_id': vpc_id,
                        'subnet_ids': subnet_ids,
                        'security_group_ids': security_groups,
                        'availability_zone': instance.get('AvailabilityZone'),
                        'multi_az': instance.get('MultiAZ', False),
                        'endpoint': instance.get('Endpoint', {}).get('Address'),
                        'port': instance.get('Endpoint', {}).get('Port'),
                        'status': instance.get('DBInstanceStatus')
                    },
                    metadata=self._create_metadata()
                )
                self.add_resource(resource)

            # Collect RDS clusters (Aurora)
            clusters = self._paginate_api_call(rds, 'describe_db_clusters', 'DBClusters')
            for cluster in clusters:
                cluster_id = cluster['DBClusterIdentifier']
                cluster_arn = cluster['DBClusterArn']

                # Get VPC info from subnet group
                subnet_group_name = cluster.get('DBSubnetGroup')
                if subnet_group_name:
                    # Need to get subnet group details
                    try:
                        subnet_groups = rds.describe_db_subnet_groups(
                            DBSubnetGroupName=subnet_group_name
                        )['DBSubnetGroups']
                        if subnet_groups:
                            subnet_group = subnet_groups[0]
                            vpc_id = subnet_group.get('VpcId')
                            subnet_ids = [subnet['SubnetIdentifier'] for subnet in subnet_group.get('Subnets', [])]
                        else:
                            vpc_id = None
                            subnet_ids = []
                    except Exception:
                        vpc_id = None
                        subnet_ids = []
                else:
                    vpc_id = None
                    subnet_ids = []

                # Filter by VPC if specified
                if allowed_vpcs and vpc_id not in allowed_vpcs:
                    continue

                # Get security groups
                security_groups = [sg['VpcSecurityGroupId'] for sg in cluster.get('VpcSecurityGroups', [])]

                # Create RDS cluster resource
                resource = BaseResource(
                    resource_id=cluster_arn,
                    resource_type=ResourceType.RDS_CLUSTER,
                    name=cluster_id,
                    location=self.location,
                    properties={
                        'cluster_id': cluster_id,
                        'engine': cluster.get('Engine'),
                        'engine_version': cluster.get('EngineVersion'),
                        'vpc_id': vpc_id,
                        'subnet_ids': subnet_ids,
                        'security_group_ids': security_groups,
                        'endpoint': cluster.get('Endpoint'),
                        'reader_endpoint': cluster.get('ReaderEndpoint'),
                        'port': cluster.get('Port'),
                        'status': cluster.get('Status'),
                        'multi_az': cluster.get('MultiAZ', False)
                    },
                    metadata=self._create_metadata()
                )
                self.add_resource(resource)

        except Exception as e:
            logger.error(f"Error collecting RDS resources: {e}")
            raise