# コードレビュー: earnings_model_core.py 共通化リファクタリング + 6因子改善

- 日時: 2026-05-03 19:59 JST
- 対象: `scripts/earnings_model/earnings_model_core.py` (新規), `batch_rerun_predict.py` (import切替), `earnings_model_predict.ipynb` (Cell 3/7/9 import切替)
- パターン: 1 (新規) + 3 (ad-hoc)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: predict ノートブックと batch_rerun で重複していた compute_score / score_to_prediction / classify_return / 四半期定数 / GCS保存カラム一覧を `earnings_model_core.py` に一元化。同時に6項目のTODO改善（F5 EPS追加、F12 PEG全Q拡張、F15 黒字転換、F16 FY未達、IFRS OdPフォールバック、低ベースフィルタ）を core.py に反映。
- 品質評価: **B** — 一元化の設計は適切で6因子改善が正しく反映されているが、Cell 5 のローカル定数再定義による名前空間衝突リスク、F5 低ベース時のEPS無効化漏れ、EPS負値ガード不足が存在。
- 主要リスク:
  1. Cell 5 で Q_MAP / CUM_PREV_Q / PREV_Q_MAP をローカル再定義 → core版をシャドウイングし、将来の定数変更時にサイレント乖離
  2. F5 低ベース判定時に `next_year_op_change` は5年中央値ベースに差替わるが、`next_year_eps_change` は差替えられない → 低ベース銘柄でEPS成長率が過大評価される
  3. EPS がマイナス（赤字）の場合、`float(_jq_eps) > 0` ガードで `next_year_eps_change = None` となるが、EPS=0 のケースでもガードが効いてしまい NxFEPS > 0 の有意な情報を捨てる

## 【重大な指摘】（即修正）

### #1 Cell 5 の Q_MAP / CUM_PREV_Q / PREV_Q_MAP ローカル再定義が core 版をシャドウイング

- 箇所: `earnings_model_predict.ipynb` Cell 5（`-- 1-7. BQ: Q-on-Q` セクション内）
- 事象: Cell 3 で `from earnings_model_core import Q_MAP, CUM_PREV_Q, PREV_Q_MAP, ...` を実行しているにもかかわらず、Cell 5 で同名の変数を再代入している:
  ```python
  Q_MAP: dict[str, str] = {'1Q': '1Q', '2Q': '2Q', '3Q': '3Q', 'FY': '4Q'}
  CUM_PREV_Q: dict[str, str] = {'2Q': '1Q', '3Q': '2Q', 'FY': '3Q'}
  PREV_Q_MAP: dict[str, str] = {'2Q': '1Q', '3Q': '2Q', '4Q': '3Q'}
  ```
- トリガー: Cell 5 を実行した時点で、Cell 3 で import した core 版がノートブックのグローバル名前空間で上書きされる。**現時点では値が同一のため動作に影響しない**が、将来 core.py の定数を変更した際に Cell 5 のローカル版が古い値のまま残り、compute_features（Cell 5）と compute_score（Cell 7, core版）で異なる四半期マッピングが使われる。
- 影響: サイレントなロジック乖離。Q_MAP を変更するケース（例: 半期決算対応、FY を別ラベルにする等）で Cell 5 の特徴量計算と Cell 7 の core版スコアリングで四半期ラベルが不整合になり、スコア計算が壊れる。
- 根拠: Cell 3 で `from earnings_model_core import Q_MAP, ...` 後、Cell 5 で `Q_MAP: dict[str, str] = {...}` が同名変数を上書き。Python のノートブック名前空間では後勝ち。
- 推奨対応: Cell 5 の3行のローカル再定義を削除する。Cell 3 で import 済みの core 版がそのまま使われる。

### #2 F5 低ベース判定時に `next_year_eps_change` が5年中央値ベースに差替わらない

