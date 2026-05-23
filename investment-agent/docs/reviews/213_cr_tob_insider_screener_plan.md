# レビュー依頼: 015 TOBインサイダー疑い検出スクリーナー Phase 1 計画

- 提出日時: 2026-05-20 JST
- 提出元: メインエージェント

---

## レビュー対象

- **計画MD（メイン対象）**: `docs/plans/analysis-015_tob_insider_screener_20260519_194557.md`
- **関連知見MD（スタブ）**: `docs/knowledges/analysis/015_tob_insider_screener.md`

## レビューパターン

**パターン 4（新規開発・設計計画のレビュー）** — `skills/planning.md` §テンプレート形式（目的/背景/作業ステップ/成果物/完了条件）に準拠。

## レビュースコープ

**Phase 1（未着手分）のみ**を対象とする。Phase 2 / Phase 3 は本レビューの対象外。
具体的には計画MD §作業ステップ → Phase 1 の Step 1〜6（スクリーナー本体実装）。

ただし、Phase 1 の設計が Phase 2 でのバックテストに無理なく接続するかという**前方互換性観点での副次評価**は許容する。

## 事象・背景

- 8141 新光商事の TOB 公表（2026-05-18）に先行する 3 月初旬からの株価急騰（+65%）パターンを定量検出することが動機。
- 既存 `screen_tob.py`（007系・ファンダ基盤）の補完として、価格・出来高の市場微細構造から「初動」を検出する。
- Phase 2 前提作業（BQテーブル `STOCK.DELISTED_STOCKS_TOB_ENHANCE` 設計・データカタログ作成・Codex成果物CSV 取り込み）は db12ca6a で完了済み。
- 本レビューは **Phase 1 実装着手前**の最終チェック。実装開始前の設計欠陥・抜け漏れ・既存システムとの統合不備を洗い出すことが目的。

## 補足情報

### 関連既存システム
- `scripts/screen_tob.py`（007 ML予測モデル、ファンダ基盤）— 補完関係
- `scripts/screen_edinet_delay_tob.py`（008 EDINET遅延スクリーニング）

### 関連知見MD
- `docs/knowledges/analysis/007_tob_ml_prediction.md`
- `docs/knowledges/analysis/008_edinet_delay_tob_screening.md`
- `docs/data_catalog/bq_delisted_stocks_tob_enhance.md`（Phase 2 で使用予定）

### 重点観点
1. **データ取得設計の現実性** — J-Quants 全銘柄(約4000) × 180日分の API スループット・キャッシュ戦略
2. **アルゴリズム設計の妥当性** — 静止スコア × 発火スコアの計算式・各指標の合成方式
3. **既存システムとの統合** — 既存スクリーナーとの命名・出力先・実行タイミングの整合
4. **完了条件の検証可能性** — 8141 の smoke test 成功基準が「3月初旬に急上昇」と曖昧。検証可能なしきい値が必要か
5. **Phase 2 への接続** — Phase 1 の出力スキーマが Phase 2 バックテストに無理なく繋がるか

---

# コードレビュー: 015 TOBインサイダー疑い検出スクリーナー Phase 1 計画

- 日時: 2026-05-20 JST
- 対象: `docs/plans/analysis-015_tob_insider_screener_20260519_194557.md`（Phase 1 のみ） / `docs/knowledges/analysis/015_tob_insider_screener.md`
- パターン: 4（新規開発・設計計画のレビュー）
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: 全上場銘柄を日次スキャンし「低出来高・低ボラ横ばい → 出来高急増・上放れ」の初動を検出する新規スクリーナー `screen_tob_insider.py` の Phase 1 設計
- 品質評価: **C** — アイディアと指標の方向性は妥当だが、(1) 既存BQ資産（`STOCK_PRICE_JQUANTS`）の利用検討が抜けてデータ取得設計が再発明、(2) 新規依存 `pandas-ta` 導入が `pyproject.toml` 反映計画に無い、(3) 配置先ディレクトリ（`scripts/` 直下 vs `scripts/tob_prediction/`）の判断根拠が未定義、(4) スコア式 0乗算による「片足欠落=即0」の挙動が未検討、と複数の構造的欠落あり
- 主要リスク:
  1. データソース選定の再発明（J-Quants API 直叩きで全銘柄180日を1日分処理 ≒ 約30分。BQ `STOCK_PRICE_JQUANTS` 利用なら数十秒で済む）
  2. 発火スコアの乗算定義により「上抜けたが陰線」等の正常な初動パターンが score=0 で消える（ロジック欠陥）
  3. Smoke test 成功基準が定性的（「3月初旬に急上昇」）で再現性検証不能 — Phase 2 着手の go/no-go 判定が形骸化

