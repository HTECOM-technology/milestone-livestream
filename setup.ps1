param(
    [ValidateSet("start", "stop", "restart", "status", "supervise")]
    [string]$Action = "start"
)

$ErrorActionPreference = "Stop"

$ScriptPath = $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogsDir = Join-Path $ProjectRoot "logs"
$SupervisorPidFile = Join-Path $LogsDir "supervisor.pid"
$WorkerPidFile = Join-Path $LogsDir "worker.pid"
$SupervisorLog = Join-Path $LogsDir "supervisor.log"
$RunPy = Join-Path $ProjectRoot "run.py"
$EnvFile = Join-Path $ProjectRoot ".env"
$RequirementsFile = Join-Path $ProjectRoot "requirements.txt"
$RestartDelaySeconds = 5

function Ensure-LogsDirectory {
    if (-not (Test-Path -LiteralPath $LogsDir)) {
        New-Item -ItemType Directory -Path $LogsDir | Out-Null
    }
}

function Write-SupervisorLog {
    param([string]$Message)

    Ensure-LogsDirectory
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $SupervisorLog -Value "[$timestamp] $Message"
}

function Read-PidFile {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }

    $rawContent = Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $rawContent) {
        return $null
    }

    $content = $rawContent.Trim()
    if ([string]::IsNullOrWhiteSpace($content)) {
        return $null
    }

    try {
        return [int]$content
    } catch {
        return $null
    }
}

function Test-ProcessAlive {
    param([int]$ProcessId)

    if ($ProcessId -le 0) {
        return $false
    }

    try {
        $null = Get-Process -Id $ProcessId -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Get-ProcessCommandLine {
    param([int]$ProcessId)

    if ($ProcessId -le 0) {
        return $null
    }

    try {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        if ($process -and $process.CommandLine) {
            return [string]$process.CommandLine
        }
    } catch {
        return $null
    }

    return $null
}

function Test-CommandLineContainsPath {
    param(
        [string]$CommandLine,
        [string]$Path
    )

    if ([string]::IsNullOrWhiteSpace($CommandLine) -or [string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }

    return $CommandLine.IndexOf($Path, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Test-SupervisorProcessAlive {
    param([int]$ProcessId)

    if (-not (Test-ProcessAlive -ProcessId $ProcessId)) {
        return $false
    }

    $commandLine = Get-ProcessCommandLine -ProcessId $ProcessId
    return (
        (Test-CommandLineContainsPath -CommandLine $commandLine -Path $ScriptPath) -and
        $commandLine -match '(?i)(?:^|\s)-Action\s+["'']?supervise(?:["'']?(?:\s|$))'
    )
}

function Test-WorkerProcessAlive {
    param([int]$ProcessId)

    if (-not (Test-ProcessAlive -ProcessId $ProcessId)) {
        return $false
    }

    $commandLine = Get-ProcessCommandLine -ProcessId $ProcessId
    return Test-CommandLineContainsPath -CommandLine $commandLine -Path $RunPy
}

function Test-ProjectAppProcess {
    param([int]$ProcessId)

    return (
        (Test-SupervisorProcessAlive -ProcessId $ProcessId) -or
        (Test-WorkerProcessAlive -ProcessId $ProcessId)
    )
}

function Remove-FileIfExists {
    param([string]$Path)

    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Force
    }
}

function Stop-ProcessTree {
    param([int]$ProcessId)

    if ($ProcessId -le 0) {
        return
    }

    try {
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue
        foreach ($child in $children) {
            Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
        }
    } catch {
        Write-SupervisorLog "Could not inspect children for PID $ProcessId`: $($_.Exception.Message)"
    }

    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
        Write-SupervisorLog "Stopped process PID $ProcessId."
    } catch {
        Write-SupervisorLog "Process PID $ProcessId could not be stopped cleanly: $($_.Exception.Message)"
    }
}

function Get-ConfiguredAppPort {
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        return 8000
    }

    $line = Get-Content -LiteralPath $EnvFile -ErrorAction SilentlyContinue |
        Where-Object { $_ -match '^\s*APP_PORT\s*=\s*"?(\d+)' } |
        Select-Object -First 1

    if ($line -and $line -match '^\s*APP_PORT\s*=\s*"?(\d+)') {
        return [int]$Matches[1]
    }

    return 8000
}

function Get-ListeningProcessIdsByPort {
    param([int]$Port)

    try {
        return @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        return @()
    }
}

function Get-ProjectAppProcessIds {
    try {
        $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.ProcessId -ne $PID -and
                $_.CommandLine -and
                (
                    (Test-CommandLineContainsPath -CommandLine $_.CommandLine -Path $RunPy) -or
                    (
                        (Test-CommandLineContainsPath -CommandLine $_.CommandLine -Path $ScriptPath) -and
                        $_.CommandLine -match '(?i)(?:^|\s)-Action\s+["'']?supervise(?:["'']?(?:\s|$))'
                    )
                )
            }

        return @($processes | Select-Object -ExpandProperty ProcessId -Unique)
    } catch {
        return @()
    }
}

function Test-AppPortCanBind {
    param([int]$Port)

    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $Port)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($listener) {
            $listener.Stop()
        }
    }
}

