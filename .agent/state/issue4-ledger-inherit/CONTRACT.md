# Task Contract (S-spec Sync)

> 由 Router 同步触发生成；Human-in-the-Loop ≥medium 必须审批。
> 本文件是 Execution Layer 与 Verification Report 之间的法律合同。

---

## 0. Meta

```yaml
schema_version: 1
contract_id: 20261002-manual-onboard-contract
task_id: issue4-ledger-inherit
router_ref: 20261002-manual-onboard-router
approved_by: human (Thy985, 2026-10-02, via AskUserQuestion "批准，进 S5 实现")
```

## 1. 4W (What / How / Feedback / Done)

```yaml
what_changes:
  files:
    - app/registry/bootstrap.py          # 新增 legacy→account 继承入口（bootstrap 前置一步）
    - app/registry/reconcile.py          # 仅当需要新增 point-suffix 去重语义时才动；优先复用现有 reconcile_registry
    - tests/unit/test_bootstrap_p03.py   # 或新增 tests/unit/test_ledger_inherit.py
    - state/accounts/5b2a5d53125728fe/registry/265997861_151695658/tasks.json   # 继承后的账（git-tracked）
  rationale:
    - issue #4 第六层卡点：P0-2 账号隔离把全部真实工作历史（63 video / 48 COMPLETED）
      留在 legacy 命名空间，账号命名空间只有 30 条退化账（29 other + 1 已 COMPLETED video）
    - reconcile_queue (task_registry.py:803) 只收 task_type=="video"，退化账唯一 video
      已 COMPLETED → queue 恒空 → upcoming=0 → _ensure_bootstrap_on_start 幂等 NOOP
      → 永远造不出 video 任务（自增强死循环）
    - 继承让 1217304719(video,PENDING) / 1217304705(video,FAILED) / 1217304731(video,FAILED)
      进入账号命名空间 → queue 非空 → scheduler 判 RUN 而非 NOOP
  explicit_non_goals:
    - 不改 .github/workflows/*（Passport §4 须先问）
    - 不触发 workflow_dispatch、不 git push
    - 不改 DB schema、不改生产配置
    - 不动 E6.1 的 COMPLETED 证据降级语义（legacy 48 条 COMPLETED 均带
      verification.level=SERVER_VERIFIED 或 UI，repair_course 会保留）
how_to_verify:
  tests:
    - python -m pytest tests/unit -q          # 全 unit 回归
    - python -m pytest tests/regression -q    # P0/P0.3/P1.2/P2 回归
  manual_steps:
    - 继承后本地跑 reconcile_queue，断言 items >= 1 且含 1217304719
    - 断言 legacy 48 条 COMPLETED 在账号账里仍以 COMPLETED 存在（未被 E6.1 误降）
    - 断言幂等：连续跑两次继承，第二次为 NOOP，账不变
feedback_signals:
  success_metrics:
    - 账号账 video 任务数 >= 63
    - reconcile_queue items >= 1
    - 48 条 COMPLETED 保留率 = 100%
  failure_metrics:
    - 任何 legacy COMPLETED 被降为 UNKNOWN
    - 继承后 queue 仍为空
    - 继承破坏 point-suffix 唯一性（同章出现 <cid> 与 <cid>:other 双键冲突）
done_when:
  - accounts/5b2a5d53125728fe/registry/.../tasks.json 含 >= 63 条 video 任务
  - reconcile_queue 在该账上返回 >= 1 项
  - legacy 48 条 COMPLETED 全部保留 COMPLETED 状态
  - 重复执行继承为幂等 NOOP
  - tests/unit + tests/regression 全绿
  - PR 通过 CI（不绕过 CI，CLAUDE.md §08）
```

## 2. Phase Alignment

```yaml
phase: issue #4 tail — P0-2 account namespace ledger inheritance
in_roadmap: yes (issue #4 已知卡点序列的第 6 层)
adr_required: no (沿用既有 E6.1/E6.2 账本语义，不引入新抽象)
adr_ref: none
```

## 3. Risk

```yaml
risk_score: medium
blast_radius: 单课程、单账号的账本内容变更；错误的最坏后果是 scheduler 重投已失败任务
  或跳过真实待办，不影响其它账号/课程，不影响代码路径
reversible: yes (git revert；账本文件本身 git-tracked)
```

## 4. Approvals

```yaml
approvals:
  - role: reviewer
    status: approved
    note: human 于 2026-10-02 通过 AskUserQuestion 批准 "批准，进 S5 实现"。
      范围锁定 CONTRACT.md §1 what_changes，不触碰 explicit_non_goals。
```
