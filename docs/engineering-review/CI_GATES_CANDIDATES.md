# CI_GATES_CANDIDATES.md
# 回归测试 → CI 频率 / 门禁（设计候选，不实现）

> 本文件用于把 `REGRESSION_MATRIX.md` 的 Regression Test 匹配到 CI 频率。
> **当前现状（必须先改这个才能让任何 gate 生效）**：`run.yml` / `e2.yml` / `e3.yml` 三条 workflow
> 均 `on: workflow_dispatch`（e2/e3）或仅 `dispatch`，**没有任何 `push` / `pull_request` 触发，
> 也从不自动执行 `pytest tests/`**。因此「150 测试全绿」从未作为 CI 门禁真正跑过。
> 以下为**候选**划分（PR / push / nightly / manual），逐条引自矩阵验证层与确定性。

---

## 1. 门禁原则

| 层（Layer） | 确定性 | 建议频率 |
|---|---|---|
| UNIT / SM（纯逻辑） | 高、快、无需浏览器 | **PR 必跑 + push 必跑** |
| INT / 持久化 / registry / progression | 中、快、无需浏览器 | **PR 必跑**（可 RM 略慢） |
| SUBP（真实子进程 watchdog） | 中低、快 | **PR 必跑**（0.2±s，可并行） |
| PW（Playwright 真浏览器，非真站） | 低、慢 | **smoke / nightly**（fixture 稳定后转 PR 可选） |
| LIVE_E2E / 真站 | 低、慢、不透明 | **manual E2E / nightly（不放宽为 PR 阻塞，除非亏稳定 fixture）** |
| CROSS_RUN / CROSS_DAY | 最慢 | **nightly（cron）+ manual**（验证「不回退」） |

---

## 2. Gate 候选表（Mapping）

| 回归（对应 RE matrix） | 验证层 | PR | push | Nightly | Manual | 备注 |
|---|---|---|---|---|---|---|
| P0-01 真实子进程 watchdog | SUBP | ✅ | ✅ | — | — | 快、无需浏览器 |
| P0-02 e2 主循环（wait_playback 终止） | INT+SUBP | ✅ | ✅ | — | — | 可 mock 播放器态但保留主循环真调用 |
| P0-03 DOM fixture 解析 | DAT+UNIT | ✅ | ✅ | — | — | 用录制 DOM fixture，快 |
| P0-04 response callback isPassed（缓存体） | SM+INT | ✅ | ✅ | — | — | 纯逻辑/无浏览器 |
| P0-05 7 步 progression | SUBP+INT | ✅ | ✅ | — | 选做 | 可 mock 播放，但 keep scheduler 真 |
| P0-06 multi mixed result (any_success 熔断) | SUBP+DAT | ✅ | ✅ | — | — | 状态层纯逻辑 |
| P0-07 TIMEOUT 写回 registry | UNIT+SUBP | ✅ | ✅ | — | — | 接 P0-01 |
| P0-08 server completed→不降 UNKNOWN | SM+INT | ✅ | ✅ | — | — | 无浏览器 |
| P0-09 DOM completed + video pending | SM | ✅ | ✅ | — | — | 状态机纯逻辑 |
| P0-10 live `video_total==0`→不 other-drop | SM+INT | ✅ | ✅ | — | — | 纯逻辑 |
| P0-11 task_id 迁移单键 | UNIT | ✅ | ✅ | — | — | 纯逻辑 |
| P0-12 CourseIdentity（cpi 隔离） | UNIT | ✅ | ✅ | — | — | 纯逻辑 |
| P1-01 clazzId/param→no_cards | UNIT+DAT | ✅ | ✅ | — | 选做 | DAT 用 fixture |
| P1-02 dead 参/死代码(initial_duration) | UNIT/SM | ✅ | ✅ | — | — | 纯逻辑 |
| P1-03 多视频 duration 判段 | UNIT | ✅ | ✅ | — | — | |
| P1-04 src/MSE 切段 | — | — | — | ✅ | ✅ | 需 PW/真站 |
| P1-05 console buffer 局部化 | UNIT | ✅ | ✅ | — | — | |
| P1-06 Step I 不覆盖已确认 | UNIT | ✅ | ✅ | — | — | |
| P1-07 失败卡队头重试 | SM/INT | ✅ | ✅ | — | — | 已有 ✅ |
| P1-08 DISCOVERED→VERIFIED 全链路 | SM | ✅ | ✅ | 选做 | — | |
| P1-09 双熔断单一源 | SM/INT | ✅ | ✅ | — | — | |
| P1-10 :vnc/half 前缀边界 | UNIT | ✅ | ✅ | — | — | |
| P1-11 双 registry 路径一致 | INT | ✅ | ✅ | — | — | 可 mock 路径 |
| P1-12 progress 会计 + 跨 run | INT+RUN | ✅ | ✅ | ✅ | — | 见 §3.2 五信号 |
| P2-01 iframe 树 | DAT | ✅ | — | — | — | 纯提取 |
| P2-02 banner/sidebar | DAT | ✅ | — | — | — | |
| 真站 e2/e3（Playwright headed） | LIVE | — | — | ✅ | ✅ | 保留现有 e2/e3 workflow 手动 |

---

## 3. 建议新增实质上 CI 触发的三处（设计，不落地）
1. **新增 `test.yml`（push/PR 必跑）**：`on: push/pull_request` → `python pytest tests/test_regression_p0.py tests/ -q` + 覆盖率（可选阈值，避免「跑但 0% 也绿」）。
2. **新增 `nightly.yml`（cron 每天 UTC）**：`CROSS_RUN / CROSS_DAY` + Playwright fixture smoke + 可选的 live_like stub（无真实 key 时不真站）。
3. **保留 `run.yml`/`e2.yml`/`e3.yml` 为 manual/schedule**：长真站链路不影响日常 PR。

> 注意：把现有 150 测试全部塞进 PR 会拖慢；**先把 P0+P1(纯逻辑)放进 PR**，PLAYWRIGHT/LIVE 走 nightly/manual，否则回归变负担反而拖垮门禁落地。

---

## 4. 假安全感告警（呼应 matrix TFS-6）
任何 CI 若不加「至少一个 P0 必须红得掉」的启发，绿不可信。**建议立即让 CI 至少跑 `test_regression_p0`（P0-01/02/03/11/12 等纯逻辑+SUBprocess）** —— 这是当前能最快补上「CI 全绿但历史生产仍失败」空洞的一步。