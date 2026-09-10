# tests/regression/ — 回归测试（规划中）

本目录用于放置**历史真实事故回归测试**：

- 锚定真实 `run` 号（如 `33873929856`）；
- 复现历史 P0/P1 事故（见 `docs/engineering-review/HISTORICAL_BUG_CASES.md`、`REGRESSION_MATRIX.md`）；
- 与普通 unit/integration 分目录存放，避免语义混淆。

当前仓库已有 149 条用例已按 unit/integration 归类；历史事故的稳定复现用例
从这里新增，不要与普通测试混排。