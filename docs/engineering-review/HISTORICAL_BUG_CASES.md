# 历史问题案例审计（Historical Bug Case Audit）

> 目标：为「补工程纪律 + 回归测试体系 + Agent.md」提供证据底座。
> 本文档梳理整个仓库 **159 次提交 / 150 项现有测试（149 pass + 1 skip）/ 3 条 CI workflow /
> E-series 报告（E3 / E5 / E6.1 / E6.2 / E7）** 中**曾经出过问题**的案例。
>
> 每条记录：现象 / 根因 / 影响 / 如何发现 / 修复方式 / 修复后如何验证 / 是否存在回归风险 /
> 当前是否已有自动化测试覆盖 / 若缺应补什么测试 —— 无论当前是否已修复均收录。
> 证据来源用 commit SHA / 代码行号 / 测试名 / 报告章节标注。
>
> 调查方法：git log/diff 全线审读 + 三路并行子 agent 深度代码审计（e2 核心 / state-scheduler /
> 测试覆盖）+ 本 agent 逐处复核代码行有效性。

---

## 0. 摘要：六大高风险类别命中情况

| 用户指定类别 | 命中度 | 代表案例 |
|---|---|---|
| 曾经 rollback 的修改 | ⚠️ 无正式 git revert，均为前向修复；存在「修复后推翻重做」 | §5.1 完成语义方案1→2；§3.3 状态降级 v1→v2 架构变更 |
| 反复修复的 bug | ✅ 高 | 完成语义状态机 ≥6 次（§6.1）；task_id 迁移 ≥5 次（§6.2）；DOM 状态解析 ≥4 次（§4） |
| 看起来没问题实际不行 | ✅ 高 | 95% 完成判据是死代码 + `initial_duration` 死参数；1500s cap 隐含边界；模块级全局 |
| 依赖真实网站才能发现 | ✅ 高 | nextUnit title 启发式、`clazzId` 大小写、`openc/hidetype`、video src/MSE、服务端 DOM 标记 |
| manual 成功但 CI/schedule 失败 | ✅ 有 | E3-C-001 `build_e3` NameError；e7→e6 双 registry 路径不一致 |
| 状态机/持久化/恢复 | ✅ 高 | 失败不写回→卡队头；task_id 迁移双记录；多章 SUCCESS 掩盖单章失败 |
| timeout / hang / 死循环 | ✅ 高 | 主循环永不退出（事故 run 34311891898）→ subprocess watchdog |
| DOM/selector 漂移 | ✅ 高 | `.icon_Completed`/`已完成|Completed`/`jobUnfinishCount`/cards iframe |
| 服务端与本地状态不一致 | ✅ 高 | DOM completed vs 持久化 registry 校准（blanket downgrade → DOM-aware） |

**最重要的待补回归测试（缺测 P0/P1）**
1. 单章执行 watchdog 的**真实子进程**（`_run_one_chapter`：Popen / wait(max_s) / killpg / exit-124 / 证据重读）——目前被 monkeypatch 掉，完全无测，而它恰恰是防挂关键。
2. `next_unit_decision` 的**真实调用方主循环**（`wait_playback` 空转 / 95% 死代码 / `isPassed` 二次 fetch）——目前只测纯函数。
3. 实时 DOM 解析（`.icon_Completed` / `.jobUnfinishCount` / `已完成|Completed` / `_CATALOG_EXTRACT_JS`）——漂移风险最高，0 test。
4. `e6/reconcile.py` 迁移漏删旧键造成同一任务双记录。
5. 多章聚合 `SUCCESS` 掩盖单章失败（`any_success` → 熔断归零）。
6. live 复核 `video_total==0` → 章被误标 `other` → 永久踢出队列。
7. `app/run.py` postflight 选项 B `:videoN` 写回定位。

---

## 1. 快速事实（Fast Facts）

### 1.1 Git 概况
- 159 commits，分布 **5 个自然日（2026-09-03 ~ 09-09，6 天强度迭代）**。
- 大量 `chore(state): update course state after scheduler/run/schedule run` —— **生产状态自动 commit 入库**，历史里全是真实课时进度快照噪音。
- **没有正式 git revert / rollback commit** —— 所有修正都是前向修复。

