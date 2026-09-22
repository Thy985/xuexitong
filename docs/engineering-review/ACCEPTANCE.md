# xuexitong 验收体系（ACCEPTANCE）

> Draft v1.0 · 2026-09-15
> 关联：`docs/REQUIREMENTS.md`（需求）、`docs/architecture/DEVELOPMENT_PLAN.md`（实现）。
> 本文件回答三件事：**每个里程碑"通过"长什么样、拿什么证据判、未通过怎么办**。
> 凡是"看起来能跑"都**不算**验收通过；必须以本文件定义的客观证据为准。

---

## 1. 验收的四层金字塔

> 越往上越接近真实，越往下越便宜、越快、越可自动化。任何一层不过就 **不得**进入下一层。

```
L4  上云回归（GHA cron 真跑）            —— 只验收 Step「上云」
L3  真站冒烟（真账号/真课程）             —— 本地，Playwright 起浏览器连真站
L2  功能/稳定性验证（本地，含真浏览器但可离线/可控）  —— Xvfb 有头 / headless=False
L1  纯逻辑测试（单元/集成/回归）         —— pytest，CI 已跑（现 403 passed + 1 skip）
```

### L1 · 纯逻辑测试（CI，pytest）
- **跑法**：`.github/workflows/test.yml`（push/PR 自动）→ `pytest tests/unit tests/integration tests/regression`。
- **判据**：全部通过；`--maxfail=5` 内不爆炸（不允许零星断言失败仍绿）。基线：2026-09-21 实测 **403 passed, 1 skipped**（共 404 collected，69.8s）。
  > 09-15 基线 209+1/72s → 09-20 上午 296+1/68s → D1~D7、D10、D11、P1 十起缺陷各带回归后 364+1/69.8s
  > → P1 帧绑定（+10）、方案1 服务端真源恢复（+10）、D12 点枚举去重（+5）、绑定 reload/导航策略（+11）、起播激活选择器与 N>=2 范围闸门（+2）后 403+1/69.8s。
  > **耗时也是判据**：同一套用例从 239s 降到 68s，差额正是"测试偷偷起真浏览器"被堵住的时间（见 §4.4）；
  > 之后又加了 68 条用例，耗时几乎没动（68→69.8s），说明新用例全在 fake 层。
- **证据**：`pytest.log`（失败时自动上传 artifact）。
- **门禁**：任何 PR/M0~M3 改动必须保持 L1 全绿。**此层失败 = 阻断。**

### L2 · 本地功能/稳定性验证（真浏览器）
- **跑法**：`python scripts/ci_local_run.py --action scheduler --trigger manual`
  （M0 落地；Xvfb `:99` 或 Windows headless=False）。
- **判据**：
  - `verdict == PASS` 且 `verification_10` 10 项全 True；
  - 连续 `--max-chapters 2` 跑 3 次，同一课程进度**只前进、不重复、不卡死**；
  - 任一失败 `failure_stage` 非空且产生截图。
  - **可配浏览器（R-08，M1）**：`XUE_BROWSER_CHANNEL=msedge`（或 `XUE_BROWSER_EXE`）下 scheduler 仍能 launch 且 evidence/归因不变；未设时用默认 chromium（回归基线）。
- **证据**：`evidence/local_<ts>.json` + `diag_*.png`（如有失败）。
- **通过 = M0 达成；** L2 稳定 3/3 前，禁止进入 M3 上云。

### L3 · 真站冒烟（真实学习通）
- **跑法**：真实 `CX_USER/CX_PASS`，`scripts/mooc2_probe.py`（已有）+ 一次真实 run。
- **判据**：登录成功、抓到 `studentstudy` 目录、一个视频任务点真正推进且 registry 写 `SERVER_VERIFIED`。
- **证据**：冒烟输出 + 截图归档到 `docs/evidence/`。
- **目的**：证明"代码在连接真实的 lyse URL/DOM 时也能跑"，堵住"本地能跑但真站被改版/验证码挡住"。

### L4 · 上云回归
- **跑法**：GHA `run.yml` 定时+手动，`action: scheduler`。
- **判据**：
  1. cron 连续 3 个自然日都产出 PASS（或非 BLOCKED 且证据完整）；
  2. **本地 L2/L3 与云的 `verification_10` 序列逐项一致**（diff 为 0 差异）——即"本地行、云就行"被直接证明。
- **证据**：每个 cron run 的 evidence artifact + 一致性 diff 报告。
- **回退触发**：若云端 evidence 与本地偏差（同样输入不同 `verification_10` 结果），**回退自查本地**，不强行修云。

---

## 2. 里程碑验收矩阵（Requirements → 判据 → 证据）

> Gate 语义：**达标（可进入下一阶段）/ 未达标（修复后再验）**。

| 里程碑 | 验收项 | L 层 | 达标判据 | 证据 |
|---|---|---|---|---|
| **M0 本地基线** | R-01 本地 runbook | L2 | `ci_local_run.py` 跑通 scheduler，PASS 可复现 | `evidence/local_*.json` |
|           | R-02 环境可复现 | L1 | 新机器按 `LOCAL_FIRST_SETUP.md` 10min 内可 run | 文档 pass |
|           | R-03 诊断打包 | L2 | 故意失败能产出 `diag_*.png`+registry dump 的 zip | zip 留痕 |
| **M1 健壮自愈** | R-04 自动续播 | L2 | `paused` 后自动 recovery，`recovered_count` 记录 | evidence 字段 |
|             | R-06 静音/倍速 | L2 | 开启后 `currentTime` 正常推进、无报错 | 运行日志 |
|             | R-07 轮询降噪 | L2 | 真异常 warn/error 出现；expected 静默 debug | 日志分级 |
|             | R-08 可配浏览器 | L2 | `XUE_BROWSER_CHANNEL=msedge` 时仍能 launch 且 evidence 一致 | launch 冒烟 + evidence |
|             | R-05 滑块(可选) | L3 | 开启时滑块自动拖动成功；关闭时不触发 | 登录冒烟 |
|             | **多课时稳定性** | L3 | 长挂4h不崩、`verification_10` 稳定、无随机失败 | 4h 记录 |
| **M2 状态机** | R-10 防重复 | 1/2 | 已完成 chapter 重复 run 不 reset 进度 | registry 用例 |
|             | R-11 多课程 | L1 | `course_urls` 列表逐课程独立持久化 | 单元测试 |
|             | R-12 失败清零 | L1 | PASS 后 `consecutive_failures==0 & blocked==0` | 单元测试 |
| **M3 上云** | R-20 同构引擎 | L4 | `run.yml` 调 `ci_local_run.py`（不重写核心） | workflow diff |
|             | R-21/R22 证据一致 | L4 | cron 3日 PASS + 本地/云 `verification_10` diff=0 | 3 张 evidence |

---

## 3. 验收执行与留痕

- **执行**：每个里程碑由维护者跑对应 L 层验收；`pytest` 由 CI 自动跑。
- **留痕**：验收结果以**验收记录表**追加到本文档第 4 节（非覆盖）。
- **定版**：一个里程碑**全部子项 PASS 且证据可复现**才标记 `✅ 完成`；任一 FAIL 打回 `🔁 修改`，不空过。
- **单一事实来源**：验收结果由本文档承载，避免"口头说好了但没证据"。

---

## 4. 验收记录表（滚动追加）

