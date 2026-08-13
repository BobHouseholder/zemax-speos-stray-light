# trace-layout-generic.ps1 -- prescription + ray fans for ANY sequential zmx.
# Usage: -LensFile <zmx> -OutJson <json>
# Emits: imgZ, imgSD, maxField (deg), primaryWave, surfaces[], rays[] (3
# normalized fields x 5 pupils, global z,y per surface).
param(
    [Parameter(Mandatory=$true)][string]$LensFile,
    [Parameter(Mandatory=$true)][string]$OutJson
)
. "$(Split-Path -Parent $PSScriptRoot)\lib\settings.ps1"
Import-ZOSAPI

$conn = New-Object ZOSAPI.ZOSAPI_Connection
$app = $conn.CreateNewApplication()
$sys = $app.PrimarySystem
if (-not $sys.LoadFile($LensFile, $false)) { $app.CloseApplication(); throw "LoadFile failed" }

$lde = $sys.LDE
# $nsurf, NOT $n: the batch-trace call below reads a direction cosine into
# [ref]$N, and PowerShell variables are CASE-INSENSITIVE, so $N and $n are the
# same variable. A plain $n here is overwritten by the ray's z-cosine and the
# stage reports "0.999 surfaces". Log-only in this script, but it is the third
# instance of this trap in this codebase.
$nsurf = [int]$lde.NumberOfSurfaces
$imgSurf = $nsurf - 1
# IWavelengths.Primary does NOT exist in 2026 R1: it reads back as $null and
# [int]$null is 0 with no error, so every layout trace before 2026-07-25 passed
# wavelength number 0 to AddRay. Ask the wavelengths which one is primary.
$wave = 1
for ($i = 1; $i -le [int]$sys.SystemData.Wavelengths.NumberOfWavelengths; $i++) {
  if ($sys.SystemData.Wavelengths.GetWavelength($i).IsPrimary) { $wave = $i; break }
}

$maxField = 0.0
$fld = $sys.SystemData.Fields
for ($i = 1; $i -le [int]$fld.NumberOfFields; $i++) {
  $y = [math]::Abs([double]$fld.GetField($i).Y)
  if ($y -gt $maxField) { $maxField = $y }
}

$verts = @{}
$z = 0.0
for ($s = 1; $s -le $imgSurf; $s++) {
  $verts[$s] = $z
  $z += [double]$lde.GetSurfaceAt($s).Thickness
}
$imgSD = [double]$lde.GetSurfaceAt($imgSurf).SemiDiameter
$stopSurf = -1; $stopSD = 0.0
for ($s = 1; $s -lt $imgSurf; $s++) {
  if ($lde.GetSurfaceAt($s).IsStop) { $stopSurf = $s; $stopSD = [double]$lde.GetSurfaceAt($s).SemiDiameter }
}

$surfJson = @()
for ($s = 1; $s -le $imgSurf; $s++) {
  $row = $lde.GetSurfaceAt($s)
  $mat = "$($row.Material)".Trim()
  $rad = [double]$row.Radius
  if ([double]::IsInfinity($rad) -or [double]::IsNaN($rad)) { $rad = 0.0 }
  # A flat surface reads back as Infinity and 0 is its correct JSON spelling.
  # A SEMI-DIAMETER has no such fallback: infinity there means the clear
  # aperture is undefined, and the old code emitted the bare token "inf",
  # producing '"sd":inf' -- invalid JSON, so the stage died with
  # "Expecting value: line 1 column 248" and named neither the surface nor the
  # cause. Seen on B22, a Double Gauss opened to f/1.0, where surfaces 3 and 5
  # went infinite between neighbours reading 66 mm and 112 mm. Fail loudly with
  # the surface number instead; preflight gate 5b rejects these before layout.
  $sdv = [double]$row.SemiDiameter
  if ([double]::IsInfinity($sdv) -or [double]::IsNaN($sdv)) {
    throw ("GUARD FAILED [layout] surface {0} has a non-finite semi-diameter - " -f $s) +
          "the clear aperture is undefined, so no bore or seat radius can be derived"
  }
  $surfJson += ('{{"s":{0},"z":{1},"R":{2},"sd":{3},"glass":"{4}"}}' -f
    $s, [math]::Round([double]$verts[$s], 4), [math]::Round($rad, 4),
    [math]::Round($sdv, 4), $mat)
}