- 箇所: `batch_rerun_predict.py:581-587`, `earnings_model_predict.ipynb` Cell 5（Low base detection セクション）
- 事象: 低ベースフラグが立った場合、`next_year_op_change` は `(nx_fop - _median_5y_op) / abs(_median_5y_op)` に差替えられる。しかし `next_year_eps_change` は差替えられず、今期の低い EPS をベースにした成長率がそのまま compute_score の F5 に渡る。
- トリガー: FY 決算で今期 OP が5年中央値の50%未満かつ EPS 成長率 > OP 成長率の銘柄。典型例: Celonis減損で今期赤字の NRI（4307）で EPS が極端に低い場合、`next_year_eps_change` が +500% 等の異常値になり F5 で `max(op_change, eps_change)` により eps_change が選択される。
- 影響: 低ベースフィルタの効果が EPS 経由で迂回され、4307 NRI のような事故が再現する。低ベースフィルタの設計意図（5年中央値ベースで正規化）が EPS 成長率に適用されないことで、F5 が過大評価を返す。
- 根拠: batch_rerun_predict.py:581-587 で `_is_low_base = True` 後に `next_year_op_change` のみ差替え。`next_year_eps_change` は同ブロックに登場しない。core.py:102-103 で `_candidates = [v for v in [_nyc_op, _nyc_eps] if ...]` → `nyc = max(_candidates)` により EPS 成長率が op_change を上回れば EPS 側が選択される。
- 推奨対応: 低ベースフラグ時は `next_year_eps_change = None` にして EPS 成長率を無効化するか、EPS にも5年中央値ベースの差替えロジックを実装する。前者が最小修正。batch_rerun と notebook 両方で修正が必要だが、いずれも compute_features 内（core.py の外）。

### #3 F5 の `max()` で OP/EPS 両方が負の場合、「小さい減益」が選ばれるべきだが「大きい減益」が選ばれる

- 箇所: `earnings_model_core.py:103`
- 事象: `nyc = max(_candidates)` で OP成長率と EPS成長率の大きい方を取る設計だが、両方が負の場合は `max()` が「0に近い方」（= 減益幅が小さい方）を返す。設計意図（「保守的に取る」= F5 ボーナスを与える方向）と一致するが、F5 減点側 (`nyc < -0.10`) では逆に減点が緩和される。
- トリガー: FY で `next_year_op_change = -0.15` かつ `next_year_eps_change = -0.30` の場合、`max(-0.15, -0.30) = -0.15` が選ばれ -2 になるが、実態は EPS が -30% 減少しており市場反応はより厳しいはず。
- 影響: 下方リスクの過小評価。EPS が OP より悪化している銘柄で DOWN シグナルが弱まる。
- 根拠: core.py:103 `nyc = max(_candidates)` + core.py:108-109 `elif nyc < -0.10: score -= 2`。正の場合は max が「より良い方」を取るので加点方向が正しいが、負の場合は max が「より良い方」を取ることで減点が緩和される。
- 推奨対応: 設計意図を再確認。加点時は `max(op, eps)`、減点時は `min(op, eps)` とする（両方向で保守的）か、現行の `max` を維持して「EPS の方が悪い場合でも OP ベースで判断する」設計意図を知見 MD に明文化する。前者が安全だが、現行精度に影響するため EDA での検証を推奨。

### #4 core.py の `f4_reasons` 引き継ぎで list 型前提だが None が渡される

- 箇所: `earnings_model_core.py:48-50`
- 事象: `_f4r = row.get("f4_reasons")` → `if _f4r:` → `reasons.extend(_f4r)` の流れで、`f4_reasons` が `None` の場合（batch_rerun:633, notebook Cell 5 の `_f4_reasons if _f4_reasons else None`）は `if _f4r:` が偽で安全にスキップされる。しかし空リスト `[]` の場合（f4_reasons が初期値のまま何も append されなかったケース）も `if _f4r:` が偽になり安全。**現時点では問題ないが**、将来 `f4_reasons` に `"" `（空文字列）や `0`（数値）が渡された場合に `extend()` が TypeError を出す。
- トリガー: 現在の実装では発生しない。しかし型ヒントが `f4_reasons` に明示されておらず、dict 構築時の `None` / `list` 切替が呼び出し元依存。
- 影響: 現時点で実害なし。防御的プログラミングの観点からの指摘。
- 根拠: core.py:48-50。呼び出し元（batch_rerun:633, notebook Cell 5）は `f4_reasons if f4_reasons else None` で list or None を渡す。
- 推奨対応: `if _f4r and isinstance(_f4r, list):` に変更するか、現行のままドキュメント（docstring の Args に `f4_reasons: list[str] | None`）で contract を明示する。

