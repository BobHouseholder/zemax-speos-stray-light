# first-run-lens.ps1 -- write a bundled example design.
# Usage: -OutZmx <path> [-Design triplet|fast|longbore|widefov]
# ASCII-ONLY (see zos-guard.ps1 header for why).
#
# WHY THESE ARE GENERATORS AND NOT SHIPPED .zmx FILES
# ---------------------------------------------------
# The pipeline needs lens prescriptions and none ship with it, because the
# designs this workflow was developed against are stock OpticStudio sample
# files and are not ours to redistribute. That rule is absolute and enforced:
# `.zmx` is in build-distribution.py's BANNED_EXT, so no build can ever contain
# one, deliberately or by accident. A bundled example that arrived as a file
# would need a hole in that rule, and the hole would be indistinguishable from
# the mistake it exists to prevent.
#
# So the examples are GENERATED. What ships is arithmetic, and the customer's
# own OpticStudio writes the .zmx on their machine. Nothing is redistributed
# and the ban stays absolute.
#
# WHERE THE NUMBERS COME FROM
# ---------------------------
# Each form below -- Cooke triplet, air-spaced doublet -- is a lens FORM,
# published long ago and in every optical design text since. Forms are not
# ownable. THE RADII AND SPACINGS ARE OURS: rounded starting points, optimised
# once by OpticStudio against a default RMS spot-size merit function with the
# focal length targeted, a total-track target where the archetype calls for
# one, and a minimum-airspace constraint. Nothing is transcribed from a sample.
#
# The numbers are FIXED rather than optimised at run time on purpose. An
# optimiser's stopping point depends on version, core count and algorithm, so
# generating a design fresh on each machine would produce a slightly different
# lens everywhere -- and then "this example is known to pass preflight, and to
# return this stray-light number" would be a hope rather than a fact. Ship the
# answer, not the search.
#
# HOW THE SET WAS CHOSEN
# ----------------------
# On OPTICAL grounds only -- f/number, field, track length, element count --
# and NOT on the stray-light answer each was expected to give. Five separate
# proxies for predicting this workflow's benefit were tested against
# measurement and all five were refuted, so designing toward a desired spread
# would be guessing dressed as method. The archetypes were fixed first, every
# one was run, and every result is published including the unflattering ones.
param(
    [Parameter(Mandatory=$true)][string]$OutZmx,
    [ValidateSet("triplet","fast","longbore","widefov")]
    [string]$Design = "triplet"
)
. "$PSScriptRoot\zos-guard.ps1"
Assert-SeatAvailable -Fix
. "$PSScriptRoot\settings.ps1"
Import-ZOSAPI

# epd/field drive the system; efl and fno are DOCUMENTATION of what the
# optimised prescription achieves, printed so a mismatch is visible.
$DESIGNS = @{
  triplet = @{
    title = "Example Cooke triplet -- f/5, 50 mm, +/-14 deg"
    epd = 10.0; field = 14.0; efl = 50.0; fno = 5.0; stop = 4
    surf = @(
      @{ r=  20.735168; t= 3.000000; g="N-SK16" },
      @{ r=-352.519657; t= 7.222805; g=""       },
      @{ r= -20.904350; t= 1.200000; g="N-SF2"  },
      @{ r=  16.773995; t= 5.421582; g=""       },
      @{ r=  61.524958; t= 3.500000; g="N-SK16" },
      @{ r= -17.208143; t=41.358228; g=""       }) }
  fast = @{
    title = "Example fast triplet -- f/2.5, 50 mm, +/-10 deg"
    epd = 20.0; field = 10.0; efl = 50.0; fno = 2.5; stop = 4
    surf = @(
      @{ r=  22.971222; t= 6.000000; g="N-SK16" },
      @{ r=-203.186339; t= 7.166176; g=""       },
      @{ r= -23.513720; t= 2.000000; g="N-SF2"  },
      @{ r=  17.053078; t= 5.378032; g=""       },
      @{ r=  47.260555; t= 6.000000; g="N-SK16" },
      @{ r= -19.241019; t=38.134845; g=""       }) }
  longbore = @{
    title = "Example long-bore doublet -- f/8, 200 mm, +/-4 deg"
    epd = 25.0; field = 4.0; efl = 200.0; fno = 8.0; stop = 2
    surf = @(
      @{ r= 119.465853; t=  8.000000; g="N-BK7" },
      @{ r= -83.047910; t=  0.795754; g=""      },
      @{ r= -80.955934; t=  5.000000; g="N-SF2" },
      @{ r=-265.402259; t=191.659450; g=""      }) }
  widefov = @{
    title = "Example wide-field objective -- f/4, 20 mm, +/-30 deg"
    epd = 5.0; field = 30.0; efl = 20.0; fno = 4.0; stop = 4
    surf = @(
      @{ r= -13.172610; t= 2.000000; g="N-SF2"  },
      @{ r= -23.806840; t= 5.383742; g=""       },
      @{ r= -94.890683; t= 4.000000; g="N-SK16" },
      @{ r= -19.148030; t= 0.773395; g=""       },
      @{ r=  23.303211; t= 4.000000; g="N-SK16" },
      @{ r=-138.539613; t=21.833009; g=""       }) }
}

