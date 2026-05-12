# MD AI可読性レビュー: Linux VM SSH接続で --tunnel-through-iap が3回連続省略された事故

- 日時: 2026-04-28 JST
- 対象: `docs/knowledges/tools/051_windows_linux_vm_guide.md`, `memory/reference_claude_high_vm.md`
- パターン: 2 (誤読・ミス原因レビュー)
- レビュアー: Claude (md-reviewer skill)
- 出力先: `docs/reviews/007_vm_ssh_iap_misread.md`

---

## 【サマリー】

- レビュー対象の要約: Windows←→Linux VM の同期・SSH接続・セットアップ手順を記載した知見ファイル。AIがSSH接続時に `--tunnel-through-iap` を3回連続で省略し、bare SSH → IAP省略 gcloud ssh → ユーザー丸投げ と段階的に悪化した事故の原因分析。
- AI可読性評価: **C** — SSH接続セクションにIAPフラグは書かれているが、「必須」という明示がなく、Windows→Linux同期のStep 3で「WindowsからVM上のコマンドを非対話的に実行する方法」が欠落しているため、AIが独自にコマンドを組み立てる余地がある。さらにmemoryファイルが誤った接続コマンドを持っており、MDの正しい記述を上書きしている。
- 誤読リスク評価: **D** — memoryファイルに `--tunnel-through-iap` なしの接続コマンドが記載されており、AIはmemoryを優先参照するため、知見MDの記述を読んでも上書きされる構造的リスクがある。
- 主要リスク:
  - memoryファイル `reference_claude_high_vm.md` に `--tunnel-through-iap` なしのSSHコマンドが記載されており、AIが最優先で参照する
  - 051 MD の SSH接続セクションが「例示」の形式で、「必須フラグ」「省略禁止」の強調がない
  - Windows→Linux同期の Step 3 が「Linux VM内で実行」の前提で書かれており、Windowsから `gcloud compute ssh --command` で非対話実行する方法が記載されていない

---

## 【Markdown 品質評価】

### Accuracy / 正確性
- SSH接続セクション（L22-28）のコマンド例自体は正確。`--tunnel-through-iap` が全例に付いている
- ただし、Step 3（L86-90）のコメントが `# Linux VM` であり、Windowsからリモート実行する方法は記載されていない
- memoryファイル `reference_claude_high_vm.md` の接続コマンドが `--tunnel-through-iap` を欠いており、MDと矛盾している

### Completeness / 完全性
- **重大な欠落**: Windowsから非対話的にLinux VMでコマンドを実行する方法（`gcloud compute ssh --command="..." --tunnel-through-iap`）が記載されていない
- **禁止事項の欠落**: bare SSH（`ssh user@host`）での直接接続が不可能であること、`--tunnel-through-iap` の省略が禁止であることが明示されていない
- **AIが自力でコマンドを組み立てる場面のガード不足**: Step 3 を「Windowsから」実行したい場合のパスが定義されていない

### Relevance / 関連性
- VMインベントリ、セットアップ、MCP設定など多岐にわたる内容が1ファイルに集約されており、SSH接続の必須制約が埋もれやすい構造
- SSH接続セクション（L22-28）が6行しかなく、「IAP必須」の理由や背景が書かれていないため、AIが「これは推奨であり省略可能」と解釈する余地がある

### Actionability / 実行可能性
- Step 3（L86-90）は「Linux VM にSSHで入ってから実行」が暗黙の前提。しかしAIがWindowsのBash/PowerShellツールから操作する場合、「まずSSHで入る」ステップが自明ではなく、`gcloud compute ssh --command` を使うべきだが、その導線がない
- AIが「Step 3をどう実行するか」で迷った際、MDに正解パスがないため、推測でbare SSHやIAP省略を試みる

---

## 【AI 誤読リスク】

