param(
    [Parameter(Mandatory = $true)]
    [string]$SourcePath,
    [string]$DestinationPath = "C:\Users\zonekun\Documents\codex\investment-agent",
    [string]$RepoRoot = "C:\Users\zonekun\Documents\codex",
    [string]$BranchName = "codex/integration",
    [switch]$InitializeGit,
    [switch]$Mirror,
    [string]$RemoteUrl = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Resolve-FullPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [switch]$AllowMissing
    )

    if (Test-Path -LiteralPath $Path) {
        return (Resolve-Path -LiteralPath $Path).Path
    }

    if ($AllowMissing) {
        $item = Get-Item -LiteralPath (Split-Path -Path $Path -Parent) -ErrorAction Stop
        return [System.IO.Path]::GetFullPath((Join-Path $item.FullName (Split-Path -Path $Path -Leaf)))
    }

    throw "Path not found: $Path"
}

function Test-PathWithin {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ChildPath,
        [Parameter(Mandatory = $true)]
        [string]$ParentPath
    )

    $normalizedParent = $ParentPath.TrimEnd('\', '/')
    $normalizedChild = $ChildPath.TrimEnd('\', '/')
    return $normalizedChild.StartsWith(
        $normalizedParent + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or $normalizedChild.Equals($normalizedParent, [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-GitAvailable {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "git command not found in PATH."
    }
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

function Invoke-RobocopySync {
    param(
        [string]$From,
        [string]$To,
        [bool]$UseMirror
    )

    $excludeDirs = @(".venv", ".pytest_cache", "__pycache__", ".mypy_cache", ".ruff_cache", ".ipynb_checkpoints")
    $excludeFiles = @(".DS_Store")
    $arguments = @(
        $From,
        $To,
        "/E",
        "/COPY:DAT",
        "/R:1",
        "/W:1",
        "/XD"
    ) + $excludeDirs
    if ($excludeFiles.Count -gt 0) {
        $arguments += @("/XF") + $excludeFiles
    }
    if ($UseMirror) {
        $arguments += "/MIR"
    }

    Write-Host "Syncing workspace..." -ForegroundColor Cyan
    $null = & robocopy @arguments
    $exitCode = $LASTEXITCODE

    if ($exitCode -ge 8) {
        throw "robocopy failed with exit code $exitCode"
    }

    Write-Host "Workspace sync completed (robocopy exit code: $exitCode)." -ForegroundColor Green
}

function Ensure-GitBranch {
    param(
        [string]$RepoPath,
        [string]$WorkspacePath,
        [string]$Name,
        [string]$OriginUrl
    )

    Assert-GitAvailable

    $gitDir = Join-Path $RepoPath ".git"
    if (-not (Test-Path $gitDir)) {
        Write-Host "Initializing local Git repository..." -ForegroundColor Yellow
        Invoke-Git -Arguments @("init", $RepoPath) | Out-Null
    }

    if ($OriginUrl) {
        $hasOrigin = $false
        $null = Invoke-Git -Arguments @("-C", $RepoPath, "remote", "get-url", "origin") -IgnoreExitCode 2>$null
        $hasOrigin = ($LASTEXITCODE -eq 0)

        if (-not $hasOrigin) {
            Write-Host "Adding origin remote..." -ForegroundColor Yellow
            Invoke-Git -Arguments @("-C", $RepoPath, "remote", "add", "origin", $OriginUrl) | Out-Null
        }
    }

    $workspaceGitIgnore = Join-Path $WorkspacePath ".git"
    if (Test-Path $workspaceGitIgnore) {
        throw "Nested .git found under workspace path: $workspaceGitIgnore"
    }

    $currentBranch = (& git -C $RepoPath branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to read current branch."
    }

    if ($currentBranch -eq $Name) {
        Write-Host "Active Git branch: $currentBranch" -ForegroundColor Green
        return
    }

    $branchExists = $false
    Invoke-Git -Arguments @("-C", $RepoPath, "show-ref", "--verify", "--quiet", ("refs/heads/{0}" -f $Name)) -IgnoreExitCode | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $branchExists = $true
    }

    if ($branchExists) {
        Invoke-Git -Arguments @("-C", $RepoPath, "checkout", $Name) | Out-Null
    } else {
        Invoke-Git -Arguments @("-C", $RepoPath, "checkout", "-b", $Name) | Out-Null
    }

    $currentBranch = (git -C $RepoPath branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to read current branch."
    }
    Write-Host "Active Git branch: $currentBranch" -ForegroundColor Green
}

if (-not (Test-Path -LiteralPath $SourcePath)) {
    throw "Source path not found: $SourcePath"
}

if (-not (Test-Path -LiteralPath $DestinationPath)) {
    Write-Host "Creating destination path: $DestinationPath" -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $DestinationPath -Force | Out-Null
}

if (-not (Test-Path -LiteralPath $RepoRoot)) {
    if ($InitializeGit) {
        Write-Host "Creating repo root: $RepoRoot" -ForegroundColor Yellow
        New-Item -ItemType Directory -Path $RepoRoot -Force | Out-Null
    } else {
        throw "Repo root not found: $RepoRoot"
    }
}

$resolvedSource = Resolve-FullPath -Path $SourcePath
$resolvedDestination = Resolve-FullPath -Path $DestinationPath
$resolvedRepoRoot = Resolve-FullPath -Path $RepoRoot

if (Test-PathWithin -ChildPath $resolvedDestination -ParentPath $resolvedSource) {
    throw "Destination path must not be inside source path."
}

if (-not (Test-PathWithin -ChildPath $resolvedDestination -ParentPath $resolvedRepoRoot)) {
    throw "Destination path must be inside repo root. destination=$resolvedDestination repo_root=$resolvedRepoRoot"
}

Write-Host "Source      : $resolvedSource" -ForegroundColor DarkGray
Write-Host "Destination : $resolvedDestination" -ForegroundColor DarkGray
Write-Host "Repo root   : $resolvedRepoRoot" -ForegroundColor DarkGray
Write-Host "Mirror mode : $Mirror" -ForegroundColor DarkGray

Invoke-RobocopySync -From $resolvedSource -To $resolvedDestination -UseMirror:$Mirror

if ($InitializeGit) {
    Ensure-GitBranch -RepoPath $resolvedRepoRoot -WorkspacePath $resolvedDestination -Name $BranchName -OriginUrl $RemoteUrl
} else {
    Write-Host "Git initialization skipped." -ForegroundColor Yellow
}

Write-Host "Codex bootstrap completed." -ForegroundColor Cyan
