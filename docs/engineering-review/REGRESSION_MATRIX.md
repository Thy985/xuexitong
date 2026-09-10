# REGRESSION_MATRIX.md
# 历史事故 → Failure Mode → Regression Test → 验证层 → CI Gate → Agent Rule（第二阶段工程化映射）

> 本文件是**工程设计**：只做映射与验证规格设计，**不实现测试、不修改代码/workflow/Agent.md**。
> 证据源：`HISTORICAL_BUG_CASES.md`（案例库，159 commits / 150 测试）+
> `ACTION_HISTORY_AUDIT.md`（GHA 运行考古，62 runs 全绿但 `progress.completed` 恒 0）。
>
> **五类 Outcome 严格区分**（禁止把 CI / Playwright / local 成功直接当课程成功）：
> Execution(进程) · Local(本地持久化) · Server(服务端点) · Progress(completed 推进) · Business(跨 run/day 不回退)。
>
> **验证层级**：UNIT=单测 / SM=STATE_MACHINE / INT=集成 / SUBP=子进程 / PW=Playwright / LIVE=真站 E2E / RUN=跨 run / DAY=跨天
> **优先级**：P0=死循环/错误完成/漏学/状态污染/不可恢复；P1=重复执行/错误调度/状态漂移/重要回归；P2=局部/诊断/低概率

---

## 0. First-Class 判定原则

1. **成功 = Execution 正确 ∧ Local 落盘正确 ∧ Server 有证据 ∧ Progress 推进且不回退。** 任缺即不得判 PASS。
2. **绝不因「可 mock」就把真实运行风险 mock 掉**。下列必须保真：
   - watchdog 真实 subprocess：`Popen / wait(timeout) / killpg / exit 124`（P0-01）
   - e2 主循环真实调用 `next_unit_decision`，必须证明可终止（P0-02）
   - DOM 解析用**保存的真实 HTML fixture**（P0-03）
   - server 持久化需跨 run / 跨天验证（§3）
   - scheduler 需多章 progression（P0-05）
3. `progress.completed=0` 双诊断见 §3.2，拿到 server 证据才能判业务未推进。

---

## 1. Regression Matrix

### 1.1 P0（死循环 / 错误完成 / 漏注册 / 状态污染 / 不可恢复）

