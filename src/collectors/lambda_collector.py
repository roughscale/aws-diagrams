"""
Lambda collector for discovering Lambda functions with VPC configuration.
"""

from typing import Any, Dict, List, Optional, Set
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
        return [
            'lambda:ListFunctions',
            'lambda:GetFunctionConfiguration',
            'ec2:DescribeNetworkInterfaces'
        ]

    def collect_resources(self) -> None:
        lam = self.get_client('lambda')
        ec2 = self.get_client('ec2')
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
                    vpc_id = vpc_cfg.get('VpcId')

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

                    self._attach_function_enis(
                        ec2_client=ec2,
                        function_arn=fn_arn,
                        function_name=name,
                        vpc_id=vpc_id
                    )
        except Exception as e:
            logger.warning(f"Lambda collection partial/failed: {e}")

    def _attach_function_enis(
        self,
        ec2_client: Any,
        function_arn: str,
        function_name: str,
        vpc_id: Optional[str]
    ) -> None:
        """Discover and associate Lambda-managed ENIs."""
        filters = [
            {'Name': 'description', 'Values': [f'AWS Lambda VPC ENI-{function_name}*']}
        ]
        if vpc_id:
            filters.append({'Name': 'vpc-id', 'Values': [vpc_id]})

        try:
            response = self._make_api_call(
                ec2_client,
                'describe_network_interfaces',
                Filters=filters
            )
        except Exception as error:
            logger.debug(
                "Failed to describe Lambda ENIs for %s: %s",
                function_name,
                error
            )
            return

        if not response:
            return

        for eni in response.get('NetworkInterfaces', []):
            eni_id = eni.get('NetworkInterfaceId')
            if not eni_id:
                continue

            self._ensure_eni_resource(eni)

            self.add_relationship(Relationship(
                source_id=eni_id,
                target_id=function_arn,
                relationship_type=RelationshipType.ATTACHED_TO
            ))

            subnet_id = eni.get('SubnetId')
            if subnet_id:
                self.add_relationship(Relationship(
                    source_id=subnet_id,
                    target_id=eni_id,
                    relationship_type=RelationshipType.CONTAINS
                ))

            for group in eni.get('Groups', []) or []:
                sg_id = group.get('GroupId')
                if sg_id:
                    self.add_relationship(Relationship(
                        source_id=sg_id,
                        target_id=eni_id,
                        relationship_type=RelationshipType.ATTACHED_TO
                    ))

    def _ensure_eni_resource(self, eni: Dict[str, Any]) -> None:
        """Create an ENI resource if it hasn't already been collected."""
        eni_id = eni.get('NetworkInterfaceId')
        if not eni_id or self.get_resource_by_id(eni_id):
            return

        subnet_id = eni.get('SubnetId')
        vpc_id = eni.get('VpcId')
        interface_type = eni.get('InterfaceType')
        status = eni.get('Status')
        private_ip = eni.get('PrivateIpAddress')
        description = eni.get('Description')
        groups = [g.get('GroupId') for g in eni.get('Groups', []) if g.get('GroupId')]
        attachment = eni.get('Attachment', {})
        association = eni.get('Association', {})

        tags: Dict[str, str] = {}
        tags_list = eni.get('TagSet', []) or eni.get('Tags', []) or []
        for tag in tags_list:
            key = tag.get('Key')
            value = tag.get('Value')
            if key and value is not None:
                tags[key] = value

        location = self.create_resource_location(eni.get('AvailabilityZone'))
        metadata = self.create_resource_metadata(tags=tags)

        eni_resource = BaseResource(
            resource_id=eni_id,
            resource_type=ResourceType.NETWORK_INTERFACE,
            name=tags.get('Name') or description,
            arn=f"arn:aws:ec2:{self.region}:{self.account_id}:network-interface/{eni_id}",
            location=location,
            metadata=metadata,
            properties={
                'subnet_id': subnet_id,
                'vpc_id': vpc_id,
                'interface_type': interface_type,
                'status': status,
                'description': description,
                'private_ip': private_ip,
                'security_group_ids': groups,
                'attachment': attachment,
                'association': association
            }
        )

        self.add_resource(eni_resource)
