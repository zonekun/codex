# 決算予測モデル ザラ場ツール修正取り込み + 既知バグ修正

**作成日時**: 2026-04-28 22:00 JST
**対象ファイル**:
- `scripts/earnings_model/earnings_model_predict.ipynb`（Cell 5, Cell 7, commit 3ae2a44 時点）
- `scripts/earnings_model/batch_rerun_predict.py`（commit 3ae2a44 時点）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: 2026-04-27〜28 のザラ場ツール（`zaraba_earnings.py`）バグ修正のうち predict ノートブック / batch_rerun に波及すべき修正を取り込む。加えて、059_earnings_model_eda.md に記載済みの高優先 TODO も同時に消化する。

**スコープ**: predict ノートブック・batch_rerun のスコアリングロジック改善。データソース（J-Quants vs XBRL）固有のバグ（TDNET_TAG_MAP 順序、ResultMember、累計/四半期コンテキスト）は J-Quants が正規化済みのため**対象外**。

---

## 前提サマリ

- 過去修正: 7d60586 で YoY/QoQ バグ修正済み（P0-1: YoY 1年ズレ、P1-1: QoQ 死亡、Cell 5 §1-7 書き換え完了）
- 残存: P1-2（batch_rerun look-ahead bias）+ P1-3（baseline_yoy as-of）は未修正（既存計画 `20260427_160000_earnings_model_yoy_qoq_bug.md` 参照）
- 新規: ザラ場ツール修正波及 4件（本プラン P0-1〜P1-2）+ 059 記載 TODO 2件（P1-3〜P1-4）
- 実機検証: 未検証

---

## 優先度の定義

- **P0**: 誤スコアリングが発生しており全ライブ予測に影響。次回 PREDICT 実行前に修正必須
- **P1**: 精度向上・因子拡充。次回 batch_rerun 前に消化
- **P2**: あれば嬉しいが、ブロッカーではない

---

## ザラ場→predict 波及判定サマリ

| # | ザラ場修正 | コミット | predict に同じバグ? | 波及要否 | 理由 |
|---|-----------|---------|-------------------|---------|------|
| 1 | IFRS/US-GAAP タグ優先順序 | 014a327 | **No** | 不要 | J-Quants が正規化済み |
| 2 | FY配当 ResultMember | 014a327 | **No** | 不要 | J-Quants が FDivAnn を正しく返す |
| 3 | ForEPS XBRL 抽出 | 014a327 | **No** | 不要 | J-Quants が ForEPS を提供 |
| 4 | 翌期非開示 cap>=3000億 | 014a327 | **差異** | P1 | predict は一切ペナルティなし、zaraba は大型のみ-1 |
| 5 | 株式分割→F6/F12 無効化 | 3ae2a44 | **Yes** | **P0** | 分割銘柄で偽の「大幅減配」が発火する |
| 6 | 株式分割 F14 +1 | 3ae2a44 | 欠如 | **P0** | 5 と同時に追加 |
| 7 | XBRL 累計/四半期コンテキスト | 014a327 | **No** | 不要 | J-Quants が正規化済み |

---

## 指摘項目

### P0-1. 株式分割銘柄で F11（配当）/ F12（PEG）が誤発火する 🚨

**症状**: 株式分割を発表した銘柄で、FDivAnn（分割後ベース）と prev_div_map（分割前ベース）が比較され、大幅な見かけ上の「減配」（例: 2分割なら -50%）としてスコアリングされる。F12 PEG も ForEPS が分割調整済みの場合に歪む可能性がある。

**該当**:
- `earnings_model_predict.ipynb` Cell 5: TDnet 開示タイトル検索セクション（§1-7c）に株式分割検知が**欠落**
- `earnings_model_predict.ipynb` Cell 7: `compute_score()` Factor 11（配当）/ Factor 12（PEG）にガードなし

**根本原因**: ザラ場ツールは `_related_titles` から株式分割を検知し F6/F12 を無効化するロジックを 3ae2a44 で追加したが、predict ノートブックの TDnet 開示検索（§1-7c）には `_STOCK_SPLIT_KW` が存在せず、`compute_score()` にも `has_stock_split` ガードがない。

**修正方針**:

**(A) Cell 5 §1-7c: 株式分割検知を追加**

```python
# before: _BUYBACK_KW, _SPECIAL_DIV_KW のみ
_BUYBACK_KW = ['自己株式の取得', '自社株買い']
_SPECIAL_DIV_KW = ['記念配当', '特別配当']

# after: _STOCK_SPLIT_KW を追加
_BUYBACK_KW = ['自己株式の取得', '自社株買い']
_SPECIAL_DIV_KW = ['記念配当', '特別配当']
_STOCK_SPLIT_KW = ['株式分割']
stock_split_tickers: set[str] = set()
```

yanoshin/TDnet HTML パス:
```python
# 既存の for d in _disclosures: ループ内に追加
if any(kw in _title for kw in _STOCK_SPLIT_KW):
    stock_split_tickers.add(_code)
    print(f'  株式分割: {_code} | {_title}')
```

BQ TDNET_DOCUMENTS_ENHANCED パス:
```python
# WHERE句に追加
OR DOC_TITLE LIKE '%株式分割%'
# 結果ループに追加
if '株式分割' in _ttl:
    stock_split_tickers.add(_tk)
```

特徴量 dict に追加:
```python
results.append({
    ...
    'has_stock_split': tk in stock_split_tickers,
    ...
})
```

**(B) Cell 7 compute_score(): F11/F12 ガード追加 + F14 新設**

```python
# Factor 11: Dividend change — 株式分割ガード追加
div_chg = row.get('div_change')
if not row.get('has_stock_split') and div_chg is not None and pd.notna(div_chg):
    # (既存ロジックそのまま)

# Factor 12: PER valuation (PEG-based, FY only) — 株式分割ガード追加
_per = row.get('per')
_nyc12 = row.get('next_year_op_change')
if not row.get('has_stock_split') and _per is not None and ...:
    # (既存ロジックそのまま)

# Factor 14: 株式分割（新設）
if row.get('has_stock_split'):
    score += 1
    reasons.append('株式分割')
```

**(C) GCS prediction JSON に `has_stock_split` を追加**

Cell 7 §2-3 の `prediction_records` のカラムリストに `'has_stock_split'` を追加。

**呼び出し側への波及**:
- `batch_rerun_predict.py`: 同一修正が必要（後述 P0-2）
- `show_prediction.py`: `has_stock_split` を表示に追加（任意）
- `review_report.py`: 影響なし（score/prediction のみ参照）

