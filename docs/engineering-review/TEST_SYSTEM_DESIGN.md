# TEST_SYSTEM_DESIGN.md
# 测试体系设计 —— 历史事故 × 现有 E2E × 现有 149 测试 → 一张可执行的测试地图

> 本文是**设计文档**（不落地实现，不修改代码/workflow 语义）。依据：
> - 事故库：`HISTORICAL_BUG_CASES.md`（C1..C10 + §3.x + §4.x，159 commits）
> - 逆向映射：`REGRESSION_MATRIX.md`（P0-01..P0-12 / P1-01..12 / P2，五类 outcome、验证层、TFS-1..6）
> - CI：`CI_GATES_CANDIDATES.md`（现有 3 workflow 均无 pytest 自动触发 = **TFS-6 最大假安全感**）
> - 现有代码与测试：`app/`（含 `app/registry/`、`app/e2_headed_gha.py`）`scheduler/` `tvdp/` `resolvers/` `state/` `utils/` `scripts/`
> - 现有测试资产：`tests/`（unit/integration 已起，当前 **149 pass + 1 skip**，139 个 `test_*` 函数）
> - 真实验证资产：`scripts/mooc2_probe.py` / `local-pw_probe.py` / `diag_login.py`（mooc2 真实登录已通）

---

## 0. 一句话定调

> 本仓库缺的不是「更多测试点」，而是**一座分层可信的测试金字塔 + 一条真正会跑 pytest 的 CI**。
> 当前 149 测试全绿，但生产事故（死循环、漏学、状态污染、repeat）几乎都发生在「真实子进程 / 主循环 /
> 真实 DOM / 跨 run / 跨天」这几层——**恰好是现有测试被 mock 掉或根本没有的层**。

---

## 1. 现状盘点（三输入）

### 1.1 历史事故（事故库浓缩，按「为何漏掉」分三桶）
| 桶 | 代表 | 共同根因 | 对应「现有覆盖」 |
|---|---|---|---|
| **进程/主循环挂起** | C1 watchdog 真子进程 / C2 cap / C3 wait_playback 空转 / 主循环永不退出 | 只测纯函数/被 monkeypatch，真 Popen/主循环=0 | `test_timeout_chapter_advances_queue`(mock)、`test_runs_multiple`(mock) |
| **真实站点/DOM/状态机** | DOM 漂移、isPassed 二次 fetch、`video_total==0` 漏学、迁移双键、DISCOVERED 假完成、cpi identity、双熔断、多章 SUCCESS 掩盖失败 | 无真 DOM fixture、无 live 复核、状态机边界无测 | `test_calibration`(服务端)、`test_task_state_machine`、`test_tdvp` 有，但缺真fixture 接口 |
| **跨 run/跨天会计** | `progress.completed` 恒 0、多章不前进、QUEUE_REPEAT | 观察面不持久/不跨 run 判 | 无 |

### 1.2 现有测试（149 pass + 1 skip ≈ 139 个函数）
**分布（unit/integration 拆分后）**
- `tests/unit/`（相当纯逻辑/状态机，8 文件）：calibration/parser/state/points/state-machine/tdvp
- `tests/integration/`（模块协作、scheduler、registry、persistence）：scheduler、integration、multi_video、tdvp_integration

**有效锚点（事故点 + 真 run 号）**
- `test_next_unit_decision`（run 34293378209 / 34332366744）
- `test_calibration.py`（服务端已完 / live 覆盖）
- `test_task_state_machine.py`（1217304721→1217304708 不误判 COMPLETED）
- `test_task_granularity.py`、`test_points_snapshot.py`、`test_multi_video.py`、`test_scheduler.py`

**关键空白（来自审计 §8/§复核）**
- watchdog 真·子进程 = 0
- 主循环（wait_playback 终止 / 95% 死代码） = 0
- 真 DOM fixture 解析（.icon_Completed / 已完成 / jobUnfinishCount / _CATALOG_EXTRACT_JS） = 0
- isPassed response-callback = 0
- 迁移双键 / live `video_total:0` 漏校 / 多章 any_success / CourseIdentity-cpi / 双 registry / 双熔断 = 0

