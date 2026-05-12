# CR-126: ザラ場ツール 6557調査起因バグ修正（6件）

**レビュー日**: 2026-05-08 JST
**パターン**: 2（既存コード改修 / バグ修正）
**提出MD**: `docs/plans/20260508_230500_zaraba_6557_bugfix.md`
**対象コード**: `scripts/zaraba_earnings.py`（commit d55fcd8 時点）、`scripts/zaraba_tdnet_poller.py`、`docs/knowledges/tools/066_zaraba_tool.md`
**レビュアー**: code-reviewer サブエージェント

---

## 総合判定: CONDITIONAL APPROVE

プランの構造・フォーマットは高品質。7フィールド完備、before/after提示、整合性マトリクス、4段検証戦略いずれも充足。ただし **P0-3 に重大なデータ可用性問題**（下記 Finding-1）があり、修正方針の補足が必要。他項目は指摘対応後に実装可。

---

## 指摘事項

### [Finding-1] BLOCKER: P0-3 で 1Q/2Q/3Q の F4c が常時不発火になる

**重大度**: BLOCKER（スコアリング精度への直接影響）

**該当**: プランMD L148-154、コード `zaraba_earnings.py:L1814-L1846`

**問題**: P0-3 は F4c のコンセンサス側を `ORD_PROFIT` -> `OP_PROFIT` に変更し、会社実績側を `OrdinaryProfit` -> `cumulative_op` に変更する方針。しかし `consensus_by_q` の 1Q/2Q/3Q データは **IFIS 由来のみ**であり、IFIS は `ORD_PROFIT` しか提供しない（`data_catalog.md:L1235`: "IFIS: 1Q/2Q/3Q/FY。ORD_PROFITのみ（他4列NULL）"）。

`_consensus_to_prior_fields()` (L917-923) で `vals = {col: _to_num(row.get(col)) for col in _CONSENSUS_VALUE_COLS}` と全5列を格納するが、IFIS行では `OP_PROFIT` は `None` になる。したがって修正後の `cons_op = cons_q_data.get("OP_PROFIT")` は 1Q/2Q/3Q で常に `None` を返し、**F4c が FY 以外で一切発火しなくなる**。

一方、QUICK は FY のみ提供（`data_catalog.md:L1234`: "QUICK: FYのみ。5項目すべて"）なので、FY 四半期では `OP_PROFIT` が利用可能。

**現状の動作**: ORD_PROFIT ベースで 1Q/2Q/3Q/FY 全てで F4c が発火可能（IFIS が ORD_PROFIT を 1Q-FY で提供するため）。

**修正後の動作**: OP_PROFIT ベースでは FY のみ発火可能。1Q/2Q/3Q は全滅。

**対処案**:
- (A) F4c に QUARTER ベースのフォールバックを導入: `cons_val = cons_q_data.get("OP_PROFIT") or cons_q_data.get("ORD_PROFIT")`（OP優先、なければ経常で代替）。会社実績側も同様に `cumulative_op or actual_odp`。ただし異種利益混在の問題はフォールバック時に残る
- (B) F4c を FY 限定にする（1Q/2Q/3Q は F4c 対象外と割り切る）。この場合は知見MDの対象Qも修正必要
- (C) 修正をFY限定で適用し、1Q/2Q/3Q は現行（経常利益ベース）を維持する分岐を追加

プラン作成者が意図を明確にしてから実装に進むべき。

---

### [Finding-2] WARN: P0-3 で `cumulative_op` を使うと F4c の「期間スコープ」が変わる

**重大度**: WARN（意図的かもしれないが明示的な記載がない）

**該当**: プランMD L152-154、コード `zaraba_earnings.py:L1818-1822`

**問題**: 現行の F4c は `actual_odp = _to_num(rec.get("OrdinaryProfit"))` で「当該四半期の経常利益」を取得している。`OrdinaryProfit` は XBRL iXBRL から抽出した値で、TDnet の context パターンにより**累計値**が取得される（`zaraba_tdnet_poller.py:L488-493` の累計優先ロジック: `YearDuration` / `AccumulatedQ` を `QuarterDuration` より優先）。

修正方針では `cumulative_op`（= `rec.get("OP")`）に変更する。`OP` は `extracted.operating_profit`（L1566）つまり `OPERATING_PROFIT` タグの抽出値で、こちらも同じく累計優先ロジックで累計値が入る。