### 1.2 模块归属（证据引用口径）
| 模块 | 职责 |
|---|---|
| `e2/e2_headed_gha.py` | 视频播放/verification 核心（含 `next_unit_decision` 完成语义状态机） |
| `app/run.py` | 产品 CLI（initialize/run/switch/scheduler/probe），postflight 写回 registry |
| `scheduler/scheduler.py` | E6 调度（多章推进 / BLOCKED 熔断 cooldown / subprocess watchdog） |
| `e6/task_registry.py` + `e6/reconcile.py` | Task Registry + Execution Queue（canonical 状态与校准） |
| `tvdp/tdvp.py` | TDVP 探针（discovery + 服务端 DOM 复核） |
| `state/` | 持久化（`state/registry/<key>/tasks.json` = canonical） |
| `tests/` | 150 测试（149 pass + 1 skip） |

---

## 2. 视频播放核心曾出过的问题（e2 + app/run）

### 2.1 timeout / hang / 死循环

#### C1 播放主循环永不退出 → subprocess watchdog
- **现象**：`cmd_run → run_test` 的 Playwright 播放循环在「nextUnit 已切但 passed_object_id 已记录」等状态下可能**永不退出**；外层即便有 budget 也拦不住一次 cmd_run 内部永久阻塞。
- **根因**：完成判据缺失/错误，循环找不到退出条件（见 §6.5 完成语义迭代）。
- **影响**：一次 run 卡死 → CI 超时 → 无证据产出；同号并发被踢。
- **如何发现**：事故 run **34311891898**（scheduler.py L358 注释记名）。
- **修复**：把每次 cmd_run 放进**独立子进程**（`app.run --action run`），用 `subprocess.wait(timeout=max_s)` 提供**墙钟硬上限**；超时 killpg 杀进程树，标 **TIMEOUT**（区别于 FAIL）。见 `scheduler.py::_run_one_chapter`（L353-457），`max_s = XUE_CHAPTER_MAX_S`（默认 900），超时退出码 124。
- **验证**：手动触发一次超时；回归 `tests/test_scheduler.py::test_timeout_chapter_advances_queue`。
- **回归风险**：中。**该测试 monkeypatch 掉了 `_run_one_chapter`**，真实 Popen/wait/killpg/exit-124/证据重读**完全无测试**。
- **应补**：用真实 `subprocess` 起一个 sleep 超过 max_s 的假命令，断言 exit_code=124 + 杀进程树 + verdict=TIMEOUT；再用一个秒回 0 的假命令断言 PASS。**不要 monkeypatch 掉被测主体**。

#### C2 `MAX_PLAY_SECONDS=1500` 硬上限 → 超长视频章永远无法完成
- **现象**：单视频 >25 分钟（从 0 起播）时，`while time.time()-start < MAX_PLAY_SECONDS` 先到，循环以未 ended 退出，服务端不认完成。
- **根因**：`e2/e2_headed_gha.py` L104 固定 `1500`，L454 循环条件用了它，**未按 `initial_duration` 动态扩上界**。
- **影响**：通用场景下该章必然 FAIL/TIMEOUT ——「看起来每章播得完、实际超过 25 分钟就播不完」的隐含边界。
- **如何发现**：对任意时长 >25min 的视频章跑一次；或静态对照 L104 与各视频 duration。
- **修复方向**：cap 按本次待播视频总时长动态推导：`max(1500, sum(durations) + 缓冲)`。
- **现有测试**：否。
- **回归风险**：低-中。

#### C3 `wait_playback` 分支退化成长达 25 分钟的空转
- **现象**：`next_unit_decision` 遇「nextUnit 已切 + passed + 未 ended」返回 `wait_playback`（L231），主循环 L607-611 把它仅用于把 `nextunit_seen` 复位为 False。
- **根因**：复位后下一轮 `not nextunit_seen` → "none"，L552 又基于仍存的 chapterId **重新置 True**……死循环，唯一兜底是 1500s cap，而非「等 ended 的专属时钟」。
- **影响**：本可更早判超时的路径被放大成最长 25 分钟空等。
- **如何发现**：构造 `nextunit_seen=True, has_passed=True, ended_seen=False`。
- **修复方向**：给 wait_playback 独立时钟（`now + ENDED_GRACE_S`）到期判 TIMEOUT。
- **测试**：单测覆盖 `wait_playback` 返回值，但主循环实际行为无测。
- **回归风险**：中（需保留 passed/not-passed 两条语义）。