### 1.3 现有 E2E（浏览器/真站验证）
- `app/e2_headed_gha.py`：10 项闭合验证（登录→iframe→duration→点击→currentTime 增长→multimedia/log→isPassed→复核），参数化 `courseid/clazzid/cpi/enc/chapterId`（原 `e2/e2_headed_gha.py`）。
- `scripts/e3_ci_run.py`：CI 可靠性实验（原 `e3/`）。
- `app/registry/`（task_registry / reconcile / evidence）；`tvdp/tdvp.py`（探针 + evidence）。
- 当前 **CI 视角**：`e2.yml`/`e3.yml` 均 `workflow_dispatch` 手动触发；`run.yml` 产品工作流。**没有一条自动跑 pytest**（TFS-6）。

---

## 2. 目标测试金字塔（物理边界 = 仓库新结构）

```
                  LIVE_E2E（真站 nightly/manual，绝不阻塞日常 PR）   ← e2/e3 workflow_dispatch
                 /  RUN/DAY  跨 run / 跨天（nightly cron）            ← 会计/回退
                / PW  Playwright fixture（非真站）smoke/nightly
               /  SUBP 真实子进程 watchdog                      ← test_regression_subprocess
              /  INT/SM 集成/状态机（scheduler+registry+persist）  ← tests/integration
             /   UNIT 纯函数/数据结构/parser/状态机                ← tests/unit
            ✔────────────  PR/push 必跑：UNIT+INT+SUBP(+纯逻辑回归) ─────────────
                               （tests/unit + integration + regression 纯逻辑层）
```

**关键分层规则（防假安全感）**
1. **可分层不替代**：每一个「看起来绿」的 `UNIT`，都要有一层（`SUBP`/`INT`/`PW`/`LIVE`）证明它在真实执行链里成立——(TFS-1..5)。
2. **不许 mock 掉被测主体**：watchdog 子进程、e2 主循环、DOM fixture、service 跨 run、scheduler 多章 progression 一律保真（matrix §2.3）。
3. **回归与 unit 物理分离**（`tests/regression/`），避免混层次掩埋。

---

## 3. 把「现有 149 测试」归类到可维护 layout（已完成结构打底）

```
tests/
├── unit/             # 纯函数/数据/parser/状态机 → 大多数现有单测
│   ├── test_calibration / test_course_resolver / test_course_state / test_next_unit_decision
│   ├── test_points_snapshot / test_state_machine / test_tdvp / test_task_granularity
├── integration/      # 模块协作 + 持久化 + queue + progression
│   └── test_scheduler / test_integration / test_multi_video / test_tdvp_integration
├── regression/       #（新建，零散的 P0/P1 固化处，见 §4）—— 按 real run 号/DOM fixture 锚定
│   ├── test_regression_process.py      # P0-01/07 真子进程 + TIMEOUT 写回
│   ├── test_regression_loop.py         # P0-02/04 主循环终止 + isPassed 缓存
│   ├── test_regression_dom.py          # P0-03 DOM 漂移 fixture
│   ├── test_regression_reconcile.py    # P0-11/7 迁移单键 + success 掩盖
│   └── test_regression_live.py         # P0-08/09/10 服务端 vs 本地（可 stub server）
└── fixtures/         #（新建）
    ├── dom/         # 真实课程页/登录页/「用户未登录」/目录树 HTML 快照
    ├── state/       # 真实 run_*.json / course_state / registry 快照
    └── net/         # isPassed 响应 / multimedia log / URL 快照
```

> 已落：`tests/unit/`、`tests/integration/` 已建（149 绿）。待建：`regression/`、`fixtures/` —— 让「缓存泄漏 P0/P1」有家，且回归与 unit 不再混排。

---

## 4. 逻辑的层次 × mock 策略

以下每一项要同时做两层，避免单测绿了真跑挂了：

