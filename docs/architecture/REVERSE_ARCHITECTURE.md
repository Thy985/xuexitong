# 刷课系统反向架构（全量探索 2026-09-21，复核修订 2026-09-22）

> 目的：对 `D:\Projects\Archive\xuexitong` 做多层逆向架构，**第一目标是理解**——不改造、不提重构方案、不评判风格。
> 方法：全部结论来自代码与仓库现状（git 历史、state 文件、fixtures、CI workflow）。标注 **「不确定」** 处为未逐行验证或存在多来源分歧的部分。
> 范围：主线核心链路 = scheduler 调度 → TDVP 探针 → registry 账本 → e2 播放引擎 → course_state 持久化；另附两个专项调查（`registry/k` 幻影目录、`catalog_map` 死代码岛）。
> 2026-09-22 复核：身份语义（`task_id` ≠ objectid）、cron 条数、章节状态机决策值、若干行号与文件路径已按当前代码逐项对齐；§3.3/§8（测试污染）标注为「部分缓解」，§6 I7（BLOCKED 释放）补第三条出口。

---

## 0. 图例与总览

- **真源分层**（沿用 `docs/architecture/PROGRESS_OUTCOME_DATAFLOW.md`）：
  - L6 超星服务器 = 最终真源；
  - L4 TDVP 探针 = 服务器视角的派生快照（`chapter_points.json` 是其缓存）；
  - L3 registry `tasks.json` = 本地 canonical 账本；
  - L2 e2e 引擎 `ExecutionResult`、execution_queue = 运行期派生；
  - L1 Browser / 播放 = 物理层。
- 图中 `A →(relation)→ B` 表示「A 指向/驱动 B」，方向为数据或控制流向。
- 「已确认」= 代码/文件/历史直接可证；「不确定」= 需进一步验证。

---

## 1. 业务模型（Business Model）

**业务场景**：一个无人值守的「自然播放」刷课代理：定时（GHA cron **两条**，UTC 16:00 = 北京 00:00、UTC 04:00 = 北京 12:00，相隔 12h）驱动 headed Chromium 打开超星学习页，**正常速度播放**任务点视频直到 ended_seen，随后以服务器判据（isPassed）确认真实完成，账本逐章推进，直至课程全部任务带证据完成。

**节奏契约（`run.yml` schedule 注释为准）**：一次触发只学 **1 个视频任务点**（schedule 下 `max_chapters` 兜底 1）。这不是性能限制而是**风控设计**——`executed` 对每次尝试计数，一轮最多 1 次失败 ⇒ 一晚最多 2 次，够不到 `consecutive_failures>=3` 的课程熔断阈值；两条 cron 相隔 12h 是为了避开 `concurrency.group: xuexitong-active-course` + `cancel-in-progress: false` 的排队。

**合规红线（硬约束，非可选项）**：
- 不构造/伪造/重放 `multimedia/log`，不改 `enc`/`playingTime`/`_t`；
- 播放必须真实（head + 真实网络），不允许 requestless 假播；
- 登录凭据只走 `ensure_login` cookie 门（v1.chaoxing 403 处理 + passport2），不得硬编码入库；
- 本地与 GHA 必须同构（`scripts/ci_local_run.py` 单引擎）。

**用户流程（操作面）**：
1. 初始化一门课：`app/run.py --action initialize --course-url <url>`（仅登记身份，不动站）；
2. 授权开关：凭据写入 `.env`（本地）或 GHA secrets（云）；
3. 被动推进：cron 触发 `--action scheduler`，一条链走完「探测 → 对账 → 选章 → 播放 → 回写」；
4. 手动干预：`--action run / switch`（按课程 URL 显式驱动）;
5. 证据留痕：每次运行写 `evidence/*.json`，状态变更 commit 到 git。

---

## 2. 核心实体 Graph