**検証**:
1. 直近で株式分割を発表した銘柄を特定（BQ: `SELECT TICKER, DOC_TITLE FROM TDNET_DOCUMENTS_ENHANCED WHERE DOC_TITLE LIKE '%株式分割%' AND SUBMISSION_DATE >= '2026-04-01'`）
2. その銘柄の PREDICT_DATE で実行し、`has_stock_split=True` が検知されること、F11/F12 がスキップされること、F14 が発火することを確認
3. 分割なし銘柄で F11/F12 が従来通り動作することを確認

**ロールバック**: コミット revert。GCS predictions は batch_rerun で再生成可能

---

### P0-2. batch_rerun_predict.py への P0-1 同一修正 🚨

**症状**: P0-1 と同一。batch_rerun にも株式分割ガードが不在。

**該当**: `batch_rerun_predict.py` — TDnet イベント取得セクション（BQ クエリ #6）+ `compute_score()` 関数

**修正方針**:

**(A) BQ クエリ #6 に株式分割を追加**

```sql
-- before
AND (MAIN_CATEGORY IN ('自己株式取得') OR DOC_TITLE LIKE '%記念配当%' OR DOC_TITLE LIKE '%特別配当%')

-- after
AND (MAIN_CATEGORY IN ('自己株式取得') OR DOC_TITLE LIKE '%記念配当%' OR DOC_TITLE LIKE '%特別配当%' OR DOC_TITLE LIKE '%株式分割%')
```

**(B) 特徴量計算に `has_stock_split` を追加**

TDnet イベント集計ループ内:
```python
stock_split_tickers: set[str] = set()
for _, r in df_tdnet_events.iterrows():
    if '株式分割' in r.get('DOC_TITLE', ''):
        stock_split_tickers.add(r['TICKER'])
```

特徴量 dict:
```python
'has_stock_split': tk in stock_split_tickers,
```

**(C) compute_score() の修正**: P0-1(B) と同一

**検証**: P0-1 と同一銘柄で batch_rerun を実行し、結果が一致することを確認

---

### P1-1. 翌期予想非開示ペナルティの時価総額フィルタ（ザラ場パリティ）⚠️

**症状**: ザラ場ツールでは FY 翌期予想非開示の -1 ペナルティを `market_cap_oku >= 3000`（時価総額3,000億円以上）に限定したが、predict ノートブックは**一切ペナルティを課していない**。中小型は不問、大型は市場が嫌気する傾向があるためザラ場側が正しい。

**該当**: `earnings_model_predict.ipynb` Cell 7, Factor 5 の `elif` 分岐

```python
# 現行（predict）
elif cur_per == 'FY' and row.get('next_year_disclosed') is False:
    reasons.append('来期予想未開示 (F5/F7/F12無効)')
    # ← score 変更なし
```

**根本原因**: ザラ場ツール zaraba_earnings.py:1526-1529 で大型限定ペナルティが実装済みだが、predict ノートブックには波及していない。

**修正方針**:

**(A) Cell 5: BQ `YF_STOCK_INFO.MARKET_CAP` から時価総額を取得**

> **データソース決定根拠**: J-Quants `get_fin_summary` API レスポンスには `ShOutFY` が含まれない（BQ `fin_summary` テーブル固有カラム）。`YF_STOCK_INFO.MARKET_CAP`（yfinance 由来、**JPY建て（円）**、週次 WRITE_APPEND）を使用する。ザラ場ツールも `YF_STOCK_INFO.MARKET_CAP / 1e8` で億円近似を行っている。

```python
# Cell 5: 新規 BQ クエリ（§1-4 付近、株価取得の後に追加）
q_market_cap = f"""
SELECT TICKER, MARKET_CAP, LOADED_AT
FROM `gmailpj-357912.STOCK.YF_STOCK_INFO`
WHERE LOADED_DATE <= '{PREDICT_DATE_HYPHEN}'
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY TICKER ORDER BY LOADED_AT DESC
) = 1
"""
df_market_cap = bq_client.query(q_market_cap).to_dataframe()
market_cap_map: dict[str, float] = {}
for _, r in df_market_cap.iterrows():
    if pd.notna(r['MARKET_CAP']) and r['MARKET_CAP'] > 0:
        # 円単位 → 億円（ザラ場ツールと同一ロジック）
        market_cap_map[r['TICKER']] = float(r['MARKET_CAP']) / 1e8
```

> **⚠️ 週次スナップショット注意**: `YF_STOCK_INFO` は週次ロードのため、最大7日古いデータを使う。`LOADED_DATE <= PREDICT_DATE` + `ORDER BY LOADED_AT DESC` で「予測日時点で最新のスナップショット」を取得。決算発表直後の株価変動は反映されないが、時価総額3,000億の閾値判定には十分な精度。

特徴量 dict に追加:
```python
'market_cap_oku': market_cap_map.get(tk),
```

**(B) Cell 7 compute_score(): 大型のみペナルティ**

```python
# before
elif cur_per == 'FY' and row.get('next_year_disclosed') is False:
    reasons.append('来期予想未開示 (F5/F7/F12無効)')

# after
elif cur_per == 'FY' and row.get('next_year_disclosed') is False:
    _cap = row.get('market_cap_oku')
    if _cap is not None and _cap >= 3000:
        score -= 1
        reasons.append(f'翌期予想非開示 cap={_cap:.0f}億')
    else:
        reasons.append('来期予想未開示 (F5/F7/F12無効)')
```

**呼び出し側への波及**: batch_rerun にも同一修正。`fetch_shared_data()` に `YF_STOCK_INFO` クエリを追加する必要あり（現状は STOCK_CODE_LIST のみ取得）。

**検証**: キーエンス（6861, FY 来期未開示の常連、大型）で -1 が発火し、小型の来期未開示銘柄ではスキップされることを確認

**ロールバック**: P0-1 と同一（コミット revert）

---

### P1-2. F14 注記追加: 株式分割発火時の F11/F12 無効化ルール ⚠️

**症状**: 059_earnings_model_eda.md の因子クイックリファレンス（line 65）に `F14注記` として「F14発火時はF6(増配/減配)・F12(PEG)を無効化」と記載済み（zaraba 側の番号 F6 = predict 側の F11）だが、predict ノートブックのコード・059 のテーブル本体にはこの注記に対応する実装がなかった。P0-1 で実装する。

**本項目は P0-1 の一部**であり、追加コード修正は不要。059 の因子テーブルの F14 行を更新する。

**059 因子テーブル更新内容**:

```markdown
# 現行 line 65（「14 | 株式分割」は TDnet related_titles のみ記載）
| 14 | 株式分割 | TDnet TITLE "株式分割" | +1 | — | — | 全Q | TDnet related_titles |

# 更新
| 14 | 株式分割 | TDnet TITLE "株式分割" | +1 | — | — | 全Q | yanoshin / TDnet HTML / BQ |

# F14注記は既存のまま維持（line 67）
> **F14注記**: F14発火時はF6(増配/減配)・F12(PEG)を無効化（暫定措置、分割比率未調整のため）
```