# ---- ray fans -------------------------------------------------------------
#
# DENSITY MATTERS, and 3 fields x 5 pupil points was not enough. The mechanical
# generator builds the beam envelope from these polylines; a sampled maximum is
# a LOWER BOUND on the continuous beam, so bores sized to it clip. Measured on
# B01 (a Double Gauss run at 26 deg): every bore "cleared" the 15-ray envelope,
# the tightest by 0.119 mm, and the barrel still removed 49% of the corner's
# true illumination -- confirmed against a lens-bodies-only Speos run. At a
# heavily vignetted field the surviving bundle is a thin crescent against the
# aperture rim, which is exactly where coarse pupil sampling misses.
#
# 7 fields x 15 pupil points = 105 rays. Field points are clustered toward the
# edge because that is where vignetting bites; pupil points are uniform because
# the extreme radius is at the rim.
$fields = @(0.0, 0.3, 0.5, 0.7, 0.85, 0.95, 1.0)
$pupils = @(-1.0, -0.95, -0.85, -0.7, -0.55, -0.4, -0.2, 0.0,
            0.2, 0.4, 0.55, 0.7, 0.85, 0.95, 1.0)

# One batch PER SURFACE holding every ray, not one batch per (ray, surface).
# The old form ran 15 x nsurf separate traces; this runs nsurf. Denser sampling
# is therefore cheaper than the sparse version it replaces, not 7x dearer.
$rayspec = @()
foreach ($hy in $fields) { foreach ($py in $pupils) { $rayspec += ,@($hy, $py) } }
$nray = $rayspec.Count
$ptsFor = @{}
for ($k = 0; $k -lt $nray; $k++) { $ptsFor[$k] = New-Object System.Collections.ArrayList }

$rt = $sys.Tools.OpenBatchRayTrace()
$readTotal = 0; $keptTotal = 0; $rnMin = 999999; $rnMax = -1
for ($s = 1; $s -le $imgSurf; $s++) {
  $norm = $rt.CreateNormUnpol($nray, [ZOSAPI.Tools.RayTrace.RaysType]::Real, $s)
  foreach ($r in $rayspec) {
    [void]$norm.AddRay($wave, [double]0.0, [double]$r[0], [double]0.0, [double]$r[1],
      [ZOSAPI.Tools.RayTrace.OPDMode]::None)
  }
  [void]$rt.RunAndWaitForCompletion()
  [void]$norm.StartReadingResults()
  for ($i = 0; $i -lt $nray; $i++) {
    $rn=0; $ec=0; $vc=0; $X=0.0; $Y=0.0; $Z=0.0; $L=0.0; $M=0.0; $N=0.0; $l2=0.0; $m2=0.0; $n2=0.0; $op=0.0; $it=0.0
    $ok = $norm.ReadNextResult([ref]$rn,[ref]$ec,[ref]$vc,[ref]$X,[ref]$Y,[ref]$Z,[ref]$L,[ref]$M,[ref]$N,[ref]$l2,[ref]$m2,[ref]$n2,[ref]$op,[ref]$it)
    if (-not $ok) { break }
    $readTotal++
    if ([int]$rn -lt $rnMin) { $rnMin = [int]$rn }
    if ([int]$rn -gt $rnMax) { $rnMax = [int]$rn }
    # The ray-number base is NOT documented. Accept 1-based or 0-based, and
    # fall back to read order. The first version assumed 1-based, so a 0-based
    # reply mapped every ray to index -1, the guard dropped it, and the trace
    # wrote 105 rays with ZERO points each -- silently.
    $idx = -1
    if ([int]$rn -ge 1 -and [int]$rn -le $nray) { $idx = [int]$rn - 1 }
    elseif ([int]$rn -ge 0 -and [int]$rn -lt $nray) { $idx = [int]$rn }
    else { $idx = $i }
    if ($ec -eq 0 -and $idx -ge 0 -and $idx -lt $nray) {
      $gz = [double]$verts[$s] + $Z
      # build the string FIRST: inside a method call, `.Add("..." -f a, b)`
      # binds b as a second argument to Add, not to -f, and the format throws
      $ptStr = "[{0},{1}]" -f [math]::Round($gz, 4), [math]::Round([double]$Y, 4)
      [void]$ptsFor[$idx].Add($ptStr)
      $keptTotal++
    }
  }
}
$rt.Close()
Write-Output ("  batch trace: {0} rays x {1} surfaces, {2} results read, {3} kept, rayNumber range {4}..{5}" -f
  $nray, $imgSurf, $readTotal, $keptTotal, $rnMin, $rnMax)
if ($keptTotal -eq 0) { throw "GUARD FAILED [layout] batch trace returned NO usable results" }

