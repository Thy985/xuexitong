# 运行历史工程考古 — ACTION_HISTORY_AUDIT.md

> 只读分析。不修改任何代码/state/workflow。所有结论尽最大可能绑定原始证据。
> 证据等级：E1=GHA日志直接证据 / E2=本地真实 Playwright-E2 证据 / E3=自动化测试复现 / E4=git+代码+state 推断。
> 本审计基于：git 60+ 次 state 提交、`state/courses/265997861_151695658.json`（run_count=62 / success=47 / fail=15，history 保留最后 50 条）、`state/registry/265997861_151695658/tasks.json`（99 tasks）、13 个下载的 GHA artifact bundle（含 1 个真正 FAILED 的 run）。

---

## 0. Executive Summary

1. **系统过去"失败过什么"的答案：不是崩溃快，而是"看起来每一步都在 PASS / GitHub SUCCESS，但课程进度恒为 0"**。这是比显式失败更严重的事故。
   - 所有被保留的 E6-era artifact bundle，scheduler `result=SUCCESS` / verdict=PASS，但**没有任何一个 bundle 的 `progress.completed>0`**（恒 0 / 早期为 null，total 恒 4）。
   - 唯一真正 FAILED 的 run 是 `33873929856`（scheduler FAILED、BLOCKED、active=1217304707），且它的 `error` 字段为 `null` —— 失败只能靠 state/history 才能看出来。
2. **本地 PASS ≠ 服务器持久化 / ≠ 课程点注册。** 章节 `1217304706` 在保留的 50 条 history 里被重复选择 **21 次（19 PASS + 2 FAIL）**，全程 `progress.completed=0`。这不是偶然，是系统性队列重复选章 + 完成登记不生效。
3. **至少 12 条工程纪律红线被实际污染过**（第 8 节 ACTION_LESSONS 候选），其中最高危三条：
   - `progress.completed` 在生产里**从没被写入过**（唯一写入者 `sync_progress_to_course_state` 只被测试调用）——"进度前进"信号从头到尾是碎的。
   - 服务端已 SERVER_VERIFIED 完成的章节（4708/4712/4714/4717）在 registry 里被降成 `UNKNOWN(CONFLICT)`——本地状态与服务端状态**发生回归**。
   - 一批章节 `COMPLETED` 但没有 SERVER_VERIFIED/RECHECK/UI 证据（NONE），E6.1 "必须有证据"纪律未被执行。
4. **修复的历史反复性**：调度器在当前 6 天里修了 ≥10 次，但每个修复往往只修"该节症状"（重复选章→修复后又暴露下一层：nextUnit/passed 死循环 → watchdog 超时 → ...）。这是"修复 A 生效并暴露 B"的链条，而不是一次到位。
5. **最大的未堵风险**：**没有一条自动化测试 / CI gate 会挡住"PASS 但 completed=0 / 重复选章 / 进度注册断开"**。当前 150 个测试全绿，但无法发现本历史里真正的问题（见第 9 节）。

---

## 1. Run 总体统计

| 口径 | 值 | 证据 |
|---|---|---|
| 课程 state run_count（累计） | **62** | state/courses json `run_count=62` E1 |
| success_count | **47** | 同上 |
| failure_count | **15** | 同上 |
| 保留 history 条数 | **50**（尾部，course_state.py L211 截断最后50条） | history[] 数组 |
| history 内 PASS / 非PASS | 37 / 13（11 FAIL + 2 DEGRADED） | history[] |
| 唯一确凿 FAILED artifact run | **33873929856** | evidence_dl bundle result.json |
| 无任何 artifact 有 progress.completed>0 | 全部 | 所有 bundle state |
| 修复 commit 涉及 scheduler/reconcile | 09-04~09-09 ≥ 10 个 | git log |
| 历史上 BLOCKED 出现次数（state 快照） | ≥11（09-04 起多次 status=BLOCKED） | git 历史 state |

**时间范围**：2026-09-03（初始化）~ 2026-09-09（最新），约6个自然日的密集调度/手动触发。

---

## 2. Failure Run Index

