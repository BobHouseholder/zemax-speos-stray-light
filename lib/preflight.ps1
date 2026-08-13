# preflight.ps1 -- can this workflow handle this lens file?
# Usage: -LensFile <zmx> [-OutJson <json>]
# ASCII-ONLY (see zos-guard.ps1 header for why).
#
# Answers BEFORE any expensive work is committed. Every gate exists because a
# system reached (or passed) mechanical generation and Speos launches before
# failing:
#   * cameralens14 -- burned a layout trace, ODX export, mech generation and 2
#     Speos launches before Speos rejected it: "Unsupported .odx aperture
#     parameters. The front and back face aperture parameters are not
#     identical."
#   * eye20 -- passed every numeric OPTICAL gate, then produced one "element"
#     and a meaningless 1.2 mm vane, because an eye has no mechanical barrel.
#
# Verdicts: GO | GO-WITH-WARNINGS | NO-GO
param(
    [Parameter(Mandatory=$true)][string]$LensFile,
    [string]$OutJson = ""
)
. "$PSScriptRoot\zos-guard.ps1"

$fail = New-Object System.Collections.ArrayList
$warn = New-Object System.Collections.ArrayList
function Bad($m)  { [void]$fail.Add($m) }
function Iffy($m) { [void]$warn.Add($m) }

# ---------- gate 0: environment (the licence seat) ----------
Assert-SeatAvailable -Fix

. "$PSScriptRoot\settings.ps1"
Import-ZOSAPI

$conn = New-Object ZOSAPI.ZOSAPI_Connection
$app = $conn.CreateNewApplication()
Assert-Connected $app
$sys = $app.PrimarySystem
if (-not $sys.LoadFile($LensFile, $false)) {
    $app.CloseApplication()
    throw "GUARD FAILED [preflight] cannot load $LensFile"
}
$lde = $sys.LDE
$n = [int]$lde.NumberOfSurfaces
$imgSurf = $n - 1
$title = ""
try { $title = "$($sys.SystemData.TitleNotes.Title)".Trim() } catch { }

# ---------- gate 1: sequential mode ----------
$mode = "$($sys.Mode)"
if ($mode -ne "Sequential") { Bad "mode is '$mode', not Sequential" }

# ---------- gate 2: object at infinity, image finite ----------
$objThick = [double]$lde.GetSurfaceAt(0).Thickness
if (-not [double]::IsInfinity($objThick)) {
    Iffy ("object at finite distance {0:F1} - the collimated stray-source pattern assumes infinity" -f $objThick)
}
$backFocus = [double]$lde.GetSurfaceAt($imgSurf - 1).Thickness
if ([double]::IsInfinity($backFocus)) { Bad "image distance is infinite (afocal system)" }

# ---------- gate 3: refractive only, allowed surface types, no tilts ----------
# TypeName comes back SPACED ("Even Asphere", "Coordinate Break"), so a list
# written in CamelCase matched nothing and the gate refused every aspheric
# system -- a false halt found by test case C04. Compare with spaces removed.
$ALLOWED = @("Standard", "EvenAspheric", "EvenAsphere")
$mirrors = 0; $badTypes = @(); $tilted = 0
for ($s = 1; $s -lt $imgSurf; $s++) {
    $row = $lde.GetSurfaceAt($s)
    if ("$($row.Material)".Trim().ToUpper() -eq "MIRROR") { $mirrors++ }
    $tn = "$($row.TypeName)".Trim()
    $tnNorm = $tn -replace '\s', ''
    if ($ALLOWED -notcontains $tnNorm) { $badTypes += "$s=$tn" }
    try {
        $td = $row.TiltDecenterData
        if ([math]::Abs([double]$td.BeforeSurfaceDecenterX) -gt 1e-9 -or
            [math]::Abs([double]$td.BeforeSurfaceDecenterY) -gt 1e-9 -or
            [math]::Abs([double]$td.BeforeSurfaceTiltX) -gt 1e-9 -or
            [math]::Abs([double]$td.BeforeSurfaceTiltY) -gt 1e-9) { $tilted++ }
    } catch { }
}
if ($mirrors -gt 0) { Bad "$mirrors MIRROR surface(s) - the seated-barrel archetype is refractive-only" }
if ($badTypes.Count -gt 0) { Bad ("unsupported surface type(s): " + ($badTypes -join ", ")) }
if ($tilted -gt 0) { Bad "$tilted surface(s) tilted or decentred - geometry is not rotationally symmetric" }

