# s0-ghosts.ps1 -- generic S0 sequential ghost stage for ANY sequential zmx.
# Emits S1 optical KPIs + the full GPIM ghost enumeration, Fresnel-weighted
# using indices read from the REAL catalog (INDX operand), so the Ghost Focus
# Generator's "all surfaces share one coating" assumption is dropped.
#
# Verified GPIM convention: Param1=Surf1 > Param2=Surf2 (light reflects at the
# later surface first), Param3=Mode (1 = image ghost, 0 = pupil).
# PowerShell trap: [EnumType]::$var silently yields $null -> operand stays
# BLNK. Always [Enum]::Parse().
param(
    [Parameter(Mandatory=$true)][string]$LensFile,
    [Parameter(Mandatory=$true)][string]$OutJson,
    [Parameter(Mandatory=$true)][string]$Slug
)
$LOG = [IO.Path]::ChangeExtension($OutJson, ".log")
"" | Out-File -Encoding utf8 $LOG
function Stage($m) { $m | Out-File -Encoding utf8 -Append $LOG }

# This file is a PIPELINE stage (runner.st_s0 invokes it), unlike the rest of
# ghost/, which is Double Gauss ghost-optimisation research. The 2026-08-08
# settings conversion scanned lib/, survey/ and testcases/ only, so this kept a
# hardcoded OpticStudio path until a full fleet run from a built distribution
# reached the s0 stage on 2026-08-09.
. "$(Split-Path -Parent $PSScriptRoot)\lib\settings.ps1"
Import-ZOSAPI
$ZemaxDir = $SL_OPTICSTUDIO

$TOPT = [ZOSAPI.Editors.MFE.MeritOperandType]
$TCOL = [ZOSAPI.Editors.MFE.MeritColumn]
$GPIM = [Enum]::Parse($TOPT, "GPIM")
$EFFL = [Enum]::Parse($TOPT, "EFFL")
$RSCE = [Enum]::Parse($TOPT, "RSCE")
$P1 = [Enum]::Parse($TCOL,"Param1"); $P2 = [Enum]::Parse($TCOL,"Param2"); $P3 = [Enum]::Parse($TCOL,"Param3")
$hasINDX = ([Enum]::GetNames($TOPT) -contains "INDX")
if ($hasINDX) { $INDX = [Enum]::Parse($TOPT, "INDX") }

$conn = New-Object ZOSAPI.ZOSAPI_Connection
$app = $conn.CreateNewApplication()
$sys = $app.PrimarySystem
if (-not $sys.LoadFile($LensFile, $false)) { $app.CloseApplication(); throw "LoadFile failed: $LensFile" }
$lde = $sys.LDE
$imgSurf = [int]$lde.NumberOfSurfaces - 1
$mfe = $sys.MFE
# IWavelengths.Primary DOES NOT EXIST in 2026 R1 -- it reads back as $null and
# `[int]$null` is 0 with NO error, so every S0 run before 2026-07-25 logged
# "primaryWave=0" and evaluated INDX at an unspecified wavelength. Find the
# primary by asking the wavelengths themselves.
$wave = 1
for ($i = 1; $i -le [int]$sys.SystemData.Wavelengths.NumberOfWavelengths; $i++) {
    if ($sys.SystemData.Wavelengths.GetWavelength($i).IsPrimary) { $wave = $i; break }
}
Stage "$Slug : surfaces=$($lde.NumberOfSurfaces) img=$imgSurf primaryWave=$wave INDX=$hasINDX"

# ---- S1 optical KPIs
$efl = $mfe.GetOperandValue($EFFL, 1, $wave, 0,0,0,0,0,0)
# RSCE stays at wave=0 DELIBERATELY: 0 means polychromatic, and a
# single-wavelength spot check once hid a +49.5% edge degradation that the
# polychromatic check exposed. Do not "fix" this to $wave.
$rms = @()
foreach ($hy in 0.0, 0.7, 1.0) {
    $rms += $mfe.GetOperandValue($RSCE, 0, 0, 0.0, $hy, 0, 0, 6, 0)
}
$maxField = 0.0
$fld = $sys.SystemData.Fields
for ($i = 1; $i -le [int]$fld.NumberOfFields; $i++) {
    $y = [math]::Abs([double]$fld.GetField($i).Y)
    if ($y -gt $maxField) { $maxField = $y }
}
Stage ("S1: EFL={0:F3} maxField={1} RSCE_mm={2}" -f $efl, $maxField,
    (($rms | ForEach-Object { "{0:F5}" -f $_ }) -join "/"))

# ---- refractive interfaces + per-interface index step
$interfaces = @()
$nBefore = @{}; $nAfter = @{}
for ($s = 1; $s -lt $imgSurf; $s++) {
    $matPrev = "$($lde.GetSurfaceAt($s - 1).Material)".Trim()
    $matHere = "$($lde.GetSurfaceAt($s).Material)".Trim()
    if ($matPrev -eq $matHere) { continue }
    $interfaces += $s
    $n1 = 1.0; $n2 = 1.0
    if ($hasINDX) {
        if ($matPrev -ne "") { $n1 = $mfe.GetOperandValue($INDX, $s - 1, $wave, 0,0,0,0,0,0) }
        if ($matHere -ne "") { $n2 = $mfe.GetOperandValue($INDX, $s, $wave, 0,0,0,0,0,0) }
    } else {
        if ($matPrev -ne "") { $n1 = 1.6 }
        if ($matHere -ne "") { $n2 = 1.6 }
    }
    if ($n1 -le 0 -or [double]::IsNaN($n1)) { $n1 = 1.0 }
    if ($n2 -le 0 -or [double]::IsNaN($n2)) { $n2 = 1.0 }
    $nBefore[$s] = $n1; $nAfter[$s] = $n2
}
Stage "interfaces: $($interfaces -join ',')"