function Clear-StaleAppProcesses {
    $port = Get-ConfiguredAppPort
    $candidatePids = @()
    $candidatePids += Get-ListeningProcessIdsByPort -Port $port
    $candidatePids += Get-ProjectAppProcessIds

    foreach ($candidatePid in ($candidatePids | Where-Object { $_ } | Sort-Object -Unique)) {
        if (
            [int]$candidatePid -ne $PID -and
            (Test-ProjectAppProcess -ProcessId ([int]$candidatePid))
        ) {
            Write-SupervisorLog "Stopping stale app process PID $candidatePid before start/stop cleanup."
            Stop-ProcessTree -ProcessId ([int]$candidatePid)
        }
    }
}

function Assert-AppPortAvailable {
    $port = Get-ConfiguredAppPort

    if (Test-AppPortCanBind -Port $port) {
        return
    }

    $owners = Get-ListeningProcessIdsByPort -Port $port
    if ($owners.Count -gt 0) {
        $ownerText = ($owners | Sort-Object -Unique) -join ", "
        throw "Port $port dang duoc giu boi PID: $ownerText. Hay chay .\setup.ps1 stop roi start lai."
    }

    try {
        $excluded = netsh int ipv4 show excludedportrange protocol=tcp 2>$null
        Write-SupervisorLog "Port $port cannot bind and has no listening owner. IPv4 excluded ranges: $($excluded -join ' | ')"
    } catch {
        Write-SupervisorLog "Port $port cannot bind and excluded port ranges could not be inspected."
    }

    throw "Port $port khong bind duoc nhung khong thay process listen. Co the port bi Windows reserve/excluded. Chay: netsh int ipv4 show excludedportrange protocol=tcp"
}

function Get-PythonExecutable {
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
    }

    throw "Khong tim thay Python. Can cai Python hoac tao .venv tren Windows."
}

function Assert-Prerequisites {
    Ensure-LogsDirectory

    if (-not (Test-Path -LiteralPath $RunPy)) {
        throw "Khong tim thay run.py trong project."
    }

    if (-not (Test-Path -LiteralPath $EnvFile)) {
        throw "Khong tim thay .env. Hay tao file .env truoc khi start."
    }

    if (-not (Test-Path -LiteralPath $RequirementsFile)) {
        throw "Khong tim thay requirements.txt."
    }

    $pythonExe = Get-PythonExecutable
    $importCheck = "import fastapi, uvicorn, dotenv, pydantic_settings, apscheduler, requests, Crypto; import app.main"
    $prevPref = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $importOutput = & $pythonExe "-c" $importCheck 2>&1
    $importExitCode = $LASTEXITCODE
    $ErrorActionPreference = $prevPref
    if ($importExitCode -ne 0) {
        Write-Host "Chi tiet loi import:" -ForegroundColor Yellow
        $importOutput | ForEach-Object { Write-Host $_ }
        throw "Thieu package Python can thiet. Hay chay pip install -r requirements.txt trong moi truong Windows."
    }

    return $pythonExe
}

function Get-StatusObject {
    $supervisorPid = Read-PidFile -Path $SupervisorPidFile
    $workerPid = Read-PidFile -Path $WorkerPidFile
    $supervisorAlive = $false
    $workerAlive = $false

    if ($supervisorPid) {
        $supervisorAlive = Test-SupervisorProcessAlive -ProcessId $supervisorPid
    }

    if ($workerPid) {
        $workerAlive = Test-WorkerProcessAlive -ProcessId $workerPid
    }

    [pscustomobject]@{
        SupervisorPid = $supervisorPid
        SupervisorAlive = $supervisorAlive
        WorkerPid = $workerPid
        WorkerAlive = $workerAlive
    }
}

