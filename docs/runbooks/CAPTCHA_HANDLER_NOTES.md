# CAPTCHA（滑块验证码）处理：设计与使用

> 新增模块 `utils/captcha_slider.py`，并接入 `utils/cookie_store.ensure_login`。
> 背景：真实登录被滑块验证码（geetest）拦时的处理能力。**注意**：按当前实测（2026-09-10），
> 本账号密码登录**未触发滑块**（登录被“账号/密码未认证”挡住），所以该能力处于「就绪未命中」。

---

## 1. API

```python
from utils.captcha_slider import detect_slider, solve_slider, wait_manual

detect_slider(page) -> bool          # 判断当前页面是否出现滑块 widget
solve_slider(page, attempts=3, mode="auto",
             save_path=None, drag_override=None) -> str
     # 返回: "no_captcha" | "solved" | "failed" | "manual_needed"
wait_manual(page, timeout_s=60.0) -> str   # 等人工完成（headed）
```

### 集成到 ensure_login
```python
ensure_login(page, ctx, url, user, pw,
             captcha_mode="auto",        # auto / manual / auto_then_manual / skip
             captcha_attempts=3)
```
- `auto`（默认）：自动拖拽多次（无图像库，用轨道宽*0.75 估算距离）。
- `manual`：检测到滑块就等人（headed 里人工拖）。
- `auto_then_manual`：先自动，失败再等人。
- `skip`：完全跳过滑块，维持旧行为。
- 环境变量 `XUE_CAPTCHA_MODE` 可覆盖默认（`auto/manual/auto_then_manual/skip`）。

---

## 2. 检测范围（选择器与文本）
- 按钮/面板类选择器：`.geetest_slider_button` `.geetest_holder .geetest_slider`
  `.geetest_panel` `.geetest_wind` `.geetest_holder` `#captcha-container`。
- 文本提示：滑块验证 / 拖动滑块 / 请完成验证 / 向右滑动 / geetest / captcha。
- **注意**：不做「验证码」/「验证码登录」这类泛词匹配（避免把 SMS 登录 tab 误判为滑块）。

---

## 3. 距离估算与精确缺口（可选增强）
- 默认距离 = 轨道宽 × 0.75（轨道优先取 `.geetest_slider_track` 等窄元素，避免取到全宽容器）。
- 若调用方能从截图感知缺口 offset，可传 `drag_override=<像素>` 精确拖拽。

---

## 4. 能/边界（如实）
- geetest 可能带轨迹校验/时限风控，`auto` 不保证命中 → 失败应回退 `manual` 或改用有效 cookie；不要无限重试。
- 可靠路径仍是：**人工登录一次 → 导出有效 cookie 到 `.cache/cookies.json`** 复用。
- 本实现不引入 PIL 等新二进制依赖（用相对拖拽 + 多次尝试）。

---

## 5. 实测记录
- `utils` 导入、语法、合成滑块 DOM 的 `detect_slider`/轨距/按钮坐标 均通过。
- 真实密码登录（`diag_login.py`）**未触发滑块**，登录仍停留在「用户未登录」→ 当前阻塞归因于账号认证而非滑块。