$ErrorActionPreference = 'Stop'
$pidPath = Join-Path $PSScriptRoot 'data\web.pid'
if (!(Test-Path -LiteralPath $pidPath)) { Write-Host '没有后台启动记录。'; return }
$webProcessId = [int](Get-Content -LiteralPath $pidPath)
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $webProcessId"
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if ($process) {
    $backgroundEntry=Join-Path (Split-Path -Parent $PSScriptRoot) '后台启动.py'
    $windowlessOwned=$process.Name -eq 'pythonw.exe' -and $process.CommandLine.Contains($backgroundEntry) -and $process.CommandLine -match 'codex-web'
    $standaloneOwned = $process.ExecutablePath -in @($pythonPath, (Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe')) -and $process.CommandLine -match 'backend\.run'
    if (-not $windowlessOwned -and -not $standaloneOwned) {
        throw 'PID 已被其他进程使用，未停止该进程。'
    }
    # venv's Windows python.exe is a launcher with a real Python child.
    & taskkill.exe /PID $webProcessId /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '停止后台进程树失败。' }
}
Remove-Item -LiteralPath $pidPath
Write-Host 'Codex Web 已停止；其 Codex 子进程随 Windows Job 关闭。'
