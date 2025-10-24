"""EC2 collector for discovering EC2 instances and their relationships."""

from typing import Set, List, Dict, Any, Optional
from datetime import datetime

try:
    from .base_collector import BaseCollector
    from ..topology.schema import (
        ResourceType,
        ComputeResource,
        Relationship,
        RelationshipType,
    )
except ImportError:
    from collectors.base_collector import BaseCollector
    from topology.schema import (
        ResourceType,
        ComputeResource,
        Relationship,
        RelationshipType,
    )

import logging

logger = logging.getLogger(__name__)


class EC2Collector(BaseCollector):
    """Collector for EC2 instances."""

    @property
    def supported_resource_types(self) -> Set[ResourceType]:
        return {ResourceType.EC2_INSTANCE}

    @property
    def required_permissions(self) -> List[str]:
        return ['ec2:DescribeInstances']

    def collect_resources(self) -> None:
        ec2_client = self.get_client('ec2')
        paginator = ec2_client.get_paginator('describe_instances')

        filters: List[Dict[str, Any]] = []
        if self.vpc_ids:
            filters.append({'Name': 'vpc-id', 'Values': list(self.vpc_ids)})

        paginate_kwargs: Dict[str, Any] = {}
        if filters:
            paginate_kwargs['Filters'] = filters

        for page in paginator.paginate(**paginate_kwargs):
            for reservation in page.get('Reservations', []):
                for instance in reservation.get('Instances', []):
                    self._process_instance(instance)

    def _process_instance(self, instance: Dict[str, Any]) -> None:
        instance_id = instance.get('InstanceId')
        if not instance_id:
            return

        vpc_id = instance.get('VpcId')
        subnet_id = instance.get('SubnetId')

        if self.allowed_subnet_ids and subnet_id not in self.allowed_subnet_ids:
            return

        availability_zone = None
        placement = instance.get('Placement') or {}
        if isinstance(placement, dict):
            availability_zone = placement.get('AvailabilityZone')

        tags_list = instance.get('Tags') or []
        tags: Dict[str, str] = {}
        name: Optional[str] = None
        for tag in tags_list:
            key = tag.get('Key')
            value = tag.get('Value')
            if not key:
                continue
            tags[key] = value or ''
            if key.lower() == 'name':
                name = value

        location = self.create_resource_location(availability_zone=availability_zone)
        metadata = self.create_resource_metadata(tags=tags)

        security_group_ids = [sg.get('GroupId') for sg in instance.get('SecurityGroups', []) if sg.get('GroupId')]
        network_interface_ids = [eni.get('NetworkInterfaceId') for eni in instance.get('NetworkInterfaces', []) if eni.get('NetworkInterfaceId')]

        properties: Dict[str, Any] = {
            'vpc_id': vpc_id,
            'subnet_id': subnet_id,
            'security_group_ids': security_group_ids,
            'network_interface_ids': network_interface_ids,
        }

        if 'LaunchTime' in instance and isinstance(instance['LaunchTime'], datetime):
            properties['launch_time'] = instance['LaunchTime'].isoformat()

        resource = ComputeResource(
            resource_id=instance_id,
            resource_type=ResourceType.EC2_INSTANCE,
            name=name,
            arn=f"arn:aws:ec2:{self.region}:{self.account_id}:instance/{instance_id}",
            location=location,
            metadata=metadata,
            properties=properties,
            instance_type=instance.get('InstanceType'),
            state=(instance.get('State') or {}).get('Name'),
            private_ip=instance.get('PrivateIpAddress'),
            public_ip=instance.get('PublicIpAddress')
        )

        self.add_resource(resource)

        if vpc_id:
            self.add_relationship(Relationship(
                source_id=vpc_id,
                target_id=instance_id,
                relationship_type=RelationshipType.CONTAINS
            ))

        if subnet_id:
            self.add_relationship(Relationship(
                source_id=subnet_id,
                target_id=instance_id,
                relationship_type=RelationshipType.CONTAINS
            ))

        for sg_id in security_group_ids:
            self.add_relationship(Relationship(
                source_id=instance_id,
                target_id=sg_id,
                relationship_type=RelationshipType.MEMBER_OF
            ))

        for eni_id in network_interface_ids:
            self.add_relationship(Relationship(
                source_id=eni_id,
                target_id=instance_id,
                relationship_type=RelationshipType.ATTACHED_TO
            ))