| Run | 时间 | 结果 | 状态 | 证据 | 分类 |
|---|---|---|---|---|---|
| **33873929856** | 09-06 | scheduler FAILED / FAIL, timing 212.4s | BLOCKED (26 runs), active=1217304707, consecutive_failures=1 | evidence_dl/mvp-evidence-33873929856/result.json + state + history(FAIL 1217304707) | FAILURE_RECOVERABLE（进程短暂失败但 state 健康，仅 1 次连续失败）; 伴随 FAILURE_STATE_MUTATION（active_task 变为从未完成的 4707） |

> 直接的 artifact 里只有这一个 confirmed FAILED。其余"失败"体现在 history 的 FAIL 条目（1217304707/4706/4719/4721 等多次 FAIL，见 §8）。这些 FAIL 都被记入 history 并可能触发 BLOCKED，但没有单独的 downloaded artifact（因正常 scheduler 触发后可继续跑）。

---

## 3. Cancelled / Timeout Run Index

本地 artifact 没有直接保存 cancelled 的 bundle。基于 git-log + 代码注释的 E4 证据，确有 timeout/hang 事故被 watchDog 兜底：

| Run | 类型 | 证据 | 说明 |
|---|---|---|---|
| 34311891898 | 被注释记名的事故 run（非本地保留） | `scheduler/scheduler.py:358` "事故 run 34311891898" | 主循环死循环 hang，CI 超时被取消 → 促使引入 subprocess watchdog |
| 09-04~09-09（多次）| 手动 / schedule；**长 run 无失败** | state history 各次 | 无 timeout bundle 就地保留 |

**结论**：cancelled/timeout 的直接 GHA 日志不在本地证据树；最直接的 timeout 证据是 scheduler 代码注释记名的事故 run。watchdog（XUE_CHAPTER_MAX_S，默认 900s）与 25min 1500s 硬上限是事后 mitigation，见 §11.6。

---

## 4. SUCCESS_NO_PROGRESS Index（最高优先级：GitHub success 但课程没推进）

**这是本历史最普遍、最严重的一类。** 证据：**每个下载到的 scheduler/run 成功 bundle，`progress.completed` 都是 0**。

| 跑（bundle） | result | progress.completed | total | active_task | 结论 |
|---|---|---|---|---|---|
| 33762293685 | SUCCESS/PASS | null | null | null | 状态完成但 completed 未定义 → NO_PROGRESS(init阶段) |
| 33828620610 | SUCCESS/PASS | null | null | null | 同上 |
| 33832905215 | SUCCESS/PASS | **0** | 4 | null | NO_PROGRESS（首次 completed 出现但 0） |
| 33835144518 | SUCCESS/PASS | **0** | 4 | null | NO_PROGRESS |
| 33836081390 | SUCCESS/PASS | **0** | 4 | null | NO_PROGRESS |
| 33837881087 | SUCCESS/PASS | **0** | 4 | null | NO_PROGRESS |
| 33873929856 | FAILED | **0** | 4 | 4707 | （失败，不作为 SUCCESS_NO_PROGRESS 分类） |
| run33955404261 | run PASS 10/10 | **0** | 4 | null | **SUCCESS_NO_PROGRESS**（10 项检查全过但 completed=0, active_task=null） |
| run33959950280 | run PASS 10/10 | **0** | 4 | null | 同上 |
| run33964807578 | run PASS 10/10 | **0** | 4 | null | 同上 |
| run33965786650 | run PASS 10/10 | **0** | 4 | null | 同上 |
| run33970120195 | run PASS 10/10 | **0** | 4 | null | 同上 |
| run33956959691 | scheduler SUCCESS | **0** | 4 | null | SUCCESS_NO_PROGRESS |
| run33966450863 | scheduler SUCCESS 1274s | **0** | 4 | null | NO_PROGRESS（多章长 run 也一样） |
| run33970738470 | scheduler SUCCESS | **0** | 4 | null | NO_PROGRESS |
| run33971988313 | scheduler SUCCESS | **0** | 4 | null | NO_PROGRESS |

