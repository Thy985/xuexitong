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