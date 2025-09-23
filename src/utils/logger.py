"""
Logging configuration and utilities for AWS topology discovery.

This module provides centralized logging configuration with support for
different log levels, file output, and structured logging for better
debugging and monitoring.
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
import json


class StructuredFormatter(logging.Formatter):
    """Custom formatter that outputs structured log data."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as structured JSON."""
        log_data = {
            'timestamp': datetime.fromtimestamp(record.created).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        # Add extra fields if present
        if hasattr(record, 'extra_fields'):
            log_data.update(record.extra_fields)
        
        return json.dumps(log_data, default=str)


class ContextFilter(logging.Filter):
    """Filter that adds context information to log records."""
    
    def __init__(self, context: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.context = context or {}
    
    def filter(self, record: logging.LogRecord) -> bool:
        """Add context information to the log record."""
        if self.context:
            if not hasattr(record, 'extra_fields'):
                record.extra_fields = {}
            record.extra_fields.update(self.context)
        return True


class TopologyLogger:
    """Main logging class for AWS topology discovery."""
    
    def __init__(
        self,
        name: str = "aws-topology",
        level: str = "INFO",
        log_file: Optional[Path] = None,
        structured: bool = False,
        max_file_size: int = 10 * 1024 * 1024,  # 10MB
        backup_count: int = 5
    ):
        """
        Initialize the logger.
        
        Args:
            name: Logger name
            level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            log_file: Path to log file (optional)
            structured: Whether to use structured JSON logging
            max_file_size: Maximum log file size before rotation
            backup_count: Number of backup files to keep
        """
        self.name = name
        self.level = getattr(logging, level.upper())
        self.log_file = log_file
        self.structured = structured
        self.max_file_size = max_file_size
        self.backup_count = backup_count
        
        self.logger = logging.getLogger(name)
        self.logger.setLevel(self.level)
        
        # Clear any existing handlers
        self.logger.handlers.clear()
        
        self._setup_handlers()
    
    def _setup_handlers(self) -> None:
        """Set up logging handlers."""
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(self.level)
        
        if self.structured:
            console_formatter = StructuredFormatter()
        else:
            console_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
        
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)
        
        # File handler (if specified)
        if self.log_file:
            # Ensure log directory exists
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            
            file_handler = logging.handlers.RotatingFileHandler(
                self.log_file,
                maxBytes=self.max_file_size,
                backupCount=self.backup_count
            )
            file_handler.setLevel(self.level)
            
            if self.structured:
                file_formatter = StructuredFormatter()
            else:
                file_formatter = logging.Formatter(
                    '%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(funcName)s:%(lineno)d - %(message)s'
                )
            
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)
    
    def add_context_filter(self, context: Dict[str, Any]) -> None:
        """Add context information to all log messages."""
        context_filter = ContextFilter(context)
        for handler in self.logger.handlers:
            handler.addFilter(context_filter)
    
    def get_logger(self) -> logging.Logger:
        """Get the configured logger instance."""
        return self.logger