結論: **両方とも累計値**であるため、期間スコープの変化はない。ただしプランMD上にこの検証根拠（両者とも累計値である理由）が明示されていない。実装者が「OrdinaryProfit は当期の経常利益、cumulative_op は四半期累計」と誤解するリスクがある。プランに注記を追加すべき。

---

### [Finding-3] WARN: P0-1 の FY 除外で F5 類似の「通期達成率」情報が消失する

**重大度**: WARN（意図的スコープ外の可能性あるが、影響の認知確認が必要）

**該当**: プランMD L30-69、コード `zaraba_earnings.py:L1662-1676`

**問題**: 現行 F1 は FY でも発火し、`cumulative_op / effective_forecast_op` が 1.2 超のとき（= 通期実績が予想を20%以上上回った）にスコア加点する。これは本来の「進捗率サプライズ」ではないが、「予想比上振れ」のシグナルとしては機能していた。

修正後は FY で F1 が発火しなくなるため、「FY で通期実績 >> 通期予想」のケースがスコアに反映されなくなる。F2（ガイダンス修正）は「今日の新FOP vs 直前FOP」なので、FOP 非開示で prior の forecast_op が古い場合にはカバーできない。

6557 のような**誤発火**を防ぐための修正であることは理解しており、FY 除外自体は正しい。ただし「FY で実績が予想を大幅に上回るケース」が今後別因子でカバーされるか、意識的にスコープ外としたかをプランに一言記載すると、将来の改修者が迷わない。

**対象Q列への副作用**: F4（L1715 `if cur_per == "FY"` 限定）、F5（L1732 `if cur_per == "3Q"` 限定）、F7g（L1860 `if cur_per == "FY"` 限定）はいずれも `cur_per` ガードが独立しているため、P0-1 の `if cur_per in ("1Q", "2Q", "3Q")` ガード追加は影響しない。F2/F3/F6/F7/F8/F10/F13/F14 は全Q対象で cur_per フィルタなし。**副作用なし**。

---

### [Finding-4] WARN: P1-1 の forecast_op_source 判定ロジックに曖昧なケースがある

**重大度**: WARN

**該当**: プランMD L189

```python
"forecast_op_source": "xbrl" if (today_forecast_op is not None and today_forecast_op != 0) else ("prior" if forecast_op else None),
```

**問題**: `today_forecast_op` が `0`（ゼロ）の場合、`today_forecast_op != 0` が False になり `"prior"` にフォールバックする。しかし `effective_forecast_op` の定義（L1645-1648）でも同じ条件で prior にフォールバックする:

```python
effective_forecast_op = (
    today_forecast_op if (today_forecast_op is not None and today_forecast_op != 0)
    else forecast_op
)
```

つまり `today_forecast_op == 0` のとき、`effective_forecast_op = forecast_op`（prior由来）となり、`forecast_op_source = "prior"` になる。これは整合している。

ただし `forecast_op` も `None` かつ `today_forecast_op` も `None` のとき、`forecast_op_source = None` だが `effective_forecast_op = None` にもなるので、`forecast_op` 列自体が `None` で source も `None` は整合。

**結論**: ロジック自体は正しいが、`today_forecast_op == 0` のエッジケース（まずありえないが、赤字企業の予想OP=0がXBRLに入る可能性）について、プラン内に「effective_forecast_op と同一条件」であることの注記があると、レビュー負荷が下がる。これは nit レベル。

---

### [Finding-5] INFO: P0-2 の OP_PROFIT 可用性は確認済み -- 問題なし

**重大度**: INFO（確認結果の記録）

**該当**: プランMD L75-113

`consensus_next` は `_consensus_to_prior_fields()` L933-936 で `fy > current_fy` の FY 行からのみ生成される。QUICK は FY で 5 項目全て提供するため、`consensus_next["OP_PROFIT"]` は利用可能。

ただし QUICK データが存在しない銘柄（IFIS のみカバー）の場合、`consensus_next` 自体が FY IFIS 行から構築される可能性がある（L927 の `q in {"1Q", "2Q", "3Q"}` ガードで 1Q-3Q は `by_q` に入るが、FY は L930-936 で処理される。IFIS FY 行は `ORD_PROFIT` のみで `OP_PROFIT = None`）。この場合、P0-2 修正後は `cons_next_op = None` となり `_guidance_vs_consensus` が `None` を返す。現行の `ORD_PROFIT` ベースでは値が返るケースが NULL になる。

