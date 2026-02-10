import boto3

from collectors.route53_collector import Route53Collector
from topology.schema import ResourceType, RelationshipType


class FakeRoute53Client:
    def __init__(self):
        self.zone_calls = []
        self._service_model = type("model", (), {"service_name": "route53"})()

    def list_hosted_zones(self, **kwargs):
        return {
            "HostedZones": [
                {
                    "Id": "/hostedzone/Z123456",
                    "Name": "example.com.",
                    "Config": {"PrivateZone": False, "Comment": "demo"},
                    "ResourceRecordSetCount": 2,
                    "CallerReference": "abc",
                }
            ],
            "IsTruncated": False,
        }

    def list_resource_record_sets(self, **kwargs):
        self.zone_calls.append(kwargs.get("HostedZoneId"))
        return {
            "ResourceRecordSets": [
                {
                    "Name": "app.example.com.",
                    "Type": "A",
                    "AliasTarget": {
                        "DNSName": "d12345.cloudfront.net.",
                        "HostedZoneId": "Z2FDTNDATAQYW2",
                        "EvaluateTargetHealth": False,
                    },
                }
            ],
            "IsTruncated": False,
        }

    def list_tags_for_resource(self, **kwargs):
        return {
            "ResourceTagSet": {
                "Tags": [
                    {"Key": "Environment", "Value": "prod"},
                ]
            }
        }


class FakePrivateZoneClient(FakeRoute53Client):
    def __init__(self, associated_vpc_id: str):
        super().__init__()
        self.associated_vpc_id = associated_vpc_id
        self.record_calls = 0

    def list_hosted_zones(self, **kwargs):
        return {
            "HostedZones": [
                {
                    "Id": "/hostedzone/ZPRIVATE",
                    "Name": "internal.example.com.",
                    "Config": {"PrivateZone": True, "Comment": "private"},
                    "ResourceRecordSetCount": 1,
                    "CallerReference": "def",
                }
            ],
            "IsTruncated": False,
        }

    def get_hosted_zone(self, Id):
        return {
            "VPCs": [
                {
                    "VPCId": self.associated_vpc_id,
                    "VPCRegion": "us-east-1",
                }
            ]
        }

    def list_resource_record_sets(self, **kwargs):
        self.record_calls += 1
        return {
            "ResourceRecordSets": [
                {
                    "Name": "service.internal.example.com.",
                    "Type": "A",
                    "ResourceRecords": [{"Value": "10.0.0.5"}],
                }
            ],
            "IsTruncated": False,
        }


def test_route53_collector_discovers_zone_and_records(monkeypatch):
    session = boto3.Session(region_name="us-east-1")
    collector = Route53Collector(
        session=session,
        account_id="123456789012",
        region="aws-global",
    )

    fake_client = FakeRoute53Client()
    monkeypatch.setattr(
        collector.session,
        "client",
        lambda service_name, region_name=None: fake_client,
    )

    collector.collect_resources()

    assert "Z123456" in collector.collected_resources
    zone = collector.collected_resources["Z123456"]
    assert zone.resource_type == ResourceType.ROUTE53_HOSTED_ZONE
    assert zone.properties["name"] == "example.com."
    assert zone.metadata.tags["Environment"] == "prod"

    record_id = "Z123456:app.example.com.:A"
    assert record_id in collector.collected_resources
    record = collector.collected_resources[record_id]
    assert record.resource_type == ResourceType.ROUTE53_RECORD
    assert record.properties["alias_target"]["dns_name"] == "d12345.cloudfront.net."
    assert record.properties["target_dns_names"] == ["d12345.cloudfront.net."]

    relationships = {
        (rel.source_id, rel.target_id, rel.relationship_type)
        for rel in collector.discovered_relationships
    }
    assert (
        "Z123456",
        record_id,
        RelationshipType.CONTAINS,
    ) in relationships

    assert "route53:ListHostedZones" in collector.required_permissions


def test_route53_collector_includes_private_zone_when_vpc_matches(monkeypatch):
    session = boto3.Session(region_name="us-east-1")
    collector = Route53Collector(
        session=session,
        account_id="123456789012",
        region="aws-global",
        vpc_ids=["vpc-123"],
    )

    fake_client = FakePrivateZoneClient("vpc-123")
    monkeypatch.setattr(
        collector.session,
        "client",
        lambda service_name, region_name=None: fake_client,
    )

    collector.collect_resources()

    assert "ZPRIVATE" in collector.collected_resources
    zone = collector.collected_resources["ZPRIVATE"]
    assert zone.properties["associated_vpcs"]
    assert zone.properties["associated_vpcs"][0]["VPCId"] == "vpc-123"


def test_route53_collector_skips_private_zone_without_matching_vpc(monkeypatch):
    session = boto3.Session(region_name="us-east-1")
    collector = Route53Collector(
        session=session,
        account_id="123456789012",
        region="aws-global",
        vpc_ids=["vpc-999"],
    )

    fake_client = FakePrivateZoneClient("vpc-123")
    monkeypatch.setattr(
        collector.session,
        "client",
        lambda service_name, region_name=None: fake_client,
    )

    collector.collect_resources()

    assert "ZPRIVATE" not in collector.collected_resources
    assert fake_client.record_calls == 0
