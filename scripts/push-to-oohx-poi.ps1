# Push the oohx-poi/ subtree of the parent oohx-matrix repo up to
# the dedicated tuanna0703/oohx-poi GitHub repo.
#
# Run from anywhere inside the parent repo — the script auto-resolves
# the git root and does the subtree split there.
#
# First call: pass -Force to overwrite whatever GitHub initialised the
# repo with (README/.gitignore/LICENSE auto-init).
#
# Usage:
#   .\scripts\push-to-oohx-poi.ps1             # subsequent pushes
#   .\scripts\push-to-oohx-poi.ps1 -Force      # first push (or after rebase)

[CmdletBinding()]
param(
    [switch]$Force,
    [string]$RemoteName    = 'oohx-poi',
    [string]$RemoteUrl     = 'https://github.com/tuanna0703/oohx-poi.git',
    [string]$TargetBranch  = 'main',
    [string]$Prefix        = 'oohx-poi'
)

$ErrorActionPreference = 'Stop'

# Resolve the parent repo root.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot  = (git -C $scriptDir rev-parse --show-toplevel).Trim()

Write-Host "[push-oohx-poi] repo root: $repoRoot"

# Add the remote on first run.
$existingRemotes = git -C $repoRoot remote
if ($existingRemotes -notcontains $RemoteName) {
    Write-Host "[push-oohx-poi] adding remote $RemoteName -> $RemoteUrl"
    git -C $repoRoot remote add $RemoteName $RemoteUrl
}

Write-Host "[push-oohx-poi] splitting subtree '$Prefix' ..."
$splitSha = (git -C $repoRoot subtree split --prefix=$Prefix HEAD).Trim()
Write-Host "[push-oohx-poi] split commit: $splitSha"

$pushArgs = @($RemoteName, "${splitSha}:refs/heads/${TargetBranch}")
if ($Force) {
    $pushArgs += '--force'
    Write-Host "[push-oohx-poi] forcing -- overwrites $RemoteName/$TargetBranch"
}

Write-Host "[push-oohx-poi] pushing to $RemoteName/$TargetBranch ..."
git -C $repoRoot push @pushArgs
Write-Host "[push-oohx-poi] done"
