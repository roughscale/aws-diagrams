"""
AWS authentication and session management for cross-account access.

This module handles authentication to AWS accounts using various methods including
SSO, cross-account role assumption, and credential management for multi-account
topology discovery.
"""

import boto3
import botocore
from botocore.exceptions import ClientError, ProfileNotFound, NoCredentialsError
from dataclasses import dataclass
from typing import Dict, Optional, List, Any
import logging
from datetime import datetime, timedelta
import json
import os


logger = logging.getLogger(__name__)


@dataclass
class AWSCredentials:
    """Container for AWS credentials."""
    access_key_id: str
    secret_access_key: str
    session_token: Optional[str] = None
    expiration: Optional[datetime] = None
    
    def is_expired(self) -> bool:
        """Check if credentials are expired."""
        if self.expiration is None:
            return False
        return datetime.now() >= self.expiration
    
    def to_dict(self) -> Dict[str, str]:
        """Convert to dictionary format for boto3."""
        creds = {
            'aws_access_key_id': self.access_key_id,
            'aws_secret_access_key': self.secret_access_key
        }
        if self.session_token:
            creds['aws_session_token'] = self.session_token
        return creds


@dataclass
class CrossAccountRole:
    """Configuration for cross-account role assumption."""
    account_id: str
    role_name: str
    role_arn: Optional[str] = None
    external_id: Optional[str] = None
    session_name: Optional[str] = None
    
    def __post_init__(self):
        if not self.role_arn:
            self.role_arn = f"arn:aws:iam::{self.account_id}:role/{self.role_name}"
        if not self.session_name:
            self.session_name = f"aws-topology-discovery-{self.account_id}"


class AWSAuthenticationError(Exception):
    """Custom exception for AWS authentication errors."""
    pass


class CrossAccountRoleAssumeError(AWSAuthenticationError):
    """Error when failing to assume cross-account role."""
    pass


