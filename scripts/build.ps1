param(
    [Parameter(Mandatory = $true)][string]$FfmpegDirectory,
    [Parameter(Mandatory = $true)][string]$FfmpegLicenseFile,
    [Parameter(Mandatory = $true)][string]$FfmpegSourceInfoFile,
    [switch]$Installer
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location -LiteralPath $projectRoot
try {
    if ((python -c "import struct; print(struct.calcsize('P') * 8)") -ne '64') {
        throw 'Use 64-bit Python to build the Windows x64 application.'
    }
    $vendorDirectory = Join-Path $projectRoot 'vendor'
    New-Item -ItemType Directory -Force -Path $vendorDirectory | Out-Null
    foreach ($binary in @('ffmpeg.exe', 'ffprobe.exe')) {
        Copy-Item -LiteralPath (Join-Path $FfmpegDirectory $binary) -Destination (Join-Path $vendorDirectory $binary)
    }
    Copy-Item -LiteralPath $FfmpegLicenseFile -Destination (Join-Path $vendorDirectory 'FFmpeg-LICENSE.txt')
    Copy-Item -LiteralPath $FfmpegSourceInfoFile -Destination (Join-Path $vendorDirectory 'FFmpeg-SOURCE.txt')
    python -m PyInstaller --noconfirm --clean vxtension.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
    if ($Installer) {
        $compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        $compilerPath = if ($compiler) { $compiler.Source } else { Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe' }
        if (-not (Test-Path -LiteralPath $compilerPath)) { throw 'Install Inno Setup 6 or add ISCC.exe to PATH.' }
        & $compilerPath 'installer\vxtension.iss'
        if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
    }
} finally {
    Pop-Location
}
