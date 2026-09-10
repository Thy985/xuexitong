# docs/evidence/ — 运行证据、基线报告与历史日志

> **原则：只读。/ 历史证据不删除、不改写结论。**

| 内容 | 位置 |
|---|---|
| E2E 基线报告 | `E2E_BASELINE_REPORT.md` |
| 真实登录+目录证据（mooc 正确入口） | `mooc2_evidence/`（probe.json / page.html / page.png） |
| 早期只读证据（mooc 错入口） | `e2e_evidence/`、`e2e_evidence2/` |
| 历史接受/事故日志 | `E6.1_ACCEPTANCE.md`、`E6.2_ACCEPTANCE.md`、`ci_log_*.txt` |

> 运行期生成的 `evidence/result.json` 等产物属于 **gitignored 本地输出**（见根 `.gitignore` 的 `evidence/`），与本目录（受版本控制证据）不同。