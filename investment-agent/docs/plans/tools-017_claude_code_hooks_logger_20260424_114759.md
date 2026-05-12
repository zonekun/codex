# セッションリカバリ堅牢化 — 同一/別ターミナル再起動の両立

**作成日時**: 2026-04-24 11:47 JST
**対象ファイル**: `scripts/claude_logger.py`（432行, commit bbdf29b 時点）, `scripts/list_claude_sessions.py`（260行）, `CLAUDE.md`
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: クラッシュリカバリ時に「セッション1件しかありません」と嘘をつく問題、および複数セッションの作業を混同する問題を根本修正する。同じターミナル再起動・別ターミナル再起動の両方で正しく動作する設計にする。

---

## 前提サマリ

- 過去修正: 本セッション内で label.txt 導入・`--exclude-session` → `--current-session` 変更・CLAUDE.md 手順改訂済み（未コミット）
- 残存: **セッション分離の根本原因が未修正**（`$PPID=1` 問題）
- 実機検証の有無: 診断コード投入済み、次プロンプトで hook データを確認予定

---

## 優先度の定義

- **P0**: セッション分離が機能しないとリカバリ全体が壊れるため最優先
- **P1**: ラベル・表示の改善。P0 が解決すれば効果が出る

---

## 根本原因

### `$PPID` = 1 問題

```
確認結果:
$ echo $PPID → 1
$ ls /c/tmp/claude_logs/ → 1/ debug.log
```

MSYS2 (Git Bash) 上では `$PPID` は常に 1（MSYS2 init プロセス）。Claude Code が hook コマンドを spawn する際、native Windows プロセス → MSYS2 bash の境界で PID マッピングが崩れ、bash の `$PPID` は MSYS2 init を指す。

**結果**: 全セッションが `C:\tmp\claude_logs\1\` に書き込まれ、以下の障害が発生:
- 複数セッションのログが同一ファイルに混在
- `list_claude_sessions.py` が常に1件しか返さない
- `--exclude-session=1` で唯一のセッションが消え、候補0件で行き詰まる
- セッション間の作業を区別する手段がない

---

## 指摘項目

### P0-1. セッションID生成メカニズムの修正 🚨

**症状**: 全 Claude Code インスタンスが同一ディレクトリ `1/` にログを書き込み、セッション分離が不可能

**該当**: `scripts/claude_logger.py:L102-L110` / `parse_session_id()`, `.claude/settings.local.json` hook command

```python:L102-L110
def parse_session_id() -> str:
    for arg in sys.argv:
        if arg.startswith("--session-id="):
            return arg.split("=", 1)[1]
    return "default"
```

```json
"command": "... --session-id=$PPID"
```

**根本原因**: `$PPID` が MSYS2 上で常に 1 になる。004 §A-5 相当（環境依存の暗黙仮定）

**修正方針**: 2段構えで修正。Phase 1 で診断、Phase 2 で実装。

#### Phase 1: hook stdin データの診断（済み）

`claude_logger.py` に診断ログを追加済み。次のプロンプトで `debug.log` に hook が受け取る JSON の全キーが記録される。

```python
write_debug(f"[diag:{event}] keys={sorted(data.keys())} session_id={data.get('session_id','N/A')}")
```

**確認ポイント**: Claude Code が `session_id` フィールドを hook data に含んでいるか。

#### Phase 2: セッションID修正（診断結果に依存）

**Case A**: hook data に `session_id` が含まれる場合（最善）
- `parse_session_id()` を `data` から取得する方式に変更
- `--session-id=$PPID` を hook command から削除
- prompt 以外のイベント（stop, tool）でも同じ session_id を受け取れることを確認

```python
# After
def resolve_session_id(data: dict, cli_fallback: str) -> str:
    """hook data の session_id を優先、なければ CLI 引数にフォールバック."""
    sid = data.get("session_id", "")
    if sid:
        return sid
    return cli_fallback
```

**Case B**: hook data に session_id が無い場合
- Windows PID ベースで Claude Code のプロセスIDを取得する

```bash
# hook command を変更
# /proc/$$/winpid → 現 bash の Windows PID
# wmic で親プロセス（Claude Code）の Windows PID を取得
"command": "BPID=$(cat /proc/$$/winpid); CPID=$(wmic process where \"ProcessId=$BPID\" get ParentProcessId /value 2>/dev/null | tr -cd '0-9'); ... --session-id=${CPID:-$$}"
```

注意点:
- `wmic` は Windows 10 で利用可能だが deprecated
- hook ごとに ~200ms のオーバーヘッド追加
- `wmic` が使えない場合のフォールバック（`$$` + タイムスタンプ）を用意

**Case C**: Case B も失敗する場合（最終手段）
- `claude_logger.py` 内でセッション自動分割ロジックを実装
- conversation.log の最終エントリから N分以上経過 → 新セッションディレクトリを生成
- `.active_session` ファイルで「現在のセッションID」を管理

```python
SESSION_SPLIT_MINUTES = 3  # 3分間プロンプト無し → 新セッション

