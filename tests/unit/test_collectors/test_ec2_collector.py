import boto3
from datetime import datetime, timezone

from collectors.ec2_collector import EC2Collector
from topology.schema import ResourceType, RelationshipType


class FakePaginator:
    def __init__(self, instances):
        self.instances = instances

    def paginate(self, **kwargs):
        yield {
            'Reservations': [
                {
                    'Instances': self.instances
                }
            ]
        }


class FakeEC2Client:
    def __init__(self, instances):
        self.instances = instances

    def get_paginator(self, name):
        assert name == 'describe_instances'
        return FakePaginator(self.instances)


def test_ec2_collector_discovers_instances(monkeypatch):
    session = boto3.Session(region_name='us-east-1')
    collector = EC2Collector(session=session, account_id='123456789012', region='us-east-1')

    instance_data = {
        'InstanceId': 'i-0123456789abcdef0',
        'InstanceType': 't3.micro',
        'PrivateIpAddress': '10.0.0.5',
        'PublicIpAddress': '34.0.0.5',
        'State': {'Name': 'running'},
        'VpcId': 'vpc-1234',
        'SubnetId': 'subnet-1234',
        'Placement': {'AvailabilityZone': 'us-east-1a'},
        'SecurityGroups': [
            {'GroupId': 'sg-1234', 'GroupName': 'default'}
        ],
        'NetworkInterfaces': [
            {'NetworkInterfaceId': 'eni-1234'}
        ],
        'Tags': [{'Key': 'Name', 'Value': 'web-1'}],
        'LaunchTime': datetime(2024, 1, 1, tzinfo=timezone.utc),
    }

    fake_client = FakeEC2Client([instance_data])
    monkeypatch.setattr(collector, 'get_client', lambda service_name: fake_client)

    collector.collect_resources()

    resource = collector.collected_resources['i-0123456789abcdef0']
    assert resource.resource_type == ResourceType.EC2_INSTANCE
    assert resource.properties['vpc_id'] == 'vpc-1234'
    assert resource.properties['subnet_id'] == 'subnet-1234'
    assert resource.properties['security_group_ids'] == ['sg-1234']
    assert resource.properties['network_interface_ids'] == ['eni-1234']

    relationship_types = {(rel.source_id, rel.target_id, rel.relationship_type) for rel in collector.discovered_relationships}
    assert ('vpc-1234', 'i-0123456789abcdef0', RelationshipType.CONTAINS) in relationship_types
    assert ('subnet-1234', 'i-0123456789abcdef0', RelationshipType.CONTAINS) in relationship_types
    assert ('i-0123456789abcdef0', 'sg-1234', RelationshipType.MEMBER_OF) in relationship_types
    assert ('eni-1234', 'i-0123456789abcdef0', RelationshipType.ATTACHED_TO) in relationship_types
