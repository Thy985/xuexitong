"""xuexitong 本地 exe 构建脚本（仿 Autovisor build.py 的 dist 布局）。

用法：
    .venv/Scripts/python.exe build.py

产物：dist/Xuexitong/Xuexitong.exe（onedir；internal/ 为运行时，
state/ evidence/ .cache/ 为可写数据，随运行生成在 exe 旁）。

- 浏览器内置：PLAYWRIGHT_BROWSERS_PATH=0 下安装 chromium 到 playwright 包内，
  由 Xuexitong.spec 的 collect_data_files 一并收集 → 产物自包含、离线可用。
- 产物**默认是干净的**：构建前把 dist 下本机的 .env/.cache/state/evidence 挪到
  临时目录且不还原（产物要发给别人，带着构建者的账号进度和 Cookie 等于泄露）。
  想在本机继续用旧数据迭代，加 `--keep-data`；不加就把数据留在打印出的暂存目录。
- GHA 模式不受影响：本脚本与 spec 只服务本地 exe 形态。
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist", "Xuexitong")

# PyInstaller 的 COLLECT 会整个删掉 dist/Xuexitong 再重建，所以「文件不存在才写模板」
# 这种事后判断根本来不及 —— 构建前先把本地数据挪走。
# 这四项全是**构建者本机**的东西（账号进度/登录 Cookie/凭据/运行现场），
# 绝不能出现在要发给别人的产物里。
PRESERVE = (".env", ".cache", "state", "evidence")


def run(cmd, **kw):
    print(">>", " ".join(cmd), flush=True)
    code = subprocess.call(cmd, **kw)
    if code != 0:
        raise SystemExit(f"构建失败(exit={code}): {' '.join(cmd)}")


def verify_browsers():
    """PLAYWRIGHT_BROWSERS_PATH=0 时 chromium 应落在 playwright 包内。"""
    import playwright
    pkg = pathlib.Path(playwright.__file__).parent
    local_browsers = pkg / "driver" / "package" / ".local-browsers"
    chromium = list(local_browsers.glob("chromium-*")) if local_browsers.exists() else []
    if not chromium:
        raise SystemExit(
            f"未找到内置浏览器：{local_browsers}\n"
            "PLAYWRIGHT_BROWSERS_PATH=0 下 playwright install chromium 应生成在"
            " playwright/driver/package/.local-browsers；请检查安装日志。")
    size_mb = sum(f.stat().st_size for f in chromium[0].rglob("*")
                  if f.is_file()) // (1024 * 1024)
    print(f"[build] 内置浏览器就绪：{chromium[0].name}（约 {size_mb} MB）")


def stash_user_data():
    """构建前把 dist 下的本地数据挪出产物，返回 (暂存目录, 还原函数)。

    **默认不还原。** 产物是要发给别人的，里面绝不能带构建者的账号状态、
    登录 Cookie 或进度账本 —— 那是隐私泄露，比本地丢配置严重得多。
    暂存目录会打印出来，丢了就手工拷回去；自己迭代调试想留着，用 --keep-data。
    """
    saved = tempfile.mkdtemp(prefix="xuexitong-preserve-")
    moved = []
    for name in PRESERVE:
        src = os.path.join(DIST, name)
        if os.path.exists(src):
            dst = os.path.join(saved, name)
            shutil.move(src, dst)
            moved.append(name)
    if moved:
        print(f"[build] 已把本地数据移出产物：{', '.join(moved)}")
        print(f"[build]   暂存于：{saved}")
        print(f"[build]   需要放回 dist 继续用本地数据："
              f"xcopy /E /I /Y \"{saved}\\*\" \"{DIST}\\\"")

    def restore():
        for name in moved:
            src = os.path.join(saved, name)
            dst = os.path.join(DIST, name)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.exists(dst):
                shutil.rmtree(dst) if os.path.isdir(dst) else os.remove(dst)
            shutil.move(src, dst)
        shutil.rmtree(saved, ignore_errors=True)
        if moved:
            print("[build] 本地数据已放回 dist/Xuexitong")

    return saved, restore


def main():
    # 浏览器装进 playwright 包内（.local-browsers），spec 的
    # collect_data_files('playwright') 会一并收集 → 产物自包含
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    run([sys.executable, "-m", "playwright", "install", "chromium"], cwd=ROOT)
    verify_browsers()

    keep_data = "--keep-data" in sys.argv
    _saved, restore = stash_user_data()
    try:
        run([sys.executable, "-m", "PyInstaller", "--noconfirm", "Xuexitong.spec"],
            cwd=ROOT)
    finally:
        # 只有显式 --keep-data 才放回；否则产物保持干净（本地数据留在暂存目录）
        if keep_data:
            restore()
        elif not os.path.isdir(_saved) or not os.listdir(_saved):
            shutil.rmtree(_saved, ignore_errors=True)

    # 可写目录骨架 + 空白 .env 模板 —— 产物里只放空目录骨架，不放任何真实数据。
    # 键必须是**注释形式**：裸的 `CX_USER=` 空值行会被 env_file 先到先得地读成
    # ""，永远遮住用户之后填的真值（exe 二次启动反复要求输入的根因）。
    for d in ("state", "evidence", ".cache"):
        os.makedirs(os.path.join(DIST, d), exist_ok=True)
    env_tpl = os.path.join(DIST, ".env")
    if not os.path.exists(env_tpl):
        with open(env_tpl, "w", encoding="utf-8") as f:
            f.write("# 学习通账号（本地保存，勿上传/入库）\n"
                    "# 方式一：取消下面两行注释并填入账号密码后保存。\n"
                    "# 方式二：直接双击 Xuexitong.exe，按提示输入（自动写回本文件）。\n"
                    "# CX_USER=\n"
                    "# CX_PASS=\n"
                    "# 可选：loop 每轮调度间隔分钟（默认 30）\n"
                    "# XUE_LOOP_INTERVAL=30\n"
                    "# 可选：视频播放倍速 0.5–2.0（默认 1.0，不倍速）\n"
                    "# XUE_PLAYBACK_RATE=1.0\n")
        print(f"[build] 已生成凭据模板：{env_tpl}（填入 CX_USER/CX_PASS 后双击 exe 即可）")

    # 使用手册随包分发（exe 用户没有仓库也能查操作说明与错误速查）
    manual_src = os.path.join(ROOT, "docs", "USER_MANUAL.md")
    if os.path.exists(manual_src):
        shutil.copyfile(manual_src, os.path.join(DIST, "使用手册.md"))
        print("[build] 已随包附带使用手册：dist/Xuexitong/使用手册.md")

    # 故障收集脚本 + 版本戳：
    # - 收集脚本**刻意不依赖 exe 可执行**——「双击闪退」本身就是要报的问题，
    #   挂在 exe 的 --action 上，那类 issue 永远收不到现场。
    # - VERSION 同时进包（utils/version.py 读 _MEIPASS 那份，写进 evidence.meta）
    #   与 exe 旁（收集脚本给不出 exe 自报版本时的兜底）。
    tools_dst = os.path.join(DIST, "tools")
    os.makedirs(tools_dst, exist_ok=True)
    for fname in ("collect_diagnostics.ps1",):
        src = os.path.join(ROOT, "tools", fname)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(tools_dst, fname))
    for fname in ("收集故障信息.bat", "VERSION"):
        src = os.path.join(ROOT, fname)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(DIST, fname))
    print("[build] 已随包附带：收集故障信息.bat + tools/collect_diagnostics.ps1 + VERSION")

    verify_clean_product(keep_data=keep_data)
    print(f"[build] 完成：{DIST}")


def verify_clean_product(keep_data=False):
    """产物自检：确认没把构建者本机的数据带进要发给别人的包里。

    这是隐私护栏，不是洁癖。之前 stash 完又还原，等于每次打包都把账号进度、
    登录 Cookie、.env 明文密码一起发给使用者 —— 而这类东西一旦发出去就收不回了。
    """
    if keep_data:
        print("[build] 自检跳过（--keep-data：产物已按要求带上本机数据）")
        return

    leaks = []
    # 1) 目录必须存在但为空
    for d in ("state", "evidence", ".cache"):
        p = os.path.join(DIST, d)
        if not os.path.isdir(p):
            leaks.append(f"缺少空目录 {d}/")
        else:
            found = [f for f in os.listdir(p)]
            if found:
                leaks.append(f"{d}/ 非空，含 {len(found)} 项：{', '.join(sorted(found)[:5])}")
    # 2) .env 必须是空模板，不能有凭据
    env_tpl = os.path.join(DIST, ".env")
    if os.path.exists(env_tpl):
        for line in open(env_tpl, encoding="utf-8"):
            line = line.strip()
            if line.startswith("CX_USER=") and line != "CX_USER=":
                leaks.append(".env 里 CX_USER 有值（凭据泄露）")
            if line.startswith("CX_PASS=") and line != "CX_PASS=":
                leaks.append(".env 里 CX_PASS 有值（凭据泄露）")

    if leaks:
        raise SystemExit(
            "构建失败：产物未通过清洁自检，不要把这个包发出去 ——\n  - "
            + "\n  - ".join(leaks))
    print("[build] 产物清洁自检通过：无凭据 / 无 Cookie / 无账号进度")


if __name__ == "__main__":
    main()
