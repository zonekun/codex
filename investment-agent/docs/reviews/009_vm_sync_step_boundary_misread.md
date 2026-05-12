# MD AI可読性レビュー: 051 VM同期手順で送り出し側/受け取り側の境界不明瞭により再発した操作範囲逸脱

- 日時: 2026-04-28 JST
- 対象: `docs/knowledges/tools/051_windows_linux_vm_guide.md`（007/008レビュー修正後の最新版）
- パターン: 2 (誤読・ミス原因レビュー)
- レビュアー: Claude (md-reviewer skill)
- 出力先: `docs/reviews/009_vm_sync_step_boundary_misread.md`

---

## 【サマリー】

- レビュー対象の要約: Windows-Linux VM間の同期・SSH接続・セットアップ手順を記載した知見ファイル。007/008レビューでSSH接続のIAP必須制約・memoryコマンド複製問題を修正済み。しかし同一セッション内で3回目の事故が発生した。ユーザーの「linuxへの同期実行」指示に対し、AIが送り出し側の操作（Step 1+2）だけ実行すべきところ、受け取り側の操作（Step 3: VM側で受け取る）まで実行しようとした。
- AI可読性評価: **C** -- 007レビューで追加した「方法A: Windowsから非対話実行（Claude Codeはこちらを使う）」が、Step 3を送り出し側の操作であるかのように誤読させる導線を生んでいる。送り出し側/受け取り側の境界が構造的に不明確。
- 誤読リスク評価: **C** -- Step 1/2/3が一列に並ぶ構成で、「linuxへの同期」という曖昧な指示を受けた際に、AIが全Stepを通しで実行する誤読が再現可能。007修正で追加した方法Aが誤読を促進する構造になっている。
- 主要リスク:
  - Step 1/2（送り出し側）と Step 3（受け取り側）の操作主体の境界が見出し・構成レベルで不明確
  - 007レビューで追加した「方法A: Windowsから非対話実行（Claude Codeはこちらを使う）」が Step 3を「AIが実行すべき操作」と誤認させる
  - CLAUDE.md L171-189の「共通手順（送り出し側）」/「受け取り側コマンド」との構造的不整合
  - Step 2 が存在しないスクリプト `scripts/push_to_linux.sh` を参照（正しくは `scripts/sync_push.sh`）

---

## 【Markdown 品質評価】

### Accuracy / 正確性

- **重大**: Step 2（L86-89）が `bash scripts/push_to_linux.sh` を参照しているが、このスクリプトはリポジトリに存在しない。実際に存在するのは `scripts/sync_push.sh`（bash）と `scripts/sync_secrets_push.ps1`（PowerShell）。CLAUDE.md L183 も `bash scripts/sync_push.sh` を記載しており、051 MDの記載と矛盾
- **重大**: ファイル冒頭の「関連ファイル」欄（L7）に `scripts/sync_secrets_push.sh`, `scripts/sync_secrets_pull.sh` が記載されているが、これらも .sh としては存在しない（.ps1 版のみ存在）
- SSH接続セクション（L22-37）のコマンド・禁止事項は007修正で正確になっている
- Step 3（L95-113）のコマンド自体は正確

### Completeness / 完全性

- **重大な欠落**: 「Windows → Linux VM」セクション全体を通して、送り出し側と受け取り側の操作境界が文章で説明されていない。Step 1/2が送り出し側の操作であること、Step 3が受け取り側の操作であることが明示されていない
- **欠落**: 「linuxへの同期」のようなユーザー指示を受けた場合に、AIがどのStepまで実行すべきかの判断基準がない
- CLAUDE.md L171-189の「共通手順（送り出し側）」/「受け取り側コマンド」は明確に分離されているが、051 MDではこの分離が欠落している

### Relevance / 関連性

- Step 3「方法A: Windowsから非対話実行（Claude Codeはこちらを使う）」の括弧内注記が強い誘導力を持つ。AIにとって「こちらを使う」は「このStepを自分が実行すべき」と読める。しかしStep 3は受け取り側の操作であり、送り出し側のAIが実行すべきものではない
- 007レビューがStep 3に方法Aを追加した意図は「AIがStep 3を実行する場合の正しいコマンドを示す」ことだったが、「AIがStep 3を実行すべきか」の判断には触れていなかった

