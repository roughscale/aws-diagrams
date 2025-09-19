"""
Lambda collector for discovering Lambda functions with VPC configuration.
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


class LambdaCollector(BaseCollector):
    """Collector for Lambda functions and their VPC configuration."""

    @property
    def supported_resource_types(self) -> Set[ResourceType]:
        return {ResourceType.LAMBDA_FUNCTION}

    @property
    def required_permissions(self) -> List[str]:
        return ['lambda:ListFunctions', 'lambda:GetFunctionConfiguration']

    def collect_resources(self) -> None:
        lam = self.get_client('lambda')
        try:
            allowed_subnets = set(self.allowed_subnet_ids) if self.allowed_subnet_ids else None
            paginator = lam.get_paginator('list_functions')
            for page in paginator.paginate():
                for fn in page.get('Functions', []):
                    fn_arn = fn.get('FunctionArn')
                    name = fn.get('FunctionName')
                    # config may include VpcConfig inline; if not, call get_function_configuration
                    vpc_cfg = fn.get('VpcConfig') or {}
                    if not vpc_cfg:
                        cfg = self._make_api_call(lam, 'get_function_configuration', FunctionName=name)
                        vpc_cfg = (cfg or {}).get('VpcConfig', {})
                    subnet_ids = vpc_cfg.get('SubnetIds', [])
                    if allowed_subnets and subnet_ids and not (set(subnet_ids) & allowed_subnets):
                        continue
                    sg_ids = vpc_cfg.get('SecurityGroupIds', [])

                    res = BaseResource(
                        resource_id=fn_arn,
                        resource_type=ResourceType.LAMBDA_FUNCTION,
                        name=name,
                        arn=fn_arn,
                        location=self.create_resource_location(),
                        metadata=self.create_resource_metadata(tags={}),
                        properties={
                            'subnet_ids': subnet_ids,
                            'security_group_ids': sg_ids
                        }
                    )
                    self.add_resource(res)
                    for subnet_id in subnet_ids:
                        self.add_relationship(Relationship(
                            source_id=subnet_id,
                            target_id=fn_arn,
                            relationship_type=RelationshipType.CONTAINS
                        ))
        except Exception as e:
            logger.warning(f"Lambda collection partial/failed: {e}")