function Start-Supervisor {
    $pythonExe = Assert-Prerequisites
    $status = Get-StatusObject
    if ($status.SupervisorAlive) {
        $port = Get-ConfiguredAppPort
        Write-Host "App dang chay tren port $port. Supervisor PID: $($status.SupervisorPid), Worker PID: $($status.WorkerPid)"
        return
    }

    Clear-StaleAppProcesses
    Assert-AppPortAvailable

    Remove-FileIfExists -Path $SupervisorPidFile
    Remove-FileIfExists -Path $WorkerPidFile

    $psExe = Join-Path $PSHOME "powershell.exe"
    if (-not (Test-Path -LiteralPath $psExe)) {
        $psExe = "powershell.exe"
    }

    $arguments = @(
        "-NoProfile"
        "-ExecutionPolicy"
        "Bypass"
        "-File"
        "`"$ScriptPath`""
        "-Action"
        "supervise"
    )

    $process = Start-Process -FilePath $psExe -ArgumentList $arguments -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru
    Set-Content -Path $SupervisorPidFile -Value $process.Id
    Write-SupervisorLog "Supervisor started with PID $($process.Id) using Python $pythonExe."
    $port = Get-ConfiguredAppPort
    Write-Host "Da start app nen tren port $port. Supervisor PID: $($process.Id)"
}

function Stop-Supervisor {
    $status = Get-StatusObject

    if ($status.WorkerAlive) {
        Stop-ProcessTree -ProcessId $status.WorkerPid
    }

    if ($status.SupervisorAlive) {
        Stop-ProcessTree -ProcessId $status.SupervisorPid
    }

    Clear-StaleAppProcesses
    Remove-FileIfExists -Path $WorkerPidFile
    Remove-FileIfExists -Path $SupervisorPidFile
    Write-Host "Da stop app."
}

function Show-Status {
    $status = Get-StatusObject
    if ($status.SupervisorAlive) {
        Write-Host "Supervisor PID: $($status.SupervisorPid)"
    } else {
        Write-Host "Supervisor: stopped"
    }

    if ($status.WorkerAlive) {
        Write-Host "Worker PID: $($status.WorkerPid)"
    } else {
        Write-Host "Worker: stopped"
    }
}

function Invoke-SupervisorLoop {
    $pythonExe = Assert-Prerequisites
    Set-Content -Path $SupervisorPidFile -Value $PID
    Write-SupervisorLog "Supervisor loop is active with PID $PID."

    try {
        while ($true) {
            $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
            $stdoutLog = Join-Path $LogsDir "app-$timestamp.out.log"
            $stderrLog = Join-Path $LogsDir "app-$timestamp.err.log"

            Write-SupervisorLog "Starting worker with log files $([IO.Path]::GetFileName($stdoutLog)) and $([IO.Path]::GetFileName($stderrLog))."
            $worker = Start-Process -FilePath $pythonExe -ArgumentList "`"$RunPy`"" -WorkingDirectory $ProjectRoot -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -WindowStyle Hidden -PassThru
            Set-Content -Path $WorkerPidFile -Value $worker.Id
            Write-SupervisorLog "Worker started with PID $($worker.Id)."

            Wait-Process -Id $worker.Id
            $exitCode = $worker.ExitCode
            Remove-FileIfExists -Path $WorkerPidFile
            Write-SupervisorLog "Worker PID $($worker.Id) exited with code $exitCode. Restarting in $RestartDelaySeconds seconds."
            Start-Sleep -Seconds $RestartDelaySeconds
        }
    } finally {
        Remove-FileIfExists -Path $WorkerPidFile
        Remove-FileIfExists -Path $SupervisorPidFile
        Write-SupervisorLog "Supervisor loop stopped."
    }
}

switch ($Action) {
    "start" {
        Start-Supervisor
    }
    "stop" {
        Stop-Supervisor
    }
    "restart" {
        Stop-Supervisor
        Start-Sleep -Seconds 1
        Start-Supervisor
    }
    "status" {
        Show-Status
    }
    "supervise" {
        Invoke-SupervisorLoop
    }
}
