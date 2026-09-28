param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('Setup', 'Start', 'Stop', 'Models')][string]$Action,
    [switch]$PrepareModels,
    [switch]$NoShortcut,
    [switch]$NoBrowser,
    [ValidateRange(1024,65535)][int]$Port = 8000
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$installation = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$appDirectory = if ($env:CASEFILE_APP_HOME) { [IO.Path]::GetFullPath($env:CASEFILE_APP_HOME) } else { Join-Path $env:LOCALAPPDATA 'CaseFileAI' }
$logDirectory = Join-Path $appDirectory 'logs'
$runtimeDirectory = Join-Path $appDirectory 'runtime'
$pythonExecutable = Join-Path $installation '.runtime\Scripts\python.exe'
$env:CASEFILE_PRODUCT_MODE = '1'
$env:CASEFILE_INSTALL_DIR = $installation
$env:CASEFILE_APP_HOME = $appDirectory
if (-not $env:CASEFILE_ENV_FILE) { $env:CASEFILE_ENV_FILE = Join-Path $appDirectory 'config.env' }
if (-not [IO.Path]::IsPathRooted($env:CASEFILE_ENV_FILE)) {
    $env:CASEFILE_ENV_FILE = [IO.Path]::GetFullPath((Join-Path $appDirectory $env:CASEFILE_ENV_FILE))
}
$env:PYTHONUTF8 = '1'
$stage = 'directories'
$setupLock = $null