**ただし** `_guidance_vs_consensus` は観察用（`obs_` プレフィックス）でスコアに影響しないため、これは許容範囲。プランの「観察用フィールドのためスコアに影響なし」(L112) の記載と整合。

---

### [Finding-6] INFO: P1-2 の知見MD修正は正確

**重大度**: INFO

**該当**: プランMD L202-228、コード `zaraba_earnings.py:L1710-1718`

コード L1711-1713 のコメントで「旧実装は effective_forecast_op を分母にしていたが、FY発表時は cumulative_op = 今期通期実績が確定しており、来期予想 vs 今期実績を採用」と理由が記載されている。L1717-1718 で実際に `cumulative_op` を分母にしている。知見MD L130 の「NxFOP vs 今期予想」を「NxFOP vs 今期実績OP」に修正するのは正確。

---

### [Finding-7] INFO: P1-3 の表示行修正は P0-3 と連動必須

**重大度**: INFO

**該当**: プランMD L232-268、コード `zaraba_earnings.py:L995-999`

P1-3 は prepare 表示のコンセンサス列を `ORD_PROFIT` -> `OP_PROFIT` に変更する。ここでも Finding-1 と同じ問題が発生する: 1Q/2Q/3Q 銘柄は IFIS 由来で `OP_PROFIT = None` となり、表示上「コンセ」列が常に `-` になる。P0-3 の方針が確定してから（FY限定 or フォールバック導入 等）、P1-3 も連動して方針を決めるべき。

---

### [Finding-8] INFO: 066知見MD の因子テーブルに F4c/F7g/F11/F12/F14 が未掲載

**重大度**: INFO（本プラン外だが関連情報として記録）

**該当**: `docs/knowledges/tools/066_zaraba_tool.md:L125-137`

知見MD のスコアリング因子テーブルには F1-F3, F5-F8, F8b, F10, F13 のみ掲載。コードに実装済みの F4c（コンセンサス乖離）、F7g（成長加速/減速）、F12（PER/PEG）、F14（株式分割）がテーブルに未掲載。L161-164 の「EDA因子との対応」節で F4/F7/F12 は「未実装」と記載されているが、実際にはコードに実装済み。P1-2 で知見MD を修正するなら、ここも併せて更新すべき。

---

## プランフォーマット評価

| チェック項目 | 判定 |
|------------|------|
| 基準 commit hash | OK（d55fcd8 明示） |
| 7フィールド完備（全項目） | OK |
| before/after 両方提示 | OK |
| 呼び出し側波及の行番号明示 | OK |
| 対応アンチパターン表 | OK（該当なし明示） |
| 4段検証戦略 | OK |
| ロールバック手順 | OK |
| 整合性マトリクス | OK（優秀 -- 全因子の利益種別を網羅的に列挙） |

---

## 修正要求まとめ

| # | 重大度 | 対象 | アクション |
|---|--------|------|----------|
| F-1 | BLOCKER | P0-3 | 1Q/2Q/3Q での OP_PROFIT=NULL 問題を解決する方針を追記。(A)フォールバック/(B)FY限定/(C)分岐のいずれか |
| F-2 | WARN | P0-3 | cumulative_op と OrdinaryProfit が同じ累計値であることの根拠を注記 |
| F-3 | WARN | P0-1 | FY除外で失われるシグナル（通期上振れ）について意識的スコープ外である旨を注記 |
| F-7 | INFO | P1-3 | P0-3 方針確定後に連動して修正方針を確定 |
| F-8 | INFO | 066知見MD | 因子テーブルに F4c/F7g/F12/F14 を追記（本プランの P1-2 作業と併せて実施推奨） |

---

## レビューサマリ

プランの構造品質は高い（整合性マトリクスが全因子を網羅しており、cross-cutting な影響分析ができている点は秀逸）。唯一の BLOCKER は P0-3 におけるデータソースの可用性問題で、IFIS が 1Q/2Q/3Q で OP_PROFIT を提供しないことに起因する。この点が解決されれば実装に進んでよい。