def resolve_session_auto() -> str:
    active_file = LOG_BASE / ".active_session"
    if active_file.exists():
        sid = active_file.read_text(encoding="utf-8").strip()
        session_dir = LOG_BASE / sid
        conv = session_dir / "conversation.log"
        if conv.exists():
            age_min = (time.time() - conv.stat().st_mtime) / 60
            if age_min < SESSION_SPLIT_MINUTES:
                return sid
    # 新セッション生成
    new_sid = datetime.now(JST).strftime("sess_%Y%m%d_%H%M%S")
    active_file.parent.mkdir(parents=True, exist_ok=True)
    active_file.write_text(new_sid, encoding="utf-8")
    return new_sid
```

Case C の制約:
- 同時実行セッションが `.active_session` を上書き合う race condition
  → 対策: `.active_session` を廃止し、全セッションの conversation.log mtime を走査して最も近いものに帰属させる
- 分割閾値の選択（短すぎると1セッションが分裂、長すぎると別セッションが合流）

**検証**: 
1. 同一ターミナルで Claude Code を起動 → クラッシュ → 再起動 → `list_claude_sessions.py` で2件表示されること
2. 別ターミナルで Claude Code を起動 → 両方クラッシュ → いずれかで再起動 → 2件+現セッション1件の計3件表示されること
3. 同時に2セッション動作中 → 各セッションのログが混在しないこと

**ロールバック**: hook command と `parse_session_id()` を元に戻す。ログディレクトリ構造が変わるが旧セッションは影響なし。

---

### P0-2. セッション識別ラベル（実装済み、レビュー対象） 🚨

**症状**: リカバリ報告時に「前のセッションでは〜」と曖昧に言及し、複数セッションの作業を混同する

**該当**: `scripts/claude_logger.py` 新規追加箇所 / `scripts/list_claude_sessions.py` 新規追加箇所

**修正方針（実装済み）**: 
- `claude_logger.py`: 初回プロンプトから40文字のラベルを `label.txt` に自動保存
- `list_claude_sessions.py`: ラベルを一覧の先頭に `「〜」` で表示
- `CLAUDE.md`: 報告時にラベルで言及する義務を明記

**検証**: `list_claude_sessions.py` 実行でラベルが表示されること（確認済み）

---

### P1-1. CLAUDE.md リカバリ手順の改訂（実装済み、レビュー対象） ⚠️

**症状**: 「1件しかない場合は確認を省略」指示により、Claude がスクリプト出力を見せずに「1件です」と嘘をつく

**該当**: `CLAUDE.md` クラッシュ後の作業再開セクション

**修正方針（実装済み）**:
- 「1件なら省略」を削除。件数によらず必ずスクリプト出力をユーザーに提示
- 「セッション件数を自分で数えるな」「別セッションの作業を混同するな」を落とし穴セクションに追加
- 「セッションラベルの使用義務」セクションを新設
- `--exclude-session` を廃止、`--current-session` でマーク表示のみに変更

---

## 対応アンチパターン

| plan ID | 004 |
|---|---|
| P0-1 | A-5（環境依存の暗黙仮定: `$PPID` が一意である前提） |
| P0-2 | — |
| P1-1 | — |

---

## 検証戦略

1. **診断**: 次プロンプトで `C:\tmp\claude_logs\debug.log` を読み、hook data に `session_id` が含まれるか確認
2. **smoke test**: 
   - 修正後、同一ターミナルで Claude Code 起動 → 終了 → 再起動 → `list_claude_sessions.py` が2件のセッション（ラベル付き）を表示すること
   - 別ターミナルで Claude Code 起動 → `list_claude_sessions.py` が別セッションとして表示されること
3. **本番適用判断基準**: smoke test で同一/別ターミナル両方のシナリオが通れば本番適用
4. **回収手順**: hook command を `--session-id=$PPID` に戻し、`parse_session_id()` を元に戻す。ログディレクトリ名が変わるだけで既存ログは壊れない

---

## 実装順序

```
Phase 1: 診断（本セッション内）
  → debug.log 確認 → Case A/B/C の判定

