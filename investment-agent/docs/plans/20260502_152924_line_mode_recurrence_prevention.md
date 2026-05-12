# LINE会話モード糞詰まり再発防止 — 改修計画 v3

**作成日時**: 2026-05-02 15:29 JST（v3: 16:10 JST 改訂）
**対象ファイル**: `CLAUDE.md`（L65-86, L99-107）, `docs/knowledges/tools/068_line_ntfy_push.md`（L222-249）
**基準 commit**: 56bc358
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: 2026-05-02 LINE会話モード糞詰まりの再発防止。3点を修正: (1) Bashパス表記ルール追加、(2) auto-BG対策のPowerShell拡大、(3) LINE会話モードのsend_ntfy_and_waitをBGデフォルト化（FG必須ルール撤回）。

> **v1→v2**: ユーザーから「全員的外れ。bash円マーク解決すべし」→ PowerShell固定からBashフォワードスラッシュルールに転換
> **v2→v3**: ユーザーから「双方向会話モードは全てBGにすれば問題解決するのでは？緊急時のみFG」→ FG必須ルールを撤回しBGデフォルト化

---

## 前提サマリ

- 過去修正: 2026-04-29 事故でレビュー017〜023作成済み。068 MD L222-249に「フォアグラウンド必須」ルール追記
- 残存: 今回の事故2件 + FG必須ルール自体の限界（ツールtimeout上限10分で返信取りこぼし）
- 実機検証: 2026-05-02 本番事故
- v1レビュー: 058（評価B、ユーザー「全員的外れ」）、v2レビュー: 059（評価A）

---

## 優先度の定義

- **P0**: LINE会話モード中の通信断絶を防ぐ改修
- **P1**: ドキュメント整合性

---

## 指摘項目

### P0-1. CLAUDE.md §実行環境にBashパス表記ルール追加 🚨

**症状**: エージェントがBashコマンドで `C:\venvs\...` とバックスラッシュで書き、Git Bashで `command not found`。

**該当**: `CLAUDE.md:L107`（gcloudルールの後）

**根本原因**: CLAUDE.md §実行環境にBash用パス変換ルールがない。L104で `C:\gdrive\...` とWindows形式で記載しており、エージェントがそのままBashコマンドに持ち込む。

**修正内容**:

```markdown
# before (CLAUDE.md L107の後に項目なし)

# after (L107の後に1項目追加)
- **Bash パス表記**: Windows パス `C:\...` はそのまま Bash に渡せない（`\` がエスケープ文字）。Bash ツールではフォワードスラッシュ `C:/venvs/investment-agent/Scripts/python.exe` または Unix 形式 `/c/venvs/...` を使う。日本語 Windows では `\` が `¥`（円マーク）表示で視認しにくい点にも注意
```

**呼び出し側への波及**: なし
**検証**: `Bash(command="C:/venvs/investment-agent/Scripts/python.exe --version")`
**ロールバック**: git revert

---

### P0-2. 068知見MDにBashパス表記の落とし穴セクション追加 🚨

**症状**: 068知見MDの落とし穴セクション（L222〜）にパス表記の注意事項がない。

**該当**: `docs/knowledges/tools/068_line_ntfy_push.md:L222以降`（新セクション追加）

**修正内容（L249の後に追加）**:

```markdown
### 落とし穴: Bash パス表記 — フォワードスラッシュ必須

Bash ツールで notify.py を呼ぶ場合、**Windows パス `C:\...` はそのまま使えない**。`\` は Bash のエスケープ文字。

```bash
# OK: フォワードスラッシュ
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/notify.py ntfy "メッセージ"

# OK: Unix 形式
PYTHONUTF8=1 /c/venvs/investment-agent/Scripts/python.exe scripts/notify.py ntfy "メッセージ"

# NG: バックスラッシュ → command not found
PYTHONUTF8=1 C:\venvs\investment-agent\Scripts\python.exe scripts/notify.py ntfy "メッセージ"
```

**日本語 Windows 固有の罠**: `\`（U+005C）が `¥`（円マーク）として表示される。エラーメッセージのパスが `C:¥venvs¥...` と表示され、パス区切り問題に気付きにくい（2026-05-02 事故）。

**注意**: Python コード内のパス（`open('C:/tmp/...')`等）はフォワードスラッシュで動作する。ただし Git Bash 固有の `/c/tmp/` 形式は Python から認識されないため使わないこと。
```

