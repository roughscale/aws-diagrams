"""
Main CLI entry point for AWS topology discovery and diagram generation.

This module provides the command-line interface for discovering AWS infrastructure
topology and generating architectural diagrams.
"""

import click
import os
import sys
from pathlib import Path
from typing import Optional, List

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not available, continue without it

# Handle imports for both package and script execution - delay logger import


@click.group()
@click.option(
    '--log-level',
    type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], case_sensitive=False),
    default='INFO',
    help='Set the logging level'
)
@click.option(
    '--log-file',
    type=click.Path(),
    help='Path to log file (optional)'
)
@click.option(
    '--profile',
    default=lambda: os.getenv('AWS_PROFILE'),
    help='AWS profile to use for authentication'
)
@click.option(
    '--region',
    default=lambda: os.getenv('AWS_DEFAULT_REGION', 'us-east-1'),
    help='Default AWS region'
)
@click.pass_context
def cli(ctx, log_level: str, log_file: Optional[str], profile: Optional[str], region: str):
    """AWS Topology Discovery and Diagram Generation Tool."""
    
    # Ensure context object exists
    ctx.ensure_object(dict)
    
    # Import logger module now that we have the log level
    try:
        from ..utils.logger import setup_logging, get_logger
    except ImportError:
        # Add src to path when running as script
        src_path = Path(__file__).parent.parent
        sys.path.insert(0, str(src_path))
        from utils.logger import setup_logging, get_logger

    # Set up logging
    logger = setup_logging(level=log_level, log_file=log_file)
    
    # Store global configuration in context
    ctx.obj['logger'] = logger
    ctx.obj['profile'] = profile or os.getenv('AWS_PROFILE')
    ctx.obj['region'] = region or os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
    ctx.obj['log_level'] = log_level
    
    logger.info(f"AWS Topology Tool starting with profile: {ctx.obj['profile']}, region: {ctx.obj['region']}")


@cli.group()
@click.pass_context
def collect(ctx):
    """Collect AWS infrastructure topology data."""
    pass


