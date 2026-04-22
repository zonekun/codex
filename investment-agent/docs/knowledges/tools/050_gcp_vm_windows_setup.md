# GCP VM セットアップ（Windowsからgcloud操作）

**カテゴリ**: tools
**作成日**: 2026-03-21
**ステータス**: 有効
**関連ファイル**: `C:\Users\Administrator\Dropbox\stock\setup_vm_claude.ps1`, `verify_vm_claude.ps1`

## 概要

WindowsからgcloudでGCP VMを作成・操作する際のハマりポイントと対処法。

## 詳細

### 1. PowerShell配列引数が結合される問題

`& gcloud $arrayArgs` でPowerShellの配列を渡すと、すべての引数が1つの文字列として結合されてgcloudに渡される。

```powershell
# ❌ NG: 引数が結合されてエラー
$gcloudArgs = @("compute", "instances", "create", ...)
& gcloud $gcloudArgs

# ✅ OK: cmd経由で文字列として渡す
$argString = $gcloudArgs -join " "
cmd /c "gcloud $argString"
```

### 2. `gcloud compute ssh --command` は単一コマンドのみ

セミコロン区切りの複数コマンドや `&&` は `--command` 引数として渡せない（plink.exeがパースする）。

```powershell
# ❌ NG: セミコロン以降が別引数として解釈される
cmd /c "gcloud compute ssh vm --command=""cmd1 && cmd2"""

# ✅ OK: スクリプトファイルをSCPで転送してから実行する
# 1. ローカルにshスクリプトを作成
# 2. gcloud compute scp でVMに転送
# 3. gcloud compute ssh --command="bash ~/script.sh" で実行
```

### 3. `gcloud compute scp` が日本語パスを処理できない

内部でpscp.exeを使用しているため、日本語を含むパス（Googleドライブの「マイドライブ」等）を扱えない。

```powershell
# ❌ NG: 日本語パスはエラーになる
gcloud compute scp "G:\マイドライブ\claude\..." vm:/home/...

# ✅ OK: 英字のtempパスに一時コピーしてから転送
Copy-Item "G:\マイドライブ\claude\...\file" "$env:TEMP\tmp_file"
cmd /c "gcloud compute scp $env:TEMP\tmp_file vm:/home/.../file ..."
Remove-Item "$env:TEMP\tmp_file"
```

### 4. Ubuntu 24.04 LTSのイメージファミリー名変更

GCPのUbuntu 24.04 LTSイメージファミリー名が変更されている（2026年3月時点）。

```
❌ 旧: projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts
✅ 新: projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64
```

確認コマンド:
```bash
gcloud compute images list --project=ubuntu-os-cloud --filter=family~ubuntu-2404 --format=table(name,family,status)
```

## 根拠・出典

2026-03-21 claude-dev-vm (us-west1-a) セットアップ作業中に発見。