Phase 2: P0-1 実装（Case 確定後）
  → session ID 生成ロジック修正
  → hook command 更新（settings.local.json）

Phase 3: 統合テスト
  → 同一ターミナル再起動テスト
  → 別ターミナル再起動テスト
  → 同時2セッション分離テスト

Phase 4: 診断コード削除・コミット
```

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/017_claude_code_hooks_logger.md`（親知見）
- 関連 commit: bbdf29b — 直前の HEAD
- フォーマット正本: `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット

---

## レビュー追記: 2026-04-24 12:30 JST — code-reviewer

# コードレビュー: セッションリカバリ堅牢化（$PPID=1 問題 + ラベル + CLAUDE.md 改訂）

- 日時: 2026-04-24 12:30 JST
- 対象: `docs/plans/tools-017_claude_code_hooks_logger_20260424_114759.md` + 実装済みコード差分
- パターン: 2 (改修)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: Git Bash 上の `$PPID=1` によるセッション分離不能を診断・修正する計画。副次的にセッションラベル機能と CLAUDE.md リカバリ手順の強化を実装済み
- 品質評価: **B** — P0-1 の Case A/B/C 設計は妥当で現実的なフォールバック構造になっているが、自プロジェクトの知見ファイルに既に答え（hook stdin に `session_id` が含まれる）が書かれており Case A 一択で確定できた可能性がある。実装済み部分（P0-2, P1-1）は堅実
- 主要リスク:
  1. 知見ファイル 017 に `session_id` が hook stdin に含まれると既に記載済みであり、診断フェーズが不要だった可能性
  2. Case C の race condition 対策が「対策: `.active_session` を廃止し mtime 走査」と書いているが、mtime 走査方式自体にも同時書き込みの race がある
  3. CLAUDE.md L199 の `--current-session=$PPID` が P0-1 修正後も `$PPID` のままでは意味がない（P0-1 で session_id 生成方式を変えたら CLAUDE.md の呼び出しコマンドも追従必須）

## 【パターン2のみ: 改修プラン評価】

### 妥当性

プランは `$PPID=1` が根本原因であることを正しく特定しており、方針は正しい。ただし **Case A（hook data から `session_id` を取得）が正解であることは既に `docs/knowledges/tools/017_claude_code_hooks_logger.md:L162-L164` に記載済み**。同知見ファイルの「Claude Code フック仕様メモ」テーブルに `UserPromptSubmit` / `Stop` / `PostToolUse` の全イベントで `session_id` が stdin JSON に含まれると明記されている。診断フェーズ（Phase 1）で debug.log を確認する前に、まず自分の知見ファイルを読めば Case A 確定できた。プラン作成時の知見ファイル参照不足。

### 副作用・デグレードチェック

- [ ] **`--current-session=$PPID` の整合性**: CLAUDE.md L199 でリカバリコマンドに `--current-session=$PPID` を指定しているが、P0-1 で session_id を `data["session_id"]` に変更した場合、`$PPID` では現セッションのマーク表示が正しく機能しなくなる。hook command から `$PPID` を除去するなら、CLAUDE.md 側も `--current-session` の値を hook data 由来の session_id に変更するか、引数自体を廃止して `list_claude_sessions.py` が自力で現セッションを判定する方式に変える必要がある
- [ ] **知見ファイル 017 との同期**: 017 の「セッション分離の仕組み」セクション（L33-37）に `$PPID` が一意であることを前提とした記述がある。P0-1 修正後はこの記述も更新が必要
- [ ] **label.txt の存在チェックが TOCTOU**: `save_session_label()` (`claude_logger.py:L238-L243`) で `label_path.exists()` → `write_text()` の間に別プロセスがファイルを作成する可能性がある。ただし現状は `$PPID=1` で全セッションが同一ディレクトリなので label が上書きされるリスクは低く、P0-1 修正後（セッション分離成功後）はそもそも同一ディレクトリへの同時書き込みが解消されるため実害なし。優先度は低い

### 抜け漏れ（類似観点での横展開含む）

- [ ] **CLAUDE.md「ログの仕組み」注記**: L234 に `--session-id=$PPID` でセッション分離と記載。P0-1 修正後は追従必須だが、プランに含まれていない
- [ ] **知見ファイル 017 のフック設定コード例**: L113-131 に hook command の設定例が `--session-id=$PPID` で記載。P0-1 修正後は追従必須だが、プランに含まれていない
- [ ] **list_claude_sessions.py のセッション数表示**: `render()` の出力に `セッション数: {len(sessions)}` があるが、CLAUDE.md L228 で「スクリプトの出力にある『復元候補セッション数: N』をそのまま使う」と書いている。出力の文言は「セッション数」であり「復元候補セッション数」ではない。CLAUDE.md の記述と実際のスクリプト出力が不一致
- [ ] **P0-2 / P1-1 の検証フィールド不足**: P0-2（ラベル）と P1-1（CLAUDE.md）にはテンプレート必須の「呼び出し側への波及」「ロールバック」フィールドが無い

### 新規リスク

- **Case C のセッション自動分割が導入された場合**: `SESSION_SPLIT_MINUTES = 3` は短すぎる。ユーザーが3分間プロンプトを打たないだけで新セッション扱いになる（長いレスポンスの読解中、コーヒーブレイク等）。本番運用では10-15分が妥当。ただし Case A で確定する可能性が高いため、Case C が実装される見込みは低い
- **診断コードの残存リスク**: `claude_logger.py:L444` の診断ログ行は「後で削除」とコメントされているが、全プロンプト/ツール呼び出し/停止で debug.log に書き込み続ける。削除忘れると debug.log が際限なく肥大する（既に追記のみで手動削除の運用）

## 【重大な指摘】（即修正）

### #1 知見ファイルに答えが既にある — 診断フェーズは不要

- 箇所: プラン P0-1 Phase 1 / `docs/knowledges/tools/017_claude_code_hooks_logger.md:L162-L164`
- 事象: 知見ファイル 017 の「Claude Code フック仕様メモ」に全3イベント（UserPromptSubmit / Stop / PostToolUse）の stdin JSON に `session_id` フィールドが含まれると明記されている。Case A が適用可能であることは既知
- トリガー: プラン作成時に知見ファイルの仕様メモセクションを読み飛ばした
- 影響: 診断フェーズの作業コスト（debug.log 確認 + 次プロンプト待ち）が無駄になる。致命的ではないが、Phase 1 をスキップして直接 Case A を実装できる
- 根拠: 知見ファイル L162: `UserPromptSubmit の stdin | {"prompt": "...", "transcript": [...], "session_id": "..."}`
- 推奨対応: Phase 1 をスキップし Case A を直接実装。診断コード（`claude_logger.py:L444`）は削除

### #2 CLAUDE.md の `--current-session=$PPID` が P0-1 修正後に壊れる

- 箇所: `CLAUDE.md:L199` / プラン P0-1
- 事象: P0-1 でセッションIDを `data["session_id"]` から取得する方式に変更した場合、セッションディレクトリ名は Claude Code が発行する UUID 等になる。一方 CLAUDE.md L199 の `--current-session=$PPID` は引き続き `1` を渡すため、現セッションの★マークが一切表示されなくなる
- トリガー: P0-1 修正を適用してリカバリを実行した場合
- 影響: ★マーク表示が機能しなくなる（致命ではないが UX 劣化）
- 根拠: P0-1 Case A で `--session-id=$PPID` を hook command から削除すると明記。しかし CLAUDE.md のリカバリコマンドは対象外
- 推奨対応: P0-1 の修正範囲に CLAUDE.md L199 とその注記 L234 を含める。`list_claude_sessions.py` 側で最新セッション（mtime 最大かつ `label.txt` が直近に生成された）を自動検出する方式にするか、`--current-session` 引数を廃止して hook data 由来の session_id をファイル（例: `.current_session`）経由で渡す

### #3 CLAUDE.md の「復元候補セッション数」がスクリプト出力と不一致

- 箇所: `CLAUDE.md:L228` / `scripts/list_claude_sessions.py:L218`
- 事象: CLAUDE.md には「スクリプトの出力にある『復元候補セッション数: N』をそのまま使う」と書いてあるが、スクリプトの実際の出力は `セッション数: {len(sessions)}`。Claude は指示通り出力文字列を探すが一致しないため、結局自分で数えることになる
- トリガー: リカバリ時に常に発生
- 影響: CLAUDE.md の指示が機能しない（「自分で数えるな」と書いているのに、参照先の文字列が存在しない）
- 根拠: `list_claude_sessions.py:L218` の `out.append(f"セッション数: {len(sessions)}")` と CLAUDE.md L228 の記述が不一致
- 推奨対応: CLAUDE.md の記述をスクリプト出力に合わせて「セッション数: N」に修正

## 【改善提案】（可読性・保守性）

### #1 `read_session_label()` が `claude_logger.py` に未使用

- 箇所: `scripts/claude_logger.py:L246-L252`
- 現状: `read_session_label()` 関数が定義されているが、`claude_logger.py` 内では一度も呼ばれていない。`list_claude_sessions.py` は独自に `_read_label()` を実装している
- 提案: `read_session_label()` を削除するか、`list_claude_sessions.py` の `_read_label()` を `claude_logger.read_session_label()` の import に置き換えて重複を排除する。ただし `list_claude_sessions.py` が `claude_logger.py` に依存するとモジュール結合度が上がるため、現状の独立実装を維持しつつ `claude_logger.py` 側の未使用関数を削除する方が良い

### #2 Case C の race condition 対策が不十分

- 箇所: プラン P0-1 Case C（L111-L139）
- 現状: `.active_session` の race condition を認識した上で「廃止して全セッションの conversation.log mtime を走査」と記載。しかし mtime 走査方式でも、2セッションが同時に conversation.log に書き込む場合（現状 `$PPID=1` で全セッションが同一ディレクトリ）、mtime だけでは帰属判定が不安定
- 提案: Case A が確定する見込みが高いため Case C の詳細設計は不要だが、万が一 Case C を採用する場合は、各セッションの conversation.log をロック無しで共有するのではなく、プロセス起動時に一意な session_id（PID + timestamp）を生成してディレクトリを分離すべき

### #3 診断ログ行の明示的な TODO マーカー

- 箇所: `scripts/claude_logger.py:L443-L444`
- 現状: コメントに「後で削除」と書かれているが、コメントだけでは見落としやすい
- 提案: `# TODO(P0-1): 診断完了後に削除` のような grep 可能なマーカーに変更する