```
CourseIdentity (models.py:21) (course_id, clazz_id, cpi, title, raw_url, resolved_at_utc)
   ├─ key() = f"{course_id}_{clazz_id}"            ← 全仓库的课程主键；title 不进键
   → 解析出 CourseParams (course_id/clazz_id/cpi/enc/chapter_id/openc/hidetype/video_index)
   → state/courses/<key>.json  (CourseState)
        ├─ progress: {completed, total, active_task}   （派生，运行时维护）
        └─ status: NEW/ACTIVE/RUNNING/PARTIALLY_COMPLETED/COMPLETED/ARCHIVED/ERROR/BLOCKED

TDVP 探针 (fetch_course_discovery / fetch_course_detail_and_verify / live_verify_chapter)
   ├─ chapter_points.json  （服务器视角快照缓存）
   └─ discovery point rows → build_tasks_from_discovery（tdvp.py:818）
        └─ task_id = **章 chapterId**（tdvp.py:852 `task_id = cid or f"_gi{ch_idx}"`），**不是 objectid**
             ├─ "1217304738"        （章内第 1 个视频点，无后缀）
             ├─ "1217304738:video2" … （同章第 N 个视频，tdvp.py:861）
             └─ "1217304738:other"   （非视频任务点归并，tdvp.py:881）

   两套 id 必须分清（这是本系统最容易读错的一点）：
     · chapterId / task_id  = 账本与队列的**主键**，10 位数字，派生后缀 `:videoN` / `:other`；
     · objectid            = 超星 content 的 32 位 hex，**不进账本主键**，只在播放期用：
                             绑帧（`.ans-insertvideo-online[objectid]`）、`activate_target_point`
                             选播放器、`multimedia/log` 请求与 `passed_object_ids` 证据。
                             账本字段 `passed_object_ids` 是二者的唯一交汇点。

TaskRecord（registry tasks.json，canonical）
   ├─ status: DISCOVERED/PENDING/READY/RUNNING/VERIFYING/COMPLETED/FAILED/BLOCKED/STALE/UNKNOWN
   ├─ 双证据字段：completion_evidence（全部完成证据）+ verification（验证性证据）
   ├─ mark_completed(run_id, evidence_level, source, passed_object_ids)  ← 唯一合法完成入口
   ├─ mark_failed(..., failure_stage)（返回**状态字符串**；cf≥max_attempts → 升 BLOCKED）
   ├─ restore_for_manual_retry()（:287，BLOCKED→PENDING，清 cf、保留 attempts 与失败留痕）
   └─ mark_rollback / mark_stale / downgrade_to_*（reconcile 用）

CourseState (state/course_state.py)
   ├─ start via itemLock (RMW, fcntl/msvcrt fallback)
   └─ helper: update_course_state / run_course / init/activate/switch

e2e 引擎 (app/e2_headed_gha.py::run_test :624)
   ├─ ExecutionResult: exit_code / verdict / timing / timed_out / evidence.passed_object_ids
   ├─ 10 步检查：login → navigate → metadata → 3×reload → natural playback loop
   ├─ bind_video_state：src-objectid 与 frame 绑定；get_video_state 状态机
   └─ 点选择路由（2026-09-22 实证后的两条腿，`video_index` 决定）：
        · video_index<2 → `should_inject_v3` 真（:253）：注入 v3 油猴脚本接管连播（旧行为）；
        · video_index≥2 → **不注入 v3**，改由 `activate_target_point`（:310）在「src 含目标
          objectid」的那一帧内点 `button[class*='play']`，让站方自己的播放器起播该点。
        evidence.v3_route = "injected" | "skipped-for-in-chapter-target"（:782/:787）留痕走哪条。

utils/cookie_store.ensure_login：cookie-first 登录（.cache/cookies.json，gitignored）
scheduler (scheduler/scheduler.py)：编排者，见 §5
```

**关系图（实体 → 关系 → 实体）**：

```
Browser/播放卡 ──(ended_seen / isPassed 读数)──> e2e run_test
e2e run_test ──(ExecutionResult)──> scheduler::_run_one_chapter
scheduler ──(mark_completed/mark_failed)──> TaskRegistry(account)
scheduler ──(fetch_course_discovery)──> TDVP.probe
TDVP.probe ──(build_tasks)──> registry（DISCOVERED 落账）
registry(account) ──(done_chapter_ids_from_registry)──> 章节完成集合
registry(account) ──(_sync_progress_from_registry)──> CourseState.progress
CourseState ──(input)──> 下一次 determine_action（"RUN"|"NOOP"|"BLOCKED"；第四值 "ERROR"
                         属 ExecutionResult.decision，由 resolve 失败在 :556 直出，:33）
scheduler(manual) ──(restore_blocked_for_manual)──> TaskRegistry（BLOCKED→PENDING，仅人点名的章）
```

**真实实体 vs 展示层 vs 缓存派生**：
- 真源：超星服务器（L6）；本地 canonical = `state/registry/<key>/tasks.json`；
- 缓存派生：`chapter_points.json`（服务器视角缓存）、`execution_queue.json`（运行期队列）、`progress.*`（由 registry 派生）；
- 展示/报告层：`evidence/*.json`、scheduler stdout 日志 —— 不参与状态机，仅留痕。

---

## 3. 状态模型

