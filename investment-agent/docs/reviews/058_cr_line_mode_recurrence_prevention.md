# コードレビュー: LINE会話モード糞詰まり再発防止 — 068知見MD改修計画

- 日時: 2026-05-02 15:32 JST
- 対象: `docs/plans/20260502_152924_line_mode_recurrence_prevention.md`
- パターン: 2 (既存コード改修 / バグ修正のレビュー)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: 2026-05-02 のLINE会話モード事故2件（Git Bash上C:\パス解決失敗、PowerShell auto-background化）に対し、068知見MDの落とし穴セクション更新 + feedbackメモリ追加で再発防止する計画
- 品質評価: **B** — 直接原因2件への対処は的確だが、フォーマット面の欠落が複数あり、修正範囲にも抜け漏れがある
- 主要リスク:
  1. 068 MD §①/§② のCLI例（L46-50, L80-86）がBash形式のまま残存する矛盾
  2. CLAUDE.md §実行環境の gcloud Git Bash 優先原則（L107）と notify.py PowerShell固定ルールの整合性が未整理
  3. feedbackメモリの粒度が既存2件（foreground_only, line_timeout）と重複する要素を含む

## 【改修プラン評価】

### フォーマット適合性チェック

- [x] 冒頭に対象ファイルの基準 commit hash が書かれているか — **OK** (commit 56bc358)
- [x] 前提サマリで過去修正と残件数が明示されているか — **OK** (2026-04-29事故6件、今回の未カバー2件)
- [x] 優先度の定義（P0/P1/P2 昇格基準）が冒頭にあるか — **OK**
- [ ] 各項目が「症状 / 該当 / 根本原因 / 修正方針 / 呼び出し側波及 / 検証 / ロールバック」7 フィールドを揃えているか — **P0-1, P0-2はOK。P1-1は7フィールド構成でない**（feedbackメモリ追加なので厳密にはコード改修項目と構成が異なるが、目的・内容・検証のみで検証が欠落）
- [x] 修正方針に before/after の両方が書かれているか — **OK** (P0-2のL228書き換え前後が明示)
- [x] 呼び出し側への波及が該当行リストで明示されているか — **OK** (全項目「なし」と明示)
- [ ] 「既に〜がある」系の前提記述を実コードと照合し、食い違いが無いか — **要検証あり** (後述 #1)
- [ ] アンチパターン対応表（plan ID → 004/T/G）が末尾にあるか — **欠落**
- [ ] 検証戦略が smoke / dev / prod / 回収手順の 4 段を網羅しているか — **部分的**: smoke testは定義されているがdev/prodの区別がない。ドキュメント変更のみなので妥当だが、明示的に「ドキュメント変更のみのため dev/prod 区別なし」は記載済み（L146）。回収手順もgit revert記載あり。**概ね OK だがフォーマット上は4段テンプレートに沿っていない**
- [x] ロールバック手順が書かれているか — **OK** (git revert)
- [x] 読みづらさ・デッドコードだけで P0 に置かれている項目が無いか — **OK** (P0は通信断絶の直接原因)
- [ ] 関連 commit・知見 MD・incident ログへのリンクがあるか — **部分的**: 関連レビュー4件リンク済み。ただし今回の事故自体のincident記録（レビュー番号 or セッションID）へのリンクが欠落

**フォーマット違反**:
1. アンチパターン対応表が末尾にない（テンプレート必須項目）
2. P1-1の7フィールド構成が不完全

### 妥当性

計画は2件の事故の真因に対処している。

- **事象1（Bash C:\パス解決失敗）**: Git Bash on Windows で `C:\` パスが解釈できない問題は実在する技術的制約であり、「PowerShell固定」は正しい対処方針。notify.py がWindows venvのPython実行パスを必要とする以上、POSIX shell での呼び出しは原理的に不安定。
- **事象2（PowerShell auto-background化）**: L228の「Bash ツール」限定記述を「Bash / PowerShell 両ツール」に拡大するのは正しい。Claude Code の PowerShell ツールにも timeout パラメータが存在し、同じ auto-background 問題が発生することは PowerShell ツール定義から確認できる（「timeout: Optional timeout in milliseconds (max 600000)」）。

**対症療法の懸念なし**: 直接原因（シェル選択ミス、timeout未指定）に対する根本対処であり、対症療法ではない。

### 副作用・デグレードチェック

- [x] 068 MD §①/§② の通常利用（LINE会話モード外）の単方向通知に影響はないか: **要注意** — P0-1は「LINE会話モード中のコード例をPowerShell形式に変更」と記述しているが、068 MD L46-50, L80-86 の**§①/§② のCLI例**はBash形式のまま残る。LINE会話モード中はPowerShell固定で、LINE会話モード外の通常利用はBash形式のまま、という二重基準が残ることになる。これは混乱の種になりうるが、CLAUDE.md §実行環境 L107「gcloud: Git Bash を第一選択」との整合性を考えると、notify.py **全体を** PowerShell固定にすべきか、LINE会話モード中のみPowerShell固定にすべきかの判断が必要。
- [x] 既存の落とし穴セクション（L222-249）のBash例との整合性: P0-2の修正でL230-237のコード例がPowerShell形式に変更されるため、L232-236の「Bash timeout=600000ms」の既存例は削除される。これは意図通り。
- [x] CLAUDE.md §ライン会話モード（L78）の「`send_ntfy_and_wait()` は必ずフォアグラウンド実行」ルールとの整合性: **整合**。PowerShell固定 + timeout=600000 で auto-background 回避というのは、CLAUDE.md のフォアグラウンド必須ルールの実現手段として適切。

### 抜け漏れ（類似観点での横展開含む）

- [ ] **068 MD §①/§② のCLI例（L46-50, L80-86）の更新が計画に含まれていない**: P0-1は「LINE会話モード中のコード例をPowerShell形式に変更」と限定しているが、§① L46-50 と §② L80-86 のCLI例もBash形式（`PYTHONUTF8=1 C:/venvs/...`）で書かれている。LINE会話モード外でもBashからnotify.pyを呼ぶ場面があるため、このBash形式が残ること自体は問題ないが、LINE会話モード中に§①/§②のCLI例を参照してBashで実行してしまうリスクがある。P0-1の追記セクションの冒頭に「§①/§②のCLI例はLINE会話モード外（通常モード）で使用する。LINE会話モード中はこちらのPowerShell形式を使うこと」の注記を追加すべき。
- [ ] **068 MD §③ ライン会話モード セクション（L129-182）のコード例（L164-176）**: Pythonスクリプトとしての擬似コード例だが、AIが実行時にこの例を参照して `Bash(command="python -c '...'")` で呼び出す可能性がある。§③のコード例にも「※ 実行シェルは§落とし穴セクション参照」等の注記があると安全。
- [ ] **CLAUDE.md への反映**: 今回の改修計画は068知見MDのみを対象としている。CLAUDE.md §ライン会話モード（L65-86）にはシェル選択に関する記述がない。CLAUDE.md の正本性を考えると、少なくとも「実行方法の詳細は068知見MDの落とし穴セクションを参照」程度のポインタがあると、068を読み飛ばした場合の導線が確保される。ただし CLAUDE.md のトークン削減方針（レビュー026で指摘済み）との兼ね合いがあるため、ポインタ追加は任意改善。
- [ ] **send_ntfy（一方通行）のシェル選択**: 計画はsend_ntfy_and_wait（双方向）のみを対象としているが、LINE会話モード外で使うsend_ntfy（一方通行）もnotify.pyを呼ぶ。send_ntfyは長時間ブロックしないため auto-background 問題は発生しないが、Git Bash での C:\パス問題は同様に発生する。send_ntfy のシェル選択ルールが未整理。事故としては発生していないため P2 相当だが、横展開として記載すべき。

### 新規リスク

- **シェル選択ルールの衝突**: CLAUDE.md §実行環境 L107 は「gcloud: Git Bash を第一選択。クォートを含む複雑なコマンドは PowerShell で事故りやすいので Git Bash 優先の原則は維持」と記載。notify.py を「PowerShell固定」にすると、スクリプト実行のシェル選択ルールが「gcloud=Bash優先、notify.py=PowerShell固定、その他=PYTHONUTF8=1付きで任意」と複雑化する。これは新規の混乱リスクだが、notify.py固有の問題（C:\パス + 長時間ブロック）を考えると PowerShell 固定は妥当。ルールの複雑化を認識した上で、068の落とし穴セクションに「なぜPowerShell固定か」の理由が記載されているので許容範囲。
- **PowerShell ツール固有の問題の未調査**: PowerShellツールで日本語メッセージを含む send_ntfy_and_wait を実行した場合のエンコーディング問題が検証されていない。P0-1 L79 で「日本語文字列を含む場合は一時ファイルを推奨」と記載されており認識はあるが、一時ファイル方式のコード例が具体的でない（`C:\tmp\ntfy_tmp.py` に書き出すとだけ記載）。smoke testで日本語メッセージの送受信を検証項目に含めるべき。

## 【重大な指摘】（即修正）

### #1 P0-1 修正範囲の不足: §①/§② CLI例との整合性

- 箇所: `docs/plans/20260502_152924_line_mode_recurrence_prevention.md:51-53`
- 事象: P0-1は「LINE会話モード中のコード例をPowerShell形式に変更」と記述するが、068 MD §① L46-50、§② L80-86 のCLI例がBash形式のまま残る。AIが LINE会話モード中にこれらのセクションを参照してBash実行するリスクがある。
- トリガー: LINE会話モード中に、§落とし穴セクションを読み飛ばし、§①/§②のCLI例を直接参照した場合
- 影響: 事象1（C:\パス解決失敗）の再発
- 根拠: 068 MD L46-50 `PYTHONUTF8=1 <python> scripts/notify.py ntfy "..."` はBash形式のまま
- 推奨対応: P0-1の修正内容に「§①/§②のCLI例の先頭に『※ LINE会話モード中は§落とし穴セクションのPowerShell形式を使うこと』の注記を追加する」を含める。あるいは§①/§②にPowerShell形式の並記を追加する。

### #2 feedbackメモリ（P1-1）の粒度が既存feedbackと重複

- 箇所: `docs/plans/20260502_152924_line_mode_recurrence_prevention.md:134-138`
- 事象: P1-1のfeedbackメモリ内容「notify.pyはPowerShellツール固定で実行。Bashツール禁止。timeout=600000ms必須」は、既存の `feedback_ntfy_foreground_only.md`（フォアグラウンド必須）と `feedback_line_timeout_from_memory.md`（timeout値の規定）の内容と重複する部分がある。特に「timeout=600000ms必須」は feedback_line_timeout_from_memory.md で既にカバーされている概念（Bashツールの timeout パラメータとntfy --timeoutの区別）。
- トリガー: 3つのfeedbackメモリが同時にロードされた場合に、ルールの重複・微妙な矛盾が生じる
- 影響: feedback過多によるルール衝突リスク（低）
- 根拠: `feedback_ntfy_foreground_only.md` L7-11 は「必ずフォアグラウンドの Bash で」と記載しており、今回のPowerShell固定ルールと矛盾する（「Bash で」の部分）
- 推奨対応: 新規feedbackを作成するのではなく、既存の `feedback_ntfy_foreground_only.md` を更新して「必ずフォアグラウンドの **PowerShell** で」に修正し、PowerShell固定 + timeout=600000ms の情報を統合する。別途新規feedbackが必要なら、スコープを「シェル選択: PowerShell固定」のみに絞り、timeout関連は既存feedbackに任せる。

### #3 P0-2 修正内容のコード例に不整合

- 箇所: `docs/plans/20260502_152924_line_mode_recurrence_prevention.md:113-123`
- 事象: P0-2のコード例（L116）で `PowerShell(command='$env:PYTHONUTF8="1"; & "<python>" "C:\tmp\ntfy_tmp.py"', timeout=600000)` と書かれている。しかしこれは一時ファイル方式（P0-1 L79 で言及）の実行例であり、直接CLIで `scripts/notify.py ntfy "..." --wait --timeout 10800` を呼ぶ形式のコード例が並記されていない。一時ファイル方式のみが提示されると、単純なメッセージ送信にも一時ファイルが必要と誤解される。
- トリガー: AIが068の落とし穴セクションを読んで実行方法を決定する際
- 影響: 不必要に複雑な一時ファイル方式を常に採用し、C:\tmp にゴミファイルが残る
- 根拠: P0-1 L79 は「日本語文字列を含む場合」に一時ファイルを推奨としているが、P0-2のコード例はこの条件分岐を反映していない
- 推奨対応: P0-2のOKコード例を2つに分ける: (a) シンプルな直接実行（`& "C:\venvs\...\python.exe" scripts/notify.py ntfy "メッセージ" --wait --timeout 10800`）、(b) 日本語文字列を含む場合の一時ファイル方式

## 【改善提案】（可読性・保守性）

### #1 068 MD §③ のPython擬似コード例への注記追加

- 箇所: `docs/knowledges/tools/068_line_ntfy_push.md:157-176`
- 現状: §③のPython擬似コードはnotify.pyの呼び出し方をPython importで示しているが、AIがこれをBash内の `python -c` で実行する可能性がある。実際に事象1はこのパターンで発生した。
- 提案: L157 `**実装**:` の直後に「※ Claude Codeからの呼び出し時はPowerShellツールを使用すること（§落とし穴セクション参照）」を追記する。計画のスコープに含めると修正範囲が明確になる。

### #2 smoke test の検証項目に日本語メッセージの送受信を追加

- 箇所: `docs/plans/20260502_152924_line_mode_recurrence_prevention.md:144`
- 現状: smoke testは「PowerShell + timeout=600000 で send_ntfy_and_wait を1回実行。送信・受信が正常に行われることを確認」のみ。
- 提案: 日本語メッセージ（マルチバイト文字）での送受信を検証項目に追加。PowerShellのデフォルトエンコーディング（UTF-16 LE）とPython の PYTHONUTF8=1 の組み合わせで問題が発生しないことを確認すべき。

## 【確認できなかった事項】

- PowerShellツールの auto-background 発動条件の詳細: Bashツールと同じく timeout パラメータ未指定時にデフォルト 120000ms（2分）を超えるコマンドが auto-background されるのか、PowerShell固有の閾値があるのかは、ツール実装を確認しないと判定不能。ただし timeout=600000 を明示指定すればいずれにせよ回避できるため、計画の妥当性には影響しない。
- `feedback_ntfy_foreground_only.md` L10 の「必ずフォアグラウンドの Bash で」という記述が、今回のPowerShell固定ルールと明示的に矛盾することの影響度: 次セッション開始時にこのfeedbackが読まれると「Bash で実行すべき」と解釈される可能性がある。計画にfeedback更新が含まれているため対処は可能だが、既存feedbackの更新が計画のスコープに明記されていない。
- 068 MD L46-50, L80-86 のCLI例がLINE会話モード外で実際にBashから正常に動作するかどうか: Git Bash で `C:/venvs/...` （スラッシュ形式）は動作する可能性があるが、`C:\venvs\...` （バックスラッシュ形式）は動作しない。既存のCLI例は `/` 形式で記載されており、Bash互換の可能性がある。今回の事故が `\` 形式で発生したのか `/` 形式でも発生したのかは、事故ログの詳細を確認しないと判定不能。
