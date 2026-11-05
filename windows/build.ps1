# Builds Blackout.exe using the C# compiler that ships with Windows (.NET Framework 4.x).
#   -Version 1.2.3   stamp a version (default 0.0.0-dev)
#   -Package         also write dist\Blackout-<version>-windows.zip
#   -Installer       also write dist\Blackout-<version>-windows-setup.exe (needs Inno Setup 6)
#   -Install         copy to %LOCALAPPDATA%\Programs\Blackout and add a Start Menu shortcut
param([string]$Version = '0.0.0-dev', [switch]$Package, [switch]$Installer, [switch]$Install)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$csc = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { $csc = "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe" }
New-Item -ItemType Directory -Force build | Out-Null

# Version info: numeric part for the file version, full string for the product version.
$numeric = ($Version -split '[-+]')[0]
@"
[assembly: System.Reflection.AssemblyTitle("Blackout")]
[assembly: System.Reflection.AssemblyProduct("Blackout")]
[assembly: System.Reflection.AssemblyVersion("$numeric")]
[assembly: System.Reflection.AssemblyFileVersion("$numeric")]
[assembly: System.Reflection.AssemblyInformationalVersion("$Version")]
"@ | Set-Content -Encoding utf8 build\AssemblyInfo.cs

& $csc /nologo /optimize+ /target:winexe /platform:anycpu /codepage:65001 /win32icon:Blackout.ico `
    /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Web.Extensions.dll /out:Blackout.exe Blackout.cs build\AssemblyInfo.cs
if ($LASTEXITCODE -ne 0) { throw "Compilation failed" }
Write-Host "Built $PSScriptRoot\Blackout.exe ($Version)"

if ($Package) {
    $dist = Join-Path (Split-Path $PSScriptRoot) dist
    New-Item -ItemType Directory -Force $dist | Out-Null
    $zip = "$dist\Blackout-$Version-windows.zip"
    Compress-Archive -Path Blackout.exe -DestinationPath $zip -Force
    Write-Host "Packaged $zip"
}

if ($Installer) {
    $iscc = @((Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source,
              "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
              "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
    & $iscc /Q "/DAppVersion=$Version" "/DNumericVersion=$numeric" installer.iss
    if ($LASTEXITCODE -ne 0) { throw "Installer build failed" }
    Write-Host "Packaged $(Split-Path $PSScriptRoot)\dist\Blackout-$Version-windows-setup.exe"
}

if ($Install) {
    $dest = "$env:LOCALAPPDATA\Programs\Blackout"
    Get-Process Blackout -ErrorAction SilentlyContinue | Stop-Process -Force
    New-Item -ItemType Directory -Force $dest | Out-Null
    Copy-Item Blackout.exe $dest -Force
    $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut(
        "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Blackout.lnk")
    $lnk.TargetPath = "$dest\Blackout.exe"; $lnk.Save()
    Write-Host "Installed to $dest\Blackout.exe (Start Menu shortcut added)"
}
