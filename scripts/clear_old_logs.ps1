<#
.SYNOPSIS
    Xoa file log qua han cua milestone-livestream. Chay hang ngay luc 00:00 qua Task Scheduler.

.DESCRIPTION
    Quet 3 nhom log:
      - <project>\logs\*.log            (app-<ts>.out.log, app-<ts>.err.log, supervisor.log...)
      - <HLS_ROOT>\*.log               (thumbnail_refresh.log)
      - <HLS_ROOT>\<camera_id>\logs\*.log (ffmpeg_stderr.log cua tung camera)

    HLS_ROOT doc tu .env cua chinh project nay, nen 2 instance tren cung server
    (hld / cgnb) moi cai chay task rieng va khong xoa log cua nhau.

    File dang bi process giu handle (ffmpeg dang chay, supervisor dang ghi) se bi
    bo qua va ghi vao log ket qua, khong lam task fail.

.EXAMPLE
    .\scripts\clear_old_logs.ps1 -Install
    Dang ky scheduled task chay 00:00 hang ngay (can chay PowerShell as Administrator).

.EXAMPLE
    .\scripts\clear_old_logs.ps1 -Days 3 -WhatIf
    Chay thu, chi in ra file nao se bi xoa.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateRange(1, 3650)]
    [int]$Days = 3,

    # Rotate file log dang mo khi vuot nguong (MB) de no gia dan roi bi xoa theo -Days.
    # Dat 0 de tat rotate.
    [ValidateRange(0, 10240)]
    [int]$RotateOverMB = 20,

    # Thu muc log bo sung ngoai cac thu muc mac dinh.
    [string[]]$ExtraPath = @(),

    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Status
)

$ErrorActionPreference = "Stop"

$ScriptFile = $PSCommandPath
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ProjectName = Split-Path -Leaf $ProjectRoot
$EnvFile = Join-Path $ProjectRoot ".env"
$LogsDir = Join-Path $ProjectRoot "logs"
$JobLogFile = Join-Path $LogsDir "clear_old_logs.log"
$TaskName = "MilestoneLivestream-ClearLogs-$ProjectName"

function Write-JobLog {
    param([string]$Message)

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$timestamp] $Message"

    Write-Host $line

    try {
        if (-not (Test-Path -LiteralPath $LogsDir)) {
            New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
        }
        Add-Content -LiteralPath $JobLogFile -Value $line -Encoding UTF8
    } catch {
        # Khong ghi duoc log cua chinh job thi van tiep tuc xoa log.
    }
}

function Get-EnvValue {
    param(
        [string]$Key,
        [string]$Default
    )

    if (-not (Test-Path -LiteralPath $EnvFile)) {
        return $Default
    }

    foreach ($line in (Get-Content -LiteralPath $EnvFile -ErrorAction SilentlyContinue)) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }

        $parts = $trimmed.Split("=", 2)
        if ($parts[0].Trim() -eq $Key) {
            return $parts[1].Trim().Trim('"').Trim("'")
        }
    }

    return $Default
}

function Get-TargetDirectories {
    $dirs = New-Object System.Collections.Generic.List[string]
    $dirs.Add($LogsDir)

    $hlsRoot = Get-EnvValue -Key "HLS_ROOT" -Default "C:\hls"
    if (-not [string]::IsNullOrWhiteSpace($hlsRoot)) {
        $dirs.Add($hlsRoot)
    }

    foreach ($extra in $ExtraPath) {
        if (-not [string]::IsNullOrWhiteSpace($extra)) {
            $dirs.Add($extra)
        }
    }

    # Bo trung lap va bo thu muc khong ton tai
    $seen = @{}
    $result = New-Object System.Collections.Generic.List[string]
    foreach ($dir in $dirs) {
        $full = $null
        try {
            $full = (Resolve-Path -LiteralPath $dir -ErrorAction Stop).Path
        } catch {
            Write-JobLog "Bo qua thu muc khong ton tai: $dir"
            continue
        }

        $key = $full.ToLowerInvariant()
        if (-not $seen.ContainsKey($key)) {
            $seen[$key] = $true
            $result.Add($full)
        }
    }

    return [string[]]$result
}

function Get-LogFiles {
    param([string]$Directory)

    return Get-ChildItem -LiteralPath $Directory -Recurse -File -Filter "*.log" -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -eq ".log" }
}

