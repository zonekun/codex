param(
    [string]$RepoPath = "C:\Users\zonekun\Documents\codex",
    [string]$BranchName = "codex/integration"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git command not found in PATH."
}

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [switch]$IgnoreExitCode
    )

    & git @Arguments
    $exitCode = $LASTEXITCODE
    if (-not $IgnoreExitCode -and $exitCode -ne 0) {
        throw "git command failed with exit code ${exitCode}: git $($Arguments -join ' ')"
    }
    return $exitCode
}

$gitDir = Join-Path $RepoPath ".git"
if (-not (Test-Path $gitDir)) {
    throw "Git repository not found: $RepoPath"
}

$resolvedRepoPath = (Resolve-Path -LiteralPath $RepoPath).Path
$branchExists = $false
$currentBranch = (& git -C $resolvedRepoPath branch --show-current).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Failed to read current branch."
}

if ($currentBranch -eq $BranchName) {
    Write-Host "Active Git branch: $currentBranch" -ForegroundColor Green
    return
}

Invoke-Git -Arguments @("-C", $resolvedRepoPath, "show-ref", "--verify", "--quiet", ("refs/heads/{0}" -f $BranchName)) -IgnoreExitCode | Out-Null
$branchExists = ($LASTEXITCODE -eq 0)

if ($branchExists) {
    Invoke-Git -Arguments @("-C", $resolvedRepoPath, "checkout", $BranchName) | Out-Null
} else {
    Invoke-Git -Arguments @("-C", $resolvedRepoPath, "checkout", "-b", $BranchName) | Out-Null
}

$currentBranch = (& git -C $resolvedRepoPath branch --show-current).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Failed to read current branch."
}
Write-Host "Active Git branch: $currentBranch" -ForegroundColor Green