### Actionability / 実行可能性

- AIが「linuxへの同期」を指示された場合:
  1. 051 MDの「Windows -> Linux VM」セクションを開く
  2. Step 1/2/3が順に並んでいる
  3. Step 3の方法Aに「Claude Codeはこちらを使う」とある
  4. AIは「Step 1 -> Step 2 -> Step 3方法A」を順次実行する、と解釈する
- この解釈は論理的に自然だが、事実としては誤り。「linuxへの同期」は送り出し側の操作であり、Step 1+2で完了する

---

## 【AI 誤読リスク】

1. **「linuxへの同期」の解釈範囲**: ユーザーの「linuxへの同期実行」は「Windowsの変更をLinuxに反映する一連の操作」を意味するが、051 MDの構造上、Step 1/2/3の全てが「一連の操作」に見える。送り出し側の責務範囲が明示されていないため、AIが「全Step実行 = 同期完了」と解釈する
2. **「Claude Codeはこちらを使う」の誘導力**: Step 3 方法Aの括弧注記が、AIに対する強い実行指示として機能する。AIは自分向けの指示を優先的に実行するため、Step 3を「送り出し側の操作ではない」と判断する手がかりが他になければ、この注記に従う
3. **CLAUDE.md との構造的不整合**: CLAUDE.md L171-189は送り出し側/受け取り側を明確に分離しているが、051 MDはこれを1つの連続Stepとして記述している。AIがCLAUDE.mdを先に読んでいれば「Step 2で送り出し完了」と判断できる可能性があるが、051 MDだけを読んだ場合は判断材料がない

---

## 【MD 構成リスク】

1. **Step 1/2/3 の連番構成が「全て実行する手順」を示唆**: 番号付きStepは通常「この順番で全て実行する」と解釈される。Step 2と3の間に操作主体の切り替えがあることが構造上表現されていない
2. **セクション見出しの粒度不足**: 「Windows -> Linux VM」は方向を示しているが、操作主体を示していない。「送り出し側（Windows）の操作」と「受け取り側（Linux VM）の操作」を分けるべきところが1つのセクションにまとまっている
3. **007レビュー修正による副作用**: Step 3に「方法A: Windowsから非対話実行」を追加したことで、Step 3全体が「Windowsから実行可能な操作」に見える。元々Step 3は「VM内で実行する操作」だったが、方法Aの追加により「Windowsからリモート実行する操作」という性質が加わり、送り出し側の操作と混同されやすくなった

---

## 【指示優先順位・文脈境界】

1. **CLAUDE.md と 051 MD の責務分担**: CLAUDE.md L160-208「端末間の作業移管」は送り出し側/受け取り側を明確に分離している。051 MDはこのセクションの「具体的なWindows-Linux VM間の手順」を記載する位置づけだが、CLAUDE.mdの分離原則を051 MDの構成に反映していない
2. **「linuxへの同期」と「共通手順（送り出し側）」の対応**: CLAUDE.md L171「共通手順（送り出し側）」はStep 0/1/2の3ステップで完結する。051 MDのStep 1/2はこれに対応するが、Step 3は「受け取り側コマンド」（CLAUDE.md L186-189）に対応する。この対応関係が051 MDに示されていない
3. **CLAUDE.md の受け取り側コマンドとの重複**: CLAUDE.md L189に「Linux VM: `cd ~/project/claude/investment-agent && git pull origin master && bash scripts/sync_pull.sh`」が書かれているが、051 MD Step 3も同じ操作を記載している。CLAUDE.mdの記述だけで受け取り側の操作は完結するため、051 MDのStep 3は重複情報であり、かつ誤読の原因になっている

---

## 【パターン 2 のみ: 誤読・ミス原因分析】

### 事象