# ---------- gate 4: fields angular and usable ----------
$fld = $sys.SystemData.Fields
$ftype = "$($fld.GetFieldType())"
$maxField = 0.0
for ($i = 1; $i -le [int]$fld.NumberOfFields; $i++) {
    $y = [math]::Abs([double]$fld.GetField($i).Y)
    if ($y -gt $maxField) { $maxField = $y }
}
if ($ftype -notmatch "Angle") { Bad "field type is '$ftype', expected Angle" }
if ($maxField -lt 3.0) { Iffy ("max field {0:F1} deg is narrow - stray-source placement gets awkward" -f $maxField) }
# Out-of-field means theta > maxField, and the stray source can only be placed
# out to 85 deg (the disc sits at z=-40 ahead of the entrance, so at 90 deg it
# is edge-on to the entrance plane and beyond that it is BEHIND it, where no
# front-facing barrel can baffle it). So maxField >= 85 leaves NO valid stray
# angle at all -- exact, not a heuristic.
#
# This BLOCKS as of 2026-08-05, after the ground truth for C10 and C12 was
# corrected. It shipped as a warning for one day precisely because it was
# shared with the injected-defect suite, where both cases were tagged
# expect-go/expect-warn -- blocking would have manufactured two false halts
# against the suite's own known answers. That expectation is now measured to be
# wrong (wideanglelen100, same 200 deg FOV base, delivers 0 W at 50 and 100 deg
# with NO mechanics at all), so truth and gate agree and the block is correct.
# C10's truth had NAMED gate4-extreme-field from the start while expecting GO:
# the gate was anticipated, never written, and the expectation set to match the
# absent gate rather than the physics.
#
# runner.py's angle_gate still enforces strayDefined on the MEASURED angle --
# this catches the case in seconds, that one catches an angle that resolves
# above 85 deg on a system whose maxField is below it.
if ($maxField -ge 85.0) {
    Bad ("max field {0:F1} deg leaves no out-of-field direction ahead of the entrance plane (stray placement is defined only to 85 deg), so no stray angle exists to measure - this needs a different archetype, not a measurement" -f $maxField)
}

# ---------- gate 4b: vertex z must increase monotonically ----------
# A seated barrel is built along a single increasing z axis. schamm110 carries
# a NEGATIVE thickness on surface 1 (a remote-stop construction placing the
# pupil ahead of the lens), so cumulative vertex z runs 0 -> -80 -> ... The
# generator then sampled the beam envelope at z positions that do not
# correspond to the elements there, produced seat rings intruding 13.5 mm into
# a 27 mm beam, and the barrel BLOCKED the imaging beam entirely -- while the
# stray metric read a triumphant -91%. Reject the construction instead of
# emitting a barrel that silently obstructs the optics.
$zc = 0.0; $negThick = @()
for ($s = 1; $s -lt $imgSurf; $s++) {
    $th = [double]$lde.GetSurfaceAt($s).Thickness
    if ($th -lt 0) { $negThick += ("surf {0} t={1:F2}" -f $s, $th) }
    $zc += $th
}
if ($negThick.Count -gt 0) {
    Bad ("negative thickness (" + ($negThick -join ", ") +
         ") - vertex z is not monotonic, so a single-axis barrel cannot be seated. " +
         "Remote-stop/reversed constructions are out of scope for the seated-barrel archetype.")
}