@collect.command('account')
@click.option(
    '--account-id',
    required=True,
    help='AWS account ID to collect from'
)
@click.option(
    '--regions',
    multiple=True,
    help='AWS regions to collect from (can be specified multiple times)'
)
@click.option(
    '--vpc-ids',
    multiple=True,
    help='Limit collection to these VPC IDs (can be specified multiple times)'
)
@click.option(
    '--role-name',
    default=lambda: os.getenv('AWS_ORG_ROLE_NAME', 'OrganizationAccountAccessRole'),
    help='Cross-account role name to assume (empty to use current credentials)'
)
@click.option(
    '--output',
    '-o',
    default='topology.yaml',
    help='Output file for topology data'
)
@click.option(
    '--collectors',
    multiple=True,
    help='Specific collectors to run (default: all available)'
)
@click.option(
    '--force',
    is_flag=True,
    help='Overwrite existing topology file'
)
@click.option(
    '--append',
    is_flag=True,
    help='Append/merge into an existing topology file instead of overwriting'
)
@click.option(
    '--direct-access',
    is_flag=True,
    help='Use current AWS credentials directly (no cross-account role assumption)'
)
@click.pass_context
def collect_account(ctx, account_id: str, regions: tuple, role_name: str, 
                   output: str, collectors: tuple, force: bool, append: bool, direct_access: bool,
                   vpc_ids: tuple):
    """Collect topology data from a single AWS account."""
    
    logger = ctx.obj['logger']
    profile = ctx.obj['profile']
    default_region = ctx.obj['region']
    
    # Import here to avoid circular imports
    try:
        from ..collectors.vpc_collector import VPCCollector
        # Optional collectors
        try:
            from ..collectors.ecs_collector import ECSCollector  # type: ignore
        except Exception:
            ECSCollector = None  # type: ignore
        try:
            from ..collectors.elbv2_collector import ELBV2Collector  # type: ignore
        except Exception:
            ELBV2Collector = None  # type: ignore
        try:
            from ..collectors.lambda_collector import LambdaCollector  # type: ignore
        except Exception:
            LambdaCollector = None  # type: ignore
        try:
            from ..collectors.rds_collector import RDSCollector  # type: ignore
        except Exception:
            RDSCollector = None  # type: ignore
        try:
            from ..collectors.elasticache_collector import ElastiCacheCollector  # type: ignore
        except Exception:
            ElastiCacheCollector = None  # type: ignore
        try:
            from ..collectors.opensearch_collector import OpenSearchCollector  # type: ignore
        except Exception:
            OpenSearchCollector = None  # type: ignore
        try:
            from ..collectors.redshift_collector import RedshiftCollector  # type: ignore
        except Exception:
            RedshiftCollector = None  # type: ignore
        from ..auth import MultiAccountAuthenticator
        from ..topology.schema import AWSTopology, TopologyMetadata, OrganizationData
        try:
            from ..topology.serializer import TopologyYAMLSerializer
        except ImportError:
            from topology.serializer import TopologyYAMLSerializer
    except ImportError:
        from collectors.vpc_collector import VPCCollector
        try:
            from collectors.ecs_collector import ECSCollector  # type: ignore
        except Exception:
            ECSCollector = None  # type: ignore
        try:
            from collectors.elbv2_collector import ELBV2Collector  # type: ignore
        except Exception:
            ELBV2Collector = None  # type: ignore
        try:
            from collectors.lambda_collector import LambdaCollector  # type: ignore
        except Exception:
            LambdaCollector = None  # type: ignore
        try:
            from collectors.rds_collector import RDSCollector  # type: ignore
        except Exception:
            RDSCollector = None  # type: ignore
        try:
            from collectors.elasticache_collector import ElastiCacheCollector  # type: ignore
        except Exception:
            ElastiCacheCollector = None  # type: ignore
        try:
            from collectors.opensearch_collector import OpenSearchCollector  # type: ignore
        except Exception:
            OpenSearchCollector = None  # type: ignore
        try:
            from collectors.redshift_collector import RedshiftCollector  # type: ignore
        except Exception:
            RedshiftCollector = None  # type: ignore
        from auth import MultiAccountAuthenticator
        from topology.schema import AWSTopology, TopologyMetadata, OrganizationData
        from topology.serializer import TopologyYAMLSerializer
    from datetime import datetime
    
    logger.info(f"Starting collection for account {account_id}")
    
    # Determine regions to collect from
    if not regions:
        regions = [default_region]
    
    # Check if output file exists
    output_path = Path(output)
    if output_path.exists() and not force and not append:
        logger.error(f"Output file {output} already exists. Use --force to overwrite or --append to merge.")
        sys.exit(1)
    
    try:
        # Check if we should use direct access or cross-account roles
        if direct_access or not role_name or role_name.strip() == "":
            logger.info(f"Using direct access to account {account_id}")
            # Use current credentials directly
            import boto3
            session = boto3.Session(profile_name=profile, region_name=default_region)
            
            # Verify we can access the account
            try:
                sts = session.client('sts')
                identity = sts.get_caller_identity()
                current_account = identity.get('Account')
                
                if current_account != account_id:
                    logger.warning(
                        f"Current credentials are for account {current_account}, "
                        f"but you requested {account_id}. Proceeding anyway..."
                    )
                
                management_account_id = current_account
                logger.info(f"Authenticated as: {identity.get('Arn', 'Unknown')}")
                
            except Exception as e:
                logger.error(f"Failed to authenticate with current credentials: {e}")
                sys.exit(1)
        else:
            # Use cross-account role assumption
            logger.info(f"Using cross-account role assumption for account {account_id}")
            authenticator = MultiAccountAuthenticator(
                profile_name=profile,
                auto_discover_accounts=False  # Disable org discovery
            )
            
            # Validate account access
            logger.info(f"Validating access to account {account_id}")
            access_results = authenticator.validate_multi_account_access([account_id], role_name)
            
            if not access_results.get(account_id, False):
                logger.error(f"Cannot access account {account_id} with role {role_name}")
                sys.exit(1)
            
            # Initialize topology
            management_account_id = authenticator.get_management_account_id()
        
        topology = AWSTopology(
            metadata=TopologyMetadata(
                generated_at=datetime.now(),
                generator_version="0.1.0",
                last_updated=datetime.now()
            ),
            organization=OrganizationData(
                organization_id=None,  # Will be populated if available
                management_account_id=management_account_id
            )
        )
        
        # Add account to topology
        account_data = topology.organization.add_account(account_id)
        
        total_resources = 0
        total_relationships = 0
        total_api_calls = 0
        
        # Collect from each region
        for region in regions:
            logger.info(f"Collecting from region {region}")
            
            # Get authenticated session for this account/region
            if direct_access or not role_name or role_name.strip() == "":
                # Use direct session for this region
                region_session = boto3.Session(profile_name=profile, region_name=region)
            else:
                # Use cross-account session
                region_session = authenticator.get_authenticated_session(account_id, region, role_name)
            
            region_data = account_data.add_region(region)
            
            # Determine which collectors to run
            available_collectors = {
                'vpc': VPCCollector
            }
            if 'ECSCollector' in locals() and ECSCollector:
                available_collectors['ecs'] = ECSCollector  # type: ignore
            if 'ELBV2Collector' in locals() and ELBV2Collector:
                available_collectors['elbv2'] = ELBV2Collector  # type: ignore
            if 'LambdaCollector' in locals() and LambdaCollector:
                available_collectors['lambda'] = LambdaCollector  # type: ignore
            if 'RDSCollector' in locals() and RDSCollector:
                available_collectors['rds'] = RDSCollector  # type: ignore
            if 'ElastiCacheCollector' in locals() and ElastiCacheCollector:
                available_collectors['elasticache'] = ElastiCacheCollector  # type: ignore
            if 'OpenSearchCollector' in locals() and OpenSearchCollector:
                available_collectors['opensearch'] = OpenSearchCollector  # type: ignore
            if 'RedshiftCollector' in locals() and RedshiftCollector:
                available_collectors['redshift'] = RedshiftCollector  # type: ignore
            
            if collectors:
                selected_collectors = {
                    name: cls for name, cls in available_collectors.items() 
                    if name in collectors
                }
            else:
                selected_collectors = available_collectors
            
            # Run collectors. If VPC IDs provided, run VPC collector first to discover subnets,
            # then pass the subnet set to other collectors for filtering.
            ordered = list(selected_collectors.items())
            if 'vpc' in selected_collectors:
                ordered = [('vpc', selected_collectors['vpc'])] + [
                    (k, v) for k, v in selected_collectors.items() if k != 'vpc'
                ]
            collected_subnet_ids: List[str] = []

            for collector_name, collector_class in ordered:
                logger.info(f"Running {collector_name} collector")
                
                collector = collector_class(
                    session=region_session,
                    account_id=account_id,
                    region=region,
                    vpc_ids=list(vpc_ids) if vpc_ids else None,
                    allowed_subnet_ids=collected_subnet_ids if collected_subnet_ids else None
                )
                
                # Run collection
                results = collector.run_collection()
                
                # Add resources to topology
                for resource in collector.collected_resources.values():
                    region_data.add_resource(resource)
                
                # Add relationships
                region_data.relationships.extend(collector.discovered_relationships)
                
                # Update statistics
                total_resources += results['resources_collected']
                total_relationships += results['relationships_discovered']
                total_api_calls += results['api_calls_made']
                
                if results.get('collection_failed', False):
                    logger.warning(f"Collector {collector_name} failed: {results.get('failure_reason')}")

                # After VPC collector, extract the targeted subnet IDs for downstream filtering
                if collector_name == 'vpc' and vpc_ids:
                    try:
                        collected_subnet_ids = [
                            r.resource_id for r in collector.collected_resources.values()
                            if getattr(r, 'resource_type', None) and r.resource_type.value == 'subnet' and
                            r.properties.get('vpc_id') in set(vpc_ids)
                        ]
                    except Exception:
                        collected_subnet_ids = []
        
        # Update topology metadata
        topology.metadata.total_resources = total_resources
        topology.metadata.api_calls_made = total_api_calls
        topology.metadata.last_updated = datetime.now()
        
        # Save or append topology
        serializer = TopologyYAMLSerializer()
        if append and output_path.exists():
            try:
                existing = serializer.load_from_file(output_path)
                # Merge account regions into existing topology
                new_org = topology.organization
                new_acct = new_org.accounts.get(account_id)
                if not new_acct:
                    logger.warning("No account data collected to append. Saving existing file unchanged.")
                else:
                    if account_id in existing.organization.accounts:
                        existing_acct = existing.organization.accounts[account_id]
                        # Merge/replace regions
                        for region_name, region_data in new_acct.regions.items():
                            existing_acct.regions[region_name] = region_data
                        # Extend cross-region relationships
                        existing_acct.cross_region_relationships.extend(new_acct.cross_region_relationships)
                        existing_acct.last_updated = new_acct.last_updated
                    else:
                        # Add whole account if not present
                        existing.organization.accounts[account_id] = new_acct
                    # Merge cross-account relationships
                    existing.organization.cross_account_relationships.extend(new_org.cross_account_relationships)
                    # Recompute simple metadata counters
                    existing.metadata.total_accounts = len(existing.organization.accounts)
                    existing.metadata.total_regions = sum(len(a.regions) for a in existing.organization.accounts.values())
                    existing.metadata.total_resources = sum(len(r.resources) for a in existing.organization.accounts.values() for r in a.regions.values())
                # Save merged topology
                serializer.save_to_file(existing, output_path)
                logger.info(f"Collection completed successfully. Appended data to {output}")
            except Exception as e:
                logger.error(f"Failed to append to existing topology: {e}")
                sys.exit(1)
        else:
            # Overwrite/save fresh topology
            serializer.save_to_file(topology, output_path)
            logger.info(f"Collection completed successfully. Saved to {output}")
        logger.info(f"Statistics: {total_resources} resources, {total_relationships} relationships, {total_api_calls} API calls")
        
    except Exception as e:
        logger.error(f"Collection failed: {e}")
        sys.exit(1)


