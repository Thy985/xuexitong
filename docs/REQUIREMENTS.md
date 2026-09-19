# xuexitong 需求文档（PRD）

> 版本：Draft v1.0 · 日期 2026-09-15
> 基线：当前仓库 `main` 已实现 MVP（E5 engine + E6 scheduler + E7 TDVP）。
> 本文档目标是**明确"先本地跑通并稳定，再扩展上云"的产品/工程路线**，把前几轮对
> Autovisor（本地桌面成熟刷课工具）的对比结论固化成可执行需求，而非停留在口头讨论。

---

## 1. 背景与目标

### 1.1 现状（已实现）

- **运行载体**：GitHub Actions 定时（cron `0 16 * * *` = 北京时间 00:00）+ `workflow_dispatch`。
- **引擎**：`app/e2_headed_gha.py` 的 10 项闭合验证（`verification_10`），`passed_count==10` 才 PASS，失败归因 `failure_stage`。
- **调度**：`scheduler/scheduler.py` 状态机 RUN / NOOP / BLOCKED / ERROR + `consecutive_failures` 熔断 + `blocked_retry_interval` cooldown 自动复位。
- **任务注册表**：`app/registry/task_registry.py`，`mark_completed/mark_failed` 一律写入（成功失败都写），同章多视频用 `<chapter>:video_N` 精确标记。
- **探针**：`tvdp/tdvp.py` PassiveProbe（低成本 DOM 解析）+ ActiveProbe（高成本真实引擎）。
- **持久化**：`state/` 通过 git commit 跨 Run 保留（`active_course.json`、`courses/<key>.json`、`tdvp_tasks.json`）。
- **合规边界**：仅真实浏览器自然播放；不构造/伪造/重放 `multimedia/log`；不修改 `enc/attDurationEnc/videoFaceCaptureEnc/playingTime/_t`。

### 1.2 现状痛点（驱动本 PRD 的动因）

1. **依赖".env"/Secrets + GHA 环境**才能运行，普通用户（或未配置 CI 的本地）无法开箱即用。
2. **本地验证是"可选"而非一等公民**：虽然已有 `LOCAL_PLAYWRIGHT_RUNBOOK.md` 和 `scripts/`，但缺少与 GHA 等价的"一键本地 runbook/list"。
3. **浏览器健壮性偏"被动排查"而非"自愈"**：站点改版/偶发验证码/视频暂停时，主要靠 `retryable()` 分类重试，缺少主动自愈（续播、自动滑块、静音保护）。
4. **日志噪声**：长跑时大量"轮询未命中"等级别未明确区分，会淹没真实异常（对比 Autovisor 的 `is_expected_polling_error`）。

### 1.3 总体目标（本文档的"北极星"）

> **先让 xuexitong 在"本地 + Playwright 无头/有头"环境下稳定跑通一门课的全部视频任务点，且验证、注册表、状态持久化、失败归因全部正确；在此之上再把同样的代码路径原样扩展到云端（GHA 定时），做到"本地行，云端就行"。**

判定"本地稳定"的三条硬指标：
1. **归因准**：每次失败都能给出准确 `failure_stage`，不存在"失败但查不出原因"。
2. **熔断能自动恢复**：BLOCKED 后 cooldown 到期能自动重试，不会陷入失败重试死循环。
3. **注册表不静默**：成功/失败都写入 registry，任务不会卡死在队列头部。

---

## 2. 需求范围与优先级

> 优先级标记：`P0`=必须先做才能谈稳定 / `P1`=显著提升稳定与体验 / `P2`=体验增强，可后置。

### 2.1 本地一等公民（P0）

| ID | 需求 | 判验收标准 |
|---|---|---|
| R-01 | 提供**本地跑通全部课程视频的权威脚本**（等价 GHA `run/scheduler` 的本地实现） | `python scripts/ci_local_run.py --action scheduler --trigger manual --max-chapters N` 一键跑通，输出与 GHA 完全一致结构的 evidence JSON |
| R-02 | 本地运行环境可复现：Xvfb + 依赖 + secrets 的最小说明 | `docs/runbooks/LOCAL_FIRST_SETUP.md`，新机器按文档 10 分钟内可起一个可 run 的 headless 环境 |
| R-03 | 本地 fail 时把 `failure_stage` + 截图 + registry 状态打包可上传 | 一个 `--collect-diagnostics` 参数，产出 `diag_*.png` 与 registry dump，可丢给 issue / log |

### 2.2 浏览器健壮自愈（P1）

> 借鉴可对照的本地同类项目 `AutoVisor` 的成熟做法（OpenCV 滑块、续播、静音、轮询噪声分级），但**保持 xuexitong"不伪造 multimedia/log"的合规红线不变**。