## 【改善提案】（可読性・保守性）

### #1 `_date_key` ヘルパーの重複

- 箇所: `batch_rerun_predict.py:355-357`, `earnings_model_predict.ipynb` Cell 5（`_date_key` 定義）
- 現状: `_date_key` / `date_key` 関数が batch_rerun と notebook で別々に定義されている。core.py に一元化する候補だが、特徴量計算のヘルパーであり compute_score / score_to_prediction とは性質が異なる。
- 提案: 現時点では無理に core.py に入れず、将来 compute_features 自体を core.py に移設する際にまとめて一元化する。ただし関数名を `_date_key` に統一する（batch_rerun は `date_key` でアンダースコアなし）。

### #2 batch_rerun の `print()` 使用

- 箇所: `batch_rerun_predict.py:121,129,156` 等（全体にわたって `print()` を使用）
- 現状: CLAUDE.md コーディング規約で「print禁止。structlogを使用」と定められているが、batch_rerun は全面的に `print()` を使用。
- 提案: 既存コードの全面書き換えはスコープ外だが、core.py は新規作成のため print を含んでいないことは良い。batch_rerun の structlog 化は次回改修時の課題として知見 MD のTODOに追加する。

### #3 `compute_score` の行数と分岐の多さ

- 箇所: `earnings_model_core.py:33-215`（182行、16因子の条件分岐）
- 現状: 1関数で16因子の全ロジックを直列に記述。各因子間に依存がなく独立しているため、因子ごとのヘルパーに分割可能。
- 提案: 可読性・テスタビリティのため、各因子を `_f1_progress(row, exp)` ... `_f16_fy_miss(row)` のようなプライベートヘルパーに分割し、`compute_score` はそれらを呼び出すオーケストレーター関数にする。ただし現時点の16因子が安定するまで（ML移行前）はリファクタリングの ROI が低いため、低優先。

### #4 `PRED_COLUMNS` にない特徴量が compute_features の結果に含まれる

- 箇所: `earnings_model_core.py:18-30` (`PRED_COLUMNS`) vs `batch_rerun_predict.py:616-652` (results dict)
- 現状: compute_features が返す DataFrame には `op`, `fop`, `odp`, `beta_20d`, `theme_boost`, `f4_reasons` 等が含まれるが、PRED_COLUMNS にはこれらが含まれない。GCS保存時に `df_feat[PRED_COLUMNS]` で絞るため保存には問題ないが、compute_score に渡す際に必要なキー（`theme_boost`, `beta_20d`, `f4_reasons` 等）が PRED_COLUMNS に含まれていないことが直感に反する。
- 提案: PRED_COLUMNS を「GCS保存カラム」と明確に命名（`GCS_PRED_COLUMNS` 等）し、compute_score が必要とする全キーは別の定数 `SCORE_INPUT_KEYS` として定義すると、将来の因子追加時に「保存対象」と「スコアリング入力」の区別が明確になる。低優先。

### #5 Cell 9 初期化ガードの core import が部分的

- 箇所: `earnings_model_predict.ipynb` Cell 9（初期化ガードブロック内）
- 現状: `from earnings_model_core import classify_return` のみ import しており、`compute_score` や `score_to_prediction` は import していない。Cell 9 は答え合わせ（Step 3）であり compute_score は不要のため現時点では正しいが、ガードの「Cell[3] 再実行相当」というコメントと実態が一致しない。
- 提案: コメントを「Step 3 で必要な最小限の import」に修正するか、Cell 3 と同じフル import にする。前者が適切（不要な import を避ける）。

