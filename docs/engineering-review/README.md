# docs/engineering-review/ — 工程审计与治理物料

历史考古、事故库、回归/门禁/Agent 规则候选。**只读性质，不改写结论。**

| 文件 | 内容 |
|---|---|
| `ACTION_HISTORY_AUDIT.md` + `action_runs.jsonl` | GHA 生产运行历史考古 |
| `HISTORICAL_BUG_CASES.md` | 历史事故案例库（C1..C10 等） |
| `REGRESSION_MATRIX.md` | 事故 → 失效模式 → 回归测试 → 验证级别 → CI 门禁 → Agent 规则矩阵 |
| `CI_GATES_CANDIDATES.md` | CI 门禁候选、频率映射 |
| `AGENT_RULE_CANDIDATES.md` | Agent 规则候选 R-0..R-10 |

运行/验证手册（Playwright runbook、能力矩阵、滑块处理）见 `docs/runbooks/`；
E2E 基线 + 真实证据见 `docs/evidence/`。