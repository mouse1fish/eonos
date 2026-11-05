# wk.ps1 —— EonOS 构建驱动（Hare `hare` 驱动的 PowerShell 版；wkcc CLI 传参）
#
# 用法（示例均在 eonos/native/ 下）：
#   powershell -File wk.ps1 build  httpd.wk                 # 编译 -> .\httpd.exe
#   powershell -File wk.ps1 run    std\strbuf_test.wk std\strbuf.inc.wk   # 拼接并运行
#   powershell -File wk.ps1 inc    httpd.wk std\strbuf.inc.wk -o httpd.exe
#                # 后面的 .inc.wk 会拼接进第一个 actor（def 片段机制）
#
# 依赖：trea-xiancheng-bianyiqi\build\wkcc2.exe（自举链，CLI 传参）
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
Set-Location $wkTool                    # blob 相对路径需要 CWD = 项目根

if (-not (Test-Path $wkcc2)) { Write-Error "wkcc2.exe 不存在（先建立自举链）"; exit 1 }
if (-not (Test-Path $Main)) { Write-Error "找不到 $Main"; exit 1 }

# 组装源：Main + 可选 .inc 拼接（inc 内容注入第一个 actor 的 { 之后）
$src = Get-Content $Main -Encoding UTF8 -Raw
$hasInc = $false
foreach ($inc in $Rest) {
    if ($inc -eq "-o") { break }
    if (-not (Test-Path $inc)) { Write-Error "找不到片段 $inc"; exit 1 }
    if ($inc -notlike "*.inc.wk") { continue }
    $frag = Get-Content $inc -Encoding UTF8 -Raw
    $src = $src -replace '(?m)^(actor\s+\w+\s*\{)', ('$1' + "`n" + $frag)
    $hasInc = $true
    Write-Host "[wk] 已拼接片段: $inc"
}

$tmp = Join-Path $env:TEMP ("wk_" + [guid]::NewGuid().ToString("N") + ".wk")
$src | Set-Content -Path $tmp -Encoding UTF8
$outExe = if ($o) { Join-Path $PSScriptRoot $o } else { Join-Path $PSScriptRoot ([IO.Path]::ChangeExtension($Main, ".exe")) }

& $wkcc2 $tmp $outExe
$rc = $LASTEXITCODE
if ($rc -ne 0) {
    Start-Sleep -Milliseconds 300
    & $wkcc2 $tmp $outExe
    $rc = $LASTEXITCODE
}
Remove-Item $tmp -Force -ErrorAction SilentlyContinue
if ($rc -ne 0 -or -not (Test-Path $outExe)) { Write-Error "编译失败"; exit 1 }

switch ($Verb) {
    { $_ -in "build", "inc" } {
        if ($o -eq $outExe) { Write-Host "[wk] OK: $outExe" }
        else { Write-Host "[wk] OK$(if($hasInc){'（拼接）'}): $outExe" }
    }
    "run" { & $outExe; exit $LASTEXITCODE }
    "test" {
        & $outExe
        if ($LASTEXITCODE -ne 0) { Write-Error "测试失败"; exit 1 }
        Write-Host "[wk] 测试通过"
    }
    default { Write-Error "未知动词: $Verb（可用 build/run/test/inc）"; exit 1 }
}