$d = $DESIGNS[$Design]
$conn = New-Object ZOSAPI.ZOSAPI_Connection
$app = $conn.CreateNewApplication()
Assert-Connected $app
$sys = $app.PrimarySystem
$sys.New($false)

$sys.SystemData.Aperture.ApertureType = [ZOSAPI.SystemData.ZemaxApertureType]::EntrancePupilDiameter
$sys.SystemData.Aperture.ApertureValue = [double]$d.epd

# F, d, C -- the standard visible triple, d primary. The pipeline traces at
# 550 nm, but a three-wavelength system is what a real prescription looks like
# and costs nothing here.
$w = $sys.SystemData.Wavelengths
while ($w.NumberOfWavelengths -gt 1) { [void]$w.RemoveWavelength($w.NumberOfWavelengths) }
$w.GetWavelength(1).Wavelength = 0.4861
$w.GetWavelength(1).Weight = 1.0
[void]$w.AddWavelength(0.5876, 1.0)
[void]$w.AddWavelength(0.6563, 1.0)
$w.GetWavelength(2).MakePrimary()

# Angular fields at 0 / 0.707 / 1.0. Preflight requires an ANGLE field type and
# warns below 3 deg; it also BLOCKS at 85 deg or above, because the stray source
# sits ahead of the entrance plane and there is no out-of-field direction left.
$f = $sys.SystemData.Fields
$f.SetFieldType([ZOSAPI.SystemData.FieldType]::Angle)
while ($f.NumberOfFields -gt 1) { [void]$f.RemoveField($f.NumberOfFields) }
$f.GetField(1).Y = 0.0
[void]$f.AddField(0.0, [double]$d.field * 0.707, 1.0)
[void]$f.AddField(0.0, [double]$d.field, 1.0)

# Catalog glasses throughout, never model glasses: preflight warns on a glass
# name beginning with a digit because index and dispersion may not survive the
# ODX bridge into Speos.
$n = $d.surf.Count
$lde = $sys.LDE
while ($lde.NumberOfSurfaces -lt ($n + 2)) { [void]$lde.AddSurface() }
for ($i = 0; $i -lt $n; $i++) {
    $row = $d.surf[$i]
    $srf = $lde.GetSurfaceAt($i + 1)
    $srf.Radius = [double]$row.r
    $srf.Thickness = [double]$row.t
    $srf.Material = [string]$row.g
}
$lde.GetSurfaceAt([int]$d.stop).IsStop = $true

# The image surface inherits the 1e18 thickness of the blank system's last
# surface. Preflight reads the thickness of the surface BEFORE the image for
# back focus, so this does not change the verdict -- but an infinite thickness
# on the detector is meaningless and shows up in every layout plot.
$lde.GetSurfaceAt($n + 1).Thickness = 0.0
$lde.GetSurfaceAt($n + 1).Radius = 0.0

$sys.SystemData.TitleNotes.Title = [string]$d.title

$dir = Split-Path -Parent $OutZmx
if (-not (Test-Path $dir)) { [void](New-Item -ItemType Directory -Force $dir) }
[void]$sys.SaveAs($OutZmx)

# Report from the MODEL rather than from the table above -- if a glass name is
# unavailable in this installation's catalogs, OpticStudio silently leaves the
# surface in air, and the element count below is how that becomes visible.
$elements = 0; $prev = ""; $track = 0.0; $minAir = [double]::MaxValue
for ($s = 1; $s -le $n; $s++) {
    $srf = $lde.GetSurfaceAt($s)
    $mat = "$($srf.Material)".Trim()
    if ($mat -ne "" -and ($prev -eq "" -or $prev -ne $mat)) { $elements++ }
    if ($mat -eq "" -and $s -lt $n) {
        $th = [double]$srf.Thickness
        if ($th -lt $minAir) { $minAir = $th }
    }
    $track += [double]$srf.Thickness
    $prev = $mat
}
Write-Output ""
Write-Output ("EXAMPLE LENS [" + $Design + "]: " + (Split-Path $OutZmx -Leaf))
Write-Output ("  {0}" -f $d.title)
Write-Output ("  {0} surfaces, {1} elements, total track {2:F2} mm, smallest airspace {3:F3} mm" -f `
              $lde.NumberOfSurfaces, $elements, $track, $minAir)
Write-Output ("  written to {0}" -f $OutZmx)
$want = if ($n -ge 6) { 3 } else { 2 }
if ($elements -lt $want) {
    Write-Output ("  WARNING: only $elements element(s) resolved, expected $want -- a glass is " +
                  "missing from this installation's catalogs, and preflight will refuse this file")
}
$app.CloseApplication()
exit 0