### 3.1 状态集合
| 状态 | 归属 | 存储 | 突变者（mutator） |
|---|---|---|---|
| TaskRecord.status | registry（per course） | `state/registry/<key>/tasks.json` | `mark_completed / mark_failed / mark_rollback / mark_stale / downgrade_to_pending / restore_for_manual_retry` |
| ExecutionQueue | 运行期 | `state/registry/<key>/execution_queue.json` | `reconcile_queue`（唯一写入口） |
| CourseState.status | 课程 | `state/courses/<key>.json` | `update_course_state / run_course` |
| progress.completed/total | 课程 | 同上 | 运行时由 `_sync_progress_from_registry`（scheduler.py:795）驱动（单一入口） |
| chapter_points | 缓存 | `state/registry/<key>/chapter_points.json` | `set_chapter_point_snapshot`（task_registry.py:491，生产唯一调用点 scheduler.py:1411） |
| active_course | 全局单例 | `state/active_course.json` | `init/activate/switch` |
| cookie | 会话 | `.cache/cookies.json`（gitignored） | `ensure_login` |

### 3.2 单一入口审计
- **registry 完成状态的唯一合法写入口** = `TaskRecord.mark_completed(...)`；证据缺失/URL/导航推理一律 `ValueError`（"URL/nextUnit/navigation inferences are NOT valid completion"）。
- **执行队列唯一写入口** = `reconcile_queue`（同时做排序+去重+保存）。
- **进度（completed）唯一写入口** = `_sync_progress_from_registry`（registry 派生，非手动）。
- **课程状态唯一 RMW 入口** = `course_state` 的 file-lock 上下文（`_course_lock`）。
- **点级解冻有两条且只有两条入口**：服务端真源 → `heal_blocked_by_live`（reconcile.py:371，probe 内自动）；人工显式 → `restore_blocked_for_manual`（reconcile.py:439，只在 `trigger=="manual"` 且经 `only_chapter` 收窄到人点名的章，见 §5 Step 2.7）。两者之外没有任何「手改账本」路径。

### 3.3 Test-planted 状态（幻影状态的来源，2026-09-22 复核：部分缓解）
`tests/conftest.py` **仍然**只 pop 凭据（`CX_USER`/`CX_PASS`）、不重定向 state 目录；`TASKS_DIR`（task_registry.py:410）固定指向真实 `state/registry`。

**已缓解的部分**：会写账本的测试自带隔离 —— `test_scheduler.py:33`、`test_regression_p05_p06.py:41`、`test_regression_p0_watchdog.py:67`、`test_regression_p12_progress_advances.py:52`、`test_multi_video.py:20`、`test_calibration.py:23` 都把 `TASKS_DIR` patch 到 `tmp_path`。

**仍存在的缺口（实证，非推断）**：`tests/unit/test_rollback_priority.py:23/:35` 仍直接用键 `"k"` 调 `reconcile_queue → save_queue`，把 fixture 值（`task_id "Y"` / `chapter_id "chY"`）写进**真实 worktree** —— 该文件今日（2026-09-22 09:11）跑完仍重新生成 `state/registry/k/execution_queue.json`。见 §8。

---

## 4. 数据流图（Data-Flow）

```
                        ┌────────────────────────────────────────────────────────┐
                        │  L6 超星服务器（最终真源）                                 │
                        └────▲───────────────────────────────────┬───────────────┘
          (拉取目录/任务点)   │                                   │  (multimedia/log
                            │                                   │   isPassed 判定)
       ┌────────────────────┘                                   ▼
   TDVP probe ────chapter_points.json──────► reconcile / merge_done
       │  build_tasks_from_discovery                              │
       ▼                                                            ▼
  TaskRegistry ── mark_* ──► tasks.json（canonical）          e2e engine: first-response
       ▲                          │                               isPassed capture
       └──── done_chapter_ids（推导）──► _sync_progress ──► progress.completed
                                              │
                                              ▼
   active/reconcile → execution_queue.json → _run_one_chapter（子进程+watchdog）
                                              │
                                              ▼
                                     ExecutionResult → record_result → 状态回写
```

`merge_done_with_points` 用点级快照踢掉「尚未真完成」的 done 章；`stale_completed_by_catalog/points` 把「服务器视角仍有未完成任务」的 COMPLETED 降 STALE；`live_verify_chapter` 复核队首章；`heal_blocked_by_live` 仅对 live 真完成过的 BLOCKED 解封；`restore_blocked_for_manual` 是**唯一**由人触发的点级解冻（manual 腿、按章收窄）。精确判定与行号见 §7.3#2。

---

## 5. 控制流图（Control-Flow）