@collect.command('organization')
@click.option(
    '--regions',
    multiple=True,
    help='AWS regions to collect from (can be specified multiple times)'
)
@click.option(
    '--role-name',
    default='OrganizationAccountAccessRole',
    help='Cross-account role name to assume'
)
@click.option(
    '--output',
    '-o',
    default='organization-topology.yaml',
    help='Output file for topology data'
)
@click.option(
    '--exclude-accounts',
    multiple=True,
    help='Account IDs to exclude from collection'
)
@click.option(
    '--include-accounts',
    multiple=True,
    help='Account IDs to include (if specified, only these accounts will be collected)'
)
@click.option(
    '--force',
    is_flag=True,
    help='Overwrite existing topology file'
)
@click.pass_context
def collect_organization(ctx, regions: tuple, role_name: str, output: str, 
                        exclude_accounts: tuple, include_accounts: tuple, force: bool):
    """Collect topology data from all accounts in the AWS Organization."""
    
    logger = ctx.obj['logger']
    profile = ctx.obj['profile']
    default_region = ctx.obj['region']
    
    logger.info("Starting organization-wide collection")
    
    # Import here to avoid circular imports
    try:
        from ..auth import MultiAccountAuthenticator
    except ImportError:
        from auth import MultiAccountAuthenticator
    
    try:
        # Initialize authenticator and discover accounts
        authenticator = MultiAccountAuthenticator(profile_name=profile)
        accounts = authenticator.get_accounts()
        
        if not accounts:
            logger.error("No accounts found in organization or insufficient permissions")
            sys.exit(1)
        
        # Filter accounts
        if include_accounts:
            accounts = [acc for acc in accounts if acc['Id'] in include_accounts]
        
        if exclude_accounts:
            accounts = [acc for acc in accounts if acc['Id'] not in exclude_accounts]
        
        logger.info(f"Found {len(accounts)} accounts to collect from")
        
        # Collect from each account
        for account in accounts:
            account_id = account['Id']
            account_name = account.get('Name', 'Unknown')
            
            logger.info(f"Processing account {account_id} ({account_name})")
            
            # For now, call the single account collection for each account
            # In a production implementation, this would be parallelized
            
            # TODO: Implement parallel collection and proper organization topology building
            
    except Exception as e:
        logger.error(f"Organization collection failed: {e}")
        sys.exit(1)