059 最終更新日は P0-1 コミット時に `2026-04-28` に更新する。

---

### P1-3. IFRS 企業コンセンサス乖離: OdP→OP フォールバック不可（059 記載 TODO, 高優先）⚠️

**症状**: IFRS 企業（ルネサス 6723 等）は J-Quants の `OdP`（経常利益）が NULL。F4（コンセンサス乖離）の Q 比較で `pd.notna(odp)` が偽 → コンセンサスが BQ にあるのに NaN としてスキップされる。

**該当**: `earnings_model_predict.ipynb` Cell 5 §1-8, コンセンサス乖離計算

```python
# 現行
cons_profit = cons_map.get((tk, cur_per, 'CURRENT'))
if pd.notna(odp) and cons_profit is not None and cons_profit != 0:
    # ← IFRS企業は odp=NaN でここに入らない
```

**根本原因と調査結果**:

> **CONSENSUS.PROFIT の利益段階（確認済み）**: BQ `CONSENSUS` テーブルには **2つのソース**が混在する:
> 
> | SOURCE | 格納内容 | PERIOD | 備考 |
> |--------|---------|--------|------|
> | **RAKU**（楽天証券） | **経常利益**（百万円） | CURRENT, NEXT | `conse_rakuten.py` で確認 |
> | **IFIS**（アイフィス） | **経常利益**（百万円） | CURRENT のみ | NEXT なし |
> 
> **両ソースとも経常利益（OdP相当）を格納**している。

**OdP→OP フォールバックが危険な理由**: IFRS 企業の OP（営業利益）と日本基準の OdP（経常利益）は利益段階が異なる（OP < OdP が一般的）。CONSENSUS.PROFIT が経常利益であるため、IFRS 企業の OP をコンセンサス経常利益と比較すると**系統的に「コンセ未達」方向にバイアス**が発生する。大型 IFRS 企業（ルネサス・ソニー・任天堂・日立等）でスコアが下方に歪む。

**修正方針（NaN-safe アプローチ）**: IFRS 企業は OdP→OP フォールバック**せず NaN のまま残す**。不正確な比較より欠損の方が安全。

```python
# before
cons_profit = cons_map.get((tk, cur_per, 'CURRENT'))
if pd.notna(odp) and cons_profit is not None and cons_profit != 0:
    cons_yen = cons_profit * 1_000_000
    consensus_deviation = float((odp - cons_yen) / abs(cons_yen))

# after: 変更なし（OdP が NaN なら NaN のまま。OP フォールバックしない）
# ただし reasons に IFRS による欠損であることを記録
cons_profit = cons_map.get((tk, cur_per, 'CURRENT'))
if pd.notna(odp) and cons_profit is not None and cons_profit != 0:
    cons_yen = cons_profit * 1_000_000
    consensus_deviation = float((odp - cons_yen) / abs(cons_yen))
    _f4_source = 'Q_CURRENT'
elif not pd.notna(odp) and cons_profit is not None:
    reasons.append('IFRS/OdP欠損→コンセ比較スキップ')
```

FY パスも同様:
```python
# FY CURRENT fallback path — OdP が NaN なら OP フォールバックしない
if pd.notna(odp) and cons_cur is not None and cons_cur != 0:
    cons_cur_yen = cons_cur * 1_000_000
    consensus_deviation = float((odp - cons_cur_yen) / abs(cons_cur_yen))
elif not pd.notna(odp) and cons_cur is not None:
    reasons.append('IFRS/OdP欠損→コンセ比較スキップ')
```

> **将来改善案（本プランスコープ外）**: IFRS 企業用のコンセンサス比較を実現するには、(a) IFRS 企業のコンセンサス OP を別途収集する、(b) 税前利益で比較する、等が必要。現時点では IFRS 企業の F4 は NaN（±0）として安全に運用。

> **IFIS ソース注意**: IFIS は CURRENT のみ（NEXT なし）。FY の翌期コンセンサス比較は RAKU ソースのみ有効。predict ノートブックで `cons_map.get((tk, 'FY', 'NEXT'))` が None の場合、IFIS のみの銘柄である可能性がある。

**呼び出し側への波及**: batch_rerun にも同一修正

**検証**:
1. ルネサス（6723, IFRS）の過去 PREDICT_DATE で、`reasons` に `'IFRS/OdP欠損→コンセ比較スキップ'` が記録されることを確認
2. 日本基準企業では従来通り consensus_deviation が計算されることを確認
3. IFIS のみ銘柄で FY NEXT コンセンサスが NaN のまま（RAKU にデータない場合）であることを確認

**ロールバック**: P0-1 と同一（コミット revert）

---

### P1-4. F12 PER データ未結合（059 記載 TODO, 高優先）⚠️

**症状**: predict ノートブックの `per` 特徴量は `price_map.get(tk) / float(row.get('ForEPS', 0))` で計算しているが、ForEPS が NaN の銘柄（J-Quants が EPS 予想を返さないケース）では F12 が構造的に不発。4/24 反省会でメタウォーター（9551）が `per=null` で F12 不発だった。

**059 記載内容（テーマC補足, line 259）**: 「PERデータはyfinance週次ロードでBQに取得済みだが、predictノートブックで結合していないため`per=null`→F12が構造的に不発。結合実装すればメタウォーター型を捕捉可能」

**該当**: `earnings_model_predict.ipynb` Cell 5 §1-8, `per` 計算

```python
# 現行: J-Quants ForEPS ベースのみ
'per': float(price_map.get(tk, float('nan')) / float(row.get('ForEPS', 0)))
    if price_map.get(tk) and pd.notna(row.get('ForEPS')) and float(row.get('ForEPS', 0)) > 0
    else None,
```

**根本原因**: ForEPS が NaN の銘柄に対するフォールバック PER ソースがない。

**修正方針**: ForEPS が NaN の場合、BQ `YF_STOCK_INFO.FORWARD_PE`（yfinance 由来、予想 PER）をフォールバックとして使用。

> **データソース決定根拠**: `STOCK_PRICE_JQUANTS` に PER カラムは存在しない（`data_catalog.md` lines 39-61 で確認済み）。yfinance 由来の PER は `YF_STOCK_INFO` テーブルに `FORWARD_PE`（予想 PER）/ `TRAILING_PE`（実績 PER）として格納。predict の PER は ForEPS ベース = 予想 PER のため `FORWARD_PE` を使用。