| 日期 | 里程碑 | L 层 | 验收项 | 结果 | 证据文件 / 实测 |
|---|---|---|---|---|---|
| 2026-09-15 | L1 基线 | L1 | `pytest tests/unit integration regression` 全绿 | ✅ 达标 | 本地 209 passed + 1 skipped；CI run 35440057875 → **210 passed in 72s** |
| 2026-09-19 | M0 | L1 | 升 playwright 1.62.0→1.63.0 后 L1 无回归 | ✅ 达标 | 同上（本地/CI 双测）；`cfb2875` |
| 2026-09-19 | M0 | L1 | R-02 环境可复现（新机器按 `LOCAL_FIRST_SETUP.md` 可 run） | ✅ 达标 | `uv venv` + `uv pip install` 一次成功；`PROJECT-PASSPORT.md` §3 记录全流程 |
| 2026-09-19 | M1 | L2 | R-08 可配浏览器：`channel=msedge` 能 launch 且断言一致 | ✅ 达标 | chromium→`153.0.8010.12` / msedge→`153.0.4234.32`，`set_content`+`inner_text` 均命中；复用 `chromium-1243` **零下载** |
| 2026-09-19 | M0 | L2 | R-01 本地 runbook：`ci_local_run.py` scheduler 跑通且 `verdict==PASS` | ✅ 达标 | run `local-1789822612`：章 `1217304750` **PASS 10/10**、435.4s、`SERVER_VERIFIED`、`banner 26→27`；证据 `evidence/chapter_1217304750.json` |
| 2026-09-19 | M0 | L2 | R-01 续：`--max-chapters 2` 连续 3 次只前进不重复 | ⏳ 待验 | 单次 PASS 不等于稳定；M0 完整达标仍需 3/3 |
| 2026-09-19 | M0 | L2 | R-03 诊断打包 `--collect-diagnostics` | ⏳ 待验 | 本轮无失败，未触发打包路径 |
| 2026-09-19 | M3 | L4 | R-20 同构引擎上云（1.63.0 在 GHA 生效） | ⏳ 待验 | `run.yml` pin 已改并推送；需一次云端 scheduler run 闭合 |
| 2026-09-20 | M0 | L2 | R-03 诊断打包 `--collect-diagnostics` | ✅ 达标 | 6 个包真实落盘：`evidence/diag/diag_20260919_{221712,221730,221751,222837,222914,222951}.zip`（各 ~14KB，含 registry dump+日志）。**注意**：触发它们的是两次失败实验，不是"故意造失败" |
| 2026-09-20 | M0 | L2 | R-01 续：`--max-chapters 2` 连续 3 次只前进不重复 | ❌ 未达标（两轮均作废，非结论） | 第 1 轮 `l2_stability_20260919.log` 被并发重型 pytest 污染（同一套测试 387s→139s，2.8×）；第 2 轮 `l2_stability_clean2` 三次全 `SCHEDULER_CRASH`（`UnicodeEncodeError: 'gbk' …'\u26a0'`，见 §4.3） |
| 2026-09-20 | 缺陷 | L2/L4 | **P0：`head_cid` 取到 TaskRecord repr → 每次 run 静默误降一章** | ✅ 已修 + 账本已回填 | `9cdcb3b`/`cbf57b9`/`003762b`；真源核对 22 章全部确有视频点（`evidence/ledger_video_points_20260920_064452.json`）；详见 §4.2 |
| 2026-09-20 | M1 | L2 | R-04 自动续播（`video.paused` → `play()`，带 `ct>0` 只管续播不管起播） | 🔁 代码达标，真站未验 | `f1e72b3`+`00c98dc`；12 个单测覆盖判据。**尚无一次真站 run 观测到它触发**（两轮 L2 都因上表原因作废） |
| 2026-09-20 | 加固 | L1 | 测试隔离：单测不得起真浏览器 / 不得带真账号 | ✅ 达标 | `tests/conftest.py` 会话级剥 `CX_USER/CX_PASS`；`_probe_video_duration_s` 缺凭据不起浏览器；跑测试期间 chrome 进程数实测 0 |
| 2026-09-20 | M0 | L2 | R-01 续：`--max-chapters 2` 连续 3 次只前进不重复（第 3 轮，机器干净） | ❌ 未达标（真实缺陷，非实验问题） | `evidence/l2_stability_clean3_20260920.log`：run1 `1789858799` 708 PASS 465.2s + 714 PASS 588.4s；run2 `1789859924` **又选 708** FAIL 24.3s；run3 `1789859977` **还是 708** FAIL 28.1s。详见 §4.5 |
| 2026-09-20 | 缺陷 | L2 | **P0：多视频章只投第 1 点 + 校准把 COMPLETED 打回 UNKNOWN（账本震荡）** | ✅ 已修（L1 已证，真站待验） | 9 章 `COMPLETED→UNKNOWN`（722/730/732/734/737/738/741/750/751，全 `SERVER_VERIFIED+CONFLICT`）；`1217304708:video2` 已建为 DISCOVERED 却永远轮不到。`8075a0c`（降级校准不再用章级 job_remaining 推翻点级服务端确认）+ `2182a11`（主 reconcile 按点级快照拆条）；详见 §4.5 |
| 2026-09-20 | 缺陷 | L2 | **自适应看门狗从未生效**（时长探测 4/4 全失败） | ✅ 已修（L1 已证，真站未验） | 4 条降级日志的 st 均为 `{'currentTime': 0, 'duration': None, 'paused': True, 'readyState': 0}` —— 探测没等 `loadedmetadata` 就取时长；`poll_video_duration` 改为轮询到 `duration>0`（25s / 60 次上限），缺凭据不再起浏览器 |
| 2026-09-20 | 缺陷 | L2 | **D3：重投同一章就地覆盖上一轮产物** | ✅ 已修 | `_archive_existing()` 在 spawn 前把 `chapter_<task>.json` 与 `.scheduler.stdout.log` 按 UTC 时间戳归档（同秒冲突追加 `-n`）；回归见 `test_rerun_same_chapter_keeps_previous_evidence` |
| 2026-09-20 | 缺陷 | L2 | **D5：父进程按本地码读子进程 UTF-8 产物 → 归因整段丢失** | ✅ 已修 | `open(evidence_path)` 无 `encoding` → Windows cp936 解 UTF-8 抛 `UnicodeDecodeError`，被 `except Exception: pass` 吞掉；一个缺陷同时造成 verdict 退回 FAIL、`failure_stage: null`、`passed_count: null` 三个症状。**又一处本地/云不对称**（Linux CI 永不出错）。详见 §4.5 第 3 条更正 |
| 2026-09-20 | M0 | L2 | R-01 续：`--max-chapters 2` 连续 3 次只前进不重复不卡死（**第 4 轮**） | ✅ **首次达标** | `evidence/l2_stability_clean4_20260920.log`：6 次全 PASS，选章两两互异（708/722·730/732·733/734），`COMPLETED 23→29`；汇总第一次带上 `passed_count: 10`。详见 §4.6 |
| 2026-09-20 | 缺陷 | L2 | **D7：E6.2 refine 的 by_title 迁移吃掉点级兄弟记录（82→74）** | ✅ 已修 + **真站已验** | `<cid>:videoN`/`:other` 与 `<cid>` 同 title → 被当成"task_id 格式迁移"合并 pop；护栏失效 → 第 4 轮 run1a 23s 空投已完成点。复验：`tasks=87→87`、`next_task=1217304708:video2`，第 2 点 `isPassed=true`；见 §4.7 |
| 2026-09-20 | 缺陷 | L2 | **D10：点级 task_id 的 `:` 在 Windows 变成 NTFS 备用数据流** | ✅ 已修 + **真站已验** | `dir /r` 实证 0 字节空壳 `chapter_1217304708` + `:video2.json:$DATA`；`_archive_existing` 归档的是空壳 → D3 对点级任务失效。`artifact_slug()` 单一入口；产物已无损迁回，复验见 §4.7 |
| 2026-09-20 | 缺陷 | L2/L3 | **P1：`--video-index` 只当停止条件，且把"到达目标段"当"播完目标段"** | 🔁 终版根因=目标点未绑定（见 §4.8 终版） | 子日志证明页面**确实换源到点 2**（`switch → src=69a6c4c5…`），但同一秒 `break`、`max_ct=0s` → 唯一失败项 `7_currentTime_growing` → 整章 DEGRADED。`video_count` 在 src 切换时自增，条件却不看 `ended_seen`。Windows 与 GHA/Linux 同现象。第一版修法 `target_segment_done()` 被 run 108 真站复验证伪（宽限从未进入）；终版修法=objectid 帧绑定，见 §4.8 终版 |
| 2026-09-20 | 缺陷 | L2 | **D11：累计 `failure_count` 被当"连续失败"用 → 单次失败锁死整门课** | ✅ 已修 + 后果已由真实 PASS 解除 | `run_count 104 / failure_count 31`，成功从不清零；D7 复验那一次 DEGRADED 直接把课程打成 `BLOCKED`（`scheduler.py:214` 见之即拒调度）⇒ 明晚 nightly 会拒绝学习。课程层与调度层（`ss.consecutive_failures>=3`）层次不同，问题是这条锁**名不副实**。修后复验：`BLOCKED→ACTIVE`、`failure_count→0`。见 §4.7 |
| 2026-09-20 | 修复验证 | L2 | head_cid / stdout 编码 / 降级不再静默 三项在真站生效 | ✅ 达标 | 同一份日志内：`E6.2 head=TaskRecord` **0 次**、`E6.2 head=<纯章号>` 4 次、`has no video` **0 次**、`⚠️ 看门狗降级` 4 次且不再崩 |
| 2026-09-21 | 缺陷 | L2 | **D12：`read_chapter_job_points` 去重键 `marker\|text[:20]` 吞掉空文本的兄弟视频点** | ✅ 已修 + 真站读数实证 | 708 诊断：两视频行 marker 全同、innerText 同为空，仅 objectid 不同 → 第二点在读数前被丢 → live 报 `total=1`，方案1 恢复永远命不中。去重下沉为纯函数 `job_rows_to_points`（oid 身份去重、无 oid 退回文本键）；修后真站读数 `total=2 finished=2`，`:video2` 据此解冻（§4.9） |
| 2026-09-21 | 缺陷 | L2/L3 | **P1 残留：headed 会话中绑定帧出现后消失，且 reload 恢复对绑定模式有害** | ✅ 已修（reload 策略）+ 误判归因修正（probe v4）+ 方案 A 落地（§4.10） | 复验日志：绑定帧 1s `found=True` → 11s 起 `target_frame_not_found`；同页 headless 只读探测两帧都在。reload 把页面打回点 1 适得其反。修法候选：绑定模式不触发 reload + 目标点导航手段（点击章内条目属页面内导航，不越播放红线，待定）。见 §4.9/§4.10 |
| 2026-09-21 | 定性更新 | L2/L3 | ↑ 该现象的根因：**已完成态 artifact，不是引擎缺陷** | ✅ 定性 + reload 已修 | 只读持续性观测（4738 真实未完成点：绑定帧 50s 稳定 25/25；708 消失只发生在服务端判完成的点 —— 完成点不再保留播放器）。修复：`video_reload_warranted` 纯函数，`target_frame_not_found` 不再触发 reload，Step F 预算耗尽诚实 FAIL。绑定链路端到端（真实未完成 `:videoN` 播进）仍待一次授权 run。 |
| 2026-09-21 | 缺陷 | L2 | **D13：账本 `:videoN` 与绑定枚举源错位（幻影点级记录）** | 📁 已立案，待修 | 4730/4734/4737 账本有 `:video2`（`read_chapter_job_points` 按 icon/文本启发式数"视频点"），页面 `.ans-insertvideo-online[objectid]` 却只有 1 个（4734/4737 那 1 个还是 finished）。绑定侧现状**诚实 FAIL**（`target video point N not on page`，不误播），但幻影记录永远学不完、反复占投递。修复方向：用 D12 起 live 行携带的 objectid 把"非 attach-video 的视频点"从可 dispatch 集合区分出来。 |

