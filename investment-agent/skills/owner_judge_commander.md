# オーナー色判定コマンダー（/owner-judge-commander）

**種別**: Skill（会話内実行）
**作成日**: 2026-05-18
**依存スキル**: `skills/owner_judge_soldier.md`（Agent型で呼び出す）

## 目的

`family_holding_candidates_classified.csv` の「要確認」行について、
`owner_judge_soldier` スキルを ticker ごとに呼び出し、結果を別CSV（owner_judge_results.csv）に追記する。

---

## 前提

| 項目 | 値 |
|------|-----|
| 入力CSV | `C:\tmp\tob_prediction\family_holding_candidates_classified.csv` |
| 出力CSV | `C:\tmp\tob_prediction\owner_judge_results.csv`（追記モード） |
| 必要列 | 発行体TICKER, 発行体名, 株主名, 最大保有比率, 区分, 判定, 判定根拠 |

---

## 手順

### Step 0: パラメータ確認

ユーザーに以下を確認（未指定ならデフォルト値を使用し確認不要）:

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| `N` | 10 | 今回の処理件数 |
| `offset` | 0 | スキップ件数（前回の続きから再開する場合） |

### Step 1: 要確認リスト取得 + ソルジャー準備

1. 入力CSV を Read する
2. 「判定」=="要確認" の行をフィルタ
3. 「発行体TICKER」でユニーク化し、対応する「発行体名」と合わせてリスト化
4. `offset` 〜 `offset+N` 件を今回の処理対象とする
5. `skills/owner_judge_soldier.md` を **一度だけ Read** し、soldier_prompt として保持する（ループ内での再Readは禁止）
6. 出力CSV が未存在の場合のみ、ヘッダー行を Bash（Git Bash）で作成する:
   ```bash
   # フォワードスラッシュ必須
   echo "発行体TICKER,発行体名,YES_NO,根拠" > C:/tmp/tob_prediction/owner_judge_results.csv
   ```
   offset 指定で再開する場合はヘッダー作成をスキップし既存ファイルに追記する
7. 処理対象件数と全要確認件数をユーザーに報告してから処理開始

### Step 2: ticker ごとにオーナー色を判定

処理対象リストを1件ずつ処理:

```
for ticker, company_name in targets:
    1. Agent(
           description="オーナー色判定 [ticker] [company_name]",
           prompt="<soldier_prompt の全文>\n\nTICKER=[ticker] 会社名=[company_name] の手順に従い判定せよ。"
       ) を呼び出す
    2. 返答の先頭行が `YES:` で始まれば YES、`NO:` で始まれば NO と判定する
       - どちらでもない場合: 再度 Agent を呼ぶか、保守的に NO として扱う
    3. Step 3 で結果CSVに追記する
```

### Step 3: 結果CSV 追記

判定結果を Bash（Git Bash）で出力CSV に1行追記する（Write/Edit は使わない）:

```bash
# フォワードスラッシュパス必須（PowerShell バックスラッシュ不可）
echo '[ticker],[company_name],[YES/NO],"[根拠1文]"' >> C:/tmp/tob_prediction/owner_judge_results.csv
```

> **注意**:
> - 追記は `>>` を使用。実行のたびにファイルを上書きしない
> - 根拠文はダブルクォート `"..."` で括ること（根拠文にカンマが含まれるとCSV崩れ防止）
> - 根拠文自体に `"` が含まれる場合は `\"` にエスケープする

### Step 4: 途中報告と続行確認

10件ごとに以下を報告し、続行するか確認する（GOシグナル待ち）:

```
[進捗] N件完了 / 今回処理 M件
YES: X件, NO: Y件
続行しますか？ (GO / 終了)
```

### Step 5: 完了報告

全処理完了後:
- YES件数
- NO件数
- スキップ件数（判定失敗）
- 処理件数合計
- 残り未処理件数（次回 offset の目安）
- 出力CSV パス

を報告する。

---

## 注意事項

- `skills/owner_judge_soldier.md` は Step 1 で **一度だけ** Read すること（ループ内での再Readは禁止）
- Bash での CSV 追記は `>>` を使用。Write/Edit ツールは使わない（トークン浪費防止）
- Agent の返答に YES/NO が明示されない場合は再度 Agent を呼ぶか、保守的に NO として扱う
- 1件の判定に失敗してもループを止めない（スキップして次の ticker へ進む）