$rows = @()
for ($k = 0; $k -lt $nray; $k++) {
  $rows += ('{{"hy":{0},"py":{1},"pts":[{2}]}}' -f
    $rayspec[$k][0], $rayspec[$k][1], ($ptsFor[$k] -join ","))
}
# RELI per field, taken from the SAME session -- one extra second, no extra
# licence seat. The in-field "throughput" the Speos loop reports is total
# detector flux from an imported per-field source, which STOPS being throughput
# once that source no longer couples into the pupil: measured 0.076 against a
# true RELI of 0.905 on a Double Gauss driven to 26 deg. Without this reference
# guard.assert_infield_metric_valid has nothing to check against, so every
# in-field number downstream is unvalidated -- which is exactly how a
# "+91%/+92% corner recovery" claim got published and then withdrawn.
#
# Columns are Param1=Samp, Param2=Wave, Param3=Field. READ THE HEADERS -- a
# first pass elsewhere passed the field number positionally as arg 1 and got
# RELI = 1.0000 for every field, silently. Wave is the PRIMARY wavelength
# ($wave above), not 1: this suite spans 248 nm to 12 um and primary is often 2.
$reliVals = @()
try {
  $mfeR = $sys.MFE
  # SENTINEL FIRST. Appending a probe operand to the file's existing merit
  # function is the cheap path, and it is what the standing advice says to do
  # (clearing a 142-operand MF recalculates on every removal). But MEASURED
  # 2026-07-26: on 6 of 77 systems the INHERITED merit function makes
  # CalculateMeritFunction return nothing for EVERY operand -- EFFL, RSCE and
  # RELI all came back exactly 0.00000 on A35 while the identical operands
  # worked on A01, same base design, same aperture, same fields. Clearing the
  # MF revived it (RELI 1.00000, 1.07889, 1.35640). The poisoning operand has
  # not been identified; surface references were checked and are all in range.
  #
  # So: evaluate EFFL as a canary. A real lens cannot have EFFL = 0, so if the
  # canary is dead the whole MF is unusable and we pay the clearing cost. This
  # keeps the fast path for the 71 healthy systems and stops the other 6 from
  # silently reporting 0 as though it were a measurement.
  $canary = $mfeR.AddOperand()
  [void]$canary.ChangeType([Enum]::Parse([ZOSAPI.Editors.MFE.MeritOperandType], "EFFL"))
  [void]$mfeR.CalculateMeritFunction()
  if ([math]::Abs([double]$canary.Value) -lt 1e-9) {
    Write-Output ("  merit function is not evaluating (EFFL=0) - clearing {0} inherited operands" -f $mfeR.NumberOfOperands)
    while ($mfeR.NumberOfOperands -gt 1) { [void]$mfeR.RemoveOperandAt(1) }
    [void]$mfeR.CalculateMeritFunction()
  }
  $reliOp = $mfeR.AddOperand()
  [void]$reliOp.ChangeType([Enum]::Parse([ZOSAPI.Editors.MFE.MeritOperandType], "RELI"))
  $TCOLR = [ZOSAPI.Editors.MFE.MeritColumn]
  for ($fnum = 1; $fnum -le [int]$fld.NumberOfFields; $fnum++) {
    foreach ($pcol in "Param1","Param2","Param3") {
      try {
        $reliCell = $reliOp.GetOperandCell([Enum]::Parse($TCOLR, $pcol))
        $reliHdr = "$($reliCell.Header)".Trim()
        if ($reliHdr -match "Field") { $reliCell.IntegerValue = $fnum }
        elseif ($reliHdr -match "Wave") { $reliCell.IntegerValue = $wave }
      } catch { }
    }
    [void]$mfeR.CalculateMeritFunction()
    $vHdr = [double]$reliOp.Value
    # CROSS-CHECK THE TWO API PATHS. Measured 2026-07-27: on B05 and B15 RELI
    # cannot be computed off-axis, and the two calls disagree about how to say
    # so - AddOperand returns 1.0000 (indistinguishable from perfectly uniform
    # illumination, and the WORST possible failure value because it silently
    # passes every plausibility test) while GetOperandValue returns 0.0000.
    # Neither raises. The field argument is NOT the problem: headers read
    # Samp/Wave/Field correctly and the cell reads back the field number.
    # When the paths disagree the value is not a measurement - emit null so
    # downstream rejects it rather than validating in-field flux against a
    # fabricated 1.0.
    $vPos = [double]$mfeR.GetOperandValue(
        [Enum]::Parse([ZOSAPI.Editors.MFE.MeritOperandType], "RELI"), 0, $wave, $fnum, 0, 0, 0, 0, 0)
    $agree = [math]::Abs($vHdr - $vPos) -le (1e-4 * [math]::Max(1.0, [math]::Abs($vHdr)))
    if ($agree) {
      $reliVals += [math]::Round($vHdr, 5)
    } else {
      $reliVals += "null"
      Write-Output ("  RELI field {0}: PATHS DISAGREE (operand {1:N5} vs positional {2:N5}) - not a measurement" -f
        $fnum, $vHdr, $vPos)
    }
  }
  Write-Output ("  RELI (wave {0}, {1} fields): {2}" -f
    $wave, [int]$fld.NumberOfFields, ($reliVals -join ", "))
} catch {
  Write-Output ("  RELI unavailable: " + $_.Exception.Message)
}