### 2.2 「看起来没问题实际不行」类

#### C4 `initial_duration` 是 `next_unit_decision` 的死参数
- **现象**：`next_unit_decision(…, ended_seen=False, initial_duration=0.0)`（L201-232）签名带 `initial_duration`，**函数体从不使用它**。
- **影响**：测试 API 传空参数；读者以为有 95% 逻辑，实际没有，制造假安全感。
- **修复**：把 95% 完成逻辑真正并入该函数并让 `initial_duration` 生效（与 C5 一起处理）。

#### C5 主循环 L617-621「95%+ 兼容边界」是**不可达死代码**
- **内容**：`if ended_seen and passed_object_ids and max_ct>0 and initial_duration>0 and max_ct/initial_duration >= 0.95: break`。
- **根因**：`ended_seen=True` 时 `next_unit_decision` 已在 L599-605 `break`；能走到 L617 时 `ended_seen` 必为 False → 该 if 恒 False → **永不执行**。日志声称的「Video ended … 95%+ exiting」实际从不打。
- **影响**：完成判据实际只有 `ended_seen`，95% 保护是摆设。
- **应补**：删除 L617-621，或把 95% 逻辑并入 `next_unit_decision`（并修 C4 的 initial_duration 陈旧）。删后 `test_next_unit_decision` 不会失败（因其从未测 L617 段）。

#### C6 `initial_duration` 在章内多视频下陈旧
- **现象**：`initial_duration` 只取一次（L446，第一个视频）；L483 src 切换只更新 `summary["duration"]`，`initial_duration` 永不更新。
- **影响**：若 C5 的 95% 逻辑被「救活」，多视频时用第一段时长判第二/三段 → 算错；L608-609 日志在多段下也打错分母。

#### C7 章内视频点切换以 `video src` 为凭据
- 现象：L464 `v_src = st.get("src") or ""` + L468 `if v_src and v_src != cur_video_src`。某段若 `currentSrc` 为空/MSE blob → 切换检测失效 → 多视频章漏切段。

#### C8 `isPassed` 用「二次 fetch 重放」判定
- **现象**：捕获到 `multimedia/log` 后，L519-525 用 `page.evaluate(fetch(u, GET))` **重放**该 URL 拿 body，再查 `"isPassed":true`（L530），而非在 response 回调缓存 body。
- **违反**：项目原则「只真实浏览器自然播放、不构造/伪造/重放 multimedia/log」。
- **影响**：二次 fetch 极可能触发新的 `/multimedia/log`、被跨域拒绝 → body `ERR:*` → isPassed 永远 False；服务器也可能返回与首次不同结果 → 序点误读。
- **不会自我放大**：被 L506-509 的 URL 去重挡住（二次 fetch 同 URL event 判重复跳过），但有隐藏依赖。
- **修复方向**：直接在 `response` 回调里 `body = resp.body()` 缓存 isPassed，删除二次 fetch。
- **测试**：无。回归风险中-高（改响应捕获需回归 E1.2）。

#### C9 `console_msgs_buffer` 是**模块级全局**、每个 `run_test` 内 clear
- 根因：L333 clear、L822 读 tail-20。多课程同名 task 串/并发 → 诊断 tail 相互踩；`page.on("console")` 注册时序竞态。
- 修复：改为 run_test 局部 buffer，`_capture_diagnostic` 接 buffer 参数。

### 2.3 快照覆盖竞态（真实站点暴露）

#### C10 Step I 覆盖 Step F 已确认的 `cards_has_video`（commit `70bea17`）
- **现象**：check4 在 Step F 已确认真实，Step I（post-nextUnit 快照采集）误覆盖为 False → 误判。
- **根因**：下一步骤快照无守卫地覆盖上一步骤已确认标志。
- **修复**：`if not evidence["checks"].get("cards_has_video"):`（L676-677），未置位才兜底赋。**该回归至今无独立单测**。

---

## 3. 状态机 / 持久化 / 恢复（scheduler + e6 + state）