class AWSSessionManager:
    """Manages AWS sessions and cross-account role assumptions."""
    
    def __init__(
        self,
        profile_name: Optional[str] = None,
        region: str = "us-east-1",
        session_duration: int = 3600
    ):
        self.profile_name = profile_name
        self.default_region = region
        self.session_duration = session_duration
        self._credential_cache: Dict[str, AWSCredentials] = {}
        self._session_cache: Dict[str, boto3.Session] = {}
        
        # Initialize base session
        self.base_session = self._create_base_session()
        
    def _create_base_session(self) -> boto3.Session:
        """Create the base AWS session for authentication."""
        try:
            if self.profile_name:
                session = boto3.Session(
                    profile_name=self.profile_name,
                    region_name=self.default_region
                )
                logger.info(f"Created session with profile: {self.profile_name}")
            else:
                session = boto3.Session(region_name=self.default_region)
                logger.info("Created session with default credentials")
                
            # Test the session
            sts = session.client('sts')
            identity = sts.get_caller_identity()
            logger.info(f"Authenticated as: {identity.get('Arn', 'Unknown')}")
            
            return session
            
        except ProfileNotFound as e:
            raise AWSAuthenticationError(f"AWS profile not found: {self.profile_name}") from e
        except NoCredentialsError as e:
            raise AWSAuthenticationError("No AWS credentials found") from e
        except ClientError as e:
            raise AWSAuthenticationError(f"Failed to authenticate to AWS: {e}") from e
    
    def get_caller_identity(self) -> Dict[str, Any]:
        """Get caller identity information."""
        try:
            sts = self.base_session.client('sts')
            return sts.get_caller_identity()
        except ClientError as e:
            raise AWSAuthenticationError(f"Failed to get caller identity: {e}") from e
    
    def assume_role(self, role_config: CrossAccountRole) -> AWSCredentials:
        """Assume a cross-account role and return credentials."""
        cache_key = f"{role_config.account_id}:{role_config.role_name}"
        
        # Check cache first
        if cache_key in self._credential_cache:
            cached_creds = self._credential_cache[cache_key]
            if not cached_creds.is_expired():
                logger.debug(f"Using cached credentials for {cache_key}")
                return cached_creds
            else:
                logger.debug(f"Cached credentials expired for {cache_key}")
                del self._credential_cache[cache_key]
        
        try:
            sts = self.base_session.client('sts')
            
            assume_role_args = {
                'RoleArn': role_config.role_arn,
                'RoleSessionName': role_config.session_name,
                'DurationSeconds': self.session_duration
            }
            
            if role_config.external_id:
                assume_role_args['ExternalId'] = role_config.external_id
            
            logger.info(f"Assuming role: {role_config.role_arn}")
            response = sts.assume_role(**assume_role_args)
            
            credentials = response['Credentials']
            aws_creds = AWSCredentials(
                access_key_id=credentials['AccessKeyId'],
                secret_access_key=credentials['SecretAccessKey'],
                session_token=credentials['SessionToken'],
                expiration=credentials['Expiration'].replace(tzinfo=None)
            )
            
            # Cache the credentials
            self._credential_cache[cache_key] = aws_creds
            logger.info(f"Successfully assumed role for account {role_config.account_id}")
            
            return aws_creds
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            if error_code == 'AccessDenied':
                raise CrossAccountRoleAssumeError(
                    f"Access denied when assuming role {role_config.role_arn}. "
                    f"Check role exists and trust policy allows assumption."
                ) from e
            elif error_code == 'InvalidUserID.NotFound':
                raise CrossAccountRoleAssumeError(
                    f"Role not found: {role_config.role_arn}"
                ) from e
            else:
                raise CrossAccountRoleAssumeError(
                    f"Failed to assume role {role_config.role_arn}: {e}"
                ) from e
    
    def get_session_for_account(
        self,
        account_id: str,
        role_name: str = "OrganizationAccountAccessRole",
        region: Optional[str] = None
    ) -> boto3.Session:
        """Get a boto3 session for a specific account."""
        region = region or self.default_region
        cache_key = f"{account_id}:{role_name}:{region}"
        
        # Check session cache
        if cache_key in self._session_cache:
            session = self._session_cache[cache_key]
            # Test if session is still valid by making a simple API call
            try:
                sts = session.client('sts')
                sts.get_caller_identity()
                logger.debug(f"Using cached session for {cache_key}")
                return session
            except ClientError:
                logger.debug(f"Cached session invalid for {cache_key}")
                del self._session_cache[cache_key]
        
        # Create new session
        role_config = CrossAccountRole(
            account_id=account_id,
            role_name=role_name
        )
        
        credentials = self.assume_role(role_config)
        
        session = boto3.Session(
            aws_access_key_id=credentials.access_key_id,
            aws_secret_access_key=credentials.secret_access_key,
            aws_session_token=credentials.session_token,
            region_name=region
        )
        
        # Cache the session
        self._session_cache[cache_key] = session
        logger.info(f"Created new session for account {account_id} in region {region}")
        
        return session
    
    def get_organization_accounts(self) -> List[Dict[str, Any]]:
        """Get list of accounts in the AWS Organization."""
        try:
            org_client = self.base_session.client('organizations')
            paginator = org_client.get_paginator('list_accounts')
            
            accounts = []
            for page in paginator.paginate():
                accounts.extend(page['Accounts'])
            
            logger.info(f"Found {len(accounts)} accounts in organization")
            return accounts
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            if error_code == 'AccessDeniedException':
                logger.warning(
                    "No access to Organizations API. Cannot enumerate accounts automatically."
                )
                return []
            else:
                raise AWSAuthenticationError(
                    f"Failed to list organization accounts: {e}"
                ) from e
    
    def validate_account_access(
        self,
        account_id: str,
        role_name: str = "OrganizationAccountAccessRole"
    ) -> bool:
        """Validate that we can assume role in the specified account."""
        try:
            role_config = CrossAccountRole(
                account_id=account_id,
                role_name=role_name
            )
            credentials = self.assume_role(role_config)
            
            # Test the credentials by getting caller identity
            session = boto3.Session(
                aws_access_key_id=credentials.access_key_id,
                aws_secret_access_key=credentials.secret_access_key,
                aws_session_token=credentials.session_token
            )
            
            sts = session.client('sts')
            identity = sts.get_caller_identity()
            
            assumed_account = identity.get('Account')
            if assumed_account == account_id:
                logger.info(f"Successfully validated access to account {account_id}")
                return True
            else:
                logger.error(
                    f"Account mismatch: expected {account_id}, got {assumed_account}"
                )
                return False
                
        except Exception as e:
            logger.error(f"Failed to validate access to account {account_id}: {e}")
            return False
    
    def clear_cache(self) -> None:
        """Clear all cached credentials and sessions."""
        self._credential_cache.clear()
        self._session_cache.clear()
        logger.info("Cleared authentication cache")
    
    def get_available_regions(self, account_id: Optional[str] = None) -> List[str]:
        """Get list of available AWS regions."""
        try:
            if account_id:
                session = self.get_session_for_account(account_id)
            else:
                session = self.base_session
                
            ec2 = session.client('ec2')
            response = ec2.describe_regions()
            
            regions = [region['RegionName'] for region in response['Regions']]
            logger.debug(f"Found {len(regions)} available regions")
            return sorted(regions)
            
        except ClientError as e:
            logger.error(f"Failed to get available regions: {e}")
            # Return common regions as fallback
            return [
                'us-east-1', 'us-east-2', 'us-west-1', 'us-west-2',
                'eu-west-1', 'eu-west-2', 'eu-central-1',
                'ap-southeast-1', 'ap-southeast-2', 'ap-northeast-1'
            ]


