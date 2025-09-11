"""
AWS resource collectors for topology discovery.

This package contains collectors for different types of AWS resources,
each implementing the BaseCollector interface for consistent data gathering.
"""

from .base_collector import BaseCollector, CollectionError, RateLimitError
from .vpc_collector import VPCCollector

__all__ = [
    'BaseCollector',
    'CollectionError', 
    'RateLimitError',
    'VPCCollector'
]