007/008レビュー修正直後の同一セッション内で、ユーザーが「linuxへの同期実行」と指示した。AIは051 MDのStep 3 方法A（`gcloud compute ssh --tunnel-through-iap --command="git pull"`）を実行しようとした。ユーザーが却下。

事実関係:
- Step 1（git push）は既に完了済み
- 「linuxへの同期」は送り出し側の操作であり、Step 2（`sync_push.sh` で secrets を GCS にプッシュ）が正しい次のアクション
- Step 3 は受け取り側の操作であり、送り出し側のAIが実行すべきものではない

### 読み手がどう解釈した可能性があるか

- AIは「linuxへの同期」= 「Windowsの変更がLinux VMに反映されるまでの全工程」と解釈した
- 051 MDの「Windows -> Linux VM」セクションを開き、Step 1/2/3を順次実行するタスクリストと解釈した
- Step 3 方法Aに「Claude Codeはこちらを使う」と書かれているため、Step 3を自分が実行すべき操作と解釈した
- Step 3が「受け取り側の操作」であるという認識がなかった（MDに明示されていないため）

### 直接原因

1. **051 MD の Step 1/2/3 の連番構成**: 送り出し側と受け取り側の操作が連番で並んでおり、全て実行すべき手順に見える
2. **Step 3 方法Aの「Claude Codeはこちらを使う」注記**: AIに対する明示的な実行指示として機能し、Step 3が自分の担当操作だと誤認させた
3. **「linuxへの同期」と「受け取り側の操作」の区別の欠如**: MDに「送り出し側の操作はStep 2で完了」「Step 3は受け取り側が自分のタイミングで実行する操作」という境界の説明がない

### 根本原因

1. **007レビューが「SSHコマンドの正確性」に集中し、「操作主体の境界」を見落とした**: 007の修正はSSH接続の技術的正確性（IAPフラグ、非対話実行方法）を改善したが、「このStep は誰が・いつ実行するか」という操作フローの境界には触れなかった
2. **007で追加した方法Aが新たな誤読導線を生成した**: 「Windowsから非対話実行（Claude Codeはこちらを使う）」という注記は、元々「AIがStep 3を実行する際にIAPフラグを忘れない」ための安全策だったが、結果的に「AIはStep 3を実行すべき」という誤ったメッセージを発信した
3. **CLAUDE.md L160-208 の送り出し/受け取り分離原則が 051 MD に反映されていない**: CLAUDE.mdは端末間作業移管を「共通手順（送り出し側）」と「受け取り側コマンド」に分けているが、051 MDはこの構造を継承していない

### 誤読を許した MD 上の原因

| 箇所 | 問題 |
|------|------|
| `051:74` 「## Windows -> Linux VM」 | セクション見出しが方向のみを示し、操作主体の区分を含まない |
| `051:76-89` Step 1/2 | 送り出し側の操作だが「送り出し側」の明示がない |
| `051:93` 「### Step 3: VM側で受け取る」 | 見出しに「受け取る」とあるが、番号付きStepの一部として読むと「前のStepの続き」に見える。操作主体が切り替わることが構造的に表現されていない |
| `051:95` 「#### 方法A: Windowsから非対話実行（Claude Code はこちらを使う）」 | 007レビューで追加。「Windowsから」「Claude Codeはこちらを使う」の2つが組み合わさることで、Step 3を送り出し側AIの操作と誤認させる強い導線 |
| `051:86-89` Step 2 | `bash scripts/push_to_linux.sh` を参照しているが、このスクリプトは存在しない（正しくは `scripts/sync_push.sh`）。これにより、AIがStep 2をスキップしてStep 3に進む可能性もある |
| CLAUDE.md L171-189 vs 051:74-113 | CLAUDE.mdは送り出し/受け取りを分離しているが、051 MDは連番Stepで一体化。構造的不整合 |

### 再発防止の方向性