### 3.1 失败任务永久卡在队列头部（E6.1 主题）
- **现象**：失败任务不处理好留在队列头反复执行；或 COMPLETED-but-no-evidence 重新入队。
- **根因（历史）**：旧版只写 PASS，**失败路径不更新 registry** → `done_ids` 不含失败任务 → 卡头重复；且 completion 无证据门。
- **修复**：`mark_completed(*, evidence=…)` 无证据抛 ValueError；postflight 失败分支 `mark_failed` + 立即落盘（app/run.py L262-289 注释记动机）。E6.1 加固「nextUnit ≠ 完成」。
- **测试**：`test_mark_completed_requires_evidence` / `test_cmd_run_fail_marks_registry` / `test_runtime_fail_writes_back` / `test_completed_task_not_queued` / `test_failed_task_can_retry` ✅
- **回归风险**：中（改过多次）。

### 3.2 新发现任务状态：DISCOVERED 而非从 DOM 继承 COMPLETED（`0c20458` → v2）
- **现象**：新发现的、DOM 显示完成的节点被直接置 COMPLETED → 未播放却标记完成 → 服务端点不推进。
- **修复**：一律 DISCOVERED，必须实际播放 + SERVER_VERIFIED 才 COMPLETED（scheduler L487 注释）。
- **测试**：scheduler 单测 ✅；但 DISCOVERED→COMPLETED 全链路（fake-page）无端到端测。

### 3.3 服务端 DOM completed vs 本地 registry（`c89dfcb` / `bc334ab`）——用服务端校准
- **现象 v1**：把 `COMPLETED(NONE)` 一律降为 DISCOVERED → 真正已完成章也被降、重播。
- **根因**：blanket downgrade 不区分「服务端已完成」vs「nextUnit 假完成」。
- **修复 v2**：以**服务端 DOM 渲染章状态**为准 —— DOM 有 completed 标记 → 保留 COMPLETED 且升级证据为 UI；否则降为 DISCOVERED（`c89dfcb`）。之后又加实时 status calibration（COMPLETED 可被 live pending 覆盖，`bc334ab` / `test_calibration.py` 154 行）。
- **测试**：`test_calibration.py` 较完善 ✅；但**真实 DOM 字符串（已完成 / Completed / .icon_Completed）的 live 解析 0 test**（仅 legacy regex 被测）。
- **遗留缺口（问题5，中风险）**：目录 DOM `completed` 只升级为**纯 UI 证据**，且 `pick_conflict_chapters`（reconcile.py L277-289）仅在 `dom_pending`（L289）时才触发 L2/live 复核。**当该章 `dom_status='completed'`、但本地 discovery 里仍有一个真实 video PENDING 时，本轮不会触发 live 反查 → 该章被「DOM completed」静默置 COMPLETED(UI) → 计入 done → 漏课**。相关函数 `_make_ui_completed`（L99）、`_dom_is_completed`（L73，用于 L200/L205/L218）。
- **应补测试**：`dom_status='completed'` + 该章有 pending video 的 case，断言不得直接 COMPLETED、应进入 live 复核或保留 PENDING。现有 `test_calibration` 只覆盖「DOM completed 不把另一 task 置完成」，未覆盖「chapter 级 DOM completed 把真实未完成 video 错置 COMPLETED」。

### 3.4 实时复核 `video_total==0` → 章被误标 other → 永久踢出队列
- **现象**：live 复核把真实视频章（`jobUnfinishCount`/纯文档边界）当作 non-video，`task_type="other"`，`reconcile_queue` L603 一律 `continue` 跳过 non-video → **永久踢出队列**（「看似合理实则漏学」）。
- **触发**：时序抖动即可。
- **应补测试**：live 复核 `video_total==0` 且该猜想为真实视频章的 case，断言队列仍保留该章 / 至少不因分类永久丢弃。

### 3.5 同一任务迁移后**双记录**（duplicate key）
- **位置**：`e6/reconcile.py::reconcile_registry` L155-195。
- **根因**：`result = dict(existing)`（L193）保留旧键（`_N_M`），`result.update(repaired)`（L194）只加新迁移键（`cid`），**从不 pop 旧键** → 同一任务两键并存。
- **影响**：`done_ids` 与队列重复处理同一章。
- **现测试覆盖**：无。
- **应补**：迁移场景单测——断言新旧键二选一、不双。