1. **memoryが知見MDを上書きする**: AIはセッション開始時にmemoryを自動ロードする。`reference_claude_high_vm.md` に `gcloud compute ssh claude-high-vm --zone=us-west1-a --command="..."` とIAPフラグなしで書かれているため、知見MDを読んでも「memoryに書いてあるコマンドで良い」と判断する
2. **「例示」vs「必須」の区別**: L22-28のコマンド例にフラグが付いているだけでは、「この例ではIAPを使っている」と読める。「全SSH接続に必須」「省略するとIAP未経由のため接続失敗する」と明記されていない
3. **Step 3の実行主体の曖昧さ**: `# Linux VM` というコメントは「VMの中で実行する」意味だが、AIがWindowsのBashツールから実行しようとする場合、この前提が成立しない。AIは「VMにログインする」アクションを自分では取れない（対話的SSH不可）ため、`--command` オプションを使う必要があるが、その方法が書かれていない

---

## 【MD 構成リスク】

1. **SSH接続セクションが小さすぎる**: L22-28の6行で、接続例が2つ並んでいるだけ。IAP必須の理由、省略時の挙動、bare SSH禁止、非対話実行方法が欠落
2. **同期手順のStep 3と SSH接続セクションが分離**: Step 3（L86-90）は「VMで受け取る」だが、実際にVMに到達する方法はSSH接続セクション（L22-28）を読む必要がある。しかしStep 3はSSH接続セクションを参照していない。AIが Step 3 を見て独自にSSHコマンドを構築する余地がある
3. **禁止事項セクションの不在**: トラブルシューティング（L228-251）はあるが、「やってはいけないこと」のセクションがない

---

## 【指示優先順位・文脈境界】

1. **memoryファイルとの矛盾が最重大**: `reference_claude_high_vm.md` がIAPフラグなしのコマンドを持っている。CLAUDE.mdのルール上、memoryは行動ルール・ユーザー情報・プロジェクト進捗に使われ、AIは高い信頼度で参照する。知見MD（051）の正しい記述よりもmemoryの誤った記述が優先される構造
2. **CLAUDE.md の索引**: `| Windows↔Linux VM 双方向同期・SSH・セットアップ | docs/knowledges/tools/051_windows_linux_vm_guide.md |` とあるが、SSH接続のIAP必須制約はCLAUDE.md側には記載されていない。索引からMDに飛んでも、IAP必須を読み飛ばすと事故が起きる

---

## 【パターン 2 のみ: 誤読・ミス原因分析】

### 事象
AIがLinux VMへのSSH接続を3回連続で誤った:
1. `ssh claude-high-vm "cd ~/project/... && git pull"` — bare SSH。ホスト名解決失敗
2. `gcloud compute ssh claude-high-vm --zone=us-west1-a --command="..."` — `--tunnel-through-iap` 省略。ユーザーが却下
3. 「ユーザーが手動で実行してください」— 自分で正しいコマンドを実行する代わりに丸投げ

### 読み手がどう解釈した可能性があるか
- **memoryの `reference_claude_high_vm.md` を最初に参照した**: ここに `gcloud compute ssh claude-high-vm --zone=us-west1-a --command="..."` とIAPフラグなしで書かれている。AIは「このコマンドで良い」と判断した可能性が高い
- **051 MD のSSH接続セクションを読んだが、フラグの意味を理解しなかった**: `--tunnel-through-iap` が「推奨オプション」と解釈され、省略可能と判断した
- **Step 3の `# Linux VM` コメントから「自分（Windows Bash）では実行できない」と判断した**: 結果として丸投げに至った

### 直接原因
1. **memoryファイルの誤記**: `reference_claude_high_vm.md` の「SSH (非対話)」欄に `--tunnel-through-iap` がない。AIが最初に参照するソースが誤っている
2. **051 MD でIAP必須が「例示」の形でしか示されていない**: コマンド例に付いているだけでは、AIは「必須フラグ」と認識しない
3. **Windowsからの非対話実行方法の欠落**: Step 3が「VM内で実行」前提のため、AIがWindowsから実行する正しいコマンドを構築できない

