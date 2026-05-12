# コードレビュー: XBRL 実績値で四半期単独値が累計値より優先されるバグの修正

- 日時: 2026-04-28 17:00 JST
- 対象: `scripts/zaraba_tdnet_poller.py` (修正済み), `scripts/zaraba_earnings.py` (F4 翌期見通しスコアリング)
- パターン: 1 (新規)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: FY決算のiXBRL実績PL値抽出で、context フィルタが広すぎてQ4単独値が通期累計値に優先採用されるバグを、YearDuration/AccumulatedQ 優先フィルタの追加で修正
- 品質評価: **A** — 真因を正確に特定し、副作用の少ない最小修正で対処。ただし営業収入加算ロジックに同種バグが残存
- 主要リスク:
  1. NET_SALES 営業収入加算ロジック（L509-524）に同種の context 広すぎ問題が残存
  2. `valid_entries[0]` の順序依存が累計フィルタ後も「連結が複数ある場合にどれが選ばれるか」で潜在リスク
  3. `extract_pipeline.py` の `is_valid_context` + `valid_entries[0]` にも同型の順序依存バグ潜在

---

## 【重大な指摘】（即修正）

### #1 NET_SALES 営業収入加算ロジックに累計優先フィルタが未適用

- 箇所: `scripts/zaraba_tdnet_poller.py:509-524`
- 事象: NET_SALES の営業収入加算処理（`TDNET_OPERATING_REVENUE_ADD_TAGS`）で `TDNET_CURRENT_PATTERNS` を使って context をフィルタしているが、今回の修正（L478-484）で追加された累計優先ロジックが**この箇所には適用されていない**。加算対象の `OperatingRevenue2` / `OperatingRevenue` タグに `CurrentQuarterDuration` と `CurrentYearDuration` の両方の context が存在する場合、Q4単独の営業収入が加算される可能性がある
- トリガー: FY決算短信の iXBRL で、NetSales をベースタグとして採用した銘柄で、OperatingRevenue 等の加算タグに Q4単独 context と通期 context の両方が存在する場合
- 影響: NET_SALES に Q4単独の営業収入が加算されるため、NET_SALES が過小評価される。ただし NET_SALES はスコアリングでは直接使われていない（F1/F3/F4/F5/F13 はすべて OP ベース）ため、スコアリングへの即時影響は軽微
- 根拠: L513 で `any(p in ctx for p in TDNET_CURRENT_PATTERNS)` を使い、最初にマッチした entry の値を `break` で採用する（L524）。`TDNET_CURRENT_PATTERNS = ["Current", "ThisQuarter"]` なので `CurrentQuarterDuration` もマッチする。elements 辞書内の出現順は iXBRL 内の出現順に依存し、Q4単独が先に出現すれば Q4単独値が採用される
- 推奨対応: 加算候補の entries を累計優先でフィルタするか、本体の `_extract_tdnet_pl` ループと同じ `YearDuration`/`AccumulatedQ` 優先ロジックをここにも適用する。ただし NET_SALES がスコアリング未使用のため優先度は低い

### #2 `extract_pipeline.py` の `valid_entries[0]` に同型の順序依存バグ

- 箇所: `scripts/xbrl_to_jquants/extract_pipeline.py:164-173`
- 事象: `extract_pipeline.py` の PL 抽出ロジックは `is_valid_context()` でフィルタした後に `valid_entries[0]` を取るが、`CURRENT_DURATION_CONTEXTS` に `CurrentYearDuration` と `CurrentQuarterDuration` の**両方が含まれている**（`xbrl_mapping.py:176-181`）。`zaraba_tdnet_poller.py` と同一構造のバグ
- トリガー: EDINET XBRL で FY 決算の場合に、同一タグに `CurrentYearDuration`（通期累計）と `CurrentQuarterDuration`（Q4単独）の両 context が存在し、Q4単独が先に出現する場合
- 影響: `extract_pipeline.py` は XBRL→J-Quants 形式変換に使われる。通期値に Q4単独値が採用されると、BQ の fin_summary テーブルに誤った実績値がロードされる可能性がある。ただし EDINET XBRL のフォーマットは TDnet iXBRL とは異なり、`Member` を含む context が `EXCLUDE_CONTEXT_PATTERNS` で除外されるため（`xbrl_mapping.py:183`）、実際に発火する頻度は未確認
- 根拠: `CURRENT_DURATION_CONTEXTS` に `CurrentQuarterDuration` が含まれ、`EXCLUDE_CONTEXT_PATTERNS` に `Member` が含まれるため `ResultMember` 付き context は除外される。しかし `Member` を含まない `CurrentQuarterDuration` 単独の context が存在する場合はすり抜ける。EDINET XBRL のフォーマット仕様を実行せずに断定はできないため、「確認できなかった事項」にも記載
- 推奨対応: `extract_pipeline.py` にも同様の累計優先ロジック（`YearDuration` / `AccumulatedQ` / `YTDDuration` 優先）を追加する。または `CURRENT_DURATION_CONTEXTS` から `CurrentQuarterDuration` を除外して `InterimDuration` 等で代替可能か検証する