1. **051 MDの「Windows -> Linux VM」セクションを構造的に再編**: Step 1/2を「送り出し側（Windows）の操作」サブセクションに、Step 3を「受け取り側（Linux VM）の操作」サブセクションに分離する。連番Stepをやめて、操作主体ごとの独立セクションにする
2. **方法Aの「Claude Codeはこちらを使う」注記を削除または修正**: Step 3が「受け取り側の操作」であることが明確になった上で、AIがStep 3を実行する場面（ユーザーから明示的にVM側の操作を指示された場合）のみの記述に限定する
3. **Step 2のスクリプトパスを修正**: `scripts/push_to_linux.sh` -> `scripts/sync_push.sh`
4. **関連ファイル欄の修正**: 存在しないスクリプト名を修正
5. **送り出し側の完了境界を明示**: 「送り出し側の操作はStep 2で完了。受け取り側がいつStep 3を実行するかは受け取り側の判断」と明記

---

## 【重大な指摘】（即修正）

### #1 送り出し側/受け取り側の操作境界がStep構成で表現されていない

- 箇所: `051_windows_linux_vm_guide.md:74-113`（「Windows -> Linux VM」セクション全体）
- 問題: Step 1/2（送り出し側）と Step 3（受け取り側）が連番Stepとして一列に並んでおり、全て同一主体が順次実行する手順に見える。操作主体の切り替えが構造的に表現されていない
- AI の誤読パターン: 「linuxへの同期」指示を受けたAIが、Step 1 -> 2 -> 3 を全て実行すべきタスクリストと解釈し、受け取り側の操作（Step 3）まで実行しようとする
- トリガー: 「linuxへの同期」「linuxに反映して」「VMと同期して」等、方向は指定するが操作範囲が曖昧な指示
- 影響: AIが不要なVM側操作を実行し、ユーザーが却下を繰り返す。同一セッションで3回発生
- 根拠: CLAUDE.md L171-189は「共通手順（送り出し側）」と「受け取り側コマンド」を別セクションとして分離しているが、051 MDはこの分離を反映していない
- 推奨対応: 「Windows -> Linux VM」セクションを「送り出し側（Windows）の操作」と「受け取り側（Linux VM）の操作」の2つのサブセクションに分割する。連番Stepの代わりに、各サブセクション内で独立した手順を記載する。送り出し側サブセクションの末尾に「ここまでで送り出し側の操作は完了。以降は受け取り側の操作」と明記する
- MD 修正だけで足りるか: 足りる

### #2 007レビューで追加した方法Aの注記がStep 3を送り出し側の操作と誤認させる

- 箇所: `051_windows_linux_vm_guide.md:95`（「方法A: Windowsから非対話実行（Claude Code はこちらを使う）」）
- 問題: 「Windowsから」「Claude Codeはこちらを使う」の組み合わせが、Step 3（受け取り側操作）を「送り出し側のAIが実行すべき操作」と誤認させる。007レビューで追加された修正が、新たな誤読導線を生成した
- AI の誤読パターン: 「Claude Codeはこちらを使う」を読んだAIが、Step 3を自分の担当タスクと解釈し、送り出し側の操作としてStep 3を実行する
- トリガー: 「linuxへの同期」指示と、Step 1/2の実行完了後にStep 3に到達した場面
- 影響: 今回の3回目事故の直接トリガー
- 根拠: 007レビュー前はStep 3は「VM内で実行」前提であり、AIが自発的にStep 3を実行する導線はなかった。方法Aの追加により「Windowsから実行可能」かつ「AIが使うべき方法」という2つの属性がStep 3に付与された
- 推奨対応: 方法Aの見出しから「Claude Codeはこちらを使う」を削除する。Step 3の導入文で「以下は受け取り側がVM側で受け取る操作。ユーザーからVM側の操作を明示的に指示された場合にのみ実行する」と記載する。方法Aは「Windowsから非対話で実行する場合」のみに限定し、デフォルトの選択肢ではないことを明記する
- MD 修正だけで足りるか: 足りる

### #3 Step 2 が存在しないスクリプトを参照

