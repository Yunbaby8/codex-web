param([string]$Python)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
if (!$Python) {
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundled) { $Python = $bundled }
    else {
        $installed = Get-Command python.exe -ErrorAction SilentlyContinue
        if (!$installed -or $installed.Source -like '*\Microsoft\WindowsApps\*') { throw '请用 -Python 指定 Python 3.12+ 的 python.exe。' }
        $Python = $installed.Source
    }
}
& $Python -c 'import sys; assert sys.version_info >= (3,12), "需要 Python 3.12+"'
if ($LASTEXITCODE -ne 0) { throw 'Python 版本检查失败。' }
& $Python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw '虚拟环境创建失败。' }
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败。' }
if (!(Test-Path -LiteralPath 'config.yaml')) {
    Copy-Item -LiteralPath 'config.example.yaml' -Destination 'config.yaml'
    Write-Host '已生成 config.yaml，请填写密码和允许的项目目录。'
}
Write-Host '安装完成。双击 start.cmd，或运行 start.ps1。'
