"""
VPC collector for discovering VPC resources and their relationships.

This collector discovers VPCs, subnets, route tables, internet gateways,
NAT gateways, security groups, and their interconnections.
"""

from typing import Set, List, Dict, Any, Optional
import logging

try:
    from .base_collector import BaseCollector
    from ..topology.schema import (
        ResourceType,
        NetworkResource,
        BaseResource,
        Relationship,
        RelationshipType,
        create_vpc_resource,
    )
except ImportError:
    from collectors.base_collector import BaseCollector
    from topology.schema import (
        ResourceType,
        NetworkResource,
        BaseResource,
        Relationship,
        RelationshipType,
        create_vpc_resource,
    )

logger = logging.getLogger(__name__)


class VPCCollector(BaseCollector):
    """Collector for VPC-related resources."""

    @property
    def supported_resource_types(self) -> Set[ResourceType]:
        """Return the set of resource types this collector can discover."""
        return {
            ResourceType.VPC,
            ResourceType.SUBNET,
            ResourceType.SECURITY_GROUP,
            ResourceType.NETWORK_ACL,
            ResourceType.ROUTE_TABLE,
            ResourceType.INTERNET_GATEWAY,
            ResourceType.NAT_GATEWAY,
            ResourceType.VPC_ENDPOINT,
            ResourceType.TRANSIT_GATEWAY,
            ResourceType.VPC_PEERING,
            ResourceType.NETWORK_INTERFACE,
        }

    @property
    def required_permissions(self) -> List[str]:
        """Return the list of IAM permissions required by this collector."""
        return [
            "ec2:DescribeVpcs",
            "ec2:DescribeSubnets",
            "ec2:DescribeSecurityGroups",
            "ec2:DescribeNetworkAcls",
            "ec2:DescribeRouteTables",
            "ec2:DescribeInternetGateways",
            "ec2:DescribeNatGateways",
            "ec2:DescribeVpcEndpoints",
            "ec2:DescribeVpcEndpointServices",
            "ec2:DescribeVpcPeeringConnections",
            "ec2:DescribeTransitGateways",
            "ec2:DescribeTransitGatewayAttachments",
            "ec2:DescribeTransitGatewayVpcAttachments",
            "ec2:DescribeNetworkInterfaces",
            "ec2:DescribeTags",
        ]

    def collect_resources(self) -> None:
        """Collect VPC-related resources."""
        ec2 = self.get_client("ec2")

        # Collect VPCs first as they are the parent resources
        self._collect_vpcs(ec2)

        # Collect VPC-dependent resources
        self._collect_subnets(ec2)
        self._collect_security_groups(ec2)
        self._collect_network_acls(ec2)
        self._collect_route_tables(ec2)
        self._collect_internet_gateways(ec2)
        self._collect_nat_gateways(ec2)
        self._collect_vpc_endpoints(ec2)
        self._collect_transit_gateways(ec2)
        self._collect_transit_gateway_attachments(ec2)
        self._collect_transit_gateway_vpc_attachments(ec2)
        self._collect_vpc_peering_connections(ec2)
        self._collect_network_interfaces(ec2)

    def _collect_network_interfaces(self, ec2_client: Any) -> None:
        """Collect Elastic Network Interfaces (ENIs) and attach to subnets/VPCs."""
        try:
            paginator = ec2_client.get_paginator("describe_network_interfaces")
            paginate_kwargs: Dict[str, Any] = {}
            if getattr(self, "vpc_ids", None):
                paginate_kwargs["Filters"] = [
                    {"Name": "vpc-id", "Values": list(self.vpc_ids)}
                ]
            for page in paginator.paginate(**paginate_kwargs):
                for ni in page.get("NetworkInterfaces", []):
                    eni_id = ni["NetworkInterfaceId"]
                    subnet_id = ni.get("SubnetId")
                    vpc_id = ni.get("VpcId")
                    interface_type = ni.get("InterfaceType")
                    status = ni.get("Status")
                    private_ip = ni.get("PrivateIpAddress")
                    description = ni.get("Description")
                    groups = [g.get("GroupId") for g in ni.get("Groups", [])]
                    attachment = ni.get("Attachment", {})
                    association = ni.get("Association", {})

                    tags = {}
                    tags_list = ni.get("TagSet", []) or ni.get("Tags", []) or []
                    if not tags_list:
                        try:
                            tag_resp = self._make_api_call(
                                ec2_client,
                                "describe_tags",
                                Filters=[{"Name": "resource-id", "Values": [eni_id]}],
                            )
                            if tag_resp:
                                tags_list = tag_resp.get("Tags", []) or []
                        except Exception as tag_error:
                            logger.debug(
                                f"Could not retrieve tags for ENI {eni_id}: {tag_error}"
                            )
                    name = None
                    for tag in tags_list:
                        if tag.get("Key") == "Name":
                            name = tag.get("Value")
                        tags[tag.get("Key")] = tag.get("Value")

                    location = self.create_resource_location()
                    metadata = self.create_resource_metadata(tags=tags)

                    eni_resource = BaseResource(
                        resource_id=eni_id,
                        resource_type=ResourceType.NETWORK_INTERFACE,
                        name=name or description,
                        arn=f"arn:aws:ec2:{self.region}:{self.account_id}:network-interface/{eni_id}",
                        location=location,
                        metadata=metadata,
                        properties={
                            "subnet_id": subnet_id,
                            "vpc_id": vpc_id,
                            "interface_type": interface_type,
                            "status": status,
                            "description": description,
                            "private_ip": private_ip,
                            "security_group_ids": groups,
                            "attachment": attachment,
                            "association": association,
                        },
                    )

                    self.add_resource(eni_resource)

                    if subnet_id:
                        self.add_relationship(
                            Relationship(
                                source_id=subnet_id,
                                target_id=eni_id,
                                relationship_type=RelationshipType.CONTAINS,
                            )
                        )

                    lambda_arn = tags.get("aws:lambda:functionArn")
                    if lambda_arn:
                        self.add_relationship(
                            Relationship(
                                source_id=eni_id,
                                target_id=lambda_arn,
                                relationship_type=RelationshipType.ATTACHED_TO,
                            )
                        )
        except Exception as e:
            error_msg = f"Failed to collect Network Interfaces: {e}"
            logger.error(error_msg)
            self.collection_errors.append(error_msg)

    def _collect_vpcs(self, ec2_client: Any) -> None:
        """Collect VPC resources."""
        try:
            kwargs: Dict[str, Any] = {}
            if getattr(self, "vpc_ids", None):
                kwargs["VpcIds"] = list(self.vpc_ids)
            response = self._make_api_call(ec2_client, "describe_vpcs", **kwargs)
            if not response:
                return

            for vpc_data in response.get("Vpcs", []):
                vpc_id = vpc_data["VpcId"]
                cidr_block = vpc_data["CidrBlock"]

                # Extract name from tags
                name = None
                tags = {}
                for tag in vpc_data.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                    tags[tag["Key"]] = tag["Value"]

                # Create VPC resource
                location = self.create_resource_location()
                metadata = self.create_resource_metadata(tags=tags)

                vpc_resource = NetworkResource(
                    resource_id=vpc_id,
                    resource_type=ResourceType.VPC,
                    name=name,
                    arn=f"arn:aws:ec2:{self.region}:{self.account_id}:vpc/{vpc_id}",
                    location=location,
                    metadata=metadata,
                    cidr_blocks=[cidr_block],
                    properties={
                        "state": vpc_data.get("State"),
                        "is_default": vpc_data.get("IsDefault", False),
                        "dhcp_options_id": vpc_data.get("DhcpOptionsId"),
                        "instance_tenancy": vpc_data.get("InstanceTenancy"),
                        "additional_cidr_blocks": [
                            assoc["CidrBlock"]
                            for assoc in vpc_data.get("CidrBlockAssociationSet", [])
                            if assoc.get("CidrBlockState", {}).get("State")
                            == "associated"
                        ],
                    },
                )

                self.add_resource(vpc_resource)
                logger.debug(f"Collected VPC: {vpc_id} ({name or 'unnamed'})")

        except Exception as e:
            error_msg = f"Failed to collect VPCs: {e}"
            logger.error(error_msg)
            self.collection_errors.append(error_msg)

    def _collect_subnets(self, ec2_client: Any) -> None:
        """Collect subnet resources."""
        try:
            kwargs: Dict[str, Any] = {}
            if getattr(self, "vpc_ids", None):
                kwargs["Filters"] = [{"Name": "vpc-id", "Values": list(self.vpc_ids)}]
            response = self._make_api_call(ec2_client, "describe_subnets", **kwargs)
            if not response:
                return

            for subnet_data in response.get("Subnets", []):
                subnet_id = subnet_data["SubnetId"]
                vpc_id = subnet_data["VpcId"]
                cidr_block = subnet_data["CidrBlock"]
                az = subnet_data["AvailabilityZone"]

                # Extract name from tags
                name = None
                tags = {}
                for tag in subnet_data.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                    tags[tag["Key"]] = tag["Value"]

                # Create subnet resource
                location = self.create_resource_location(availability_zone=az)
                metadata = self.create_resource_metadata(tags=tags)

                subnet_resource = NetworkResource(
                    resource_id=subnet_id,
                    resource_type=ResourceType.SUBNET,
                    name=name,
                    arn=f"arn:aws:ec2:{self.region}:{self.account_id}:subnet/{subnet_id}",
                    location=location,
                    metadata=metadata,
                    cidr_blocks=[cidr_block],
                    properties={
                        "vpc_id": vpc_id,
                        "availability_zone": az,
                        "availability_zone_id": subnet_data.get("AvailabilityZoneId"),
                        "state": subnet_data.get("State"),
                        "map_public_ip_on_launch": subnet_data.get(
                            "MapPublicIpOnLaunch", False
                        ),
                        "assign_ipv6_address_on_creation": subnet_data.get(
                            "AssignIpv6AddressOnCreation", False
                        ),
                        "available_ip_address_count": subnet_data.get(
                            "AvailableIpAddressCount", 0
                        ),
                    },
                )

                self.add_resource(subnet_resource)

                # Create relationship to VPC
                vpc_relationship = Relationship(
                    source_id=vpc_id,
                    target_id=subnet_id,
                    relationship_type=RelationshipType.CONTAINS,
                )
                self.add_relationship(vpc_relationship)

                logger.debug(f"Collected subnet: {subnet_id} in VPC {vpc_id}")

        except Exception as e:
            error_msg = f"Failed to collect subnets: {e}"
            logger.error(error_msg)
            self.collection_errors.append(error_msg)

    def _collect_security_groups(self, ec2_client: Any) -> None:
        """Collect security group resources."""
        try:
            kwargs: Dict[str, Any] = {}
            if getattr(self, "vpc_ids", None):
                kwargs["Filters"] = [{"Name": "vpc-id", "Values": list(self.vpc_ids)}]
            response = self._make_api_call(
                ec2_client, "describe_security_groups", **kwargs
            )
            if not response:
                return

            for sg_data in response.get("SecurityGroups", []):
                sg_id = sg_data["GroupId"]
                vpc_id = sg_data.get("VpcId")

                # Extract name and tags
                name = sg_data.get("GroupName")
                tags = {}
                for tag in sg_data.get("Tags", []):
                    tags[tag["Key"]] = tag["Value"]

                # Create security group resource
                location = self.create_resource_location()
                metadata = self.create_resource_metadata(tags=tags)

                sg_resource = BaseResource(
                    resource_id=sg_id,
                    resource_type=ResourceType.SECURITY_GROUP,
                    name=name,
                    arn=f"arn:aws:ec2:{self.region}:{self.account_id}:security-group/{sg_id}",
                    location=location,
                    metadata=metadata,
                    properties={
                        "group_name": name,
                        "description": sg_data.get("Description"),
                        "vpc_id": vpc_id,
                        "owner_id": sg_data.get("OwnerId"),
                        "ingress_rules": sg_data.get("IpPermissions", []),
                        "egress_rules": sg_data.get("IpPermissionsEgress", []),
                    },
                )

                self.add_resource(sg_resource)

                # Create relationship to VPC if it exists
                if vpc_id and vpc_id in self.collected_resources:
                    vpc_relationship = Relationship(
                        source_id=vpc_id,
                        target_id=sg_id,
                        relationship_type=RelationshipType.CONTAINS,
                    )
                    self.add_relationship(vpc_relationship)

                logger.debug(f"Collected security group: {sg_id} ({name})")

        except Exception as e:
            error_msg = f"Failed to collect security groups: {e}"
            logger.error(error_msg)
            self.collection_errors.append(error_msg)

    def _collect_network_acls(self, ec2_client: Any) -> None:
        """Collect Network ACL resources."""
        try:
            kwargs: Dict[str, Any] = {}
            if getattr(self, "vpc_ids", None):
                kwargs["Filters"] = [{"Name": "vpc-id", "Values": list(self.vpc_ids)}]
            response = self._make_api_call(
                ec2_client, "describe_network_acls", **kwargs
            )
            if not response:
                return

            for nacl_data in response.get("NetworkAcls", []):
                nacl_id = nacl_data["NetworkAclId"]
                vpc_id = nacl_data["VpcId"]

                # Extract name from tags
                name = None
                tags = {}
                for tag in nacl_data.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                    tags[tag["Key"]] = tag["Value"]

                # Create network ACL resource
                location = self.create_resource_location()
                metadata = self.create_resource_metadata(tags=tags)

                nacl_resource = BaseResource(
                    resource_id=nacl_id,
                    resource_type=ResourceType.NETWORK_ACL,
                    name=name,
                    arn=f"arn:aws:ec2:{self.region}:{self.account_id}:network-acl/{nacl_id}",
                    location=location,
                    metadata=metadata,
                    properties={
                        "vpc_id": vpc_id,
                        "is_default": nacl_data.get("IsDefault", False),
                        "entries": nacl_data.get("Entries", []),
                        "associations": nacl_data.get("Associations", []),
                    },
                )

                self.add_resource(nacl_resource)

                # Create relationship to VPC
                vpc_relationship = Relationship(
                    source_id=vpc_id,
                    target_id=nacl_id,
                    relationship_type=RelationshipType.CONTAINS,
                )
                self.add_relationship(vpc_relationship)

                logger.debug(f"Collected Network ACL: {nacl_id}")

        except Exception as e:
            error_msg = f"Failed to collect Network ACLs: {e}"
            logger.error(error_msg)
            self.collection_errors.append(error_msg)

    def _collect_route_tables(self, ec2_client: Any) -> None:
        """Collect route table resources."""
        try:
            kwargs: Dict[str, Any] = {}
            if getattr(self, "vpc_ids", None):
                kwargs["Filters"] = [{"Name": "vpc-id", "Values": list(self.vpc_ids)}]
            response = self._make_api_call(
                ec2_client, "describe_route_tables", **kwargs
            )
            if not response:
                return

            for rt_data in response.get("RouteTables", []):
                rt_id = rt_data["RouteTableId"]
                vpc_id = rt_data["VpcId"]

                # Extract name from tags
                name = None
                tags = {}
                for tag in rt_data.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                    tags[tag["Key"]] = tag["Value"]

                # Create route table resource
                location = self.create_resource_location()
                metadata = self.create_resource_metadata(tags=tags)

                rt_resource = BaseResource(
                    resource_id=rt_id,
                    resource_type=ResourceType.ROUTE_TABLE,
                    name=name,
                    arn=f"arn:aws:ec2:{self.region}:{self.account_id}:route-table/{rt_id}",
                    location=location,
                    metadata=metadata,
                    properties={
                        "vpc_id": vpc_id,
                        "routes": rt_data.get("Routes", []),
                        "associations": rt_data.get("Associations", []),
                        "propagating_vgws": rt_data.get("PropagatingVgws", []),
                    },
                )

                self.add_resource(rt_resource)

                # Create relationship to VPC
                vpc_relationship = Relationship(
                    source_id=vpc_id,
                    target_id=rt_id,
                    relationship_type=RelationshipType.CONTAINS,
                )
                self.add_relationship(vpc_relationship)

                logger.debug(f"Collected route table: {rt_id}")

        except Exception as e:
            error_msg = f"Failed to collect route tables: {e}"
            logger.error(error_msg)
            self.collection_errors.append(error_msg)

    def _collect_internet_gateways(self, ec2_client: Any) -> None:
        """Collect Internet Gateway resources."""
        try:
            kwargs: Dict[str, Any] = {}
            if getattr(self, "vpc_ids", None):
                kwargs["Filters"] = [
                    {"Name": "attachment.vpc-id", "Values": list(self.vpc_ids)}
                ]
            response = self._make_api_call(
                ec2_client, "describe_internet_gateways", **kwargs
            )
            if not response:
                return

            for igw_data in response.get("InternetGateways", []):
                igw_id = igw_data["InternetGatewayId"]

                # Extract name from tags
                name = None
                tags = {}
                for tag in igw_data.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                    tags[tag["Key"]] = tag["Value"]

                # Create internet gateway resource
                location = self.create_resource_location()
                metadata = self.create_resource_metadata(tags=tags)

                igw_resource = BaseResource(
                    resource_id=igw_id,
                    resource_type=ResourceType.INTERNET_GATEWAY,
                    name=name,
                    arn=f"arn:aws:ec2:{self.region}:{self.account_id}:internet-gateway/{igw_id}",
                    location=location,
                    metadata=metadata,
                    properties={"attachments": igw_data.get("Attachments", [])},
                )

                self.add_resource(igw_resource)

                # Create relationships to attached VPCs
                for attachment in igw_data.get("Attachments", []):
                    vpc_id = attachment.get("VpcId")
                    if vpc_id and attachment.get("State") == "available":
                        attachment_relationship = Relationship(
                            source_id=igw_id,
                            target_id=vpc_id,
                            relationship_type=RelationshipType.ATTACHED_TO,
                            properties={"state": attachment.get("State")},
                        )
                        self.add_relationship(attachment_relationship)

                logger.debug(f"Collected Internet Gateway: {igw_id}")

        except Exception as e:
            error_msg = f"Failed to collect Internet Gateways: {e}"
            logger.error(error_msg)
            self.collection_errors.append(error_msg)

    def _collect_nat_gateways(self, ec2_client: Any) -> None:
        """Collect NAT Gateway resources."""
        try:
            kwargs: Dict[str, Any] = {}
            if getattr(self, "vpc_ids", None):
                # EC2 DescribeNatGateways uses 'Filter' (singular) parameter
                kwargs["Filter"] = [{"Name": "vpc-id", "Values": list(self.vpc_ids)}]
            response = self._make_api_call(
                ec2_client, "describe_nat_gateways", **kwargs
            )
            if not response:
                return

            for nat_data in response.get("NatGateways", []):
                nat_id = nat_data["NatGatewayId"]
                subnet_id = nat_data.get("SubnetId")
                vpc_id = nat_data.get("VpcId")

                # Extract name from tags
                name = None
                tags = {}
                for tag in nat_data.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                    tags[tag["Key"]] = tag["Value"]

                # Create NAT gateway resource
                location = self.create_resource_location()
                metadata = self.create_resource_metadata(tags=tags)

                nat_resource = BaseResource(
                    resource_id=nat_id,
                    resource_type=ResourceType.NAT_GATEWAY,
                    name=name,
                    arn=f"arn:aws:ec2:{self.region}:{self.account_id}:nat-gateway/{nat_id}",
                    location=location,
                    metadata=metadata,
                    properties={
                        "subnet_id": subnet_id,
                        "vpc_id": vpc_id,
                        "state": nat_data.get("State"),
                        "nat_gateway_addresses": nat_data.get(
                            "NatGatewayAddresses", []
                        ),
                        "connectivity_type": nat_data.get("ConnectivityType"),
                        "failure_reason": nat_data.get("FailureReason"),
                    },
                )

                self.add_resource(nat_resource)

                # Create relationship to subnet
                if subnet_id:
                    subnet_relationship = Relationship(
                        source_id=subnet_id,
                        target_id=nat_id,
                        relationship_type=RelationshipType.CONTAINS,
                    )
                    self.add_relationship(subnet_relationship)

                logger.debug(f"Collected NAT Gateway: {nat_id}")

        except Exception as e:
            error_msg = f"Failed to collect NAT Gateways: {e}"
            logger.error(error_msg)
            self.collection_errors.append(error_msg)

    def _collect_vpc_endpoints(self, ec2_client: Any) -> None:
        """Collect VPC Endpoint resources and enrich with service owner."""
        try:
            kwargs: Dict[str, Any] = {}
            if getattr(self, "vpc_ids", None):
                kwargs["Filters"] = [{"Name": "vpc-id", "Values": list(self.vpc_ids)}]
            response = self._make_api_call(
                ec2_client, "describe_vpc_endpoints", **kwargs
            )
            if not response:
                return

            endpoints = response.get("VpcEndpoints", [])
            service_names = sorted(
                {ep.get("ServiceName") for ep in endpoints if ep.get("ServiceName")}
            )

            owners_by_service: Dict[str, str] = {}
            try:
                if service_names:
                    svc_resp = self._make_api_call(
                        ec2_client,
                        "describe_vpc_endpoint_services",
                        ServiceNames=service_names,
                    )
                    details = svc_resp.get("ServiceDetails") or []
                    for d in details:
                        name = d.get("ServiceName")
                        owner = d.get("Owner")
                        if name and owner:
                            owners_by_service[name] = owner
            except Exception as e:
                logger.debug(f"Could not resolve endpoint service owners: {e}")

            for endpoint_data in endpoints:
                endpoint_id = endpoint_data["VpcEndpointId"]
                vpc_id = endpoint_data["VpcId"]
                service_name = endpoint_data.get("ServiceName")

                # Extract name from tags
                name = None
                tags = {}
                for tag in endpoint_data.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                    tags[tag["Key"]] = tag["Value"]

                # Create VPC endpoint resource
                location = self.create_resource_location()
                metadata = self.create_resource_metadata(tags=tags)

                endpoint_resource = BaseResource(
                    resource_id=endpoint_id,
                    resource_type=ResourceType.VPC_ENDPOINT,
                    name=name,
                    arn=f"arn:aws:ec2:{self.region}:{self.account_id}:vpc-endpoint/{endpoint_id}",
                    location=location,
                    metadata=metadata,
                    properties={
                        "vpc_id": vpc_id,
                        "service_name": service_name,
                        "service_owner": owners_by_service.get(service_name),
                        "vpc_endpoint_type": endpoint_data.get("VpcEndpointType"),
                        "state": endpoint_data.get("State"),
                        "route_table_ids": endpoint_data.get("RouteTableIds", []),
                        "subnet_ids": endpoint_data.get("SubnetIds", []),
                        "network_interface_ids": endpoint_data.get(
                            "NetworkInterfaceIds", []
                        ),
                        "security_group_ids": endpoint_data.get("Groups", []),
                        "dns_entries": endpoint_data.get("DnsEntries", []),
                    },
                )

                self.add_resource(endpoint_resource)

                # Create relationship to VPC
                vpc_relationship = Relationship(
                    source_id=vpc_id,
                    target_id=endpoint_id,
                    relationship_type=RelationshipType.CONTAINS,
                )
                self.add_relationship(vpc_relationship)

                # Link ENIs to the VPC endpoint for deterministic mapping
                for eni_id in (
                    endpoint_resource.properties.get("network_interface_ids", []) or []
                ):
                    if eni_id:
                        self.add_relationship(
                            Relationship(
                                source_id=eni_id,
                                target_id=endpoint_id,
                                relationship_type=RelationshipType.ATTACHED_TO,
                            )
                        )

                logger.debug(f"Collected VPC Endpoint: {endpoint_id}")

        except Exception as e:
            error_msg = f"Failed to collect VPC Endpoints: {e}"
            logger.error(error_msg)
            self.collection_errors.append(error_msg)

    def _collect_transit_gateways(self, ec2_client: Any) -> None:
        """Collect Transit Gateway resources."""
        try:
            # No vpc-id filter for TGWs; collect minimal data. Attachments will be filtered by VPC.
            response = self._make_api_call(ec2_client, "describe_transit_gateways")
            if not response:
                return
            for tgw in response.get("TransitGateways", []):
                tgw_id = tgw["TransitGatewayId"]
                name = None
                tags = {}
                for tag in tgw.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                    tags[tag["Key"]] = tag["Value"]
                location = self.create_resource_location()
                metadata = self.create_resource_metadata(tags=tags)
                tgw_resource = BaseResource(
                    resource_id=tgw_id,
                    resource_type=ResourceType.TRANSIT_GATEWAY,
                    name=name,
                    arn=f"arn:aws:ec2:{self.region}:{self.account_id}:transit-gateway/{tgw_id}",
                    location=location,
                    metadata=metadata,
                    properties={
                        "state": tgw.get("State"),
                        "owner_id": tgw.get("OwnerId"),
                        "options": tgw.get("Options", {}),
                    },
                )
                self.add_resource(tgw_resource)
                logger.debug(f"Collected Transit Gateway: {tgw_id}")
        except Exception as e:
            error_msg = f"Failed to collect Transit Gateways: {e}"
            logger.error(error_msg)
            self.collection_errors.append(error_msg)

    def _collect_transit_gateway_attachments(self, ec2_client: Any) -> None:
        """Collect Transit Gateway Attachments and create relationships to subnets/VPCs."""
        try:
            kwargs: Dict[str, Any] = {}
            if getattr(self, "vpc_ids", None):
                kwargs["Filters"] = [
                    {"Name": "resource-type", "Values": ["vpc"]},
                    {"Name": "resource-id", "Values": list(self.vpc_ids)},
                ]
            response = self._make_api_call(
                ec2_client, "describe_transit_gateway_attachments", **kwargs
            )
            if not response:
                return
            for att in response.get("TransitGatewayAttachments", []):
                tgw_id = att.get("TransitGatewayId")
                resource_type = att.get("ResourceType")  # e.g., 'vpc'
                resource_id = att.get("ResourceId")  # e.g., VPC ID
                state = att.get("State")
                # For VPC attachments, fetch the subnets used in the attachment
                subnet_ids = []
                assoc = att.get("Association", {})
                if "SubnetIds" in att:
                    subnet_ids = att.get("SubnetIds", [])
                elif assoc.get("SubnetId"):
                    subnet_ids = [assoc["SubnetId"]]

                # Link VPC to TGW
                if tgw_id and resource_id and resource_type == "vpc":
                    self.add_relationship(
                        Relationship(
                            source_id=resource_id,
                            target_id=tgw_id,
                            relationship_type=RelationshipType.CONNECTS_TO,
                            properties={"state": state},
                        )
                    )

                # Link participating subnets to TGW (per-AZ attachments)
                for subnet_id in subnet_ids:
                    self.add_relationship(
                        Relationship(
                            source_id=subnet_id,
                            target_id=tgw_id,
                            relationship_type=RelationshipType.CONNECTS_TO,
                            properties={"state": state},
                        )
                    )
            logger.debug("Collected Transit Gateway attachments and relationships")
        except Exception as e:
            error_msg = f"Failed to collect Transit Gateway Attachments: {e}"
            logger.error(error_msg)
            self.collection_errors.append(error_msg)

    def _collect_transit_gateway_vpc_attachments(self, ec2_client: Any) -> None:
        """Collect Transit Gateway VPC Attachments to get per-AZ SubnetIds and link them to TGW."""
        try:
            kwargs: Dict[str, Any] = {}
            if getattr(self, "vpc_ids", None):
                kwargs["Filters"] = [{"Name": "vpc-id", "Values": list(self.vpc_ids)}]
            response = self._make_api_call(
                ec2_client, "describe_transit_gateway_vpc_attachments", **kwargs
            )
            if not response:
                return
            for att in response.get("TransitGatewayVpcAttachments", []):
                tgw_id = att.get("TransitGatewayId")
                vpc_id = att.get("VpcId")
                subnet_ids = att.get("SubnetIds", [])
                state = att.get("State")

                # Ensure VPC <-> TGW relationship exists
                if tgw_id and vpc_id:
                    self.add_relationship(
                        Relationship(
                            source_id=vpc_id,
                            target_id=tgw_id,
                            relationship_type=RelationshipType.CONNECTS_TO,
                            properties={"state": state},
                        )
                    )

                # Add subnet-level relationships to TGW (for attachment grouping by AZ)
                for subnet_id in subnet_ids:
                    self.add_relationship(
                        Relationship(
                            source_id=subnet_id,
                            target_id=tgw_id,
                            relationship_type=RelationshipType.CONNECTS_TO,
                            properties={"state": state},
                        )
                    )
            logger.debug(
                "Collected Transit Gateway VPC attachments and subnet relationships"
            )
        except Exception as e:
            error_msg = f"Failed to collect Transit Gateway VPC Attachments: {e}"
            logger.error(error_msg)
            self.collection_errors.append(error_msg)

    def _collect_vpc_peering_connections(self, ec2_client: Any) -> None:
        """Collect VPC peering connections and create peer relationships."""
        try:
            kwargs: Dict[str, Any] = {}
            if getattr(self, "vpc_ids", None):
                kwargs["Filters"] = [
                    {"Name": "requester-vpc-info.vpc-id", "Values": list(self.vpc_ids)},
                    {"Name": "accepter-vpc-info.vpc-id", "Values": list(self.vpc_ids)},
                ]
            response = self._make_api_call(
                ec2_client, "describe_vpc_peering_connections", **kwargs
            )
            if not response:
                return
            for pcx in response.get("VpcPeeringConnections", []):
                pcx_id = pcx["VpcPeeringConnectionId"]
                name = None
                tags = {}
                for tag in pcx.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                    tags[tag["Key"]] = tag["Value"]
                location = self.create_resource_location()
                metadata = self.create_resource_metadata(tags=tags)
                pcx_resource = BaseResource(
                    resource_id=pcx_id,
                    resource_type=ResourceType.VPC_PEERING,
                    name=name,
                    arn=f"arn:aws:ec2:{self.region}:{self.account_id}:vpc-peering-connection/{pcx_id}",
                    location=location,
                    metadata=metadata,
                    properties={
                        "status": pcx.get("Status", {}),
                        "requester_vpc": pcx.get("RequesterVpcInfo", {}),
                        "accepter_vpc": pcx.get("AccepterVpcInfo", {}),
                    },
                )
                self.add_resource(pcx_resource)

                # Create peer relationship between the two VPCs
                req_vpc = pcx.get("RequesterVpcInfo", {}).get("VpcId")
                acc_vpc = pcx.get("AccepterVpcInfo", {}).get("VpcId")
                if req_vpc and acc_vpc:
                    self.add_relationship(
                        Relationship(
                            source_id=req_vpc,
                            target_id=acc_vpc,
                            relationship_type=RelationshipType.PEERS_WITH,
                            properties={"status": pcx.get("Status", {})},
                        )
                    )
                logger.debug(f"Collected VPC Peering Connection: {pcx_id}")
        except Exception as e:
            error_msg = f"Failed to collect VPC Peering Connections: {e}"
            logger.error(error_msg)
            self.collection_errors.append(error_msg)