### 3.6 task_id 格式迁移史（按 title 匹配）
- **commit**：`7926ff7, ad13087, 0bf1469, 1bcd0d8, fe86f4f` —— `_N_M` → `_giN` → per-video、`_ch_idx/_cell_idx`、cell_index 反复迁移。
- **根本风险**：**按 title 匹配** 迁移（`by_title` 撞键，reconcile L81-84 / L146-149）——同名章节会误绑（E6.2 记录 `1217304708` 的「并列」同名）。无测试。

### 3.7 多章聚合 SUCCESS 掩盖单章失败
- **位置**：scheduler.py L634 `any_success = any(c not in chapters_failed for c in chapters_attempted)` → `result="SUCCESS"` → `record_result` 把 `consecutive_failures` 清空。
- **影响**：顽性失败的一章每轮都被同轮其它成功章「垫背」→ **永不进入 course 级 BLOCKED cooldown**，反复空跑、烧预算。
- **应补测试**：一章故意失败 + 一章成功共存，断言连续失败不会被全部归零。

### 3.8 TIMEOUT 无 registry 写回
- 现象：watchdog TIMEOUT 记为失败但**不计入连续失败熔断 budget**（L608-609），却**没有 `mark_failed` 写 reg** → 状态机恢复时该章仍感觉 RUNNING。
- **应补**：watchdog超时 case 断言 registry 状态被标 FAILED（或至少非 RUNNING/VERIFYING）。

### 3.9 其余已知风险
- `models.py::CourseIdentity` = `course_id + clazz_id` **不含 cpi**（L31）：不同 cpi 的课被认作同一 identity → 复用一个 registry，引擎 base URL 用 URL 的 cpi，若 cpi 改变则 iframe render 可能失败（no_cards）。修复方向：把 `cpi` 纳入 key 或至少校验 state 与当前 URL 一致。
- **双熔断不对称（问题7，中风险）**：course `status=BLOCKED` 由**累计** `failure_count>=3`（course_state.py L419-422，成功会把 status 复位 ACTIVE，但 failure_count 只增不减）触发；scheduler 熔断用**连续** `consecutive_failures>=3`（L216）。两套判据（累计 vs 连续）阈值与复位语义不同 → 熔断含义易混、语义不一致。应统一单一熔断源（建议 scheduler.consecutive_failures 为权威，成功一并清零）。
- **应补测试**：`test_three_failures_blocks_next` 只测 scheduler 连续失败；补「累计失败≥3 → status=BLOCKED → 再来 1 次 SUCCESS → status 复位 ACTIVE + failure_count 语义」。
- 多 cards iframe 只读首帧（tdvp.py L902-953 `break`）。
- `:videoN` 半前缀耦合：`_split_video_target`（scheduler L687）对 `:video` 半后缀判定 index=1；与 discovery 的 `.videoN` 分隔约定强绑定，缺边界测试（`4706:video` / `4706:video10`）。
- registry/queue 的 **RMW 不走 course lock**（写入用 .tmp+replace 原子，但读-改-写无锁）→ 多进程并发丢更新。

---

## 4. DOM/selector 漂移（最容易板块击穿）

### 4.1 URL 参数与卡片 iframe 渲染联动
- `clazzId → clazzid`（`e787869`，README 已知明明）：服务端区分大小写，大写的 cards iframe 不渲染 → FAIL(no_cards)。修 build_base_url。
- `openc/hidetype = 必需`（`ceca4b7` / `9d22d39` / README）：缺失 → 服务端不渲染 knowledge/cards iframe → `NO_CARDS_FRAME`。修复：URL 解析完整保留。
- **依赖真实站点回归**；单测覆盖 URL 解析（test_course_resolver）✅，但未测「参数缺失 → 无 iframe → fail stage」。

