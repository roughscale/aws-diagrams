"""
AWS resource collectors for topology discovery.

This package contains collectors for different types of AWS resources,
each implementing the BaseCollector interface for consistent data gathering.
"""

from .base_collector import BaseCollector, CollectionError, RateLimitError
from .vpc_collector import VPCCollector
from .ecs_collector import ECSCollector
from .elbv2_collector import ELBV2Collector
from .lambda_collector import LambdaCollector
from .rds_collector import RDSCollector
from .elasticache_collector import ElastiCacheCollector
from .opensearch_collector import OpenSearchCollector
from .redshift_collector import RedshiftCollector

__all__ = [
    'BaseCollector',
    'CollectionError',
    'RateLimitError',
    'VPCCollector',
    'ECSCollector',
    'ELBV2Collector',
    'LambdaCollector',
    'RDSCollector',
    'ElastiCacheCollector',
    'OpenSearchCollector',
    'RedshiftCollector'
]