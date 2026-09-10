# LOCAL_CAPABILITY_MATRIX.md
# 本地能力矩阵（实测，2026-09-10 snapshot）

> 逐一验证「本地环境能否」以下能力。✅=实测通过；❌=实测不通过/受阻；⚠️=部分/需条件。
> 仅做事实记录，不夸大。

---

## 1. 能力矩阵

| 能力 | 结果 | 实测证据 | 备注 |
|---|---|---|---|
| 本地 Python 可导入 playwright | ✅ | `pip show playwright 1.62.0` | |
| 本地 Chromium 可启动(headless) | ✅ | `PLAYWRIGHT_LAUNCH_OK 151.0.7922.34` | PW cache chromium-1234 |
| 有头启动（Windows）| ⚠️ | 桌面存在；Linux runner 需 Xvfb | |
| Playwright 浏览器版本匹配 | ✅ | 引擎 151.0.7922.34 与 1.62.0 匹配 | |
| 读取 `.env` 的 CX_USER | ✅ | `creds_present=true`（修 parser 后） | `.env` 是 `key: value` 非 `=` |
| 读取 `.env` 的 CX_PASS | ✅ | 同源 success | 不外泄值 |
| cookie 会话存在且可注入 | ⚠️ | `.cache/cookies.json` 21 条，但**已失效**（返回“用户未登录”） | session stale |
| cookie 会话有效性判断 | ✅（已修复） | `ensure_login` 现追加「页面无『用户未登录』」DOM 校验（`_is_login_warning`） | 修复 URL-only 误报 |
| 密码登录填表能力 | ✅ | `#phone`/`#pwd` 存在即可填，登录按钮可点击 | 表单可达 |
| 密码登录（产出认证会话） | ✅（正确入口） | 用 **mooc2** 入口（`/mycourse/stu?...enc/t...`）登录 `login.ok=true`，标题「计算机网络-2025级」 | 关键：必须 mooc2 入口，非 mooc1 |
| 滑块验证码处理（如出现） | ⏳ 就绪 | 新增 `utils/captcha_slider.py`：检测+自动拖拽+人工回退，已接入 `ensure_login(captcha_mode)` | 本次真实登录**未触发滑块** |
| 真实课程已登录页 | ✅（mooc2） | 标题「计算机网络-2025级」，HTML 10,650,872B | 早期 e2e_evidence 的 766B 是 mooc1 错入口 |
| 课程目录 DOM 解析 | ✅ | 章节×6 / 目录×4 / 视频×16 | mooc2_evidence/page.html |
| 只读探索（不写 state/）| ✅ | 证据全部写 `docs/evidence/`（mooc2_evidence / e2e_evidence*） | 满足 scope |
| 视频播放（learning/multimedia） | ⚠️ | 未执行（scope 只读）；**需要登录** | 不宜本地强行 |
| 写 `state/` 生产状态 | ❌（本轮刻意不做） | scope=只读 | 保持审计可信 |
| 离线 pytest（不装浏览器） | ✅ | `python -m pytest tests/` → 149 pass + 1 skip | 只 import playwright 模块，不 launch |
| CI `test.yml`（push/PR 自动跑） | ✅（新增） | `.github/workflows/test.yml` 跑 unit/integration/regression | 堵 TFS-6「CI 从不跑 pytest」 |
| 离线 DOM/state/网络 fixture | ✅（新增第一批） | `tests/fixtures/`（dom/state/net/） | 脱敏快照，供 P0-03 等回归

---

## 2. 结论
- **本地可做（全部实测通过）**：启动浏览器、登录、打开真实课程（mooc2 入口）、读目录/章节/视频列表，并跑离线 149 测试。
- **关键认知**：真实课程入口必须是 `mooc2-ans.../mooc2-ans/mycourse/stu?...enc/t...`；`enc/t` 动态（会变），写死 enc 会失效。早期用 mooc1 `studentstudy` 一直「用户未登录」系**用错入口**。
- **已落地改进**：
  - `ensure_login` 增加「页面无『用户未登录』」DOM 断言（`_is_login_warning`）。
  - 新增 `utils/captcha_slider.py`（滑块处理就绪），本次未触发滑块。
  - 新增 `tests/fixtures/`（第一批脱敏 DOM/state/net 快照）与 `.github/workflows/test.yml`（push/PR 自动跑 149）。

---

## 4. 建议后续（不是本轮）
- 播放/学分同步 → 需用户授权跑一轮正式 run（会写 `state/` 与服务端进度）。
- 把 mooc2 正确入口 + 动态 `enc/t` 取数纳入 run/tvs 发现，避免写死 enc。
- 若某账号出现滑块 → `XUE_CAPTCHA_MODE=auto`(默认) 或 `manual`；不要无限自动重试。