# コードレビュー: 決算反応モデル median_5y_op修正 / is_low_profit / F5a/F5b分離

- 日時: 2026-05-13 17:30 JST
- 対象: `docs/plans/tools-059_earnings_model_eda_20260513_165133.md` + `scripts/earnings_model/predict.py` / `scripts/earnings_model/earnings_model_core.py`
- パターン: 2 (改修)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: median_5y_op を4Q単独OPから通期OP集約に修正（P0-1）、低利益率企業ガードフラグ is_low_profit 追加（P1-2）、F5をF5a(OP)/F5b(EPS)に分離しEPS優先ロジック導入（P1-3）
- 品質評価: **A** — P0-1のバグ修正は根本原因に正しく対処。P1-2/P1-3の設計判断も合理的。重大な欠陥はないが、F5スコア上限拡大とshow_prediction.py未追従の2点に注意
- 主要リスク:
  1. F5a+F5b同時発火でスコア上限が旧max(+2,-2)から最大(+4,-4)に拡大する意図的変更か要確認
  2. show_prediction.py の FIELD_ORDER に `next_year_eps_change`/`is_low_profit`/`median_5y_op` が無く、因子分解表示が不完全
  3. 既存GCS JSON に `is_low_profit` カラムが無いため、旧データを compute_score に通すと `row.get("is_low_profit")` が None → `bool(None)` = False で安全だが、明示的なフォールバックではない

## 【パターン2: 改修プラン評価】

### フォーマット適合性

- [x] 冒頭に基準 commit hash (`f3a3414`) あり
- [x] 前提サマリで過去修正と残件数が明示されている
- [x] 優先度の定義あり
- [ ] **P1-2 が7フィールド未完**: P1-2 は「症状 / 設計方針 / 検証」の3フィールドのみ。「該当（行番号）」「根本原因」「呼び出し側波及」「ロールバック」が欠落。実装済みのためプランと実装の照合ができない
- [ ] **P1-3 に before/after コードなし**: 「修正方針」に設計意図は書かれているが before/after のコード対比がない
- [ ] **アンチパターン対応表なし**: テンプレート必須の「対応アンチパターン」セクションが欠落
- [x] 検証戦略は smoke / backfill / 本番適用判断基準 / 回収手順の4段を網羅
- [x] ロールバック手順あり（P0-1は明示、全体は git revert）
- [x] 関連 commit・知見MDへのリンクあり

### 妥当性

**P0-1 (median_5y_op)**: 独自推定と一致。根本原因は `dff = dfq_asof[dfq_asof["QUARTER"] == "4Q"]` でフィルタ後の `OPERATING_PROFIT`（四半期単独値）を5年中央値に使っていたこと。修正方針は `baseline_yoy_op` ループで構築済みの通期OP合計 `fy_ops` を再利用するもので、正しく根本原因に対処している。コードの変更も `sorted_fys` から直接リスト内包表記で `ops_5y` を構築しており、旧来の `dff` 依存を完全に排除。方向性に問題なし。

**P1-2 (is_low_profit)**: 低利益率企業でOP系%因子が暴れる問題を事後的閾値ではなく事前フラグで対処する設計は妥当。閾値 5億円（500_000_000）はEDAで決定予定とプランに記載されているが、コード上は既にハードコードされている。

**P1-3 (F5分離)**: 旧F5は `max(OP成長率, EPS成長率)` で正の値が常に勝つため、EPS減益がマスクされる構造的欠陥があった。F5b(EPS)を主因子、F5a(OP)を従因子として分離し、矛盾時にF5aを無効化するロジックは、6644の事例で示された問題に正しく対処している。

### 副作用・デグレードチェック

