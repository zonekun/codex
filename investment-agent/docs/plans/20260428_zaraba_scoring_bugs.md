# ザラ場スコアリング 4/28 反省会バグ修正

**作成日時**: 2026-04-28 JST
**対象ファイル**: `scripts/zaraba_earnings.py`（1775行）, `scripts/zaraba_tdnet_poller.py`（649行）, commit `33ebc88` 時点
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: 4/28 ザラ場反省会で発見されたスコアリングバグ6件を修正する。新因子追加（株式分割検知等）は別プランとし本プランのスコープ外。
**ステータス**: ✅ P0-1/P0-2/P1-1/P1-3/P2-1 実装完了・検証済み。P1-2（コンセンサス全不発）は構造的課題として別途対応。

---

## 前提サマリ

- 過去修正: `33ebc88` で catchup の TDnet 移行完了。L479-484 に Q4 単独 OP 除外 fix あり
- 残存: 本プランで扱う 6 件（P0×2、P1×3、P2×1）
- 実機検証の有無: 4/28 VM 実行結果（`results.csv` 87 銘柄）で症状確認済み
- 関連: `docs/knowledges/tools/066_zaraba_tool.md` §ザラ場反省会ログ 2026-04-28

---

## 優先度の定義

- **P0**: スコア計算が数値的に誤り。誤ったスコアで売買判断につながるリスク
- **P1**: 因子が発火すべき場面で不発。スコアが過小評価される
- **P2**: 因子の適用範囲が過剰。精度改善だが urgent ではない

---

## 指摘項目

### P0-1. F4 翌期変化率の異常値（6902 +792%, 7259 +691%, 6762 +1111%, 6473 +262%, 5482 +53%） 🚨

**症状**: FY 発表銘柄の翌期変化率が実態と大幅に乖離。5482 愛知製鋼は OP ほぼフラットなのに +53%、6902 デンソーは +792% と表示。

**該当**: `scripts/zaraba_tdnet_poller.py:L478-L484` / `scripts/zaraba_earnings.py:L1512-L1515`

```python:zaraba_tdnet_poller.py L478-484
# 実績値: YearDuration/AccumulatedQ（累計）を QuarterDuration（四半期単独）より優先
# FY時にQ4単独OPを拾うと翌期比較 F4 等が異常値になる（6902 +792% 事故）
if not is_forecast and len(valid_entries) > 1:
    cumul = [e for e in valid_entries
             if "YearDuration" in e["context"] or "AccumulatedQ" in e["context"]]
    if cumul:
        valid_entries = cumul
```

```python:zaraba_earnings.py L1512-1515
if cur_per == "FY":
    nx_op = _to_num(rec.get("NxFOP"))
    if nx_op is not None and cumulative_op and cumulative_op != 0:
        nx_chg = (nx_op - cumulative_op) / abs(cumulative_op)
```

**根本原因（仮説 3 つ、要切り分け）**:

1. **NxFOP（分子）の抽出値が異常**（最有力）: L478-484 の cumulative フィルタは substring match（`"YearDuration" in ctx`）で IFRS context にもマッチするため、分母（cumulative_op）は正しく取れている可能性が高い。むしろ **NxFOP の抽出パス**で誤った context/値を拾っている疑い。5482 は OP フラットなのに +53% → NxFOP が実際の翌期予想 OP と乖離
2. **VM デプロイ漏れ**: L479-484 の Q4 単独除外 fix がローカルにはあるが VM にデプロイされていない可能性。ただし仮説 1 と排他ではない
3. **XBRL context マッチ漏れ**: IFRS 企業の context 名が既存パターンにマッチしない可能性（仮説 1 より低い確率）

**修正方針**:

1. **NxFOP の検証を最優先**: 6902/5482 等の XBRL ZIP を VM から取得し、`NEXT_YEAR_FORECAST_OP` の抽出値・context・タグ名を dump。実際の翌期予想 OP と突合
2. VM のコードバージョンを確認（`git log -1` on VM）。デプロイ漏れなら再デプロイ
3. cumulative_op 側も念のため検証: `_parse_ixbrl()` で抽出された OP の context が `YearDuration` / `AccumulatedQ` にマッチするか確認

**検証**: 6902/5482/6301 の XBRL ZIP を手動パースし、抽出された OP / NxFOP / context を目視確認

**ロールバック**: コミット revert で済む（データ破壊なし）

---

### P0-2. 6301 コマツ XBRL 抽出完全失敗（翌期予想ありなのに非開示判定） 🚨

**症状**: 6301 コマツの results.csv で `cumulative_op` も `forecast_op` も空。「翌期予想非開示」と NEG 判定されるが、実際には翌期予想を開示している。

**該当**: `scripts/zaraba_tdnet_poller.py:L300-330` (TDNET_TAG_MAP) / `L336` (TDNET_CURRENT_PATTERNS)