> 记录规则：追加不覆盖；结果不可复现时降级为「待验」而非删除。

### 4.1 已结案的判定争议（isPassed 假阴性）

run 35265696173（09-17）报 `failure_stage=ISPASSED_FALSE`、`isPassed_body=null`。
本地同引擎复现后定论：**服务端当时已判通过，是测量手段失效**。

- 真实响应体序列：6× `isPassed:false` → 2× `isPassed:true`（`ml_probes` 首次落盘）
- 独立佐证：`1217304745` 在本次 run 的实时 reconcile 中被服务器报为已完成，
  registry 现记 `COMPLETED / verified=UI`（09-19 12:57）
- 根因：判定用「对 multimedia/log 的 URL 二次 GET」取 body，而非读首次真实响应；
  该上报端点重复 GET 不返回 isPassed（且属红线禁止的重放）
- 修复：`read_event_body` 读首次真实响应并缓存，二次 GET 降级为 `XUE_DIAG_REFETCH=1` 显式诊断

### 4.2 已结案的判定争议（第二例：把"没测到"当成"测到 0"）

**症状**（日志直读，非推测）：`[scheduler] DIAG E6.2 head=TaskRecord(task_id='1217304754', chapter_id='1217304754', …`
—— 目标章位置打出的不是章号，是整条 dataclass repr。

**机制链**：`head_cid = str(existing.get(task_id) or next(...))`，而 `existing` 是 `{task_id: TaskRecord}`，
`.get()` 命中的是**对象**，`str()` 即 repr → `live_verify_chapter()` 拿 repr 当 `knowledge_id` 去查一个不存在
的章 → `video_total=0` → 命中"该章实际没有视频点"分支 → 把真实视频章写成 `task_type=other / status=PENDING`
并**从 video 队列剔除**。每次 scheduler run 掉一章，本地与 GHA 走同一条路径。

**存续时间**：`56c6e115`（2026-09-06）起，且已在 origin/main —— 即两周的 nightly 每次都在削账。

**真源核对**（`scripts/diag_video_points_ledger.py`，一次登录逐章读 job 点，只读不播放）：
22 章**全部**确有视频点（1~3 个），`keep_other=0`、`unknown=0` —— 无一例外，降级 100% 是缺陷所致。
分类可信度另经抽样验证：marker 含 `ans-job-video` 类名，是类名驱动而非 `播放` 文本启发式。

| 账本项 | 修复前 | 修复后（合并 nightly） |
|---|---|---|
| `task_type=video` 记录 | 33 | **55** |
| 其中 COMPLETED | 21 | 31 |
| **未完成视频记录** | 12 | **24** |
| 残留误降的纯章号记录 | 22（+nightly 1） | 0 |

> 结论：**"done=21/27" 一直是真的，被低估的一直是剩余量。** 此前所有"快学完了"的判断都建立在一份
> 每 run 静默缩一章的账上。`status` 不手工挑：取"该记录最后一次仍是 video"的历史提交值
> （`UNKNOWN` 是 `task_registry.py:34` 的合法状态，照实回填 3 章，不臆造）。

**下游一并发现**：`chapter_points.json` 的 8 个键全是 TaskRecord repr —— 即代码注释里的"洞2 点级快照校准"
自写下以来**从未被查到过**（`merge_done_with_points` 按章号取，永远 miss），已清空待重填。

**判据加固**：`points_prove_no_video()` 把"没读到点"（探测失败/章号错）与"读到点且点里无视频"分开，
前者只打日志、**不动账**。修 `head_cid` 只止住继续损坏；这条判据保证下一次测量失误不再直接改写账本。

### 4.3 M0/L2 三次稳定性验证为何仍无结论

两轮都已作废，且都是**实验缺陷**而非结论：

| 轮次 | 证据 | 作废原因 |
|---|---|---|
| 第 1 轮 `l2_stability_20260919.log` | 21:04–21:52 五章日志 | 与两个并发全量 pytest 同时跑；同一套测试 387s→139s（2.8×）证明机器不干净 |
| 第 2 轮 `l2_stability_clean2_20260919.log` | 三次 `SCHEDULER_CRASH` | 父进程 stdout 按 gbk 建流，看门狗降级日志里的 `⚠️` 抛 `UnicodeEncodeError` —— **崩溃点正是上一轮为"降级不再静默"新加的那行**；`app/run.py` 早有 UTF-8 加固，父进程没有 |

重跑前置现已具备：`head_cid` 修复（不再一边跑一边掉章）、看门狗降级带原因、R-04 带 `ct>0` 约束、
stdout 编码加固。**尚未验证的部分**：R-04 在真站是否触发、以及"只前进不重复"是否成立。

> 2026-09-20 追记：第 3 轮已跑（机器干净），结果与根因见 §4.5 —— 上述前置全部生效，
> 但"不重复"因真实缺陷而**未达标**。R-04 在现存两份章日志（708 的 run3、714）里零触发；
> run1 的 708 日志已被重投覆盖，无法核，故 R-04 真站验证仍记为未验。

### 4.4 本轮记过的三次测量误判（都差点变成结论）

1. **`curl` 返 000 / schannel `CRYPT_E_REVOCATION_OFFLINE` ≠ 站点不可达。** 分层只读探测：DNS→`45.113.20.48`、
   TCP 443→0.09s、TLS→TLSv1.3、HTTP→404（站点在应答）、目标 URL→200 后跳 `passport2`。Windows 控制台
   工具链的证书校验路径与 Chromium 不同，不能拿前者读数断后者。（同时撤回一条我未经证据提出的"风控"猜测。）
2. **`max_ct=0` ≠ "整章没播就判完成"。** 完成日志前明明有 `ct=473/474 (100%) isPassed=True`；0 是章内多视频
   切换时把 `max_ct` 清零的**显示**缺陷。当时我把它说成"危险的假完成判据"，属夸大，已更正。
3. **L1 全绿 ≠ 测试干净。** 走 `run_scheduler` 的用例会真起 headed 浏览器；本机 shell 有 `CX_USER` 而无
   `CX_PASS`，于是桌面反复弹出停在 passport2 的半填登录窗（用户名已填、密码框空）。新增判据：
   **跑测试期间 chrome 进程数应为 0**；同一套用例耗时从 239s 掉到 68s 即其副作用被消除的证据。

> 三条的共同点：用一次性的、来自错误工具链的读数，替代了分层只读探测。以后凡"某某不通/某某假完成"，
> 先给分层探测表，再给结论。

### 4.5 第 3 轮 M0 稳定性验证：判定、根因与三处对本文档的更正

**判据逐项**（`evidence/l2_stability_clean3_20260920.log`，机器干净、无并发重型任务）

| 子判据 | 结果 | 依据 |
|---|---|---|
| 只前进 | 部分 | 714 真新增（`COMPLETED/SERVER_VERIFIED`）；run2/run3 零前进 |
| 不重复 | **❌** | `1217304708` 在三次里**各被选中一次** |
| 不卡死 | ✅ | 每轮都在预算内终止，无挂起 |

**根因：多视频章投错了点。** 708 有 2 个视频点（`E6.2 … video_total=2 finished_video=1`）。第 1 点已过，
系统也**确实**建出了 `1217304708:video2`（DISCOVERED），但队列反复投的是 `1217304708`
（`FAILED → RETRY`，优先级更高）。run2/3 再进该章时播放器停在 `ct=655 / readyState=0 / duration=None`，
引擎立刻看到 `nextUnit: 708 -> 712` 便退出 → 24/28s FAIL。第 2 点因此**永远学不到**。
熔断仍在：`attempts=4 cf=2 max_attempts=3`，再失败一次即 BLOCKED —— 循环有界，代价是白耗 3 次投递。

**账本震荡（本轮最重要的发现）**：这三次 run 把 **9 章从 `COMPLETED` 打回 `UNKNOWN`**
（722/730/732/734/737/738/741/750/751，全部 `SERVER_VERIFIED + CONFLICT`），而这 9 章正是 §4.2 里
我按历史恢复成 COMPLETED 的那批。机制：校准发现"章内还有未完成点"就把 COMPLETED 打回 UNKNOWN，
UNKNOWN 又可排队 → 再投 → 再打回。**"恢复 status"正在与校准路径互相抵消**，所以 §4.2 的回填
只修了 `task_type` 这一半，`status` 那一半会被自动改回去。

**三处更正（对我自己先前的说法）**

