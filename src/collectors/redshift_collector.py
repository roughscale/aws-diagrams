"""
Redshift collector for discovering Redshift clusters.
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


class RedshiftCollector(BaseCollector):
    """Collector for Redshift clusters."""

    @property
    def supported_resource_types(self) -> Set[ResourceType]:
        return {ResourceType.REDSHIFT_CLUSTER}

    @property
    def required_permissions(self) -> List[str]:
        return [
            'redshift:DescribeClusters',
            'redshift:DescribeClusterSubnetGroups'
        ]

    def collect_resources(self) -> None:
        redshift = self.get_client('redshift')
        allowed_vpcs = set(self.vpc_ids) if self.vpc_ids else None

        try:
            # Collect Redshift clusters
            clusters = self._paginate_api_call(redshift, 'describe_clusters', 'Clusters')
            for cluster in clusters:
                cluster_id = cluster['ClusterIdentifier']

                # Get VPC info from subnet group
                subnet_group_name = cluster.get('ClusterSubnetGroupName')
                vpc_id = None
                subnet_ids = []

                if subnet_group_name:
                    try:
                        subnet_groups = redshift.describe_cluster_subnet_groups(
                            ClusterSubnetGroupName=subnet_group_name
                        )['ClusterSubnetGroups']
                        if subnet_groups:
                            subnet_group = subnet_groups[0]
                            vpc_id = subnet_group.get('VpcId')
                            subnet_ids = [subnet['SubnetIdentifier'] for subnet in subnet_group.get('Subnets', [])]
                    except Exception:
                        pass

                # Filter by VPC if specified
                if allowed_vpcs and vpc_id not in allowed_vpcs:
                    continue

                # Get security groups
                security_groups = [sg['VpcSecurityGroupId'] for sg in cluster.get('VpcSecurityGroups', [])]

                # Create Redshift cluster resource
                # Use ARN from AWS response (try multiple possible fields)
                cluster_arn = cluster.get('ARN') or cluster.get('ClusterArn') or cluster.get('ClusterNamespaceArn')
                resource = BaseResource(
                    resource_id=cluster['ClusterIdentifier'],
                    resource_type=ResourceType.REDSHIFT_CLUSTER,
                    name=cluster_id,
                    arn=cluster_arn,
                    location=self.create_resource_location(),
                    properties={
                        'cluster_identifier': cluster_id,
                        'node_type': cluster.get('NodeType'),
                        'cluster_status': cluster.get('ClusterStatus'),
                        'cluster_version': cluster.get('ClusterVersion'),
                        'db_name': cluster.get('DBName'),
                        'master_username': cluster.get('MasterUsername'),
                        'vpc_id': vpc_id,
                        'subnet_ids': subnet_ids,
                        'security_group_ids': security_groups,
                        'availability_zone': cluster.get('AvailabilityZone'),
                        'preferred_maintenance_window': cluster.get('PreferredMaintenanceWindow'),
                        'cluster_parameter_group_name': cluster.get('ClusterParameterGroups', [{}])[0].get('ParameterGroupName') if cluster.get('ClusterParameterGroups') else None,
                        'automated_snapshot_retention_period': cluster.get('AutomatedSnapshotRetentionPeriod'),
                        'manual_snapshot_retention_period': cluster.get('ManualSnapshotRetentionPeriod'),
                        'cluster_security_groups': [sg['ClusterSecurityGroupName'] for sg in cluster.get('ClusterSecurityGroups', [])],
                        'vpc_security_groups': security_groups,
                        'cluster_public_key': cluster.get('ClusterPublicKey'),
                        'cluster_nodes': cluster.get('ClusterNodes', []),
                        'endpoint': {
                            'address': cluster.get('Endpoint', {}).get('Address'),
                            'port': cluster.get('Endpoint', {}).get('Port')
                        } if cluster.get('Endpoint') else None,
                        'cluster_create_time': cluster.get('ClusterCreateTime'),
                        'cluster_revision_number': cluster.get('ClusterRevisionNumber'),
                        'number_of_nodes': cluster.get('NumberOfNodes'),
                        'publicly_accessible': cluster.get('PubliclyAccessible'),
                        'encrypted': cluster.get('Encrypted'),
                        'restore_status': cluster.get('RestoreStatus'),
                        'data_transfer_progress': cluster.get('DataTransferProgress'),
                        'hsm_status': cluster.get('HsmStatus'),
                        'cluster_snapshot_copy_status': cluster.get('ClusterSnapshotCopyStatus'),
                        'cluster_availability_status': cluster.get('ClusterAvailabilityStatus'),
                        'modify_status': cluster.get('ModifyStatus'),
                        'expected_next_snapshot_schedule_time': cluster.get('ExpectedNextSnapshotScheduleTime'),
                        'expected_next_snapshot_schedule_time_status': cluster.get('ExpectedNextSnapshotScheduleTimeStatus'),
                        'next_maintenance_window_start_time': cluster.get('NextMaintenanceWindowStartTime'),
                        'resize_info': cluster.get('ResizeInfo'),
                        'aqua_configuration': cluster.get('AquaConfiguration')
                    },
                    metadata=self.create_resource_metadata(tags={})
                )
                self.add_resource(resource)

        except Exception as e:
            logger.error(f"Error collecting Redshift resources: {e}")
            raise