| ID | 证据 | Failure Mode | 模块 | 为何漏掉 | 需补 Regression | Layer | 关键断言 / State-flow | 现有覆盖 | 缺口 | 优先级 |
|---|---|---|---|---|---|---|---|---|---|---|
| P0-01 | C1 事故 run 34311891898；scheduler.py `_run_one_chapter` L353-457 | 主循环永不退出→一次 cmd_run 永久阻塞；超时无 registry 写回 | scheduler | 测试 monkeypatch 掉 `_run_one_chapter`，真 Popen/wait/killpg/exit-124 无测 | 真实子进程 `sleep` 超时→killpg→exit 124→TIMEOUT→registry 写 FAILED；再起秒回命令断言 PASS | SUBP | A→子进程 RUN→超时→TIMEOUT→registry≠RUNNING | `test_timeout_advances_queue`(仅 mock) | 真子进程+写回 0 | **P0** |
| P0-02 | C3 / 完成语义状态机 ≥6 次（篇 5.1） | e2 主循环 `wait_playback` 空转，仅靠 1500s cap 兜底 | e2 主循环 | `test_next_unit` 只测纯函数，不测主循环终止 | 构造 nextUnit切+passed+ended_seen=False，断言在 ENDED_GRACE/watchdog 内终止 | INT+SUBP | →wait_playback(多轮)→终止 mark | 纯函数有 / 主循环 0 | 主循环全 | **P0** |
| P0-03 | 篇 4.2 DOM 漂移（已完成/Completed/.icon_Completed/.jobUnfinishCount/_CATALOG_EXTRACT_JS） | 超星改 class→全解析 0→误判 | tvdp DOM | DOM 解析无真 fixture | 录制真实 DOM→纯函数提取→structured completed；drift-guard | DAT+UNIT | DOM→{已条目}精确映射 | tiny inline | 无真实大 DOM | **P0** |
| P0-04 | C8 违反「不得二次 fetch」 | response 回调不用 body，二次 fetch 重放→isPassed 恒 False | e2 response callback | 无测；改需回归 E1.2 | 缓存 `response` body.isPassed，断言读取缓存，signature 无二次 fetch | SM+INT | {url,isPassed}→无重放 | 无 | 无 | **P0** |
| P0-05 | 考古 1217304706 重复选中；dedup→再暴露 e2 hang | 多章不前进 / 重复 A | scheduler 主循环 | `test_runs_multiple` monkeypatch，只问循环 | 7 步 Progression（见 2.1）：A 完→reprobe→选 B→完 B→再选；断言 A 不再被选、严格前进 | INT+SUBP | 完成集单调/选章有序 | mock | 真 progression 0 | **P0** |
| P0-06 | 篇 3.7 `any_success` | 多章 A ok+B fail→聚合 SUCCESS 归一雷击→頑固章不熔断 | scheduler L634 | 无 mixed 回报测 | 章1 OK+章2 FAIL 并存：断言 susaved 归零不是全局，记录每章 | SUBP(state) | 连续失败预算未被消退 | 无 | 无 | **P0** |
| P0-07 | 篇 3.8 watchdog TIMEOUT | 超时后 registry 残留 RUNNING/VERIFYING | registry+scheduler | 只测 queue 前进 | 超时终态断言：≠RUNNING/VERIFYING，应 FAILED/TIMEOUT | UNIT+SUBP | TIME→FAILED | 半 | 无 | **P0** |
| P0-08 | 篇 3.3 / 考古 4708/4712/4714/4717 被降 UNKNOWN | server 已完成章被校准降级 | reconcile/calibration | 不覆盖「已 VERIFIED→遇 live unknown 勿清零」 | server 已完成章校准后仍 COMPLETED / 不 UNKNOWN | SM+INT | server done → 不 UNKNOWN | test_calibration 不覆盖 | gap | **P0** |
| P0-09 | 篇 3.3 结尾坑 / pick_conflict L277-289 | DOM completed + 本地真实 video 仍在 pending →静默 COMPLETED→漏课 | reconcile | dom_pending 分支不会 live 复核 | DOM completed + 该章有 pending video：断言不得直接 COMPLETED，应 live 复核/保 PENDING | SM+INT | dom=completed⋀video pending→维持 pending | 无 | 无 | **P0** |
| P0-10 | 篇 3.4 video_total==0 → other | 真实视频章被当 non-video→队列 continue 永久丢弃 | scheduler/queue | 只测 point-snapshot | live 复核 `video_total==0` 且该章真有视频：断言不被丢、入复核 | SM+INT | video_total==0 && has_video→不 other-drop | points-snapshot | queue-drop 0 | **P0** |
| P0-11 | 篇 3.5/3.6 迁移双记录 | 旧键未 pop→双键并存→重复处理 | reconcile L155-195 | `test_reconcile` 无迁移 | 旧→新迁移断言 single-key 不双 | UNIT | 迁移后仅新键 | 无 | 无 | **P0** |
| P0-12 | 篇 3.9 CourseIdentity 不含 cpi | 不同 cpi→共用 registry→no_cards | models.CourseIdentity | 无测 | 两个 cpi 相异→线 identity 不同 | UNIT | cpi 异→不共享 | 无 | 无 | **P0** |

### 1.2 P1（重复执行 / 错误调度 / 状态漂移 / 重要回归）

| ID | 证据 | 失效 | 模块 | Regression | Layer | 现有 | Gap | 优先级 |
|---|---|---|---|---|---|---|---|---|
| P1-01 | 篇 4.1 clazzId 大小写 / openc- hidetype 必传 | 参数缺→iframe 不渲→no_cards | resolvers | 缺参/大小写→fail_stage | UNIT+DAT | 解析✅ | 缺 iframe→fail | P1 |
| P1-02 | C4/C5 dead 参/死代码 | 仅 ended_seen 判完成，95% 摆设 | e2 | 删 L617 / 让 initial_duration 生效 | UNIT/SM | incomplete | 有 | P1 |
| P1-03 | C6 多视频 initial_duration 陈旧 | 多段用首段判后续 | e2 | 切 src→第二段判对 | UNIT | 无 | 有 | P1 |
| P1-04 | C7 视频切段以 src 为凭据(MSE) | currentSrc 空→漏切 | e2 | src 空/blob | PW | 无 | 无 | P1 |
| P1-05 | C9 console_msgs_buffer 全局 | 并发/同名诊断互踩 | e2 | 局部 buffer | UNIT/PW | 无 | 无 | P1 |
| P1-06 | C10 Step I 覆盖已确认 cards_has_video | 已确认被覆盖返 False | e2 | 未set 才兜底 | single | 无 | 无 | P1 |
| P1-07 | 篇 3.1 失败卡队头（已修反复） | 失败不写→卡头 | registry | 失败→空可重试/done 不含失败 | SM/INT | test_cmd_run_fail ✅ | 完整需 keep | P1 |
| P1-08 | 篇 3.2 新发现 DISCOVERED v2 | 未播放标完成 | scheduler | DISCOVERED→VERIFIED 全链路 | SM | 单测✅ 全链路半 | 有 | P1 |
| P1-09 | 双熔断（累计 vs 连续） | 熔断语义混 | course+scheduler | 单一源 之后鉴权成功清 | SM/INT | 只连续 | 有 | P1 |
| P1-10 | :v-child半前缀 | :video/:videoN 边界 | scheduler._split | 边界:video10 | UNIT | 无 | 无 | P1 |
| P1-11 | 双 registry/双 queue | load/save 路径乱 | repair/registry | 路径一致性 | INT | 无 | 无 | P1 |
| P1-12 | progress.completed 恒 0 会计 | 观察面骗（runtime 不写 .completed） | course_state | 见 §3.2 | §3.2 双诊断（before/after/server/next-run） | 0 | 关键 | **高(建议并入 P0-05)** |

