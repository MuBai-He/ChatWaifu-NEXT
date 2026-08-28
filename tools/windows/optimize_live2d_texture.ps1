param(
    [Parameter(Mandatory = $true)][string]$TexturePath,
    [ValidateRange(512, 8192)][int]$MaxDimension = 4096
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "This texture optimizer only supports Windows."
}

$ResolvedTexture = (Resolve-Path -LiteralPath $TexturePath).Path
Add-Type -AssemblyName System.Drawing

$SourceImage = [System.Drawing.Image]::FromFile($ResolvedTexture)
try {
    $SourceWidth = $SourceImage.Width
    $SourceHeight = $SourceImage.Height
} finally {
    $SourceImage.Dispose()
}

if ([Math]::Max($SourceWidth, $SourceHeight) -le $MaxDimension) {
    Write-Host "Live2D texture is already bounded at ${SourceWidth}x${SourceHeight}."
    return
}

$TextureDirectory = Split-Path -Parent $ResolvedTexture
$TextureName = [System.IO.Path]::GetFileNameWithoutExtension($ResolvedTexture)
$TextureExtension = [System.IO.Path]::GetExtension($ResolvedTexture)
$SourceBackup = Join-Path $TextureDirectory "$TextureName.source$TextureExtension"
$TemporaryTexture = Join-Path $TextureDirectory "$TextureName.optimizing-$PID$TextureExtension"

if (-not (Test-Path -LiteralPath $SourceBackup)) {
    Copy-Item -LiteralPath $ResolvedTexture -Destination $SourceBackup
}

$Scale = $MaxDimension / [double][Math]::Max($SourceWidth, $SourceHeight)
$TargetWidth = [Math]::Max(1, [int][Math]::Round($SourceWidth * $Scale))
$TargetHeight = [Math]::Max(1, [int][Math]::Round($SourceHeight * $Scale))
$SourceImage = $null
$TargetImage = $null
$Graphics = $null

try {
    $SourceImage = [System.Drawing.Image]::FromFile($ResolvedTexture)
    $TargetImage = New-Object System.Drawing.Bitmap(
        $TargetWidth,
        $TargetHeight,
        [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
    )
    $Graphics = [System.Drawing.Graphics]::FromImage($TargetImage)
    $Graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
    $Graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
    $Graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $Graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $Graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
    $Graphics.DrawImage(
        $SourceImage,
        (New-Object System.Drawing.Rectangle(0, 0, $TargetWidth, $TargetHeight)),
        0,
        0,
        $SourceWidth,
        $SourceHeight,
        [System.Drawing.GraphicsUnit]::Pixel
    )
    $TargetImage.Save($TemporaryTexture, [System.Drawing.Imaging.ImageFormat]::Png)
} finally {
    if ($null -ne $Graphics) { $Graphics.Dispose() }
    if ($null -ne $TargetImage) { $TargetImage.Dispose() }
    if ($null -ne $SourceImage) { $SourceImage.Dispose() }
}

try {
    Move-Item -LiteralPath $TemporaryTexture -Destination $ResolvedTexture -Force
} finally {
    Remove-Item -LiteralPath $TemporaryTexture -Force -ErrorAction SilentlyContinue
}

Write-Host "Optimized local Live2D texture from ${SourceWidth}x${SourceHeight} to ${TargetWidth}x${TargetHeight}."
Write-Host "Original preserved at $SourceBackup"