1. 我说过"run 1 白播了一个已完成的点"。该结论**证据已不可复现**：per-chapter 日志与 evidence JSON
   按 task_id 命名，run3 重投 708 时**覆盖**了 run1 的产物（现存文件只有 23:20:04–23:20:31、5 个 ct 采样，
   不含 run1 的 465s 中段）。**"同一章被重投即销毁上一轮证据"本身是第 4 个缺陷**，与"证据可复现"直接冲突。
2. 我说过"看门狗降级这次带上了原因、探测生效"。**前半对**（4 条降级日志确实打出来且不再崩），
   后半错：**4/4 次时长探测全部失败**，st 都是 `{'currentTime': 0, 'duration': None, 'paused': True,
   'readyState': 0}` —— 探测没等 `loadedmetadata` 就取 `duration`。即 **P0-01 的自适应看门狗至今从未生效**，
   一直在用静态 900s；714 是 838s 视频，本轮 588s 播完属侥幸，按 0.33× 速率就会被砍成 TIMEOUT。
   这条正是"降级不再静默"改完后才第一次看得见的。
3. **我归因错了：不存在"父进程按 exit_code 重映射子进程 verdict"。** `HEAD` 里早就有
   「`result.verdict` 非空则覆盖父进程结论」的代码，重映射从未发生。三个症状
   （`FAIL` 而非 `DEGRADED`、`failure_stage: null`、`passed_count: null`）的**唯一根因是编码**：
   `_run_one_chapter` 用 `open(evidence_path)`（无 `encoding`）读子进程写的 UTF-8 产物，
   Windows 下按 cp936 解码抛 `UnicodeDecodeError`，又被 `except Exception: pass` 整段吞掉 ——
   于是 `result`/`evidence` 两个分支一个都没执行，verdict 停在退出码推出的 `FAIL`。
   这是**又一处本地/云不对称**：同一份代码在 Linux runner 上永远不报错，所以云端一直是对的。
   证据（现存 4 份真实产物，按生产方式读）：
   ```
   无 encoding 读取 → UnicodeDecodeError: 'gbk' codec can't decode byte 0x82 in position 2432
   带 utf-8 读取    → chapter_1217304708.json: DEGRADED | failure_stage=UNKNOWN
   ```
   即子进程**一直如实上报了** `DEGRADED + VIDEO_NOT_COMPLETED/UNKNOWN`，是父进程没读进去。
   最小复现见 `test_non_ascii_evidence_reaches_the_parent`：只往产物里加一个中文字段，
   `DEGRADED` 立刻变 `FAIL`（真站产物里的章标题、页面文案、console 行全是非 ASCII）。

**已落地的修法**（本节三个缺陷）

- `open(evidence_path, encoding="utf-8")` —— 一处改动同时消掉三个症状。
- 读取失败不再静默：`except Exception as e:` 打 `[scheduler] chapter_x.json 产物读取失败 …`
  并保留退出码结论。静默是它能活过三轮验证的原因。
- 非 PASS 且无失败段 → 显式记 `failure_stage=UNREPORTED_BY_RUNTIME`，不再留 null 伪装成已归因。
- 重投前 `_archive_existing()` 归档上一轮 `.json` 与 `.log`。
- `ensure_utf8_stdio()` 从 `scheduler.scheduler` 的 import 移到**进程入口**（`app/run.py`、
  `scripts/ci_local_run.py`），父日志混合编码（`utf8=2 / gbk=1`）随之消失。

### 4.6 第 4 轮 M0 稳定性验证：**首次达标**，并揪出吞掉点级账目的 D7

`evidence/l2_stability_clean4_20260920.log`（机器干净、无并发重型任务；run_id
`local-1789883740 / -1789884345 / -1789885486`）

**判据逐项**

| 子判据 | 结果 | 依据 |
|---|---|---|
| 只前进 | ✅ | 6 次投递全 PASS；`COMPLETED 23 → 29`、`UNKNOWN 10 → 9`、`FAILED 3 → 2`，无章净回退 |
| 不重复 | ✅ **首次** | 三次里选的章两两互异：r1 `708 / 722` → r2 `730 / 732` → r3 `733 / 734` |
| 不卡死 | ✅ | 最长 549.9s，全部在预算内自然终止，无挂起、无 TIMEOUT |

**6 次投递的耗时**：`708 23.1s`（空投，见 D7）· `722 486.7s` · `730 549.9s` · `732 470.0s` · `733 351.0s` · `734 316.6s`

**D5/D3 的端到端证据（真站，不是单测）**：汇总从第 3 轮的
`{"verdict":"PASS","passed_count":null,"failure_stage":null}` 变成
`{"verdict":"PASS","passed_count":10,"failure_stage":null}` —— 子进程自述的计数第一次穿到 ci_local 汇总；
父日志里 `上一轮产物归档` 真实触发 2 次；整轮无 `产物读取失败`。

**D7（新发现，已按 TDD 修）**：E6.2 live refine 只带**当前一章**的 `video_counts` 重建 discovery
（`scheduler.py:1320`），其它多视频章退化成单条 `<cid>`；而 `<cid>:videoN` / `<cid>:other` 与 `<cid>`
**同 title**，于是被 `reconcile_registry` 的 by_title「task_id 格式迁移」分支命中并 `result.pop()`。
真站读数：`reconcile → 82 tasks` 之后 `E6.2 after live refine … tasks=74` —— 少的正是 `2182a11`
刚建出来的 8 条点级记录。承载者一消失，`701a6ba` 的"已确认的点不再回队"护栏当场失效，
这就是 run1a 用 23s 空投了 `1217304708` 已完成点 1 的原因（第 3 轮的 74 也是同一个来路）。
修法：点级记录（task_id 含 `:`）**不参与**格式迁移。回归
`test_refine_rebuild_does_not_swallow_sibling_points`（RED 时 `:video2` 确实从结果里消失）。

**看门狗仍未生效（P0-01 保持开放）**：6/6 次都打了 `⚠️ 看门狗降级 … 25s 内未读到 video.duration
（st 里 `readyState=0, currentTime=0|655, duration=None`）→ 静态 900s`。
探测这次修好的是**可见性**，不是生效：播放器在探测窗口内根本没挂上 metadata，之后却正常播完了
486–550s。本轮没被误杀只是因为最长 549.9s < 900s，**属侥幸**。

**两处待查（只记观测，不提前定机制）**

1. `1217304722` 的证据等级从 run 前的 `UNKNOWN + SERVER_VERIFIED` 变成
   `COMPLETED + UI` —— 该点服务端确认在先、本轮真播成功也在后，等级反而变弱，疑似被
   `_make_ui_completed` 覆盖（第 3 轮 reconcile 行有 `upgraded_ui=1`）。
2. `1217304705` 本轮 live refine 读到 `video_total=2 finished_video=2`，而 §4.2 与
   `test_no_video_chapter_never_emits_video_task` 都按"705 是无视频章"记账 —— 两份真源口径冲突。
3. D2 残留：第 2 次 run 起手 `stale=1 chapters re-queued: ['1217304722']`，刚完成的章仍会被打回一次；
   本轮因 refine 读到 `finished_video=1/1` 才没形成循环。

### 4.7 D7 真站复验：成立；顺带炸出 D10（点级产物落进 NTFS 数据流）

`evidence/d7_verify_20260920.log`（1 轮 × 2 章上限，`local-1789895257`）

**D7 修好在真站成立**（判据在跑之前写死，未事后挑数据）：

| 判据 | 第 3/4 轮 | 本轮 |
|---|---|---|
| refine 后任务数不缩水 | `reconcile → 82` → `tasks=74`（丢 8 条点级） | `reconcile → 87` → `tasks=87` ✅ |
| 投的是未完成的点 | `next_task=1217304708`（已完成点 1） | `next_task=1217304708:video2` ✅ |
| 不出现秒级空投 | run1a 23.1s 空投 | 无空投（26.1s 是真失败，见下） ✅ |

而且 `9_isPassed_true: True` —— **`1217304708` 的第 2 个视频点第一次被服务端判通过**。

**但整章 verdict 是 `DEGRADED`（9/10）**，唯一失败项 `7_currentTime_growing`，st 是
`currentTime=655 / duration=None / readyState=0`，即 `ct=655`（=点 1 的片尾位置）。
这与第 3 轮 708 的 24/28s 失败是**同一现象**：进入章内第 2 点时页面仍复用点 1 那个已播完的
`<video>` 元素，没换源起播。定性为**真实播放层缺陷**（不是测量假阴性：服务端确实回过 isPassed）。
→ 新立 **P1：章内切点未重新起播**。

**D5 至此在失败路径上也验实**：汇总 `{"verdict":"DEGRADED","passed_count":9,"failure_stage":"UNKNOWN"}`
完整穿到 `ci_local` 汇总；第 3 轮同一条路是 `FAIL`/`null`/`null`。`failure_stage=UNKNOWN`
是**子进程自己没归类**（10 项里只差 `7_currentTime_growing` 却说不出段），下一步按检查项映射。

**D10（Windows 独有第 4 例）**：产物路径直接拼 task_id，而点级 id 含 `:` →
Windows 把 `chapter_1217304708:video2.json` 解释成 `chapter_1217304708` 的
**NTFS 备用数据流**。`dir /r` 实测：

