# 裁量トレーディングツール索引

スクリーナー・ザラバ補助・シグナル等、裁量トレード向けツールの一覧。
スクリプト追加・知見MD作成時は本索引を更新すること（004 §新規スクリプト作成時 参照）。

## 残TODO

NO12　サプライチェーンマスタ、連鎖マスタのクリーニング 偽陽性キーワード LLMパワーで除外　| 年次で更新ルールとファイル名ルール | バックテスト

## スクリーナー（10番台）

| # | ツール名 | 用途 | スクリプト | 知見MD |
|---|----------|------|-----------|--------|
| 11 | EDINET遅延TOB | 大量保有報告書の遅延提出からTOB/MBO候補をスコアリング | `scripts/screen_edinet_delay_tob.py` | `analysis/008_edinet_delay_tob_screening.md` |
| 12 | サプライチェーン連鎖(★使える) | サプライチェーン先行好決算→後攻決算またぎ候補抽出 | `scripts/screen_earnings_cascade.py` | `analysis/013_supply_chain_earnings_cascade.md` |
| 13 | 清原式 | ネットキャッシュ控除後の実質PER全銘柄スキャン | `scripts/kiyohara_screening.py` | — |
| 14 | FY弱気ガイダンス | 弱気初期ガイダンス→売られ→実績上振れの繰返しパターン検出 | `scripts/fy_conservative_guidance_screener.py` | `analysis/014_fy_conservative_guidance_screener.md` |
| 15 | アクティビストスキャン | EDINET大量保有報告書からアクティビストファンド保有を検出 | `scripts/activist_edinet_scan.py` | — |
| 16 | TOB ML予測 | ML特徴量でTOB確率を予測しランキング | `scripts/screen_tob.py` | `analysis/007_tob_ml_prediction.md` |
| 17 | VCP日次スクリーナー | Minervini VCP で Stage2 収縮→ブレイク候補を毎日抽出（Cloud Run 19:00 JST） | `scripts/tob_prediction/vcp_daily_cloud.py` | `analysis/016_vcp_insider_daily.md` |
| 18 | TOB価格パターン ML | LightGBM で TOBターゲット株を日次予測（lift@5%=1.73 / 部分採用） | `scripts/tob_prediction/predict_tob_ml.py` | `analysis/017_tob_price_pattern_ml.md` |

## ザラバツール（20番台）

| # | ツール名 | 用途 | スクリプト | 知見MD |
|---|----------|------|-----------|--------|
| 21 | ザラバ決算リアクション | prepare/watch/catchup/review。TDnetポーリング+XBRL抽出+スコアリング | `scripts/zaraba_earnings.py` | `tools/066_zaraba_tool.md` |
| 22 | 決算未発表一覧 | 当日予定 vs TDnet実績を突合し未開示銘柄を表示 | `scripts/menu_earnings_undisclosed.py` | `tools/100_earnings_undisclosed.md` |

## シグナル（30番台）

| # | ツール名 | 用途 | スクリプト | 知見MD |
|---|----------|------|-----------|--------|
| 31 | 米日セクターリードラグ日次シグナル | 米国ETF終値→日本セクターETFロング/ショート確定 | `scripts/signal_011_4_daily.py` | `analysis/011-4_us_japan_sector_leadlag.md` |
| 32 | テールリスクシグナル | SKEW×VIX×Fear&Greed直近10日チェック | `scripts/menu_signal_check.py` | `analysis/003_skew_vix_fg_tail_risk.md` |

## 深掘り分析（40番台）

| # | ツール名 | 用途 | スクリプト | 知見MD |
|---|----------|------|-----------|--------|
| 41 | キーワードベクトル類似検索(★使える) | TDNET CHUNK_TEXTキーワード共起+ベクトル類似検索 | `scripts/093_deep_analysis_screener.py` | `analysis/093_earnings_deep_analysis.md` |
| 42 | セグメント転換検知(★使える) | テーマ出現頻度の時系列変化でセグメント転換を早期発見 | `scripts/093_deep_analysis_transform_scanner.py` | `analysis/093_earnings_deep_analysis.md` |

## データ収集（50番台）

| # | ツール名 | 用途 | スクリプト | 知見MD |
|---|----------|------|-----------|--------|
| 51 | TOB大株主取得 | EDINET有報から上場廃止企業の大株主を取得しアクティビスト判定 | `scripts/fetch_tob_shareholders.py` | `analysis/007_tob_ml_prediction.md` |
| 52 | TOB届出書取得 | EDINET公開買付届出書をスクレイピングしBQ拡張 | `scripts/fetch_tob_announcements.py` | `analysis/007_tob_ml_prediction.md` |
