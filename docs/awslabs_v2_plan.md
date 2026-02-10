## AWS Labs Transformer V2 Conversation Snapshot

1. **Goal & Scope**
   - Ensure AWS Labs v2 transformer renders diagrams matching legacy layout without falling back to v1/legacy builders.
   - V2 must rely solely on the generic `DiagramGraph` produced by `BaseTransformer`; direct topology access is being removed.
   - Focus on v2 improvements only; v1 is deprecated.

2. **Load Balancer / Target Group Requirements**
   - Layout: `SG (LB)` → `LB` → `SG (targets)` → `Targets`, with target groups arranged horizontally.
   - When multiple target groups share the same SG and ECS cluster, aggregate by cluster (`SG cluster` → `Cluster` → services) and link LB directly to services.
   - For IP-based target groups: show only the IP/instance icon (no TG container) and connect LB directly; if target is another LB, deduplicate and link LB-to-LB.
   - Maintain TG nodes for ECS-based groups; only change presentation.

3. **Bug Fix History**
   - Resolved earlier cycle involving a sample service by deduplicating ECS clusters; later cycles surfaced in other sample services.
   - TG nodes disappeared temporarily after refactor; restored but containers still present for IP-only groups.
   - Recent regression: cycle detected again when IP-based TG handling changed.

4. **Testing Expectations**
   - Existing tests were too simple; need complex fixtures mirroring real topologies (multiple LBs, shared SGs, ECS + IP targets).
   - Tests must detect graph cycles (e.g., helper that walks `DiagramGraph` and fails on repeats).
   - Legacy (v1) tests moved under `tests/legacy/` and marked optional via `pytest.ini`.

5. **Operational Notes**
   - Always use `.venv` environment.
   - No use of legacy transformer or shared builder abstractions.
   - Prefer logging via `logging` module over print statements.
   - Worktree is dirty; avoid `git checkout --` or reverting unrelated changes.

6. **Next Work Items**
   - Finish LB/TG graph handling so IP-only targets create stack entries without cycles.
   - Update tests/fixtures to cover cycle scenarios and confirm no TG nodes are missing.
   - After stabilizing behaviour, address remaining warnings (e.g., missing SG icon definitions) if prioritized later.
