# first-run-lens.ps1 -- write the bundled example design.
# Usage: -OutZmx <path>
# ASCII-ONLY (see zos-guard.ps1 header for why).
#
# WHY THIS EXISTS AS A GENERATOR AND NOT AS A SHIPPED .zmx
# --------------------------------------------------------
# The pipeline needs a lens prescription and none ships with it, because the
# designs this workflow was developed against are stock OpticStudio sample
# files and are not ours to redistribute. That rule is absolute and enforced:
# `.zmx` is in build-distribution.py's BANNED_EXT, so no build can ever contain
# one, deliberately or by accident. A bundled example that arrived as a file
# would need a hole in that rule, and the hole would be indistinguishable from
# the mistake it exists to prevent.
#
# So the example is GENERATED. What ships is arithmetic, and the customer's own
# OpticStudio writes the .zmx on their machine. Nothing is redistributed and
# the ban stays absolute.
#
# WHERE THE NUMBERS COME FROM
# ---------------------------
# A Cooke triplet is a lens FORM -- three air-spaced elements, crown-flint-crown,
# stop in the rear air space -- published in 1893 and in every optical design
# text since. The form is not ownable. THE RADII AND SPACINGS BELOW ARE OURS:
# a rounded starting point, optimised once by OpticStudio against a default RMS
# spot-size merit function with EFL targeted at 50 mm. Nothing here is
# transcribed from a sample file.
#
# The numbers are FIXED rather than optimised at run time on purpose. An
# optimiser's stopping point depends on version, core count and algorithm, so
# generating the design fresh on each machine would produce a slightly
# different lens everywhere -- and then "this example is known to pass
# preflight" would be a hope rather than a fact. Ship the answer, not the search.
#
# The design is deliberately ordinary: f/5, 50 mm focal length, +/-14 degree
# field, catalog glasses, all-spherical, flat image. It is a plain barrel-mounted
# objective, which is exactly the archetype this workflow handles, so a first run
# exercises the pipeline rather than its edge cases.
param(
    [Parameter(Mandatory=$true)][string]$OutZmx
)
. "$PSScriptRoot\zos-guard.ps1"
Assert-SeatAvailable -Fix
. "$PSScriptRoot\settings.ps1"
Import-ZOSAPI

$conn = New-Object ZOSAPI.ZOSAPI_Connection
$app = $conn.CreateNewApplication()
Assert-Connected $app
$sys = $app.PrimarySystem
$sys.New($false)

$sys.SystemData.Aperture.ApertureType = [ZOSAPI.SystemData.ZemaxApertureType]::EntrancePupilDiameter
$sys.SystemData.Aperture.ApertureValue = 10.0

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

# Angular fields at 0 / 0.7 / 1.0 of 14 deg. Preflight requires an ANGLE field
# type and warns below 3 deg; 14 deg also leaves the whole 14-85 deg range
# available for stray-source placement, which is where the measurement happens.
$f = $sys.SystemData.Fields
$f.SetFieldType([ZOSAPI.SystemData.FieldType]::Angle)
while ($f.NumberOfFields -gt 1) { [void]$f.RemoveField($f.NumberOfFields) }
$f.GetField(1).Y = 0.0
[void]$f.AddField(0.0, 9.9, 1.0)
[void]$f.AddField(0.0, 14.0, 1.0)

# ---- the prescription ----
#   surfaces 1-2  crown (+)      N-SK16
#   surfaces 3-4  flint (-)      N-SF2      surface 4 is the stop
#   surfaces 5-6  crown (+)      N-SK16
# Catalog glasses, not model glasses: preflight warns on a glass name beginning
# with a digit because index and dispersion may not survive the ODX bridge.
$lde = $sys.LDE
while ($lde.NumberOfSurfaces -lt 8) { [void]$lde.AddSurface() }
$PRESCRIPTION = @(
    @{ s=1; r=  20.735168; t= 3.000000; g="N-SK16" },
    @{ s=2; r=-352.519657; t= 7.222805; g=""       },
    @{ s=3; r= -20.904350; t= 1.200000; g="N-SF2"  },
    @{ s=4; r=  16.773995; t= 5.421582; g=""       },
    @{ s=5; r=  61.524958; t= 3.500000; g="N-SK16" },
    @{ s=6; r= -17.208143; t=41.358228; g=""       }
)
foreach ($row in $PRESCRIPTION) {
    $srf = $lde.GetSurfaceAt($row.s)
    $srf.Radius = [double]$row.r
    $srf.Thickness = [double]$row.t
    $srf.Material = [string]$row.g
}
$lde.GetSurfaceAt(4).IsStop = $true

# The image surface inherits the 1e18 thickness of the blank system's last
# surface. Preflight reads the thickness of the surface BEFORE the image for
# back focus, so this does not change the verdict -- but an infinite thickness
# on the detector is meaningless and shows up in every layout plot.
$lde.GetSurfaceAt(7).Thickness = 0.0
$lde.GetSurfaceAt(7).Radius = 0.0

$sys.SystemData.TitleNotes.Title = "Example Cooke triplet - bundled first-run design"

$dir = Split-Path -Parent $OutZmx
if (-not (Test-Path $dir)) { [void](New-Item -ItemType Directory -Force $dir) }
[void]$sys.SaveAs($OutZmx)

# Report what was built, from the MODEL rather than from the table above -- if
# a glass name is unavailable in this installation's catalogs, OpticStudio
# silently leaves the surface in air and the totals below are how that shows.
$elements = 0; $prev = ""
for ($s = 1; $s -lt 7; $s++) {
    $mat = "$($lde.GetSurfaceAt($s).Material)".Trim()
    if ($mat -ne "" -and ($prev -eq "" -or $prev -ne $mat)) { $elements++ }
    $prev = $mat
}
$track = 0.0
for ($s = 1; $s -lt 7; $s++) { $track += [double]$lde.GetSurfaceAt($s).Thickness }
Write-Output ""
Write-Output ("EXAMPLE LENS: " + (Split-Path $OutZmx -Leaf))
Write-Output ("  {0} surfaces, {1} elements, total track {2:F2} mm" -f $lde.NumberOfSurfaces, $elements, $track)
Write-Output ("  f/5 at 50 mm focal length, +/-14 deg field, glasses N-SK16 / N-SF2")
Write-Output ("  written to {0}" -f $OutZmx)
if ($elements -lt 3) {
    Write-Output ("  WARNING: only $elements element(s) resolved -- one of the glasses is " +
                  "missing from this installation's catalogs, and preflight will refuse this file")
}
$app.CloseApplication()
exit 0