- 箇所: `051_windows_linux_vm_guide.md:88`
- 問題: `bash scripts/push_to_linux.sh` を記載しているが、このスクリプトはリポジトリに存在しない。実際に存在するのは `scripts/sync_push.sh`（CLAUDE.md L183 と一致）
- AI の誤読パターン: AIが `scripts/push_to_linux.sh` を実行しようとして「ファイルが見つからない」エラーに遭遇する。結果としてStep 2をスキップしてStep 3に進む可能性がある
- トリガー: Step 2を実行しようとする全ての場面
- 影響: secrets の GCS プッシュが実行されない、またはStep 2スキップ後にStep 3に進む
- 根拠: `ls scripts/push_to_linux*` → ファイルなし。`ls scripts/sync_push*` → `scripts/sync_push.sh` が存在。CLAUDE.md L183 も `bash scripts/sync_push.sh`
- 推奨対応: `scripts/push_to_linux.sh` を `scripts/sync_push.sh` に修正
- MD 修正だけで足りるか: 足りる

### #4 関連ファイル欄のスクリプトパスが不正確

- 箇所: `051_windows_linux_vm_guide.md:7`
- 問題: 関連ファイルに `scripts/sync_secrets_push.sh`, `scripts/sync_secrets_pull.sh` が記載されているが、.sh 版は存在しない。存在するのは `scripts/sync_secrets_push.ps1`, `scripts/sync_secrets_pull.ps1`（PowerShell版）と `scripts/sync_push.sh`, `scripts/sync_pull.sh`（bash版）
- AI の誤読パターン: AIが関連ファイルとして記載されたスクリプトを参照しようとして見つからない
- トリガー: 051 MDの関連ファイルを確認する場面
- 影響: 軽微。関連ファイルの参照失敗
- 根拠: Glob検索で `scripts/sync_secrets_push.ps1` は存在するが `scripts/sync_secrets_push.sh` は存在しない
- 推奨対応: 関連ファイルを実際に存在するスクリプト名に修正（`scripts/sync_push.sh`, `scripts/sync_pull.sh`）
- MD 修正だけで足りるか: 足りる

---

## 【改善提案】（中優先度）

### #1 「Windows -> Linux VM」セクションの構造改善案

- 箇所: `051_windows_linux_vm_guide.md:74-113`
- 現状: Step 1/2/3 の連番構成で、送り出し/受け取りの境界が不明確
- 提案: 以下の2サブセクション構成に変更:

```
## Windows -> Linux VM

### 送り出し側（Windows で実行）

「linuxへの同期」「linux に反映」等の指示は、以下の操作で完了する。

1. git push（コード）
   ...
2. secrets を GCS へプッシュ
   bash scripts/sync_push.sh

**ここまでで送り出し側の操作は完了。**

---

### 受け取り側（Linux VM で実行）

以下は受け取り側がVM上で実行する操作。
送り出し側のAIが「linuxへの同期」指示で実行する範囲ではない。
ユーザーからVM側の操作を明示的に指示された場合にのみ実行する。

Windowsから非対話で実行する場合:
  gcloud compute ssh ... --tunnel-through-iap --command="..."

VMにSSHログインして実行する場合:
  gcloud compute ssh ... --tunnel-through-iap
  cd ~/project/... && git pull && bash scripts/sync_pull.sh
```

- 期待効果: 送り出し側の操作範囲が構造的に限定され、AIが受け取り側の操作まで実行する誤読を防ぐ

### #2 「Linux VM -> Windows」セクションも同様に構造化

- 箇所: `051_windows_linux_vm_guide.md:116-140`
- 現状: 「Linux VM -> Windows」も送り出し/受け取りの区別がない
- 提案: #1と同様に「送り出し側（Linux VM）」と「受け取り側（Windows）」に分離
- 期待効果: 方向の対称性を持たせることで、構造的な理解を促進

### #3 CLAUDE.md索引にStep範囲の補足

- 箇所: CLAUDE.md L377
- 現状: `| Windows<->Linux VM 双方向同期・SSH・セットアップ | docs/knowledges/tools/051_windows_linux_vm_guide.md |`
- 提案: 「同期指示は送り出し側操作のみ実行。受け取り側操作は明示指示時のみ」の補足を追加
- 期待効果: 索引段階で操作範囲の制約がAIに伝わる