# Fresnel per interface; AR (9% residual) applied to air-glass only, cemented
# interfaces left bare (they are already ~2 orders weaker)
$AR = 0.09
# NB: named $Rfres, not $R -- PowerShell variables are CASE-INSENSITIVE, so a
# hashtable called $R is silently destroyed by the later `$r = $mfe.AddOperand()`
$Rfres = @{}
foreach ($s in $interfaces) {
    $n1 = $nBefore[$s]; $n2 = $nAfter[$s]
    $r = [math]::Pow(($n2 - $n1) / ($n2 + $n1), 2)
    $isAirGlass = ($n1 -lt 1.01) -or ($n2 -lt 1.01)
    if ($isAirGlass) { $r = $r * $AR }
    $Rfres[$s] = $r
    Stage ("  surf {0,2}: n {1:F4}->{2:F4}  {3}  R={4:E3}" -f $s, $n1, $n2,
        $(if ($isAirGlass) { "air-glass" } else { "cemented " }), $r)
}

# ---- GPIM enumeration over all ordered pairs (Surf1 > Surf2)
#
# CANARY FIRST, then append as before.
# Fixed 2026-07-26 after the enumeration was found returning 0.0000 for EVERY
# pair on 6 of 77 systems (A35 0/90 nonzero, B04 0/30, B15 0/132, B25 0/30,
# against 100% nonzero on every control) while the stage still reported ok.
#
# Cause: AddOperand + CalculateMeritFunction evaluates the WHOLE merit
# function, including the ~142 operands inherited from the sample lens file.
# Bisection pinned it to row 136, a TRCY (transverse ray) operand at wave 3.
# A01 carries the IDENTICAL operand at the same row with the same weight and is
# fine - the operand is not malformed, its ray simply no longer traces on the
# perturbed geometry, and ONE untraceable ray zeroes the entire evaluation.
# That is why EFFL/RSCE/INDX above were always correct: they already use
# GetOperandValue, which evaluates a single operand standalone.
#
# Reading GPIM via GetOperandValue also works and was tried first, but it
# returns ~1% different values (unset Param4 defaults differ) and that was
# enough to move one borderline case out of rank 1: p@1 87% -> 83%, MRR
# 0.933 -> 0.917 over the 30 known ghost pairs. None of the six poisoned
# systems carries an injected pair, so the published ranking was never
# corrupted by them - meaning a method switch would have traded a real
# regression for no gain in score. Clearing the poisoned MF instead keeps the
# original evaluation semantics AND revives the dead systems.
$canary = $mfe.AddOperand()
[void]$canary.ChangeType($EFFL)
[void]$mfe.CalculateMeritFunction()
if ([math]::Abs([double]$canary.Value) -lt 1e-9) {
    Stage "merit function is not evaluating (EFFL=0) - clearing $($mfe.NumberOfOperands) inherited operands"
    while ($mfe.NumberOfOperands -gt 1) { [void]$mfe.RemoveOperandAt(1) }
    [void]$mfe.CalculateMeritFunction()
}
$rows = @()
foreach ($j in $interfaces) {
    foreach ($i in $interfaces) {
        if ($i -le $j) { continue }
        foreach ($mode in 0, 1) {
            $r = $mfe.AddOperand()
            if (-not $r.ChangeType($GPIM)) { Stage "ChangeType failed ($i,$j)"; continue }
            $r.GetOperandCell($P1).IntegerValue = $i
            $r.GetOperandCell($P2).IntegerValue = $j
            $r.GetOperandCell($P3).IntegerValue = $mode
            $r.Target = 0.0
            $r.Weight = 0.0
            $rows += ,@{ r = $r; s1 = $i; s2 = $j; m = $mode }
        }
    }
}
Stage "added $($rows.Count) GPIM operands; calculating..."
[void]$mfe.CalculateMeritFunction()
foreach ($x in $rows) { $x.v = [double]$x.r.Value }
$nzero = ($rows | Where-Object { [math]::Abs($_.v) -gt 1e-12 }).Count
if ($rows.Count -gt 0 -and $nzero -eq 0) {
    throw "GUARD FAILED [s0] all $($rows.Count) GPIM values are 0 - the ghost enumeration measured nothing"
}
Stage "  $nzero of $($rows.Count) GPIM values are non-zero"

$json = @()
foreach ($x in $rows) {
    $v = $x.v
    $vs = if ([double]::IsNaN($v)) { "null" } else { "$v" }
    $w = $Rfres[$x.s1] * $Rfres[$x.s2]
    $json += ('{{"surf1":{0},"surf2":{1},"mode":{2},"value":{3},"fresnel":{4}}}' -f
        $x.s1, $x.s2, $x.m, $vs, $w)
}
$app.CloseApplication()

$rj = ($interfaces | ForEach-Object { '"{0}":{1}' -f $_, $Rfres[$_] }) -join ","
('{"slug":"' + $Slug + '","lens":"' + ($LensFile -replace '\\','/') + '"' +
 ',"efl":' + $efl + ',"maxField":' + $maxField +
 ',"rsce_mm":[' + ($rms -join ",") + ']' +
 ',"interfaces":[' + ($interfaces -join ",") + ']' +
 ',"fresnel":{' + $rj + '}' +
 ',"gpim":[' + ($json -join ",") + ']}') | Out-File -Encoding utf8 $OutJson
Stage "wrote $OutJson"
