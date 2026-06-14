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

function Remove-FileIfExists {
    param([string]$Path)

    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Force
    }
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
    $importCheck = "import fastapi, pyodbc, uvicorn, dotenv, pydantic_settings; import app.main"
    $importOutput = & $pythonExe "-c" $importCheck 2>&1
    $importExitCode = $LASTEXITCODE
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
        $supervisorAlive = Test-ProcessAlive -ProcessId $supervisorPid
    }

    if ($workerPid) {
        $workerAlive = Test-ProcessAlive -ProcessId $workerPid
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
        Write-Host "App dang chay. Supervisor PID: $($status.SupervisorPid), Worker PID: $($status.WorkerPid)"
        return
    }

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
    Write-Host "Da start app nen. Supervisor PID: $($process.Id)"
}

function Stop-Supervisor {
    $status = Get-StatusObject

    if ($status.WorkerAlive) {
        try {
            Stop-Process -Id $status.WorkerPid -Force -ErrorAction Stop
            Write-SupervisorLog "Stopped worker PID $($status.WorkerPid)."
        } catch {
            Write-SupervisorLog "Worker PID $($status.WorkerPid) could not be stopped cleanly: $($_.Exception.Message)"
        }
    }

    if ($status.SupervisorAlive) {
        try {
            Stop-Process -Id $status.SupervisorPid -Force -ErrorAction Stop
            Write-SupervisorLog "Stopped supervisor PID $($status.SupervisorPid)."
        } catch {
            Write-SupervisorLog "Supervisor PID $($status.SupervisorPid) could not be stopped cleanly: $($_.Exception.Message)"
        }
    }

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
            $worker = Start-Process -FilePath $pythonExe -ArgumentList "`"$RunPy`"" -WorkingDirectory $ProjectRoot -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
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