```
run_scheduler(course_url?, chapter_id?, trigger, run_id, max_chapters)   :512
 │
 ├─ Step 1  resolve_course(url)                  读身份（verify_via_browser 生产恒 False → 标题恒 course_<id>）
 │                                               解析失败 → ExecutionResult(decision="ERROR") 直出 :556
 ├─ Step 2  determine_action :186                RUN / NOOP / BLOCKED（schedule 下 consecutive_failures≥3 亦 BLOCKED）
 │    └─ BLOCKED：manual 立即放行 :168；schedule 需等 blocked_retry_interval（cooldown 到点放行一次）
 ├─ Step 2.5 cooldown 复位点只给最小推进          max_chapters=1 :604
 ├─ Step 2.7 人工显式恢复 :607（仅 trigger=="manual"）
 │    restore_blocked_for_manual(reg, only_chapter=chapter_id.split(":")[0]) :617
 │    → 被点名的章内 BLOCKED 点回 PENDING；schedule 腿不走这里（夜巡不自行放宽熔断）
 ├─ Step 3  [每章] _run_one_chapter :388
 │    ├─ 前置 _run_tdvp_probe :1195                浏览器探针：
 │    │    fetch_course_detail_and_verify（:1240）→ reconcile_registry
 │    │    → stale_completed_by_catalog / _by_points（L1 廉价校准，:1312/:1313）
 │    │    → merge_done_with_points（done 剔除「快照显示未完」章，:1331）
 │    │    → reconcile_queue（:1337）
 │    │    → heal_blocked_by_live（冻结章服务端真源恢复，:1361）
 │    │    → live_verify_chapter（:1395）→ set_chapter_point_snapshot（写点级快照，:1411）
 │    │    → 重建 discovery/registry/queue → 无视频章降级 other（points_prove_no_video 守卫 :857）
 │    ├─ 时长探测 _probe_video_duration_s :962 → poll_video_duration :887
 │    │    └─ duration_probe_policy(video_index) :868：第 1 点不激活、25s 预算；
 │    │       第 ≥2 点先 activate_target_point 再轮询、45s 预算
 │    └─ 子进程隔离（start_new_session）+ watchdog（video_watch_budget :946，基线 XUE_CHAPTER_MAX_S=900 :541）
 │        └─ 输出归档 + 证据 JSON（exit 124 = 看门狗判定；探测失败 → 回落静态 900s 并留 dur_err）
 ├─ record_result :233                             写 schedule state / history
 └─ _sync_progress_from_registry :795              progress.completed 0→N（由 done 章驱动）

并行约束：GHA concurrency.group 单一 active-course；每课程 file-lock；每运行 子进程隔离。
```

---

## 6. 系统不变量（验证过的 + 推论的）

| # | 不变量 | 判定 |
|---|---|---|
| I1 | **完成 = 全部章任务有 SERVER_VERIFIED/RECHECK/UI 证据**；无证据 → 未知/回退 | 代码级成立 |
| I2 | 完成唯一入口 mark_completed，证据为空即报错 | 成立 |
| I3 | 执行队列唯一写者 reconcile_queue，一回一改 | 成立 |
| I4 | 本地与云同构（同一 scheduler/e2e 路径） | 成立（ci_local_run 单引擎） |
| I5 | 不伪造 播放/log；关卡可选 | 红线约束 |
| I6 | progress 由 registry 驱动，天然单调 | 成立（P1-12 修复后） |
| I7 | BLOCKED 的释放有三条且只有三条出口：① 课程级 manual 立即放行；② 课程级 schedule cooldown 到点放行一次；③ **点级**解冻 = 服务端真源 `heal_blocked_by_live` 或人工显式 `restore_blocked_for_manual`（仅 manual 腿、仅人点名的章） | 成立（schedule 腿永不自愈点级冻结；`test_scheduled_run_leaves_the_frozen_point_frozen` 锁死） |
| I8 | reconcile 不得丢「回退」语义（rollback 优先于新章） | 成立（rollback_priority 测试） |
| I9 | **站方把章内任务点串行化**：同一章只有「页面当前位」那一个 `<video>` 能被真实播放；对非当前位要么由我方**点击它自己的播放键**激活，要么被别的播放抢走回合 | 2026-09-22 三次只读探测 + 一次 GHA 真 run 实证（见下） |

**I9 的实证内容（本系统最硬的一条外部约束，也是多轮误判的来源）**：
- 给**已完成**的当前点做 JS seek（拖到 90%）→ 无回钳、能连播，但**页面不会自推进到下一点**，而是把整章导航走（chapterId 跳到下一章）——「播完前点让页面自己切到 `:videoN`」这条路**已否证**。
- 注入 v3（连播油猴脚本）去学 `:videoN` → v3 每个模块帧都从**第 1 个** `<video>` 接管；点 1 在播时站点把目标点钉在 `paused=True`，180s 内 0 次翻转 —— **是我们自己的注入抢走了回合**，不是站点不给。
- 撤掉 v3、只点目标点自己的 `.vjs-big-play-button` → 该点持续播、站方自发为该 oid 记 `multimedia/log`，最终 `isPassed` → 远程账本 `1217304738:video2` = COMPLETED / SERVER_VERIFIED。

