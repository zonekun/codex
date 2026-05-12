# CR-128: ザラ場ツール 6557調査起因バグ修正 v2（8件）

- 日時: 2026-05-08 23:37 JST
- 対象: `docs/plans/20260508_230500_zaraba_6557_bugfix.md`（23:40 更新版）
- パターン: 2（既存コード改修 / バグ修正）
- レビュアー: Claude (code-reviewer runbook)
- 前回レビュー: CR-126（CONDITIONAL APPROVE、BLOCKER 1件）

---

## 【サマリー】

- 変更の要約: ザラ場スコアリング7件のバグ修正+新因子F15追加。CR-126 BLOCKER（P0-3のOP_PROFITデータ可用性問題）を「通期=営業利益、四半期=経常利益」の分岐で解決。F15（通期着地サプライズ）を新設しP0-1のFY除外で失われるシグナルを補完。
- 品質評価: A -- プランの構造品質は高い。CR-126指摘を的確に反映。整合性マトリクス更新・検証戦略・ロールバック全て充足。重大な論理欠陥なし。
- 主要リスク:
  1. F2とF15のFY同時発火による二重加点（下記 Finding-1）
  2. F15の分母 effective_forecast_op がBQ prior由来の古い値のとき乖離率が過大に出る（下記 Finding-2）
  3. P1-3 表示行の `q_label` フォールバック `or "FY"` が `_normalize_quarter` 戻り値 `None` ケースでFY扱いになる（下記 Finding-3）

---

## 【改修プラン評価】

### CR-126 BLOCKER 解決確認

CR-126 Finding-1（P0-3で1Q/2Q/3QのF4cが常時不発火になる問題）は解決済み。更新版プランMD L159-164 で `cur_per == "FY"` なら `OP_PROFIT` + `cumulative_op`、それ以外なら `ORD_PROFIT` + `OrdinaryProfit` の分岐を導入。IFIS が1Q/2Q/3Qで ORD_PROFIT のみ提供する制約を正しく反映している。

CR-126 Finding-2（累計値の根拠明示）はプランMD L170 の注記で解決済み。

CR-126 Finding-3（FY除外シグナル消失）はP0-4（F15新設）で対応済み。

### 妥当性

プランの修正方針は各バグの真因に対処している。独立推定した根本原因:

- P0-1: F1の `expected_progress` dict にFYが含まれ、FY時 `expected=1.0` で `surprise = progress / 1.0 = progress` となり、FOP非開示時にprior由来の古い予想値で進捗率が異常値になる -> FY除外が正しい対処
- P0-2: NxFOP（営業利益予想）をORD_PROFIT（経常利益コンセ）と比較する異種利益比較 -> OP_PROFITへの統一が正しい
- P0-3: 一律ORD_PROFITだったF4cをデータソースの可用性に合わせて通期/四半期分岐 -> データの実態に即した正しい対処
- P0-4: P0-1のFY除外で失われる「通期着地サプライズ」を専用因子で補完 -> 設計として妥当。ただしF2との重複リスクあり（Finding-1参照）

方向性の問題なし。対症療法パターンも検出されない。

### 副作用・デグレードチェック

- [x] P0-1 FY除外: F4/F5/F7g/F12 はそれぞれ独自の `cur_per` ガードを持つため影響なし（CR-126 Finding-3 で確認済み）
- [x] P0-3 通期/四半期分岐: FYで OP_PROFIT=NULL の銘柄（QUICK未カバー）ではF4cが不発火になる。これは現行のORD_PROFITベースより劣化するケースだが、P0-3のafter L165 `if actual_val is not None and cons_val and cons_val != 0` で安全にスキップされ、クラッシュはしない。発火率の変化は許容範囲
- [x] P0-4 F15新設: `_score_record` の戻り値構造は不変。scoreとfactorsへの加算のみ。既存テストケース（1Q/2Q/3Q）では `cur_per == "FY"` ガードにより不発火
- [x] P1-1 forecast_op_source列追加: CSV列追加のみ。既存列の変更なし。downstream（GCS results CSV）の読み取り側は新列を無視

### 抜け漏れ（類似観点での横展開含む）