class CollectionLogger:
    """Logger specifically for collection operations with context."""
    
    def __init__(
        self,
        account_id: str,
        region: str,
        collector_name: str,
        base_logger: Optional[logging.Logger] = None
    ):
        """
        Initialize collection logger with context.
        
        Args:
            account_id: AWS account ID being collected
            region: AWS region being collected
            collector_name: Name of the collector class
            base_logger: Base logger to use (creates new if None)
        """
        self.account_id = account_id
        self.region = region
        self.collector_name = collector_name
        
        if base_logger:
            self.logger = base_logger
        else:
            self.logger = logging.getLogger("aws-topology.collection")
        
        # Add context to all log messages
        self.context = {
            'account_id': account_id,
            'region': region,
            'collector': collector_name
        }
    
    def debug(self, message: str, **extra) -> None:
        """Log debug message with collection context."""
        self._log(logging.DEBUG, message, extra)
    
    def info(self, message: str, **extra) -> None:
        """Log info message with collection context."""
        self._log(logging.INFO, message, extra)
    
    def warning(self, message: str, **extra) -> None:
        """Log warning message with collection context."""
        self._log(logging.WARNING, message, extra)
    
    def error(self, message: str, **extra) -> None:
        """Log error message with collection context."""
        self._log(logging.ERROR, message, extra)
    
    def critical(self, message: str, **extra) -> None:
        """Log critical message with collection context."""
        self._log(logging.CRITICAL, message, extra)
    
    def _log(self, level: int, message: str, extra: Dict[str, Any]) -> None:
        """Internal method to log with context."""
        # Create a LogRecord with extra context
        record = self.logger.makeRecord(
            self.logger.name, level, "", 0, message, (), None
        )
        
        # Add context and extra fields
        if not hasattr(record, 'extra_fields'):
            record.extra_fields = {}
        record.extra_fields.update(self.context)
        record.extra_fields.update(extra)
        
        self.logger.handle(record)
    
    def log_api_call(
        self,
        service: str,
        operation: str,
        success: bool,
        duration_ms: float,
        error: Optional[str] = None
    ) -> None:
        """Log an API call with timing and success information."""
        extra = {
            'api_service': service,
            'api_operation': operation,
            'success': success,
            'duration_ms': duration_ms
        }
        
        if error:
            extra['error'] = error
        
        if success:
            self.debug(f"API call successful: {service}:{operation} ({duration_ms:.2f}ms)", **extra)
        else:
            self.error(f"API call failed: {service}:{operation} - {error}", **extra)
    
    def log_resource_collected(
        self,
        resource_type: str,
        resource_id: str,
        resource_name: Optional[str] = None
    ) -> None:
        """Log successful resource collection."""
        extra = {
            'resource_type': resource_type,
            'resource_id': resource_id
        }
        
        if resource_name:
            extra['resource_name'] = resource_name
        
        self.debug(f"Collected {resource_type}: {resource_id}", **extra)
    
    def log_collection_summary(
        self,
        resources_collected: int,
        relationships_discovered: int,
        api_calls_made: int,
        duration_seconds: float,
        errors: int = 0
    ) -> None:
        """Log collection summary statistics."""
        extra = {
            'resources_collected': resources_collected,
            'relationships_discovered': relationships_discovered,
            'api_calls_made': api_calls_made,
            'duration_seconds': duration_seconds,
            'errors': errors
        }
        
        message = (
            f"Collection completed: {resources_collected} resources, "
            f"{relationships_discovered} relationships, "
            f"{api_calls_made} API calls in {duration_seconds:.2f}s"
        )
        
        if errors > 0:
            message += f" with {errors} errors"
            self.warning(message, **extra)
        else:
            self.info(message, **extra)


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    structured: bool = False
) -> logging.Logger:
    """
    Set up logging for the application.
    
    Args:
        level: Logging level
        log_file: Path to log file (optional)
        structured: Whether to use structured JSON logging
        
    Returns:
        Configured logger instance
    """
    # Get configuration from environment variables
    level = os.getenv('LOG_LEVEL', level).upper()
    log_file_path = Path(os.getenv('LOG_FILE', log_file)) if log_file or os.getenv('LOG_FILE') else None
    structured = os.getenv('LOG_STRUCTURED', str(structured)).lower() == 'true'
    
    # Create topology logger
    topology_logger = TopologyLogger(
        name="aws-topology",
        level=level,
        log_file=log_file_path,
        structured=structured
    )

    # Also set the root logger level to ensure all child loggers inherit the level
    logging.getLogger().setLevel(getattr(logging, level))

    return topology_logger.get_logger()


def get_collection_logger(
    account_id: str,
    region: str,
    collector_name: str
) -> CollectionLogger:
    """
    Get a collection logger with context.
    
    Args:
        account_id: AWS account ID
        region: AWS region
        collector_name: Name of the collector
        
    Returns:
        Collection logger with context
    """
    base_logger = logging.getLogger("aws-topology")
    return CollectionLogger(account_id, region, collector_name, base_logger)


# Configure logging for the module
_logger = setup_logging()


def get_logger(name: str = "") -> logging.Logger:
    """Get a logger for the specified name."""
    if name:
        return logging.getLogger(f"aws-topology.{name}")
    return logging.getLogger("aws-topology")