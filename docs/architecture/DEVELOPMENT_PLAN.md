# xuexitong 开发文档（DEVELOPMENT PLAN）

> 版本：Draft v1.0 · 关联需求：`docs/REQUIREMENTS.md`
> 目标：把需求文档里的"本地先行→上云"路线拆成**可执行、以现有代码为准**的开发步骤，
> 并定位每一处改动落在哪个文件、改动什么、验收怎么测。

---

## 1. 当前架构速览（代码为准）

```
app/
  run.py                # 入口：initialize / run / switch / scheduler / probe
  e2_headed_gha.py      # E5 引擎：10 项闭合验证（verification_10），passed_count
  catalog.py            # 目录树探查
  probe_catalog.py      # CI 探查辅助（转储 #coursetree DOM，诊断 fetch 空）
  registry/
    task_registry.py    # TaskRecord, save/load, done_chapter_ids, reconcile_queue
    reconcile.py        # reconcile_registry / stale_completed_by_catalog / pick_conflict
    click_probe.py      # click_probe_chapter_id
scheduler/
  scheduler.py          # determine_action / run_scheduler / record_result
                        #   + BLOCKED cooldown（blocked_hits / blocked_retry_interval）
tvdp/
  tdvp.py               # PassiveProbe / ActiveProbe / EvidenceAggregator
state/
  course_state.py       # load/activate/archive/initialize/run_course  + 持久化
  migrations/repair.py  # 状态修复
models.py               # 共享领域模型（CourseIdentity / CourseParams）——单一起源
resolvers/
  course_resolver.py    # resolve_course / detect_course_change / _parse_url_params
utils/
  cookie_store.py       # ensure_login / cookie 保存加载
  captcha_slider.py     # 滑块验证（已存在，需求 R-05 若启用则复用）
tests/                  # unit / integration / regression（含真实 DOM fixture）
docs/                   # REQUIREMENTS.md（本文档）+ architecture/ + runbooks/ + engineering-review/ + evidence/
```

**核心数据流**（本地与云一致）：
```
命令/CI 触发 → app/run.py --action scheduler
    → scheduler.run_scheduler()          # 读取 state，determine_action：RUN/NOOP/BLOCKED
        → tvdp PassiveProbe               # 低成本扫描 tdvp_tasks.json
        → 若 RUN：调 app/e2_headed_gha.run_test()  → verification_10
        → registry.mark_completed/mark_failed       # 成功失败都写，绝不静默
    → state_course_state.write/persist       # course_state on disk，git commit 跨 Run
    → 输出 evidence JSON + 诊断               # verdict: PASS/FAIL/DEGRADED + failure_stage
```

---

## 2. 开发里程碑实施步骤（锚定本地先行）

### M0 · 本地基线
> 让"本地跑通"成为一等公民。

1. **[脚本] `scripts/ci_local_run.py`（R-01）**
   - 封装现有 `app/run.py --action scheduler --trigger manual`，补齐：
     - `--max-chapters N` 透传；可选 `--xvfb-display`。
     - 输出结构= GHA 完全一致的 evidence JSON（`verdict/passed_count/failure_stage`）。
   - 验收：新开终端 `python scripts/ci_local_run.py --action scheduler --trigger manual --output ./evidence/local_<ts>.json`，`verdict==PASS` 且 `evidence/verification_10` 10 项全 True。
2. [1] **本地环境说明（R-02）**：`docs/runbooks/LOCAL_FIRST_SETUP.md`
   - Linux/mac：`xvfb-run -a` / `Xvfb :99` + `export DISPLAY=:99`；Windows：`headless=False` 直接可见。
   - 依赖：`pip install -r app/requirements.txt`（含 playwright + sync）。
   - secrets：说明 `CX_USER/CX_PASS` 可通过 `.env` 或环境变量注入（不硬编码进 CI）。
3. [1] **诊断打包（R-03）**：`ci_local_run.py` 增加 `--collect-diagnostics`，
   一旦 FAIL 就把 `diag_*.png`（若引擎已存）+ `registry dump` + `.jsonld` 证据打包为 zip。

### M1 · 浏览器健壮自愈
> 借鉴 AutoVisor 模块（`tasks.py` 的 `video_optimize/play_video`、`utils.py` 的 `optimize_page`），
> **顺时针落在 xuexitong 的引擎循环内**，且不越过合规红线。

4. [2] **自动续播（R-04）**：在 `app/e2_headed_gha.py` 的视频播放等待循环里，
   加轮询 `video.paused`：若为真则 `video.play()`，并记一次 `recovered_count` 进 evidence。
   - 不动 `multimedia/log`、不改 `playingTime`。验收：人为 `page.click('.pause')` 可被自动恢复。
5. [3] **静音/倍速本地化（R-06）**：新增 `app/actions.py`（新模块）封装
   `mute_video / set_playback_rate(safe)`，在引擎可配置开关 `--mute --speed 1.5`下生效。
   - `auto` 静音只对声音、倍速只对 `playbackRate`，不涉时间戳合成。
6. [4] **轮询噪声分级（R-07）**：`app/e2_headed_gha.py` 现有 retryable 分类
   已是雏形（`retryable(verdict)`），把它转成更通用的 `is_expected_polling_error(e)`
   （借鉴 AutoVisor `tasks.is_expected_polling_error`），日志：expected→debug，其余→warn/error。
   - 验收：无真异常时长跑 1h，日志无刷屏、无消失的ERROR。
7. [5] **滑块验证（R-05，若选做）**：复用现有 `utils/captcha_slider.py`，
   挂在登录后（需面对 login 页）时，用**配置开关 `enableAutoCaptcha` 默认 False**。
   - 可与 `e2_headed_gha.ensure_login` 组合。验收：开启后登录页滑块自动拖动；关闭后不触发。
