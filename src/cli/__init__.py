"""Command-line interface entry points for AWS topology tooling."""

from .main import cli as topology_cli
from .report import cli as report_cli

# Preserve the original name for backwards compatibility
cli = topology_cli

__all__ = [
    "cli",
    "topology_cli",
    "report_cli",
]
