# AGENT_RULE_CANDIDATES.md
# 候选 Agent 规则（设计提取，不写入 Agent.md）

> 本文件只是**候选规则清单**，供后续落 Agent.md（未落地，仅提取）。
> 每条含：Rule / Why / Historical Incident / Required Verification / Evidence Level。
> 证据：E1=GHA 日志，E2=本地 Playwright，E3=自动测试，E4=git+代码+state 推断。

---

## 0. 第一优先规则

**R-0 「改动 scheduler 多章逻辑后，必须验证 progression（A→B→C），而不能只测单章选择。」**
- Why：历史上 dedup 修复生效后又暴露下一层 e2 hang；只测单章节选择发现不了「A 完成却不前进」。
- Historical Incident：dedup/commit 28ffdaf、b85866e 之后 → 事故 run 34311891898；考古章节 1217304706 被选中 21 次。
- Required Verification：7 步 Progression（RE P0-05）+ Multi-chapter（matrix §2.2）。
- Evidence Level：E4（复盘）+ E3（未来 regression）。

---

## 1. 完成语义 / 状态

**R-1 「不得从 nextUnit / URL 变化推断完成；完成必须有证据（SERVER_VERIFIED / RECHECK / UI-DOM）锚定。」**
- Why：nextUnit 自动切换不是完成凭据；旧代码曾把 nextUnit+passed 当完成而出现短暂假完成。
- Incident：完成语义篇 5.1（commit 7332a7e）+ run 34293378209 vs 34332366744。
- Verification：每次 mark_completed 必须带 TaskEvidence；用 run 号回归锚定。
- Level：E1/E4 + E3（test_next_unit_decision）。

**R-1b 「`progress.completed / active_task` 必须在 run 后推进或显式说明不推进（NOOP/BLOCKED）；不得让它恒 0 仍不触发告警。」**
- Why：runtime recorder 从不写 `.completed`，`sync_progress_advance` 只在测试被调用 → 60 commits 全 0，全绿但白跑。
- Incident：考古 47×SUCCESS 但 completed=0；`update_state_after_run`（course_state.py L372-424）无 `.completed` 写。
- Required：RE P1-12 + matrix §3.2（before/after/server/next-run/cross-day）。
- Level：E4。

**R-2 「多章结束时不得用「同轮任一 SUCCESS」把其它章 FAIL 冲掉并归零连续失败。」**
- Evidence：any_success 聚合 → 顽章永不 BLOCKED。
- Incident：matrix §3.7 / run。
- Required：RE P0-06（章 OK + 章 FAIL 并存，断言熔断不被清）。
- Level：E4。

**R-3 「watchdog / TIMEOUT 后必须把 registry 写回（≠RUNNING/VERIFYING）；错误不得被吞（error 非空可诊断）。」**
- Evidence：整批 timeout 只计预算不写 → 残留 RUNNING；考古唯一 FAILED bundle error==null，症状只在 history。
- Required：RE P0-01 / P0-07（真子进程→TIMEOUT→registry==FAILED）。
- Level：E1 考古 + E4。

---

## 2. DOM / fixture 纪律

**R-4 「DOM 解析（`.icon_Completed` / 已完成 / Completed / jobUnfinishCount 等）每次改动必须用录制真实 DOM 快照回归；不得依赖 inline 字符串假 fixtures。」**
- Why：DOM authority 漂移（class 黑盒）为本项目最高板块击穿风险。
- Historical：篇 4.2 反复改（39afefe, 87f44ed, 6620880 等，累计 6 次）。
- Required：RE P0-03（recorded DOM fixture → structured completed，drift-guard）。
- Level：E1(现网） + E3。

**R-5 「video 完成不得被「目录 DOM 显示 completed」静默覆盖；本地仍有真实 video pending 时应先 live 复核。」**
- Why：blanket downgrade 把真完成章降级、另一坑 DOM completed 覆盖真实 pending → 漏课。
- Historical：篇 3.3（问题5，pick_conflict 区域）。
- Required：RE P0-08 + P0-09。
- Level：E4。

---

## 3. 绑定 / 一致性纪律

**R-6 「单 module / 单一 canonical registry，不得双份 load/save 路径；task_id 迁移后必须 pop 旧键（不双键）。」**
- Why：双 registry 路径 drifts（load/save 不一致）；不 pop 旧键 → duplicate key 双处理。
- Historical：eb24c60（路径）+ §3.6（迁移双键）。
- Required：RE P0-11。
- Level：E4。

**R-7 「CourseIdentity 应用 cpi 或至少校验 state 与当前 URL 一致；不同 cpi 不得共享 registry。」**
- Historical：matrix §3.9。
- Required：RE P0-12。
- Level：E4。

**R-8 「单一熔断源（建议 scheduler.consecutive_failures 权威，成功一并清零），拒绝「累计 vs 连续」双语义。」**
- Historical：matrix §3.9。
- Required：RE P1-09。
- Level：E4。

---

## 4. 长链路 / 防挂纪律

**R-9 「长链路必须子进程 + watchdog（墙钟上限），不能只在同进程 sleep/budget 兜底。」**
- Historical：C1 事故 run 34311891898；scheduler 超时曾 25→50 min（2a2eb61）。
- Required：RE P0-01/02。
- Level：E1 + E4。

**R-10 「Playwright 通过但服务端点未推进——先判观测/会计问题（progress），不得直接判业务未推进，须拿 server 证据。」**
- Historical：考古全部 SUCCESS 但 completed=0。
- Required：matrix §3.2 双析。
- Level：E4。

---

## 5. 收尾：哪些应最终入 Agent.md（候选优先级）
在落地 Agent.md 时，至少把以下（按高到低）写入「项目已知坑位」：
1. R-0 多章必须 progression
2. R-1 完成必须有证据
3. R-3 超时必须写回 registry
4. R-4 DOM 必须真 fixture
5. R-1b progress 会计必须推进或说明
（以上为候选，未落地需人工确认 OK 后才并入。）