| 层 | 被测什么 | 现状 mock | 目标策略 | 落到 |
|---|---|---|---|---|
| UNIT | `next_unit_decision` 纯函数 | ✅ 已有 | 保留（真状态机单测） | tests/unit |
| SM | 状态机转换（DOM→state / done 单调 / 迁移单键） | 部分 | 纯逻辑 + fixture 输入 | tests/unit |
| SUBP | `_run_one_chapter` watchdog（Popen/wait/killpg/exit 124） | mock，0 | **真 subprocess**：`sleep>max` 假命令 → killpg/exit124；再加秒回 0 假命令断言 PASS | tests/regression/test_regression_process.py |
| INT | scheduler+registry+执行队列 7 步 progression | mock 主轴 | mock 只播放单章，scheduler 主体保真，step A↔D 断言 | integration + regression_process |
| PW | Playwright 页面 fixture：`get_video_state`/iframe/切段 | 无 | Playwright 起本地 sample「fake 课程页」，不用真站 | test_regression_pw.py（可选） |
| LIVE | mooc 真实课程 + 登录 / 服务器 isPassed + 进度 | 基本无 | 由 `scripts/mooc2_probe.py` 支撑的 live 证据——**手动/nightly**，不为 PR 阻塞 | `e2.yml`/`e3.yml` 手动触发 + 可选 live | 

---

## 5. 回归层：把 P0/P1 固化（直接可执行的 backlog）

### 5.1 P0（防挂死/防错完成/防漏学/防状态污染） —— 新建 `tests/regression/`
| 待补回归（编号） | 断言/Spec | 层 |
|---|---|---|
| P0-01 真 watchdog | 起 sleep>max 假命令 → exit124+killpg+verdict=TIMEOUT；秒回0 → PASS；registry 非 RUNNING | SUBP |
| P0-02 主循环终止 | 构造 passed/not-`ended_seen` wait_playback，断言在 ENDED_GRACE 内终止不发散 | INT+SUBP |
| P0-03 DOM 漂移 guard | `fixtures/dom/*.html` → 纯函数提取完成章；断言 class 改了也不误判 | DAT |
| P0-04 isPassed 不 fetch | response 回调缓存 body.isPassed，断言无二次 fetch forward | INT |
| P0-05 progression 单调 | 真 registry：A 完→re-probe✅→选 B→完成→A 不再选（run 34311891898 / 34332366744 锚） | INT+SUBP |
| P0-06 any_success 熔断 | 章1 OK + 章2 fail：断言连续失败预算不被全局归零 | SUBP(state) |
| P0-07 TIMEOUT → 写 registry | 超时终态 ≠ RUNNING/VERIFYING，=FAILED/TIMEOUT | UNIT+SUBP |
| P0-08 server done → COMPLETED | 服务端已完成 → calibration 仍 COMPLETE（不 UNKNOWN） | SM+INT |
| P0-09 DOM completed + video pending | 不得直接 COMPLETED；走 live 复核/保 PENDING | SM+INT |
| P0-10 video_total0+有视频 → 不 other-drop | 队列保留该章 | SM+INT |
| P0-11 task_id 迁移单键 | 迁移后仅存新键（不双） | UNIT |
| P0-12 CourseIdentity（cpi 隔离） | 两个 cpi → identity 不同 | UNIT |

### 5.2 P1（重复/错误调度/漂移/会计缺口）
| 回归 | Spec | 层 |
|---|---|---|
| 熔断单一源 | 累计/连续两套 → 单一权威，SUCCESS 全清 | SM/INT |
| :video10 半前缀边界 | `:video`/`:videoN` 边界不误解析 | UNIT |
| 双 registry 路径一致 | load/save 同一 canonical | INT |
| progress.completed 会计 | before/after/server/next-run/cross-day 五信号 | INT+RUN |
| DISCOVERED→VERIFIED 全链路 | 真 fake-page 全链路 | SM + INT |

---

## 6. 夹具（fixtures）策略

**当前最大弱项：没有一份真实的 DOM 快照 / network response / state 快照。**

