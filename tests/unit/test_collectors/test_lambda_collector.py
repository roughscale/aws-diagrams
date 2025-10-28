import boto3

from collectors.lambda_collector import LambdaCollector
from topology.schema import ResourceType, RelationshipType


class FakeLambdaPaginator:
    def __init__(self, functions):
        self._functions = functions

    def paginate(self):
        yield {'Functions': self._functions}


class FakeLambdaClient:
    def __init__(self, functions):
        self._functions = functions

    def get_paginator(self, name):
        assert name == 'list_functions'
        return FakeLambdaPaginator(self._functions)


class FakeEC2Client:
    def __init__(self):
        self.calls = []


def test_lambda_collector_links_managed_enis(monkeypatch):
    session = boto3.Session(region_name='us-east-1')
    collector = LambdaCollector(session=session, account_id='123456789012', region='us-east-1')

    function = {
        'FunctionArn': 'arn:aws:lambda:us-east-1:123456789012:function:demo',
        'FunctionName': 'demo',
        'VpcConfig': {
            'SubnetIds': ['subnet-1234'],
            'SecurityGroupIds': ['sg-1234'],
            'VpcId': 'vpc-1234'
        }
    }

    fake_lambda = FakeLambdaClient([function])
    fake_ec2 = FakeEC2Client()

    monkeypatch.setattr(
        collector,
        'get_client',
        lambda service_name: fake_lambda if service_name == 'lambda' else fake_ec2
    )

    eni_payload = {
        'NetworkInterfaces': [
            {
                'NetworkInterfaceId': 'eni-1234',
                'SubnetId': 'subnet-1234',
                'VpcId': 'vpc-1234',
                'InterfaceType': 'lambda',
                'Status': 'in-use',
                'PrivateIpAddress': '10.0.0.15',
                'Description': 'AWS Lambda VPC ENI-demo',
                'Groups': [{'GroupId': 'sg-1234'}],
                'Attachment': {'InstanceOwnerId': 'amazon-lambda'},
                'Association': {},
                'AvailabilityZone': 'us-east-1a'
            }
        ]
    }

    def fake_make_api_call(self, client, operation_name, **kwargs):
        self.api_calls_made += 1
        assert operation_name == 'describe_network_interfaces'
        filters = kwargs.get('Filters', [])
        # Ensure the Lambda-specific description filter is used
        assert any(
            f.get('Name') == 'description' and f.get('Values') == ['AWS Lambda VPC ENI-demo*']
            for f in filters
        )
        return eni_payload

    monkeypatch.setattr(
        collector,
        '_make_api_call',
        fake_make_api_call.__get__(collector, LambdaCollector)
    )

    collector.collect_resources()

    lambda_resource = collector.collected_resources[function['FunctionArn']]
    assert lambda_resource.resource_type == ResourceType.LAMBDA_FUNCTION
    assert lambda_resource.properties['subnet_ids'] == ['subnet-1234']
    assert lambda_resource.properties['security_group_ids'] == ['sg-1234']

    eni_resource = collector.collected_resources['eni-1234']
    assert eni_resource.resource_type == ResourceType.NETWORK_INTERFACE
    assert eni_resource.properties['subnet_id'] == 'subnet-1234'
    assert eni_resource.properties['security_group_ids'] == ['sg-1234']

    relationships = {
        (rel.source_id, rel.target_id, rel.relationship_type)
        for rel in collector.discovered_relationships
    }
    assert (
        'eni-1234',
        function['FunctionArn'],
        RelationshipType.ATTACHED_TO
    ) in relationships
    assert (
        'subnet-1234',
        'eni-1234',
        RelationshipType.CONTAINS
    ) in relationships
    assert (
        'sg-1234',
        'eni-1234',
        RelationshipType.ATTACHED_TO
    ) in relationships

    # Ensure the collector declares the new permission requirement
    assert 'ec2:DescribeNetworkInterfaces' in collector.required_permissions