- [x] P0-2 の OP_PROFIT 統一: `_guidance_vs_consensus` は観察用（`obs_` プレフィックス）でスコア非参照。IFIS FY行から `consensus_next` が構築される場合に OP_PROFIT=None で関数が None を返すが、これは CR-126 Finding-5 で許容済み
- [x] P0-3 修正後の F4c コメント更新: プランMD L154-155 の after コードでコメントを「コンセンサス乖離」に簡潔化し、通期/四半期の分岐理由を記載。適切
- [x] 066知見MD F4cの対象Q記載: P1-2でF4定義を修正するが、F4cの通期/四半期分岐の追記は記載されていない。プランMD L261-268 はP1-3（表示行）の修正のみで、F4cの知見MD更新が抜けている。CR-126 Finding-8（F4c/F7g/F12/F14のテーブル未掲載）と合わせて実装時に対応が必要
- [x] 整合性マトリクス: 更新版（L389-408）でF4c FY/1Q2Q3Q分離・F15追加・観察guidance_vs_cons・表示コンセ列の全行が正確に記載されている。整合

### 新規リスク

F2（ガイダンス修正）とF15（通期着地サプライズ）のFY同時発火リスク（下記 Finding-1 で詳述）。これはスコアインフレの可能性であり、データ欠損やクラッシュのリスクではない。

---

## 【重大な指摘】（即修正）

### #1 F2 と F15 が FY で同時発火 -- 二重加点リスク

- 箇所: `scripts/zaraba_earnings.py:L1678-L1687`（F2）、プランMD L196-212（F15新規追加）
- 事象: FY決算でFOP（会社予想営業利益）が開示され、かつ実績OPが予想を大幅に上回るケースでは、F2（ガイダンス修正 `new_forecast_op vs prior forecast_op`）と F15（通期着地 `cumulative_op vs effective_forecast_op`）が同時に発火する。例: 期中に上方修正→FY実績が修正後予想を更に上回る場合、F2で「上方修正+X%」+1点、F15で「通期上振れ+Y%」+1〜2点が加算される。
- トリガー: FY決算で (a) FOP != None かつ forecast_op != 0 かつ `(new_forecast_op - forecast_op) / abs(forecast_op) > 0.05` で F2 発火、かつ (b) `(cumulative_op - effective_forecast_op) / abs(effective_forecast_op) > 0.05` で F15 発火。(b) の `effective_forecast_op` は (a) の `today_forecast_op`（= 今日のFOP）が優先されるため、F15の分母は「今日の新FOP」になる。つまりF2で「前回予想→今回予想」の変化を検知し、F15で「今回予想→実績」の変化を検知する。**これは別の情報を捉えているため、意図的な二重加点であれば問題ない**。
- 影響: スコアが最大+3点（F2:+1 + F15:+2）加算される。これが意図的かどうかをプランに明記すべき。
- 根拠: F2（L1680）は `new_forecast_op`（今日のFOP）vs `forecast_op`（prior）を比較。F15は `cumulative_op` vs `effective_forecast_op`（今日のFOPが優先）を比較。FY で FOP 開示ありの場合、`effective_forecast_op = today_forecast_op` なので、F15 は「実績 vs 今日の新予想」、F2 は「今日の新予想 vs 前回予想」となり、**分母が異なる別の比較**。
- 推奨対応: これは設計判断であり、「別の情報を捉えている」という認識で二重加点を許容するなら、プランMD P0-4 の設計根拠にF2との同時発火について一文追記すればよい（例: 「F2はガイダンス変更幅、F15は着地精度を測る別指標のため、同時発火は意図的に許容」）。EDA検証後にウェイト調整で対応可能。

### #2 F15 の effective_forecast_op が BQ prior 由来の古い値のケース

- 箇所: プランMD L198、`scripts/zaraba_earnings.py:L1645-1648`
- 事象: `effective_forecast_op` は `today_forecast_op`（XBRL FOP）優先、なければ `forecast_op`（BQ prior由来）にフォールバック。6557のケースでは FOP=None のため prior の 750M が使われ、実績 1,219M との乖離が `(1219-750)/750 = +62%` で +2点になる。しかしこの 750M は「何期の何Q時点の予想か」が不明（P1-1 で source 追加予定だが、金額の妥当性は検証できない）。
- トリガー: FY決算で FOP 非開示 かつ BQ prior の forecast_op が古い値（期中上方修正が反映されていない等）
- 影響: F15 の乖離率が実態より過大に計算され、+2点が付く。6557 のような「実際には上振れだが、比較対象が古すぎて乖離が膨張」するケースが発生する。
- 根拠: プランMD L216 の設計根拠「分母は effective_forecast_op（今日の新FOP優先、なければBQ prior）。通期予想との比較なのでこれが正しい分母」は正しいが、BQ prior が「直前の最新予想」であることの保証がない。prior の forecast_op は `_build_prior_data` で fin_summary から取得されるが、期中の修正開示が fin_summary に反映されないケースがある。
- 推奨対応: これは P0-4 の設計上避けられない（利用可能なデータの限界）。プランに「BQ prior の forecast_op が古い場合、乖離率が過大になるリスクがある。forecast_op_source=prior のとき F15 の信頼度は低い」旨の注記を追加し、EDA検証時に `forecast_op_source=prior` ケースの F15 精度を個別に評価することを検証戦略に追記する。

