"""
ELBv2 collector for discovering Application/Network Load Balancers.
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


class ELBV2Collector(BaseCollector):
    """Collector for ALB/NLB resources."""

    @property
    def supported_resource_types(self) -> Set[ResourceType]:
        return {ResourceType.LOAD_BALANCER}

    @property
    def required_permissions(self) -> List[str]:
        return [
            'elasticloadbalancing:DescribeLoadBalancers',
            'elasticloadbalancing:DescribeTargetGroups',
            'elasticloadbalancing:DescribeTargetHealth'
        ]

    def collect_resources(self) -> None:
        elbv2 = self.get_client('elbv2')
        try:
            lbs = self._paginate_api_call(elbv2, 'describe_load_balancers', 'LoadBalancers')
            allowed_vpcs = set(self.vpc_ids) if self.vpc_ids else None
            kept_lb_arns = set()
            for lb in lbs:
                arn = lb['LoadBalancerArn']
                name = lb.get('LoadBalancerName')
                lb_type = lb.get('Type')  # application|network|gateway
                scheme = lb.get('Scheme')
                vpc_id = lb.get('VpcId')
                subnet_ids = [az.get('SubnetId') for az in lb.get('AvailabilityZones', []) if az.get('SubnetId')]
                sg_ids = lb.get('SecurityGroups', []) or []
                if allowed_vpcs and vpc_id not in allowed_vpcs:
                    continue

                res = BaseResource(
                    resource_id=arn,
                    resource_type=ResourceType.LOAD_BALANCER,
                    name=name,
                    arn=arn,
                    location=self.create_resource_location(),
                    metadata=self.create_resource_metadata(tags={}),
                    properties={
                        'vpc_id': vpc_id,
                        'type': lb_type,
                        'scheme': scheme,
                        'subnet_ids': subnet_ids,
                        'security_group_ids': sg_ids,
                        'dns_name': lb.get('DNSName'),
                        'canonical_hosted_zone_id': lb.get('CanonicalHostedZoneId'),
                    }
                )
                self.add_resource(res)
                # Link subnets to LB
                for subnet_id in subnet_ids:
                    self.add_relationship(Relationship(
                        source_id=subnet_id,
                        target_id=arn,
                        relationship_type=RelationshipType.CONTAINS
                    ))
                kept_lb_arns.add(arn)

            # Target groups per LB
            tgs = self._paginate_api_call(elbv2, 'describe_target_groups', 'TargetGroups')
            tg_by_arn = {tg['TargetGroupArn']: tg for tg in tgs}
            # Map LB -> TG using LoadBalancerArns in TGs
            for tg in tgs:
                tg_arn = tg['TargetGroupArn']
                lb_arns = tg.get('LoadBalancerArns', [])
                vpc_id = tg.get('VpcId')
                target_type = tg.get('TargetType')
                if allowed_vpcs and vpc_id not in allowed_vpcs and not (set(lb_arns) & kept_lb_arns):
                    continue
                # Create TG resource
                res = BaseResource(
                    resource_id=tg_arn,
                    resource_type=ResourceType.TARGET_GROUP,
                    name=tg.get('TargetGroupName'),
                    arn=tg_arn,
                    location=self.create_resource_location(),
                    metadata=self.create_resource_metadata(tags={}),
                    properties={
                        'vpc_id': vpc_id,
                        'target_type': target_type,
                        'targets': []
                    }
                )
                self.add_resource(res)
                for lb_arn in lb_arns:
                    self.add_relationship(Relationship(
                        source_id=lb_arn,
                        target_id=tg_arn,
                        relationship_type=RelationshipType.CONTAINS
                    ))

            # Target health to populate targets list on TGs
            for tg_arn, tg in tg_by_arn.items():
                # Only health-check TGs we kept
                if tg_arn not in self.collected_resources:
                    continue
                th = self._make_api_call(elbv2, 'describe_target_health', TargetGroupArn=tg_arn)
                if not th:
                    continue
                descs = th.get('TargetHealthDescriptions', [])
                # Append simple dicts
                for d in descs:
                    target = d.get('Target', {})
                    tgt = {
                        'id': target.get('Id'),
                        'port': target.get('Port'),
                        'availability_zone': target.get('AvailabilityZone')
                    }
                    # Update in collected resource
                    if tg_arn in self.collected_resources:
                        self.collected_resources[tg_arn].properties.setdefault('targets', []).append(tgt)
        except Exception as e:
            logger.warning(f"ELBv2 collection partial/failed: {e}")