# ---------- gate 4b: NON-ZERO TOTAL TRACK ----------
# Every surface at z=0 means the prescription has never been laid out - a
# starting-point template with all thicknesses still zero, not a lens. There is
# no space between elements to house, no bore to compute and no image distance.
#
# MEASURED 2026-07-27 on scdoublet15, whose .zmx has DISZ=0 on every surface
# including the image. It used to reach the LAYOUT stage before dying with
# "layout for scdoublet15 has imgZ=0 -- no image distance", which is a correct
# complaint from the wrong stage: nothing about that file needed a ray trace to
# reject. Scanned all layouts across the test suite and the survey - it is the
# only system with a total track of 0, and the next smallest is a real system,
# so this cannot refuse anything buildable.
if ($zc -le 1e-6) {
    Bad ("total track is {0:F4} mm - every surface sits at the same vertex, so this is an unlaid-out prescription (all thicknesses zero), not a system that can be housed" -f $zc)
}

# ---------- gate 5: elements ----------
# An element starts where glass begins OR where the glass CHANGES inside a
# cemented group. Counting a whole cemented group as one element refused a
# cemented apochromatic triplet as "only 1 element - nothing to seat in a
# barrel" (test cases B07/C09), which is plainly a barrel-mounted objective.
$elements = 0; $prevMat = ""; $glasses = @()
for ($s = 1; $s -lt $imgSurf; $s++) {
    $mat = "$($lde.GetSurfaceAt($s).Material)".Trim()
    if ($mat -ne "") {
        if ($prevMat -eq "" -or $prevMat -ne $mat) { $elements++ }
        if ($glasses -notcontains $mat) { $glasses += $mat }
    }
    $prevMat = $mat
}
if ($elements -lt 2) { Bad "only $elements element(s) detected - nothing to seat in a barrel" }
if ($elements -gt 10) { Iffy "$elements elements - ODX export gets slow and fragile" }

# ---------- gate 5b: FINITE CLEAR APERTURES ----------
# Every downstream artefact is built from surface rims: the bore radius, the
# seat rings, the beam-envelope check, the ODX solid. A semi-diameter that
# reads back as Infinity means OpticStudio could not determine that surface's
# clear aperture at all, so none of those can be computed.
#
# Found by the 2026-07-26 loop sweep on B22 (a Double Gauss opened to f/1.0):
# surfaces 3 and 5 went infinite while their neighbours read 66 mm and 112 mm,
# i.e. the aperture had been widened past what the prescription supports. The
# failure surfaced two stages later as "layout is not valid JSON: Expecting
# value: line 1 column 248" -- because the layout serialised the bare token
# "inf" -- which names neither the surface nor the cause. Reject it here.
$badSD = @()
for ($s = 1; $s -lt $imgSurf; $s++) {
    $sdv = [double]$lde.GetSurfaceAt($s).SemiDiameter
    if ([double]::IsInfinity($sdv) -or [double]::IsNaN($sdv)) { $badSD += $s }
}
if ($badSD.Count -gt 0) {
    Bad ("surface(s) " + ($badSD -join ",") + " have a non-finite semi-diameter - " +
         "the clear apertures are undefined, so no bore or seat radius can be derived")
}

# ---------- gate 5c: CLEAR RADIUS vs SURFACE RADIUS ----------
# A spherical surface cannot have a clear semi-diameter larger than its radius
# of curvature -- that is more than a hemisphere, and there is no such surface
# to build. Speos refuses it on IMPORT:
#     Error: Speos  Surface clear radius too large.
#     Clear radius is larger than maximum radius possible for surface
#     'Lens_1-2 - Back Face'.
#
# MEASURED 2026-07-26 on B25 (zebase opened up for the fast-f/number defect):
# surface 1 has R=25.747 and sd=26.265. The ODX import failed, but the wire
# script only LOGGED odx.StatusInfo without testing it, so it carried on to
# odx.Detectors.Item[0] and died with "Property 'Detectors' (Sensors) is not
# defined" -- five stages and about four minutes after the real cause, and
# naming none of it.
#
# Same family as gate 5b (an aperture opened past what the prescription
# supports) but the milder form: the semi-diameter is finite, just
# geometrically impossible. Scanned all 77 layouts: exactly two systems trip
# this, B25 and C30, and both already failed later anyway - so it is a pure
# saving, with no case changing verdict for the worse.
$sdOverR = @()
for ($s = 1; $s -lt $imgSurf; $s++) {
    $srow = $lde.GetSurfaceAt($s)
    $rad = [double]$srow.Radius
    $sdv = [double]$srow.SemiDiameter
    if ([double]::IsInfinity($rad) -or [double]::IsNaN($rad)) { continue }  # flat
    if ([double]::IsInfinity($sdv) -or [double]::IsNaN($sdv)) { continue }  # gate 5b
    if ($rad -ne 0 -and $sdv -gt [math]::Abs($rad)) {
        $sdOverR += ("surf {0} sd={1:F3} > |R|={2:F3}" -f $s, $sdv, [math]::Abs($rad))
    }
}
if ($sdOverR.Count -gt 0) {
    Bad ("clear radius exceeds the surface radius on " + ($sdOverR -join ", ") +
         " - more than a hemisphere, so Speos refuses the .odx import with " +
         "'Surface clear radius too large'")
}

