# ENI Relationship Determination

This note summarizes how Elastic Network Interfaces (ENIs) are associated with
other resources in the generated topology graph and when heuristic fallbacks are
still required.

## Deterministic Attachments

The collectors emit explicit `ATTACHED_TO` relationships for ENIs whenever the
AWS APIs expose a direct reference. These relationships are consumed by the
graph transformer, so downstream views and reports no longer need to guess.

- **EC2 instances** – `EC2Collector` uses the `NetworkInterfaces` block returned
  by `DescribeInstances` to link ENIs to compute resources and security groups.
- **Lambda functions** – `VPCCollector` emits relationships whenever an ENI
  carries the `aws:lambda:functionArn` tag, which is always populated for VPC
  enabled Lambdas.
- **VPC endpoints & AWS-managed services** – When the EC2 APIs report the ENI
  attachment owner (for example, VPC endpoints owned by AWS), the collector
  records the relationship and the transformer keeps those ENIs grouped with the
  underlying service.
- **Application, Network, and Gateway Load Balancers** – The graph transformer
  recognises deterministic `ELB app/...` / `ELB net/...` descriptions and
  resolves them to the canonical load balancer ARN. ENIs are linked to the
  owning load balancer during graph construction, removing the need for
  substring heuristics in later stages.

With these relationships in place the CLI ENI report is a straight projection of
the graph, and ENIs belonging to different load balancers no longer collide.

## Remaining Heuristic Cases

Some managed services continue to omit explicit ENI attachment metadata. For
those resources we keep targeted heuristics in the transformer to avoid losing
context:

- **Amazon RDS / Aurora** – ENIs surface through the RDS control plane without a
  consistent back-reference. We match on the `InstanceOwnerId` of the ENI
  attachment (`amazon-rds`) and on the `rds:db-id` tag populated by RDS.
- **Amazon ElastiCache** – Similar to RDS, ENIs use `amazon-elasticache` as the
  owner and include descriptive strings such as `cache` or `elasticache` in the
  ENI description. These hints are necessary because the EC2 ENI API does not
  expose the replication group ARN directly.
- **Amazon OpenSearch Service / Amazon Elasticsearch Service** – ENIs expose the
  owner `amazon-opensearch-service` but not the domain ID, so we match on the
  owner field and characteristic description text.
- **Amazon Redshift** – Uses owner `amazon-redshift` and descriptive text to
  identify the cluster that provisioned the ENI.

Each heuristic is limited to a small set of identifiers and is used only when no
explicit relationship exists. Whenever AWS adds first-class references for these
services we can retire the fallback logic in
`BaseTransformer._collect_service_eni_ids` and rely solely on the collector
output.

## References

- `src/collectors/vpc_collector.py` – raw ENI collection and tag-based Lambda
  attachments.
- `src/collectors/ec2_collector.py` – EC2 instance and security group
  relationships.
- `src/transformers/base_transformer.py::_collect_service_eni_ids` – scoped
  heuristics for remaining managed services and deterministic load balancer
  association.
- `src/utils/eni_inference.py` – shared helpers for interpreting ENI metadata
  (e.g., ELB description fragments) in a deterministic manner.
- `src/cli/report.py` – now a thin view layer that renders the graph without
  additional matching heuristics.