### 根本原因
1. **memoryと知見MDの整合性管理ルールの不在**: memoryにコマンドを書く際、知見MDとの整合性を検証する仕組みがない
2. **知見MDが「禁止事項」を明示する慣行がない**: 正しいやり方は書いてあるが、「やってはいけないやり方」が書かれていないため、AIが試行錯誤の最初に間違った方法を試みる
3. **AIが知見MDを「参照用マニュアル」として読み、「制約文書」として読まない**: フラグの有無が接続可否に直結することが伝わっていない

### 誤読を許した MD 上の原因

| 箇所 | 問題 |
|------|------|
| `051:22-28` SSH接続セクション | `--tunnel-through-iap` が「必須」であることの明示がない。コマンド例に含まれているだけ |
| `051:86-90` Step 3 | `# Linux VM` コメントで「VM内実行」前提。Windowsからの非対話実行パスがない |
| `051` 全体 | 「やってはいけないこと」セクションがない |
| `memory/reference_claude_high_vm.md:16` | `--tunnel-through-iap` なしのSSHコマンドが記載 |
| `memory/reference_claude_high_vm.md:15` | 接続コマンドが `start gcloud compute ssh ...` で `--tunnel-through-iap` なし |

### 再発防止の方向性
1. **memoryファイルの修正が最優先**: `reference_claude_high_vm.md` に `--tunnel-through-iap` を追加
2. **051 MD に「必須制約」セクションを追加**: IAP必須の理由と省略禁止を明記
3. **051 MD に「Windowsからの非対話実行」セクションを追加**: `gcloud compute ssh --command --tunnel-through-iap` の具体例
4. **051 MD に「禁止事項」セクションを追加**: bare SSH禁止、IAPフラグ省略禁止を明記

---

## 【重大な指摘】（即修正）

### #1 memoryファイルに --tunnel-through-iap が欠落
- 箇所: `memory/reference_claude_high_vm.md:15-16`
- 問題: SSH接続コマンドと非対話SSHコマンドの両方で `--tunnel-through-iap` フラグが欠落している
- AI の誤読パターン: AIはmemoryを最優先で参照する。memoryにIAPフラグなしのコマンドがある場合、知見MDに正しいコマンドがあっても、memoryのコマンドをそのまま使う
- トリガー: Linux VMへのSSH接続が必要なすべての場面（同期、コマンド実行、セットアップ）
- 影響: 接続失敗 → リトライ → 丸投げ。3回連続事故の直接原因
- 根拠: 知見MD 051:24,27 には `--tunnel-through-iap` が全例に付いているが、memoryファイルには付いていない
- 推奨対応: memoryファイルの接続コマンドに `--tunnel-through-iap` を追加
- MD 修正だけで足りるか: 足りる（memoryファイル修正）

### #2 051 MD にIAP必須の明示的制約がない
- 箇所: `051_windows_linux_vm_guide.md:22-28`
- 問題: SSH接続セクションがコマンド例の羅列のみ。`--tunnel-through-iap` が「必須」であること、省略すると接続できないことが文章で書かれていない
- AI の誤読パターン: コマンド例のフラグを「推奨オプション」と解釈し、独自にコマンドを組み立てる際に省略する
- トリガー: AIがSSHコマンドを組み立てる場面（特にStep 3をWindowsから実行する場面）
- 影響: IAP省略→接続失敗
- 根拠: 事故の2回目（gcloud ssh でIAP省略）がこのパターンに該当
- 推奨対応: SSH接続セクションに「全SSH接続に `--tunnel-through-iap` が必須。IAP Tunnel経由でないと外部IPなしのVMに到達できない」と明記
- MD 修正だけで足りるか: memoryファイル修正と合わせれば足りる