## 【パターン2のみ: フォーマット適合性チェック】

- [x] 冒頭に対象ファイルの基準 commit hash が書かれているか — **OK**: `commit bbdf29b 時点` と記載
- [x] 前提サマリで過去修正と残件数が明示されているか — **OK**
- [x] 優先度の定義（P0/P1/P2 昇格基準）が冒頭にあるか — **OK**（P2 は無いが対象項目が無いため問題なし）
- [ ] 各項目が7フィールドを揃えているか — **NG**: P0-2 と P1-1 に「呼び出し側への波及」「ロールバック」フィールドが無い
- [x] 修正方針に before/after の両方が書かれているか — **OK**（P0-1 Case A に before は暗黙だが `parse_session_id()` の現行コードが `該当` セクションで示されている）
- [ ] 呼び出し側への波及が該当行リストで明示されているか — **NG**: P0-1 は CLAUDE.md や知見ファイル 017 への波及が未記載
- [x] 「既に〜がある」系の前提記述を実コードと照合 — **OK**: `parse_session_id()` のコード引用は実コードと一致
- [ ] アンチパターン対応表に T-x / G-x 列があるか — **NG**: テンプレートでは `| plan ID | 004 | T-x | G-x |` の4列だが、プランでは `| plan ID | 004 |` の2列のみ。T-x/G-x は該当なしでも列自体は必要
- [ ] 検証戦略が smoke / dev / prod / 回収手順の 4 段を網羅しているか — **部分OK**: smoke と回収手順はあるが dev / prod 段階の明示がない（本件はローカルツールなので prod = 実機利用で smoke と同一と解釈可能。ただし明示すべき）
- [x] ロールバック手順が書かれているか — **OK**: P0-1 に記載あり
- [x] 読みづらさ・デッドコードだけで P0 に置かれている項目が無いか — **OK**
- [x] 関連 commit・知見 MD へのリンクがあるか — **OK**

フォーマット違反: 3件（P0-2/P1-1 の7フィールド不足、アンチパターン対応表の列不足、呼び出し側波及の記載漏れ）

## 【確認できなかった事項】

- Claude Code の hook stdin JSON に実際に含まれる `session_id` の値の形式（UUID か数値か文字列か）。知見ファイル 017 には `"session_id": "..."` と文字列として記載されているが、実際の値は debug.log を確認するか Claude Code の公式ドキュメントを参照する必要がある。ディレクトリ名として安全な文字列であることの確認が必要
- `list_claude_sessions.py` で `limit` を適用した後の `セッション数` 表示が「全セッション数」ではなく「表示件数」になる点。`collect_sessions()` が返す件数と `render()` 内で `limit` 適用後の `len(sessions)` が異なるが、これが意図的かどうか
