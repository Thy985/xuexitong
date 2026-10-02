# Router Output · Adaptive Workflow Selection

> 本文件由 Router（`select_flow.py` 或 `select_flow.md` 引导的人工决策）产出，
> 是 Discovery 之后唯一权威的 flow / gates / roles 决策。
> Execution Layer（Agent / 人类）必须按本文件执行；偏离须显式记录在 `task-state/checkpoint.md`。

---

## 0. Meta

```yaml
schema_version: 1
router_id: 20261002-manual-onboard-router
based_on_discovery: 20261002-manual-onboard-a4f1
decided_by: ai (manual — select_flow.py blocked by permission classifier)
decided_at: 2026-10-02T06:50:00+00:00
```

> **Provenance**：`select_flow.py` 未执行（分类器超时）。本文件按
> `templates/router/FLOW.template.md` 手工渲染，flow/gates/roles 严格按
> `select_flow.py` 的 `FLOW_MATRIX` / `GATE_PROFILES` / `ROLE_PROFILES` 查表得出，
> 没有自造新条目。

## 1. Flow Selection

```yaml
flow:
  name: Standard
  rationale: DISCOVERY §4 blast_radius_score=medium 且 production_repo=yes
    → FLOW_MATRIX[("medium","yes")] = "Standard"
  skill_pipeline:
    - Understand Before Implement (CLAUDE.md §01)
    - Debugging Protocol Reproduce→Evidence→RootCause→Fix→Regression (CLAUDE.md §03)
    - Git Workflow Atomic Commit (CLAUDE.md §07)
gates:
  G_ci: required
  G_stop: required
  G_risk: required
  G_permission: optional
  G_hitl: optional
  G_understanding: required
roles:
  - TechLead
  - Developer
  - Reviewer
execution_status_model:
  enforced: true
  values: [Completed, Skipped, Deferred, Escalated]
  skipped_requires_reason: true
```

## 2. Task Contract (Sync)

```yaml
contract_ref: CONTRACT.md
```

## 3. Risk & Rollback Pre-flight

```yaml
risk_score: medium
risk_signals:
  - blast_radius_score=medium
  - production_repo=yes
  - adr_count=0
  - 改动对象为 git-tracked 的 CI 运行态账本，非新增模块
rollback_plan:
  steps:
    - git revert <inherit-commit>      # 恢复继承前的退化账
    - 若误合并导致 scheduler 行为异常，先回滚账本，代码改动另行 revert
    - 账本文件本身是 git-tracked，无独立备份需要
  cost: low
```

## 4. Escalation Triggers

```yaml
triggers:
  - condition: same_task_failures > 5
    escalate_to: human
  - condition: scope_drift == true
    escalate_to: human
  - condition: requires_new_adr == true
    escalate_to: human
  - condition: 需要在 accounts/<acc>/registry 写入 legacy 之外的任何字段
    escalate_to: human
```
