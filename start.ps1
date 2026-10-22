param([switch]$Background)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:CODEX_HOME = Join-Path $env:USERPROFILE '.codex'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $pythonPath)) { throw '请先运行 setup.ps1 安装项目依赖。' }
if (!(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'config.yaml'))) { throw '请先按 config.example.yaml 创建 config.yaml。' }
$codexCommand = Get-Command codex.exe -ErrorAction SilentlyContinue
if (!$codexCommand) {
    $codexCommand = Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin') -Filter codex.exe -Recurse -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (!$codexCommand) { throw '未找到 codex.exe，请安装并登录 Codex CLI。' }
    $env:PATH = $codexCommand.DirectoryName + ';' + $env:PATH
}
# Git may be installed separately or supplied by the local Codex runtime.
if (!(Get-Command git.exe -ErrorAction SilentlyContinue)) {
    $gitCandidates = @(
        (Join-Path $env:ProgramFiles 'Git\cmd'),
        (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd')
    )
    $gitDir = $gitCandidates | Where-Object { Test-Path -LiteralPath (Join-Path $_ 'git.exe') } | Select-Object -First 1
    if (!$gitDir) { throw '未找到 Git for Windows。' }
    $env:PATH = $gitDir + ';' + $env:PATH
}
if ($Background) {
    $dataPath = Join-Path $PSScriptRoot 'data'
    New-Item -ItemType Directory -Path $dataPath -Force | Out-Null
    $pidPath = Join-Path $dataPath 'web.pid'
    if (Test-Path -LiteralPath $pidPath) {
        $existing = Get-CimInstance Win32_Process -Filter "ProcessId = $([int](Get-Content -LiteralPath $pidPath))"
        if ($existing -and ($existing.CommandLine -match 'backend\.run' -or
            ($existing.CommandLine -like '*后台启动.py*' -and $existing.CommandLine -like '*codex-web*'))) {
            throw 'Codex Web 已在后台运行。'
        }
    }
    $pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
    $backgroundEntry = Join-Path (Split-Path -Parent $PSScriptRoot) '后台启动.py'
    $backgroundArgs = if (Test-Path -LiteralPath $backgroundEntry) { @(('"'+$backgroundEntry+'"'),'codex-web') } else { @('-m','backend.run') }
    $process = Start-Process -FilePath $pythonPath -ArgumentList $backgroundArgs -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $dataPath 'web.stdout.log') -RedirectStandardError (Join-Path $dataPath 'web.stderr.log')
    Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ascii
    Write-Host "Codex Web 已启动，PID=$($process.Id)。启动结果请查看 data\web.stderr.log。"
} else {
    & $pythonPath -m backend.run
    if ($LASTEXITCODE -ne 0) { throw "Codex Web 退出，代码 $LASTEXITCODE" }
}