### 4.2 任务完成标记（TDVP/E6）解析
- 中英：`已完成` / `N个待完成`；英文 `Completed`（`6620880`）；class `.icon_Completed` / `.posCatalog_select` / `.posCatalog_finish` / `.jobUnfinishCount` / `.orangeNew`。
- **反复修复 commit**：`39afefe`（HTML 正则→ Playwright 真 HTML）、`87f44ed`（HTML→ DOM 提取）、`6620880`（英文 Completed + .icon_Completed + stderr）、`258efd8`（item-scoped isFinished + widget-aware video）、`1bcd0d8`（cell_index 选择器）、`9346ee0`（cell_index 作为正确索引）。
- **风险**：类名全是超星黑盒，**改一种 class 就全军覆没**；`_CATALOG_EXTRACT_JS` 0 test。
- **应补**：把「真实 DOM 字符串 → 结构化状态」提取为纯函数 + 用录制 DOM 快照做回归（drift guard）。

### 4.3 iframe 树 / 视频发现
- `get_video_state` 用 `knowledge/cards` src 正则 + `video#video_html5_api`、退回 cards→ananas iframe 里裸 video。
- `--disable-web-security` 掩盖跨域 `contentDocument` 失败。
- **应补**：iframe 树/selector 提取成可测函数。

### 4.4 侧栏 / banner
- `get_sidebar` 遍历 `[onclick]` + `$.jobUnfinishCount.value` / `.orangeNew.innerText`（类型 EWG）。
- banner「已学习了」全文本扫描——可能命中其它节点。
- 均无单测。

---

## 5. 反复修复的 bug 家族（六大）

### 5.1 完成语义状态机 `next_unit_decision`（最高频）
| commit | 修复 |
|---|---|
| `88b5f31` | 抛弃 title 启发式（误命中当前章标题）→ 只认 URL chapterId 变化 |
| `345ffa6` | 页面 v3 自动跳转到下一章时「当前章仍在 passing」的误判 |
| `2e1dee0` | 视频 ended+95%+passed 时主动退出（防在 nextUnit 重置里无限等） |
| `ba24cd8` | 多视频：track per-video src（同 chapterId 数视频点） |
| `7427494` | 引入 `next_unit_decision` 纯函数（方案1）与死循环回归 |
| `7332a7e` | **require ended_seen** 为真完成凭据；`nextUnit+passed+max_ct` 被历史进度污染会 33s 伪装完成（run 34293378209 vs 34332366744 对比） |
| 遗留 P0 | C4 死参 / C5 死代码 / C2 cap / C3 空转 |

### 5.2 task_id 格式迁移（cell/global index）——见 §3.5/3.6

### 5.3 reconciliation 服务端状态漂移 ——见 §3.3/3.7

### 5.4 完成证据门槛 —— §3.1/3.2、E6.1

### 5.5 多视频章 dispatch
- `56c6e11` 拆 per-video task；`b8f752f` per-video dispatch（Option B）；`e64d8e0` chain。
- 风险：`:videoN` 半截约定、`target_vi/video_count` 以 video src 为凭据（MSE 失效）、postflight 定位不精确。

### 5.6 双 registry/双队列
- `state/tdvp_tasks.json`（已弃用）⇄ `state/registry/<key>/tasks.json`（canonical）⇄ `state/registry/execution_queue.json`（derived cache）。
- commit：`eb24c60`（load/save 路径不一致）、`fe86f4f`（E7→E6 flow）、`9c2b6e1`（switch 同步）+ `a9d127c`（untrack 派生缓存）。
- 现状：load/save 路径仍散落（`repair.py` 用 `state/registry/<key>/tasks.json` 硬 `Path`，`task_registry.py` 用 repo-root 锚定）；无锁 RMW。

---

## 6. CI/workflow 层的既往问题

- `387279a`：+ `permissions: contents-write` + token push（否则 initialize/switch 无法持久化）。
- `b6b687`/`b86b68`：YAML 语法 / schedule trigger 打补丁。
- `d7f017d`/`a6a0662`/`0950d4d`/`4cecd25`/`c7f060c`/`fca55fa`：scheduler action 在 diagnostics & Final verdict 的多次兼容修补 —— **workflow 分支无自动化测试**。
- `d699297`：`run_\.json` 正则转义。
- `2a2eb61`：learn job timeout 25→50 min（暗示 25 曾不够）。
- E3-C-123：`build_e3` NameError（`65651fe`）——**测试脚本自身 bug**，CI 首次未盖住、仅 local 通过。

---

## 7. 安全类（纪律必备）