要补：
1. **dom fixture**：从 `HISTORICAL_BUG_CASES §4` 和 `scripts/local-pw_probe` 已存的「用户未登录」页、
   mooc2 课程页 `docs/evidence/mooc2_evidence/page.html` 等，**提取完整目录 DOM 摘录**（不含敏感），
   固化为 `tests/fixtures/dom/*.html`。
2. **state fixture**：1 个典型课程 `<key>/` 目录 + 1 个 history-round `run_*.json` + `tasks.json`（含 `.completed` 恒 0 现状）作为回归输入。
3. **内嵌 fixture 收拢**：`test_tdvp_integration._make_state`、各处 `tmp_state_dir`/`tmp_registry` 复用 → 提到 conftest fixture，消除跨文件复制（审计 8.1 点）。

---

## 7. CI 門禁设计（把「测试真正跑起来」作为第一步）

> 现状最大洞口（TFS-6）：**没有一条 CI 自动跑 pytest**。

### 目标（三个 workflow 各司其职）
1. **`test.yml`（新增，push/PR 必跑）**
   - 跑 `pytest tests/ + explicit过新 regression 层`（慢不在 unit/integration；`playwright` fixture 单独处理）。
   - 命令：`python -m pytest tests/unit tests/integration tests/regression -q`（`playwright` 和 live 排除）。
   - 加 `--cov`（开发可用可选阈值，禁「跑 0% 也绿」）。
2. **`nightly.yml`（cron 每日 UTC，可选按需）**
   - `CROSS_RUN/CROSS_DAY` 会计 + PW fixture smoke + 可选 `live_like` stub（无真实密钥不真站）。
3. **`run.yml` / `e2.yml` / `e3.yml` 保持 manual/schedule**：长真站链路不阻塞日常 PR。

> 演进：先把 `test_regression_p*`（P0-01/02/03/11/12… 纯逻辑+SUBprocess）进 PR（快、无需浏览器）；
> PLAYWRIGHT/LIVE 走 nightly，避免「浏览器太慢」拖垮门禁落地舒适感。

---

## 8. 依赖顺序（先修死代码，再固化测试 —— 否则「测个寂寞」）
审计 C4/C5/C6（`initial_duration` 死参、95% 死代码）与 C8（isPassed 二次 fetch）**先修**，
再固化对应回归，避免「测试覆盖个永远不走的路径」。

建议 rollout：
1. **第 1 步**：`test.yml` 把现有 unit/integration 接上自动跑（先让 149 真正进 CI）。
2. **第 2 步**：补 `tests/regression/` 的 P0（process→loop→dom→reconcile→state），用真 fixture。
3. **第 3 步**：修 C4/C5/C8 死区 → 让后固化的测试测真实路径。
4. **第 4 步**：`fixtures/` 固化真实录制的 DOM/state 快照，把「本地 PASS ≠ 真实」的历史点验证到位。
5. **第 5 步**：nightly 跨 run / 跨天会计（progress 恒 0 等） + PW 冒烟；live E2E 转由 `workflow_dispatch`（manual）治理。

---

## 9. 关键设计原则（写入新 Agent 的原则）

- **任何测试的通过**都必须是「它在真实被 mock 掉的层之外也立得住」；mock 只隔离**与事故无关**的依赖。
- **回归不混 unit**（`tests/regression/` 物理隔离），并以真实 run 号 / DOM fixture / 状态快照锚定。
- **CI 跑 pytest** 是底线门禁；没有它，任何测试都是纸面绿（TFS-6）。
- **先修死代码/违规再固化**，别让回归测「不存在的路」。
- **fixture 永远是真实录制 + 消毒**，不做数据库导演。

---

## 10. 结论（一句话到可执行）

> 设计成果：**一层可信度金字塔 + 已拆分的 unit/integration/regression 物理边界 + fixtures 快照库 +
> 一条真正会跑 pytest 的 `test.yml`**。第一批执行 = 让现有 149 进 PR：然后新 `tests/regression/` 的 P0；
> 在此之前先修 C4/C5/C8 死代码；再演进 nightly 会计（progress 恒 0 / 多 rollback 守卫）。
**Business Logic Changed: NO**（本设计仅文档，无代码/state/workflow 变动）。