**未承诺/不确定（截至 2026-09-22 复核）**：
- L6「服务器真接受点」端到端证据仍**未做**（`PROGRESS_OUTCOME_DATAFLOW` §2 明确列为后续；本报告 §7.3#1）。
- `resolve_course` 标题退化：已核验（见 §7.3#3）——影响面窄（仅留痕/展示），不再是「不确定」。

---

## 7. 系统总模型：真源分析 / 结构风险 / 待查清单

### 7.1 真源（Source-of-Truth）分层结论
- **唯一真源** = 超星服务器；本地各层都是派生/缓存，且各自可能撒谎：
  - `progress.completed` 只反映本地账，不保证服务器真接受；
  - registry 的证据可自欺（假 COMPLETED 需 reconcile 反复纠偏）；
  - 因此判定「是否推进」必须以服务器探针（TDVP）+ 服务器渲染的判定为准，单一来源无法定案。
- **完成章判定**=`done_chapter_ids_from_registry`（全部 COMPLETED 且带证据）——但需与 live 采样比对（`live_verify_chapter`），防止本地假账绿。

### 7.2 结构风险（按严重性降序）
1. **test 污染 worktree state**（R1，2026-09-22 复核：**部分缓解，未闭环**）：conftest 仍不隔离 state 目录；6 个测试文件已自带 `TASKS_DIR` patch，但 `test_rollback_priority.py` 用键 `"k"` 那条**今日仍在真实 worktree 重新生成** `state/registry/k/execution_queue.json`。制造幻影状态（详见 §8、§3.3）。
2. **课程标题退化**（R2）：生产路径 `resolve_course(... verify_via_browser=False)` 使 identity.title 恒等于 `course_<id>`，课程标题从来不是真实标题（见实证数据）；后续若依赖 title 做展示会失真。
3. **`catalog.py` 死代码岛**（R3）：`build_chapter_map` 无生产调用，`state/catalog_map/<id>.json` 提交后从未被消费。见 §9。
4. **progress.completed 与 total 不一致**（R4，已核验）：`_sync_progress_from_registry`（scheduler.py:795-824）每次运行重算 `completed`（= done 章数），但 `total` 仅在 `is None` 时写一次（首次 = registry 发现章数）。真实数据 `completed=25 / total=4`（`state/courses/265997861_151695658.json`，2026-09-22）由此而来：completed 持续演化、total 是历史首写值的冻结残留 → 展示侧自相矛盾但**不是会计错误**，属字段语义分轨。
5. **令牌/凭据贴合**：cookie 在 gitignored `.cache/cookies.json`，若 cookie 过期（服务端 403），重登录需真浏览器，时间成本高。
6. **看门狗与外网瞬时断**（本地已有事实）：断网时视频卡、看门狗误杀，需要足够预算。
7. **`:videoN` 的时长探测仍会回落静态预算**（R5，2026-09-22 **仍开放**）：探测对第 ≥2 点已改为「先点击激活再轮询 45s」（`duration_probe_policy`），但真站一轮里子进程解析目标 oid 约 33s、点击→metadata 约 32s，45s 窗口经常不够 → 日志仍见 `看门狗降级 … -> 静态 900s`。对 4738:video2 无害（站方已存 1006/1130，900s 内能播完），但**一个进度低、时长接近或超过 900s 的 `:videoN` 会在真正播完前被砍**。候选修法：像 Step F 那样重试激活，或把子进程读到的 `video_duration` 回灌父进程预算。

### 7.3 待查/不确定点（2026-09-21 逐项核验，2026-09-22 行号与结论对齐）