```python:zaraba_tdnet_poller.py L336
TDNET_CURRENT_PATTERNS = ["Current", "ThisQuarter"]
```

**根本原因（仮説）**: コマツは **IFRS 採用企業**。IFRS 決算短信の iXBRL は context 名・タグ名が J-GAAP と異なる可能性がある。`TDNET_CURRENT_PATTERNS` の `"Current"` にマッチしない context を使っている、または OP タグ候補に IFRS 固有のタグが不足。

**修正方針**:

1. コマツの XBRL ZIP を取得し `_parse_ixbrl()` の出力を dump
2. 全 context 名・全タグ名を列挙し、既存の `TDNET_TAG_MAP` / `TDNET_CURRENT_PATTERNS` でマッチするか確認
3. マッチしないタグ/context があれば追加。IFRS 企業に共通するパターンなら汎用的に対応

**検証**: コマツ + 他 IFRS 企業（デンソー 6902、アイシン 7259 等）の XBRL で一括検証

**ロールバック**: コミット revert で済む

---

### P1-1. F6 配当増 +10% が不発（5482 愛知製鋼） ⚠️

**症状**: 5482 は配当約 10% 増だが、F6（増配）が発火していない。`FDivAnn` が None の可能性。

**該当**: `scripts/zaraba_earnings.py:L1539-L1555` / `scripts/zaraba_tdnet_poller.py:L329,L348,L450-453`

```python:zaraba_earnings.py L1539-1542
actual_div = _to_num(rec.get("FDivAnn"))
prev_div = p.get("forecast_div_ann")
div_detected = False
if actual_div is not None and prev_div and prev_div > 0:
```

**根本原因（仮説 2 つ）**:

1. **XBRL 配当タグ抽出失敗**: `FORECAST_DIV_ANN` の候補が `["DividendPerShare"]` のみ。一部企業は異なるタグ名を使用
2. **prior 側の配当データ欠落**: `prev_div = p.get("forecast_div_ann")` が None。BQ `fin_summary` の `FORECAST_DIVIDEND_PER_SHARE_ANNUAL` が NULL の銘柄

**修正方針**:

1. 5482 の XBRL ZIP から配当関連タグを全列挙
2. `FDivAnn` 抽出値と `prev_div`（prior_data.json）の両方を確認し、どちらが None かを特定
3. タグ不足なら `TDNET_TAG_MAP["FORECAST_DIV_ANN"]` に追加。prior 不足なら prepare のクエリを確認

**検証**: 5482 + 他の増配銘柄で F6 発火を確認

**ロールバック**: コミット revert で済む

---

### P1-2. コンセンサス比較が全銘柄で不発（4578, 9412 等） ⚠️

**症状**: 4578 大塚HD はコンセンサス達成（128）を市場が評価しているが、`obs_guidance_vs_consensus` が空。9412 スカパーJSAT も同様。results.csv 全 87 行で `obs_guidance_vs_consensus` が空。

**該当**: `scripts/zaraba_earnings.py:L1385-L1391`

```python:zaraba_earnings.py L1385-1391
def _guidance_vs_consensus(rec: dict, prior: dict) -> float | None:
    """翌期会社予想 vs 来期コンセンサス の乖離率。観察用。"""
    nx_op = _to_num(rec.get("NxFOP"))
    cons_next = prior.get("consensus_profit_next")
    if nx_op is None or not cons_next or cons_next == 0:
        return None
```

**根本原因**: `_guidance_vs_consensus` は**翌期**予想 vs **来期**コンセンサスの比較。4578 は 1Q 発表で NxFOP=None（翌期予想なし）→ 常に None。しかしユーザーが求めるのは「**今期実績 vs 今期コンセンサス**」の達成度。現行ロジックにはその因子（F4c）が経常利益ベースで存在するが（L1602-1613）、OP ベースのコンセ比較は無い。

さらに F4c 自体も `actual_odp`（経常利益）依存で、XBRL から経常利益タグが取れない企業では不発。

**修正方針**:

1. `obs_guidance_vs_consensus` の定義を再検討。**今期 OP 実績 vs 今期 OP コンセ**の比較も追加
2. F4c（L1602-1613）の `actual_odp` が None のケースで `cumulative_op` にフォールバック
3. prepare 時のコンセンサスキャッシュに**今期 OP コンセンサス**が含まれているか確認（`consensus_profit` は経常利益の可能性）

**検証**: 4578 の prior_data.json でコンセンサスデータの中身を確認 → F4c の発火を検証

**ロールバック**: コミット revert で済む

---

### P1-3. F12（PEG割安度）が TDnet 移行後に恒久不発 ⚠️

**症状**: F12（PEG 割安度）が全 FY 銘柄で発火しない。TDnet + XBRL パスに移行後、`ForEPS`（予想EPS）が常に None。