```
0        chapter_1217304708                 ← 0 字节空壳
28,529   chapter_1217304708:video2.json:$DATA
 3,691   chapter_1217304708:video2.scheduler.stdout.log:$DATA
```

即**刚修好的点级记账引爆了它**：`_archive_existing()` 归档的是那个空壳，D3 对点级任务整体失效；
Linux 上 `:` 合法，所以云端不会有这个形状。修法：`artifact_slug()` 把 `:` → `_`（单一路径入口，
`--output` 是唯一出口，无别处按老名重建）。RED 真实复现：修前 `glob('chapter_*')` 只看得见
`chapter_1217304708` 一个空壳。本轮 ADS 里的两份产物已**逐字节校验后**迁回
`chapter_1217304708_video2.{json,scheduler.stdout.log}`，空壳删除。
L1：`356 passed, 1 skipped`（357 collected，69.7s）。**D10 目前只有 L1 证据** —— 点级产物的归档链路
要等下一次真站 run 才算验过。

**D11（同一次 run 顺带暴露，影响明晚 nightly）**：`course_state.py` 的熔断判据写的是
"连续失败多次"，读的却是**只增不减**的累计计数 `failure_count`（成功分支从不清零）。
真站读数：`run_count: 104`、`failure_count: 31` —— 第 4 轮 6 次连续 PASS 也没把它拉回 0，
于是这次 `:video2` 的**单次** DEGRADED 立刻把**整门课**打成 `BLOCKED`
（`scheduler.py:214` 见课程 BLOCKED 即拒调度）。
**与 `scheduler.py:219` 的 `ss.consecutive_failures >= 3` 不是简单重复**：后者是**调度层**的
连续失败计数（`app/run.py:331 → run_course` 才是**课程状态层**，按章记账），两者层次不同；
真正的问题是这条课程层的锁**语义名不副实** —— 它记的是累计值，却行使"连续失败才锁"的职责，
结果任何一次失败都会永久锁死整门课。修法：成功即 `failure_count = 0`
（回归三条：清零 / 单次失败不 BLOCKED / 真连续 3 次仍 BLOCKED —— 护栏不删保护）。

**D10 / D11 的真站复验**（`evidence/d10_d11_verify2_20260920.log`，1 轮 × 2 章，
`XUE_SCHEDULER_FAILURE_BUDGET=2`：默认 1 会让 `:video2` 的必然失败直接 break 掉本轮，
拿不到自愈所需的 PASS；该 env 只影响这一次本地 run，不动 nightly 行为）

- 第一次尝试被**瞬时网络**打断：每次 `Page.goto` 都 `net::ERR_CONNECTION_CLOSED` → TDVP 走
  `PROBE_EMPTY`，不臆测选章、**账本零污染**（`run_count` 仍 104、工作树干净）。事后 curl 与
  Chromium 各 3/3 正常 ⇒ 归因环境而非代码；同日 `git push` 也撞上一次同类瞬时失败。
- 重试：`next_task=1217304708:video2` → `DEGRADED 26.0s`（P1 未修，符合预期）→ 越过它 →
  `1217304721 PASS 548.5s`（又一章真学完）。
- **D10 + D3 同链成立**：点级产物以**常规文件**落盘并按时间戳归档 ——
  `chapter_1217304708_video2.20260920T100338Z.json` 28,529 B（即从数据流里迁回的那份）与
  当前 `chapter_1217304708_video2.json` 28,598 B 并存，不再互相抹掉。
- **D7 第三次复现**：`reconcile → 87` → `E6.2 after live refine … tasks=87`。
- **D11 由真实事件解除**：课程 `BLOCKED → ACTIVE`、`failure_count 31 → 0`、`success_count 74`
  —— 不是手改账本，是一次真 PASS 的结果。
- 新观测（记待办，未修）：ci_local 汇总 `{"verdict":"PASS","passed_count":9,"failure_stage":"UNKNOWN"}`
  与 `exit=1` 口径不一致 —— `verdict` 取末章，退出码取聚合 `FAILED`。
  "看起来 PASS 的汇总 + 失败退出码"是下一类归因歧义的种子。

### 4.8 P1 根因（终版）：目标点从未被绑定 —— 帧遍历顺序决定观测对象

**两次更正记录。** 第一版我写"页面复用点 1 已播完的 `<video>` 没换源"——错，快照来自
调度侧探测，子进程日志证明页面**确实换到了点 2**（§4.8 初版已更正）。第二版把根因定为
"`target_segment_done` 把到达当播完"并落地修法——**真站复验证伪**：run 108（2026-09-21）
投 `1217304708:video2`，引擎观测到的视频 4s 内 max_ct=0，`7_currentTime_growing` 失败，
整章 DEGRADED，`:video2` cf 3→4。子进程日志显示 `in_next_video_grace` **从未进入**——
因为观测到的是服务端恢复到 100% 的点 1（656s 处 ended=true），宽限窗口一开就满足旧条件，
而这"ended"属于点 1 不属于目标点 2。

**根因（终版，只读探针取证）**：章 1217304708 的页面**同时**挂两个
`ananas/modules/video/index.html` 帧，各有 `<video id="video_html5_api">`，可凭 src 里的
32 位 hex objectid 区分（`19da22cc…`=点 1 ct=655，`53d6b112…`=点 2 ct=0）；cards 帧里每个
点是一个 `.ans-insertvideo-online[objectid]`。而 `get_video_state(page)` **无绑定概念**——
帧遍历顺序决定观测对象（cards 帧优先 → 第一个含 video 的帧）。于是：

- 观测读到点 1（ct=655、时长 656s）→ `max_ct`/`initial_duration`/时长探测全是点 1 的；
- isPassed 也是点 1 的 objectId；
- 方案A 护栏 `max_ct>0 or initial_duration>0` 被点 1 的数据满足 → 目标点 2 一秒没播
  就被判 `exit_complete`。P0-01（时长探测拿到错的时长）与 P1 **同一根因**。

**修法（终版，帧绑定）**：`enumerate_video_objectids` 从 cards 帧按 DOM 序读出全部
`.ans-insertvideo-online[objectid]`，`pick_target_objectid` 把 `:videoN` 解析成第 N 个
objectid；`bind_video_state` 只认「`<video>.src` 含该 objectid」的帧，找不到显式报
`target_frame_not_found`。观测（get_video_state）、续播（resume_paused_video 只 play
绑定帧）、时长（Step F 与 scheduler 看门狗探测均绑定）、完成判定全部只认这一帧：

- 方案A 加护栏 `bound_max_ct`：绑定帧自身 max_ct==0 时只判 `exit_switch` 不判完成，
  且 `has_passed` 收窄为"目标点自己的 objectId 收到 isPassed"；
- 绑定帧的 `ended` 即"该段真的播完"→ 直接完成退出，不走宽限期；
- Step F 枚举不到目标点 → `FAIL(target video point N not on page)`，诚实失败。
- 上一版 `target_segment_done` 保留（`<cid>` 自然连播路径仍用它防"到达即退出"）。

TDD：`tests/unit/test_video_frame_binding.py` 10 条（点↔objectid 解析、绑定选择、
未命中显式报错、无绑定回退、方案A 护栏开/关）。L1 `374 passed, 1 skipped`
（375 collected，69.6s）。**真站复验待授权**：`1217304708:video2`（cf=4），判据改为
"绑定帧被选中（evidence `target_objectid` 命中）且该帧 currentTime 真实增长 / 或按
新语义诚实失败"。

**遗留（本轮不夹带，只记）**：切换日志打的是 `switch -> #{video_count + 1}`，
即第 2 段被标成 `#3` —— 这正是把我（以及任何读日志的人）带偏的第一现场。
`1217304708:video2` 熔断解冻仍需一次真实成功 run，不手改账本。


### 4.9 P1 真站复验 + 「手动已观看」恢复路径（方案1）

**帧绑定复验（2026-09-21，章 1217304708:video2）**：绑定机制按设计工作 ——
`[bind] target video #2 -> objectid=53d6b112…` 正确解析目标点；目标帧不在时诚实报
`target_frame_not_found`，没有拿点 1 冒充（旧病灶消除）。但复验**没有播起来**，
两个原因：

1. **用户手动看完了点 2**：只读探测（headless，只读不上报）确认服务端已把两个点
   都判 `finished: true` —— replay 的前提消失了：点已完成，页面不会播它，
   replay 根本产生不了「真实成功事件」。
2. **新缺陷立案（P1 残留）**：headed 会话中点 2 的 video 帧短暂出现（1s 时
   `found=True dur=None`）后消失（11s 起 not_found），而 Step F 的 reload 恢复把
   页面打回点 1、适得其反；同一页面 headless 探测下两帧都在。待修两点：
   绑定模式下 `target_frame_not_found` 不应触发 reload；目标点不在当前播放
   位置时需要导航手段（点击点内条目属页面内导航，不越播放红线，待定）。

**「手动已观看」恢复路径（用户选定方案1）**：`reconcile` 的 BLOCKED 冻结护栏
（§4.8 的 1217304719 教训）对「服务端已完成」场景过严。新路径：

- `tvdp.build_live_finished(job_points)`：从实时 job 点读「服务端已判 finished」
  的 task_id 集合（与 `build_live_pending` 互补；非 video 行无 task_id，安全跳过）。
