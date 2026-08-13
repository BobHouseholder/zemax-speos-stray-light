# run-queue.ps1 -- drive the remaining survey Speos runs sequentially.
# Each entry: slug + variant; stages survey-config.txt then invokes the
# headless Speos launcher and waits. Stdout is invisible inside Speos, so the
# per-run result file is the source of truth (checked after each).
. "$(Split-Path -Parent $PSScriptRoot)\lib\settings.ps1"
$BASE = Split-Path -Parent $PSScriptRoot
$SURVEY = "$BASE\survey"
$WALL = "$BASE\black-anodize-plausible.anisotropicbsdf"
$LAUNCHER = $SL_SPEOS_LAUNCHER

$queue = @(
    @{ slug = "rearstop31";   pre = "rear"; variant = "redesign" },
    @{ slug = "petzval4";     pre = "petz"; variant = "base" },
    @{ slug = "petzval4";     pre = "petz"; variant = "redesign" },
    @{ slug = "cameralens14"; pre = "caml"; variant = "base" },
    @{ slug = "cameralens14"; pre = "caml"; variant = "redesign" }
)

foreach ($q in $queue) {
    $slug = $q.slug; $d = "$SURVEY\systems\$slug"
    $p = Get-Content "$d\$slug-params.json" -Raw | ConvertFrom-Json
    $step = if ($q.variant -eq "base") { "$d\$slug-baseline.step" } else { "$d\$slug-seated.step" }
    $edge = if ($q.variant -eq "base") { "NONE" } else { "EDGEBLACK" }
    $sfx = "$($q.pre)_$($q.variant)"
    $log = "$d\result-$sfx.txt"
    if (Test-Path $log) { Write-Output "SKIP $sfx (already run)"; continue }
    $cfg = @("$d\$slug.odx", "$d\$slug-speos.scdocx", $step, $sfx, $WALL,
             $p.zImg, $p.rDisc, $p.zCatch, $p.strayDeg, $p.zSrc, $p.rSrc, $p.wave,
             $edge, $log) -join "`n"
    Set-Content -Encoding utf8 "$SURVEY\survey-config.txt" $cfg
    # This is the ONE launcher that does not go through pst_read, which is
    # where every Python driver exports these. wire-survey.py stops with a
    # named error if either is missing rather than guessing a path.
    $env:SL_SURVEY_CONFIG = "$SURVEY\survey-config.txt"
    $env:SL_SURVEY_DIR = $SURVEY
    $env:SL_ROOT = $BASE
    Write-Output "RUN  $sfx  (mech $(Split-Path $step -Leaf), edge $edge)"
    & $LAUNCHER /RunScript="$SURVEY\wire-survey.py" /Headless=True /Splash=False `
        /Welcome=False /ExitAfterScript=True | Out-Null
    if (Test-Path $log) {
        $bad = Select-String -Path $log -Pattern "FATAL" -Quiet
        $end = Select-String -Path $log -Pattern "wire-survey end" -Quiet
        Write-Output "     -> log written; fatal=$bad complete=$end"
    } else {
        Write-Output "     -> NO RESULT FILE (crashed)"
    }
}
Write-Output "queue done"