# DISTORTION per field -- the independent check on RELI.
# Relative illumination normally FALLS with field (~cos^4). It may legitimately
# RISE above 1 only when the system COMPRESSES the image, i.e. barrel
# (negative) distortion packing the same flux into a smaller area. Measured
# 2026-07-26: B04 rises to RELI 1.691 at 30 deg and has -24.9% barrel
# distortion, whose area-compression ratio (50.518/37.919)^2 = 1.775 accounts
# for it - that reading is REAL. A35 rises to 1.356 while its distortion is
# PINCUSHION +6.2%, which must LOWER illumination, and B15 returns exactly
# 1.0000 at every field despite +17.7% pincushion (the signature of a field
# argument that never landed). Both of those are still wrong.
# Without distortion alongside it, RELI > 1 cannot be judged either way.
# REAY/EFFL go through GetOperandValue, which evaluates one operand standalone
# and is immune to the inherited-merit-function poisoning.
$distVals = @()
$fieldDegVals = @()
$realHVals = @()
try {
  $EFFLop = [Enum]::Parse([ZOSAPI.Editors.MFE.MeritOperandType], "EFFL")
  $REAYop = [Enum]::Parse([ZOSAPI.Editors.MFE.MeritOperandType], "REAY")
  $eflv = [double]$sys.MFE.GetOperandValue($EFFLop, 1, $wave, 0,0,0,0,0,0)
  $fmax = [double]$fld.GetField([int]$fld.NumberOfFields).Y
  for ($fnum2 = 1; $fnum2 -le [int]$fld.NumberOfFields; $fnum2++) {
    $ydeg = [double]$fld.GetField($fnum2).Y
    $hyn = if ([math]::Abs($fmax) -gt 1e-12) { $ydeg / $fmax } else { 0.0 }
    $realh = [double]$sys.MFE.GetOperandValue($REAYop, $imgSurf, $wave, 0.0, $hyn, 0.0, 0.0, 0, 0)
    $parh = $eflv * [math]::Tan($ydeg * [math]::PI / 180.0)
    if ([math]::Abs($parh) -gt 1e-9) {
      $distVals += [math]::Round(100.0 * ($realh - $parh) / $parh, 3)
    } else {
      $distVals += 0.0
    }
    # keep these for the geometric relative-illumination cross-check. They MUST
    # be collected here, while the application is still open: the JSON string is
    # assembled after $app.CloseApplication(), where every API call silently
    # returns nothing (which is how an earlier attempt emitted empty arrays).
    $fieldDegVals += [math]::Round($ydeg, 5)
    $realHVals += [math]::Round($realh, 5)
  }
  Write-Output ("  distortion %: " + ($distVals -join ", "))
} catch {
  Write-Output ("  distortion unavailable: " + $_.Exception.Message)
}

# lens unit -> mm factor, carried so downstream generators are never
# unit-blind (all current corpus files are MM -- verified 2026-08-05 -- but a
# customer file can arrive in anything; mech_scale.py consumes unitToMm)
$lu = "$($sys.SystemData.Units.LensUnits)"
$luF = switch ($lu) { "Millimeters" { 1.0 } "Centimeters" { 10.0 } "Inches" { 25.4 } "Meters" { 1000.0 } default { 1.0 } }
$app.CloseApplication()
('{"lensUnit":"' + $lu + '","unitToMm":' + $luF + ',"imgZ":' + [math]::Round([double]$verts[$imgSurf], 4) + ',"imgSD":' + [math]::Round($imgSD, 4) +
  ',"maxField":' + [math]::Round($maxField, 3) + ',"primaryWave":' + $wave +
  ',"reli":[' + ($reliVals -join ",") + ']' +
  ',"distortPct":[' + ($distVals -join ",") + ']' +
  ',"efl":' + $(if ($eflv) { [math]::Round([double]$eflv, 5) } else { 0 }) +
  ',"fieldDeg":[' + ($fieldDegVals -join ",") + ']' +
  ',"realH":[' + ($realHVals -join ",") + ']' +
  ',"stopSurf":' + $stopSurf + ',"stopSD":' + [math]::Round($stopSD, 4) +
  ',"surfaces":[' + ($surfJson -join ",") + '],"rays":[' + ($rows -join ",") + ']}') |
  Out-File $OutJson -Encoding utf8
Write-Output ("layout written: {0} ({1} surfaces, maxField {2}, imgZ {3})" -f
  $OutJson, $nsurf, $maxField, [math]::Round([double]$verts[$imgSurf], 2))