---

## 【改善提案】（可読性・保守性）

### #1 P1-3 表示行の `q_label` フォールバック `or "FY"` と `_normalize_quarter` の整合

- 箇所: `scripts/zaraba_earnings.py:L997`、プランMD L302-303
- 現状: L997 `q_label = _normalize_quarter(item.get("quarter")) or "FY"` で、`_normalize_quarter` が `None` を返す場合に `"FY"` にフォールバックする。`_normalize_quarter`（L851-858）は、入力が空文字列・None・マッピング外の文字列の場合に `None` を返す。プランの after（L321）で `conse_key = "OP_PROFIT" if q_label == "FY" else "ORD_PROFIT"` と分岐するため、`_normalize_quarter` が None を返すケース（= quarter フィールドが不明な銘柄）で `q_label="FY"` → `conse_key="OP_PROFIT"` となり、FY扱いで OP_PROFIT を参照する。これは意図的か不明。
- 提案: prepare 側で `quarter` フィールドは必ず設定されるため、実際には `_normalize_quarter` が None を返すケースは稀。ただし防御的に、フォールバック時のコンセキー選択について一言コメントを追加すると、将来の改修者が迷わない。コメント例: `# quarter 不明時は FY 扱い（FY 前提で OP_PROFIT を参照）`

### #2 066知見MD のスコアリング因子テーブル網羅性

- 箇所: `docs/knowledges/tools/066_zaraba_tool.md:L125-137`
- 現状: CR-126 Finding-8 の指摘が残存。テーブルに F4c/F7g/F11/F12/F14 が未掲載、L161-164 で「未実装」と記載されているが実装済み。P0-4 で F15 が追加されるため、この機会に因子テーブルを完全に更新すべき。
- 提案: P1-2 の知見MD修正作業と併せて、因子テーブルに F4c/F7g/F11/F12/F14/F15 を追記し、L161-164 の「未実装」記述を削除する。

---

## 【プランフォーマット評価】

| チェック項目 | 判定 |
|------------|------|
| 基準 commit hash | OK（d55fcd8 明示） |
| 前提サマリで過去修正と残件数 | OK |
| 優先度定義（P0/P1/P2） | OK |
| 7フィールド完備（全8項目） | OK |
| before/after 両方提示 | OK |
| 呼び出し側波及の行番号明示 | OK |
| 対応アンチパターン表 | OK（該当なし明示） |
| 4段検証戦略 | OK |
| ロールバック手順 | OK（全項目 commit revert） |
| 整合性マトリクス | OK（全因子の利益種別を網羅的に列挙、通期/四半期分岐を反映） |
| 関連ドキュメントリンク | OK |
| 提出前セルフチェック | OK |

フォーマット違反: なし

---

## 【確認できなかった事項】

- F15 の閾値（±5%/±20%）の妥当性は、過去データでのバックテストなしには判断できない。プランに「EDA で検証する」旨の記載があり、閾値は暫定値として許容
- `effective_forecast_op` の BQ prior 由来値の鮮度（何期の何Q時点の予想か）は、`_build_prior_data` の fin_summary 取得ロジックを深追いしないと判定不能。P1-1 の `forecast_op_source` 列追加で事後的にトレース可能になるため、実装後のデータで検証する
- QUICK 未カバー銘柄（IFIS のみ）の FY で `OP_PROFIT=None` となるケースの出現頻度は、BQ CONSENSUS テーブルのデータを集計しないと不明

---

## 【総合判定】

**APPROVE** -- CR-126 BLOCKER は適切に解決されている。Finding-1（F2/F15同時発火）は設計判断の範囲であり、プランへの注記追加のみで実装に進んでよい。Finding-2（prior由来乖離率過大）はデータの限界であり、EDA検証時に評価するアプローチで妥当。実装可。