| # | 原不确定点 | 核验结果（证据） |
|---|---|---|
| 1 | **L6 服务器真接受点端到端证明** | **仍未做**（维持 `PROGRESS_OUTCOME_DATAFLOW` §2「谨慎保留，不拍板」定位）；代码中无对应检查点。**注**：单个任务点的 `isPassed=true` → `mark_completed(SERVER_VERIFIED)` 已有真站实例（4738:video2），但「服务器进度与本地账整体一致」这门课级对账仍未做。 |
| 2 | `merge_done / stale_completed_by_*` 精确判定 | **已证（逻辑+触发链）**：真名 `merge_done_with_points`（task_registry.py:547，剔除快照显示未完成章）；`stale_completed_by_catalog`（reconcile.py:521，目录 job_remaining>0 且无点级服务端确认且无快照兜底）与 `stale_completed_by_points`（reconcile.py:557，快照有未 finish 视频点且无兄弟点）。生产调用点 scheduler.py:1227-1229 / 1312-1313 / 1331 / 1369-1371 / 1429-1431 / 1452-1455。**剩余未验**：「语义与服务器判定一致」——只证了本地逻辑与触发，未与服务器行为实测比对。 |
| 3 | `resolve_course` 标题退化的影响面 | **已证**：生产三处调用皆默认 `verify_via_browser=False`（run.py:90/115、scheduler.py:553），title 恒 `course_<course_id>`（真实 state 文件实证）。**影响窄**：identity 键只取 course_id+clazz_id（`CourseIdentity.key()`，models.py:30），title 仅存留痕/展示，不进调度路径。 |
| 4 | `chapter_points.json` 过期/刷新策略 | **已证**：生产唯一写点 = `set_chapter_point_snapshot`（定义 task_registry.py:491，唯一调用 scheduler.py:1411，对队首候选章 L2 复核后落盘）；**无 TTL、无删除路径** —— 陈旧章不刷新（除非再次成为候选+复核成功），也从不因课程变更清空。实证：4738:video2 于 9/22 完成后，该文件 mtime 仍是 **9/20 18:04** 未动。 |
| 5 | Windows 冒号 ADS 文件名覆盖范围 | **已证全覆盖**：全仓库以 task_id 拼文件名的位置仅 scheduler.py:417-419，且经 `artifact_slug`（:377）单一入口（`:→_`）；单元测试 `test_artifact_paths.py` 与回归 `test_regression_p2_evidence_attribution.py` 锁死格式。`{cid}:video2` 只作账本 key，不出现在文件名。 |
| 6 | （复核新发现）`progress.total` 语义 | **已证**：见 §7.2#4 —— `total` 只在 `is None` 时写一次，永不重算；无 TTL 语义。 |
| 7 | （原 §2/§I9 悬案）章内多视频点「怎么才学得到」 | **已定案（2026-09-22，三次只读探测 + 一次真 run）**：既不是 seek 前点（页面会跳章）、也不是 v3 连播（v3 抢回合、目标点被钉 paused）；唯一可用通道 = **不注入 v3 + 点击目标点自己的播放键**。落为 `should_inject_v3`/`activate_target_point` 两条腿，见 §2 e2e 引擎与 §6 I9。 |

**仍保持「未实测」的项**：L6 服务器真接受点（#1），与 #2 的「与服务器判定一致」半项。

---

## 8. 专项调查 A：`registry/k` 幻影目录来源（已闭环）

**现象**：`state/registry/k/` 下出现 `execution_queue.json`，但其课程键 `"k"` 从未在任何生产路径出现过 → 被怀疑为「工厂密钥注册表之外的幻影」。

**证据链（已闭合）**：
1. `grep -r "reconcile_queue\|builder" state tests`：调用方只有 `scheduler`（生产）与 `tests/unit/test_rollback_priority.py`（测试）。
2. `tests/unit/test_rollback_priority.py:23/:35`：`reconcile_queue("k", {"X": unknown, "Y": far}, done_ids(...))` —— **使用密钥 "k" 并期望 Y 入队**。
3. `app/registry/task_registry.py:410` `TASKS_DIR = _REPO_ROOT / "state" / "registry"`；`:631` `reconcile_queue()` 末端 `:746` 调 `save_queue(course_key, q)` → **直接写 `<TASKS_DIR>/k/execution_queue.json`**。
4. `state/registry/k/` 实测产物（**mtime 2026-09-22 09:11**，即本仓库当日跑完 `pytest` 之后）：
   ```json
   {"items": [{"task_id": "Y", "chapter_id": "chY", "priority": 0, "state": "READY", "course_key": "k"}], "reconciled_at_utc": "2026-09-22T01:11:24Z"}
   ```
   这正是该测试的 fixture 值（`TaskRecord("Y", "chY", "新课")` 的序列化）—— **污染可复现，一天跑一次就长回来一次**。
5. `.gitignore` 只命中 `state/**/execution_queue.json` → 队列文件不进 git，但**目录与文件留在本地工作区**，成为「测试产物幻影」；**tasks.json 未写**（该测试未触发 registry 持久化）——这个目录只含队列，不含 registry 账本。
6. **同类第二例，但成因不同（2026-09-22 新证，成因标注为「不确定」）**：`state/registry/execution_queue.json`（**顶层、不在任何课程键下**，mtime 9/5）装的是真课程 `265997861_151695658` 的队列。git 史显示它**曾被真实 scheduler run 写出并跟踪**（`12cc850`/`301a424`/`1a1963b`/`860d114` 等 "chore(state)" 提交），直到 `a9d127c`「untrack derived execution_queue」才脱离 git。形状上等价于 `_queue_file("")`（task_registry.py:413 把键直接拼进路径，空键即塌到顶层）—— 但**当次复核未定位到那条传空键的生产调用点**，故只登记现象、不拍板成因。它与 §8 的测试幻影不是一回事：这个是运行期产物，只是今天已不再被写。