```python
# Cell 5: P1-1 と同じ YF_STOCK_INFO クエリで FORWARD_PE も取得
# （P1-1 の market_cap クエリと統合）
q_yf_info = f"""
SELECT TICKER, MARKET_CAP, FORWARD_PE, LOADED_AT
FROM `gmailpj-357912.STOCK.YF_STOCK_INFO`
WHERE LOADED_DATE <= '{PREDICT_DATE_HYPHEN}'
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY TICKER ORDER BY LOADED_AT DESC
) = 1
"""
df_yf_info = bq_client.query(q_yf_info).to_dataframe()

# market_cap_map（P1-1 と共有）
market_cap_map: dict[str, float] = {}
# forward_pe_map（P1-4 用）
forward_pe_map: dict[str, float] = {}
for _, r in df_yf_info.iterrows():
    if pd.notna(r['MARKET_CAP']) and r['MARKET_CAP'] > 0:
        market_cap_map[r['TICKER']] = float(r['MARKET_CAP']) / 1e8
    if pd.notna(r['FORWARD_PE']) and r['FORWARD_PE'] > 0:
        forward_pe_map[r['TICKER']] = float(r['FORWARD_PE'])

# §1-8 特徴量計算
_for_eps_per = (
    float(price_map[tk] / float(row['ForEPS']))
    if price_map.get(tk) and pd.notna(row.get('ForEPS')) and float(row.get('ForEPS', 0)) > 0
    else None
)
_per = _for_eps_per if _for_eps_per is not None else forward_pe_map.get(tk)

results.append({
    ...
    'per': _per,
    ...
})
```

> **⚠️ 週次スナップショット注意（P1-1 と同様）**: `YF_STOCK_INFO` は週次ロード。`FORWARD_PE` は最大7日古い値。決算発表直後の株価・EPS 変動は反映されないが、ForEPS が NaN の銘柄でのフォールバック用途としては許容範囲。
> 
> **⚠️ FORWARD_PE の NULL カバレッジ**: yfinance の `Ticker.info['forwardPE']` は一部銘柄で欠損する（アナリストカバーなし等）。ForEPS も NaN かつ FORWARD_PE も NULL の銘柄は F12 不発のまま残るが、これは情報不足による正当な欠損。

**呼び出し側への波及**: batch_rerun にも同一修正。P1-1 と共有の `YF_STOCK_INFO` クエリを `fetch_shared_data()` に追加。

**検証**: メタウォーター（9551）で修正後に `per` が値を持ち、F12 が発火することを確認

**ロールバック**: P0-1 と同一（コミット revert）

---

## 検証戦略

1. **smoke test**: 直近の PREDICT_DATE（20260428）で以下を確認:
   - 株式分割銘柄の `has_stock_split=True`、F11/F12 スキップ、F14 発火
   - IFRS 企業（ルネサス等）の reasons に `'IFRS/OdP欠損→コンセ比較スキップ'` が記録される
   - 大型 FY 翌期未開示で -1 発火
2. **横展開**: batch_rerun で 4/22-4/24 を再実行し、修正前後のスコア差分を確認
3. **本番適用判断基準**: smoke test PASS + 横展開で想定外の大量スコア変動がないこと
4. **回収手順**: batch_rerun で過去分再生成可能。コミット revert で完全復元

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/059_earnings_model_eda.md`（親知見・因子テーブル・反省会ログ）
- 既存バグ修正計画: `docs/plans/20260427_160000_earnings_model_yoy_qoq_bug.md`（P1-2/P1-3 batch_rerun look-ahead bias は本プランのスコープ外、既存計画で対応）
- ザラ場修正コミット: 3ae2a44（株式分割検知）、014a327（IFRS/US-GAAP OP+配当+ForEPS+翌期非開示フィルタ）
- ザラ場コードレビュー: `docs/reviews/013_zaraba_xbrl_quarter_vs_cumulative.md`
- フォーマット正本: `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット

---

## レビュー追記: 2026-04-28 23:15 JST — code-reviewer

- 日時: 2026-04-28 23:15 JST
- 対象: `docs/plans/20260428_220000_predict_zaraba_parity.md`
- パターン: 2 (改修)
- レビュアー: Claude (code-reviewer runbook)

---

### 【サマリー】

- 変更の要約: ザラ場ツールで修正済みの株式分割ガード・翌期非開示ペナルティ・IFRS OdP フォールバック・PER フォールバックを predict ノートブック / batch_rerun に波及させるプラン
- 品質評価: **B** — P0 系の方針は正しいが、P1-1 と P1-4 にデータソース誤認があり実装時にクラッシュを招く
- 主要リスク:
  1. P1-4 が `STOCK_PRICE_JQUANTS.PER` を参照するがこのカラムは存在しない（正しくは `YF_STOCK_INFO.FORWARD_PE`）
  2. P1-1 が J-Quants `get_fin_summary` の `ShOutFY` を前提するが、このフィールドは BQ `fin_summary` 由来であり predict ノートブックの J-Quants API レスポンスには含まれない可能性が高い
  3. P1-3 の IFRS OdP→OP フォールバックで CONSENSUS テーブルの PROFIT 列が経常利益/営業利益のどちらを格納しているか未確認のまま混在比較する

### 【パターン2: 改修プラン評価】

#### フォーマット適合性チェック

- [x] 冒頭に対象ファイルの基準 commit hash が書かれているか — **OK**（`3ae2a44` 明記）
- [x] 前提サマリで過去修正と残件数が明示されているか — **OK**
- [x] 優先度の定義（P0/P1/P2 昇格基準）が冒頭にあるか — **OK**
- [ ] 各項目が「症状 / 該当 / 根本原因 / 修正方針 / 呼び出し側波及 / 検証 / ロールバック」7 フィールドを揃えているか — **P0-2 にロールバック欠落、P1-2 に検証・ロールバック欠落、P1-3/P1-4 にロールバック欠落**
- [x] 修正方針に before/after の両方が書かれているか — **OK**（P0-1/P1-1/P1-3/P1-4 に before/after あり）
- [x] 呼び出し側への波及が該当行リストで明示されているか — **OK**（P0-1 に batch_rerun / show_prediction / review_report 明記）
- [ ] 「既に〜がある」系の前提記述を実コードと照合し、食い違いが無いか — **NG: 2 件（後述 #1, #2）**
- [ ] アンチパターン対応表（plan ID → 004/T/G）が末尾にあるか — **欠落**
- [x] 検証戦略が smoke / dev / prod / 回収手順の 4 段を網羅しているか — **OK**（smoke / 横展開 / 本番適用判断基準 / 回収手順）
- [x] ロールバック手順が書かれているか — **OK**（P0-1 に記載。ただし P0-2 以降は個別記載なし）
- [ ] 読みづらさ・デッドコードだけで P0 に置かれている項目が無いか — **OK**（P0 は実バグ）
- [x] 関連 commit・知見 MD・incident ログへのリンクがあるか — **OK**