- `reconcile_registry(..., live_finished=…)`：BLOCKED 护栏的例外 —— **本点**
  的 task_id 出现在服务端 finished 集合 → 以 SERVER_VERIFIED 证据置 COMPLETED，
  失败计数保留不清（留痕曾熔断）。兄弟点 finished 不解冻（点身份精确匹配）。
- `heal_blocked_by_live(course_key, existing, discovery, dom_status, verify_points)`：
  生产封装 —— 只对冻结章做 live 读数（健康章不烧 L2 成本），读数失败保持冻结。
- `scheduler` 步骤 4.2：冻结章不进队列 → 4.5 的 live 复核永远轮不到它们，故在
  建队后显式对冻结章做恢复，命中则 `save_registry` + 重建队列。

护栏回归：`test_reconcile_blocked_preserved`（无真源时 BLOCKED 原样冻结）全数保留。
TDD：`tests/unit/test_reconcile_blocked_server_heal.py` 10 条。

**首跑落空 → 炸出 D12**：第一次执行恢复动作时 live 读数报 `708: total=1
finished=1 points=[('1217304708', True)]` —— `:video2` 根本不在读数里，护栏正确地
不动账本。只读诊断（同页两行 marker 全同、innerText 同为空、仅 objectid 不同）
定位为 `read_chapter_job_points` 的 JS 去重键碰撞（新立 D12）。修法：行携带
`.ans-insertvideo-online[objectid]`，去重下沉为纯函数 `job_rows_to_points`
（oid 身份去重；无 oid 退回旧文本键，双访问折叠能力不变）。TDD：
`tests/unit/test_job_rows.py` 5 条。

**恢复结果（2026-09-21，用户授权的只读动作）**：修后 live 读数
`total=2 finished=2` → `healed_by_server=1`，`:video2` 以
`SERVER_VERIFIED("server finished marker on the exact point")` 置 COMPLETED，
`cf=4` 保留不清（留痕）；同轮冻结章 `1217304719`（live finished=false）**不动**，
护栏如设计。L1 `389 passed, 1 skipped`（390 collected，69.9s）。

**P1 残留的定性收尾（同日，只读持续性观测）**：在真实**未完成**的 attach-video 点上
（4738 的 `:video2`，目标 oid `e79a9a86…`），绑定帧在 headed 会话里 **50s 持续存在**
（25/25 采样 found=True）—— 708 的"出现后消失"只发生在服务端已判完成的点：完成点
不再保留播放器，是**已完成态 artifact，不是引擎缺陷**。已修的部分：
`video_reload_warranted` 纯函数 —— `target_frame_not_found` 不再触发 reload
（reload 实证把页面打回点 1、阻碍推进），Step F 预算耗尽即诚实 FAIL。
未修并立案 **D13**：账本 `:videoN` 按 job-icon 启发数点、绑定按
`.ans-insertvideo-online[objectid]` 枚举 —— 4730/4734/4737 出现"账本有 `:video2`、
页面只有 1 个 attach-video"的幻影记录（现状只会诚实 FAIL，不再误播，但永远学不完）。
绑定链路**端到端**（真实未完成 `:videoN` 从绑定到播进到完成）仍缺一次授权 run，
候选目标 `1217304738:video2`。L1 `394 passed, 1 skipped`。

**端到端的三次尝试与"C 探测"结论（同日）**：4738:video2 连跑两次 ——
① Step F 90s 内绑定帧始终 `dur=None` → 诚实 FAIL；② 加"滚动进视口"式导航
（`scrollIntoView`，3 次 ok=True）仍 FAIL —— **滚动不能激活播放器**。第三次
在点击 `<video>` 上加码 → `ok=False`：点击从未落地（video 元素被 poster/
播放按钮层挡住，Playwright actionability 不通过）。用户目视确认"页面跳到了
第二视频的位置，但播的还是第一个" —— 据此复盘：超星 cards 页**串行化**任务点，
只有当前播放器由页面驱动，服务端 finished 不会让页面跳过重播；观测绑定解决
"看哪一帧"，不解决"哪一帧在播"。headless 静态探测（`evidence/nav_probe.json`）：
cards 无"定位任务点"入口（无 onclick/按钮/cursor:pointer）；播放器是 **video.js**，
**每个点（含未激活的）都有可见且 hit-test 可命中的 `.vjs-big-play-button`** ——
最用户级的激活动作=滚到目标节 + 点它自己的播放按钮（点 `<video>` 是无效目标，
这是第二次点击 ok=False 的根因）。已落地 `player_activation_selectors()`
（大按钮优先、video 兜底）；端到端待再跑一次授权 run（判据不变：绑定帧
metadata→currentTime 增长→本轮 isPassed 含目标 oid）。
**端到端的三次尝试与"C 探测"结论（同日，run 4 前）**：4738:video2 连跑两次 ——
① Step F 90s 内绑定帧始终 `dur=None` → 诚实 FAIL；② 加"滚动进视口"式导航
（`scrollIntoView`，3 次 ok=True）仍 FAIL —— **滚动不能激活播放器**。第三次
在点击 `<video>` 上加码 → `ok=False`：点击从未落地（video 元素被 poster/
播放按钮层挡住，Playwright actionability 不通过）。用户目视确认"页面跳到了
第二视频的位置，但播的还是第一个" —— 据此复盘：超星 cards 页**串行化**任务点，
只有当前播放器由页面驱动，服务端 finished 不会让页面跳过重播；观测绑定解决
"看哪一帧"，不解决"哪一帧在播"。headless 静态探测（`evidence/nav_probe.json`）：
cards 无"定位任务点"入口（无 onclick/按钮/cursor:pointer）；播放器是 **video.js**。
此前把"点目标播放器大按钮"立为主解，run 4 实测后**证伪**（见 §4.10）：轮外播放器
起播会被页面每 ~2s 暂停并整章切走 —— 已回退删除 `player_activation_selectors()`。
本轮副作用账：`4738:video2` FAILED cf=1、课程 failure_count 3 → **BLOCKED**
（解除照旧走真实成功事件）。L1 `402+1 passed`（403 collected）。

**已有证据补充误判的根因修正（probe v4，只读）**：第 9 轮前两次 seek 探测
播放器 90s 不激活（dur=None/rs=0，v3 静默）曾被归因于"滚动不激活/时序"；
后与引擎 Step B 逐字对齐启动参数（`--disable-web-security` +
`--disable-site-isolation-trials` + 1440x900 视口 + 真实 Chrome UA）后，
**v3 立即启动并播放** —— 探测环境少传这两个跨域 flag 才导致 dur 永不出现。
即：**引擎的启动参数就是 v3 能接管播放的前提**，探测自身条件缺口，不是站点行为。


---

### 4.10 方案 A 前提验证（probe v4）+ 快进链路落地（同日，只读探测 + L1 纯函数）

**前提验证（用户定的先后：先证"已完成点能自由拖动"，再实现）**：4738 当前点
（第 1 点，`ans-job-finished` 真值=True），引擎同构条件下 ——
`evidence/seek_probe_v4.log`：
- **激活**：注入 v3 后 2s 即 `dur=1062 ct=0.2 paused=False`，v3 控制台
  "IPV6 开始播放→视频加载完成→1.5x"。**修正前两次探测的条件缺口**：probe v2/v3
  漏传 `--disable-web-security`/`--disable-site-isolation-trials`（跨域 iframe
  contentDocument 不可达）且默认 900x600 视口 —— 与引擎 Step B 对齐后 v3 立刻接管。
- **seek-a（脚本置 currentTime=0.9×dur=955.8）**：+3s `ct=958.3 rs=4`、+8s
  `ct=966.0` —— **无回钳、连续播**：已 finished 的点站点允许自由拖动（等效拖条）。
- **seek-b（真实鼠标点进度条 90%）**：`hover` 3s 超时 —— 视频层 hover 不上
  （视频播放中被覆盖/不在顶层），**真实输入路径不可用**；引擎实现只走 JS seek。
- 快进后自然播放（未拉 100%），页面自己的 ended→推进 状态机负责把目标点变成
  当前点 —— 不做 100% 是为了不越过"页面自己判完成"这条线。

**落地（L1，TDD RED→GREEN）**：`tests/unit/test_bound_reload_policy.py` 新增
4 组 RED 测试（`nav_action_for_attempt(1)=='fastforward_finished_current'`、
闸门纯函数、seek 位置纯函数）→ `app/e2_headed_gha.py` 实现：
- `nav_action_for_attempt`：第 1 次仍只滚动（最保守），第 2 次起改为
  **快进当前点**（run 4 已证伪"点目标播放器"：轮外播放器被页面每 ~2s 暂停并切章）。
- `should_fastforward_current`（红线闸门，纯函数）：**只快进服务端 finished 真点**；
  未完成点绝不 seek（拖未完成的进度=跳课）。真值读 cards `ans-job-finished` 类。
- `fastforward_seek_position`：90% 位置（纯函数，无效 dur 返回 None）。
- `fastforward_current_player`：选"有 metadata 且 src 不含目标 oid"的**轮内当前**帧
  → 读 finished 真值 → 闸门放行 → 按 src 匹配置 `currentTime=0.9×dur`。
- `navigate_to_video_point` 撤掉 click 分支（只保留滚动）；删除失效的
  `player_activation_selectors()`；修复 `should_navigate_to_target` 重复 return。