# ---------- gate 6: ODX APERTURE CONSISTENCY (the cameralens14 killer) ----------
# CRITICAL DISTINCTION, found by validating against known outcomes:
#   FloatingAperture = the DEFAULT, which merely tracks the semi-diameter. Its
#     value differs front-to-back on every normal lens, and Speos accepts it.
#     rearstop31 and wideangle32 both use it and both imported fine.
#   CircularAperture / Rectangular / etc = an EXPLICIT user aperture. THIS is
#     what Speos rejects when the two faces disagree (cameralens14).
# So: only compare when an explicit aperture is present. A naive
# compare-everything check blocks 2 of 6 known-good systems.
$IMPLICIT = @("None", "none", "FloatingAperture")
function Get-ApSig {
    param($row)
    try {
        $ap = $row.ApertureData
        $t = "$($ap.CurrentType)"
        if ($IMPLICIT -contains $t) { return "implicit" }
        $sig = $t
        try {
            $cs = $ap.CurrentTypeSettings
            foreach ($p in @("MinimumRadius","MaximumRadius","XHalfWidth","YHalfWidth",
                             "ApertureXDecenter","ApertureYDecenter")) {
                $prop = $cs.PSObject.Properties[$p]
                if ($null -ne $prop) { $sig += ("|{0}={1:F6}" -f $p, [double]$prop.Value) }
            }
        } catch { }
        return $sig
    } catch { return "unknown" }
}
$apMismatch = @()
$s = 1
while ($s -lt $imgSurf) {
    $mat = "$($lde.GetSurfaceAt($s).Material)".Trim()
    if ($mat -eq "") { $s++; continue }
    $first = $s
    while ($s -lt $imgSurf -and "$($lde.GetSurfaceAt($s).Material)".Trim() -ne "") { $s++ }
    $last = $s
    $a = Get-ApSig $lde.GetSurfaceAt($first)
    $b = Get-ApSig $lde.GetSurfaceAt($last)
    # both implicit (floating/none) is the normal case - Speos is fine with it
    if (-not ($a -eq "implicit" -and $b -eq "implicit") -and $a -ne $b) {
        $apMismatch += "surf $first '$a' vs surf $last '$b'"
    }
    $s++
}
if ($apMismatch.Count -gt 0) {
    Bad ("ODX aperture mismatch, Speos will refuse the import: " + ($apMismatch -join " ; "))
}

