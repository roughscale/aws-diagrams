"""
ElastiCache collector for discovering Redis and Memcached clusters.
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


class ElastiCacheCollector(BaseCollector):
    """Collector for ElastiCache clusters."""

    @property
    def supported_resource_types(self) -> Set[ResourceType]:
        return {ResourceType.ELASTICACHE_CLUSTER}

    @property
    def required_permissions(self) -> List[str]:
        return [
            'elasticache:DescribeCacheClusters',
            'elasticache:DescribeReplicationGroups',
            'elasticache:DescribeCacheSubnetGroups'
        ]

    def collect_resources(self) -> None:
        elasticache = self.get_client('elasticache')
        allowed_vpcs = set(self.vpc_ids) if self.vpc_ids else None

        try:
            # Collect cache clusters
            clusters = self._paginate_api_call(elasticache, 'describe_cache_clusters', 'CacheClusters')
            for cluster in clusters:
                cluster_id = cluster['CacheClusterId']

                # Get subnet group info
                subnet_group_name = cluster.get('CacheSubnetGroupName')
                vpc_id = None
                subnet_ids = []

                if subnet_group_name:
                    try:
                        subnet_groups = elasticache.describe_cache_subnet_groups(
                            CacheSubnetGroupName=subnet_group_name
                        )['CacheSubnetGroups']
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
                security_groups = [sg['SecurityGroupId'] for sg in cluster.get('SecurityGroups', [])]

                # Create ElastiCache cluster resource
                resource = BaseResource(
                    resource_id=cluster['CacheClusterId'],
                    resource_type=ResourceType.ELASTICACHE_CLUSTER,
                    name=cluster_id,
                    location=self.location,
                    properties={
                        'cluster_id': cluster_id,
                        'engine': cluster.get('Engine'),
                        'engine_version': cluster.get('EngineVersion'),
                        'node_type': cluster.get('CacheNodeType'),
                        'num_cache_nodes': cluster.get('NumCacheNodes'),
                        'vpc_id': vpc_id,
                        'subnet_ids': subnet_ids,
                        'security_group_ids': security_groups,
                        'availability_zone': cluster.get('PreferredAvailabilityZone'),
                        'status': cluster.get('CacheClusterStatus'),
                        'replication_group_id': cluster.get('ReplicationGroupId')
                    },
                    metadata=self._create_metadata()
                )
                self.add_resource(resource)

            # Also collect replication groups (Redis clusters)
            replication_groups = self._paginate_api_call(elasticache, 'describe_replication_groups', 'ReplicationGroups')
            for rg in replication_groups:
                rg_id = rg['ReplicationGroupId']

                # Get subnet group info from first member cluster
                vpc_id = None
                subnet_ids = []
                security_groups = []

                member_clusters = rg.get('MemberClusters', [])
                if member_clusters:
                    # Get info from first member cluster
                    try:
                        cluster_details = elasticache.describe_cache_clusters(
                            CacheClusterId=member_clusters[0]
                        )['CacheClusters']
                        if cluster_details:
                            cluster = cluster_details[0]
                            subnet_group_name = cluster.get('CacheSubnetGroupName')
                            if subnet_group_name:
                                subnet_groups = elasticache.describe_cache_subnet_groups(
                                    CacheSubnetGroupName=subnet_group_name
                                )['CacheSubnetGroups']
                                if subnet_groups:
                                    subnet_group = subnet_groups[0]
                                    vpc_id = subnet_group.get('VpcId')
                                    subnet_ids = [subnet['SubnetIdentifier'] for subnet in subnet_group.get('Subnets', [])]
                            security_groups = [sg['SecurityGroupId'] for sg in cluster.get('SecurityGroups', [])]
                    except Exception:
                        pass

                # Filter by VPC if specified
                if allowed_vpcs and vpc_id not in allowed_vpcs:
                    continue

                # Create replication group resource (if we don't already have individual clusters)
                if not member_clusters:  # Only if no individual clusters were found
                    resource = BaseResource(
                        resource_id=rg_id,
                        resource_type=ResourceType.ELASTICACHE_CLUSTER,
                        name=rg_id,
                        location=self.location,
                        properties={
                            'replication_group_id': rg_id,
                            'description': rg.get('Description'),
                            'status': rg.get('Status'),
                            'vpc_id': vpc_id,
                            'subnet_ids': subnet_ids,
                            'security_group_ids': security_groups,
                            'num_cache_clusters': rg.get('NumCacheClusters'),
                            'automatic_failover': rg.get('AutomaticFailover'),
                            'multi_az': rg.get('MultiAZ')
                        },
                        metadata=self._create_metadata()
                    )
                    self.add_resource(resource)

        except Exception as e:
            logger.error(f"Error collecting ElastiCache resources: {e}")
            raise