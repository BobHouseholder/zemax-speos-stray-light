# zos-guard.ps1 -- fail-loud assertions for the ZOS-API side of the workflow.
# Dot-source it:  . "$PSScriptRoot\zos-guard.ps1"
#
# NOTE: pure ASCII on purpose. PowerShell 5.1 reads .ps1 as ANSI unless the
# file has a BOM, so a UTF-8 em-dash becomes mojibake that TERMINATES STRINGS
# and produces a wall of bogus parse errors. Keep these files ASCII-only.
#
# Every function here exists because the corresponding failure happened
# SILENTLY during development and produced confident wrong output. That is the
# dangerous class: on a familiar system I notice, on a customer's I would not.

$script:GuardFailures = New-Object System.Collections.ArrayList

function Guard-Fail {
    param([string]$Check, [string]$Detail)
    [void]$script:GuardFailures.Add("$Check : $Detail")
    throw "GUARD FAILED [$Check] $Detail"
}

function Guard-Warn {
    param([string]$Check, [string]$Detail)
    Write-Output "GUARD WARN [$Check] $Detail"
}

# --- 1. Operand type actually took.
# [EnumType]::$var yields $null in PowerShell -> ChangeType() gets nothing,
# returns nothing, and the row stays BLNK with value NaN. NO ERROR IS RAISED.
function Assert-OperandType {
    param($Row, [string]$Expected, $ChangeTypeResult = $null)
    if ($null -ne $ChangeTypeResult -and -not $ChangeTypeResult) {
        Guard-Fail "operand-type" "ChangeType($Expected) returned false"
    }
    $actual = "$($Row.TypeName)".Trim()
    if ($actual -ne $Expected) {
        Guard-Fail "operand-type" "asked for '$Expected' but row is '$actual' (BLNK means the enum reference was null; use [Enum]::Parse)"
    }
}

# --- 2. A merit-operand value is real.
function Assert-OperandValue {
    param([double]$Value, [string]$What, [switch]$AllowZero)
    if ([double]::IsNaN($Value)) { Guard-Fail "operand-value" "$What is NaN (operand did not evaluate)" }
    if ([double]::IsInfinity($Value)) { Guard-Fail "operand-value" "$What is Infinity" }
    if (-not $AllowZero -and $Value -eq 0) {
        Guard-Warn "operand-value" "$What is exactly 0 - check the parameter convention (e.g. GPIM needs Surf1 > Surf2)"
    }
}

# --- 3. A ray trace actually returned rays.
# My hand-rolled RMS returned -1 when the trace produced nothing, so a whole
# optimization ran with ZERO image-quality verification.
function Assert-RaysTraced {
    param([int]$Count, [int]$Expected, [string]$What)
    if ($Count -le 0) { Guard-Fail "ray-trace" "$What returned NO rays (expected $Expected)" }
    if ($Count -lt [math]::Ceiling($Expected * 0.5)) {
        Guard-Fail "ray-trace" "$What returned only $Count of $Expected rays (more than half lost - vignetting or bad surface index?)"
    }
    if ($Count -lt $Expected) { Guard-Warn "ray-trace" "$What returned $Count of $Expected rays" }
}

# --- 4. A hashtable/array serialized with real values.
# PowerShell variables are CASE-INSENSITIVE: a `$R` hashtable is destroyed by a
# later `$r = ...` in scope, and every value serializes EMPTY with no error.
function Assert-JsonComplete {
    param([string]$Json, [string]$What)
    if ($Json -match '":\s*,' -or $Json -match '":\s*\}' -or $Json -match '":\s*\]') {
        Guard-Fail "json-complete" "$What contains EMPTY values - a variable was clobbered (check names differing only by case)"
    }
    if ($Json -match 'System\.Object|System\.Collections') {
        Guard-Fail "json-complete" "$What contains a stringified .NET object instead of a value"
    }
}

# --- 5. A file was actually produced, with content.
function Assert-FileProduced {
    param([string]$Path, [string]$What, [int]$MinBytes = 32)
    if (-not (Test-Path $Path)) { Guard-Fail "file-produced" "$What did not create $Path" }
    $len = (Get-Item $Path).Length
    if ($len -lt $MinBytes) { Guard-Fail "file-produced" "$What wrote only $len bytes to $Path" }
}

# --- 6. The single OpticStudio licence seat is free.
# zemax-mcp holds the SAME seat; an active server makes CreateNewApplication
# block forever with no error and no OpticStudio process (the absence is the
# diagnostic). A killed run also LEAKS the seat.
function Assert-SeatAvailable {
    param([switch]$Fix)
    $holders = @(Get-Process -Name "ZemaxMCP.Server" -ErrorAction SilentlyContinue)
    if ($holders.Count -gt 0) {
        if ($Fix) {
            Write-Output "GUARD FIX [licence-seat] stopping $($holders.Count) ZemaxMCP.Server process(es)"
            $holders | Stop-Process -Force
            Start-Sleep -Seconds 2
        } else {
            Guard-Fail "licence-seat" "$($holders.Count) ZemaxMCP.Server process(es) hold the seat - stop them or pass -Fix"
        }
    }
    $help = @(Get-Process -Name "ANSYSHelpViewer" -ErrorAction SilentlyContinue)
    if ($help.Count -gt 0 -and $Fix) { $help | Stop-Process -Force }
    $stale = @(Get-Process | Where-Object { $_.ProcessName -like "*OpticStudio*" })
    if ($stale.Count -gt 0) {
        Guard-Warn "licence-seat" "$($stale.Count) OpticStudio process(es) already running - a previous run may have leaked a seat"
    }
}

# --- 7. The application connected and is licensed.
function Assert-Connected {
    param($App)
    if ($null -eq $App) { Guard-Fail "connect" "CreateNewApplication returned null (seat unavailable?)" }
    if (-not $App.IsValidLicenseForAPI) { Guard-Fail "connect" "licence is not valid for the ZOS-API" }
}

function Guard-Summary {
    if ($script:GuardFailures.Count -eq 0) { Write-Output "GUARDS: all passed" }
    else { Write-Output "GUARDS: $($script:GuardFailures.Count) FAILED"; $script:GuardFailures | ForEach-Object { Write-Output "  $_" } }
}
