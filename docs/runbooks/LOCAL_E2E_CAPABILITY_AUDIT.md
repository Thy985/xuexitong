# LOCAL E2E Capability Audit（真实链路，突破只读）

> 本审计**打破只读**，用 `scripts/local_e2e_audit.py` 在本机（Windows + headless Chromium）驱动**真实 mooc2**，
> 一步步记录「实际观察到了什么」，而不只是记 PASS。证据文件：`docs/evidence/e2e_local_audit.json`（脱敏：enc/t/cookie 打码）。
> 目的：区分「真实课程（证明这套系统能连上真站）」vs「fixture/fake（边界逻辑可稳定回归）」各自负责什么。

## 1. 本次真实观察到的（证据摘录）

| 步骤 | 观察值 | 结论 |
|---|---|---|
| 环境/凭据 | CX_USER/CX_PASS 本机 .env 有取 | ✅ |
| browser | headless chromium 151.0.7922.34 | ✅ |
| 入口 | `mooc1.chaoxing.com/mycourse/studentstudy?chapterId=12173xxxxx&courseId=265997861&clazzid=151695658&cpi=506830460&enc=***` | ✅ 真实入口可达 |
| **登录** | `login.ok=true`, title=**学生学习页面**, 未停在 passport 登录页 | ✅ 真实登录成功 |
| 课程解析 | catalog_anchor_count=80, 标题=学生学习页面 | ✅ 真实目录 DOM 到位 |
| 目录发现 | chapter_nodes=79（`.posCatalog_select` / `.chapter_item`） | ✅ 真实目录 79 节点 |
| video 发现 | 找到真实 video src=`s2.cldisk.com/sv-w9/video/..sd.mp4?…`（签名 URL）；duration=null（元数据未及捕获） | ✅ 真实视频资源 |
| **multimedia/log** | **`ml_log_200_seen=true`, `ml_log_count=1`**，URL= `mooc1.chaoxing.com/mooc-ans/multimedia/log/a/506830460/<id>…` | ✅ **真实上报端点被触发** |
| **真实播放推进** | **currentTime 0 → 11.69s**（duration=906s），`currentTime_increased=true`（长播放模式真实 play()） | ✅ 真实播放推进 |
| **isPassed / server completion** | **`multimedia/log` 响应体含 `{"isPassed":true,...}`**（多帧含 **P0-04 单一真源**），于播放 ~12s 时出现；`isPassed_seen_true=true` | ✅ **CAP-005 → VERIFIED**（诚实注：该章 isPassed 本已为真，非本次播完才产生；但证明「真实服务端 isPassed=true 可观测」） |

## 2. 能力边界表 CAP-XXX

| 能力 | 状态 | 证据 | 说明 |
|---|---|---|---|
| **CAP-001 登录/课程进入** | **VERIFIED** | 真实登录 ok=true，title=学生学习页面 | `e2e_local_audit.json` |
| **CAP-002 目录 discovery** | **VERIFIED** | 真实 `#coursetree`：80 锚点、79 章节点 | 同上 |
| **CAP-003 单章 run（video 发现）** | **VERIFIED** | 真实 video src（s2.cldisk 签名 URL）被定位 | 同上 |
| **CAP-003b 单章 run（currentTime 推进）** | **VERIFIED** | 长播放模式真实 `play()`（非 muted）：currentTime 0→11.69s，duration=906s | 需真实 `--long`；短模式静音自动播受限不代表站点不推进 |
| **CAP-004 multimedia/log 上报** | **VERIFIED** | 真实 `multimedia/log` POST 200 出现在 wire（count=1） | 证明完成上报链路真实可达 |
| **CAP-005 server completion（isPassed）** | **VERIFIED** | `multimedia/log` 响应体含 `{"isPassed":true}`（P0-04 单一真源），真实可观测 | 诚实注：该章 isPassed 本已为真；证明「能可靠读服务端 isPassed」已成立，但「本次播完新产生完成点」未单独证明 |
| **CAP-006 registry/state 写回** | **PARTIAL** | 真实 E2E 未观察到写回（无播放完成），但运行时路径已由 P1-12/3D regression VERIFIED（scheduler→registry→progress） | fixture/fake 已锚定；真 run 未到 isPassed |
| **CAP-007 单章 scheduler** | **VERIFIED(fake/fixture)** | p05/p06/p12 regression：真实 scheduler 主循环 + mock 叶子 | fixture 层已封闭 |
| **CAP-010 timeout / watchdog** | **VERIFIED（真实 subprocess）** | `test_regression_p0_watchdog.py`：真实 `Popen(start_new_session)` + 卡死 child + 真实 `wait(timeout)/killpg` → exit 124 → verdict=TIMEOUT 写回；进程树清理不 mock 被测主体 | 用真实 subprocess，不拿真实课程制造死循环（P0-01/P0-07） |
| **CAP-008 multi-chapter / nextUnit** | **NOT_YET_VERIFIED** | 未跑（长时 + 写行为） | 需真实播放多章 |
| **CAP-009 cross-day persistence** | **NOT_LOCAL_VERIFIABLE** | 跨天需真实运行多日样本 | 设计给 CI/cron |

