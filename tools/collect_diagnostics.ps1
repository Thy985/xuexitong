# collect_diagnostics.ps1 — 收集 xuexitong 本地 exe 的故障诊断包
#
# 设计约束（与 issue 处理直接相关）：
#  1. 不能依赖 exe 可执行 —— 「双击闪退」类问题恰恰是 exe 起不来，
#     若收集入口挂在 exe 的 --action 上，那类 issue 永远收不到现场。
#  2. 不能收集任何凭据：.env（含明文密码）与 .cache/（登录 Cookie）一律跳过。
#  3. 必须脱敏：课程 URL 里的 enc 是访问令牌，CDN 的 ak_/at_/ad_ 是临时签名，
#     手机号属个人信息 —— 都要在打包前抹掉，避免用户直接贴到公开 issue。
#  4. 只收有诊断价值的文件：evidence/（每章证据 + 子进程日志 + 截图）与
#     state/（任务账本/熔断计数）。internal/ 是 560MB 运行时，与排障无关。
#
# 用法：双击 收集故障信息.bat（会自动调用本脚本），或
#       powershell -ExecutionPolicy Bypass -File collect_diagnostics.ps1
#
# !! 本文件必须存成 UTF-8 with BOM + CRLF。PowerShell 5.1（Win10 自带）按 ANSI
#    读无 BOM 的 .ps1，保存时丢了 BOM，所有中文会变成「瀹屾垚锛?」这类乱码；
#    而这份脚本的输出正是给用户看的指引，乱码等于白搭。改完务必确认 BOM 还在。

param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Continue'