- [x] **P0-1 downstream影響**: `is_low_base` 判定と `next_year_op_change` の差替えロジック（L767-772）は `_median_5y_op` の値に依存するが、ロジック自体は変更なし。通期OP中央値が4Q単独OPより大きくなるため、`is_low_base` の発火閾値が上がり（`float(op) < _median_5y_op * 0.5` が厳しくなる）、結果として `is_low_base=True` になるケースは減る方向。既存の is_low_base=True で正しく機能していたケースが is_low_base=False に変わる可能性はあるが、これは正しい修正の帰結。
- [x] **F5スコア上限拡大**: 旧F5は `max()` で1つの値を選択し最大+2/-2。新F5はF5a+F5b同時発火で最大+4/-4。これは全体スコアのレンジを拡大させる。意図的であればよいが、プランには「F5b(EPS)が主因子で単独スコア付与、F5a(OP)は従因子で F5bと同符号の場合のみ有効」としか書かれておらず、**合計スコアが最大倍増する影響の検討がない**。
- [x] **is_low_profit の全Q適用**: `predict.py:L766` で `is_low_profit` は `cur_per` に関係なく全四半期で計算される。`earnings_model_core.py` の F3/F13 も全Qで `_is_low_profit` をチェック。設計意図通り。
- [x] **GCS JSON互換性**: `PRED_COLUMNS` に `is_low_profit` が追加されたため、新しい prediction JSON には含まれる。旧JSONを `show_prediction.py` で表示する際は `is_low_profit` が無いだけで表示には影響なし（FIELD_ORDER に含まれていないため）。`compute_score` で旧データを再スコアリングする場合は `row.get("is_low_profit")` が `None` → `bool(None)` = `False` で旧動作と同等。安全だが暗黙的。

### 抜け漏れ（類似観点での横展開含む）

- [ ] **show_prediction.py の FIELD_ORDER 未更新**: `next_year_eps_change`、`is_low_profit`、`median_5y_op` が FIELD_ORDER に含まれていない。特に `next_year_eps_change` はF5b導入で重要な因子分解情報となるが、反省会時の因子表示で見えない。プランの「検証: show_prediction.py 20260512 5449 で因子分解を確認」で気づくはずだが、プラン上の変更対象ファイルに show_prediction.py が含まれていない
- [ ] **F7 が is_low_profit ガード済みだが F12(PER/PEG) は未ガード**: F12 は `_yoy12 = row.get("yoy_op")` を使っており、低利益率企業の暴れたYoYが PEG 計算に影響する。OP系%因子ガードの横展開漏れの可能性（ただしF12のYoYは成長率として使用しており、PEG>10で罰点がつくのは問題ないため、意図的に除外している可能性もある）
- [ ] **rerun_predict.py**: `earnings_model_core` を import していないが、predict.py の `compute_features` と `compute_score` を何らかの形で使用しているか確認が必要。Grep結果では直接importが見つからなかったため、おそらく影響なし

### 新規リスク

- **F5スコアレンジ拡大によるbackfill精度への影響**: F5a+F5b同時発火で従来の最大+2が+4になり得る。これはbackfill再構築時に全体のスコア分布を変え、UP/DOWN判定の比率に影響する可能性がある。backfillで確認するとプランに記載されているが、この影響の大きさを事前に認識しておくべき
- **is_low_profit 閾値のハードコード**: 5億円の閾値がコード内に直接埋め込まれている。プランでは「EDAで分布とフラグ対象銘柄の方向一致率を確認して決定」とあるが、コードには既にハードコードされている。EDAの結果で閾値を変更する場合、コード修正が必要になる。定数化が望ましい

## 【重大な指摘】（即修正）

### #1 F5スコア上限が旧+2/-2から最大+4/-4に拡大 — 意図確認が必要

- 箇所: `scripts/earnings_model/earnings_model_core.py:113-128`
- 事象: F5b(EPS) +2 と F5a(OP) +2 が同時発火すると合計 +4。旧実装は `max()` で1つ選択し最大+2だった
- トリガー: FY決算で来期OP+10%超 かつ 来期EPS+10%超 の企業（同符号なのでF5aも発火）
- 影響: スコアレンジの実質拡大により、UP/DOWN判定の閾値（+2/-2）に対する影響力が増大。特にF4(コンセ乖離)の最大+3/-5と比較してF5の影響力が逆転する可能性
- 根拠: 旧コード `nyc = max(_candidates)` は1値で +2/-2。新コードは F5a/F5b 独立にそれぞれ +2/-2
- 推奨対応: **[方向性]** 意図的であれば問題なし。意図的でなければ、F5a の寄与を +1/-1 に縮小するか、F5 全体で cap(+2,-2) を設けることを検討。backfill 再構築前に判断すべき

