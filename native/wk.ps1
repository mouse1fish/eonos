# wk.ps1 —— EonOS 构建驱动（Hare `hare` 驱动的 PowerShell 版）
# 封装 Whisker 工具链的 lex_input/wkcc2 约定，本驱动不含编译逻辑。
#
# 用法（示例均在 eonos/native/ 下）：
#   powershell -File wk.ps1 build  httpd.wk                 # 编译 → .\httpd.exe
#   powershell -File wk.ps1 build  httpd.wk -o my.exe       # 指定输出
#   powershell -File wk.ps1 run    std\strbuf_test.wk std\strbuf.inc.wk
#                # 后面的 .inc.wk 会拼接进第一个 actor（def 片段机制）
#   powershell -File wk.ps1 inc    httpd.wk std\strbuf.inc.wk -o httpd_sb.exe
#                # 拼接式构建（inc 的 def 进第一个 actor）
#
# 依赖：trea-xiancheng-bianyiqi\build\wkcc2.exe（自举链）
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Verb,
    [Parameter(Mandatory = $true, Position = 1)]
    [string]$Main,
    [Parameter(Position = 2, ValueFromRemainingArguments = $true)]
    [string[]]$Rest,
    [string]$o
)
$ErrorActionPreference = "Stop"
$wkTool = "G:\trea-Eon-os\trea-xiancheng-bianyiqi"
$wkcc2 = Join-Path $wkTool "build\wkcc2.exe"
Set-Location $PSScriptRoot

if (-not (Test-Path $wkcc2)) { Write-Error "wkcc2.exe 不存在（先建立自举链）"; exit 1 }
if (-not (Test-Path $Main)) { Write-Error "找不到 $Main"; exit 1 }

# 组装源：Main + 可选的 .inc 拼接（inc 内容注入第一个 actor 的 { 之后）
$src = Get-Content $Main -Encoding UTF8 -Raw
foreach ($inc in $Rest) {
    if ($inc -eq "-o") { break }
    if (-not (Test-Path $inc)) { Write-Error "找不到片段 $inc"; exit 1 }
    if ($inc -notlike "*.inc.wk") { continue }
    $frag = Get-Content $inc -Encoding UTF8 -Raw
    $src = $src -replace '(?m)^(actor\s+\w+\s*\{)', ('$1' + "`n" + $frag)
    Write-Host "[wk] 已拼接片段: $inc"
}

Copy-Item $Main "$wkTool\build\lex_input.txt" -Force
# 有拼接时以拼接结果覆盖 lex_input（编码统一 UTF8）
$combined = Join-Path $env:TEMP ("wk_" + [guid]::NewGuid().ToString("N") + ".wk")
$src | Set-Content -Path $combined -Encoding UTF8
Copy-Item $combined "$wkTool\build\lex_input.txt" -Force
Remove-Item $combined -Force

Push-Location $wkTool
$oldSelf = Join-Path $wkTool "build\selfhost.exe"
if (Test-Path $oldSelf) { Remove-Item $oldSelf -Force }
& .\build\wkcc2.exe | Out-Host
$rc = $LASTEXITCODE
if ($rc -ne 0) {
    Start-Sleep -Milliseconds 300
    & .\build\wkcc2.exe | Out-Host
    $rc = $LASTEXITCODE
}
Pop-Location
if ($rc -ne 0 -or -not (Test-Path $oldSelf)) { Write-Error "编译失败"; exit 1 }

switch ($Verb) {
    "build" {
        $out = if ($o) { $o } else { [IO.Path]::ChangeExtension($Main, ".exe") }
        Copy-Item $oldSelf $out -Force
        Write-Host "[wk] OK: $out"
    }
    "inc" {
        $out = if ($o) { $o } else { [IO.Path]::ChangeExtension($Main, ".exe") }
        Copy-Item $oldSelf $out -Force
        Write-Host "[wk] OK(拼接): $out"
    }
    "run" {
        & $oldSelf
        exit $LASTEXITCODE
    }
    "test" {
        & $oldSelf
        if ($LASTEXITCODE -ne 0) { Write-Error "测试失败"; exit 1 }
        Write-Host "[wk] 测试通过"
    }
    default { Write-Error "未知动词: $Verb（可用 build/run/test/inc）"; exit 1 }
}