L1 全套 `306 passed, 1 skipped`（RED=18 项含新 4 组先红后绿）。

**未竟**：`4738:video2` 端到端 run 尚缺末次授权（§7）：验证快进 → ended → 页面自推进
→ 目标点成当前 → 绑定帧 metadata→currentTime 增长 → 服务端判完成 + 课程解冻（BLOCKED fail=4）。

**→ 本节的方案 A 前提已于 2026-09-22 被真站否证，快进链路整体退役，见 §4.11。**


### 4.11 章内 `:videoN` 激活通道定论（2026-09-22，对照实测 + 方案 A 退役）

**显式恢复先落地**（`run_scheduler` Step 2.7，仅 manual 腿、仅人点名的章）：任务级 BLOCKED
原先只有"服务端真源治愈"与"手改 JSON"两条出路，真没学成的点永远等不到前者。真站验证
（run 35669129208）：日志 `manual_restore=1 tasks=['1217304738:video2']`，远端账本该点
`BLOCKED(cf=3) → FAILED(cf=1)`（attempt_count=4 留痕），未被点名的 `1217304719` 仍冻结。

**同一次 run 的 verdict 是 FAIL，由此做完三腿对照**（`evidence/target_activation_with_v3.log`、
`evidence/target_activation_no_v3.log`、`evidence/seek_play_probe.log`；同章 4738、同样只点一次
目标卡的 `.vjs-big-play-button`）：

| 腿 | 目标点 | 点 1 | 站点自己的进度上报 |
|---|---|---|---|
| 注入 v3 | `rs=4 dur=1130` 但 ct 恒 19.8、`paused=True`、180s 内 0 次翻转 | 被 v3 从 0 拽着连播到 262 | 只有点 1 |
| 不注入 v3 | ct 20.4→194.1、89/90 采样前进、0 次暂停 | 安静停在存储位 227 | `playingTime=198 objectId=<目标点>` |
| 播完点 1 的尾 | 从未激活 | 真播到 `ended` | — 页面 chapterId 直接跳 1217304740 |

**结论**：① 方案 A 的前提"seek 后当前点不动"是错的（它照播），而"播完让页面自推进"更是
反向 —— 播完即整节跳走；② 抢走当前位的一直是**我们自己的 v3**（它 resume 每个模块帧里
第一个 `<video>` = 点 1，点 1 在播时站点绝不让点 2 播）；③ 章内 `:videoN` 的有效形态 =
**不注入 v3 + 点目标卡自己的播放键 + 交给已绑定目标帧的 R-04 续播**。

**改动（L1，TDD RED→GREEN）**：`should_inject_v3(video_index)`（段号 ≥2 不注入；点 1 与未
指定段号照旧走 v3 这条已验证的稳定链路）、`frame_is_bound_to(src, objectid)`（点击与观测的
身份判定，防"拿点 1 冒认目标点"这一 P1 旧病灶）、`activate_target_point()`（滚动进视口 +
只点目标帧自己的播放键）；退役 `nav_action_for_attempt` / `should_fastforward_current` /
`fastforward_seek_position` / `fastforward_current_player` 及其 6 项测试。Step E 的重载恢复
注入点同样受 `should_inject_v3` 约束；`checks["v3_injected"]` 记"按本次分派设计处理好了"，
具体路由 `evidence["v3_route"]` 说明，保证两条路都仍可达 10/10。
L1 全套 `419 passed, 1 skipped`。

**真站复验（run 35673388111 → 35675040542，2026-09-22）**：新链路第一次让 `4738:video2`
在 GHA 上拿到 metadata —— `[v3] skipped by design (video_index=2)` → `clicked target
e79a9a86's own play button` → `Video ready: duration=1130s ct=…` → `★ isPassed=true`。
第一次（35673388111）播到 ct=1036/1130=92%、服务端已判通过，却被父层静态 900s 墙钟在 901s
砍成 TIMEOUT（子进程被杀 ⇒ registry 连这次尝试都没记上）；修掉探测缺口后再跑
（35675040542）**verdict=PASS，10/10 checks，`chapter_completed=True`**，远端账本该点
**COMPLETED / SERVER_VERIFIED / passed_object_ids=1 / cf=0 / attempts=5** —— P1 的实质缺口闭合。

**仍开着的一处**：`_probe_video_duration_s` 现在会先激活目标点、预算 45s（`duration_probe_policy`），
但那次探测仍未读到 duration（作业日志里 `看门狗降级 … 45s 内未读到` 照旧，回退静态 900s）。
本轮无碍（站点已存进度 1006/1130，只差 124s），但**换一个存储进度很低的 `:videoN` 长视频点就会
再次被 900s 砍**。下一步：让探测像 Step F 那样反复重试激活（而不是只点一次），或让子进程把已读到的
`video_duration` 回灌父层预算。

### 4.12 R6：已完成章被 L1 校准降级回炉（2026-09-22，run 35678657158 实证 + L1 归因落地）

**真站事实（读日志与远端账本，非退出码）**：手动 dispatch `--action scheduler --max-chapters 1`，
- `manual_restore=1 tasks=['1217304719']`（Step 2.7 按预期把老的冻结章解冻回 PENDING，cf→0、attempts=3 留痕）；
- `TDVP: stale=1 chapters re-queued: ['1217304738']` —— 而**同一轮**十几秒后的 E6.2 live refine 读到
  `1217304738 video_total=2 finished_video=2`（服务器视角两视频点都已 finished）。降级判据与它自己的
  live 读数互相矛盾，队首仍是该章 ⇒ 引擎重播已 PASS 的点（attempts 2→3、`verdict=PASS timing_s=868.1`），
  **当晚唯一的 1 个点位预算被吃掉**，队列里真正该学的 `1217304730:video2` 没轮到（仍 DISCOVERED）。
- 同一条日志顺手把 R5 从"`:videoN` 专属"扩大成"两点通吃"：点 1 的 25s 探测读到的是尚未激活的帧
  （`st.found=True / currentTime=227 / dur=None`）→ 照样回落静态 900s，本轮距墙钟只剩 **32s**。

**这一步只做归因，不改判据**（判据怎么错还不知道，先让它自己说出是谁干的）：
新增纯函数 `mark_stale_with_source(existing, by_catalog=…, by_points=…)`（scheduler.py:884），
把来源写进账本 `completion_evidence.detail`（`…; stale_by=catalog|points|catalog+points`），
日志同步打 `by_catalog=[…] by_points=[…]`。原 `stale_ids` 合并逻辑与 `save_registry` 时机不变。
L1：`tests/unit/test_stale_source_attribution.py` 5 项 RED→GREEN，全套 **427 passed, 1 skipped**。

**待这一次真站复现给答案**：4738 的 `:other` 仍是 UNKNOWN —— 若 `stale_by=catalog`，就是"为不可播放的
other 点把整章回炉、而引擎只会重播视频"，属系统性浪费（每个含 other 点的章在视频完成后都会被反复回炉）；
若 `stale_by=points`，则是点级快照与 live 读数的口径冲突。两种修法不同，所以先测再改。

**复现结果（run 35681460999，`headSha=81c16b1`，03:00–03:01 UTC）**：

- **R6 没有复发**：整份日志里 `TDVP: stale=` 出现 **0 次**，`1217304738` 没被再次降级 —— 上一轮那次
  PASS 把「点级服务端确认」补上了。⇒ R6 的代价是**每章一次性学费**（已经付掉一晚一个点位），不是每夜
  复发；严重性下调一档。腿名这次没机会打出（判据未触发），归因代码留在链路上等下一次降级。
- **但拿到了更有价值的一条**：`:videoN` 第一次在**自然队列**下被投递（`TDVP: next_task=1217304730:video2`），
  却在 **15.9s** 就 `verdict=FAIL`，理由 `FAIL(target video point 2 not on page; points=1)`。子进程时间线：
  Step C 登录 6.4s → Step D 预态 7.3s → `[v3] skipped by design (video_index=2)`（Step E 正常）→
  Step F 起手 1s 即失败。也就是说**4738 那套"点目标播放键"链路还没轮到出手，页面枚举就已经只有 1 个点**；
  同刻 `sidebar_before={"unfinish":null,"points":null,"row_text":"Previous Next to learn"}` 提示 cards 帧
  当时尚未挂全。代码形态：`e2_headed_gha.py:824-833` 对"点不在页面上"是**一次性判定 + 立刻收尾**，
  没有 reload 后重枚举的恢复路（而 metadata 那条路是有 reload recovery 的）。账本据此记
  `1217304730:video2` FAILED cf=1/3。
- 附带读数：上轮解冻的 `1217304719` 本轮 live refine `video_total=0`（服务端视角该章没有视频点），
  队列在 26/27 READY 之间波动。

**下一步（先便宜后贵）**：本地只读探测 4730 —— 记录 cards 帧内 `[objectid]` 视频模块数量随时间的曲线
（是否 lazy-mount、第 2 个点在什么时刻才出现），确认是"渲染时机"而非"账本幻影"（D13 那一类）之后，
再决定给 Step F 加「重载入 + 重枚举」恢复路。

### 4.13 `1217304730:video2` 是幻影：陈旧点级快照每轮重新 mint 出根本不存在的点（2026-09-22，两次只读探测）