---

## 【ソースコード・仕組み側への波及】

該当なし。本件はMDの構成問題であり、コード側の対策は不要。

ただし、`scripts/push_to_linux.sh` が存在しない問題について、かつてこのスクリプトが存在して後にリネーム・削除された可能性がある。その場合、051 MD以外にもこの旧スクリプト名を参照している箇所がないか確認が望ましい。

---

## 【修正文案】

### 051_windows_linux_vm_guide.md L7 関連ファイル

```markdown
# before
**関連ファイル**: `scripts/sync_secrets_push.sh`, `scripts/sync_secrets_pull.sh`, `.claude/settings.local.json`

# after
**関連ファイル**: `scripts/sync_push.sh`, `scripts/sync_pull.sh`, `.claude/settings.local.json`
```

### 051_windows_linux_vm_guide.md L74-113 「Windows -> Linux VM」セクション

```markdown
# before
## Windows → Linux VM

### Step 1: git push（コード）

```bash
# Windows Git Bash
cd /g/マイドライブ/claude
git push origin master
```

### Step 2: secrets を GCS へプッシュ

```bash
# Windows Git Bash
bash scripts/push_to_linux.sh
```

対象: `.env`, `keys/gcp-service-account.json`, `data/logs/`

### Step 3: VM側で受け取る

#### 方法A: Windowsから非対話実行（Claude Code はこちらを使う）

```bash
# Windows PowerShell から実行（--tunnel-through-iap 必須）
gcloud compute ssh claude-high-vm --zone=us-west1-a --tunnel-through-iap --command="cd ~/project/claude/investment-agent && git pull origin master && bash scripts/sync_secrets_pull.sh"
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

# after
## Windows → Linux VM

### 送り出し側（Windows で実行）

「linuxへの同期」「linuxに反映して」等の指示は、以下の2ステップで完了する。

**Step 1: git push（コード）**

```bash
# Windows Git Bash
cd /g/マイドライブ/claude
git push origin master
```

**Step 2: secrets を GCS へプッシュ**

```bash
# Windows Git Bash
bash scripts/sync_push.sh
```

対象: `.env`, `keys/gcp-service-account.json`

**送り出し側の操作はここまでで完了。** 以降の受け取り操作はユーザーから明示的に指示された場合にのみ実行する。

---

### 受け取り側（Linux VM で実行）

以下はVM側で変更を受け取る操作。送り出し側の「linuxへの同期」指示の範囲には含まれない。

**Windowsから非対話で実行する場合（`--tunnel-through-iap` 必須）:**

```bash
gcloud compute ssh claude-high-vm --zone=us-west1-a --tunnel-through-iap \
  --command="cd ~/project/claude/investment-agent && git pull origin master && bash scripts/sync_secrets_pull.sh"
```

**VMにSSHログインしてから実行する場合（`--tunnel-through-iap` 必須）:**

```bash
# 先にSSHログイン
gcloud compute ssh claude-high-vm --zone=us-west1-a --tunnel-through-iap

# VM内で実行
cd ~/project/claude/investment-agent
git pull origin master
bash scripts/sync_secrets_pull.sh
```
```

---

## 【確認できなかった事項】

- `scripts/push_to_linux.sh` がかつて存在したかの git 履歴は未確認（リネーム経緯の調査が必要だが本レビューのスコープ外）
- `scripts/sync_secrets_pull.sh`（051 MD の Step 3 で使用）が存在するかは未確認（`scripts/sync_pull.sh` と同じものか別物かの検証が必要）
- 「Linux VM -> Windows」セクション（L116-140）にも同様の送り出し/受け取り境界問題があるかの詳細分析は未実施
- 007レビューの修正が051 MDに適用された正確なタイミング（3回目事故の前か後か）は未確認。ただし依頼文から「修正済みの最新版」に対するレビューであり、方法Aが記載された状態での事故と判断