**フォーマット違反**:
1. アンチパターン対応表（plan ID → 004/T/G）が末尾に無い
2. P0-2/P1-2/P1-3/P1-4 のロールバックフィールドが欠落（P0-1 の「コミット revert」に実質包含されるが、各項目に「P0-1 と同一」の明示が必要）
3. P1-2 は「P0-1 の一部」と記載しつつ独立項目として存在。059 更新が主目的なら P0-1 の検証欄に統合すべき

#### 妥当性

P0-1/P0-2（株式分割ガード）は真因に対処している。ザラ場ツール 3ae2a44 の修正をデータソース差（XBRL `_related_titles` vs J-Quants + TDnet BQ/HTML）に合わせて翻訳する方針は正しい。F11/F12 のガード・F14 新設の3点セットはザラ場側と同等の保護を提供する。

P1-1（翌期非開示ペナルティ）の方向性は正しい。ザラ場ツール zaraba_earnings.py:1526-1529 で `cap >= 3000` による大型限定ペナルティが実装済みであり、predict 側に同等ロジックを入れるのは合理的。

P1-3（IFRS OdP→OP フォールバック）も方向性は正しいが、比較先の CONSENSUS テーブルが経常利益相当か営業利益相当かの確認が不足している（後述 #3）。

P1-4（PER データ未結合）の方向性は正しいが、データソースの指定が誤っている（後述 #1）。

#### 副作用・デグレードチェック

- [x] P0-1(A) Cell 5 の TDnet 検索に `_STOCK_SPLIT_KW` を追加する修正は、既存の yanoshin/HTML/BQ 3段フォールバック構造に正しく組み込まれている。BQ パスの WHERE 句に `OR DOC_TITLE LIKE '%株式分割%'` を追加する点も問題ない
- [x] P0-1(B) `compute_score()` の F11/F12 ガードは `if not row.get('has_stock_split')` を前段に追加するだけで、既存の分岐ロジックを変更しない。分割なし銘柄では `has_stock_split=False` なので既存動作は保持される
- [x] P0-1(C) GCS prediction JSON に `has_stock_split` を追加する点は後方互換（既存の JSON に無いキーが増えるだけ）。ただし `show_prediction.py` や `review_report.py` が KeyError を出さないか確認が必要
- [x] P1-1 の `score -= 1` は、現行の「reasons に追加するだけでスコア変更なし」を変えるため、過去の batch_rerun 結果とスコアが変わる。これは意図的な改善であり問題ない
- [ ] P1-3 の OdP→OP フォールバックで、**IFRS 企業の Q パス（非 FY）でも `odp` が NaN → `op` にフォールバックする**。IFRS 企業の OP と日本基準企業の OdP は利益段階が異なる（OP < OdP が一般的）。CONSENSUS テーブルの PROFIT が経常利益相当を格納している場合、IFRS 企業で OP を使うと**系統的に下方バイアス**が発生する（後述 #3）

#### 抜け漏れ（類似観点での横展開含む）

- [ ] **F12 の FY 限定問題**: zaraba_earnings.py:1670 では F12（PEG）に `if cur_per == "FY" and not has_stock_split:` と FY 限定条件がある。predict ノートブック Cell 7 の F12 には FY 限定条件がない（`_nyc12 = row.get('next_year_op_change')` は FY でのみ非 None になるため実質 FY 限定だが、暗黙依存）。プランはこの差異に言及していない
- [ ] **batch_rerun の GCS prediction カラムリスト**: batch_rerun_predict.py:721-729 の `pred_records` カラムリストに `has_stock_split` を追加する必要がある。P0-2(B) で `compute_score()` の修正は記載されているが、カラムリスト追加は明記されていない
- [ ] **`market_cap_oku` が batch_rerun で取得可能か**: batch_rerun の `fetch_shared_data()` は STOCK_CODE_LIST しか取得しておらず、YF_STOCK_INFO（MARKET_CAP カラムあり）は取得していない。P1-1 で `market_cap_oku` を使う場合、batch_rerun にもデータ取得の追加が必要
- [ ] **EDA ノートブックの `has_stock_split` との整合**: EDA ノートブック（cell-9）は `MAIN_CATEGORY == '株式分割・併合'` で検知し `CHUNK_TEXT` から分割比率も抽出している。predict ノートブックは `DOC_TITLE LIKE '%株式分割%'` で検知する。検知方法が異なるが、株式併合を拾うか否かの差異がある（併合時も per-share 指標が狂う）
- [ ] **059 因子テーブルの F11/F12 行への注記追加**: P1-2 で F14 行を更新するが、F11/F12 の行にも「F14 発火時は無効化」の注記を入れるべき

#### 新規リスク

- P1-1 の時価総額計算で J-Quants `get_fin_summary` に `ShOutFY` が含まれない場合、`market_cap_oku` が常に None になり、ペナルティが一切発火しない（silent failure）
- P1-4 の PER フォールバックで `YF_STOCK_INFO` は週次スナップショットのため、直近値が 1 週間以上古い可能性がある。決算発表当日の株価変動を反映しない PER が使われるリスク
- batch_rerun で `DATE_PAIRS` 外の日付を再実行する際、BQ クエリ #6 の `SUBMISSION_DATE BETWEEN` 範囲に含まれない日付は株式分割検知が効かない

---

### 【重大な指摘】（即修正）

#### #1 P1-4: `STOCK_PRICE_JQUANTS` に PER カラムは存在しない

- 箇所: `docs/plans/20260428_220000_predict_zaraba_parity.md` P1-4 修正方針
- 事象: プランが `STOCK_PRICE_JQUANTS` テーブルに `PER` カラムがあると前提しているが、`data_catalog.md` lines 39-61 のスキーマに PER は存在しない。yfinance 由来の PER データは **`YF_STOCK_INFO`** テーブルの `TRAILING_PE`（実績 PER）/ `FORWARD_PE`（予想 PER）に格納されている（`data_catalog.md` lines 1488-1489）
- トリガー: P1-4 のコード例をそのまま実装した場合
- 影響: BQ クエリでカラムが見つからず `BadRequest` エラー。または SQL に `PER` を追加しても値が全て NULL → `per_bq_map` が空 → フォールバックが機能しない
- 根拠: `data_catalog.md` lines 39-61 に STOCK_PRICE_JQUANTS の全 18 カラムが列挙されており PER は含まれない。lines 1488-1489 に `YF_STOCK_INFO.TRAILING_PE` / `FORWARD_PE` が存在
- 推奨対応: P1-4 のデータソースを `YF_STOCK_INFO` に変更。`FORWARD_PE` を使用（predict の PER は ForEPS ベース = 予想 PER のため）。ただし YF_STOCK_INFO は週次スナップショットで WRITE_APPEND のため、as-of 最新を取得するクエリ（`ROW_NUMBER() OVER (PARTITION BY TICKER ORDER BY LOADED_AT DESC)`）が必要

