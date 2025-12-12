"""CloudFront collector for discovering distributions."""

from typing import Set, List, Dict, Any, Optional

try:
    from .base_collector import BaseCollector, logger
    from ..topology.schema import BaseResource, ResourceLocation, ResourceType
except ImportError:
    from collectors.base_collector import BaseCollector, logger
    from topology.schema import BaseResource, ResourceLocation, ResourceType


class CloudFrontCollector(BaseCollector):
    """Collector for global CloudFront distributions."""

    GLOBAL_REGION = "aws-global"
    CONTROL_PLANE_REGION = "us-east-1"
    EDGE_REGION = "aws-global"

    @property
    def supported_resource_types(self) -> Set[ResourceType]:
        return {ResourceType.CLOUDFRONT_DISTRIBUTION}

    @property
    def required_permissions(self) -> List[str]:
        return [
            "cloudfront:ListDistributions",
            "cloudfront:GetDistribution",
        ]

    def collect_resources(self) -> None:
        # CloudFront is a global service exposed via us-east-1 endpoints.
        if self.region != self.GLOBAL_REGION:
            logger.info(
                "CloudFront is global; skipping regional collection for %s", self.region
            )
            return

        client = self.session.client(
            "cloudfront", region_name=self.CONTROL_PLANE_REGION
        )
        marker: Optional[str] = None

        while True:
            kwargs: Dict[str, Any] = {}
            if marker:
                kwargs["Marker"] = marker

            response = self._make_api_call(client, "list_distributions", **kwargs)
            if not response:
                break

            distribution_list = response.get("DistributionList") or {}
            for summary in distribution_list.get("Items") or []:
                self._handle_distribution(summary)

            if not distribution_list.get("IsTruncated"):
                break
            marker = distribution_list.get("NextMarker")

    def _handle_distribution(self, summary: Dict[str, Any]) -> None:
        distribution_id = summary.get("Id")
        if not distribution_id:
            return

        arn = summary.get("ARN") or f"arn:aws:cloudfront::{self.account_id}:distribution/{distribution_id}"
        aliases = summary.get("Aliases", {}).get("Items") or []
        origins = summary.get("Origins", {}).get("Items") or []

        tags: Dict[str, str] = {}
        comment = summary.get("Comment")
        if comment:
            tags["aws:cloudfront:comment"] = comment

        location = ResourceLocation(
            account_id=self.account_id,
            region=self.EDGE_REGION,
        )
        metadata = self.create_resource_metadata(tags=tags)

        properties: Dict[str, Any] = {
            "status": summary.get("Status"),
            "enabled": summary.get("Enabled"),
            "domain_name": summary.get("DomainName"),
            "aliases": aliases,
            "origins": [
                {
                    "id": origin.get("Id"),
                    "domain_name": origin.get("DomainName"),
                    "origin_path": origin.get("OriginPath"),
                    "type": self._infer_origin_type(origin),
                }
                for origin in origins
            ],
            "default_cache_behavior": summary.get("DefaultCacheBehavior"),
        }

        resource = BaseResource(
            resource_id=distribution_id,
            resource_type=ResourceType.CLOUDFRONT_DISTRIBUTION,
            name=aliases[0] if aliases else summary.get("DomainName"),
            arn=arn,
            location=location,
            metadata=metadata,
            properties=properties,
        )
        self.add_resource(resource)

    @staticmethod
    def _infer_origin_type(origin: Dict[str, Any]) -> Optional[str]:
        if not origin:
            return None
        if "S3OriginConfig" in origin:
            return "s3"
        if "CustomOriginConfig" in origin:
            domain = (origin.get("DomainName") or "").lower()
            if "cloudfront.net" in domain:
                return "cloudfront"
            if "elb.amazonaws.com" in domain:
                return "load_balancer"
            return "custom"
        return None