function Write-SafeStage([string]$Result) {
    # This function never accepts exception messages or native command output.
    $entry = [ordered]@{time=[DateTime]::UtcNow.ToString('o'); stage=$stage; result=$Result; pid=0; port=$Port}
    $path = Join-Path $logDirectory 'launcher.log'
    if ((Test-Path -LiteralPath $path) -and (Get-Item -LiteralPath $path).Length -gt 2MB) {
        for ($number=3; $number -ge 1; $number--) {
            $old = if ($number -eq 1) { $path } else { "$path.$($number - 1)" }
            $next = "$path.$number"
            if (Test-Path -LiteralPath $old) { Move-Item -LiteralPath $old -Destination $next -Force }
        }
    }
    [IO.File]::AppendAllText($path, (($entry | ConvertTo-Json -Compress) + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
}

function Invoke-Uv([string[]]$Arguments) {
    # Windows PowerShell 5 treats ordinary native stderr progress as ErrorRecords.
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $script:uvExecutable @Arguments *> $null
        $uvExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    if ($uvExit -ne 0) { throw 'native_command_failed' }
}

function Invoke-Launcher([string]$Operation) {
    if (-not (Test-Path -LiteralPath $pythonExecutable)) { throw 'environment_missing' }
    $arguments = @('-I', '-X', 'utf8', '-W', 'ignore::SyntaxWarning', '-m', 'casefile_ai.interfaces.windows.launcher', $Operation, '--port', [string]$Port)
    if ($NoBrowser) { $arguments += '--no-browser' }
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $pythonExecutable @arguments 2>$null
        $appExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    if ($appExit -ne 0) { throw 'application_check_failed' }
}

try {
    if ($appDirectory.TrimEnd('\').Equals($installation.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase) -or
        $appDirectory.StartsWith($installation.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'data_in_installation'
    }
    [IO.Directory]::CreateDirectory($logDirectory) | Out-Null
    [IO.Directory]::CreateDirectory($runtimeDirectory) | Out-Null
    if ($Action -in @('Setup', 'Start') -and
        ((Test-Path -LiteralPath (Join-Path $installation '.env')) -or
         (Test-Path -LiteralPath (Join-Path $installation 'data\casefile-ai.db')))) {
        Write-Host '检测到旧开发配置或数据。本入口使用用户 config.env，不自动复制凭据或切换旧案件；请按 Windows 说明先备份，再恢复到用户目录。'
    }
    if ($Action -eq 'Setup') {
        $stage = 'setup_lock'
        # Also excludes concurrent Python launcher start/stop using this same lock file.
        $setupLock = [IO.File]::Open((Join-Path $runtimeDirectory 'launcher.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $record = Join-Path $runtimeDirectory 'instance.json'
        if (Test-Path -LiteralPath $record) {
            $instance = Get-Content -LiteralPath $record -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($instance.pid -gt 0 -and (Get-Process -Id $instance.pid -ErrorAction SilentlyContinue)) {
                throw 'running_instance'
            }
        }
        try {
            $identity = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/system/identity" -TimeoutSec 1
        } catch { $identity = $null }
        if ($identity -and $identity.application -eq 'casefile-ai') { throw 'running_instance' }
        $stage = 'uv'
        Write-Host '正在检查 uv……'
        $versions = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'windows-runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $command = Get-Command uv.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        $script:uvExecutable = if ($command) { $command.Source } else { $null }
        foreach ($candidate in @((Join-Path $installation '.tools\uv.exe'), (Join-Path $env:USERPROFILE '.local\bin\uv.exe'))) {
            if (Test-Path -LiteralPath $candidate) { $script:uvExecutable = $candidate; break }
        }
        if (-not $script:uvExecutable) {
            Write-Host "未找到 uv。请从官方固定版本页面下载 Windows x64 ZIP，将 uv.exe 解压到程序目录的 .tools 文件夹，再重新双击 Setup。"
            Write-Host "https://github.com/astral-sh/uv/releases/tag/$($versions.uv)"
            throw 'uv_missing'
        }
        $foundVersion = & $script:uvExecutable --version 2>$null
        if ($LASTEXITCODE -ne 0 -or $foundVersion -notmatch ('^uv ' + [regex]::Escape($versions.uv) + '(\s|$)')) {
            Write-Host "需要已验收的 uv $($versions.uv)，不会静默升级。请从官方版本页面取得该版本放入 .tools\uv.exe。"
            throw 'uv_version'
        }
        Write-SafeStage 'ok'
        $stage = 'python'
        Write-Host "正在准备 Python $($versions.python)……"
        Invoke-Uv -Arguments @('--no-config', 'python', 'install', [string]$versions.python)
        Write-SafeStage 'ok'
        $stage = 'dependencies'
        Write-Host '正在安装锁定的 core、OCR 和 embedding 依赖（首次下载较大，请等待）……'
        $env:UV_PROJECT_ENVIRONMENT = Join-Path $installation '.runtime'
        Invoke-Uv -Arguments @('--no-config', 'sync', '--project', $installation, '--locked', '--no-dev', '--group', 'ocr', '--group', 'embeddings', '--no-editable', '--reinstall-package', 'casefile-ai', '--python', [string]$versions.python, '--managed-python')
        Write-SafeStage 'ok'
        $stage = 'configuration'
        if (-not (Test-Path -LiteralPath $env:CASEFILE_ENV_FILE)) {
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($env:CASEFILE_ENV_FILE)) | Out-Null
            [IO.File]::Copy((Join-Path $installation 'config.env.example'), $env:CASEFILE_ENV_FILE, $false)
        }
        Write-SafeStage 'ok'
        $stage = 'self_check'
        Invoke-Launcher 'setup-check'
        Write-SafeStage 'ok'
        # Release before optional model preparation acquires the Python launcher lock.
        $setupLock.Dispose(); $setupLock = $null
        if ($PrepareModels) { $stage = 'models'; Invoke-Launcher 'prepare-models' }
        if (-not $NoShortcut) {
            $stage = 'shortcuts'
            try {
                $desktop = [Environment]::GetFolderPath('Desktop')
                $shell = New-Object -ComObject WScript.Shell
                foreach ($name in @('Start CaseFile AI', 'Stop CaseFile AI')) {
                    $link = $shell.CreateShortcut((Join-Path $desktop "$name.lnk"))
                    $target = Join-Path $installation "$name.cmd"
                    if ($link.TargetPath -and $link.TargetPath -ne $target -and
                        [IO.Path]::GetFileName($link.TargetPath) -ne "$name.cmd") { continue }
                    $link.TargetPath = $target
                    $link.WorkingDirectory = $installation
                    $link.Description = 'CaseFile AI 本地卷宗分析'
                    $link.Save()
                }
                Write-SafeStage 'ok'
            } catch { Write-Host '桌面快捷方式未能创建，可直接双击程序目录中的 Start / Stop。'; Write-SafeStage 'warning' }
        }
        Write-Host "Setup 完成。配置：$env:CASEFILE_ENV_FILE"
        Write-Host '首次使用请双击 Prepare Models CaseFile AI.cmd；以后双击 Start，结束后双击 Stop。'
    } else {
        $stage = $Action.ToLowerInvariant()
        $operation = @{Start='start'; Stop='stop'; Models='prepare-models'}[$Action]
        Invoke-Launcher $operation
    }
    exit 0
} catch {
    try { Write-SafeStage 'failed' } catch { }
    $known = [string]$_.Exception.Message
    $message = switch ($known) {
        'environment_missing' { '运行环境未准备，请先双击 Setup CaseFile AI.cmd。' }
        'data_in_installation' { '用户数据目录不能位于程序目录中。' }
        'running_instance' { 'CaseFile AI 仍在运行，请先停止，再重新运行 Setup。' }
        'uv_missing' { '未安装 uv，请按上述官方固定版本下载流程准备。' }
        'uv_version' { 'uv 版本不符合交付要求。' }
        'application_check_failed' { '应用操作未完成，请按上方提示处理；如无提示，请重新 Setup，并按 Windows 说明检查本地运行依赖。' }
        default { '请检查网络、目录权限、空间或是否有其他安装/启动操作正在进行。' }
    }
    Write-Host "CaseFile AI 操作失败。阶段：$stage。$message"
    Write-Host "安全日志目录：$logDirectory"
    exit 1
} finally {
    if ($setupLock) { $setupLock.Dispose() }
}
