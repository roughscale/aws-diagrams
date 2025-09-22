"""
OpenSearch collector for discovering OpenSearch domains.
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


class OpenSearchCollector(BaseCollector):
    """Collector for OpenSearch domains."""

    @property
    def supported_resource_types(self) -> Set[ResourceType]:
        return {ResourceType.OPENSEARCH_DOMAIN}

    @property
    def required_permissions(self) -> List[str]:
        return [
            'opensearch:ListDomainNames',
            'opensearch:DescribeDomain'
        ]

    def collect_resources(self) -> None:
        opensearch = self.get_client('opensearch')
        allowed_vpcs = set(self.vpc_ids) if self.vpc_ids else None

        try:
            # Get list of domain names
            domains_response = opensearch.list_domain_names()
            domain_names = [domain['DomainName'] for domain in domains_response.get('DomainNames', [])]

            # Describe each domain to get detailed information
            for domain_name in domain_names:
                try:
                    domain_response = opensearch.describe_domain(DomainName=domain_name)
                    domain = domain_response['DomainStatus']

                    domain_arn = domain['ARN']

                    # Get VPC configuration
                    vpc_options = domain.get('VPCOptions', {})
                    vpc_id = vpc_options.get('VPCId')
                    subnet_ids = vpc_options.get('SubnetIds', [])
                    security_group_ids = vpc_options.get('SecurityGroupIds', [])

                    # Filter by VPC if specified and domain is in VPC
                    if allowed_vpcs and vpc_id and vpc_id not in allowed_vpcs:
                        continue

                    # Create OpenSearch domain resource
                    resource = BaseResource(
                        resource_id=domain_arn,
                        resource_type=ResourceType.OPENSEARCH_DOMAIN,
                        name=domain_name,
                        location=self.create_resource_location(),
                        properties={
                            'domain_name': domain_name,
                            'engine_version': domain.get('EngineVersion'),
                            'vpc_id': vpc_id,
                            'subnet_ids': subnet_ids,
                            'security_group_ids': security_group_ids,
                            'endpoint': domain.get('Endpoint'),
                            'endpoints': domain.get('Endpoints', {}),
                            'created': domain.get('Created'),
                            'deleted': domain.get('Deleted'),
                            'processing': domain.get('Processing'),
                            'upgrade_processing': domain.get('UpgradeProcessing'),
                            'cluster_config': domain.get('ClusterConfig', {}),
                            'ebs_options': domain.get('EBSOptions', {}),
                            'access_policies': domain.get('AccessPolicies'),
                            'log_publishing_options': domain.get('LogPublishingOptions', {}),
                            'service_software_options': domain.get('ServiceSoftwareOptions', {}),
                            'domain_endpoint_options': domain.get('DomainEndpointOptions', {}),
                            'advanced_security_options': domain.get('AdvancedSecurityOptions', {})
                        },
                        metadata=self.create_resource_metadata(tags={})
                    )
                    self.add_resource(resource)

                except Exception as e:
                    logger.warning(f"Error describing OpenSearch domain {domain_name}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error collecting OpenSearch resources: {e}")
            raise