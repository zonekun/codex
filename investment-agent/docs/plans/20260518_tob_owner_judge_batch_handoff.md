# TOB オーナー色バッチ判定 ハンドオフ

**作成日**: 2026-05-18  
**作成セッション**: 65acdd89（コンテキスト圧縮後）  
**ステータス**: 完了（2026-05-18: STEP1-2完了済み、STEP3「将来」タスクは 007_tob_ml_prediction.md § 残課題 に移行）

---

## このセッションで完了したこと

### 1. auto_classify_candidates.py 偽陽性修正

| 対応 | 内容 |
|------|------|
| `_normalize_fullwidth()` | ＆→& 変換追加 |
| `_normalize()` | 注記サフィックス・(現XX)除去追加 |
| `listed_company_names.csv` 再生成 | 4555→5279件（BQ最新版） |
| `corporate_vehicles.csv` | 26件追加（上場グループ子会社・外資日本法人等） |
| R3偽陽性削減 | 815→785件 |
| 要確認 | 1113→1105件 |
| 出力 | `family_holding_candidates_classified.csv` → Dropbox転送済み |

コミット: `eaacdd22`（supply_chain偽陽性60件修正）

### 2. オーナー色判定スキル新設（コミット 6f504536）

| ファイル | 種別 | 内容 |
|---------|------|------|
| `skills/owner_judge_soldier.md` | Agent型 | 1 ticker → WebSearch → YES/NO |
| `skills/owner_judge_commander.md` | Skill型 | 要確認N件をバッチ処理 |
| `.claude/commands/owner-judge-soldier.md` | コマンド | `/owner-judge-soldier` |
| `.claude/commands/owner-judge-commander.md` | コマンド | `/owner-judge-commander` |
| `CLAUDE.md §8` | 更新 | スキル一覧に両スキル追記 |

---

## 次セッションでやること

### STEP 1: オーナー色バッチ判定実行（最優先）【進行中】

**進捗** (2026-05-18):
- offset 0〜4: 5件処理済み → `C:/tmp/tob_prediction/owner_judge_results.csv`
  - YES: 4件（7076名南M&A・7071アンビスHD・3133海帆・8927明豊エンタープライズ）
  - NO: 1件（4772 SM ENTERTAINMENT JAPAN）
- スキル修正済み: 非上場HD経由支配の誤判定対策を `skills/owner_judge_soldier.md` に追加
- Codex依頼済み: offset=5以降799件をスクレイピングスクリプトで処理依頼（`owner_judge_results_codex.csv` に出力）



**コマンド**: `/owner-judge-commander`

**入力ファイル**:
- `C:\tmp\tob_prediction\family_holding_candidates_classified.csv`（要確認1105件）

**出力ファイル**:
- `C:\tmp\tob_prediction\owner_judge_results.csv`（新規作成、追記モード）

**実行方法**:
```
/owner-judge-commander
```
- N=10（デフォルト）で開始、結果を確認しながら offset を増やして継続
- YES → ASSET_MGMT 相当（オーナー系資産管理会社）
- NO → 要確認維持（除外対象から外す）

**確認事項**:
1. `C:\tmp\tob_prediction\` フォルダが存在すること
2. 入力CSVが最新版であること（`family_holding_candidates_classified.csv`）
3. owner_judge_results.csv が存在しない場合は初回実行でヘッダー作成

### STEP 2: 結果のCSVマージ【完了】

- `owner_judge_results.csv`（Claude判定5件）+ `owner_judge_results_batch.csv`（バッチ799件）を統合
- `family_holding_candidates_classified_r4.csv` に反映: YES→ASSET_MGMT(619件) / NO→要確認維持(486件)
- BQ `STOCK.SHAREHOLDER_COMPOSITION_EXTEND` UPDATE: 619件 PRIVATE_CORP→ASSET_MGMT
- `compute_owner_features.py --mode full`: 37,607行の特徴量更新完了

### STEP 3: TOB ML予測モデルへのフィードバック（将来）

「007_tob_ml_prediction.md」のプランMD(TOP10_NAMES_JSON TYPE付与)と連携。
オーナー判定結果を特徴量として活用する。

---

## 重要ファイルパス

| ファイル | パス |
|---------|------|
| 入力CSV | `C:\tmp\tob_prediction\family_holding_candidates_classified.csv` |
| 出力CSV | `C:\tmp\tob_prediction\owner_judge_results.csv` |
| ソルジャースキル | `skills/owner_judge_soldier.md` |
| コマンダースキル | `skills/owner_judge_commander.md` |
| 知見MD（TOB MLモデル） | `docs/knowledges/analysis/007_tob_ml_prediction.md` |

---

## 注意事項

- コマンダーはソルジャーMDをループ外で**一度だけ** Read する
- 結果CSVへの追記は `Bash echo >>` を使う（Write/Edit は使わない）
- 根拠文はダブルクォートで括る（カンマ対策）
- Bash パスはフォワードスラッシュ必須