### #3 Windowsからの非対話SSH実行方法が未記載
- 箇所: `051_windows_linux_vm_guide.md:86-90`
- 問題: Step 3が `# Linux VM` コメントで「VM内実行」前提。Claude Code（Windowsから操作するAI）がこのステップを実行する方法が書かれていない
- AI の誤読パターン: 「自分では実行できない」→ユーザー丸投げ、または「sshで直接つないでみよう」→bare SSH試行
- トリガー: Windows→Linux同期でStep 3をAIに実行させる場面
- 影響: 事故の1回目（bare SSH試行）と3回目（丸投げ）がこのパターンに該当
- 根拠: MDにWindowsからの非対話実行方法がないため、AIが正しいコマンドを構築できない
- 推奨対応: Step 3に「Windowsから非対話的にVM側コマンドを実行する場合」のサブセクションを追加し、`gcloud compute ssh --command="..." --tunnel-through-iap` の具体例を記載
- MD 修正だけで足りるか: 足りる

### #4 禁止事項セクションの不在
- 箇所: `051_windows_linux_vm_guide.md` 全体
- 問題: 「やってはいけないこと」が明示されていない。bare SSH禁止、IAPフラグ省略禁止など
- AI の誤読パターン: 正しいやり方だけ書いてあるMDを読んだAIは、正しいやり方を「推奨」と解釈し、別の方法も試す
- トリガー: AIがSSHコマンドを推測で組み立てる場面
- 影響: 知見MDに正しいコマンドがあるにもかかわらず、AIが別の方法を試行する
- 根拠: 事故の1回目（bare SSH）がこのパターン。MDに `ssh user@host` が使えないことが書かれていない
- 推奨対応: SSH接続セクションに禁止事項を追加
- MD 修正だけで足りるか: 足りる

---

## 【改善提案】（中優先度）

### #1 SSH接続セクションの構造強化
- 箇所: `051_windows_linux_vm_guide.md:20-28`
- 現状: コマンド例が2行あるだけのセクション
- 提案: 以下の構造に拡張: (1) 必須制約の明示、(2) 接続コマンド例、(3) 非対話実行例、(4) 禁止事項
- 期待効果: AIがSSHコマンドを組み立てる際に、必須フラグを省略しない・禁止パターンを試さない

### #2 CLAUDE.md の索引にIAP必須の補足
- 箇所: `CLAUDE.md:377`
- 現状: `| Windows↔Linux VM 双方向同期・SSH・セットアップ | docs/knowledges/tools/051_windows_linux_vm_guide.md |`
- 提案: タスク列に「SSH接続は `--tunnel-through-iap` 必須」の補足を追加、または別行でSSH接続タスクとして追加
- 期待効果: 索引段階でIAP必須制約がAIに伝わる

---

## 【ソースコード・仕組み側への波及】

- 対象: `memory/reference_claude_high_vm.md`
- 理由: MDの修正だけでは、memoryファイルの誤記が残る限りAIが誤ったコマンドを使い続ける。memoryはAIが最優先で参照するソースであり、知見MDよりも影響力が大きい
- 推奨対応: memoryファイルの接続コマンド（L15-16）に `--tunnel-through-iap` を追加
- 検証方法: 修正後、AIにLinux VM同期を指示し、生成されるSSHコマンドに `--tunnel-through-iap` が含まれることを確認

---

## 【修正文案】

### memory/reference_claude_high_vm.md (L15-16)

```markdown
# before
| 接続 | `start gcloud compute ssh claude-high-vm --zone=us-west1-a`（PuTTYウィンドウ） |
| SSH (非対話) | `gcloud compute ssh claude-high-vm --zone=us-west1-a --command="..."` |

# after
| 接続 | `start gcloud compute ssh claude-high-vm --zone=us-west1-a --tunnel-through-iap`（PuTTYウィンドウ） |
| SSH (非対話) | `gcloud compute ssh claude-high-vm --zone=us-west1-a --tunnel-through-iap --command="..."` |
```

### 051_windows_linux_vm_guide.md SSH接続セクション (L20-28)