**结论**：`registry/k` 是单元测试**无隔离地把自己种进真实 worktree** 的产物——不是运行时系统状态；不影响调度容错（生产永远用真实 identity key），但污染本地磁盘、滋生误判风险（本次调查早期就曾把它当成幻影课程）。修复方向（**只记录不实施**）：conftest 把 `TASKS_DIR` 与会话级 tmp 目录一次性绑死（现在靠 6 个测试文件各自 patch，属**逐文件补救**），或给这类测试改用可识别的 test 专属键并在 conftest 断言真实 `state/registry` 未被写入。

---

## 9. 专项调查 B：`catalog.py` / `catalog_map` 死代码岛（生命周期）

**现状**：`app/catalog.py`（108 行，纯正则解析超星 `#coursetree` 目录）、`tests/unit/test_catalog.py`（73 行）、`state/catalog_map/265997861_151695658.json`（475 行、79 个键，head 仍在、git 跟踪）存在，但**全仓库 grep 无任何生产调用方**（`build_chapter_map / load_catalog` 仅被模块自身与 test_catalog 引用）。

**生命周期**（全部实证）：
1. **诞生** `f0a3137`（2026-09-12 06:20，commit 信息「feat(catalog): 解析超星 #coursetree『章→节→任务』层级并持久化映射，支持回答某章某节」）：同一 commit 导入 parser + 一次 dump + snapshot test，共 3 文件 659 行。
2. **从未被消费**：`git log --all -S "catalog_map"`（目录名/字符串在任意文件内容中**零匹配**）→ 该字符串只存在于**目录名**；`build_chapter_map` 在整个 git 历史中也仅此一次出现（即 f0a3137 本身）。
3. **死亡后无变化**：`git log f0a3137..HEAD -- app/catalog.py tests/unit/test_catalog.py state/catalog_map/` 于 2026-09-22 复跑仍**空** → 该功能从诞生到今日（10 天）零改动、零调用、零引用。
4. **id 体系其实是对得上的（2026-09-22 修正原判断）**：本文件早期版本写「dump 的 key 是 DOM `cur<数字>`，与主线 registry 身份不对应，这是它复用不上的结构性原因」——**该判断经数据比对为伪**：`state/catalog_map/265997861_151695658.json` 共 79 个键（章节点 + 节节点混平铺），registry 的 **55 个 chapterId 全部命中**（55/55）。两套 id 就是同一批超星节点 id。
5. **它真正闲置的原因是内容而非身份**：value 只有展示字段（`chapter` / `chapter_sbar` / `section` / `section_sbar`，真 unicode 标题如 "互联网概述"），**没有任务点、没有 objectid、没有完成态** —— 回答不了主线任何一个问题（哪一章未完、该投哪个点、是否真完成），所以没有任何 reconcile/调度路径会需要它。顺带一个已核实的事实：R2 说课程标题在生产路径退化成 `course_<id>`，而**真实章/节标题恰恰只存在这份从未被连接的 map 里**。

**结论**：这是**一次性「章→节」问答工具**的残余：写了单次 dump 就弃用，parser 与测试以死代码形式留存。**删除** `app/catalog.py`、`tests/unit/test_catalog.py`、`state/catalog_map/` 三处，对系统的运行、测试、reconcile 零影响（无被依赖方）。业务上「这一章学到哪、算不算完成」的问答职能，由 TDVP 探针 + registry（章 chapterId 身份）那条主链路承担；catalog_map 只带展示标题，从没被接进任何判据。

---

## 10. 2026-09-22 复核改了什么（供后人判断本文哪部分来自哪一轮）

首版（09-21）是「读代码求理解」的一次性快照，本版逐项跑命令对齐现状。**被推翻或改写的判断**：

