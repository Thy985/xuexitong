# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — xuexitong 本地 exe（onedir，仿 Autovisor dist/Autovisor 布局）。

产物：dist/Xuexitong/Xuexitong.exe（+ internal/ 运行时；state/ evidence/ .cache/
等可写目录由程序在 exe 旁自动生成，见 utils/paths.py）。

浏览器内置：build.py 以 PLAYWRIGHT_BROWSERS_PATH=0 把 chromium 预装进 playwright
包内（driver/package/.local-browsers），由下方 collect_data_files('playwright')
一并收进包 → 产物自包含、离线可用；也可用 XUE_BROWSER_CHANNEL=msedge 改用系统
浏览器（utils/browser_factory.py 原生支持）。

GHA 模式不受影响：本 spec 与 build.py 只服务于本地 exe 形态，源码运行路径零改动。
"""
import sys
from pathlib import Path

ROOT = Path(SPECPATH)

datas = [
    # 浏览器引擎注入用的 user.js（运行时经 utils.paths.resource_root() 读取）
    (str(ROOT / "scripts" / "v3_optimized.user.js"), "scripts"),
    # 版本戳：冻结形态下 utils/version.py 从 _MEIPASS 读这份，写进 evidence.meta。
    # 缺了它，本地 exe 报的 issue 就无法判断对方是哪个 build（复现不了的常见原因）。
    (str(ROOT / "VERSION"), "."),
]
# playwright driver + .local-browsers（内置 chromium）由 playwright 包自带的
# PyInstaller hook（entry point `pyinstaller40`）自动收集，无需在此 collect。

hiddenimports = [
    # 函数体内/懒加载导入，显式列出保险（modulegraph 通常也能静态扫到）
    "models",
    "resolvers.course_resolver",
    "state.course_state",
    "scheduler",
    "tvdp.tdvp",
    "app.e2_headed_gha",
    "app.loop",
    "app.registry.task_registry",
    "app.registry.reconcile",
    "app.registry.click_probe",
    "app.probe_catalog",
    "utils.browser_factory",
    "utils.captcha_slider",
    "utils.cookie_store",
    "utils.env_file",
    "utils.paths",
    # 运行期由 app.loop / app.e2_headed_gha 直接 import，显式列出
    "utils.runlog",
    "utils.version",
    # 注意：utils.diag_summary **刻意不进包** —— 它是维护者侧工具，收到用户
    # 交来的诊断包后用源码 venv 跑 `python -m utils.diag_summary <目录>` 即可。
    # 冻结形态里没有调用方，打进去只是白占体积。
]

a = Analysis(
    [str(ROOT / "app" / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

# playwright 自带 hook 会把 .local-browsers 全量收集（含 headless shell），
# spec datas 层过滤无效 —— 只能在 Analysis 结果上过滤。.exe/.dll 被归类为
# BINARY 进 a.binaries，其余文件进 a.datas，两边都要滤：
#   - chromium_headless_shell（~270MB）：本项目所有启动点均 headless=False，用不到
#   - winldd：Linux 诊断工具
#   - ffmpeg 保留（视频播放需要）
def _drop_unused_browser_parts(entries):
    return [
        e for e in entries
        if "chromium_headless_shell" not in e[0] and "winldd" not in e[0]
    ]

a.datas = _drop_unused_browser_parts(a.datas)
a.binaries = _drop_unused_browser_parts(a.binaries)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Xuexitong",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=None,
    # 注意：contents_directory 必须写在 EXE 上（COLLECT 从 EXE 参数继承；
    # 写在 COLLECT 上会被静默忽略回落 _internal）
    contents_directory="internal",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Xuexitong",
)