### 1.3 P2（局部 / 诊断 / 低概率）

| ID | 证据 | Regression | Layer | 现 | 优先级 |
|---|---|---|---|---|---|
| P2-01 | iframe 树 / get_video_state | 提取纯函数 | INT+fixture | 无 | P2 |
| P2-02 | 侧栏/banner（[onclick]/orangeNew/jobUnfinishCount） | 纯函数提取 | INT+fixture | 无 | P2 |
| P2-03 | 内置 fetch 类似基础诊断 | — | — | — | P2 |
| P2-04 | course_lock 无 RMW 锁 | 记录风险 | — | 无 | P2 |
| P2-05 | 只读首帧（多 iframe） | PW 断言 | PW | 无 | P2 |

---

## 2. Progression 专项

### 2.1 7 步（P0-05 可执行化）
```
State A: 表有 READY 章 A（server 未交）
  Action: scheduler 选 A 完成 → mark_completed+EVIDENCE
State B: registry[A]==COMPLETED(带证据)，course_state 同步
  Action: re-probe 把已完成集纳入 excluded
State C: 断言 A 不再被选；下述选到 B（前进）
  Action: 执行完成 B
State D: registry[B]==COMPLETED，done 集单调
Assert: 最终持久化成功、reload 一致
```
每步断言：A 不重选（=不 QUEUE_REPEAT），B 不跳（=不漏学）。对照 run 号 34293378209/34332366744。

### 2.2 Multi-chapter（完整链路，非单函数）
- 真实遍历→逐章→再 probe；不 mock 主轴。
- 断言严格前进、`progress.completed` 单调、不出现同一章多播。

### 2.3 禁止随意绕 mock 点
watchdog 真子进程 / e2 主循环真 / DOM 真 fixture / server 跨 run / scheduler 真 progression —— 全部保真。

---

## 3. Business Outcome Assertions

### 3.1 分层
Execution / Local / Server / Progress / 跨日 — 每层需各自证据，不可单一叠回。

### 3.2 `progress.completed=0` 双析
先标「观察/会计失败」B。`sync_progress...` 只在测试被调、runtime 不写 .completed，故即使 server 推进观察面也坏。
- before / after / server（探针/DOM）/ next-run（不重选 A）/ cross-day（集合不回退）共五点验证。

### 3.3 PASS 强制 gate
Execution+Local+（Server 或强证据）+Progress 推进（除 NOOP/BLOCKED）。否则 DEGRADED/FAIL 参考 AUDIT§10.

---

## 4. 测试假安全感（TEST_FALSE_CONFIDENCE）
| 编号 | 表面 | 背后 | 破解 |
|---|---|---|---|
| TFS-1 | pure fn 绿 | 主循环未测 | INT P0-02 |
| TFS-2 | watchdog 测 | mock 子进程 | 真 SUBP P0-01 |
| TFS-3 | 字段测 | 未持久化 | §3.2 |
| TFS-4 | local PASS | 无 server | §3 |
| TFS-5 | inline fixture | 无真 DOM | P0-03 |
| TFS-6 | CI 全绿 | 3 workflow 都不跑 pytest | 加 pytest gate |

> **TFS-6 是当前体系最大假安全点**：`run.yml`/`e2.yml`/`e3.yml` 均无 push/PR 自动触发，**从不自动跑 pytest**；e2/e3 仅 dispatch。→ 150 测试全绿从不在 CI 真正执行。

---

## 5. 现有覆盖（对照）
- 有效锚点：`test_next_unit_decision`(run 锚)、`test_calibration`、`test_task_granularity`、`test_task_state_machine`、`test_points_snapshot`、`test_multi_video`。
- 表面覆盖：`test_timeout_chapter_advances_queue`(mock)、`test_runs_multiple_chapters`(mock)、tdvp marker(inline)、calibration（VERIFIED→UNKNOWN 缺）。
- 完全无测：P0-04/06/09/11/12、（07 半） /（08 缺）；P1-03/04/05/06/09/10/11/12 等。

---

## 6. 附录：落地顺序
1. 建 `tests/test_regression_p0.py` 逐步实现 P0（watchdog 真进程、主循环、DOM fixture）。
2. 接既有锚点测试追加进度断言。
3. 打分入 `CI_GATES_CANDIDATES.md`。
4. 候选规则转 `AGENT_RULE_CANDIDATES.md`（非 Agent.md）。