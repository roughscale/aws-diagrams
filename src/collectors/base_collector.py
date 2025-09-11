"""
Base collector class for AWS resource discovery.

This module provides the abstract base class and common functionality
for all AWS resource collectors in the topology discovery system.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Set
import logging
import time
from datetime import datetime
import boto3
from botocore.exceptions import ClientError, BotoCoreError

try:
    from ..topology.schema import (
        BaseResource, ResourceType, ResourceLocation, ResourceMetadata,
        Relationship, RelationshipType
    )
    from ..auth import AWSSessionManager
except ImportError:
    from topology.schema import (
        BaseResource, ResourceType, ResourceLocation, ResourceMetadata,
        Relationship, RelationshipType
    )
    from auth import AWSSessionManager


logger = logging.getLogger(__name__)


class CollectionError(Exception):
    """Exception raised during resource collection."""
    pass


class RateLimitError(CollectionError):
    """Exception raised when API rate limits are hit."""
    pass


class BaseCollector(ABC):
    """
    Abstract base class for AWS resource collectors.
    
    Each collector is responsible for discovering specific types of AWS resources
    and their relationships within a single account and region.
    """
    
    def __init__(
        self,
        session: boto3.Session,
        account_id: str,
        region: str,
        rate_limit_delay: float = 0.1,
        max_retries: int = 3
    ):
        """
        Initialize the collector.
        
        Args:
            session: Authenticated boto3 session for the target account
            account_id: AWS account ID being collected
            region: AWS region being collected
            rate_limit_delay: Delay between API calls to avoid rate limiting
            max_retries: Maximum number of retries for failed API calls
        """
        self.session = session
        self.account_id = account_id
        self.region = region
        self.rate_limit_delay = rate_limit_delay
        self.max_retries = max_retries
        
        self.collected_resources: Dict[str, BaseResource] = {}
        self.discovered_relationships: List[Relationship] = []
        self.collection_errors: List[str] = []
        self.api_calls_made = 0
        
        # Initialize clients dictionary for caching
        self._clients: Dict[str, Any] = {}
        
        # Track collection statistics
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
    
    def get_client(self, service_name: str) -> Any:
        """Get a cached boto3 client for the specified service."""
        if service_name not in self._clients:
            self._clients[service_name] = self.session.client(service_name)
        return self._clients[service_name]
    
    def _rate_limit_delay(self) -> None:
        """Apply rate limiting delay between API calls."""
        if self.rate_limit_delay > 0:
            time.sleep(self.rate_limit_delay)
    
    def _make_api_call(
        self,
        client: Any,
        operation_name: str,
        **kwargs
    ) -> Any:
        """
        Make an API call with retry logic and error handling.
        
        Args:
            client: boto3 client to use
            operation_name: Name of the API operation
            **kwargs: Arguments to pass to the API call
            
        Returns:
            API response
            
        Raises:
            CollectionError: If the API call fails after retries
        """
        self.api_calls_made += 1
        
        for attempt in range(self.max_retries + 1):
            try:
                self._rate_limit_delay()
                
                operation = getattr(client, operation_name)
                response = operation(**kwargs)
                
                logger.debug(
                    f"API call successful: {client._service_model.service_name}:"
                    f"{operation_name} (attempt {attempt + 1})"
                )
                return response
                
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', 'Unknown')
                
                if error_code in ['Throttling', 'RequestLimitExceeded', 'TooManyRequestsException']:
                    if attempt < self.max_retries:
                        delay = min(2 ** attempt, 30)  # Exponential backoff, max 30s
                        logger.warning(
                            f"Rate limited on {operation_name}, retrying in {delay}s "
                            f"(attempt {attempt + 1}/{self.max_retries + 1})"
                        )
                        time.sleep(delay)
                        continue
                    else:
                        raise RateLimitError(
                            f"Rate limit exceeded for {operation_name} after {self.max_retries} retries"
                        ) from e
                
                elif error_code in ['AccessDenied', 'UnauthorizedOperation']:
                    logger.warning(
                        f"Access denied for {operation_name}: {e}. "
                        f"Collector may be missing required permissions."
                    )
                    return None
                    
                elif error_code in ['InvalidParameterValue', 'InvalidParameter']:
                    logger.error(f"Invalid parameter for {operation_name}: {e}")
                    return None
                    
                else:
                    if attempt < self.max_retries:
                        delay = min(2 ** attempt, 10)
                        logger.warning(
                            f"API call failed: {operation_name}, retrying in {delay}s "
                            f"(attempt {attempt + 1}/{self.max_retries + 1}): {e}"
                        )
                        time.sleep(delay)
                        continue
                    else:
                        raise CollectionError(
                            f"API call failed after {self.max_retries} retries: {operation_name}: {e}"
                        ) from e
                        
            except BotoCoreError as e:
                if attempt < self.max_retries:
                    delay = min(2 ** attempt, 10)
                    logger.warning(
                        f"BotoCore error on {operation_name}, retrying in {delay}s "
                        f"(attempt {attempt + 1}/{self.max_retries + 1}): {e}"
                    )
                    time.sleep(delay)
                    continue
                else:
                    raise CollectionError(
                        f"BotoCore error after {self.max_retries} retries: {operation_name}: {e}"
                    ) from e
        
        # Should never reach here
        raise CollectionError(f"Unexpected error in API call: {operation_name}")
    
    def _paginate_api_call(
        self,
        client: Any,
        operation_name: str,
        result_key: str,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Make a paginated API call and return all results.
        
        Args:
            client: boto3 client to use
            operation_name: Name of the API operation
            result_key: Key in response containing the list of results
            **kwargs: Arguments to pass to the API call
            
        Returns:
            List of all results from all pages
        """
        results = []
        paginator = client.get_paginator(operation_name)
        
        try:
            for page in paginator.paginate(**kwargs):
                if result_key in page:
                    results.extend(page[result_key])
                self.api_calls_made += 1
                self._rate_limit_delay()
                
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            if error_code in ['AccessDenied', 'UnauthorizedOperation']:
                logger.warning(
                    f"Access denied for paginated {operation_name}: {e}. "
                    f"Returning partial results."
                )
            else:
                logger.error(f"Error in paginated API call {operation_name}: {e}")
                self.collection_errors.append(
                    f"Paginated API call failed: {operation_name}: {e}"
                )
        
        return results
    
    def create_resource_location(self, availability_zone: Optional[str] = None) -> ResourceLocation:
        """Create a ResourceLocation for the current account and region."""
        return ResourceLocation(
            account_id=self.account_id,
            region=self.region,
            availability_zone=availability_zone
        )
    
    def create_resource_metadata(self, tags: Optional[Dict[str, str]] = None) -> ResourceMetadata:
        """Create ResourceMetadata with current timestamp and collection info."""
        return ResourceMetadata(
            discovered_at=datetime.now(),
            last_updated=datetime.now(),
            api_calls_made=0,  # Will be updated per resource
            collection_errors=[],
            tags=tags or {}
        )
    
    def add_resource(self, resource: BaseResource) -> None:
        """Add a discovered resource to the collection."""
        self.collected_resources[resource.resource_id] = resource
        logger.debug(f"Added resource: {resource.resource_type.value}:{resource.resource_id}")
    
    def add_relationship(self, relationship: Relationship) -> None:
        """Add a discovered relationship to the collection."""
        self.discovered_relationships.append(relationship)
        logger.debug(f"Added relationship: {relationship}")
    
    def get_resource_by_id(self, resource_id: str) -> Optional[BaseResource]:
        """Get a resource by its ID."""
        return self.collected_resources.get(resource_id)
    
    def get_resources_by_type(self, resource_type: ResourceType) -> List[BaseResource]:
        """Get all collected resources of a specific type."""
        return [
            resource for resource in self.collected_resources.values()
            if resource.resource_type == resource_type
        ]
    
    @property
    @abstractmethod
    def supported_resource_types(self) -> Set[ResourceType]:
        """Return the set of resource types this collector can discover."""
        pass
    
    @property
    @abstractmethod
    def required_permissions(self) -> List[str]:
        """Return the list of IAM permissions required by this collector."""
        pass
    
    @abstractmethod
    def collect_resources(self) -> None:
        """
        Collect resources for this account and region.
        
        This method should:
        1. Discover all resources of supported types
        2. Create appropriate resource objects
        3. Add them to the collection using add_resource()
        4. Discover relationships between resources
        5. Add relationships using add_relationship()
        """
        pass
    
    def discover_relationships(self) -> None:
        """
        Discover relationships between collected resources.
        
        This method is called after collect_resources() to identify
        relationships between the discovered resources. Subclasses
        can override this to implement custom relationship discovery.
        """
        # Default implementation - subclasses should override
        pass
    
    def run_collection(self) -> Dict[str, Any]:
        """
        Run the complete collection process.
        
        Returns:
            Dictionary containing collection results and statistics
        """
        self.start_time = datetime.now()
        
        try:
            logger.info(
                f"Starting collection for {self.__class__.__name__} "
                f"in account {self.account_id}, region {self.region}"
            )
            
            # Collect resources
            self.collect_resources()
            
            # Discover relationships
            self.discover_relationships()
            
            self.end_time = datetime.now()
            duration = (self.end_time - self.start_time).total_seconds()
            
            results = {
                'collector_class': self.__class__.__name__,
                'account_id': self.account_id,
                'region': self.region,
                'start_time': self.start_time,
                'end_time': self.end_time,
                'duration_seconds': duration,
                'resources_collected': len(self.collected_resources),
                'relationships_discovered': len(self.discovered_relationships),
                'api_calls_made': self.api_calls_made,
                'errors': self.collection_errors,
                'supported_resource_types': [rt.value for rt in self.supported_resource_types]
            }
            
            logger.info(
                f"Collection completed: {len(self.collected_resources)} resources, "
                f"{len(self.discovered_relationships)} relationships, "
                f"{self.api_calls_made} API calls in {duration:.2f}s"
            )
            
            return results
            
        except Exception as e:
            self.end_time = datetime.now()
            error_msg = f"Collection failed for {self.__class__.__name__}: {e}"
            logger.error(error_msg)
            self.collection_errors.append(error_msg)
            
            return {
                'collector_class': self.__class__.__name__,
                'account_id': self.account_id,
                'region': self.region,
                'start_time': self.start_time,
                'end_time': self.end_time,
                'duration_seconds': (self.end_time - self.start_time).total_seconds() if self.start_time else 0,
                'resources_collected': len(self.collected_resources),
                'relationships_discovered': len(self.discovered_relationships),
                'api_calls_made': self.api_calls_made,
                'errors': self.collection_errors,
                'collection_failed': True,
                'failure_reason': str(e)
            }
    
    def validate_permissions(self) -> Dict[str, bool]:
        """
        Validate that the session has required permissions.
        
        Returns:
            Dictionary mapping permission names to validation results
        """
        # This is a basic implementation - subclasses can override
        # for more specific permission validation
        results = {}
        
        try:
            # Test basic permissions by calling get_caller_identity
            sts = self.get_client('sts')
            identity = sts.get_caller_identity()
            results['basic_access'] = True
            logger.debug(f"Permission validation passed for {identity.get('Arn', 'Unknown')}")
        except Exception as e:
            results['basic_access'] = False
            logger.error(f"Basic permission validation failed: {e}")
        
        return results