"""xuexitong 本地 exe 构建脚本（仿 Autovisor build.py 的 dist 布局）。

用法：
    .venv/Scripts/python.exe build.py

产物：dist/Xuexitong/Xuexitong.exe（onedir；internal/ 为运行时，
state/ evidence/ .cache/ 为可写数据，随运行生成在 exe 旁）。

- 浏览器内置：PLAYWRIGHT_BROWSERS_PATH=0 下安装 chromium 到 playwright 包内，
  由 Xuexitong.spec 的 collect_data_files 一并收集 → 产物自包含、离线可用。
- 构建后预置 .env 模板（不含凭据，绝不覆盖已有文件）。
- GHA 模式不受影响：本脚本与 spec 只服务本地 exe 形态。
"""

import os
import pathlib
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist", "Xuexitong")


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


def main():
    # 浏览器装进 playwright 包内（.local-browsers），spec 的
    # collect_data_files('playwright') 会一并收集 → 产物自包含
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    run([sys.executable, "-m", "playwright", "install", "chromium"], cwd=ROOT)
    verify_browsers()
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "Xuexitong.spec"],
        cwd=ROOT)

    # 可写目录骨架 + .env 模板（仅缺失时创建，绝不覆盖已有凭据）
    for d in ("state", "evidence", ".cache"):
        os.makedirs(os.path.join(DIST, d), exist_ok=True)
    env_tpl = os.path.join(DIST, ".env")
    if not os.path.exists(env_tpl):
        with open(env_tpl, "w", encoding="utf-8") as f:
            f.write("# 学习通账号（本地保存，勿上传/入库）\n"
                    "CX_USER=\nCX_PASS=\n"
                    "# 可选：loop 每轮调度间隔分钟（默认 30）\n"
                    "# XUE_LOOP_INTERVAL=30\n")
        print(f"[build] 已生成凭据模板：{env_tpl}（填入 CX_USER/CX_PASS 后双击 exe 即可）")

    # 使用手册随包分发（exe 用户没有仓库也能查操作说明与错误速查）
    manual_src = os.path.join(ROOT, "docs", "USER_MANUAL.md")
    if os.path.exists(manual_src):
        shutil.copyfile(manual_src, os.path.join(DIST, "使用手册.md"))
        print("[build] 已随包附带使用手册：dist/Xuexitong/使用手册.md")

    print(f"[build] 完成：{DIST}")


if __name__ == "__main__":
    main()