| ID | 需求 | 备注 |
|---|---|---|
| R-04 | 视频暂停自动续播（poll `video.paused`，为真则重放） | 与"自然播放"不冲突；只是浏览器正常行为 |
| R-05 | 登录滑块验证自动通过 | 仅当你决定施放（xue exit 若坚持纯 DOM 可跳过；默认 P2，见下表） |
| R-06 | 自动静音 / 局域倍速，且操作不影响播放进度 | 只在"自然播放"边界内做，不伪造时间戳 |
| R-07 | 轮询类异常分级（"expected polling error" debug 级，真异常 warn/error） | 减少长跑日志噪声，保留真实 trace |
| R-08 | **可配浏览器启动**：`XUE_BROWSER_CHANNEL`（chrome/msedge/chromium）或 `XUE_BROWSER_EXE`（精确路径）驱动 launch，缺内置 build 时可用系统浏览器 | `utils/browser_factory.py`；默认仍 chromium，GHA/CI 行为零变化（参照 AutoVi 的 `EXE_PATH/driver`） |

> **R-05 明确标注为 P2**（可做可不做）：因为它与"仅真实播放"的合规叙事有一定的张力；若要推出，只在登录页滑块、且通过 `enableAutoCaptcha` 配置开关严格默认关闭，避免误导用户以为"自动化验证码"是 xuexitong 的核心卖点。

### 2.3 状态机 & 注册表增强（P1）

| ID | 需求 |
|---|---|
| R-10 | 本地 run 失败后再次 run 不清空已完成的 registry 记录（防重复学习） |
| R-11 | 多课程支持：`config` 里课程的 URL 列表，像 Autovi 的 `URL1..N` 循环 |
| R-12 | 连续失败始终准确累计；恢复成功即清零，避免"幽灵 BLOCKED" |

### 2.4 云端投产（P2, 本地稳定后）

| ID | 需求 |
|---|---|
| R-20 | 本地一键-runbook 的**同一引擎**暴露给 GHA（不改核心，只换启动器） |
| R-21 | 为"已拿证的本地 run"自动生成 GHA 触发所需 secrets 清单与 workflow 变量 |
| R-22 | Evidence 产物与本地跑一致（`result.json` + `verification_10` + `failure_stage`）可跨环境 diff |

---

## 3. 非目标（Non-Goals）

- ❌ 不实现"刷完整个课程在云端自动全跑"（当前一次只自然完成一个/若干视频点，`max_chapters` 可控）。
- ❌ 不引入伪造 `multimedia/log` 或篡改 `playingTime` 的机制（与 README 合规声明冲突）。
- ❌ 不做多账号并发；同账号严格串行（避免 `detect.chaoxing.com` 判异常）。
- ❌ 不把 `cv2/numpy` 等重型依赖变成启动必需项（按需、弱依赖，如 R-05 只在其启用时拉取）。

---

## 4. 里程碑（多阶段落地）

| 阶段 | 时间 | 目标 | 对应需求 |
|---|---|---|---|
| M0 本地基线 | 现在～1周 | 本地跑通 scheduler + registry + 持久化，`verdict==PASS` 可复现 | R-01..R-03 |
| M1 健壮自愈 | 1～2周 | 续播/静音/轮询降噪，长挂 4h 稳定；本地回归 P0/P1 全绿 | R1..R7（除 R-05 若选做 P2） |
| M2 状态机增强 | 2~3周 | 失败累计准确、多课程、防重复学习 | R10..R12 |
| M3 上云 | 3~4周 | 本地 runbook 同一引擎映射到 GHA，evidence 可 diff；cron 定时稳定 | R20..R22 |

> **建议 macOS/Linux 用户自然用 Xvfb；Windows 本机用户用 `headless=False` 也可直接 run**（文档里已有多跑 runbook）。

---

## 5. 验收 / 回退策略

> 详细验收体系见 **`docs/engineering-review/ACCEPTANCE.md`**（四层金字塔 + 里程碑矩阵 + 留痕表）。此处为摘要。

- **门禁 1（L1）**：`python -m pytest tests/` 全绿（现有 unit/integration/regression）。
- **门禁 2（L2）**：本地 `scheduler --trigger manual --max-chapters 2` 连续跑 3/3 PASS（需 M0 的 `ci_local_run.py`）。
- **门禁 3（L3）**：真站 `mooc2_probe.py` 冒烟 + 真实 run 推进一视频点，registry 写 `SERVER_VERIFIED`。
- **门禁 4（L4）**：cron 3 日 PASS + 本地/云 `verification_10` diff=0。
- **回退**：任一 L 层 FAIL 打回，云上偏差回退自查本地；不动 `state/` 与 registry（git 跨 Run 保留）。

---

## 6. 与现有工程体系的映射

- 本文档是**需求**来源；实现拆解见 `docs/architecture/DEVELOPMENT_PLAN.md`。
- 需求落地后进入 `docs/engineering-review/`（回归矩阵 / CI 门禁候选）维护。
- runbook 类写 `docs/runbooks/`。