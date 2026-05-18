# Claude Code <-> Codex Message Board

Append-only message board. All writes use `printf >>` / `echo >>`.

**File operation rules (BOTH Claude Code and Codex MUST follow):**
- Write / Edit (full file rewrite): **PROHIBITED** — always use append (`>>`)
- Read: allowed ONLY when you need to interpret task content (e.g. session start)
- Append (`printf >>` / `echo >>`): the ONLY permitted write method

## Format Rules

### New task entry

```
## TASK: <short-id> <timestamp>
- from: Claude Code | Codex | User
- to: Claude Code | Codex
- 関連計画MD: <path or N/A>

<task body — free-form markdown>
```

### Mark done

Append one line (do NOT edit the original task block):

```
DONE: <short-id> <timestamp> [optional one-line summary]
```

### Result report

Append a block referencing the task:

```
## RESULT: <short-id> <timestamp>

<result body>
```

## How to find active tasks

Active = has a `## TASK:` line but no matching `DONE:` line with the same short-id.

```bash
# Show active task IDs (bash example)
grep -oP '(?<=^## TASK: )\S+' docs/codex-to-claude-handoff.md | while read id; do
  grep -q "^DONE: $id " docs/codex-to-claude-handoff.md || echo "$id"
done
```

## Conventions

- short-id: kebab-case, unique enough (e.g. `supply-chain-dedup`, `tob-paired-ttest`)
- timestamp: ISO-like `YYYY-MM-DD HH:MM` JST
- Do NOT delete or edit past entries. Append only.
- Codex-related messages only. Claude Code terminal-to-terminal uses `docs/terminal-relay.md`.
- Commit & push after every append so the other side sees it.

---


## TASK: owner-judge-batch 2026-05-18 22:05
- from: Claude Code
- to: Codex
- 関連計画MD: docs/plans/20260518_tob_owner_judge_batch_handoff.md
- 関連スキル: skills/owner_judge_soldier.md

### 目的

`family_holding_candidates_classified.csv` の「要確認」銘柄（ユニーク804件）について、
各社が「オーナー色が強い（創業家・オーナー経営者が実質支配）」か否かを YES/NO で判定する Python スクリプトを作成し、可能であれば実行も行うこと。

### 入力ファイル

`C:/tmp/tob_prediction/family_holding_candidates_classified.csv`
- エンコーディング: UTF-8 BOM付き（utf-8-sig）
- 「判定」列 == "要確認" の行をフィルタ
- 「発行体TICKER」でユニーク化し「発行体名」と合わせてリスト化
- offset=5 以降から処理（offset 0〜4 は Claude Code 側で処理済み）

### 出力ファイル

**`C:/tmp/tob_prediction/owner_judge_results_codex.csv`**（新規作成、Claude側の成果物を上書きしない）

列構成（ヘッダー行）:
```
発行体TICKER,発行体名,YES_NO,根拠
```

### 判定ロジック（skills/owner_judge_soldier.md 参照）

**YES（オーナー色あり）** — 以下のいずれかを満たす:
- 創業者または一族が上位大株主（個人名 or 創業家系の資産管理会社名）
- 代表取締役・会長が創業家一族（苗字一致 or 記事に明記）
- 非上場の持株会社・興産・商事等経由で創業家が支配（必ず持株会社の背後まで調査）

**NO（オーナー色なし）** — 以下のいずれかを満たす:
- 外資系企業・機関投資家・上場親会社が支配株主で創業家なし
- 創業家が完全にexit済み
- 上場子会社（親会社が上場企業または外資）
- 検索結果に創業家の痕跡が全くなく、サラリーマン経営

**重要**: 筆頭株主が非上場のHD・持株会社の場合は「上場子会社」と判断せず、その背後の所有者まで調査してからYES/NO判定すること。

### 実装方針

irbank.net や kabutan.jp 等の公開IR情報サイトからスクレイピングして大株主情報を取得するか、または利用可能なAPIを活用すること。
情報不足で判定困難な場合は `NO`（デフォルト）として「情報不足によりデフォルトNO」と根拠列に記載。

### 参考: Claude 側処理済み結果

`C:/tmp/tob_prediction/owner_judge_results.csv` に offset 0〜4 の5件が入っている（参照のみ、上書き禁止）。

