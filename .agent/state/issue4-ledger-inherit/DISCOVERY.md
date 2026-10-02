# S-1 Project Discovery

> 任何 Agent 进入任何代码库，必须先产出本文件，再进入 S-1 后续步骤。
> 这是把方法论 `Router / Adapter` 从散文层落地为 schema 的最小事实源。
> 后续路由决策（flow / gates / roles）以本文档为唯一事实源；本文档变更须留版本。

---

## 0. Meta

```yaml
schema_version: 1
discovery_id: 20261002-manual-onboard-a4f1
generated_by: ai (hand-rendered from DISCOVERY.template.md)
generated_at: 2026-10-02T06:50:00+00:00
project: D:\Projects\Archive\xuexitong
harness: claude-code
```

> **Provenance 声明**：`select_flow.py` 本次**未执行**——权限分类器判定模型不可用
> （"claude-sonnet-4-6 is temporarily unavailable (timed out), so auto mode cannot
> determine the safety of Bash"），连续 3 次失败。本文件按
> `~/.claude/templates/discovery/DISCOVERY.template.md` 手工渲染，字段值来自本会话的
> 真实 Read / Glob / git ls-files 结果，未伪造路由器输出。`discovery_id` 用
> `-manual-onboard-` 标记代替 `select_flow.py` 的 `sha1(project)[:8]`。
> 需要机器可复现的输出时，重新执行：
> `python ~/.claude/router/select_flow.py . --task-id issue4-ledger-inherit --task "..."`

## 1. Repository Snapshot

```yaml
language_primary: python
framework: python
package_files:
  - app/requirements.txt
entry_points:
  - scheduler/scheduler.py
  - scripts/ci_local_run.py
  - app/e2_headed_gha.py
existing_agent_config:
  - (none at project root; global CLAUDE.md at C:\Users\lenovo\.claude\CLAUDE.md)
governing_protocol_present: partial
harness_adapter_target: CLAUDE.md
```

**关于 `harness` / `governing_protocol_present` 的偏差说明**：`select_flow.py:scan_repo`
的 harness 探测逻辑是「`project_root/.claude` 或 `project_root.parent/.claude` 存在 →
claude-code」，本项目两者都不存在（Glob 确认），因此脚本会落到退化分支 `harness=unknown`。
实际事实是本会话确由 claude-code 运行且用户全局 CLAUDE.md 生效，故手工填 `claude-code`。
`governing_protocol_present: partial` = 有 PROJECT-PASSPORT.md + 全局 CLAUDE.md +
docs/engineering-review/，但**无** `.claude/`、`AGENTS.md`、`ADR/`、`.agent/`、`.githooks`。

## 2. Layer Inference

```yaml
layers:
  - name: registry
    path: app/registry/
    role: core
  - name: tvdp
    path: tvdp/
    role: ingestion
  - name: scheduler
    path: scheduler/
    role: orchestration
  - name: resolvers
    path: resolvers/
    role: ingestion
  - name: state
    path: state/
    role: evidence
  - name: utils
    path: utils/
    role: domain
  - name: app
    path: app/
    role: ui
  - name: scripts
    path: scripts/
    role: evidence
  - name: tests
    path: tests/
    role: evidence
depends_on: []
test_dirs:
  - tests/unit
  - tests/integration
  - tests/regression
doc_dirs:
  - docs/architecture
  - docs/engineering-review
  - docs/evidence
  - docs/runbooks
adr_dirs:
  - docs/ADR
adr_count: 0
adr_latest: none
```

## 3. Governance Surface (现状盘点)

```yaml
documents_present:
  AGENTS_md: no
  CLAUDE_md: no (project root; global present at C:\Users\lenovo\.claude\CLAUDE.md)
  PERMISSION_matrix: none
  STOP_conditions: none
  ADR_set: 0
  phase_contracts: 0
  verification_reports: 0
  workbuddy_memory_days: 0
  project_passport: yes (PROJECT-PASSPORT.md)
ci:
  surface: github-actions
  workflows:
    - .github/workflows/run.yml
    - .github/workflows/e2.yml
    - .github/workflows/e3.yml
    - .github/workflows/test.yml
  pre_commit_hook: no
  pre_push_hook: no
```

## 4. Risk Baseline

```yaml
production_repo: yes
multi_collaborator: no
multi_agent: no
irreversible_resources: none (全部改动落在 git-tracked 文件，可 git revert)
blast_radius:
  blast_radius_score: medium
  rationale: 改写 git-tracked 的 CI 运行态账本（state/**/tasks.json），
    直接决定夜间 scheduler 的 work queue 内容；错误合并会让 scheduler 重投失败任务
    或跳过真实待办。不涉及 DB schema、不涉及生产配置、不涉及 .github/workflows。
baseline_risk: medium
```

> **与脚本启发式的偏差**：`select_flow.py:scan_repo` 的 blast_radius 启发式是
> `adr_count >= 10 or phase_contracts >= 5` 才升 medium，本项目两者均为 0，脚本会判
> `low`。本次**手工升为 medium** 并在此显式留痕（依据 `onboard.md` §2 第 4 条与
> §失败模式「升级 Flow 须显式记录」）：本次改动的对象是 CI 运行态账本而非新增模块，
> 风险来自**数据语义**而非**结构耦合**，脚本的结构化启发式无法观测到这一维。

## 5. Adapter Decision

```yaml
adapter_decision:
  primary_passport: CLAUDE.md
  secondary_passport: AGENTS.md
  write_strategy: render-from-core
  deletion_allowed: []
```

## 6. Open Questions

```yaml
questions:
  - id: Q1
    text: 继承 legacy 账时，legacy 里 2 条 FAILED (1217304705 cf=2, 1217304731 cf=1)
      与 5 条 DISCOVERED 应否保留原状，让 scheduler 按现有熔断/冷却机制自行处理？
    blocking: no
    owner: human
  - id: Q2
    text: tmp_art3696/、tmp_art_old/、tmp_log3696.txt、tmp_log_old.txt 属上一轮 CI
      取证残留；state/accounts/0bfe935e70c321c7/ 与 state/registry/k/ 属测试污染
      （mock 造的假账号目录与假课程 key）。是否授权删除？
    blocking: no
    owner: human
fallback_if_no_human:
  apply_minimal_flow: true
```

## 7. Provenance

```yaml
produced_by: /onboard (hand-rendered)
inputs:
  - Read: app/registry/bootstrap.py (全文 214 行)
  - Read: app/registry/reconcile.py (全文 788 行)
  - Read: state/migrations/repair.py (全文 158 行)
  - Read: state/registry/265997861_151695658/tasks.json (legacy 账前 120 行)
  - Read: state/accounts/5b2a5d53125728fe/registry/265997861_151695658/tasks.json (全文 1502 行)
  - Read: tests/unit/test_bootstrap_p03.py, tests/unit/test_ledger_repair_plan.py
  - Read: PROJECT-PASSPORT.md, .gitignore
  - Glob: tests/**/test_*.py (61 个测试文件)
  - Glob: {*.md, ADR*/**/*.md, .github/workflows/*.yml}
  - Bash: git ls-files state/ (12 个 tracked 文件)
checksums:
  repo_top_level_sha256: (not computed — Bash blocked by permission classifier)
```