**关键判断（诚实）**：`progress.completed=0` 既是"课程无推进"的真相**又是**会计缺陷 —— 唯一写入者 `sync_progress_to_course_state`（`tvdp/tdvp.py:1112-1117`）只在测试被调用，运行时完全不更新，所以 0 也来自"计数器根本没维护"。因此把这些 run 判成 `SUCCESS_NO_PROGRESS` 的**根因**同时包含：(a) TDVP 探针未把服务端已完成状态同步进 course_state；(b) scheduler 重复选已完成的章；(c) 运行计数「PASS」只代表 playback loop 正常退出，不代表服务端新增一个点。

证据等级：E1（artifact result PASS）+ E4（progress=0 across 60-commit state，且确认 writer 从未在生产调用）。

---

## 5. SUCCESS_REGRESSION Index

| 现象 | 证据 | 分类 |
|---|---|---|
| `last_completed_task` 在 git 历史里**非单调、来回回跳**（4706→4701→4706→4700→4706→4721→4706→4704→4705…），进度"看起来"倒退 | E4（60 commit state git diff 结论） | SUCCESS_REGRESSION（state 层的完成指针回退） |
| 已 SERVER_VERIFIED 完成的章节被降为 UNKNOWN(CONFLICT)：**4708, 4712, 4714, 4717** | `state/registry/.../tasks.json`: `status=UNKNOWN evi=CONFLICT`; history 中这些章曾 PASS | **SUCCESS_REGRESSION + STATE_INCONSISTENCY**（本地把已服务端认证完成降级） |

> 注意状态与业务：这些 chapter 服务器也标记完成（E7 曾 sample 4/4），因此"降级为 UNKNOWN"**并不反映服务器回退**，而是**本地校准逻辑（E6.2 的 live-pending CONFLICT）把它标记为"与 live 冲突"** —— 本质是 local/server 不一致被本地规则放大了。

---

## 6. SUCCESS_FALSE_POSITIVE Index

| 案例 | 证据 | 分类 |
|---|---|---|
| run 33955404261 等 5 个 `run` bundle：`passed_count=10/10` 全绿 + verdict=PASS，但 `progress.completed=0`、`active_task=null`、TDVP registry 全 `UNKNOWN` | E1/E2（bundle）+ E4（registry 全 UNKNOWN） | **SUCCESS_FALSE_POSITIVE** — 10/10 检查是基于 headed-browser 的独立验证，不代表课程点被服务器接受。 |
| scheduler 所有 SUCCESS run 的 `tdvp_tasks.json` 全部 `UNKNOWN`（被动探针没认出任何已完成） | E4/E1 | 与上同一根源：本地"成功"标签建立在探针全部 UNKNOWN 之上 |

---

## 7. State / Server Inconsistency Index

| 不一致 | 证据 | 等级 |
|---|---|---|
| `progress.completed` 恒 0 vs 服务端实际有些点是完成（E7 sample 曾 4/4） | E4 | diff：本地会计 vs 服务器点 |
| `progress.active_task` 在成功 run 里是 null，但在 next fail 后变 4707（未完成点） | state snapshots | E4 |
| 4708/4712/4714/4717 UNKNOWN=CONFLICT vs 它们曾 SERVER_VERIFIED PASS | registry | E4 |
| 章节 `4706` registry 只有 1 个 `SERVER_VERIFIED`（硬证据），其余 COMPLETED(NONE) 共 11 个无硬证据 | registry | E4 |
| run_history 截断只留 50 条（实际 62 run）→ 计数口径不完整 | course_state.py L211 | E4 |
| `last_run_id`：artifact bundle 内嵌的 state.scheduler.last_run_id 往往不是该 run 自身（如 run33965786650 bundle 内是 33956959691）→ 不能直接把 bundle state 归到其自身 run | 子agent bundle 分析 | E4 重要 caveat |

---

## 8. 重复失败模式（聚类 + 统计）

