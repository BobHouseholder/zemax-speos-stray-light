# export-odx.ps1 -- headless OpticStudio -> Speos ODX (whitebox) export
# Usage: powershell -File export-odx.ps1 [-LensFile <path.zmx>]
# Discovered via reflection over ZOSAPI_Interfaces.dll (2026 R1):
#   ZOSAPI.Tools.IOpticalSystemTools.OpenExportToSpeosWhitebox  -> .odx (full geometry)
#   (OpenExportToSpeosLensSystem is the .OPTDistortion ROM -- not this workflow)
param(
    [string]$LensFile = "C:\Users\<user>\Dropbox\Optics\stray-light-loop\Double Gauss 28 degree field.zmx"
)

$ZemaxDir = "C:\Program Files\Ansys Zemax OpticStudio 2026 R1.00"
Add-Type -Path (Join-Path $ZemaxDir "ZOSAPI_NetHelper.dll")
if (-not [ZOSAPI_NetHelper.ZOSAPI_Initializer]::Initialize($ZemaxDir)) {
    throw "ZOSAPI_Initializer failed for $ZemaxDir"
}
[void][System.Reflection.Assembly]::LoadFrom((Join-Path $ZemaxDir "ZOSAPI.dll"))
[void][System.Reflection.Assembly]::LoadFrom((Join-Path $ZemaxDir "ZOSAPI_Interfaces.dll"))

$conn = New-Object ZOSAPI.ZOSAPI_Connection
$app = $conn.CreateNewApplication()
if ($null -eq $app) { throw "CreateNewApplication returned null (license?)" }
if (-not $app.IsValidLicenseForAPI) { throw "License is not valid for ZOS-API" }

try {
    $sys = $app.PrimarySystem
    if (-not $sys.LoadFile($LensFile, $false)) { throw "LoadFile failed: $LensFile" }
    Write-Output "Loaded: $LensFile"

    $tool = $sys.Tools.OpenExportToSpeosWhitebox()
    if ($null -eq $tool) { throw "OpenExportToSpeosWhitebox returned null" }
    [void]$tool.RunAndWaitForCompletion()
    $ok = $tool.Succeeded
    Write-Output "Succeeded : $ok"
    Write-Output "ErrorCode : $($tool.ErrorCode)"
    Write-Output "Output    : $($tool.OutputFilename)"
    if (-not [string]::IsNullOrEmpty($tool.ErrorMessage)) { Write-Output "Message   : $($tool.ErrorMessage)" }
    $tool.Close()
}
finally {
    $app.CloseApplication()
}