---

## 【改善提案】（可読性・保守性）

### #1 `TDNET_CURRENT_PATTERNS` の名前と実態の乖離

- 箇所: `scripts/zaraba_tdnet_poller.py:336`
- 現状: `TDNET_CURRENT_PATTERNS = ["Current", "ThisQuarter"]` という名前だが、実態は「当期を含む任意の context」のプレフィックスマッチであり、`CurrentQuarterDuration` も `CurrentYearDuration` も `CurrentAccumulatedQ` もすべてマッチする。今回の修正で累計優先フィルタが追加されたが、`TDNET_CURRENT_PATTERNS` の定義自体は変更されていないため、将来の保守者がこのパターンの広さに気づかず新たなバグを埋め込むリスクが残る
- 提案: (a) コメントに「`Current` は CurrentQuarterDuration / CurrentYearDuration / CurrentAccumulatedQ すべてにマッチする。累計優先ロジック（L478-484）で後段フィルタ済み」と明記する、または (b) `TDNET_CURRENT_PATTERNS` を `["CurrentYearDuration", "CurrentAccumulatedQ", "CurrentQuarterDuration", "ThisQuarter"]` に細分化し、用途別に使い分ける

### #2 累計優先ロジックのガード条件 `len(valid_entries) > 1` は冗長

- 箇所: `scripts/zaraba_tdnet_poller.py:480`
- 現状: `if not is_forecast and len(valid_entries) > 1:` のガード条件。`len(valid_entries) == 1` なら累計フィルタをスキップするが、1件しかない場合にそれが Q4単独であれば結局そのまま採用される。1件の場合は Q4単独と通期累計の区別がつかないため実害はないが、意図が「複数あるときだけ優先選択」なのか「フィルタ結果0件回避」なのか読みにくい
- 提案: `if cumul:` のガードで 0件回避は既に行われているため、`len(valid_entries) > 1` の条件は安全策として妥当。ただし意図をコメントで明示すると保守性が向上する

### #3 `_xbrl_to_jquants_rec` の `extracted.raw_extract.get(...)` の冗長パターン

- 箇所: `scripts/zaraba_earnings.py:1370-1373`
- 現状: 各行が `extracted.raw_extract.get("KEY", {}).get("value") if extracted.raw_extract.get("KEY") else None` と同一パターンの冗長なコードを4行繰り返している。`extracted.raw_extract.get("KEY")` を2回呼んでいる
- 提案: ヘルパーを導入するか、`(extracted.raw_extract.get("KEY") or {}).get("value")` で1行化する。ただし `None` と `{}` の挙動差に注意（現行コードは `None` と falsy dict の両方で `None` を返すので動作は同等）

---

## 【修正例】（必要な箇所のみ）

#### #1 に対する修正案

```python
# before: scripts/zaraba_tdnet_poller.py:509-524
    if ns is not None and ns["tag"].startswith("NetSales"):
        for add_tag in TDNET_OPERATING_REVENUE_ADD_TAGS:
            if add_tag not in elements:
                continue
            # NET_SALES と同じタグなら二重加算しない
            if add_tag == ns["tag"]:
                continue
            for e in elements[add_tag]:
                ctx = e["context"]
                if "Prior" in ctx or "ForecastMember" in ctx:
                    continue
                if any(p in ctx for p in TDNET_CURRENT_PATTERNS):
                    try:
                        add_val = int(float(e["value"]))
                        ...

# after
    if ns is not None and ns["tag"].startswith("NetSales"):
        for add_tag in TDNET_OPERATING_REVENUE_ADD_TAGS:
            if add_tag not in elements:
                continue
            if add_tag == ns["tag"]:
                continue
            add_candidates = []
            for e in elements[add_tag]:
                ctx = e["context"]
                if "Prior" in ctx or "ForecastMember" in ctx:
                    continue
                if any(p in ctx for p in TDNET_CURRENT_PATTERNS):
                    add_candidates.append(e)
            # 累計優先（本体ループと同じロジック）
            if len(add_candidates) > 1:
                cumul = [e for e in add_candidates
                         if "YearDuration" in e["context"] or "AccumulatedQ" in e["context"]]
                if cumul:
                    add_candidates = cumul
            if add_candidates:
                try:
                    add_val = int(float(add_candidates[0]["value"]))
                    ...
```

---