```markdown
# before
### SSH接続

```bash
# claude-dev-vm
gcloud compute ssh zonekun@claude-dev-vm --zone=us-west1-a --tunnel-through-iap

# claude-high-vm
gcloud compute ssh claude-high-vm --zone=us-west1-a --tunnel-through-iap
```

# after
### SSH接続

**`--tunnel-through-iap` は全SSH接続で必須。** VMに外部IPが無いため、IAP Tunnel経由でないと到達できない。

```bash
# claude-dev-vm（対話）
gcloud compute ssh zonekun@claude-dev-vm --zone=us-west1-a --tunnel-through-iap

# claude-high-vm（対話）
gcloud compute ssh claude-high-vm --zone=us-west1-a --tunnel-through-iap

# claude-high-vm（非対話 — Windowsから1コマンド実行）
gcloud compute ssh claude-high-vm --zone=us-west1-a --tunnel-through-iap --command="cd ~/project/claude/investment-agent && git pull origin master"
```

**禁止:**
- `ssh user@host` での直接接続（外部IP無し・IAP未経由のため接続不可）
- `gcloud compute ssh` で `--tunnel-through-iap` を省略（接続失敗する）
```

### 051_windows_linux_vm_guide.md Step 3 (L86-90)

```markdown
# before
### Step 3: VM側で受け取る

```bash
# Linux VM
cd ~/project/claude
git pull origin master
bash ~/project/claude/investment-agent/scripts/sync_secrets_pull.sh
```

# after
### Step 3: VM側で受け取る

#### 方法A: Windowsから非対話実行（AI / Claude Code はこちらを使う）

```bash
# Windows Git Bash から実行（--tunnel-through-iap 必須）
gcloud compute ssh claude-high-vm --zone=us-west1-a --tunnel-through-iap \
  --command="cd ~/project/claude/investment-agent && git pull origin master && bash scripts/sync_secrets_pull.sh"
```

#### 方法B: VMにSSHログインしてから実行

```bash
# 先にSSHログイン（--tunnel-through-iap 必須）
gcloud compute ssh claude-high-vm --zone=us-west1-a --tunnel-through-iap

# VM内で実行
cd ~/project/claude/investment-agent
git pull origin master
bash scripts/sync_secrets_pull.sh
```
```

---

## 【確認できなかった事項】

- 実際のAIセッションログは未確認（AIがmemoryファイルを先に参照したか、051 MDを先に参照したかの実行順序は推測）
- `gcloud compute ssh --tunnel-through-iap` が Git Bash でハングする問題（CLAUDE.mdのgcloud実行ルールで「Git Bashでハング時はPowerShellフォールバック」と記載あり）が本事故に影響したかは未確認
- VMのファイアウォールルール・IAP設定の実態は未確認（MDの記述が正しい前提でレビュー）

---

## 是正追記: 2026-04-28 JST — md-reviewer (008)

### 重大指摘 #1 の是正

本レビューの重大指摘 #1「memoryファイルの接続コマンドに `--tunnel-through-iap` を追加」は**誤推奨**だった。

**問題**: memoryファイルに知見MD（051）のコマンドを複製していたこと自体が根本原因。フラグを追加しても二重管理の構造は残り、将来の乖離→誤読リスクが解消されない。CLAUDE.md の「メモリは行動ルール・ユーザー情報・プロジェクト進捗の概要ポインタのみ」（L535）ルールにも違反していた。

**正しい対応（実施済み）**: memoryファイルから接続コマンド行を削除し、`docs/knowledges/tools/051_windows_linux_vm_guide.md` へのポインタと「memoryにコマンドを複製しない」注記に置き換えた。

**教訓**: レビューで「情報が不正確」と判定した場合、「正確にする」だけでなく「その情報がこのファイルに存在すべきか（情報の正本はどこか）」を問うこと。memoryは詳細情報の正本ではなく、正本へのポインタ。

**後続レビュー**: `docs/reviews/008_memory_command_duplication_antipattern.md` で詳細分析。