#### #2 P1-1: J-Quants `get_fin_summary` に `ShOutFY` が含まれない可能性

- 箇所: `docs/plans/20260428_220000_predict_zaraba_parity.md` P1-1 修正方針(A)
- 事象: プランが `row.get('ShOutFY')` で J-Quants fin_summary のフィールドを参照しているが、EDA ノートブック（cell-4）では `ShOutFY` を **BQ `fin_summary` テーブル**から `NUMBER_OF_ISSUED_AND_OUTSTANDING_SHARES_AT_THE_END_OF_FISCAL_YEAR_INCLUDING_TREASURY_STOCK AS ShOutFY` として取得している。predict ノートブック Cell 5 は `jq_cli.get_fin_summary()` の API レスポンスを使っており、BQ の fin_summary テーブルにあるカラムが全て J-Quants API レスポンスに含まれるとは限らない
- トリガー: J-Quants API が `ShOutFY` フィールドを返さない場合
- 影響: `market_cap_oku` が常に None → 翌期非開示ペナルティが大型株でも発火しない（silent failure、エラーは出ない）
- 根拠: predict ノートブック Cell 5 の数値変換リストに `ShOutFY` は含まれていない。Cell 5 で `df_fin` に結合されるのは J-Quants API 由来のカラムのみ
- 推奨対応: 時価総額は別ルートで取得する。選択肢: (a) BQ `YF_STOCK_INFO.MARKET_CAP`（yfinance 由来、億円換算で使用）、(b) BQ `STOCK_PRICE_JQUANTS.ADJ_CLOSE` x BQ `fin_summary.ShOutFY` で算出。(a) が最も簡便

#### #3 P1-3: CONSENSUS.PROFIT の利益段階が OdP/OP のどちらに対応するか未検証

- 箇所: `docs/plans/20260428_220000_predict_zaraba_parity.md` P1-3 修正方針
- 事象: `_compare_profit = odp if pd.notna(odp) else op` でフォールバックした値を `CONSENSUS.PROFIT`（百万円）と比較するが、CONSENSUS テーブルの PROFIT 列が経常利益相当か営業利益相当かがプランに記載されていない。`data_catalog.md` の CONSENSUS スキーマでは `PROFIT` としか書かれておらず、楽天証券コンセンサスのスクレイピング元データがどの利益段階かによって意味が変わる
- トリガー: IFRS 企業で `op` にフォールバックした場合（OP < OdP が一般的な日本基準企業のコンセが経常利益ベースだった場合）
- 影響: IFRS 企業で系統的に「コンセ未達」方向にバイアス。大型 IFRS 企業（ルネサス・ソニー・任天堂等）でスコアが下方に歪む
- 根拠: zaraba_earnings.py:1616 の `actual_odp = _to_num(rec.get("OrdinaryProfit"))` も経常利益を使っており、XBRL 側でも IFRS 企業は同じ問題を抱えている可能性がある
- 推奨対応: 実装前に (1) `data_catalog.md` の CONSENSUS テーブル説明、(2) `scripts/conse_rakuten.py` のスクレイピングロジックで PROFIT に何を格納しているか確認。経常利益なら IFRS 企業は OP フォールバックせず NaN のまま残すのが安全（不正確な比較より欠損の方がまし）

---

### 【改善提案】（可読性・保守性）

#### #1 P0-1/P0-2 の compute_score() 二重管理を明示的に管理する仕組み

- 箇所: `earnings_model_predict.ipynb` Cell 7 / `batch_rerun_predict.py:565-673`
- 現状: 059 に「`compute_score()` は完全一致する必要がある」と記載されているが、同一ロジックを 2 箇所に手動コピーで維持している。今回のような修正で片方だけ漏れるリスクが構造的に残る
- 提案: batch_rerun の `compute_score()` を共通モジュール（例: `scripts/earnings_model/scoring.py`）に切り出し、ノートブックと batch_rerun の両方から import する。ただし本プランのスコープ外なので次回リファクタリング課題として 059 の TODO に追記

#### #2 P1-1 の時価総額取得で YF_STOCK_INFO.MARKET_CAP を直接使う

- 箇所: `docs/plans/20260428_220000_predict_zaraba_parity.md` P1-1
- 現状: `_close * float(_shares) / 1e8` で自力計算する方針
- 提案: `YF_STOCK_INFO.MARKET_CAP` を BQ から取得し `/ 1e8` で億円変換する方が簡便かつ正確（自己株式控除済みの時価総額が得られる）。batch_rerun の `fetch_shared_data()` に BQ クエリ追加が必要

---

### 【確認できなかった事項】

- J-Quants `get_fin_summary` API のレスポンスに `ShOutFY` に相当するフィールドが含まれるかどうか。API ドキュメント or 実データの確認が必要（`jq_cli.get_fin_summary(date_yyyymmdd='20260428')` を実行して `df_fin.columns` を確認すれば判明）
- CONSENSUS テーブルの PROFIT 列が経常利益・営業利益・純利益のいずれを格納しているか。`scripts/conse_rakuten.py` のコードを読めば確認可能だが、本レビューでは分析ツール実行が禁止のため未確認
- `YF_STOCK_INFO` の `FORWARD_PE` が全銘柄で取得されているか。yfinance の `Ticker.info['forwardPE']` は一部銘柄で欠損する可能性がある
- batch_rerun の `DATE_PAIRS` を拡張した場合に、BQ クエリ #6 の日付範囲が自動追従するかの確認（`DATE_MIN_PREDICT` / `DATE_MAX_PREDICT` 定数に依存）

---

## レビュー追記: 2026-04-29 00:30 JST — code-reviewer (P1 再レビュー)

- 日時: 2026-04-29 00:30 JST
- 対象: `docs/plans/20260428_220000_predict_zaraba_parity.md` P1-1, P1-3, P1-4（書き直し後）
- パターン: 2 (改修)
- レビュアー: Claude (code-reviewer runbook)
- スコープ: P1 セクションのみ。P0 系と P1-2 は前回レビュー済みのため対象外

---

### 【サマリー】

- 変更の要約: 前回の重大指摘3件（P1-4 存在しないカラム、P1-1 ShOutFY 不在、P1-3 利益段階未検証）を受けて P1 セクションを全面書き直し。YF_STOCK_INFO.MARKET_CAP / FORWARD_PE への変更、NaN-safe コンセンサス比較方針の採用
- 品質評価: **A** — 前回指摘3件は全て適切に解消済み。新データソースの使い方も概ね正しい。残存は通貨誤認1件（重大）と軽微な抜け漏れ数件
- 主要リスク:
  1. P1-1 が MARKET_CAP を「JPY建て（円）」と記載し `/1e8` で億円近似するが、yfinance は日本株(.T)の marketCap を **JPY** で返す。`/1e8` は正しいが理由が誤り（円→億円が正）
  2. notebook と batch_rerun で CONSENSUS の SOURCE フィルタが異なる（notebook=全SOURCE、batch_rerun=RAKU のみ）。P1-3 の NaN-safe 修正を両方に適用する際にこの差異を認識していないと IFIS 銘柄で挙動が割れる
  3. P1-4 で ForEPS ベース PER と FORWARD_PE フォールバックの定義差異（基準日・ソースEPS）に言及がない