8. [5b] **可配浏览器（R-08）**：`utils/browser_factory.py` 提供 `launch_kwargs()`
   （读 `XUE_BROWSER_CHANNEL` / `XUE_BROWSER_EXE`，默认 `channel="chromium"`），
   接入 `app/e2_headed_gha.py`、`scheduler/scheduler.py`、`tvdp/tdvp.py` 的 launch 点。
   - 验收：`XUE_BROWSER_CHANNEL=msedge` 时仍能 launch 且 evidence/归因一致；未设时用默认 chromium。

### M2 · 状态机 & 注册表
9. [6] **防重复学习（R-10/R-11）**：`mark_completed` 时校验
   `done_chapter_ids` / `stale_completed_by_catalog`，已有 reconcile 逻辑（`app/registry/reconcile.py`）——
   补充"重复 run 同一已完成 chapter 时不 reset 进度" 的回归用例。
9. [7] **多课程（R-11）**：在 `scheduler/` 增加 `course_urls` 列表解析（config 或 env），
   逐课程循环（参考 AutoVi 的 `URL1..N`），每次 run 一个课程独立持久化。
10. [8] **累计失败准确清零（R-12）**：`record_result` 里当 `verdict==PASS` 时
    `consecutive_failures=0` 且 `blocked_hits=0`；已有实现需加单测补锁。

### M3 · 上云（本地稳定后）
11. [9] **同一引擎上云**：不重写核心，`run.yml` 里 `learn` job 改为调用
    `scripts/ci_local_run.py`（或等价 `app.run.cmd_scheduler`），保证"本地/云的 evidence 结构可 diff"。
12. [10] **证据 diff**：给 CI 加一个 `if failure_stage 截图 / registry` 上传 artifact 的 step；
    本地/云跑出的 `verification_10` 序列可逐项比对，找出环境差异（无头 vs 有头）。

---

## 3. 每个模块的"改动点"清单（对照文件）

| 改动目标 | 文件（现有代码位置） | 改动内容 |
|---|---|---|
| 本地脚本 | `scripts/ci_local_run.py`（新） | 包装 `run.py --scheduler` + 诊断打包 |
| 续播 | `app/e2_headed_gha.py` | 视频循环加 `paused→resume` 轮询 |
| 静音/倍速 | `app/actions.py`（新） | 封装 `mute/set_playback_rate` |
| 滑块 | `utils/captcha_slider.py` + `app/e2_headed_gha.ensure_login` | 接开关 |
| 噪声分级 | `app/e2_headed_gha.py` / `scheduler/scheduler.py` | `is_expected_polling_error` |
| 可配浏览器 | `utils/browser_factory.py`（新）+ `app/e2_headed_gha.py` / `scheduler/scheduler.py` / `tvdp/tdvp.py` | `launch_kwargs()` 读 `XUE_BROWSER_CHANNEL/EXE`，默认 chromium |
| 注册表防重 | `app/registry/reconcile.py` / `task_registry.py` | 已有，补单测 |
| 多课程 | `scheduler/scheduler.py` / `config` | `course_urls` 列表 |
| 失败清零 | `scheduler/scheduler.py`（`record_result`） | 补断言 |
| 上云映射 | `.github/workflows/run.yml` | job 调本地脚本 + 证据 diff |

> 以上所有改动**不应改 `models.py` 的共享领域模型**（`CourseIdentity`/`CourseParams` 是单一起源，
> 若需新字段必须回到 `models.py` 扩展并跑全套测试；避免出现第二份隐式模型）。

---

## 4. 测试策略（延续现有分层）

- **unit/**：`task_registry.mark_completed/mark_failed` 状态迁移；`scheduler.determine_action` 的 RUN/NOOP/BLOCKED + cooldown。
- **integration/**：`scheduler + registry 持久化`（已有），补"多课程顺序"、"失败累计清零"。
- **regression/**：历史事故回归（已有 `test_regression_p003_dom.py`、`p05_p06`），新增：
  - 滑块入口若启用，记录登录页 fixture 与用例。
  - 续播逻辑（模拟 `paused`）用例。

**本地验收（M0 gate）**：
```bash
python -m pytest tests/ -q            # 全绿
python scripts/ci_local_run.py --action scheduler --trigger manual --max-chapters 2  # PASS 可复现
```

> **每个里程碑的"通过由 `docs/engineering-review/ACCEPTANCE.md`（四层验收体系）裁定**：
> 不是"看起来能跑"就行，必须有判据 + 证据（L1 pytest、L2 本地真浏览器、L3 真站冒烟、L4 上云
> `verification_10` diff=0），并把结果滚动记入 ACCEPTANCE 的验收记录表。详见该文件。

---

## 5. 里程碑与回退

- 同 REQUIREMENTS §4。每步独立可回退——新增模块（`app/actions.py` / `scripts/ci_local_run.py`）先行，
  不改 `e2_headed_gha.py` 的多畴状态机的，风险最小。
- 若 M0 本地都跑不通，禁止进入 M3 上云：先修本地，再谈云。

---

## 6. 关联文档

- 需求与路线：`docs/REQUIREMENTS.md`
- 工程审计/回归矩阵：`docs/engineering-review/`（落地后更新 `REGRESSION_MATRIX.md`）
- 本地 runbook：`docs/runbooks/LOCAL_PLAYWRIGHT_RUNBOOK.md`（M0 后新增 `LOCAL_FIRST_SETUP.md`）
- 运行诊断：`docs/运行诊断手册.md`