## 【6項目 TODO改善の反映確認】

| # | TODO項目 | core.py 反映 | batch_rerun 反映 | notebook 反映 | 判定 |
|---|---------|-------------|-----------------|--------------|------|
| J | F5 EPS成長率追加 | core.py:96-109 `max(op, eps)` | batch_rerun:576-579 `next_year_eps_change` 計算 | Cell 5 同等ロジック | OK（ただし#2, #3 の指摘あり） |
| I | F12 PEG全Q拡張 | core.py:159-185 FY: `next_year_op_change`, 非FY: `yoy_op` | batch_rerun:compute_features で `per` フィールド構築 | Cell 5 で `_per` 構築 | OK |
| 9 | F15 黒字転換 | core.py:199-205 `is_turnaround` + YoY 有無で +2/+1 | batch_rerun:425-429 `qoq_map` に `is_turnaround` | Cell 5 同等ロジック | OK |
| 2 | F16 FY未達 | core.py:208-213 `fy_achievement < 0.65` → -2, `< 0.80` → -1 | batch_rerun:589-591 `_fy_achievement` 計算 | Cell 5 同等ロジック | OK |
| 3 | IFRS OdP→OPフォールバック | core.py は F4 の特徴量計算に非関与（呼び出し元の責務） | batch_rerun:539-569 FY/Q 両方で OdP→OP フォールバック | Cell 5 同等ロジック | OK |
| 1 | 低ベースフィルタ | core.py は F5 入力を受け取るだけ（呼び出し元が差替え済み） | batch_rerun:581-587 `_is_low_base` → `next_year_op_change` 差替え | Cell 5 同等ロジック | OK（ただし#2 の EPS 未差替え指摘あり） |

## 【副作用・サイドエフェクトチェック】

### import パス

- [x] **batch_rerun**: `from earnings_model_core import (Q_MAP, CUM_PREV_Q, PRED_COLUMNS, PREV_Q_MAP, classify_return, compute_score, score_to_prediction)` — batch_rerun_predict.py:28-36。同ディレクトリの `earnings_model_core.py` を直接 import。`sys.path.insert(0, str(PROJECT_ROOT / "scripts"))` (line 66) により `scripts/` がパスに入るが、`earnings_model_core.py` は `scripts/earnings_model/` にあるため、`from earnings_model_core import` が解決されるには **`sys.path` に `scripts/earnings_model/` が必要**。しかし batch_rerun のパス設定は `scripts/` のみ。**→ 実際には batch_rerun が `scripts/earnings_model/` ディレクトリ内で実行されるため、Python がカレントディレクトリからの相対 import で解決する。ただし異なるディレクトリから `python scripts/earnings_model/batch_rerun_predict.py` と実行した場合は ImportError になり得る。** batch_rerun の docstring に記載の Usage は `scripts/earnings_model/batch_rerun_predict.py` をフルパスで指定しているため、Windows の python.exe がスクリプトのディレクトリを `sys.path[0]` に自動追加する仕様で動作する。問題なし。
- [x] **notebook Cell 3 (local)**: `sys.path.insert(0, str(PROJECT_ROOT / 'scripts' / 'earnings_model'))` — `earnings_model_core.py` のディレクトリを明示的に追加。問題なし。
- [x] **notebook Cell 3 (Colab)**: `sys.path.insert(0, '/content/drive/MyDrive/claude/investment-agent/scripts/earnings_model')` — Google Drive マウント後のパスを追加。問題なし。
- [x] **notebook Cell 9 (初期化ガード)**: local/Colab 判定後に `sys.path.insert(0, ...)` + `from earnings_model_core import classify_return`。Cell 3 と同じパス設定ロジックを含む。問題なし。

### 関数削除

- [x] batch_rerun から `compute_score`, `score_to_prediction`, `classify_return` のローカル定義は既に存在しなかった（以前は存在していたが、core.py 作成時に削除済みと推定）。`STEP C` コメント (line 657-659) にて core import 済みの旨を明記。問題なし。
- [x] notebook Cell 7 のスコアリングが `compute_score(row)` を直接呼び出し (line `s, r = compute_score(row)`)。Cell 7 にローカル定義は残っていない。問題なし。
- [x] notebook Cell 9 の `classify_return` は core から import。Cell 9 にローカル定義は残っていない。問題なし。

