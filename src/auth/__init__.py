"""
AWS authentication module for cross-account topology discovery.

This module provides authentication and session management capabilities
for accessing AWS resources across multiple accounts in an organization.
"""

from .aws_auth import (
    AWSCredentials,
    CrossAccountRole,
    AWSAuthenticationError,
    CrossAccountRoleAssumeError,
    AWSSessionManager,
    MultiAccountAuthenticator
)

__all__ = [
    'AWSCredentials',
    'CrossAccountRole',
    'AWSAuthenticationError',
    'CrossAccountRoleAssumeError',
    'AWSSessionManager',
    'MultiAccountAuthenticator'
]