**該当**: `scripts/zaraba_earnings.py:L1652-L1668` / `scripts/zaraba_earnings.py:L1337-L1376` (`_xbrl_to_jquants_rec`)

```python:zaraba_earnings.py L1655-1658
    for_eps = _to_num(rec.get("ForEPS"))
    nx_op_12 = _to_num(rec.get("NxFOP"))
    if (latest_close and for_eps and for_eps > 0
            and nx_op_12 is not None and cumulative_op and cumulative_op != 0):
```

**根本原因**: `_xbrl_to_jquants_rec`（L1337-1376）は `rec["ForEPS"]` をセットしていない。`TDNET_TAG_MAP` にも `FORECAST_EPS` エントリが無い。J-Quants パス時代は `fin_summary` から取得していたが、TDnet XBRL 移行時にマッピングが抜け落ちた。

**修正方針**:

1. `TDNET_TAG_MAP` に `"FORECAST_EPS"` を追加。タグ候補: `["BasicEarningsPerShare", "NetIncomePerShare"]` + IFRS 系。context は `TDNET_FORECAST_CURRENT_PATTERNS`（当期予想）
2. `_xbrl_to_jquants_rec` に `rec["ForEPS"] = extracted.raw_extract.get("FORECAST_EPS", {}).get("value")` を追加
3. `extract_from_xbrl` の forecast ルーティングに `FORECAST_EPS` を追加

**検証**: FY 発表銘柄で `ForEPS` が正しく抽出され、F12 が発火することを確認

**ロールバック**: コミット revert で済む

---

### P2-1. 翌期予想非開示ペナルティが中小型に過剰適用（8622 水戸証券） 💡

**症状**: 8622 水戸証券（中小型証券）が「翌期予想非開示」で W-Sell だが、株価は無反応。中小型の翌期非開示は市場の常態であり嫌気されない。

**該当**: `scripts/zaraba_earnings.py:L1522-L1524`

```python:zaraba_earnings.py L1522-1524
        else:
            score -= 1
            factors.append("翌期予想非開示")
```

**根本原因**: 時価総額フィルタなしで全銘柄に一律 -1 ペナルティを付与。

**修正方針**:

```python
# before
else:
    score -= 1
    factors.append("翌期予想非開示")

# after（時価総額フィルタ追加）
else:
    cap = p.get("market_cap_oku")  # 億円（※キー名注意: "market_cap" ではない）
    if cap is not None and cap >= 5000:  # 閾値は要EDA
        score -= 1
        factors.append("翌期予想非開示")
```

閾値（5000 億円等）は EDA で検証して決定。results.csv に `market_cap_oku` 列が既にあるが全行空 → prepare の market_cap 取得にもバグあり（付随修正）。

**検証**: 8622（中小型）で不発、6301/4063 信越化（超大型）で発火を確認

**ロールバック**: コミット revert で済む

---

## 検証戦略

1. **smoke test**: 6902/6301/5482 の XBRL ZIP を VM から取得し、`_parse_ixbrl()` + `extract_from_xbrl()` を単独実行。抽出値と context を dump して原因特定
2. **dev 実機**: 修正後に 4/28 の全 XBRL を再処理し、修正前 results.csv と diff。改善銘柄・劣化銘柄を確認
3. **本番適用判断基準**: 異常値（+100% 超の翌期変化率）がゼロになること。正常値（±50% 以内）の変動が 5% 以内
4. **回収手順**: results.csv は日次上書きのため、翌日の実行で自動回収。コード revert のみ

---

## 付随発見

### market_cap_oku が全行空

results.csv の `market_cap_oku` 列が 87 行すべて空。prepare → watch/catchup のデータ受け渡しで時価総額が欠落している。P2-1 の時価総額フィルタの前提として修正が必要。原因候補: prepare 時の BQ `YF_STOCK_INFO` クエリが空 DataFrame を返した（週次ロードジョブ遅延の可能性）。

### consensus_profit の意味が曖昧

`V_CONSENSUS_MERGED.PROFIT` を `consensus_profit`（F4c で経常利益と比較）と `consensus_profit_next`（`_guidance_vs_consensus` で NxFOP と比較）の両方に使用。PROFIT が経常利益・営業利益・純利益のどれを指すか未検証。系統的な誤比較の温床。実装前に `data_catalog.md` / BQ スキーマで確認必須。

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/066_zaraba_tool.md`（§ザラ場反省会ログ 2026-04-28）
- 知見 MD: `docs/knowledges/tools/059_earnings_model_eda.md`（因子定義の本体）
- 関連 commit: `33ebc88` — catchup TDnet 移行
- XBRL 抽出仕様: `docs/knowledges/tools/071_xbrl_to_jquants.md`