### 定数参照元変更

- [x] **PRED_COLUMNS**: batch_rerun は `from earnings_model_core import PRED_COLUMNS` (line 31)。notebook Cell 3 も同様。GCS保存時に `df_feat[PRED_COLUMNS]` で使用。core.py:18-30 に `is_low_base`, `median_5y_op`, `is_turnaround`, `fy_achievement` が追加済み。問題なし。
- [ ] **Q_MAP / CUM_PREV_Q / PREV_Q_MAP**: batch_rerun は core から import。notebook は Cell 3 で import するが Cell 5 で再定義（→ 重大指摘 #1）。

## 【修正例】（必要な箇所のみ）

#### #1 に対する修正案（Cell 5 のローカル再定義削除）

```python
# before: earnings_model_predict.ipynb Cell 5（`-- 1-7. BQ: Q-on-Q` セクション）
Q_MAP: dict[str, str] = {'1Q': '1Q', '2Q': '2Q', '3Q': '3Q', 'FY': '4Q'}
CUM_PREV_Q: dict[str, str] = {'2Q': '1Q', '3Q': '2Q', 'FY': '3Q'}
PREV_Q_MAP: dict[str, str] = {'2Q': '1Q', '3Q': '2Q', '4Q': '3Q'}

# after: 上記3行を削除（Cell 3 の from earnings_model_core import ... で取得済み）
```

#### #2 に対する修正案（低ベース時 EPS 無効化）

```python
# before: batch_rerun_predict.py:581-587 / notebook Cell 5 Low base detection
_is_low_base = False
_median_5y_op = median_5y_op_map.get(tk)
if cur_per == 'FY' and _median_5y_op is not None and _median_5y_op > 0:
    if pd.notna(op) and float(op) < _median_5y_op * 0.5:
        _is_low_base = True
        if pd.notna(nx_fop) and _median_5y_op != 0:
            next_year_op_change = float((nx_fop - _median_5y_op) / abs(_median_5y_op))

# after: 低ベース時はEPS成長率も無効化
_is_low_base = False
_median_5y_op = median_5y_op_map.get(tk)
if cur_per == 'FY' and _median_5y_op is not None and _median_5y_op > 0:
    if pd.notna(op) and float(op) < _median_5y_op * 0.5:
        _is_low_base = True
        if pd.notna(nx_fop) and _median_5y_op != 0:
            next_year_op_change = float((nx_fop - _median_5y_op) / abs(_median_5y_op))
        next_year_eps_change = None  # 低ベース時はOP成長率(中央値ベース)のみ使用
```

## 【確認できなかった事項】

- **Colab での実行テスト**: `sys.path` 設定 + `from earnings_model_core import ...` が Colab 環境（Google Drive マウント後）で実際に動作するかは、実行しないと確定できない。コードパスの論理分析上は問題ないが、Drive マウントのタイミングやパスエンコーディングが影響する可能性がある。
- **NxFEPS の J-Quants データ品質**: `NxFEPS`（翌期予想EPS）が J-Quants API から返される全銘柄で信頼できる値か（株式分割調整済みか）は、実データを確認しないと判断できない。F14（分割）発火時に EPS を無効化するガードは core.py:100-101 に存在するが、分割が過去に行われた後に NxFEPS が未調整で返されるケース（J-Quants 側のバグ）は検知できない。
- **`max()` の設計意図（#3）**: F5 の加点/減点で異なる集約関数（max/min）を使うべきかは、過去の反省会データで EDA 検証しないと最適解が判断できない。
- **batch_rerun の SQL f-string**: batch_rerun_predict.py の BQ クエリは f-string で日付パラメータを埋め込んでおり（coding conventions C-1 違反）、既存コードの問題であるが本リファクタリングのスコープ外。