class MultiAccountAuthenticator:
    """High-level authenticator for multi-account operations."""
    
    def __init__(
        self,
        profile_name: Optional[str] = None,
        default_role_name: str = "OrganizationAccountAccessRole",
        session_duration: int = 3600,
        auto_discover_accounts: bool = True
    ):
        self.session_manager = AWSSessionManager(
            profile_name=profile_name,
            session_duration=session_duration
        )
        self.default_role_name = default_role_name
        self.auto_discover_accounts = auto_discover_accounts
        
        self._discovered_accounts: Optional[List[Dict[str, Any]]] = None
        
        if auto_discover_accounts:
            self._discover_accounts()
    
    def _discover_accounts(self) -> None:
        """Discover accounts in the organization."""
        try:
            self._discovered_accounts = self.session_manager.get_organization_accounts()
        except Exception as e:
            logger.warning(f"Could not auto-discover accounts: {e}")
            self._discovered_accounts = []
    
    def get_accounts(self) -> List[Dict[str, Any]]:
        """Get list of organization accounts."""
        if self._discovered_accounts is None:
            self._discover_accounts()
        return self._discovered_accounts or []
    
    def get_authenticated_session(
        self,
        account_id: str,
        region: str,
        role_name: Optional[str] = None
    ) -> boto3.Session:
        """Get authenticated session for specific account and region."""
        role_name = role_name or self.default_role_name
        return self.session_manager.get_session_for_account(
            account_id=account_id,
            role_name=role_name,
            region=region
        )
    
    def validate_multi_account_access(
        self,
        account_ids: List[str],
        role_name: Optional[str] = None
    ) -> Dict[str, bool]:
        """Validate access to multiple accounts."""
        role_name = role_name or self.default_role_name
        results = {}
        
        for account_id in account_ids:
            results[account_id] = self.session_manager.validate_account_access(
                account_id=account_id,
                role_name=role_name
            )
        
        return results
    
    def get_management_account_id(self) -> str:
        """Get the management account ID from caller identity."""
        identity = self.session_manager.get_caller_identity()
        return identity['Account']