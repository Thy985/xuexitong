# E2E BASELINE REPORT — 本地 Playwright & 真实课程只读探测基线（修订版）
日期：2026-09-10（UTC 03:39 修订） · 环境：Windows local · Python 3.12.14 · Playwright 1.62.0 · Chromium 151.0.7922.34

> 权限：只读性真实登录 + 读课程目录；**未播放视频 / 未注册课程点 / 未写生产 `state/`**。
> 决定性证据：`docs/evidence/mooc2_evidence/`（原始页面不入库，其脱敏最小快照见 `tests/fixtures/net/probe_mooc2_ok.json` 与 `tests/fixtures/dom/`）。

---

## 0. 核心结论（修订版 — 早前用错入口导致误判）
- ✅ **本地 Playwright 能启动、能真实登录、能读到课程目录** —— 全链路在正确入口下**打通**。
- 🔑 **根因纠正**：之前的「登录不达」（一直回「用户未登录」）是**因为用了错误的课程入口 URL**
  （`mooc1.../mooc-ans/mycourse/studentstudy`）。本次改用**正确入口**：
  `https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/stu?courseid=265997861&clazzid=151695658&cpi=506830460&enc=<当次>&t=<当次>&pageHeader=0&v=2&hideHead=0`
  登录即**通过**：`login.ok=true`，页面标题 **「计算机网络-2025级」**，HTML ≈10.6MB，含 章节×6 / 目录×4 / **视频×16**，`not_logged_in=false`、`permission_gate=false`、`personal_space=true`。
- ✅ **`.env` 凭据在正确入口下有效**（认证成功）；**未触发任何滑块验证码**。
- ⚠️ 早前「密码失效 / 滑块拦截 / 缓存过期」均为错入口导致的误判，现已全部纠正。

---

## 1. 早期结论（错入口 mooc1，保留作对照）
- 用 `mooc1.../mooc-ans/mycourse/studentstudy` 时持续「用户未登录」(766B)，曾被误判为「凭据失效 / 滑块拦截」——**已被正确入口证伪**。
- 期间顺带修复一个真实 bug：`ensure_login` 原只按「URL 是否落在 login 页」判已登录，会把「未重定向到 login 的失效会话」误判为已登录；已追加 `_is_login_warning` DOM 校验（`utils/cookie_store.py`）。该修复仍保留（防止错入口误报）。

---

## 2. 事实执行流水（mooc2 正确入口，只读）

| 步骤 | 结果 | 证据 |
|---|---|---|
| 环境确认 | ✅ Python 3.12.14 / PW 1.62.0 / chromium-1234 | 终端 |
| Chromium headless launch | ✅ `PLAYWRIGHT_LAUNCH_OK 151.0.7922.34` | 输出 |
| 加载 `.env`(CX_USER/CX_PASS) | ✅ `creds_present=true` | mooc2_evidence/probe.json |
| 登录（cookie 优先 + 密码） | ✅ `login.ok=true`，URL=mooc2 课程页 | mooc2_evidence/probe.json |
| 课程渲染 | ✅ 标题「计算机网络-2025级」，HTML 10,650,872B | page.html / page.png |
| 目录/章节/视频 | ✅ 章节×6 / 目录×4 / 视频×16 | page.html 词频 |
| 页面可用性标记 | ✅ personal_space=on / 无「用户未登录/暂无权限」 | markers |
| DOM 安全 | ✅ 无明文密码/CX_PASS/手机号泄漏 | 扫描 |
| 只读约束 | ✅ 未播放、未注册点、未写生产 `state/`，证据在 `docs/` | git status |

---

## 3. 关键技术发现：入口 URL 决定成败
- 本系统代码默认入口是 `mooc1.../mooc-ans/mycourse/studentstudy`（见 `e2/e2_headed_gha.py` DEMO）；而该账号可用学习后台实际在 `mooc2-ans.../mooc2-ans/mycourse/stu`（带 `course_id/clazz_id/cpi/enc/t` 动态参数）。
- `enc`/`t` 每次会变：用户提供的 enc=<当次>（本次）与代码写死 `DEMO_ENC` 不同 → **写死 enc 会失效**，须由真实跳转/TDVP discovery 当次取得。
- `ensure_login` 的 URL-only 判据在错入口情况会误报；已用 `_is_login_warning` 兜底。

---

## 4. 登录判据修复 + 滑块功能（保留，就绪未命中）
- ✅ 修复：`ensure_login` 追加「页面无『用户未登录』」DOM 校验（`utils/cookie_store.py::_is_login_warning`）。
- ✅ 新增：`utils/captcha_slider.py` —— 滑块检测 + 自动拖拽 + 人工回退，接入 `ensure_login(captcha_mode=...)`。
- ⚠️ 实测：在真实登录（mooc2 正确入口）下**未触发滑块**，该能力「就绪未命中」；若某账号登录出现滑块它会被自动调用。

---

## 5. 基线结论（更新）

| 项目 | 基线值 | 备注 |
|---|---|---|
| Chromium 本地启动 | ✅ 正常 | 可作 PR smoke |
| 真实登录（按**正确入口**) | ✅ 通过（`.env` 凭据有效） | 关键：一定要 mooc2 入口 |
| 课程目录 / 章节 / 视频读取 | ✅ 可达（章节×6 / 视频×16） | 只读抓取 |
| 写生产 state/ 学习进度 | 本轮 **未做**（保持只读） | 需另行确认 |
| 滑块验证码处理（如出现） | ⏳ 就绪未命中 | 内部试点 |

**因此本 repo 本地 Playwright「真实 E2E」在**正确入口**下可完成登录 + 目录读取**；「播放/进度点同步/写 state」仍需在用户明确授权下另跑一个正式 run。

---

## 6. 后续建议（已落地与仍待）
- ✅ 修复 `ensure_login` 已登录判据（`_is_login_warning`）。
- ✅ 实现滑块处理并接入 `ensure_login(captcha_mode)`。
- ⏳ 待定：把 mooc2 正确入口 + 动态 `enc/t` 取数纳入 run/tvs 发现路径，避免写死 enc。
- ⏳ 待定：用户确认开启真实学习后，再跑一轮带播放/进度注册的正式 E2E（会写 `state/` 与服务端进度）。