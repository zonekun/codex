# =============================================================
# Claude Code パス診断スクリプト
# 実行方法: powershell -ExecutionPolicy Bypass -File diagnose-claude-code.ps1
# =============================================================

$ErrorActionPreference = "SilentlyContinue"
$divider = "=" * 60

function Write-Section($title) {
    Write-Host "`n$divider" -ForegroundColor Cyan
    Write-Host "  $title" -ForegroundColor Cyan
    Write-Host $divider -ForegroundColor Cyan
}

function Write-Ok($msg)   { Write-Host "[OK]  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[!!]  $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "[NG]  $msg" -ForegroundColor Red }
function Write-Info($msg) { Write-Host "      $msg" -ForegroundColor Gray }

# -------------------------------------------------------
Write-Section "1. claude / claude-code コマンドの which 確認"
# -------------------------------------------------------
foreach ($cmd in @("claude", "claude-code", "claude.exe", "claude-code.exe")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) {
        Write-Ok "$cmd -> $($found.Source)"
    } else {
        Write-Err "$cmd はPATH上に見つかりません"
    }
}

# -------------------------------------------------------
Write-Section "2. よくあるインストール先の存在確認"
# -------------------------------------------------------
$candidates = @(
    "$env:LOCALAPPDATA\Programs\claude-code",
    "$env:LOCALAPPDATA\Programs\Claude Code",
    "$env:APPDATA\claude-code",
    "$env:APPDATA\Claude Code",
    "$env:USERPROFILE\.claude",
    "$env:USERPROFILE\.claude\local",
    "$env:USERPROFILE\AppData\Local\Programs\claude-code",
    "$env:ProgramFiles\Claude Code",
    "$env:ProgramFiles\claude-code",
    "${env:ProgramFiles(x86)}\Claude Code",
    "C:\claude-code",
    "$env:USERPROFILE\claude-code"
)

$foundDirs = @()
foreach ($path in $candidates) {
    if (Test-Path $path) {
        Write-Ok "ディレクトリ存在: $path"
        $foundDirs += $path
        # exeを探す
        $exes = Get-ChildItem $path -Filter "*.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 5
        foreach ($exe in $exes) {
            Write-Info "  EXE: $($exe.FullName)"
        }
    }
}
if ($foundDirs.Count -eq 0) {
    Write-Warn "上記の候補パスにインストール先が見つかりませんでした"
}

# -------------------------------------------------------
Write-Section "3. PATH 環境変数の一覧"
# -------------------------------------------------------
Write-Host "`n--- User PATH ---" -ForegroundColor Yellow
$userPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath) {
    $userPath -split ";" | Where-Object { $_ } | ForEach-Object {
        $exists = Test-Path $_
        $mark = if ($exists) { "[OK]" } else { "[--]" }
        $color = if ($exists) { "White" } else { "DarkGray" }
        Write-Host "$mark $_" -ForegroundColor $color
    }
} else {
    Write-Warn "User PATH が空です"
}

Write-Host "`n--- Machine PATH ---" -ForegroundColor Yellow
$machinePath = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
if ($machinePath) {
    $machinePath -split ";" | Where-Object { $_ } | ForEach-Object {
        $exists = Test-Path $_
        $mark = if ($exists) { "[OK]" } else { "[--]" }
        $color = if ($exists) { "White" } else { "DarkGray" }
        Write-Host "$mark $_" -ForegroundColor $color
    }
}

Write-Host "`n--- 現在のプロセス PATH (抜粋: claude関連) ---" -ForegroundColor Yellow
$env:PATH -split ";" | Where-Object { $_ -match "claude|node|npm|local\\programs" -and $_ } | ForEach-Object {
    Write-Host "  $_" -ForegroundColor Magenta
}

# -------------------------------------------------------
Write-Section "4. Node.js / npm 環境 (Claude Code の依存)"
# -------------------------------------------------------
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
    $ver = & node --version 2>&1
    Write-Ok "node: $($node.Source)  version: $ver"
} else {
    Write-Warn "node.exe が見つかりません (npmベースの場合は必要)"
}

