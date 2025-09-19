"""
ECS collector for discovering ECS clusters, services, and tasks, and mapping ENIs.
"""

from typing import Set, List, Dict, Any
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


class ECSCollector(BaseCollector):
    """Collector for ECS clusters, services, and tasks."""

    @property
    def supported_resource_types(self) -> Set[ResourceType]:
        return {ResourceType.ECS_CLUSTER, ResourceType.ECS_SERVICE}

    @property
    def required_permissions(self) -> List[str]:
        return [
            'ecs:ListClusters', 'ecs:DescribeClusters',
            'ecs:ListServices', 'ecs:DescribeServices',
            'ecs:ListTasks', 'ecs:DescribeTasks'
        ]

    def collect_resources(self) -> None:
        ecs = self.get_client('ecs')
        try:
            allowed_subnets = set(self.allowed_subnet_ids) if self.allowed_subnet_ids else None
            clusters_arns = self._paginate_api_call(ecs, 'list_clusters', 'clusterArns')
            if not clusters_arns:
                return
            # Describe clusters in batches
            for i in range(0, len(clusters_arns), 50):
                batch = [c for c in clusters_arns[i:i+50]]
                resp = self._make_api_call(ecs, 'describe_clusters', clusters=batch)
                if not resp:
                    continue
                for c in resp.get('clusters', []):
                    cluster_arn = c['clusterArn']
                    name = c.get('clusterName')
                    location = self.create_resource_location()
                    metadata = self.create_resource_metadata(tags={})
                    cluster_res = BaseResource(
                        resource_id=cluster_arn,
                        resource_type=ResourceType.ECS_CLUSTER,
                        name=name,
                        arn=cluster_arn,
                        location=location,
                        metadata=metadata,
                        properties={}
                    )
                    self.add_resource(cluster_res)

                    # Services for this cluster
                    service_arns = self._paginate_api_call(ecs, 'list_services', 'serviceArns', cluster=cluster_arn)
                    for j in range(0, len(service_arns), 10):
                        s_batch = [s for s in service_arns[j:j+10]]
                        s_resp = self._make_api_call(ecs, 'describe_services', cluster=cluster_arn, services=s_batch)
                        if not s_resp:
                            continue
                        for svc in s_resp.get('services', []):
                            svc_arn = svc['serviceArn']
                            svc_name = svc.get('serviceName')
                            tgs = [lb.get('targetGroupArn') for lb in (svc.get('loadBalancers') or []) if lb.get('targetGroupArn')]
                            vpc_cfg = (svc.get('networkConfiguration', {})
                                       .get('awsvpcConfiguration', {}))
                            subnet_ids = vpc_cfg.get('subnets', [])
                            sg_ids = vpc_cfg.get('securityGroups', [])
                            if allowed_subnets and not (set(subnet_ids) & allowed_subnets):
                                continue
                            svc_res = BaseResource(
                                resource_id=svc_arn,
                                resource_type=ResourceType.ECS_SERVICE,
                                name=svc_name,
                                arn=svc_arn,
                                location=location,
                                metadata=self.create_resource_metadata(tags={}),
                                properties={
                                    'clusterArn': cluster_arn,
                                    'subnet_ids': subnet_ids,
                                    'security_group_ids': sg_ids,
                                    'target_group_arns': tgs
                                }
                            )
                            self.add_resource(svc_res)

                            # Service is contained within VPC via subnets (link subnets -> service)
                            for subnet_id in subnet_ids:
                                self.add_relationship(Relationship(
                                    source_id=subnet_id,
                                    target_id=svc_arn,
                                    relationship_type=RelationshipType.CONTAINS
                                ))

                            # Tasks for service (limit to RUNNING for noise)
                            task_arns = self._paginate_api_call(ecs, 'list_tasks', 'taskArns', cluster=cluster_arn, serviceName=svc_name, desiredStatus='RUNNING')
                            if task_arns:
                                for k in range(0, len(task_arns), 100):
                                    t_batch = [t for t in task_arns[k:k+100]]
                                    t_resp = self._make_api_call(ecs, 'describe_tasks', cluster=cluster_arn, tasks=t_batch)
                                    if not t_resp:
                                        continue
                                    for task in t_resp.get('tasks', []):
                                        # Attachments may include networkInterfaceId and subnetId
                                        for att in task.get('attachments', []):
                                            if att.get('type') == 'ElasticNetworkInterface':
                                                details = {d['name']: d['value'] for d in att.get('details', [])}
                                                eni_id = details.get('networkInterfaceId')
                                                subnet_id = details.get('subnetId')
                                                if eni_id:
                                                    # task -> eni attached_to
                                                    self.add_relationship(Relationship(
                                                        source_id=task['taskArn'],
                                                        target_id=eni_id,
                                                        relationship_type=RelationshipType.ATTACHED_TO
                                                    ))
                                                    # eni -> service member_of (deterministic mapping)
                                                    self.add_relationship(Relationship(
                                                        source_id=eni_id,
                                                        target_id=svc_arn,
                                                        relationship_type=RelationshipType.MEMBER_OF
                                                    ))
                                                if subnet_id:
                                                    # subnet contains service indirectly; already linked above
                                                    pass
                                        # Link task to service
                                        self.add_relationship(Relationship(
                                            source_id=task['taskArn'],
                                            target_id=svc_arn,
                                            relationship_type=RelationshipType.MEMBER_OF
                                        ))
        except Exception as e:
            logger.warning(f"ECS collection partial/failed: {e}")