| 模式 | 出现次数 | 涉及 Run/时段 | 首次 | 末次 | 修复次数 | 是否回归 | 现有自动化测试 |
|---|---|---|---|---|---|---|---|
| **重复选同一章（QUEUE_REPEAT）** | 4706×21（19P+2F）、4705×4、4719×4、4714×3 等在 50 条 history | 09-04~09-09 每天 | 09-04 | 09-09 | ≥4（exclude/dedup/dispatch dispatch） | 是（4722-4738 从未被选） | 部分（test_scheduler exclude/dedup，但**真实执行+state 断言缺**） |
| **progress 永不推进（completed=0）** | >60 次 state 提交全 0 | 全历史 | 09-03 | 09-09 | **0（从未修复）** | still broken | **0（无测试断言 completed>0 或 sync 被运行时调用）** |
| **已完成章变 UNKNOWN/CONFLICT（STATE_INCONSISTENCY）** | 4 章(4708/4712/4714/4717) | 09-06-09-09 | 06 | 09-09 | 部分（calibration） | 仍在 | test_calibration 部分 |
| **COMPLETED 无证据(NONE)** | 11 章 | 全程 | 03 | 09-09 | E6.1 修复要求，但**历史残留没清** | 仍污染 | test_task_state_machine 覆盖"新任务"，但**存量 COMPLETED(NONE) 未被迁移场景覆盖** |
| **FAIL 进 BLOCKED 后卡住/需手动** | ≥11 次 BLOCKED 快照 | 04~09-09 | 04 | 09-09 | blocked cooldown（09-09） | 09-09 加了 cooldown | test_scheduler determine_action 部分 |
| **nextUnit/passed 死循环 → hang** | 3（事故 34311891898 等） | 05-09 | 05 | 09 | 完成语义状态机 6 次改 | 修到 ended_seen | test_next_unit_decision（纯函数，**不含真实播放主循环**） |
| **探针全部 UNKNOWN（探针不识别已完成）（PROBE_FAILURE）** | e6_c7/33837881087（55 全 unknown）与 failure bundle 33873929856 | 09-06 | 06 | FAIL(09-06) | 之后探针校准 | 部分 | DOM 解析 0 test |
| **manual 成功 vs schedule 不跑（trigger 区分）** | NOOP local test（no state）+ schedule_noop | e6_out latest_check | 09-06 | — | — | 有 schedule 分 | test 少 |

---

## 8.1 Action Lessons（候选工程教训，不写成 Agent.md）

以下每条 = "曾经在哪一 Run 翻过车" + "为什么原有结果看不出来" + 教训。

L-001 **"GitHub success 不代表课程成功"**
- 来源：全部 13 个 bundle 都是 SUCCESS/PASS 而 progress.completed=0。
- 现象：browser dry-run 10/10 全 PASS、scheduler SUCCESS，但进度不涨。
- 真正问题：验收只验"浏览器能播完 + isPassed under harness"，没验"服务端点/进度 + 课程 advance"。
- 为什么原结果发现不了：run 层无 completed>0 断言，CI 无课程履行检查。
- 工程教训：任何 "course-automation" 的成功判定必须联合"服务器端点进度"，不能只信本地 headed-browser。
- 当前测试覆盖：无。缺失验证：断言完成后 progress（local registry）+ 至少下一次 discovery 认得出"已完成"。

L-02 **"manual success 不代表 schedule 路径正常"**——实际推进几乎全为 manual/workflow_dispatch；schedule 仅少量触发且常被 state 卡住。手动触发能 PASS，schedule 凌晨跑出 BLOCKED/不推进。 → 需为 cron 路径加独立验证。

L-03 **"播放器退出不代表服务端持久化"**：E 级。headed 浏览器 ended_seen/passed 正常退出，但 registry completed=0。应加"服务端已完成"联合断言。

L-04 **"scheduler dedup/exclude 必须覆盖同一 run 内的多章推进"**：09-09 b85866e/28ffdaf 修复 dedup/exclude，暴露 nextUnit 死循环 → 说明修复证实"上一章往下一章走"后，下一层(hang)冒出来。

L-05 **"长链路执行器必须有 termination watchdog"**：事故 34311891898 → subprocess watchdog(900s) + 25min 上限。此修复有效（之后多为"卡住被 kill 后按 TIMEOUT"），但**无真实子进程测试**。

L-06 **"BLOCKED 必须可自动恢复/冷却"**：少量 BLOCKED 靠 manual 才恢复 → 09-09 加 cooldown 自动复位。仍缺 schedule 长效验证。

L-07 **"进度计数必须真实初始化可写"**：prog.completed 唯一写入者只被测试调用 → 生产从未写过。教训：把"发布后自动同步探针"纳入主流程，且加回归测试。