function Invoke-RotateLargeLogs {
    param(
        [string[]]$Directories,
        [datetime]$Cutoff
    )

    if ($RotateOverMB -le 0) {
        return
    }

    $limitBytes = [int64]$RotateOverMB * 1MB
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $rotated = 0

    foreach ($dir in $Directories) {
        foreach ($file in (Get-LogFiles -Directory $dir)) {
            if ($file.Length -lt $limitBytes) {
                continue
            }

            # File da qua han thi de pha xoa xu ly, khong rotate vo ich.
            if ($file.LastWriteTime -lt $Cutoff) {
                continue
            }

            # File dang duoc append lien tuc (supervisor.log, ffmpeg_stderr.log) khong bao gio
            # "qua 3 ngay" theo LastWriteTime, nen phai doi ten de no bat dau gia di.
            $rotatedName = "$($file.BaseName)-$stamp$($file.Extension)"

            if (-not $PSCmdlet.ShouldProcess($file.FullName, "Rotate -> $rotatedName")) {
                continue
            }

            try {
                Rename-Item -LiteralPath $file.FullName -NewName $rotatedName -ErrorAction Stop
                $rotated++
                Write-JobLog "ROTATE $($file.FullName) ($([math]::Round($file.Length / 1MB, 1)) MB) -> $rotatedName"
            } catch {
                Write-JobLog "SKIP ROTATE (file dang bi process giu) $($file.FullName): $($_.Exception.Message)"
            }
        }
    }

    if ($rotated -gt 0) {
        Write-JobLog "Da rotate $rotated file vuot $RotateOverMB MB."
    }
}

function Invoke-ClearOldLogs {
    # @() bat buoc ket qua la array ke ca khi chi co 1 thu muc.
    $directories = @(Get-TargetDirectories)
    if ($directories.Count -eq 0) {
        Write-JobLog "Khong co thu muc log nao de quet. Dung."
        return
    }

    Write-JobLog "=== Bat dau: giu log <= $Days ngay | thu muc: $($directories -join ' ; ')"

    $cutoff = (Get-Date).AddDays(-$Days)

    Invoke-RotateLargeLogs -Directories $directories -Cutoff $cutoff

    $deleted = 0
    $skipped = 0
    $freedBytes = [int64]0

    foreach ($dir in $directories) {
        foreach ($file in (Get-LogFiles -Directory $dir)) {
            if ($file.LastWriteTime -ge $cutoff) {
                continue
            }

            # Khong tu xoa file log cua chinh job nay trong luc dang ghi vao no.
            if ($file.FullName -eq $JobLogFile) {
                continue
            }

            if (-not $PSCmdlet.ShouldProcess($file.FullName, "Remove")) {
                continue
            }

            $size = $file.Length
            try {
                Remove-Item -LiteralPath $file.FullName -Force -ErrorAction Stop
                $deleted++
                $freedBytes += $size
            } catch {
                $skipped++
                Write-JobLog "SKIP DELETE (dang bi process giu hoac thieu quyen) $($file.FullName): $($_.Exception.Message)"
            }
        }
    }

    $freedMB = [math]::Round($freedBytes / 1MB, 2)
    Write-JobLog "=== Xong: da xoa $deleted file ($freedMB MB), bo qua $skipped file."
}

function Install-ClearLogsTask {
    if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
        throw "PowerShell nay khong co Register-ScheduledTask. Dung lenh schtasks trong README thay the."
    }

    $psExe = Join-Path $PSHOME "powershell.exe"
    if (-not (Test-Path -LiteralPath $psExe)) {
        $psExe = "powershell.exe"
    }

    $argument = "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptFile`" -Days $Days -RotateOverMB $RotateOverMB"

    $action = New-ScheduledTaskAction -Execute $psExe -Argument $argument -WorkingDirectory $ProjectRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date -Hour 0 -Minute 0 -Second 0)
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Xoa log milestone-livestream qua $Days ngay ($ProjectName)" -Force | Out-Null

    Write-JobLog "Da dang ky scheduled task '$TaskName' chay 00:00 hang ngay (-Days $Days)."
    Write-Host ""
    Write-Host "Kiem tra: .\scripts\clear_old_logs.ps1 -Status"
    Write-Host "Chay thu ngay: Start-ScheduledTask -TaskName '$TaskName'"
}

function Uninstall-ClearLogsTask {
    if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) {
        throw "PowerShell nay khong co module ScheduledTasks. Dung: schtasks /Delete /TN `"$TaskName`" /F"
    }

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) {
        Write-Host "Khong co task '$TaskName'."
        return
    }

    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-JobLog "Da xoa scheduled task '$TaskName'."
}

function Show-ClearLogsTaskStatus {
    if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) {
        Write-Host "PowerShell nay khong co module ScheduledTasks. Dung: schtasks /Query /TN `"$TaskName`""
        return
    }

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) {
        Write-Host "Task '$TaskName': chua dang ky."
        return
    }

    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "Task        : $TaskName"
    Write-Host "State       : $($existing.State)"
    Write-Host "Next run    : $($info.NextRunTime)"
    Write-Host "Last run    : $($info.LastRunTime)"
    Write-Host "Last result : $($info.LastTaskResult)  (0 = OK)"
    Write-Host "Log ket qua : $JobLogFile"
}

if ($Install) {
    Install-ClearLogsTask
} elseif ($Uninstall) {
    Uninstall-ClearLogsTask
} elseif ($Status) {
    Show-ClearLogsTaskStatus
} else {
    Invoke-ClearOldLogs
}