@cli.group()
@click.pass_context
def generate(ctx):
    """Generate architectural diagrams from topology data."""
    pass


@generate.command('vpc')
@click.option(
    '--topology',
    '-t',
    default='topology.yaml',
    help='Path to topology YAML file'
)
@click.option(
    '--vpc-id',
    required=True,
    help='VPC ID to generate diagram for'
)
@click.option(
    '--account-id',
    help='Account ID (required if multiple accounts in topology)'
)
@click.option(
    '--region',
    help='Region (required if multiple regions in topology)'
)
@click.option(
    '--output',
    '-o',
    default='vpc-diagram.yaml',
    help='Output file for diagram-as-code'
)
@click.option(
    '--format',
    type=click.Choice(['awslabs', 'd2', 'plantuml'], case_sensitive=False),
    default='awslabs',
    help='Diagram format'
)
@click.pass_context
def generate_vpc_diagram(ctx, topology: str, vpc_id: str, account_id: Optional[str], 
                        region: Optional[str], output: str, format: str):
    """Generate a diagram for a specific VPC."""
    
    logger = ctx.obj['logger']
    
    logger.info(f"Generating {format} diagram for VPC {vpc_id}")
    
    # Import here to avoid circular imports
    try:
        try:
            from ..topology.serializer import TopologyYAMLSerializer
        except ImportError:
            from topology.serializer import TopologyYAMLSerializer
        from ..views import ViewEngine
        from ..transformers import AWSLabsTransformer
    except ImportError:
        from topology.serializer import TopologyYAMLSerializer
        from views import ViewEngine
        from transformers import AWSLabsTransformer
    from pathlib import Path
    
    topology_path = Path(topology)
    if not topology_path.exists():
        logger.error(f"Topology file not found: {topology}")
        sys.exit(1)
    
    try:
        # Load topology
        serializer = TopologyYAMLSerializer()
        topology_obj = serializer.load_from_file(topology_path)
        
        # Create view engine
        view_engine = ViewEngine(topology_obj)
        
        # Create single VPC view
        view = view_engine.create_single_vpc_view(
            vpc_id=vpc_id,
            account_id=account_id,
            region=region
        )
        
        logger.info(f"Created view with {len(view.filtered_resources)} resources")
        
        # Transform based on format
        if format.lower() == 'awslabs':
            transformer = AWSLabsTransformer(view)
            transformer.save_to_file(output)
            logger.info(f"AWS Labs diagram saved to {output}")
        else:
            logger.error(f"Format {format} not yet implemented")
            sys.exit(1)
        
    except Exception as e:
        logger.error(f"Failed to generate VPC diagram: {e}")
        sys.exit(1)