## 3. 真实课程 vs fixture/fake 分工（你点的）

- **真实课程**（本审计）：证明能连上真站、真登录、真目录、真 video、真 multimedia/log 上报。
- **fixture/fake（回归）**：证明边界逻辑（scheduler 主循环/去重/聚合/progress 派生）可稳定重复测试
  （`tests/regression/*` p05/p06/p12、`tests/unit/*`）。
- **规则**：不改真站、不伪造观察；拿不到证据就 PARTIAL/UNKNOWN/NOT_YET，不填 PASS。

## 4. 复现方式
```bash
# 本机 .env 有 CX_USER/CX_PASS
PYTHONPATH=. python scripts/local_e2e_audit.py          # 短观察（登录+目录+短播放 ≤25s）
PYTHONPATH=. python scripts/local_e2e_audit.py --long --max-s 220   # 长播放：真实推进 + 读 isPassed=true
# → 写 docs/evidence/e2e_local_audit.json（脱敏）
```
> 提醒：这是会触真站/真登录/真实播放的审计，会产生真实学习记录、触发验证码/风控，不要在正式环境反复跑。
## 5. 探针空（TDVP fetch returned empty）根因与修复

**现象（CI/Xvfb run.yml run 34564369602）**：scheduler 自动选章前，TDVP 探针报 `fetch returned empty`，旧逻辑回落到 `_fallback_chapter` 硬猜章，视频任务点推进错位（落到非目标章，而非「图里下一个真正未完成点」）。

**根因**：真实 `#coursetree` 先挂空 `<ul>`，章节节点是**异步渲染/填充**的。探针在 `wait_for_selector("#coursetree")` 一附加（shell 在、子节点还空）就立即 `extract`，在较慢的 CI/Xvfb 上极容易取到空。本地 `course_health.py` 多了一段 settle wait 所以未暴露；CI 环境更快触发。

**修复**（commit 见 `git log`）：
1. `tvdp/tdvp.py::extract_catalog_from_page`：首轮取空且 `#coursetree` 已存在时，**轮询等 `.posCatalog_select` 节点出现（≤12s）再重取**，显著降低「目录空→整轮空抓」。
2. `scheduler/scheduler.py::_run_tdvp_probe`：目录空时**先重试一次**；重试后仍空显示 `PROBE_EMPTY` 并返回 None（外层 NOOP），**不再调用 `_fallback_chapter` 硬猜**。
3. NOOP verdict 改为如实 `No pending / probe empty (no guess)`。
4. 新增回归：`test_extract_catalog_waits_for_hydration`、`test_probe_empty_catalog_returns_none_no_guess`。

> 仍待下一步：探针在 CI 上是 async hydration（而非登录失败）仍未独立抓到现场证据；若再次「空目录」，建议探针打印 `#coursetree` 的 `outerHTML` 片段辅助分辨。

## 6. 完成语义（方案A）——服务端 auto 切章 + 本轮 isPassed 即判完成

**现象（run 34573528666）**：auto 选章正确（选到 1217304712/已切、或下个 pending），
但长视频章在 watchDog 时 TIMEOUT：服务端 `isPassed=true` 且**自动把 URL 切到下一章**后，
旧引擎仍坚持要 `ended_seen=True` 才判完成；页面已切走，旧 video 的 `ended` 永不发生 →
死等 900s 看门狗被砍。

**根因**：`next_unit_decision`（app/e2_headed_gha.py）旧的 anti-fake 规则
「nextUnit+passed 但未 ended → wait_playback」。副作用就是：服务端已 PASS 并自动续下一章，
引擎却等一个已被切走的 video 的 ended → 死循环。

**修复（方案A，用户确认，Business Logic=YES）**：完成凭据扩展为两条真源（都要**本轮真实观测**）：
1. ended_seen=True（本轮视频真播到末尾）；
2. **nextunit_seen（URL chapterId 已切到另一章）+ 本轮真实 isPassed（passed_object_ids 非空）
   + 有实际进度(max_ct>0 或已知时长>0)** → exit_complete（服务端 auto 切章已 PASS = 服务端真源）。

护栏保留：`zero-progress`（max_ct=0 且时长=0）+ passed 也绝不判完成 → exit_switch；
`has_passed` 本身（无 URL 切换）不在别的分支 → none。绝不"单凭 max_ct>=95% / 历史进度"
判完成（旧 33s 假完成事故的护栏依旧）。

**回归**：`test_next_unit_decision.py`（方案A 行改为 exit_complete、零progress 行改 exit_switch）、
`test_regression_p0_e2e_term.py`（结束性不变量改为"ended 或 (nextunit+passed+进度) 才允许完整"）。
全量 `pytest`：182 passed + 1 skipped。