### #2 show_prediction.py に next_year_eps_change が表示されない

- 箇所: `scripts/earnings_model/show_prediction.py:112-137`
- 事象: FIELD_ORDER に `next_year_eps_change` エントリがない。F5b(EPS)導入で主因子になったにもかかわらず、因子分解表示で来期EPS変化率が見えない
- トリガー: `show_prediction.py` で因子分解を表示する全ケース
- 影響: 反省会時にF5b(EPS)の判定根拠が確認できず、デバッグ・検証に支障
- 根拠: FIELD_ORDER の `next_year_op_change` の次に `next_year_eps_change` が必要だが存在しない
- 推奨対応: **[検証済み]** FIELD_ORDER に `("next_year_eps_change", "来期EPS変化", _fmt_pct)` を追加。同時に `("is_low_profit", "低利益率", _fmt)` と `("median_5y_op", "5年中央値OP", _fmt)` も追加が望ましい

## 【改善提案】（可読性・保守性）

### #1 is_low_profit 閾値の定数化

- 箇所: `scripts/earnings_model/predict.py:766`
- 現状: `500_000_000` がインラインでハードコード。プランでは「EDAで決定」としつつコードには既に埋め込み
- 提案: `earnings_model_core.py` に `LOW_PROFIT_THRESHOLD = 500_000_000` を定数定義し、predict.py から参照。EDA後の閾値変更を1箇所で完結させる

### #2 F5a/F5b の reason 文言で主従関係を明示

- 箇所: `scripts/earnings_model/earnings_model_core.py:116-128`
- 現状: F5b は「来期EPS増益」、F5a は「来期OP増益」だが、どちらが主でどちらが従か reason 文字列からは判別不能
- 提案: F5a の reason に「(従)」等を付与して主従を明示。例: `"来期OP増益(従) {_nyc_op:+.1%}"`。反省会時の因子分解で判別しやすくなる

### #3 F7 の nyc7 変数が F5 の _nyc_op と重複取得

- 箇所: `scripts/earnings_model/earnings_model_core.py:141`
- 現状: F7 で `nyc7 = row.get("next_year_op_change")` を取得しているが、F5 で既に `_nyc_op = row.get("next_year_op_change")` を取得済み。同じ値を2回取得
- 提案: F7 で `_nyc_op` を再利用（`nyc7 = _nyc_op`）。可読性と一貫性の改善

## 【修正例】（必要な箇所のみ）

#### #2 (show_prediction.py) に対する修正案
```python
# before: scripts/earnings_model/show_prediction.py:129
    ("next_year_op_change", "来期OP変化", _fmt_pct),
    ("has_special_dividend", "特別配当", _fmt),

# after
    ("next_year_op_change", "来期OP変化", _fmt_pct),
    ("next_year_eps_change", "来期EPS変化", _fmt_pct),
    ("is_low_profit", "低利益率", _fmt),
    ("has_special_dividend", "特別配当", _fmt),
```

## 【確認できなかった事項】

- is_low_profit 閾値 5億円の妥当性（EDA未実施のため判断不能。backfill後の分布確認で検証予定）
- F5スコア拡大が backfill accuracy に与える実際の影響（実行して確かめないと判定不能）
- F12(PER/PEG) で `yoy_op` を使う箇所が is_low_profit ガードの対象外であることの意図（設計判断として妥当かどうかはドメイン知識依存）
- 旧 prediction JSON に対する compute_score 再実行時の `is_low_profit` 欠落の影響範囲（`bool(None)` = False で安全だが、明示的デフォルトの方が堅牢）