@generate.command('cross-account')
@click.option(
    '--topology',
    '-t',
    default='topology.yaml',
    help='Path to topology YAML file'
)
@click.option(
    '--output',
    '-o',
    default='cross-account-diagram.yaml',
    help='Output file for diagram-as-code'
)
@click.option(
    '--format',
    type=click.Choice(['awslabs', 'd2', 'plantuml'], case_sensitive=False),
    default='awslabs',
    help='Diagram format'
)
@click.pass_context
def generate_cross_account_diagram(ctx, topology: str, output: str, format: str):
    """Generate a cross-account connectivity diagram."""
    
    logger = ctx.obj['logger']
    
    logger.info(f"Generating {format} cross-account connectivity diagram")
    
    # TODO: Implement cross-account diagram generation
    logger.warning("Cross-account diagram generation not yet implemented")


@cli.command()
@click.option(
    '--topology',
    '-t',
    default='topology.yaml',
    help='Path to topology YAML file'
)
@click.pass_context
def validate(ctx, topology: str):
    """Validate a topology YAML file."""
    
    logger = ctx.obj['logger']
    
    topology_path = Path(topology)
    if not topology_path.exists():
        logger.error(f"Topology file not found: {topology}")
        sys.exit(1)
    
    try:
        # Import here to avoid circular imports
        try:
            from ..topology.serializer import TopologyYAMLSerializer
        except ImportError:
            from topology.serializer import TopologyYAMLSerializer
        
        serializer = TopologyYAMLSerializer()
        topology_obj = serializer.load_from_file(topology_path)
        
        # Generate statistics
        stats = topology_obj.get_statistics()
        
        logger.info("Topology validation successful")
        logger.info(f"Statistics: {stats}")
        
    except Exception as e:
        logger.error(f"Topology validation failed: {e}")
        sys.exit(1)


@cli.command()
@click.option(
    '--topology',
    '-t',
    default='topology.yaml',
    help='Path to topology YAML file'
)
@click.pass_context
def stats(ctx, topology: str):
    """Display statistics about a topology file."""
    
    logger = ctx.obj['logger']
    
    topology_path = Path(topology)
    if not topology_path.exists():
        logger.error(f"Topology file not found: {topology}")
        sys.exit(1)
    
    try:
        # Import here to avoid circular imports
        try:
            from ..topology.serializer import TopologyYAMLSerializer
        except ImportError:
            from topology.serializer import TopologyYAMLSerializer
        
        serializer = TopologyYAMLSerializer()
        topology_obj = serializer.load_from_file(topology_path)
        
        # Generate and display statistics
        stats = topology_obj.get_statistics()
        
        click.echo("\n=== Topology Statistics ===")
        click.echo(f"Total Accounts: {stats['total_accounts']}")
        click.echo(f"Total Regions: {stats['total_regions']}")
        click.echo(f"Total Resources: {stats['total_resources']}")
        click.echo(f"Cross-Account Relationships: {stats['cross_account_relationships']}")
        click.echo(f"Last Updated: {stats['last_updated']}")
        
        click.echo("\n=== Resource Counts by Type ===")
        for resource_type, count in stats['resource_counts'].items():
            if count > 0:
                click.echo(f"  {resource_type}: {count}")
        
    except Exception as e:
        logger.error(f"Failed to display statistics: {e}")
        sys.exit(1)


if __name__ == '__main__':
    cli()