### 【パターン2: 改修プラン評価】

#### 前回重大指摘の解消状況

| # | 前回指摘 | 解消状況 | 判定 |
|---|---------|---------|------|
| 旧#1 | P1-4 が `STOCK_PRICE_JQUANTS.PER` を参照 → 存在しない | `YF_STOCK_INFO.FORWARD_PE` に変更済み。P1-1 と BQ クエリ統合、as-of ロジック追加 | **解消** |
| 旧#2 | P1-1 が J-Quants `ShOutFY` を前提 → API レスポンスに含まれない | `YF_STOCK_INFO.MARKET_CAP` に変更済み。as-of クエリ + 週次注意書き追加 | **解消** |
| 旧#3 | P1-3 が CONSENSUS.PROFIT の利益段階を未検証 | 両ソース（RAKU/IFIS）とも経常利益であることを確認済み。IFRS 企業は OdP→OP フォールバックせず NaN のまま残す方針に変更 | **解消** |

#### 妥当性

**P1-1（翌期非開示ペナルティ — YF_STOCK_INFO.MARKET_CAP）**: 方針は正しい。YF_STOCK_INFO の as-of クエリ（`LOADED_DATE <= PREDICT_DATE` + `ORDER BY LOADED_AT DESC` + `QUALIFY ROW_NUMBER() = 1`）はザラ場ツール zaraba_earnings.py:379-384 のクエリ構造を踏襲しつつ、batch_rerun 対応の日付フィルタを追加した適切な設計。ただし通貨記載に誤りあり（後述 #1）。

**P1-3（IFRS コンセンサス — NaN-safe）**: 方針は正しく安全。RAKU/IFIS 両方とも経常利益であることの裏取りが data_catalog.md line 1210 および両スクリプトのコメント（`update_conse_rakuten.py:2` 「経常利益」、`update_conse_ifis.py:2` 「経常利益」）で確認できた。IFRS 企業で OdP=NaN → OP フォールバックせず NaN のまま残すのは、不正確な比較を避ける正しい判断。

**P1-4（PER フォールバック — YF_STOCK_INFO.FORWARD_PE）**: 方針は正しい。ForEPS → FORWARD_PE のフォールバック順は合理的。P1-1 との BQ クエリ統合もコスト効率の面で適切。

#### 副作用・デグレードチェック

- [x] P1-1 の BQ クエリ追加は Cell 5 §1-4 付近に新規セクションとして挿入。既存クエリ（株価・コンセンサス・前回発表等）と独立しており干渉なし
- [x] P1-1 の `score -= 1` は現行の「reasons 追加のみ（スコア不変）」から変更。大型 FY 翌期未開示銘柄でスコアが -1 になる。batch_rerun 再実行時に過去スコアが変わるが、これは意図的な改善
- [x] P1-3 のコード変更はロジック的には「変更なし」（OdP が NaN のときの既存の NaN パスはそのまま）+ reasons への IFRS 欠損記録追加のみ。既存の正常系（日本基準企業）への影響なし
- [x] P1-4 の `_per` フォールバックチェーンは ForEPS ベース PER を優先し、None の場合のみ FORWARD_PE を使用。ForEPS が存在する銘柄では現行と同一の挙動が保たれる
- [ ] P1-1 と P1-4 の統合 BQ クエリが `fetch_shared_data()` に追加される際、既存の 7 本のクエリに 8 本目が加わる。BQ 課金面では問題ないが、`shared` dict のキー名（`df_yf_info` or `market_cap_map` + `forward_pe_map`）がプランに未定義

#### 抜け漏れ（類似観点での横展開含む）

- [ ] **notebook と batch_rerun で CONSENSUS SOURCE フィルタが異なる**: notebook の Cell 5 §1-5 は SOURCE フィルタなし（`ROW_NUMBER() OVER (PARTITION BY TICKER, QUARTER, TARGET ORDER BY DATAAT DESC)` で全 SOURCE の最新を取得）。batch_rerun は `SOURCE = 'RAKU'` 固定（`batch_rerun_predict.py:168`）。P1-3 で「IFIS は CURRENT のみ（NEXT なし）」と注記しているが、notebook では IFIS が CURRENT の最新として選ばれる可能性がある（IFIS の DATAAT が RAKU より新しい場合）。この差異が P1-3 修正の挙動に影響する場面: notebook で IFIS 経常利益が使われ、batch_rerun で RAKU 経常利益が使われ、値が微妙に異なり得る
- [ ] **P1-3 の `elif not pd.notna(odp)` は冗長**: `not pd.notna(odp)` は `pd.isna(odp)` と同義。可読性のため `pd.isna(odp)` に統一すべき（動作上の問題はない）
- [ ] **P1-3 IFIS 銘柄で FY NEXT コンセンサスが None になる注意書きはあるが、コード上の対応が不足**: FY パスで `cons_next = cons_map.get((tk, 'FY', 'NEXT'))` が None → フォールバックで `cons_cur = cons_map.get((tk, 'FY', 'CURRENT'))` を使う既存ロジックは IFIS 銘柄に対しても正常動作するので実害はない。ただしプラン P1-3 の IFRS/OdP 欠損 reasons は Q パスにのみ記載されており、FY CURRENT フォールバックパスの `elif not pd.notna(odp)` が FY パスのコードブロック内に追記されることを明示すべき
- [ ] **batch_rerun の `fetch_shared_data()` に YF_STOCK_INFO クエリを追加する際の日付範囲**: P1-1 の `LOADED_DATE <= '{PREDICT_DATE_HYPHEN}'` は日付が固定される notebook 向けの設計。batch_rerun では DATE_PAIRS 全体（DATE_MIN_PREDICT ~ DATE_MAX_PREDICT）をカバーする必要がある。`LOADED_DATE <= '{hy(DATE_MAX_PREDICT)}'` で全日程の最新が取れるが、as-of 精度は最大 7 日 + DATE_PAIRS 幅分ずれる。プランはこの点に言及していない
- [ ] **前回指摘の持ち越し**: batch_rerun の `pred_records` カラムリスト（`batch_rerun_predict.py:721-729`）に `market_cap_oku` と `has_stock_split` を追加する必要がある（前回レビューで `has_stock_split` の漏れを指摘済み。`market_cap_oku` も同様に追加が必要）

#### 新規リスク

