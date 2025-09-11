"""
View engine and filtering for topology subsets.

This package provides capabilities for creating filtered views of AWS topology
based on various criteria and transforming them for diagram generation.
"""

from .view_engine import (
    ViewEngine, TopologyView, ViewDefinition, ViewFilter, FilterType
)

__all__ = [
    'ViewEngine',
    'TopologyView', 
    'ViewDefinition',
    'ViewFilter',
    'FilterType'
]