[CmdletBinding()]
param(
    [switch]$BuildRouter,
    [switch]$AddToPath
)
$ErrorActionPreference = 'Stop'
$ccxRoot = $PSScriptRoot
$ccxWindows = Join-Path $ccxRoot 'windows'
if (-not (Get-Command py -ErrorAction SilentlyContinue) -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Install Python 3.10 or newer, then run this installer again.'
}
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -X utf8 (Join-Path $ccxWindows 'ccx.py') --init-windows
} else {
    & python -X utf8 (Join-Path $ccxWindows 'ccx.py') --init-windows
}
if ($LASTEXITCODE -ne 0) { throw 'Windows configuration initialization failed.' }
if ($BuildRouter) {
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
        throw 'Building the router requires Rust and Cargo with the Windows MSVC toolchain.'
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -X utf8 (Join-Path $ccxWindows 'build_router.py') --release
    } else {
        & python -X utf8 (Join-Path $ccxWindows 'build_router.py') --release
    }
    if ($LASTEXITCODE -ne 0) { throw 'Router build failed.' }
}
if ($AddToPath) {
    $ccxUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($ccxWindows -notin ($ccxUserPath -split ';')) {
        [Environment]::SetEnvironmentVariable('Path', "$ccxUserPath;$ccxWindows", 'User')
    }
    if ($ccxWindows -notin ($env:Path -split ';')) { $env:Path += ";$ccxWindows" }
}
Write-Host "Windows launcher: $ccxWindows\ccx.cmd"
Write-Host 'Keep this checkout in place. Open a new CMD after adding it to PATH.'
Write-Host 'Install and authenticate each AI CLI separately, then run: ccx new'