**呼び出し側への波及**: なし
**検証**: Bashツールで `C:/venvs/...` 形式のnotify.py呼び出しが正常動作
**ロールバック**: git revert

---

### P0-3. LINE会話モード: send_ntfy_and_wait をBGデフォルトに変更 🚨

**症状**: 現行の「FG必須」ルール（CLAUDE.md L78, 068 MD L225-237）はツールtimeout上限600000ms（10分）に制約され、10分以内にユーザーが返信しないと返信を取りこぼす。また、timeout未指定時にシステムが自動BG化する問題も完全には防げない。

**該当**:
- `CLAUDE.md:L78` — `send_ntfy_and_wait()` は**必ずフォアグラウンド実行**
- `CLAUDE.md:L79` — 先行BGの残存ルール
- `docs/knowledges/tools/068_line_ntfy_push.md:L223-237` — フォアグラウンド必須セクション

**根本原因**: 4/29事故でFG必須ルールを導入したが、そもそもBG実行 + task-notification処理でループを維持する方が、ツールtimeout制約を回避でき安定する。4/29の真因は「BG完了通知を処理しなかった」ことであり「BGで実行したこと」ではなかった。

**修正方針**: LINE会話モード中の send_ntfy_and_wait を `run_in_background=true` デフォルトに変更。task-notification 受信時に返信を処理し、次の send_ntfy_and_wait(BG) を発行するループ。FGはBG詰まり等の緊急時のフォールバック。

**修正内容**:

#### CLAUDE.md §ライン会話モード（L77-79 書き換え）

```markdown
# before (L78-79)
- `send_ntfy_and_wait()` は**必ずフォアグラウンド実行**（`run_in_background=true` 禁止）。ブロック回避は `timeout` で行う
- **先行 send_ntfy_and_wait がBGで残存していても気にせず新規 send_ntfy_and_wait をBGで実行してよい**（IDフィルタで混信しない）。send_ntfy へのダウングレードは理由を問わず禁止

# after
- `send_ntfy_and_wait()` は `run_in_background=true` で実行する。task-notification 受信時に必ず出力ファイルを読み、返信内容を処理する。処理後、次の `send_ntfy_and_wait()` を再び BG で発行してループを維持する。send_ntfy へのダウングレードは理由を問わず禁止
- **FG実行はフォールバック**: BG詰まり・task-notification未着等の緊急時のみ `run_in_background` なし（FG）で実行してよい。FG時はツールの `timeout` パラメータに `600000`（10分、ミリ秒）を指定して auto-BG 化を防ぐ
```

#### 068知見MD §落とし穴セクション（L223-237 書き換え）

```markdown
# before (L223-237)
### 落とし穴: `send_ntfy_and_wait` の実行方法 — LINE会話モード中はフォアグラウンド必須

**LINE会話モード中（CLAUDE.md §ライン会話モード、`line_conversation_mode.md` active: true）**:
- **フォアグラウンド実行が必須**。`run_in_background=true` は**禁止**
- バックグラウンド実行は返信受信ループを断絶させる（2026-04-29 事故: 9通一方通行、7時間放置）
- Bash ツールが auto-background する問題の対処: **Bash ツールの `timeout` パラメータに `600000`（最大値10分、ミリ秒）を明示指定**する。ntfy の `--timeout`（秒）とは別物。ntfy timeout は memory の値（デフォルト10800秒）を使い、**絶対に短縮しない**（2026-04-29 事故: timeout=300に短縮→5分でタイムアウト→ユーザー返信取りこぼし）

# after
### 落とし穴: `send_ntfy_and_wait` の実行方法 — LINE会話モード中はBGデフォルト

**LINE会話モード中（CLAUDE.md §ライン会話モード、`line_conversation_mode.md` active: true）**:
- `run_in_background=true` で実行する（BGデフォルト）
- task-notification 受信時に出力ファイルを読み、返信を処理 → 次の `send_ntfy_and_wait` をBGで発行してループ継続
- ntfy timeout は memory の `line_conversation_mode.md` の値を優先。デフォルト 10800秒。**絶対に短縮しない**
- 4/29事故の教訓: BGが問題だったのではなく、**task-notification を処理しなかった**ことが問題。BGで送信しても task-notification を確実に処理すればループは維持される
- **FGフォールバック**: BG詰まり・task-notification未着等の緊急時のみFGで実行。FG時は Bash / PowerShell ツールの `timeout` に `600000`（10分、ミリ秒）を指定して auto-BG 化を防ぐ
```