# Root 可能因调用方引号/转义问题变成空串或带尾反斜杠。空串必须在这里挡住 ——
# 带着空 $Root 继续跑，会在后面炸出一堆与根因无关的 Join-Path 报错。
if ([string]::IsNullOrWhiteSpace($Root)) {
    Write-Host '  [ERROR] -Root 为空。请双击本目录下的「收集故障信息.bat」，' -ForegroundColor Red
    Write-Host '          或手动指定：powershell -File collect_diagnostics.ps1 -Root "D:\path\to\Xuexitong"' -ForegroundColor Red
    exit 2
}
try {
    $Root = (Resolve-Path -LiteralPath $Root).ProviderPath
} catch {
    Write-Host "  [ERROR] 目录不存在：$Root" -ForegroundColor Red
    exit 2
}
# 去掉尾部分隔符，避免后续 Join-Path 拼出双反斜杠
$Root = $Root.TrimEnd('\', '/')

Write-Host ''
Write-Host '  xuexitong 故障信息收集' -ForegroundColor Cyan
Write-Host "  目录：$Root" -ForegroundColor DarkGray
Write-Host ''

# ── 1. 建暂存目录 ────────────────────────────────────────────────────
$stamp   = Get-Date -Format 'yyyyMMdd-HHmmss'
$stage   = Join-Path $env:TEMP "xuexitong-diag-$stamp"
New-Item -ItemType Directory -Path $stage -Force | Out-Null

# ── 2. 明确不收的东西（凭据）────────────────────────────────────────
$EXCLUDE_NAMES = @('.env', '.cache', 'cookies', 'internal', 'build', 'dist', '.git')
$EXCLUDE_EXT   = @('.lock', '.exe', '.dll', '.pyd', '.zip', '.pyc')

function Test-Excluded([string]$relative) {
    $parts = $relative -split '[\\/]'
    foreach ($p in $parts) {
        if ($EXCLUDE_NAMES -contains $p.ToLower()) { return $true }
    }
    $ext = [System.IO.Path]::GetExtension($relative).ToLower()
    return ($EXCLUDE_EXT -contains $ext)
}

# ── 3. 拷贝 evidence/ 与 state/ ──────────────────────────────────────
# 配额：截图单张 1~5MB，攒几轮就能顶穿 GitHub issue 附件 25MB 上限，用户
# 上传失败 = 收不到现场 = 白跑一趟。所以按「时间倒序 + 总预算」只取最近的，
# 并在收集信息.txt 里写明丢了多少张 —— 截图为定位佐证，不是主体。
$MAX_PNG      = 5
$MAX_PNG_MB   = 12

$copied = 0
$skipped = @()
$pngKept = 0
$pngBudget = [int]($MAX_PNG_MB * 1MB)
$pngSpent = 0
$pngDropped = 0
$candidates = @()

foreach ($sub in @('evidence', 'state')) {
    $srcRoot = Join-Path $Root $sub
    if (-not (Test-Path -LiteralPath $srcRoot)) {
        Write-Host "  [跳过] 没有 $sub 目录（程序可能还没运行过）" -ForegroundColor Yellow
        continue
    }
    Get-ChildItem -LiteralPath $srcRoot -Recurse -File -Force | ForEach-Object {
        $rel = $_.FullName.Substring($Root.Length).TrimStart('\', '/')
        if (Test-Excluded $rel) {
            $script:skipped += $rel
            return
        }
        if ($_.Extension.ToLower() -eq '.png') {
            # 截图：先进候选池，按 mtime 倒序择优
            $script:candidates += [pscustomobject]@{
                FullName = $_.FullName; Rel = $rel
                MTime = $_.LastWriteTimeUtc; Size = $_.Length
            }
            return
        }
        $dest = Join-Path $stage $rel
        $destDir = Split-Path -Parent $dest
        if (-not (Test-Path -LiteralPath $destDir)) {
            New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        }
        Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
        $script:copied++
    }
}

# 遗留：旧版本把截图硬编码写到 <盘符>:\tmp\diag_*.png。也捞回来（尽力而为）。
try {
    $legacy = Join-Path ([System.IO.Path]::GetPathRoot($Root)) 'tmp'
    if (Test-Path -LiteralPath $legacy) {
        Get-ChildItem -LiteralPath $legacy -Filter 'diag_*.png' -File -ErrorAction SilentlyContinue |
            ForEach-Object {
                $script:candidates += [pscustomobject]@{
                    FullName = $_.FullName; Rel = "evidence/legacy_$($_.Name)"
                    MTime = $_.LastWriteTimeUtc; Size = $_.Length
                }
            }
    }
} catch { }

foreach ($c in ($candidates | Sort-Object MTime -Descending)) {
    if ($pngKept -ge $MAX_PNG -or ($pngSpent + $c.Size) -gt $pngBudget) {
        $pngDropped++
        continue
    }
    $dest = Join-Path $stage $c.Rel
    $destDir = Split-Path -Parent $dest
    if (-not (Test-Path -LiteralPath $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    Copy-Item -LiteralPath $c.FullName -Destination $dest -Force
    $pngKept++
    $pngSpent += $c.Size
    $copied++
}

Write-Host "  已收集 $copied 个文件（含 $pngKept 张截图）" -ForegroundColor Green
if ($pngDropped -gt 0) {
    Write-Host "  另有 $pngDropped 张更早的截图未收录（超出配额，保留最近的 $MAX_PNG 张 / $MAX_PNG_MB MB）" -ForegroundColor DarkYellow
}

# ── 4. 脱敏（仅文本文件；png 等二进制跳过）──────────────────────────
$TEXT_EXT = @('.json', '.log', '.txt', '.md', '.tmp')
$patterns = @(
    @{ p = '(enc=)[^&\s"'']+';      r = '$1<REDACTED>' },
    @{ p = '((?:ak|at|ad)_=)[^&\s"'']+'; r = '$1<REDACTED>' },
    @{ p = '(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)'; r = '$1****$2' }
)
$redacted = 0
Get-ChildItem -LiteralPath $stage -Recurse -File -Force | ForEach-Object {
    if ($TEXT_EXT -notcontains $_.Extension.ToLower()) { return }
    try {
        $content = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 -ErrorAction Stop
        $new = $content
        foreach ($rule in $patterns) { $new = [regex]::Replace($new, $rule.p, $rule.r) }
        if ($new -ne $content) {
            [System.IO.File]::WriteAllText($_.FullName, $new, (New-Object System.Text.UTF8Encoding $false))
            $script:redacted++
        }
    } catch { }
}
Write-Host "  已脱敏 $redacted 个文件（手机号/enc/CDN 令牌）" -ForegroundColor Green

# ── 5. 附一份环境与版本信息 ──────────────────────────────────────────
$versionFile = Join-Path $Root 'VERSION'
$appVersion = '未知'
if (Test-Path -LiteralPath $versionFile) {
    $appVersion = (Get-Content -LiteralPath $versionFile -TotalCount 1).Trim()
}
$sys = ''
try {
    $sys = (Get-CimInstance Win32_OperatingSystem).Caption
} catch { $sys = [System.Environment]::OSVersion.VersionString }

$info = @()
$info += "收集时间   : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
$info += "程序版本   : $appVersion"
$info += "操作系统   : $sys"
$info += "Python     : $($PSVersionTable.PSVersion)"
$info += "运行根目录 : $Root"
$info += ""
$info += "已收集文件 : $copied"
$info += "  其中截图 : $pngKept 张"
$info += "脱敏文件   : $redacted"
$info += "主动排除   : .env / .cache（凭据与登录态）、*.lock、internal/（560MB 运行时）"
if ($pngDropped -gt 0) {
    $info += "截图配额   : 另有 $($pngDropped) 张更早的截图未收录（单次上限 $MAX_PNG 张 / $MAX_PNG_MB MB，避免超出 issue 附件 25MB 限制）"
}
if ($skipped.Count -gt 0) {
    $info += ""
    $info += "以下文件因含凭据或体积原因未收录："
    $skipped | Select-Object -First 20 | ForEach-Object { $info += "  - $_" }
}
$info += ""
$info += "如何提交：把生成的 zip 作为附件发到 issue 即可（已是脱敏后的纯文本+截图）。"
[System.IO.File]::WriteAllLines(
    (Join-Path $stage '收集信息.txt'), $info,
    (New-Object System.Text.UTF8Encoding $true))

# ── 6. 打包 ──────────────────────────────────────────────────────────
$zip = Join-Path $Root "故障信息-$stamp.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
try {
    Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -Force -ErrorAction Stop
} catch {
    Write-Host "  [错误] 打包失败：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host '  暂存目录保留在：'"$stage" -ForegroundColor Yellow
    exit 1
}
Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue

$sizeMb = [math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 2)
Write-Host ''
Write-Host "  完成：$zip  ($sizeMb MB)" -ForegroundColor Green
Write-Host '  请把这个 zip 作为附件发到 issue。' -ForegroundColor Cyan
Write-Host ''