## 【横展開チェック結果】

### 1. `TDNET_CURRENT_PATTERNS` の全使用箇所

| 箇所 | 行 | 用途 | 累計優先適用 | 状態 |
|------|----|------|------------|------|
| `_extract_tdnet_pl` ループ | L456 | 実績PL値の context フィルタ | L478-484 で適用済み | 修正済み |
| NET_SALES 営業収入加算 | L513 | 加算タグの context フィルタ | **未適用** | 要修正（重大指摘 #1） |

使用は上記 2 箇所のみ。他ファイルでの `TDNET_CURRENT_PATTERNS` 参照は無い。

### 2. `valid_entries[0]` の順序依存箇所

| 箇所 | 行 | 累計優先有無 | 状態 |
|------|----|------------|------|
| `zaraba_tdnet_poller.py:_extract_tdnet_pl` | L486 | あり（L478-484） | 修正済み |
| `extract_pipeline.py` | L173 | **なし** | 要確認（重大指摘 #2） |

### 3. 1Q/2Q/3Q 報告時の副作用分析

今回追加された累計優先ロジック（L478-484）:
```python
if not is_forecast and len(valid_entries) > 1:
    cumul = [e for e in valid_entries
             if "YearDuration" in e["context"] or "AccumulatedQ" in e["context"]]
    if cumul:
        valid_entries = cumul
```

各四半期での挙動を脳内シミュレーション:

| 四半期 | iXBRL に出現する context | cumul フィルタ結果 | 副作用 |
|--------|------------------------|--------------------|--------|
| **1Q** | `CurrentAccumulatedQ1Duration`（累計=1Q単独）, `CurrentQuarterDuration`（=1Q単独） | `AccumulatedQ` にマッチ → 累計値採用 | なし。1Q は累計=単独なので同一値 |
| **2Q** | `CurrentAccumulatedQ2Duration`（2Q累計=1Q+2Q）, `CurrentQuarterDuration`（2Q単独） | `AccumulatedQ` にマッチ → 累計値採用 | **正しい動作**。スコアリング側は `cumulative_op - prev_cumulative_op` で standalone_op を算出するため、累計値が必要 |
| **3Q** | `CurrentAccumulatedQ3Duration`（3Q累計=1Q+2Q+3Q）, `CurrentQuarterDuration`（3Q単独） | `AccumulatedQ` にマッチ → 累計値採用 | **正しい動作**。同上 |
| **FY** | `CurrentYearDuration`（通期累計）, `CurrentQuarterDuration`（Q4単独） | `YearDuration` にマッチ → 通期値採用 | **正しい動作**。これが今回の修正対象 |

**結論**: 1Q/2Q/3Q のいずれでも副作用はない。累計優先は全四半期で正しい挙動をする。

### 4. F4 翌期見通しスコアリング (`zaraba_earnings.py:1506-1523`)

修正後の F4 計算:
```python
if cur_per == "FY":
    nx_op = _to_num(rec.get("NxFOP"))
    if nx_op is not None and cumulative_op and cumulative_op != 0:
        nx_chg = (nx_op - cumulative_op) / abs(cumulative_op)
```

- `cumulative_op` = 修正後は通期累計値が入る（修正前は Q4単独値が入りうる）
- `NxFOP` = 翌期通期予想OP（NEXT_YEAR_FORECAST_OP, `NextYearDuration` フィルタ済みで元から正しい）
- 分母が通期実績、分子が翌期予想 → 意図通りの YoY 比較

**F4 の副作用はない**。修正前のバグが解消されるだけ。

---

## 【確認できなかった事項】

- `extract_pipeline.py` が処理する EDINET XBRL で `CurrentQuarterDuration` context が `Member` サフィックスなしで存在するかどうか。`EXCLUDE_CONTEXT_PATTERNS = ["NonConsolidated", "Prior", "Member"]` により `ResultMember` 付き context は除外されるが、`Member` を含まない `CurrentQuarterDuration` 単独 context が EDINET フォーマットに存在するかは実データを確認しないと断定できない。存在しなければ重大指摘 #2 は発火しない
- TDnet iXBRL で `ThisQuarter` を含む context の実例。`TDNET_CURRENT_PATTERNS` に `"ThisQuarter"` が含まれるが、実際にこのパターンでマッチする iXBRL 文書が存在するかは未確認。存在しない場合は事実上 `["Current"]` のみが有効パターン
- `_parse_ixbrl` が返す elements 辞書内の要素順序が、iXBRL ファイル内の出現順と一致する保証。Python 3.7+ の dict は挿入順を保持するため、`re.finditer` の出現順 = elements list の格納順と推論されるが、iXBRL 仕様として Q4 context が YearDuration context より先に出現するかはファイル依存