- P1-1 / P1-4 統合クエリの `QUALIFY ROW_NUMBER() OVER (PARTITION BY TICKER ORDER BY LOADED_AT DESC) = 1` は YF_STOCK_INFO 全行をスキャンする。YF_STOCK_INFO は WRITE_APPEND で蓄積しているため行数が増大すると BQ スキャンコストが上がる。ただし現時点では週次 x 数千銘柄 = 数十万行規模なので実用上問題ない
- P1-4 の FORWARD_PE フォールバックで、yfinance の forwardPE と J-Quants の ForEPS ベース PER は基準日が異なる。ForEPS は「当日発表された決算の予想 EPS」、FORWARD_PE は「yfinance が算出した直近のアナリスト予想 EPS」に基づく。同一銘柄で両方値が存在する場合の一貫性は問題ない（ForEPS 優先で FORWARD_PE は使わない）が、ForEPS=NaN で FORWARD_PE にフォールバックした場合、F12（PEG）の分母 `next_year_op_change` と FORWARD_PE の分子（アナリスト予想 EPS）のソースが異なることに留意

---

### 【重大な指摘】（即修正）

#### #1 P1-1: MARKET_CAP は JPY 建て（USD ではない）

- 箇所: `docs/plans/20260428_220000_predict_zaraba_parity.md` P1-1 修正方針(A)、lines 195-196
- 事象: プランに「YF_STOCK_INFO.MARKET_CAP（yfinance 由来、**JPY建て（円）**）」「USD÷1e8→億円」と記載されているが、yfinance は日本株（`.T` サフィックス）の `marketCap` を **JPY（円）** で返す。`scripts/zaraba_earnings.py:794` のコメントにも「円単位 → 億円」と明記されており、`/ 1e8` は「円 → 億円」の換算。USD ではない
- トリガー: コード実装時にこの誤記を信じて USD→JPY 為替換算を追加した場合
- 影響: 時価総額が ~150 倍に膨張し、全銘柄で `market_cap_oku >= 3000` が真となり、翌期非開示ペナルティが中小型にも誤って発火する。または為替換算を正しく入れても、プランの `/1e8` ロジックと矛盾して混乱を招く
- 根拠: `scripts/yf_stock_info_load.py:74` で `("marketCap", "MARKET_CAP")` と直接マッピング。yfinance の公式ドキュメントおよび実データで、日本株の marketCap は当該市場の通貨（JPY）で返される。`scripts/zaraba_earnings.py:794` コメント「円単位 → 億円」。`data_catalog.md:1481` では通貨に関する記載がないが、yfinance の仕様から JPY
- 推奨対応: プランの「JPY建て（円）」を「JPY建て（円単位）」に修正。`/ 1e8` のロジック自体は正しい（円 → 億円）。ザラ場ツールとの一致も確認済み

---

### 【改善提案】（可読性・保守性）

#### #1 P1-3 の `not pd.notna(odp)` を `pd.isna(odp)` に統一

- 箇所: `docs/plans/20260428_220000_predict_zaraba_parity.md` P1-3 修正方針 lines 312, 322
- 現状: `elif not pd.notna(odp) and cons_profit is not None:` — 二重否定で可読性が低い
- 提案: `elif pd.isna(odp) and cons_profit is not None:` に変更。動作は同一だが意図が明確になる

#### #2 batch_rerun の YF_STOCK_INFO クエリで as-of 精度を明示

- 箇所: P1-1 呼び出し側波及（batch_rerun）
- 現状: プランは notebook の `LOADED_DATE <= '{PREDICT_DATE_HYPHEN}'` を記載するが、batch_rerun の `fetch_shared_data()` では日付が動的に変わる。DATE_PAIRS 全範囲に対して単一クエリで取得するため、as-of 精度がどの程度ずれるかの注意書きがない
- 提案: batch_rerun 用クエリは `LOADED_DATE <= '{hy(DATE_MAX_PREDICT)}'` で全日程のスナップショットを取得し、`compute_features()` 内で `LOADED_AT <= predict_date 23:59:59` の pandas フィルタで as-of を適用する方式を明記。または「週次スナップショットなので DATE_PAIRS 13 日幅であれば as-of 差異は最大 7 日で閾値判定に影響しない」と明示的に許容する

#### #3 P1-3 FY パスの IFRS 欠損 reasons を明示

- 箇所: `docs/plans/20260428_220000_predict_zaraba_parity.md` P1-3 修正方針の FY パス
- 現状: FY パスのコード例（lines 318-323）は Q パスと同じ `elif not pd.notna(odp)` を追記しているが、FY パスには `cons_next`（NEXT）→ `cons_cur`（CURRENT フォールバック）の 2 段構造がある。IFRS 企業で OdP=NaN の場合、FY_NEXT パスの `nx_fodp` も NaN であるため `cons_next` パスには入らず、CURRENT フォールバックの `elif not pd.notna(odp)` で reasons が記録される。この動作は正しいが、NEXT パスにも OdP 相当の NaN チェックを入れるべきか検討（`nx_fodp` は OdP ベースなので IFRS 企業では NaN のはず — 確認が必要）
- 提案: J-Quants の `NxFOdP` が IFRS 企業で NaN になることを確認し、確認結果をプランに追記

---

### 【確認できなかった事項】

- yfinance の `Ticker.info['marketCap']` が日本株で JPY を返すことは `zaraba_earnings.py:794` のコメントおよび yfinance の仕様から強く推定されるが、分析ツール実行禁止のため BQ の実データでの確認はできていない。実装前に `SELECT TICKER, MARKET_CAP FROM YF_STOCK_INFO WHERE TICKER = '7203' QUALIFY ROW_NUMBER() OVER (PARTITION BY TICKER ORDER BY LOADED_AT DESC) = 1` で桁数を確認すべき（トヨタの時価総額が ~50兆円 = ~5e13 であれば JPY、~3e11 程度であれば USD）
- J-Quants API の `NxFOdP`（翌期予想経常利益）が IFRS 企業で NaN になるかどうか。NaN でなければ FY_NEXT パスで IFRS 企業のコンセンサス比較が OdP ベースで動作し、NaN-safe の考慮は不要になる可能性がある
- `YF_STOCK_INFO.FORWARD_PE` の NULL 率。yfinance の forwardPE はアナリストカバレッジが薄い銘柄で欠損する。ForEPS も NaN かつ FORWARD_PE も NULL の銘柄がどの程度存在するかで、P1-4 の実効性が変わる
- batch_rerun の CONSENSUS クエリが `SOURCE = 'RAKU'` 固定であることと notebook が SOURCE フィルタなしであることの意図的差異か否か。TODO コメント（`batch_rerun_predict.py:159`）に「IFIS 優先 as-of マージに対応」とあるため認識済みと推定されるが、P1-3 修正適用時に挙動差を生む可能性がある
