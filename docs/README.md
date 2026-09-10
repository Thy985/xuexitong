# docs/ 目录地图

仓库统一文档目录。工程文档、审计、历史事故、证据、运行/验证手册都在这里，按用途分目录。

| 目录 | 放什么 |
|---|---|
| `architecture/` | 架构说明、状态机、scheduler / registry / persistence 设计师说明 |
| `engineering-review/` | 工程审计 / 考古 / 回归矩阵 / CI 门禁候选 / Agent 规则候选 |
| `runbooks/` | 本地 Playwright 运行手册、能力矩阵、滑块验证码处理等操作/验证手册 |
| `evidence/` | E2E 基线报告、运行证据快照（DOM / HTML / JSON / 截图）、历史运行日志 |

原则：
- 历史事故、审计、证据文件**只移不删、不改结论**。
- 新的工程文档按上述四个一级分类就近归档。