| 首版说法 | 复核后 | 依据 |
|---|---|---|
| `task_id = objectid（超星 content 的 objectid）` | **错**。task_id = 章 chapterId，后缀 `:videoN`/`:other`；objectid 是另一套 32 位 hex，只在播放期与 `passed_object_ids` 证据里出现 | tdvp.py:852/:861/:881 |
| `CourseIdentity(url, course_id, class_id, user_id)` | **错**。字段是 `course_id/clazz_id/cpi/title/raw_url/resolved_at_utc`，**没有 user_id**；键 = `course_id_clazz_id` | models.py:21-32 |
| 定时「UTC 16:00 一条 cron」 | **两条** cron（16:00 + 04:00），且 schedule 明确 `max_chapters=1` —— 节奏是风控设计不是吞吐限制 | run.yml:48-56 |
| chapter_points 写者 `save_chapter_points / pocedural` | **函数名不存在**。真名 `set_chapter_point_snapshot`，生产唯一调用 scheduler.py:1411 | task_registry.py:491 |
| `determine_action` 值 `RECORD/NOOP/BLOCKED` | **`RUN`/`NOOP`/`BLOCKED`**；`ERROR` 属 ExecutionResult，由 resolve 失败直出 | scheduler.py:33/:186/:556 |
| I7「BLOCKED 释放 = manual 立即 / schedule 定时」 | 补第三条：**点级**解冻（`heal_blocked_by_live` 或 manual-only `restore_blocked_for_manual`），schedule 腿永不自愈点级冻结 | reconcile.py:371/:439、§5 Step 2.7 |
| §9「catalog_map 与主线 id 体系不相容，故复用不上」 | **错**。55 个 registry chapterId **全部命中** catalog_map 的 79 键；它闲置是因为 value 只有展示标题、无任务点/完成态 | 实测两份 json |
| §3.3/§8「测试无条件污染真实 worktree」 | **部分缓解**：6 个测试文件已各自 patch `TASKS_DIR`；`test_rollback_priority.py` 的 `"k"` 那条今日仍在写 | grep + 文件 mtime |
| `watchdvog` / `tvdp/tvdp.py` / `resolve/course_resolver.py` | 笔误，改为 `watchdog` / `tvdp/tdvp.py` / `resolvers/course_resolver.py` | ls |
| 各函数行号（:1159/:531/:456/:492/:394/:730/:778…） | 全量重测并刷新；见 §5 与附录 | grep -n |

**新增（首版没有的机制）**：§6 I9 章内串行化与其三条实证、§2 的 `activate_target_point`/`should_inject_v3` 两条腿、§5 Step 2.7 与时长探测策略、§7.2#7 的 `:videoN` 预算回落 900s **开放缺口**。

---

## 附：关键文件导航（读完本文仍想深挖的入口）

| 主题 | 文件 |
|---|---|
| run_scheduler 编排 | `scheduler/scheduler.py`（`run_scheduler` :512，Step 注释 :548/:590/:603/:607/:624） |
| TDVP 探针/模型 | `tvdp/tdvp.py`（注意目录名 `tvdp`、模块名 `tdvp`；`build_tasks_from_discovery` :818、`read_chapter_job_points` :960、`live_verify_chapter` :1062） |
| 任务注册表/对账 | `app/registry/task_registry.py`（756 行；`TASKS_DIR` :410、`set_chapter_point_snapshot` :491、`merge_done_with_points` :547、`reconcile_queue` :631-746、`TaskRecord.restore_for_manual_retry` :287） |
| 对账覆盖正确性 | `app/registry/reconcile.py`（631 行；`downgrade_to_unknown` :119、`heal_blocked_by_live` :371、`restore_blocked_for_manual` :439、`stale_completed_by_catalog` :521、`stale_completed_by_points` :557） |
| 播放引擎 | `app/e2_headed_gha.py`（1429 行；`should_auto_resume` :187、`resume_paused_video` :211、`should_inject_v3` :253、`activate_target_point` :310、`get_video_state` :383、`bind_video_state` :567、`run_test` :624、v3 路由留痕 :779-787） |
| 课程状态持久化 | `state/course_state.py`（495 行） |
| 课程身份/URL 规约 | `models.py`（`CourseIdentity` :21 / `key()` :30 / `CourseParams` :43） |
| 解析错误兜底 | `resolvers/course_resolver.py`（`verify_via_browser` 默认 False 在 :113） |
| 登录 cookie | `utils/cookie_store.py`（251 行） |
| 本地权威入口 | `scripts/ci_local_run.py` |
| CI 配置 | `.github/workflows/run.yml`（两条 cron :55/:56，schedule 注释 :48-54 即「一次一个点」的节奏契约） |
| 完成度语义 | `docs/architecture/PROGRESS_OUTCOME_DATAFLOW.md` |
| 验收/真站结论 | `docs/engineering-review/ACCEPTANCE.md`（§4.10 方案 A 否证、§4.11 激活通道定案） |
| 历史审查 | `docs/engineering-review/ACTION_HISTORY_AUDIT.md` |