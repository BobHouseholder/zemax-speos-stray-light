# settings.ps1 -- the PowerShell half of lib/settings.py. ASCII ONLY.
# (PS 5.1 reads a .ps1 as ANSI when there is no BOM, so non-ASCII corrupts.)
#
# Dot-source it, then use the variables:
#
#     . "$(Split-Path -Parent $PSScriptRoot)\lib\settings.ps1"
#     Add-Type -Path (Join-Path $SL_OPTICSTUDIO "ZOSAPI_NetHelper.dll")
#
# Provides: $SL_ROOT $SL_ANSYS $SL_OPTICSTUDIO $SL_PYTHON $SL_ZEMAX_DATA
#           $SL_SPEOS_BIN $SL_SPEOS_LAUNCHER $SL_ANSYS_PY
#
# Reads the same straylight.toml as settings.py so there is ONE configuration,
# not two that drift. The parser is a regex over `key = "value"` rather than a
# TOML library because PS 5.1 ships no TOML support and the file is flat.
#
# Fails with `throw` on a missing or wrong path, deliberately: a $null path in
# PowerShell does not error, it silently becomes an empty string, and
# `Join-Path $null "ZOSAPI.dll"` yields a relative filename that Add-Type then
# reports as "file not found" with no hint that the CONFIG is what is wrong.

$SL_ROOT = Split-Path -Parent $PSScriptRoot
$SL_CONFIG = if ($env:SL_CONFIG) { $env:SL_CONFIG } else { "$SL_ROOT\straylight.toml" }

if (-not (Test-Path $SL_CONFIG)) {
    throw ("no configuration file at`n    $SL_CONFIG`n" +
           "Copy straylight.toml.example beside it and edit the four paths, " +
           "then verify with:  python lib\settings.py --check")
}

$slCfg = @{}
foreach ($line in (Get-Content $SL_CONFIG)) {
    $m = [regex]::Match($line, '^\s*([A-Za-z_]+)\s*=\s*"([^"]*)"')
    if ($m.Success) { $slCfg[$m.Groups[1].Value] = $m.Groups[2].Value }
}

function Get-SLPath($key, $what, $required) {
    $v = $slCfg[$key]
    $ov = [Environment]::GetEnvironmentVariable("SL_" + $key.ToUpper())
    if ($ov) { $v = $ov }
    if (-not $v) {
        if ($required) {
            throw "$SL_CONFIG : ``$key`` ($what) is required but blank"
        }
        return $null
    }
    $v = $v.Replace("/", "\").TrimEnd("\")
    if (-not (Test-Path $v)) {
        throw ("$SL_CONFIG : ``$key`` ($what) points at a path that does not " +
               "exist on this machine:`n    $v")
    }
    return $v
}

$SL_ANSYS       = Get-SLPath "ansys_root"       "Ansys unified install root"    $true
$SL_OPTICSTUDIO = Get-SLPath "opticstudio_root" "OpticStudio install directory" $false
$SL_PYTHON      = Get-SLPath "python_exe"       "driver CPython interpreter"    $true
$SL_ZEMAX_DATA  = Get-SLPath "zemax_data"       "OpticStudio user-data folder"  $false

# Derived -- the layout inside an Ansys install is fixed.
$SL_SPEOS_BIN      = "$SL_ANSYS\Optical Products\Speos\bin"
$SL_SPEOS_LAUNCHER = "$SL_SPEOS_BIN\AnsysSpeosLauncher.exe"
$SL_ANSYS_PY       = "$SL_ANSYS\commonfiles\CPython\3_10\winx64\Release\python\python.exe"

# Every ZOS-API script wants the same four lines, and getting them wrong is the
# difference between "no licence seat" and "no such file". One place for them.
function Import-ZOSAPI {
    if (-not $SL_OPTICSTUDIO) {
        throw ("this script needs ZOS-API, but ``opticstudio_root`` is blank " +
               "in $SL_CONFIG")
    }
    Add-Type -Path (Join-Path $SL_OPTICSTUDIO "ZOSAPI_NetHelper.dll")
    [void][ZOSAPI_NetHelper.ZOSAPI_Initializer]::Initialize($SL_OPTICSTUDIO)
    [void][System.Reflection.Assembly]::LoadFrom((Join-Path $SL_OPTICSTUDIO "ZOSAPI.dll"))
    [void][System.Reflection.Assembly]::LoadFrom((Join-Path $SL_OPTICSTUDIO "ZOSAPI_Interfaces.dll"))
}