## 【パターン4のみ: 新規計画評価】

### 技術選定の妥当性

**[#A] データソース再発明** — 計画 §作業ステップ Phase 1-1 と §必要データ では「J-Quants API で全上場銘柄の日次OHLCV（過去180日）取得」と明記しているが、`STOCK.STOCK_PRICE_JQUANTS` テーブルが既に日次更新（毎週月〜金 18:00 JST / 銘柄マスタクラスタ）で全銘柄分稼働している（`docs/data_catalog/bq_stock_price_jquants.md`）。BQ 経由なら以下の利点がある:
- 全銘柄180日分のデータ取得 = 1クエリで完結（クラスタリング: TICKER + パーティション: DATE → スキャン量数百MB級・数十秒）
- API レート制限 / ページネーション / 認証トークンリフレッシュの実装不要
- `ADJ_CLOSE` / `ADJ_VOLUME` 等の調整済み値（株式分割吸収）が即利用可能 ← **Bollinger Band / Donchian は調整済み価格でないと分割銘柄で偽シグナル**
- 既存スクリプト（`screen_earnings_cascade.py` 等）と同じパターンで `CLAUDE.md §3 BQオフロード原則` に整合

J-Quants 直接 API は `scripts/jquants_common.py` 経由で既存パターンが存在するが、**過去180日 × 約4000銘柄 = 約72万行を毎日取得するのは BQ 利用に劣る**。計画段階で代替案検討の記述がないこと自体が問題。

**[#B] pandas-ta 新規依存の未計画** — 計画 §アルゴリズム設計 §使用ライブラリ で `pandas-ta` を採用予定だが、`pyproject.toml` には未掲載（確認: 2026-05-20）。さらに `pandas-ta` は近年メンテナンス停滞気味（pandas 2.x への対応に既知バグあり）。Bollinger Band と ATR は数十行の自前実装で済む（既存の `scripts/zaraba_earnings.py` 等の rolling 演算と同じ規模）。
- 「既存依存だけで実装可能か」「pandas-ta 導入のメリットがコスト（依存追加 / 保守リスク）に見合うか」の判断記載なし

### 既存システムとの統合

**[#C] スクリプト配置先の不整合** — 計画 §成果物 では `scripts/screen_tob_insider.py`（scripts 直下）としているが、既存の `screen_tob.py` は `scripts/tob_prediction/` 配下。TOB 関連ツール群（007 系の `train_rf.py` / `run_backtest.py` / `screen_tob.py`）が同ディレクトリに集約されている中、本ツールだけを scripts 直下に置く合理性が不明。
- 命名の親類: `scripts/tob_prediction/screen_tob.py`（007 ファンダ）vs `scripts/screen_edinet_delay_tob.py`（008 EDINET）vs **新規 `scripts/screen_tob_insider.py`?**
- 推奨方向性: `scripts/tob_prediction/screen_tob_insider.py` または `scripts/tob_insider/screen.py` のいずれか。**Phase 3 で `screen_tob.py` とのスコア統合を予定している以上、同一ディレクトリ配置の方が import 関係がシンプル**

**[#D] 出力 CSV 名のバージョニング不在** — `data/output/tob_insider_screen_YYYYMMDD.csv` はパラメータが変わると過去出力と非互換になる。Phase 2 でキャリブレーション後に閾値変更すると、過去 CSV と互換性のないスコアが上書きされる。
- 比較: `scripts/tob_prediction/` は `predictions_YYYY.csv`（年単位）で、CSV 内に予測モデルバージョンを含めていない（同じ欠陥がある）。本ツールは Phase 2 でパラメータが流動するため、出力 CSV にパラメータハッシュまたはバージョン番号を含めるか、parquet/JSON でスコア定義をサイドカーするべき

**[#E] 実行タイミングと既存ジョブの関係** — 知見MD で「毎営業日 15:30 以降（当日終値確定後）」と運用方針があるが:
- `STOCK_PRICE_JQUANTS` の更新は毎週月〜金 18:00 JST（`bq_stock_price_jquants.md`）。**当日終値確定（15:30）から BQ 取り込み完了（18:00）の間は前日終値までのデータしか使えない**
- J-Quants API 直叩きなら 15:30〜16:00 で当日データ取得可能だが、本ツールの「初動検出」が当日中の発火を狙うのか翌営業日朝の発火を狙うのかが Phase 1 計画から判別できない
- 実行タイミングをいつにするか（15:30 / 18:30 / 翌朝）でデータソース選定（API 直 vs BQ）も変わるはず

**[#F] ticker マスタの取得経路が二系統混在** — 計画 §必要データ で「銘柄マスタ（ticker一覧）」を BQ `stock_code_list`（小文字）と書いている。実際の BQ テーブル名は `STOCK.STOCK_CODE_LIST`（大文字）。スキーマ参照は `docs/data_catalog/bq_stock_code_list.md`。実装時のミス誘発リスクあり。
- 既存スクリプト（`scripts/tob_prediction/screen_tob.py:124-128`）は `EXCHANGE = 'TSE'` でフィルタしているが、本計画には除外条件の記述なし → 上場廃止済み銘柄や地方単独上場（J-Quants 対象外）への対処が未定義

### リスク・コスト

**[#G] スコア式の数学的欠陥（コアロジック）** — §アルゴリズム設計 のスコア式:
```
発火スコア = clip(vol_ratio_20d / 5, 0, 1)
           × upper_bb_break          # 1 or 0
           × donchian_60d_break      # 1 or 0
           × candle_bullish_rate     # 陽線なら1.0
```
このフレームでは:
1. 当日陰線（出来高急増したが終値が始値割れ）→ candle_bullish_rate=0 → **発火スコア=0**。しかし「インサイダー的な仕込み」は陰線+大商いでも発生し得る（押し目買い吸収パターン）
2. Upper BB は突破したが Donchian 60日高値は未到達 → どちらか 0 → **発火スコア=0**。実質「両方同時 break」しか発火しない → 検出感度が極端に低い
3. 出来高比率 5倍未満（例: 3倍）+ BB break + Donchian break + 陽線 → score = 0.6 × 1 × 1 × 1.0 = 0.6 という値になるが、**逆に出来高 6倍 + BB break のみ（Donchian 未達）= 0** という不連続性

→ 既存 TOB 予測（007）はファンダメンタル和（重み付き加算）方式、008 はルールベース加点。**本計画だけが乗算式 × 二値フラグ多用で「ほぼ全銘柄スコア0、極一部だけ高スコア」の二極分布**になる。8141 新光商事の3月初旬パターンが本式で実際に高スコアを出せる根拠（過去データでの試算）が計画段階で示されていない

→ 推奨方向性 **[方向性]**: 二値フラグを連続値化（例: `donchian_score = (close - max_60d) / atr` のような距離正規化）、または乗算でなく加算+正規化に切り替える。8141 で実際にスコア時系列を1ケース手計算し、計画段階で「3月初旬に高スコアが出ること」を**机上検証**してから実装に入るべき

**[#H] 静止スコアの dormancy_days^0.5 の効き** — `(dormant_days / 120)^0.5` は dormant=60日なら sqrt(0.5)=0.707、dormant=120日なら 1.0。dormant=0日（直前まで活発）でも 0 にはならず計算は通るが、他項（BBwidth_pct, vol_pct）が極小なので結局静止スコアは限りなく 0 に近づく。**ただし dormant_days を「BBwidth下位20%ile 連続日数」と定義しているため、新規上場銘柄や120日未満のデータ銘柄では分母120 が常に大きく不利**。データ不足銘柄のスキップ方針が未記述。

**[#I] 8141 新光商事の事例検証の欠如** — 計画の動機・smoke test 基準は全て 8141 を起点としているが:
- 8141 は東証スタンダード、時価総額数百億円規模の銘柄。本ツールが全市場4000銘柄を対象とするなら、「同じパラメータで他のTOB銘柄でも検出できるか」の事前検証が必要
- §動機 では 8141 の3月初旬+65% を「典型的な情報漏洩パターン」としているが、+65% は既にかなりの動き（初動を取り損ねた段階）。「初動」の定義が「いつ気付けば良いか」（例: +5%段階 / +15%段階）と整合していない

**[#J] 偽陽性経路の網羅性不足** — 知見MD §既知の制約 で「決算・増配等のファンダ材料」のみ言及。実際に発火しがちな経路は他にもある:
- 株式分割発表 → 出来高急増（既に `ADJ_VOLUME` 使えば吸収できるが、計画では生 VOLUME / ADJ_VOLUME のどちらを使うか未定義）
- 適時開示（業績修正・自社株買い・配当増額）
- 業界全体の急騰（半導体・防衛等のセクター連動）
- 急騰銘柄ランキング上位入りによる順張りアルゴ起因の出来高
- 仕手系・低位株の循環物色

これらを発火しないように「TOB予兆」を選り分けるロジックが計画段階で見えない。偽陽性率の評価指標も Phase 1 完了条件に含まれていない（Phase 2 のキャリブレーションで対処と読めるが、Phase 1 で「ノイズ多すぎて Phase 2 検証コスト過大」となるリスク）

### 抜け漏れ

**[#K] 完了条件の検証可能性** — §完了条件 「8141 新光商事の初動スコアが『3月初旬に急上昇している』ことを過去データで確認できる」は曖昧:
- 「3月初旬」の定義（3/1-3/10 / 3/1-3/15 / 3月第1週 ?）
- 「急上昇」の定義（前日比+50% / 過去20日比 top1% / 絶対値 0.X 超え?）
- **完了条件側で具体的数値しきい値を与えない限り、go/no-go 判定が「ぱっと見でそれっぽい」になり、Phase 2 に進んでから「実は smoke 通ってなかった」が起きる**

[方向性]: smoke 基準を例えば「2026-03-01〜03-15 の任意の1日で、8141 の初動スコアが 0.5 を超える」「同期間内に8141 が全銘柄スコア降順 Top30 に1日でも入る」のような検証可能条件に置き換える

**[#L] Phase 2 接続点の前方互換性** — 計画 §作業ステップ Phase 2 Step 7 で `STOCK.DELISTED_STOCKS_TOB_ENHANCE.IR_FIRST_RELEASE_DATE` と「TICKER, IR_FIRST_RELEASE_DATE」だけ使う前提だが:
- Phase 1 の CSV 出力カラムが日付別個別ファイル `tob_insider_screen_YYYYMMDD.csv` の単日スナップショット形式である。Phase 2 のバックテストは「過去 TOB 銘柄について、TOB発表のN日前の初動スコア」を引き当てる必要があるため、**過去全営業日 × 全銘柄のスコア時系列が必要**。日次 CSV を後から concat する設計だと、Phase 2 で過去5年分を再生成するコストが大きい
- Phase 1 段階で**過去180日分の全銘柄スコア時系列を BQ テーブルに保存する**設計の方が Phase 2 接続がスムーズ（だがそれは Phase 1 のスコープを膨らませる）。**Phase 1 で「単日CSV出力」と「過去時系列の再生成可能性」のどちらを優先するか方針を明示すべき**

**[#M] Cloud Run Job / スケジューラ計画の不在** — 知見MD §運用 では「毎営業日 15:30以降」と書いているが、計画書には Cloud Run Job 化 / Cloud Scheduler 化の作業ステップが**完全に欠落**。日次スクリーニングは Cloud Run Job 化が標準（既存 `edinet-delay` 等）。Phase 1 完了条件にこれが含まれないなら、「ローカル実行のみで完了とする」「Phase 3 で Cloud Run 化」のどちらか明示すべき。

**[#N] CLAUDE.md コーディング規約への明示参照欠如** — 計画書には `docs/knowledges/tools/004_coding_conventions.md` への言及がない。新規スクリプト追加時の必須チェック（型ヒント / docstring / structlog / JST 統一 / errors カウンタ + sys.exit(1) / TICKER は str 型）が計画段階で組み込まれていない。`screen_edinet_delay_tob.py:81-88` のような GCP 認証パターン、`screen_tob.py:56-65` の credentials_path チェックなどの既存ボイラープレートを踏襲する旨を明記すべき。

**[#O] 知見MD（015）の Phase 1 スタブが既にスコア定数を確定** — `015_tob_insider_screener.md §パラメータ（初期値）` で `bb_pct_threshold=0.20` 等の初期値表が掲載済みだが、計画では Phase 2 でキャリブレーションする予定。**Phase 1 完了時点でこれらの値が試行錯誤の結果として固まっているか、暫定値かが知見MDから判別できない**。「Phase 2 後に確定」のような注記が必要

### 目的・スコープの明確性

- 計画書 §目的 は1段落で目的が言い切れている（OK）
- ただし「TOB公表前に情報漏洩的な動きをしている銘柄を事前検出することが最終ゴール」と「全上場銘柄から日次スクリーニング」が並列に書かれており、**直接の出力ゴール（=スクリーナーCSV）と最終ビジネスゴール（=TOB候補選別）の区別がやや曖昧**
- 非スコープが §Phase 3 として明示されているのは良い（screen_tob.py との統合・ML 化）

### 段階的検証計画

- Phase 1 §6 で smoke test は設計されているが、`devtest`（複数銘柄での網羅性確認）→ `dev`（全銘柄スクリーニングの完走確認）→ `prod`（Cloud Run Job 化 + 監視）の各段階に分かれていない
- 計画 §作業ステップ Phase 1-5 で「全銘柄スコアリング → 上位N件 CSV 出力」とあるが、「N=何件」「上位N件のうち何件が手動レビューで妥当か」の検証手順なし

### データカタログ整合

- `STOCK.STOCK_PRICE_JQUANTS` を本計画では使う想定がない（J-Quants API 直叩き予定）が、**実際は使うべき**（観点 [#A]）。data_catalog 不整合の典型
- `data/cache/tob_insider_screener/` は新規キャッシュディレクトリ。`data/csv/` 配下のキャッシュ規約（CLAUDE.md §3）との関係が未定義（CLAUDE.md §3 では `data/cache/` は外部API キャッシュ用、`data/csv/` は BQ/GCS コピー用）

## 【重大な指摘】（即修正）

### #1 データソース選定の再発明 — BQ既存テーブルが未検討
- 箇所: 計画 §作業ステップ Phase 1-1 / §必要データ
- 事象: `STOCK_PRICE_JQUANTS`（既に日次更新中、調整済み列あり）を無視して J-Quants API 直叩きを前提化
- トリガー: 計画通り実装するとAPI 認証・レート制限・180日ページネーション処理を新規実装する必要があり、3〜4時間見積もりの大半を消費する
- 影響: 開発コスト膨張、調整済み価格（分割対応）の未考慮による偽シグナル、Phase 2 で過去5年分時系列が必要になった時の再取得負荷
- 根拠: `docs/data_catalog/bq_stock_price_jquants.md`（全銘柄日次・ADJ_CLOSE/ADJ_VOLUME 列あり・パーティション+クラスタ最適化済）。既存 `scripts/screen_earnings_cascade.py` 等は BQ 経由で同種データを取得
- 推奨対応 **[検証済み]**: §必要データ表の「日次OHLCV」ストレージを (a) BQ に変更し、Step 1 を「BQ `STOCK_PRICE_JQUANTS` から過去180日分の全銘柄 OHLCV を ADJ_* 列込みで取得（1クエリ・キャッシュは `C:\tmp\tob_insider_screener\`）」に書き換える。J-Quants API 直叩きは「当日終値直後（15:30〜18:00の谷間）」が要件になった場合のフォールバックとして残す。波及: 計画 §必要データ表 / Phase 1-1 / 知見MD §既知の制約

### #2 発火スコアの乗算定義による検出不能パターン
- 箇所: 計画 §アルゴリズム設計 §コアコンセプト L34-46
- 事象: 発火スコア = `vol_ratio × upper_bb_break × donchian_60d_break × candle_bullish_rate`（二値フラグ複数の積）により、いずれか1つが0で全体0
- トリガー: 「Donchian 60日高値は1円届かなかったが BB Upper を抜けて出来高3倍・陽線」のような典型的初動 → score=0 で検出失敗
- 影響: 8141 のような実例でも、各日が「両フラグ同時 break」を満たさない限り検出されない。検出感度が極端に低くなる
- 根拠: 計画 §アルゴリズム設計の式そのもの。8141 で実際に score 時系列を試算した結果が計画書・知見MD のいずれにも記載なし
- 推奨対応 **[方向性]**: ①二値フラグを連続値化（距離 / ATR 正規化）、②または OR 合成（重み付き和の clip）に変更、③`bullish_rate` を陰線で 0.5（中立）にする等の最小修正。**実装前に 8141 の3月初旬データで手計算して「3月初旬の任意日で score≥X が成立する」ことを机上検証**することを Phase 1 開始前の必須タスクとして追加する

### #3 完了条件が定性的で go/no-go 判定不能
- 箇所: 計画 §完了条件 L156
- 事象: Phase 1 完了条件「8141 の初動スコアが『3月初旬に急上昇している』ことを過去データで確認できる」が定性的
- トリガー: 実装後に「それなりに上がってるように見える」で完了マーキング → Phase 2 着手後に「実は smoke test 不十分」が露呈
- 影響: Phase 2 バックテスト（過去TOB銘柄での検出率 50% 目標）が形骸化し、本ツールの実用性検証が遅れる
- 根拠: 「3月初旬」「急上昇」共に閾値定義なし
- 推奨対応 **[方向性]**: 例: 「2026-03-01〜03-15 の各営業日における 8141 の初動スコアが、(a) 当該15営業日中の少なくとも3営業日で 0.5 以上、(b) 同期間内に全銘柄スコア降順 Top50 に少なくとも1日入る」のような検証可能基準に置き換える。具体値はパラメータ未確定段階での目安としてユーザー合意必要

### #4 pandas-ta 新規依存の計画外導入
- 箇所: 計画 §使用指標表 L51-58 / 知見MD §使用ライブラリ
- 事象: `pandas-ta` を使う前提だが、`pyproject.toml` 反映が計画書・知見MDのいずれにも記載なし。`uv add pandas-ta` の作業ステップ欠落
- トリガー: 実装着手して `import pandas_ta` で ModuleNotFoundError → 急遽 uv add するが、依存先で別の不具合（pandas 2.x 互換問題）が出る可能性
- 影響: 着手段階の手戻り。CLAUDE.md §6 「uv で管理」原則違反のリスク
- 根拠: `pyproject.toml`（確認: 2026-05-20）に `pandas-ta` 未掲載。`scripts/` 配下に既存利用例なし
- 推奨対応 **[方向性]**: ①計画 §作業ステップ Phase 1-1 に「依存追加: `uv add pandas-ta`」を明示する、または ②**Bollinger Band / ATR を自前実装に切り替える**（数式は数行）。pandas-ta の代替判断（採否）を計画段階で明示

### #5 スクリプト配置先の不整合
- 箇所: 計画 §成果物 L148 / 知見MD §スクリプト L41
- 事象: `scripts/screen_tob_insider.py`（scripts 直下）配置だが、既存 007 系の `screen_tob.py` は `scripts/tob_prediction/` 配下
- トリガー: Phase 3 で `screen_tob.py` とのスコア統合実装時に、import path が `scripts.screen_tob_insider` ⇔ `scripts.tob_prediction.screen_tob` で非対称になる
- 影響: TOB 関連ツール群の発見性低下。`tob_prediction/` か `screen_*.py` 直置きかの判断基準が不明確のまま新規パターン乱立
- 根拠: 既存ファイル配置（screen_tob.py = `tob_prediction/` / screen_edinet_delay_tob.py = `scripts/` 直下 / screen_earnings_cascade.py = `scripts/` 直下）
- 推奨対応 **[方向性]**: `scripts/tob_prediction/screen_tob_insider.py` を推奨（Phase 3 統合の見通しと、関連ツールのクラスタ化のため）。または `scripts/tob_insider/` という新規ディレクトリで切り出す。**いずれにせよ計画段階で配置理由を1行コメント**で残す

### #6 「初動」検出と実行タイミングの不整合
- 箇所: 知見MD §運用 L68 / 計画 §作業ステップ全体
- 事象: 「毎営業日 15:30以降（当日終値確定後）」と運用記載だが、BQ `STOCK_PRICE_JQUANTS` の更新は 18:00 JST 以降。15:30 時点で使えるのは前日終値まで
- トリガー: 計画通り 15:30 実行しても、データは前日終値で計算するため**当日の急変は1日遅れで検出される**
- 影響: 「初動を捕捉する」ツールが実質1日遅延スクリーナーになる。ユーザー期待とのギャップ
- 根拠: `docs/data_catalog/bq_stock_price_jquants.md` の「毎週月〜金 18:00 JST」スケジュール
- 推奨対応 **[方向性]**: ①実行タイミングを 18:30〜19:00 に変更（BQ 取り込み完了後）、または ②15:30 実行を維持する場合は J-Quants `/prices/daily_quotes` を直接叩く（既存パターン: `scripts/jquants_common.py`）。**どちらを採るか計画段階で決定し、§運用の文言を一致させる**

### #7 過去時系列の保存設計が Phase 2 と不整合
- 箇所: 計画 §作業ステップ Phase 1-5 L92 / Phase 2-7 L116-119
- 事象: Phase 1 は日次 CSV `tob_insider_screen_YYYYMMDD.csv` 単発スナップショット出力。Phase 2 では「発表N日前の初動スコアを全TOB銘柄で集計」が必要で、過去全営業日 × 全銘柄のスコア時系列が前提
- トリガー: Phase 2 着手時に Phase 1 の出力では検証データが足りず、過去5年分の全銘柄スコアを再生成する必要が出る
- 影響: Phase 2 着手前に Phase 1 を再実行（既存出力CSV破棄/拡張）するコスト発生
- 根拠: 計画 §作業ステップの両 Phase の出力形式の非対称
- 推奨対応 **[方向性]**: Phase 1 段階で出力を ①日次 CSV と ②BQ テーブル（または parquet 累積ファイル）の両方にすることを明示。スキーマ案: `DATE, TICKER, dormancy_score, ignition_score, total_score, bb_width_pct_120d, vol_pct_120d, vol_ratio_20d, ...`。**Phase 2 仕様を Phase 1 出力スキーマに織り込んでおく**

### #8 偽陽性除外ロジックが Phase 1 完了条件に含まれない
- 箇所: 計画 §作業ステップ Phase 1 全体 / §完了条件
- 事象: 出来高急増は決算・株式分割・適時開示・セクター連動・自社株買い・配当変更・仕手等で発生するが、Phase 1 計画ではこれらを除外するロジックが含まれない
- トリガー: 実装後に「Top30 のうち過半数が決算翌日銘柄」「Top30 のうち多くが半導体セクター連動」のような事態
- 影響: Phase 2 着手後にバックテストで偽陽性率が異常に高く、キャリブレーションでカバーしきれない → 「Phase 1 設計に立ち戻る」手戻り
- 根拠: 知見MD §既知の制約 L77 で「決算・増配等のファンダ材料でも発火」とは認知しているが対処は記述なし
- 推奨対応 **[方向性]**: Phase 1 §作業ステップに「ノイズ除外フィルタ」ステップを追加: ①当日決算開示銘柄を `EARNINGS_DISCLOSURE_CALENDAR` で除外、②直近3営業日内に TDnet 適時開示があった銘柄をフラグ、③`ADJ_FACTOR ≠ 1.0` の分割影響日を除外。または、出力 CSV に各除外フラグ列を含めて事後フィルタ可能にする

## 【改善提案】（可読性・保守性）

### #1 計画書 §背景・動機 と §完了条件 の8141 起点バイアス
- 箇所: 計画 §背景・動機 L19-23 / §完了条件 L156
- 現状: 動機・smoke test・完了条件すべてが 8141 新光商事 1 銘柄起点。「8141 で動く=ツール完成」というバイアスを誘発
- 提案: 8141 以外に過去6ヶ月の TOB 銘柄（例: `DELISTED_STOCKS_TOB_ENHANCE` から `IR_FIRST_RELEASE_DATE >= 2025-11-19` を抽出）から複数銘柄を smoke 対象に追加。「対象3〜5銘柄のうち2銘柄以上で発表30日前にスコア上昇」のような複数事例ベースの基準に変更

### #2 知見MD §パラメータ表に「Phase 2 後に確定」注記が不在
- 箇所: 知見MD §パラメータ（初期値） L46-54
- 現状: パラメータ表は掲載済みだが、Phase 1 完了時点で確定値か暫定値かが不明
- 提案: 表の上に「※ Phase 1 完了時点では暫定。Phase 2 キャリブレーション後に確定値を上書きする」を1行追加

### #3 計画書 §使用指標表 の自前実装範囲が曖昧
- 箇所: 計画 §使用指標表 L51-58
- 現状: Bollinger Band / ATR は `pandas-ta`、Donchian / Darvas Box は「自前実装」。`pandas-ta` 不採用にする場合、全て自前実装になる
- 提案: ライブラリ列を「実装方針」に変更し、`pandas` の rolling/std で4指標すべて自前実装可能なことを明示。pandas-ta 採否の判断を1行で書く

### #4 計画書 §見積もり の根拠記載不足
- 箇所: 計画 §見積もり L162-166
- 現状: 「Phase 1 想定所要時間: 3〜4時間」とだけ記載。データ取得設計を BQ に変更すると2時間程度に短縮できる可能性
- 提案: 内訳（データ取得設計 / 指標計算 / 静止スコア / 発火スコア / smoke / 知見MD更新）の時間配分を1行ずつ書く

### #5 CLAUDE.md コーディング規約への明示参照
- 箇所: 計画書全体
- 現状: `004_coding_conventions.md` への参照なし
- 提案: §作業ステップ Phase 1 の前文に「実装は `docs/knowledges/tools/004_coding_conventions.md` の規約（型ヒント / docstring / structlog / JST / errors+sys.exit(1) / TICKER は str）に準拠」を1行追加

## 【修正例】（必要な箇所のみ）

#### #1 に対する修正案（計画 §必要データ）

```markdown
# before:
| 全上場銘柄 日次OHLCV | (d) J-Quants API | `data/cache/tob_insider_screener/` |

# after:
| 全上場銘柄 日次OHLCV | (a) BQ | `STOCK.STOCK_PRICE_JQUANTS`（過去180日・ADJ_*列込み・1クエリで全銘柄取得。キャッシュ: `C:\tmp\tob_insider_screener\ohlcv_180d.csv`） |
```

#### #2 に対する修正案（計画 §アルゴリズム設計）

```markdown
# before:
発火スコア（0〜1）
  = clip(vol_ratio_20d / 5, 0, 1)
  × upper_bb_break                   # Upper BB突破: 1 or 0
  × donchian_60d_break               # 60日高値超え: 1 or 0
  × candle_bullish_rate              # 陽線割合（当日陽線なら1.0）

# after（案A: 重み付き和に変更）:
発火スコア = clip(
    0.4 × clip(vol_ratio_20d / 5, 0, 1)
  + 0.3 × (上抜け距離 / ATR_20d を [0,1] にclip)
  + 0.2 × (close - max_60d) / atr_20d を [0,1] にclip
  + 0.1 × max(0, (close - open) / open / 0.03)  # +3%陽線で満点
, 0, 1)

# after（案B: 二値フラグを保ちつつ OR 化）:
発火スコア = (
    0.5 × clip(vol_ratio_20d / 5, 0, 1)
  + 0.2 × upper_bb_break
  + 0.2 × donchian_60d_break
  + 0.1 × bullish_flag
)
```

## 【確認できなかった事項】

- 8141 新光商事の3月初旬の日次 OHLCV を実際に取得して 計画式で score を試算してはいない（コードレビュアーは Python 実行禁止のため）。**[#2] の検証必要性指摘は「机上の数式定義から導かれる構造的欠陥」に基づく**。実データ試算結果次第で結論が補強される
- `pandas-ta` の pandas 2.x 互換性問題の最新状況は本レビュー時点では未確認。**[#4] の保守リスク言及は一般論レベル**。具体的なバグレポートに基づくものではない
- Cloud Run Job 化を含めて Phase 1 完了とするかどうかはユーザー意図次第。本レビューは「ローカル実行のみ」を Phase 1 完了として暗黙的に許容しているが、計画書からは断定できなかった（**[#M]**）
- Codex 成果物 `tob_announcement_dates_for_claude_20260519_200803.csv` の中身（475件の TICKER と IR_FIRST_RELEASE_DATE の分布）は確認していない。Phase 2 検証用データとして十分かは別途検証必要


---

## 返却 2026-05-20

- #A: [採用] BQ STOCK_PRICE_JQUANTS に切り替え
- #B: [採用] pandas-ta 導入前に評価・pyproject.toml 反映を明記
- #C: [採用] scripts/tob_prediction/ 配下に変更
- #D: [採用] 出力CSVにバージョン/パラメータ情報追記を考慮
- #E: [採用] 実行タイミングを 18:30以降（BQ更新後）に修正
- #F: [採用] テーブル名を STOCK.STOCK_CODE_LIST（大文字）に修正、TSEフィルタ明記
- #G: [採用] 発火スコアを加重和方式（連続値）に設計変更
- #H: [採用] データ不足銘柄スキップ方針を追記
- #I: [採用] 8141以外の過去TOB銘柄でのクロスチェックを smoke test に組み込む
- #J: [採用] 偽陽性経路（分割・業績修正・セクター連動等）を制約・注意事項に追記
- #K: [採用] 完了条件を定量化（Top50入り or スコア 0.3超）
- #L: [採用] Phase 2接続のためのスコア時系列再生成方針を追記
- #M: [採用] Cloud Run化はPhase 3スコープとして明示
- #N: [採用] 004_coding_conventions.md 参照を作業ステップに追記
- #O: [採用] 知見MDパラメータ表に「暫定値・Phase 2でキャリブレーション」注記追加
