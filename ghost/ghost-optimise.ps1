# ghost-optimise.ps1 -- minimise DOUBLE-BOUNCE ghost focus on a COPY of a lens.
#
# Method follows the Ansys note "Stray Light Analysis with Ghost Focus
# Generator": rank double-bounce ghosts, then drive the worst IMAGE ghosts with
# GPIM operands targeted to 0, which pushes the ghost image distance towards
# infinity so its energy is dispersed at the image plane rather than focused on
# it. The article is explicit that GPIM is optimised IN ADDITION TO the original
# merit function -- that is what keeps the design inside spec, and it is the
# single most important property of the method.
#
# WHAT THIS CANNOT DO. GPIM disperses foci; it does not remove Fresnel-fixed
# energy (lib/kpi.py says the same). The acceptance test is therefore PEAK ghost
# concentration, never total ghost flux. A run that leaves total flux unchanged
# is behaving correctly.
#
# THE ORIGINAL IS NEVER TOUCHED. Everything happens on <slug>-ghost.zmx.
#
# NO MERIT FUNCTION IS A REFUSAL, NOT A DEFAULT. The bundled sample lenses ship
# a single BLNK operand and MF = 0 -- they carry no design constraints at all.
# Optimising GPIM against that is unconstrained optimisation, and this repo has
# already measured where that leads: lib/guard.py records a GPIM-optimised
# Double Gauss whose edge spot grew +49.5% polychromatically while a
# single-wavelength check reported +4.2%. So when no real merit function is
# present this script SYNTHESISES an explicit design-intent guard set (hold
# EFFL, hold TOTR, hold polychromatic spot across the field) and stamps
# `meritSynthesised: true` in the output. A synthesised guard is a stated
# assumption, not a silent one.
#
# SAMPLING. Two domains are sampled here and both are gated:
#   ghosts -- enumerated EXHAUSTIVELY over ordered pairs, so there is no gap.
#             Only the top N are DRIVEN, so every pair is re-measured afterwards:
#             an optimiser will happily worsen a ghost nobody constrained.
#   field  -- image quality is CONSTRAINED at a few fields but VERIFIED on a
#             dense grid computed outside the optimiser. Constraining at 3
#             fields and reporting those same 3 fields would report only what
#             the optimiser was told to protect.
param(
    [Parameter(Mandatory=$true)][string]$LensFile,
    [Parameter(Mandatory=$true)][string]$OutJson,
    [Parameter(Mandatory=$true)][string]$Slug,
    [int]$TopN = 3,                 # how many image ghosts to drive
    [double]$GhostWeight = 1.0,     # weight on each injected GPIM operand
    [int]$DenseFields = 11,         # verification grid across the field
    # Focal length is a HARD constraint, and 10.0 was not hard enough. At the
    # default ghost weight, a 3.85% EFFL drift costs ~0.015 of relative merit
    # while the ghost gain is worth ~0.85, so the optimiser sells focal length
    # for ghosts every time: longbore-f8 drifted -3.85% and fast-f2p5 -1.68%,
    # and in both cases EFFL was the ONLY failing gate -- the ghosts improved
    # -61.0% and -12.7% with image quality intact. A constraint that loses to
    # the thing it is meant to bound is decorative, which is the same defect
    # class as the unnormalised weights this file already documents.
    [double]$EfflWeight = 1000.0,
    # The imaging BEAM must not grow against the element rims that set the
    # barrel bore. survey/make-survey-mech.py fits "bore" and "ring" sections to
    # the element rim and explicitly refuses to grow them ("widening them breaks
    # the seat"), so a design whose beam swells inside an unchanged rim produces
    # a barrel that obstructs its own imaging beam -- runner.py then HALTS with
    # `mech envelope FAILS by N mm`. That is exactly what happened to fast-f2p5:
    # all four optical gates passed and the design was unmeasurable end to end.
    # Holding real ray heights at their baseline keeps the beam inside the rims
    # the mechanics will be cut to.
    [double]$EnvelopeWeight = 100.0,
    [switch]$VaryThickness          # also vary airspaces, not just curvatures
)

$LOG = [IO.Path]::ChangeExtension($OutJson, ".log")
"" | Out-File -Encoding utf8 $LOG
function Stage($m) { $m | Out-File -Encoding utf8 -Append $LOG; Write-Output $m }

