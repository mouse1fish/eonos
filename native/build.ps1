# EonOS 原生组件构建脚本：用 Whisker 自举编译器（wkcc2）编译 httpd.wk
# 前置：G:\trea-Eon-os\trea-xiancheng-bianyiqi\build\wkcc2.exe 存在（自举链已建立）
$ErrorActionPreference = "Stop"
$wk = "G:\trea-Eon-os\trea-xiancheng-bianyiqi"
Set-Location $PSScriptRoot

if (-not (Test-Path "$wk\build\wkcc2.exe")) {
    Write-Error "wkcc2.exe 不存在：$wk\build\wkcc2.exe（先在 Whisker 项目里建立自举链）"
    exit 1
}

Copy-Item httpd.wk "$wk\build\lex_input.txt" -Force
Push-Location $wk
& .\build\wkcc2.exe | Out-Host
$rc = $LASTEXITCODE
if ($rc -ne 0) {
    # Windows 下新 exe 可能被杀毒短暂锁定导致写出 -1，重跑一次
    Start-Sleep -Milliseconds 300
    & .\build\wkcc2.exe | Out-Host
    $rc = $LASTEXITCODE
}
Pop-Location
if ($rc -ne 0) {
    Write-Error "wkcc2 编译失败（exit=$rc）"
    exit 1
}
if (-not (Test-Path "$wk\build\selfhost.exe")) {
    Write-Error "未生成 selfhost.exe"
    exit 1
}
Copy-Item "$wk\build\selfhost.exe" .\httpd.exe -Force
Write-Host "[eonos-native] 已生成 httpd.exe（Whisker 原生，零依赖）"