$npm = Get-Command npm -ErrorAction SilentlyContinue
if ($npm) {
    $ver = & npm --version 2>&1
    Write-Ok "npm:  $($npm.Source)  version: $ver"
    # npm global prefix
    $prefix = & npm config get prefix 2>&1
    Write-Info "npm global prefix: $prefix"
    $npmGlobalBin = Join-Path $prefix ""
    if (Test-Path $npmGlobalBin) {
        $claudeInNpm = Get-ChildItem $npmGlobalBin -Filter "claude*" -ErrorAction SilentlyContinue
        if ($claudeInNpm) {
            foreach ($f in $claudeInNpm) {
                Write-Ok "npm global に発見: $($f.FullName)"
            }
        } else {
            Write-Warn "npm global ($npmGlobalBin) に claude* ファイルなし"
        }
    }
} else {
    Write-Warn "npm.exe が見つかりません"
}

# -------------------------------------------------------
Write-Section "5. ファイルシステム全体から claude*.exe を検索"
# -------------------------------------------------------
Write-Host "  ユーザープロファイル以下を検索中..." -ForegroundColor Gray
$results = Get-ChildItem $env:USERPROFILE -Filter "claude*.exe" -Recurse -ErrorAction SilentlyContinue |
           Select-Object -First 10
if ($results) {
    foreach ($r in $results) {
        Write-Ok "発見: $($r.FullName)"
    }
} else {
    Write-Warn "$env:USERPROFILE 以下に claude*.exe が見つかりませんでした"
}

# ProgramFiles も検索
Write-Host "  ProgramFiles 以下を検索中..." -ForegroundColor Gray
$results2 = Get-ChildItem $env:ProgramFiles -Filter "claude*.exe" -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 10
if ($results2) {
    foreach ($r in $results2) { Write-Ok "発見: $($r.FullName)" }
}

# -------------------------------------------------------
Write-Section "6. 診断サマリー & 修正方法の提案"
# -------------------------------------------------------
Write-Host ""

# exeが見つかっていれば修正コマンドを提示
$allFound = @()
if ($foundDirs.Count -gt 0) {
    foreach ($d in $foundDirs) {
        $exes = Get-ChildItem $d -Filter "claude*.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($exes) { $allFound += $exes.DirectoryName }
    }
}

if ($allFound.Count -gt 0) {
    $targetDir = $allFound[0]
    Write-Warn "EXEは見つかりましたがPATHが通っていません。"
    Write-Host "`n  以下のコマンドで User PATH に追加できます:" -ForegroundColor Yellow
    Write-Host @"

  # PowerShell (管理者不要・ユーザーレベル)
  `$newPath = "$targetDir"
  `$current = [System.Environment]::GetEnvironmentVariable("PATH","User")
  [System.Environment]::SetEnvironmentVariable("PATH", "`$current;`$newPath", "User")
  Write-Host "PATH を更新しました。新しいターミナルを開いて確認してください。"

"@ -ForegroundColor Cyan
} else {
    Write-Err "Claude Code の実行ファイルが見つかりませんでした。"
    Write-Host "`n  考えられる原因:" -ForegroundColor Yellow
    Write-Info "  - インストールが完了していない / 途中で失敗した"
    Write-Info "  - インストール先が上記候補以外の場所にある"
    Write-Info "  - ネイティブバイナリを手動で配置した場合、配置先を確認"
    Write-Host "`n  公式インストールコマンド (npm):" -ForegroundColor Yellow
    Write-Host "  npm install -g @anthropic-ai/claude-code" -ForegroundColor Cyan
    Write-Host "`n  または公式サイトからバイナリを再取得してください。" -ForegroundColor Yellow
}

Write-Host "`n$divider`n" -ForegroundColor Cyan