. "$(Split-Path -Parent $PSScriptRoot)\lib\settings.ps1"
Import-ZOSAPI

$TOPT = [ZOSAPI.Editors.MFE.MeritOperandType]
$TCOL = [ZOSAPI.Editors.MFE.MeritColumn]
# [EnumType]::$var silently yields $null and leaves the operand BLNK.
# Always [Enum]::Parse() -- s0-ghosts.ps1 carries the same warning.
$GPIM = [Enum]::Parse($TOPT, "GPIM")
$EFFL = [Enum]::Parse($TOPT, "EFFL")
$RSCE = [Enum]::Parse($TOPT, "RSCE")
$TOTR = [Enum]::Parse($TOPT, "TOTR")
$REAY = [Enum]::Parse($TOPT, "REAY")
$P1 = [Enum]::Parse($TCOL,"Param1"); $P2 = [Enum]::Parse($TCOL,"Param2"); $P3 = [Enum]::Parse($TCOL,"Param3")

# ---- work on a COPY, always (task b)
$workDir = Split-Path -Parent $OutJson
$copy = Join-Path $workDir ("{0}-ghost.zmx" -f $Slug)
Copy-Item -LiteralPath $LensFile -Destination $copy -Force
Stage "copy      : $copy"

$conn = New-Object ZOSAPI.ZOSAPI_Connection
$app = $conn.CreateNewApplication()
if ($null -eq $app) { throw "CreateNewApplication returned null (licence?)" }
try {
    $sys = $app.PrimarySystem
    if (-not $sys.LoadFile($copy, $false)) { throw "LoadFile failed: $copy" }
    $lde = $sys.LDE
    $mfe = $sys.MFE
    $imgSurf = [int]$lde.NumberOfSurfaces - 1

    # primary wavelength: IWavelengths.Primary does not exist in 2026 R1 and
    # reads back $null, which [int] turns into 0 with no error.
    $wave = 1
    for ($i = 1; $i -le [int]$sys.SystemData.Wavelengths.NumberOfWavelengths; $i++) {
        if ($sys.SystemData.Wavelengths.GetWavelength($i).IsPrimary) { $wave = $i; break }
    }

    # ---- does a REAL merit function exist? (task b / task c)
    $nOps = [int]$mfe.NumberOfOperands
    $realOps = 0
    for ($i = 1; $i -le $nOps; $i++) {
        $t = $mfe.GetOperandAt($i).Type.ToString()
        if ($t -ne "BLNK" -and $t -ne "DMFS") { $realOps++ }
    }
    $mfOrigBefore = [double]$mfe.CalculateMeritFunction()
    Stage "merit fn  : $nOps operand(s), $realOps substantive, MF=$mfOrigBefore"

    # ---- helpers that evaluate ONE operand standalone.
    # GetOperandValue does not touch the merit function, which is what lets the
    # original MF stay in place while these are read (task b).
    function Get-Spot([double]$hy) {
        # wave=0 is POLYCHROMATIC and is deliberate: lib/guard.py records a
        # single-wavelength check reporting +4.2% where the polychromatic truth
        # was +49.5%. Never "fix" this to $wave.
        [double]$mfe.GetOperandValue($RSCE, 0, 0, 0.0, $hy, 0, 0, 6, 0)
    }
    function Get-Effl { [double]$mfe.GetOperandValue($EFFL, 1, $wave, 0,0,0,0,0,0) }
    function Get-Totr { [double]$mfe.GetOperandValue($TOTR, 0, 0, 0,0,0,0,0,0) }
    function Get-Gpim([int]$s1, [int]$s2, [int]$mode) {
        [double]$mfe.GetOperandValue($GPIM, $s1, $s2, $mode, 0,0,0,0,0)
    }
    # Extreme rays of the imaging beam -- axial marginal and full-field upper /
    # lower marginal. These three bound the meridional envelope at a surface,
    # which is the quantity the bore is cut against.
    $ENV_RAYS = @(@(0.0,1.0), @(1.0,1.0), @(1.0,-1.0))
    function Get-Reay([int]$s, [double]$hy, [double]$py) {
        [double]$mfe.GetOperandValue($REAY, $s, $wave, 0.0, $hy, 0.0, $py, 0, 0)
    }

    # dense field grid -- the VERIFICATION domain, wider than what is constrained
    $denseHy = @()
    for ($k = 0; $k -lt $DenseFields; $k++) {
        $denseHy += [math]::Round($k / [double]($DenseFields - 1), 4)
    }

    function Measure-Fields {
        $o = @()
        foreach ($hy in $denseHy) { $o += ,@{ hy = $hy; spot = (Get-Spot $hy) } }
        return $o
    }

    # ---- refractive interfaces (exclude object, stop-only and image)
    $interfaces = @()
    for ($s = 1; $s -lt $imgSurf; $s++) {
        $mat = $lde.GetSurfaceAt($s).Material
        $matPrev = $lde.GetSurfaceAt($s - 1).Material
        if ($mat -ne "" -or $matPrev -ne "") { $interfaces += $s }
    }
    Stage "interfaces: $($interfaces -join ',')"

    # ---- EXHAUSTIVE ghost enumeration (no sampling gap by construction)
    function Measure-Ghosts {
        $g = @()
        foreach ($j in $interfaces) {
            foreach ($i in $interfaces) {
                if ($i -le $j) { continue }
                foreach ($mode in 0, 1) {
                    $g += ,@{ s1 = $i; s2 = $j; mode = $mode; v = (Get-Gpim $i $j $mode) }
                }
            }
        }
        return $g
    }

    function Measure-Envelope {
        $e = @()
        foreach ($s in $interfaces) {
            foreach ($r in $ENV_RAYS) {
                $e += ,@{ surf = $s; hy = $r[0]; py = $r[1]; y = (Get-Reay $s $r[0] $r[1]) }
            }
        }
        return $e
    }

    $ghostsBefore = Measure-Ghosts
    $envBefore = Measure-Envelope
    $fieldsBefore = Measure-Fields
    $efflBefore = Get-Effl
    $totrBefore = Get-Totr
    $nz = ($ghostsBefore | Where-Object { [math]::Abs($_.v) -gt 1e-12 }).Count
    if ($ghostsBefore.Count -gt 0 -and $nz -eq 0) {
        throw "GUARD FAILED [ghost-opt] all $($ghostsBefore.Count) GPIM values are 0 - the enumeration measured nothing"
    }
    Stage "ghosts    : $($ghostsBefore.Count) pairs, $nz non-zero"
    Stage "baseline  : EFFL=$efflBefore TOTR=$totrBefore"

    # ---- pick the worst IMAGE ghosts (mode 1). The article prioritises image
    # ghosts over pupil ghosts: a pupil ghost affects uniformity, an image ghost
    # lands on the detector.
    $targets = $ghostsBefore |
        Where-Object { $_.mode -eq 1 } |
        Sort-Object -Property @{Expression={ [math]::Abs($_.v) }} -Descending |
        Select-Object -First $TopN
    Stage "driving   : $(($targets | ForEach-Object { "($($_.s1),$($_.s2))=$([math]::Round($_.v,6))" }) -join ' ')"

    # ---- synthesise a design-intent guard when no real MF exists
    $synthesised = $false
    if ($realOps -eq 0) {
        $synthesised = $true
        Stage "WARNING   : no substantive merit function - synthesising design-intent guard"
        while ($mfe.NumberOfOperands -gt 1) { [void]$mfe.RemoveOperandAt(1) }
        # NORMALISE EVERY WEIGHT BY 1/target^2.
        #
        # A merit function sums weight*(value-target)^2 in the operands' own
        # units, so raw weights compare a spot residual in millimetres against a
        # GPIM residual in inverse millimetres and the smaller quantity simply
        # loses. Measured on example-triplet with raw weights: the on-axis spot
        # residual contributed ~1e-6 against GPIM's ~7e-4, so the guard was
        # ~700x too weak and the optimiser grew the on-axis spot +46.7% -- which
        # is the +49.5% edge degradation lib/guard.py already records from the
        # Double Gauss attempt, reproduced exactly.
        #
        # Dividing by target^2 makes every term a RELATIVE error, so a weight is
        # a statement of priority rather than an accident of units.
        function Norm([double]$t, [double]$w) {
            if ([math]::Abs($t) -lt 1e-12) { return $w }
            return $w / ($t * $t)
        }
        # hold focal length hard
        $r = $mfe.AddOperand(); [void]$r.ChangeType($EFFL)
        $r.GetOperandCell($P1).IntegerValue = 1
        $r.GetOperandCell($P2).IntegerValue = $wave
        $r.Target = $efflBefore; $r.Weight = (Norm $efflBefore $EfflWeight)
        # hold overall length
        $r = $mfe.AddOperand(); [void]$r.ChangeType($TOTR)
        $r.Target = $totrBefore; $r.Weight = (Norm $totrBefore 1.0)
        # hold polychromatic spot ACROSS the field, not just on axis and corner
        foreach ($f in $fieldsBefore) {
            $r = $mfe.AddOperand(); [void]$r.ChangeType($RSCE)
            $r.GetOperandCell($P1).IntegerValue = 0
            $r.GetOperandCell($P2).IntegerValue = 0
            $r.GetOperandCell([Enum]::Parse($TCOL,"Param4")).DoubleValue = $f.hy
            $r.Target = $f.spot; $r.Weight = (Norm $f.spot 1.0)
        }
        # hold the imaging beam inside the rims the barrel will be cut to
        foreach ($e in $envBefore) {
            $r = $mfe.AddOperand(); [void]$r.ChangeType($REAY)
            $r.GetOperandCell($P1).IntegerValue = $e.surf
            $r.GetOperandCell($P2).IntegerValue = $wave
            $r.GetOperandCell([Enum]::Parse($TCOL,"Param4")).DoubleValue = $e.hy
            $r.GetOperandCell([Enum]::Parse($TCOL,"Param6")).DoubleValue = $e.py
            $r.Target = $e.y; $r.Weight = (Norm $e.y $EnvelopeWeight)
        }
        [void]$mfe.CalculateMeritFunction()
        $mfOrigBefore = [double]$mfe.CalculateMeritFunction()
        Stage "synth MF  : $($mfe.NumberOfOperands) operands, MF=$mfOrigBefore"
    }
    $nGuard = [int]$mfe.NumberOfOperands

    # ---- inject the GPIM operands (task b)
    $injected = @()
    foreach ($t in $targets) {
        $r = $mfe.AddOperand()
        if (-not $r.ChangeType($GPIM)) { Stage "ChangeType failed ($($t.s1),$($t.s2))"; continue }
        $r.GetOperandCell($P1).IntegerValue = $t.s1
        $r.GetOperandCell($P2).IntegerValue = $t.s2
        $r.GetOperandCell($P3).IntegerValue = 1
        $r.Target = 0.0                 # target 0 -> ghost distance to infinity
        # Target is 0, so it cannot normalise against itself -- scale on the
        # BASELINE value instead, which puts this residual on the same relative
        # footing as the guard operands above. Without this the ghost term
        # dominates by orders of magnitude and the guard is decorative.
        $gw = if ([math]::Abs($t.v) -gt 1e-12) { $GhostWeight / ($t.v * $t.v) } else { $GhostWeight }
        $r.Weight = $gw
        $injected += ,@{ s1 = $t.s1; s2 = $t.s2 }
    }
    $mfInjected = [double]$mfe.CalculateMeritFunction()
    Stage "injected  : $($injected.Count) GPIM operands, MF now $mfInjected"

    # ---- variables. Without these the optimiser has no freedom and returns
    # the input unchanged, which would read as "no ghost improvement possible".
    $nVar = 0
    for ($s = 1; $s -lt $imgSurf; $s++) {
        $surf = $lde.GetSurfaceAt($s)
        if ([math]::Abs([double]$surf.Radius) -lt 1e7) {
            [void]$surf.RadiusCell.MakeSolveVariable(); $nVar++
        }
        if ($VaryThickness -and $surf.Material -eq "") {
            [void]$surf.ThicknessCell.MakeSolveVariable(); $nVar++
        }
    }
    Stage "variables : $nVar"
    if ($nVar -eq 0) { throw "GUARD FAILED [ghost-opt] no variables set - the optimiser cannot move anything" }

    # ---- optimise
    $opt = $sys.Tools.OpenLocalOptimization()
    if ($null -eq $opt) { throw "OpenLocalOptimization returned null" }
    $opt.Algorithm = [ZOSAPI.Tools.Optimization.OptimizationAlgorithm]::DampedLeastSquares
    $opt.Cycles = [ZOSAPI.Tools.Optimization.OptimizationCycles]::Automatic
    $opt.NumberOfCores = 8
    [void]$opt.RunAndWaitForCompletion()
    $mfOptimised = [double]$opt.CurrentMeritFunction
    [void]$opt.Close()   # Close() returns a bool; unvoided it prints "True"
    Stage "optimised : MF $mfInjected -> $mfOptimised"

    # ---- remove the GPIM operands and re-evaluate the ORIGINAL merit function
    # on the OPTIMISED design. This is the design-integrity number (task c):
    # comparing $mfOrigBefore with $mfOrigAfter compares like with like, whereas
    # comparing against $mfOptimised would compare a merit function to a
    # different merit function and call the difference a result.
    while ($mfe.NumberOfOperands -gt $nGuard) {
        [void]$mfe.RemoveOperandAt([int]$mfe.NumberOfOperands)
    }
    $mfOrigAfter = [double]$mfe.CalculateMeritFunction()
    Stage "orig MF   : $mfOrigBefore -> $mfOrigAfter"

    # ---- re-measure EVERYTHING outside the optimiser
    $ghostsAfter = Measure-Ghosts
    $envAfter = Measure-Envelope
    $fieldsAfter = Measure-Fields
    $efflAfter = Get-Effl
    $totrAfter = Get-Totr

    [void]$sys.Save()
    Stage "saved     : $copy"

    # ---- emit
    function G-Json($rows) {
        ($rows | ForEach-Object {
            $v = $_.v
            $vs = if ([double]::IsNaN($v)) { "null" } else { "$v" }
            '{{"surf1":{0},"surf2":{1},"mode":{2},"value":{3}}}' -f $_.s1, $_.s2, $_.mode, $vs
        }) -join ","
    }
    function E-Json($rows) {
        ($rows | ForEach-Object {
            $v = $_.y
            $vs = if ([double]::IsNaN($v)) { "null" } else { "$v" }
            '{{"surf":{0},"hy":{1},"py":{2},"y":{3}}}' -f $_.surf, $_.hy, $_.py, $vs
        }) -join ","
    }
    function F-Json($rows) {
        ($rows | ForEach-Object {
            $v = $_.spot
            $vs = if ([double]::IsNaN($v)) { "null" } else { "$v" }
            '{{"hy":{0},"spot":{1}}}' -f $_.hy, $vs
        }) -join ","
    }
    $inj = ($injected | ForEach-Object { '{{"surf1":{0},"surf2":{1}}}' -f $_.s1, $_.s2 }) -join ","

    ('{"slug":"' + $Slug + '"' +
     ',"lensOriginal":"' + ($LensFile -replace '\\','/') + '"' +
     ',"lensOptimised":"' + ($copy -replace '\\','/') + '"' +
     ',"meritSynthesised":' + $(if ($synthesised) {"true"} else {"false"}) +
     ',"meritOperandsOriginal":' + $realOps +
     ',"topN":' + $TopN + ',"ghostWeight":' + $GhostWeight +
     ',"variables":' + $nVar +
     ',"injected":[' + $inj + ']' +
     ',"meritOriginalBefore":' + $mfOrigBefore +
     ',"meritOriginalAfter":' + $mfOrigAfter +
     ',"meritWithGhostBefore":' + $mfInjected +
     ',"meritWithGhostAfter":' + $mfOptimised +
     ',"efflBefore":' + $efflBefore + ',"efflAfter":' + $efflAfter +
     ',"totrBefore":' + $totrBefore + ',"totrAfter":' + $totrAfter +
     ',"denseFields":' + $DenseFields +
     ',"fieldsBefore":[' + (F-Json $fieldsBefore) + ']' +
     ',"fieldsAfter":[' + (F-Json $fieldsAfter) + ']' +
     ',"envelopeWeight":' + $EnvelopeWeight +
     ',"envelopeBefore":[' + (E-Json $envBefore) + ']' +
     ',"envelopeAfter":[' + (E-Json $envAfter) + ']' +
     ',"ghostsBefore":[' + (G-Json $ghostsBefore) + ']' +
     ',"ghostsAfter":[' + (G-Json $ghostsAfter) + ']}') | Out-File -Encoding utf8 $OutJson
    Stage "wrote     : $OutJson"
}
finally {
    $app.CloseApplication()
}