# ---------- gate 6b: UNSUPPORTED APERTURE TYPE ----------
# Gate 6 above asks whether the two faces of an element AGREE. That is not the
# only way the ODX bridge refuses an aperture: some TYPES are not importable at
# all, however consistently they are applied.
#
# MEASURED 2026-07-26 on C17, a case the suite expected to PASS: matched
# central obscurations (OBSC 2.0 on both faces of element 1) sailed through
# gate 6 -- they agree perfectly -- and then the export died with
#     Error: Unsupported aperture type - surface 1... aborting!
#     (ec:UnsupportedGeometry)
# That is a FALSE PROCEED: the gate accepted a job that cannot work, and only
# a 13 s ODX export revealed it. C17's ground truth ("matched obscurations are
# legal") was written from intent; the exporter disagrees.
#
# Scope of the claim, stated honestly: CIRCULAR obscuration is MEASURED to
# fail. Other obscuration types are rejected by INFERENCE -- they are the same
# geometry (a central blocker punching a hole in the clear aperture), which
# neither the exporter nor the seated-barrel archetype models. If a future
# system proves one of them importable, narrow this rule rather than widen the
# suite. Deliberately a deny-list on obscurations, NOT an allow-list of known
# good types: every gate bug found in this codebase so far has been a false
# HALT, so refusing only the proven-bad class is the safer error.
$badApType = @()
for ($s = 1; $s -lt $imgSurf; $s++) {
    $apt = ""
    try { $apt = "$($lde.GetSurfaceAt($s).ApertureData.CurrentType)" } catch { }
    if ($apt -match "Obscuration") { $badApType += ("surf {0} '{1}'" -f $s, $apt) }
}
if ($badApType.Count -gt 0) {
    Bad ("unsupported ODX aperture type (obscuration) on " +
         ($badApType -join ", ") + " - the Speos .odx export aborts with " +
         "'Unsupported aperture type ... (ec:UnsupportedGeometry)' regardless " +
         "of whether the two faces agree")
}

# ---------- gate 7: archetype -- mechanically housed objective? ----------
$BIO = @("AQUEOUS","VITREOUS","CORNEA","HUMOR","RETINA","EYE")
$bioHits = @()
foreach ($g in $glasses) {
    $gu = $g.ToUpper()
    foreach ($b in $BIO) { if ($gu -like "*$b*") { $bioHits += $g; break } }
}
if ($bioHits.Count -gt 0) {
    Bad ("media look biological (" + ($bioHits -join ",") + ") - not a mechanically housed assembly")
}
$imgR = [double]$lde.GetSurfaceAt($imgSurf).Radius
if (-not [double]::IsInfinity($imgR) -and [math]::Abs($imgR) -gt 1e-6) {
    Bad ("image surface is CURVED (R={0:F2}) - the workflow assumes a flat detector" -f $imgR)
}
$modelGlass = @($glasses | Where-Object { $_ -match "^[0-9]" })
if ($modelGlass.Count -gt 0) {
    Iffy ("model glasses present (" + ($modelGlass -join ",") + ") - index/dispersion may not survive the ODX bridge")
}

$app.CloseApplication()

# ---------- verdict ----------
$verdict = "GO"
if ($fail.Count -gt 0) { $verdict = "NO-GO" }
elseif ($warn.Count -gt 0) { $verdict = "GO-WITH-WARNINGS" }

Write-Output ""
Write-Output ("PREFLIGHT: " + (Split-Path $LensFile -Leaf))
Write-Output ("  title   : $title")
Write-Output ("  config  : {0} surfaces, {1} elements, max field {2:F1} deg, glasses: {3}" -f $n, $elements, $maxField, ($glasses -join ","))
Write-Output ("  VERDICT : $verdict")
foreach ($f in $fail) { Write-Output "    BLOCK  $f" }
foreach ($w in $warn) { Write-Output "    warn   $w" }

if ($OutJson -ne "") {
    $fj = ($fail | ForEach-Object { '"' + ($_ -replace '\\','\\' -replace '"','\"') + '"' }) -join ","
    $wj = ($warn | ForEach-Object { '"' + ($_ -replace '\\','\\' -replace '"','\"') + '"' }) -join ","
    $json = '{"lens":"' + ($LensFile -replace '\\','/') + '","title":"' + ($title -replace '"','') +
        '","verdict":"' + $verdict + '","surfaces":' + $n + ',"elements":' + $elements +
        ',"maxField":' + $maxField + ',"blocks":[' + $fj + '],"warnings":[' + $wj + ']}'
    $json | Out-File -Encoding utf8 $OutJson
}
if ($verdict -eq "NO-GO") { exit 2 }
exit 0