### Cookie / 会话凭据曾**入库并被 commit**
- commit `dbe86af`：删除 `state/cookies.json`（212 行完整 Cookie：JSESSIONID、jrose 等），并把 `utils/cookie_store.COOKIE_DIR` 从 `state/` 移到 git 未追踪的 `.cache/`。
- **现状**：凭据仍留在 git 历史（该文件被多个 commit 提交过），**即使已删、可从历史审计恢复** → 属于「凭据曾入库」事故，必须在纪律里：历史无需再暴露新值（rotate）、`.gitignore` 已防未来。
- 状态文件 `active_course.json` 等因跨 Run 需 commit，属设计；`cookies.json/execution_queue.json/chapter_points.json` 已 gitignore。

---

## 8. 现有测试覆盖 vs 空白

**已有好的回归锚点**
- `test_next_unit_decision.py`：用 run 号 34293378209 / 34332366744 锚定完成语义 ✅
- `test_calibration.py`（154 行）：服务端已完成 / live 覆盖 ✅
- `test_task_granularity.py`：4705 章「video 完成 + other 待完成」✅
- `test_task_state_machine.py`：nextUnit 从 1217304721 跳到 1217304708 的事故点不误判 COMPLETED ✅

**核心空白（缺测，需回归）**
| 待补 | 危险等级 |
|---|---|
| watchdog 真·子进程行为 + TIMEOUT 写回 | P0（防挂起） |
| 实时 DOM 解析（`.icon_Completed`/已完成/.job） | P0（漂移最高） |
| isPassed 二次 fetch → 改 response 回调 | P0（违规+竞态） |
| 完成判据主循环（wait_playback 空转 / 95% 死代码 / initial_duration） | P0 |
| 迁移 task_id 双键 | 高 |
| 多章 SUCCESS 掩盖单章失败 | 高 |
| video_total==0 漏学 | 高 |
| 双 registry 路径一致性回归 | 中 |
| course identity 含 cpi 的回归 | 中 |

---

### 8.1 conftest.py 路径不一致（测试可导入性隐患）
- `conftest.py` 仅 9 行，`sys.path.insert` 了 `resolvers / state / e2`，**漏了 `scheduler / tvdp / e6 / app / models`**（这些也是测试实际 import 的顶层包）。目前能跑通全靠 `tests/__init__.py` + CWD=repo root 的隐性路径；`test_integration:263` 用扁平 `from e2_headed_gha import …`，`test_next_unit_decision` 用 `from e2.e2_headed_gha import …`，两种 import 风格并存，靠路径顺序碰巧都对。
- **修复方向**：conftest 应插入 **repo 根**（而非各子目录），让全部顶层包用统一 `package.module` 风格可导入；消除扁平/含包两种混用。
- 另：`test_tdvp_integration._make_state`（L30-39）用 `STATE_DIR.__class__.__setitem__(…)` 改模块级路径常量，极脆；应改用 patch/monkeypatch。复制粘贴的 fixture（`tmp_state_dir` / `tmp_registry`）跨 4-5 个文件重复，应收拢为共享 conftest fixture。

---

## 9. 结论：优先补什么（给 test 体系 / Agent.md 的输入）

1. **把 P0/P1 固化为 `tests/test_regression_*.py`**（用真实 run 号 / 录制 DOM 快照做 fixture 锚定），补上 §8 表所列缺项。
2. **增加「测试即门禁」CI**：PR / push 跑 `pytest tests/ &&` 覆盖率检查；e2 浏览器回归保留 workflow_dispatch 手动触发（不阻塞日常）。当前没有任何一条 CI 会在代码变更时自动跑 `pytest`。
3. **收敛双 registry / 双 identity / 双熔断**，并把 task_id 迁移、course identity 含 cpi 纳入回归。
4. **修复死代码 / 死参数**（C4/C5/C6）与 isPassed 二次 fetch（C8）后再固化测试，避免「测试覆盖了个寂寞」。
5. **Agent.md**：把以上证据作为「本项目已知坑位」输入 —— 尤其是「绝不从 nextUnit / URL 变化推断完成；证据必须 SERVER_VERIFIED/RECHECK/UI-DOM；改 DOM 选择子必须留 fixture；超长视频、进程 watchdog、历史污染 repair」。