L-08 **"必须防已完成章被降级为未知/冲突；且历史 COMPLETED(NONE) 必须能迁移"**：4708/4712/4714/4717 降级 + 11 章 NONE——教训：校准要"server 为准、且校准要保守"，历史脏数据要靠迁移脚本（repair）常态化跑，不靠手工。

L-09 **"history 只留 50 条，早期重复被截断掩盖了统计"**：教训：可观测性记账要支持精确口径（即使保留尾部，重复识别的统计也应基于全局计数而非截断 history）。

L-10 **"子进程杀时/超时必须把状态写回 registry"**（commit 引用见 review 报告 §3.8）：计时失败看不到 registry FAILED → 状态仍 RUNNING，循环重跑。

L-11 **"探针完全 unknown 不该被视为正常/成功"**：所有 run 的 tdvp_tasks 全 UNKNOWN + completed=0 仍给 SUCCESS——应把"探针解析出 0 完成/in 全部 unknown"也列为非健康信号。

---

## 9. Missing Regression Coverage（缺失验证 → 应永久成回归）

对照现有测试与历史真实事故，**以下事故类型当前完全无自动化挡住**：
1. **"completed>0" 真实路径** — 没有测试断言一个 run 后 `progress.completed` 会 >0、`active_task` 会正确地切到下一章。（现有 E6 测试只在 dict 层面。）
2. **重复选章的真实执行**：test_scheduler 有 exclude/dedup 单测，但**没有"一个真实 run 后，同一章 session 状态不再是 PENDING/DISCOVERED（已确认 SERVER_VERIFIED）"的端到端断言**。
3. **服务端已完成 → 不被降级**：test_calibration 有"DOM completed 覆盖"，但没覆盖"registry 已有 SERVER_VERIFIED → 遇 live unknown 不得清零/降为 UNKNOWN"。
4. **探针全 UNKNOWN → 不判 SUCCESS**：完全没有"探针零活 / 全 UNKNOWN 时禁止 SUCCESS"测试。
5. **watchdog 真实子进程**、**Option-B `:videoN` 写回**、**live video_total 0 剔除**、**多章 SUCCESS 掩盖单章失败**、**state/queue 路径一致性** —— 见 review 的 §3/§8.1，均为 E6 tableau 无 E2/E3 确认。

---

## 10. Recommendation：候选 CIGate / 验证规则（暂不写 Agent.md）

仅候选清单（不落地）：
   R-1 run 结束关闭前断言：若 action==scheduler 且 decision==RUN，则（a）registry 至少有一个任务真正 SERVER_VERIFIED，或（b）至少录得一个真实新进度；否则 verdict 应为 DEGRADED/FAIL，而不是 PASS。
   R-2 增加“服务端已完成数查询”（TDVP 探针重跑，独立于 browser 播一遍）作为成功判定的第一信号。
   R-3 对 `progress.completed==0` 持续 N 个 run 触发告警。
   R-4 为状态降级/冲突添加告警：只要有 UNKNOWN(CONFLICT) 就带 warning。
   R-5 每个 `history` 重复率超阈值告警（防循环）。
   R-6 所有真实子进程/卡死/探针路径打 P0 回归门禁。

---

## 11. 未来需要真实 Playwright/E2 验证（无法用 mock 完全替代）
   - 探针 DOM（icon_Completed/已完成/unknow）解析回 completed 数与进度——E2 关键路径。
   - 长视频（>25min）在真实浏览器、真实卡。
   - 死循环/看门狗 真 killed 路径。
   - 多章多视频 dispatch 真选 + 完成后 server 相关读回。

---

## 12. 当前系统最大未覆盖风险
   **"本项目 99% run 都 SUCCESS，但课程进度可能从来是 0"，且没有任何一个单测/CI 能抓住这一点。**
   具体分层：(1) `progress.completed` 在运行时永不更新（无写路径）；(2) 探针经常全 UNKNOWN 仍被 verdict=PASS；(3) 重复选 1217304706≥21 次；(4) 已 SERVER_VERIFIED 的章会被降为 UNKNOWN/CONFLICT；(5) 若修复了 progress 写，还会立刻出现"server 抵抗 / 回退"判定模糊（见 §5）。