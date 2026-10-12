# SLAPrep masaustu ve Baslat menusu kisayollarini olusturur.
$root = Split-Path -Parent $PSScriptRoot
$target = Join-Path $root ".venv\Scripts\slaprep-gui.exe"
$icon = Join-Path $root "slaprep\assets\slaprep.ico"
if (-not (Test-Path $target)) { Write-Error "Bulunamadi: $target (once install.bat calistirin)"; exit 1 }
$ws = New-Object -ComObject WScript.Shell
$places = @([Environment]::GetFolderPath('Desktop'), (Join-Path ([Environment]::GetFolderPath('Programs')) ''))
foreach ($dir in $places) {
    if (-not (Test-Path $dir)) { continue }
    $lnk = $ws.CreateShortcut((Join-Path $dir 'SLAPrep.lnk'))
    $lnk.TargetPath = $target
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = $icon
    $lnk.Description = 'SLA baski oncesi hazirlik (hollow, delik, plate)'
    $lnk.Save()
    Write-Output "Kisayol: $(Join-Path $dir 'SLAPrep.lnk')"
}