#### 068知見MD L239-247（LINE会話モード外）はそのまま維持

```markdown
**LINE会話モード外（通常モード）**:
- `run_in_background=true` で実行可。返信到着時に task-notification が届く
- フォアグラウンド実行するとセッションが timeout 秒ブロックされるため非推奨
```

**呼び出し側への波及**: CLAUDE.md L78-79 の変更がLINE会話モードの全操作に影響。ただし動作フロー自体は「send_ntfy_and_wait → 返信受信 → 処理 → 次のsend_ntfy_and_wait」で同じ。FG/BGの違いのみ。

**検証**:
1. LINE会話モード中に `send_ntfy_and_wait` をBG実行
2. ユーザー返信 → task-notification → 出力ファイル読み取り → 返信処理
3. 次の `send_ntfy_and_wait` をBG発行 → ループ継続確認

**ロールバック**: CLAUDE.md + 068 MDのテキスト変更のみ。git revert で即復旧可能。旧ルール（FG必須）に戻すだけ

---

### P0-4. auto-BG対策をPowerShellにも拡大（FGフォールバック時用） 🚨

**症状**: P0-3でFGフォールバック時にtimeout=600000を指定するルールを設けるが、現行068 MD L228はBashのみ言及。

**該当**: `docs/knowledges/tools/068_line_ntfy_push.md:L228`

**修正内容**: P0-3の書き換え内容に含む（「Bash / PowerShell ツールの `timeout` に `600000`」と記載済み）

---

### P1-1. 既存feedbackメモリの更新 ⚠️

**内容**: `feedback_ntfy_foreground_only.md` を以下に更新:
- 名前: `send_ntfy_and_wait はBGデフォルト + task-notification処理義務`
- L11「必ずフォアグラウンドの Bash で」→「`run_in_background=true` で実行。task-notification 受信時に必ず出力ファイルを読んで返信処理。Bashの場合はパスをフォワードスラッシュ（`C:/venvs/...`）にすること。FGフォールバック時はtimeout=600000ms必須（Bash/PowerShell共通）」

**目的**: 既存feedbackのFG必須→BGデフォルト方針変更を即時反映。

---

## 検証戦略

1. **smoke test**: LINE会話モード中に `send_ntfy_and_wait` をBG実行。ユーザー返信 → task-notification → 返信処理 → 次のBG send_ntfy_and_wait 発行。ループが2往復以上継続すること
2. **パス表記確認**: Bashツールで `C:/venvs/...` と `/c/venvs/...` 両方のnotify.py呼び出しが正常動作
3. **本番適用判断基準**: smoke test PASS で適用。ドキュメント変更のみ
4. **回収手順**: git revert。旧ルール（FG必須）に復旧

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/068_line_ntfy_push.md`（親知見）
- v1レビュー: `docs/reviews/058_cr_line_mode_recurrence_prevention.md`（ユーザー「全員的外れ」）
- v2レビュー: `docs/reviews/059_cr_line_mode_recurrence_prevention_v2.md`（評価A）
- 関連レビュー: `docs/reviews/017_line_mode_no_reply.md` 〜 `docs/reviews/023_ntfy_background_conversation_break.md`
- フォーマット正本: `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット

---

## 実施記録

**ステータス: 全項目実施完了（2026-05-02 16:20 JST）**

| 項目 | 状態 | 実施内容 |
|------|------|---------|
| P0-1 | ✅ | CLAUDE.md L108 に Bash パス表記ルール追加 |
| P0-2 | ✅ | 068 MD L253-271 に Bash パス表記落とし穴セクション追加 |
| P0-3 | ✅ | CLAUDE.md L78-79 FG必須→BGデフォルト化 + 068 MD L223-237 同 |
| P0-4 | ✅ | P0-3に含む（Bash/PowerShell両対応記載済み） |
| P1-1 | ✅ | feedback_ntfy_foreground_only.md 更新 + MEMORY.md サマリ更新 |
| 004-1 | ✅ | 自己課題 `md:review-quality-low` 記録済み（BGエージェント） |

**smoke test**: 次回LINE会話モードでのBG send_ntfy_and_wait → task-notification → 返信処理ループで検証予定
