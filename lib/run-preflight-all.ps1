# run-preflight-all.ps1 -- validate the preflight gate against known outcomes.
# ASCII-ONLY (PS 5.1 reads .ps1 as ANSI without a BOM).
# The two systems that previously wasted work MUST come back NO-GO:
#   cameralens14 -- Speos rejected the .odx (front/back apertures differ)
#   eye20        -- no mechanical barrel (biological media, curved retina)
# The six that completed the loop MUST come back GO / GO-WITH-WARNINGS.
$BASE = Split-Path -Parent $PSScriptRoot
$PF = "$BASE\lib\preflight.ps1"

$systems = @(
    @{ slug = "dg14";         lens = "$BASE\Double Gauss 28 degree field.zmx";                expect = "GO" },
    @{ slug = "cooke20";      lens = "$BASE\cooke\Cooke 40 degree field.zmx";                 expect = "GO" },
    @{ slug = "tessar25";     lens = "$BASE\survey\systems\tessar25\tessar25.zmx";            expect = "GO" },
    @{ slug = "petzval4";     lens = "$BASE\survey\systems\petzval4\petzval4.zmx";            expect = "GO" },
    @{ slug = "rearstop31";   lens = "$BASE\survey\systems\rearstop31\rearstop31.zmx";        expect = "GO" },
    @{ slug = "wideangle32";  lens = "$BASE\survey\systems\wideangle32\wideangle32.zmx";      expect = "GO" },
    @{ slug = "cameralens14"; lens = "$BASE\survey\systems\cameralens14\cameralens14.zmx";    expect = "NO-GO" },
    @{ slug = "eye20";        lens = "$BASE\survey\systems\eye20\eye20.zmx";                  expect = "NO-GO" }
)

$results = @()
foreach ($s in $systems) {
    if (-not (Test-Path $s.lens)) { Write-Output "SKIP $($s.slug) (missing)"; continue }
    $json = "$BASE\lib\preflight-$($s.slug).json"
    $out = "$env:TEMP\pf-$($s.slug).txt"
    $p = Start-Process powershell -ArgumentList "-NoProfile","-File","`"$PF`"",
        "-LensFile","`"$($s.lens)`"","-OutJson","`"$json`"" `
        -RedirectStandardOutput $out -PassThru -WindowStyle Hidden
    $ok = $p.WaitForExit(240000)
    if (-not $ok) { try { $p.Kill() } catch {}; Write-Output "$($s.slug): TIMED OUT"; continue }
    Get-Content $out -ErrorAction SilentlyContinue | Where-Object { $_ -match "VERDICT|BLOCK|warn |config" } |
        ForEach-Object { Write-Output "$($s.slug) $_" }
    $verdict = "ERROR"
    if (Test-Path $json) {
        $m = [regex]::Match((Get-Content $json -Raw), '"verdict":"([^"]+)"')
        if ($m.Success) { $verdict = $m.Groups[1].Value }
    }
    $hit = if ($verdict -eq $s.expect -or ($s.expect -eq "GO" -and $verdict -eq "GO-WITH-WARNINGS")) { "PASS" } else { "MISS" }
    $results += ,@{ slug = $s.slug; expect = $s.expect; got = $verdict; hit = $hit }
    Write-Output ""
}

Write-Output "================ preflight validation ================"
$pass = 0
foreach ($r in $results) {
    Write-Output ("  {0,-14} expected {1,-16} got {2,-16} {3}" -f $r.slug, $r.expect, $r.got, $r.hit)
    if ($r.hit -eq "PASS") { $pass++ }
}
Write-Output ("  {0}/{1} correct" -f $pass, $results.Count)