**测量设计**：`_probe_point_mount.py`（只读，不点击/不 seek/不注入 v3/不写 state），同一套采样分别打
三件事 —— 引擎自己的 `enumerate_video_objectids`、cards 帧内 `.ans-insertvideo-online[objectid]` 原始标记、
服务端 `read_chapter_job_points`；时间点 t=0/3/8/15/30/45/60/90s + 滚动到底后再一次。
**对照组 = 已知确有 2 个视频点的 4738**（用来排除"读数在少算"这种测量自身故障）。

| 章 | engine | DOM markers | 服务端 live 点读 | 随时间/滚动 |
|---|---|---|---|---|
| 1217304730 | 1（`2b3ec8e6`） | 1 | 1（`video_points=1`） | 0→3s 起恒为 1，滚动后仍 1 |
| 1217304738 | 2（`94382be4`,`e79a9a86`） | 2 | 2 | 恒为 2 |

⇒ **lazy-mount 被否证**（对照组 apparatus 正常，空标题的多点也没被折叠 —— D12 的修法在这条路上仍成立），
**站点今天对 4730 只暴露 1 个视频点**，而账本里躺着 `1217304730:video2`（DISCOVERED→本轮 FAILED cf=1/3）。

**幻影是怎么被 mint、且为什么每轮都会重 mint**（读代码定位，非猜测）：
`build_tasks_from_discovery` 的拆分数 `n_videos` 来自 `video_counts_from_points(load_chapter_points(...))`
（scheduler.py:1302 + task_registry.py:530）—— 也就是**来自缓存快照**，而 `chapter_points.json` 里
`1217304730 = {video_total: 2, video_finished: 1}`，`updated_at = 2026-09-20T06:15Z`。§7.3#4 已证该快照
**无 TTL、无删除路径**：不是队首候选就永不刷新。于是「一次快照读错（或老师真的删了一个视频）→ 每轮 reconcile
按它 mint 出 `:video2` → 引擎 15.9s 判 `target video point 2 not on page` → 记一次真实 FAILED → 三次后整章冻结」
是一条**自我维持**的链路，4719 当初 BLOCKED 很可能就是同一形态。
**快照自身也不可审计**：它只存聚合数（`video_total/video_finished`），不存点列表与 objectid，所以
"9/20 那天到底读到什么"已无从复核。

**探针自纠**：本次输出里 `finished=None` 是我打印键名取错（真键是 `isFinished`，见 `chapter_video_summary`
tvdp.py:1058），不构成站点读数结论 —— 已在此标注，避免下一轮把它当"服务端没判完成"。

**落地（2026-09-22，方案①）**：引擎在早退出处新增 `failure_stage="TARGET_NOT_ON_PAGE"` +
`target_video_index` + `video_points_observed`（e2_headed_gha.py:825-837）；子进程 postflight 命中该段时
**不走 mark_failed**，改为 `prune_phantom_video_points`（收掉 K>observed 的记录，**去掉 D13 的 cf==0 条件**
—— 幻影不该因为撞过墙就获得豁免权）+ `video_total_from_observation`（把快照的 video_total 纠正到实测值，
`video_finished` 一并夹住）；`phantom_correction_policy` 是唯一判据，父层复用同一个函数。
**一个非显然的坑**：纠正必须让整轮聚合判 SUCCESS —— workflow 的 state 提交步条件是 `success()`，
若判 FAILED 则纠正根本落不回仓库，幻影明晚照旧再来吃一个点位。所以父层像 TIMEOUT 那样把它从
`chapters_failed` 里摘出来，单独记 `chapters_corrected`（进 summary/日志，`PHANTOM-CORRECTED …`）。
测试：`tests/unit/test_phantom_point_correction.py` 11 项（含"纠正之后 `build_tasks_from_discovery`
不再 mint `:video2`"这条端到端保证、以及"observed=0 不许采信"）+ `tests/integration/test_scheduler.py`
1 项（熔断/聚合）。**变异检查已做**：把 `chapters_corrected.append` 改回 `chapters_failed.append`
后集成测试必须变红（第一次尝试因为 heredoc 没执行、误报通过，重做后才拿到红），还原后全量
**439 passed, 1 skipped**。
**尚未做（原方案②）**：把 D13 已有的幻影清理从"冻结章恢复"这一条路扩到正常的 head-candidate
refine，并规定快照**只在 live 复核成功时**更新 —— ①治的是"撞上了怎么记账"，②治的是"别再从陈旧
快照里 mint 出来"。另有一条同源缺口：快照只存聚合数（`video_total/video_finished`），不存点列表与
objectid，所以 9/20 那次为什么读到 2 已经无法复核；要能事后审计就得把点级明细一并存进去。

---

## 5. 失败 / 回退策略

- **L1 失败**：阻断合并，必须修测试。
- **L3/L2 失败**：不进入下一层；反复失败则回退到上一层或上次 PASS 的 commit。
- **L4 云上偏差**：回退自查本地；定位"真站变了"还是"代码回归"，不直接改云。
- **进度不因回退损失**：回退不动 `state/` 与 registry（由 git 跨 Run 保留）。

---

## 6. 与现有工程体系衔接

- 单元/集成/回归用例 → `tests/`（已在 CI 自动运行）。
- 验收项逐步沉淀 → `docs/engineering-review/REGRESSION_MATRIX.md`。
- CI 门禁候选 → `docs/engineering-review/CI_GATES_CANDIDATES.md`。
- 真站冒烟 runbook → `docs/runbooks/`。

---

## 7. 待授权项（Agent 不自行执行的验收动作）

> 这些步骤本身没有技术障碍，但**副作用超出本仓库**，按 `PROJECT-PASSPORT.md` §4 必须先取得授权。

| 验收项 | 需要的动作 | 副作用 |
|---|---|---|
| M0 / R-01（L2） | `ci_local_run.py --action scheduler --trigger manual` | 对**真实课程**播放一个任务点（约 10–15 min），改服务端完成态并写 `state/`；`manual` 触发会**绕过 BLOCKED cooldown** |
| **M0 / L2 三次稳定性验证** | `ci_local_run.py --action scheduler --trigger manual --max-chapters 2 --repeat 3 --collect-diagnostics` | 真站连学 2–6 章（约 20–45 min），改服务端完成态 + 写 `state/` 并产生一次 `chore(state)` 提交；期间本机不得并发重型任务（见 §4.3 第 1 轮作废原因） |
| M0 / R-03（L2） | 故意造一次失败以验 `--collect-diagnostics` | 同上，且会在 registry 记一次失败。**已顺带达标**（§4 表 09-20 行），无需专门造失败 |
| M3 / R-20（L4） | `workflow_dispatch` 跑一次 `run.yml` | 云端真实学习 + `state/` 回写提交 |
| L4 一致性 diff | 启动 WSL2 Ubuntu 跑 Xvfb | Company 级服务，需 `D:\Company\requests\REQ-*` |

**当前真正阻塞 M0/L2 的问题**（原挂起的 `isPassed_seen` 测量嫌疑已于 §4.1 结案，此处不再重复）：

1. **M0 / R-01 三次稳定性：第 4 轮已达标**（§4.6）—— 其中 1 次是 D7 造成的 23s 空投；
   **D7 已复验通过**（§4.7：`tasks=87→87`、投到 `:video2`、无空投），所以 M0 判据本身闭合。
   没宣布结案的真正原因改成下面第 2 条：多视频章的第 2 点仍学不完（P1）。
2. **P1 章内切点（§4.8/§4.9）**：帧绑定已推送；复验被改道 —— 用户手动看完 708 点 2
   （服务端 finished，replay 无意义），`:video2` **已经服务端真源恢复路径（方案1）解冻**
   （SERVER_VERIFIED，cf=4 留痕；期间顺带修复 D12）。原"P1 残留"经只读观测**定性为
   已完成态 artifact**（未完成点绑定帧 50s 稳定），reload 策略已修；**未结**：
   真实未完成 `:videoN` 的端到端授权 run（候选 4738:video2）+ D13 幻影点级记录。
3. **P0-01 自适应看门狗从未生效**：第 4 轮 6/6 仍回落静态 900s（探测窗口内播放器没挂 metadata）。
   本轮最长 549.9s < 900s 属侥幸；>900s 内容的章会被误杀成 TIMEOUT。
4. **R-04 真站未验**：自动续播有 12 个单测，但四轮真站里**没有一次观测到它触发**。
5. **账本刚被重估**：§4.2 使未完成视频从 12 条变成 24 条。任何"还剩多少 / 何时学完"的
   既有结论都必须按新账重说一遍，旧结论不再引用。
6. **待查**：`1217304758`（标题「扩展阅读」）在 registry 里是 `video / COMPLETED / UI`
   —— 阅读类任务被建成 video 记录，与 §4.2 是不同源头，尚未定位。
7. **待查**：`1217304722` 完成证据由 `SERVER_VERIFIED` 变弱为 `UI`、`1217304705`
   被 live refine 读出 `video_total=2`（与"无视频章"的旧认定冲突）—— 见 §4.6 末尾两条。

---

## 附：一句话版本

> **每段"能跑"都要有（证据 + 可复现的判据）才算通过**；分层从最便宜(L1 pytest) 到最贵(L4 上云) 逐层放行，未达标绝不止步，回退倒着查——本地、云统一引擎、证据 diff=0，才是"本地稳定→上云"被证明的时刻。