# ザラ場決算リアクションツール（ザラ場ツール）

**カテゴリ**: tools
**作成日**: 2026-04-05
**ステータス**: 有効（watch/catchup とも TDnet HTML ポーリング + XBRL 抽出に移行完了。2026-04-23 予想 context 限定化完了）

## 🔴 未解決 TODO（先頭掲示）

- **P1: コンセンサス比較が全銘柄で不発**: `obs_guidance_vs_consensus` が全87行空。今期OP実績 vs 今期OPコンセの比較が未実装。v3スキーマ対応（2026-05-05）でデータ構造は整備済み、ロジック接続が未実施（起源: 4/28反省会）
- **P2: 強弱混在銘柄の「危険」フラグ**: POS因子とNEG因子が拮抗している銘柄を NEUTRAL ではなく「MIXED/危険」として明示摘出。方向感なしでポジション取ると大けがするリスクの警告用（起源: 4/28反省会）
- ~~**P2: TMP パス Linux 実装の検証要**~~: 解決済み（2026-05-12）。poller を `~/zaraba_cache` に統一。backup コマンドで xbrl キャッシュも保管対象
- **P2: config YAML 切り出し**: スコアリングパラメータ（閾値・ウェイト）を config YAML に分離。predict notebook から自動反映可能にする
- **P2: TDnet 並行ポーリング**: 自社株買い・株式分割・優待変更・**中期経営計画評価**のリアルタイム検知 → Gemini Flash で解析（中計追加: 5/15反省会、8157都築電気 中計上方修正+配当性向引上げが未検知）
- **P2: 自社株買い過去パターン分析**: 常習 vs 初回サプライズ判定。TDnet 過去データ蓄積が前提（スケール判定は F10 スケール化で実装済み、残りは初回サプライズ判定）
- ~~**P1: F7折込ウェイト段階化**~~: 実装済み（2026-05-15）。+10〜20%→-1、+20〜30%→-2、+30%超→-3
- ~~**P1: 因子基準値の表示強化**~~: 実装済み（2026-05-15）。F4/F4c/F4n/F4np/F6/F15に基準値(百万円)併記
- ~~**P1: is_low_profit移植**~~: 実装済み（2026-05-15）。F3/F4(±2→±1)/F7g/F13を抑制。閾値: 5年中央値OP < 5億
- **P2: 個人投資家関心度マーキング**: 旧F9テーマブースト（β×TOPIX+）は廃止。βでは個人投資家関心度を代理できなかった。別指標（出来高急増・信用買残変化・SNS言及数等）を検討
- **P3: 四半期受注ツール連携**: 開発中の四半期受注ツールと連携。受注高増減を因子化。089側にも双方向TODO記載済み（起源: 5/15反省会、6376日機装）→ `089_quarterly_disclosure_master.md` §未定事項

**関連ファイル**:
- `scripts/zaraba_earnings.py`
- `zara.py`（クロスプラットフォーム対話ランチャー。Linux/Windows共通）
- `zara.sh`（Linux 一発起動ラッパー。`uv run python zara.py` を呼ぶ。`~/.local/bin/zara.sh` は本ファイルへの symlink）
- `C:\Users\zonekun\Dropbox\stock\script\claude-investment-agent.ps1`（Windows専用メニュー。CLI引数変更時は同期必須。詳細は066-2 §PSメニュー依存パッケージチェック）
- `docs/plans/20260405_zaraba_tool.md`（設計ドラフト）
- **計画**: `docs/plans/tools-066_zaraba_consensus_quarter_match_20260430_153500.md`（F4c コンセ乖離の四半期/FY 不一致バグ修正、2026-04-30 1878 +320.5% 誤検出契機）
- **計画**: `docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md`（Q列追加 + GCSアップ/表示 + earnings_modelフォルダリネーム）
- **計画**: `docs/plans/tools-066_zaraba_tool_20260513_005500.md`（F10 自社株買いスケール化 — PDF解析 + ToSTNeT-3判定 + ウェイト段階化）
- **計画**: `docs/plans/tools-066_zaraba_tool_20260515_193100.md`（知見MD分離 — トークン費消削減）
- `docs/knowledges/tools/066-1_zaraba_retrospective.md`（反省会ログ・運用手順・書き込みルール）
- `docs/knowledges/tools/066-2_zaraba_detail.md`（詳細リファレンス: データソース・キャッシュ・catchupフロー・教訓等）
- `docs/knowledges/tools/059_earnings_model_eda.md`（Phase 2 として位置づけ）
- `docs/knowledges/tools/071_xbrl_to_jquants.md`（**XBRL勘定科目マッピングの本体**。TAG_CANDIDATES定義・検証結果・アダプター設計・営業収入合算ロジック・予想context仕様はこちらで管理。`zaraba_tdnet_poller.py` の `TDNET_TAG_MAP` もこのプロジェクトが保守する）
- `docs/knowledges/tools/099_xbrl_lookup.md`（**XBRL四半期推移ツール**。`zaraba_tdnet_poller.py` を共有。ポーラー/抽出ロジック変更時は両方確認必須）

## 概要

事前準備を BQ で行い、ザラバ中の決算発表を TDnet 適時開示ポーリング + XBRL 数値抽出でリアルタイム検知し、期待値との乖離をスコアリングして買い/売り候補を rich Live で表示する裁量トレード支援ツール。自動トレードは行わない。watch 起動時に全データをメモリ展開しディスクI/Oゼロでスコアリング。

## サブコマンド

| コマンド | データソース | 用途 |
|---------|------------|------|
| `prepare --date YYYYMMDD [--force]` | BQ | 事前準備。BQ から銘柄情報を一括取得してキャッシュ |
| `catchup --date YYYYMMDD --until HH:MM` | TDnet HTML + XBRL | 指定時刻までの決算短信を TDnet から取得し、XBRL 抽出 & スコアリング → results.csv 追記 |
| `watch --date YYYYMMDD` | TDnet HTML + XBRL | ザラバ監視。TDnet ポーリング + XBRL 抽出 + スコアリング + rich Live 表示。起動直後に対話プロンプトで指定時間 HHMM (4桁、例 `1100`) と時価総額フィルタ（億円）を入力する。時価総額フィルタ: `500`=500億以下、`+500` or `>500`=500億以上、無入力=全社。指定時間±15秒は 0.05秒間隔、+15〜+60秒は 0.2秒間隔、それ以外は 1.0秒間隔の動的ポーリング |
| `review --date YYYYMMDD` | ローカル CSV | 過去 watch/catchup 結果（results.csv）を時系列で表形式表示。時価総額フィルタ対応（gcs-review と同仕様） |
| `backup` | ローカル | キャッシュ全体を zip でスナップショット保管（バグ調査用）。`backup_*.zip` は除外 |

> **サブコマンド間整合性**: watch と catchup は同じ TDnet ポーラー + XBRL 抽出 + スコアリングパスを共有する。データソースやスコアリングロジックを変更した場合、**両サブコマンドで整合性を確認すること**（教訓: catchup が J-Quants のまま放置され0件返却した事故あり）。
>
> **サブコマンド追加・改修時は4箇所を同時更新**:
> 1. `scripts/zaraba_earnings.py`（CLI argparse）
> 2. `zara.py`（クロスプラットフォームランチャー）
> 3. `claude-investment-agent.ps1`（PS1 サブメニュー + switch ハンドラ）
> 4. 本MD + `023_powershell_menu.md`（サブコマンド表）

## 使い方

```bash
# 1. 事前準備（前日夜 or 当日朝）
PYTHONUTF8=1 python scripts/zaraba_earnings.py prepare --date 20260407

# 2. 監視開始
PYTHONUTF8=1 python scripts/zaraba_earnings.py watch --date 20260407

# 3. 中断後に別の時間帯を監視（11:00→14:00 等）
PYTHONUTF8=1 python scripts/zaraba_earnings.py catchup --date 20260407 --until 13:50
PYTHONUTF8=1 python scripts/zaraba_earnings.py watch --date 20260407
```

## データソース・キャッシュ・内部仕様

→ `docs/knowledges/tools/066-2_zaraba_detail.md`（BQテーブル一覧・コンセンサスキャッシュ・prior_data.json構造・累計→Q変換・キャッシュ構造・GCSパス・catchupフロー・XBRL予想context・ランチャー・PSメニュー依存パッケージ・教訓等）

## スコアリング因子

| # | 因子 | ウェイト | 計算 | 対象Q |
|---|------|---------|------|-------|
| F1 | 進捗率サプライズ | ±1 | 累計OP÷通期予想の進捗率 vs 期待進捗率(25%/50%/75%/100%)。乖離±20%超で発火 | 1Q/2Q/3Q |
| F2 | ガイダンス修正 | ±1 | 今回 FOP vs 前回予想。±5%超で発火 | 全Q |
| F3 | YoY 営業利益 | ±1 | Q単独OP vs 前年同期Q単独OP。±30%超で発火（EDA側で廃止候補、F13で代替予定） | 全Q |
| F4 | 翌期見通し（FY のみ） | ±2 | 翌期予想ODP vs 今期実績ODP（経常利益ベース、ODP不在時はOPフォールバック）。±10%超で発火 | FYのみ |
| F4c | コンセンサス乖離 | +1〜+3 / -1〜-5 | 実績ODP vs コンセンサスORD_PROFIT（全Q経常利益ベース）。>+10%→+3、>+5%→+2、>0→+1、<-30%→-5、<-20%→-4、<-10%→-3、<-5%→-2、<0→-1 | 全Q |
| F4n | 翌期コンセンサス乖離 | +1〜+3 / -1〜-5 | 翌期会社予想NP vs 翌期コンセNET_PROFIT（純利益ベース）。ウェイトはF4cと同一。表示: `翌コ純` | FYのみ |
| F5 | 出尽くしリスク（3Q） | -2 | 3Q累計÷通期予想 > 90% かつ予想据え置きで発火 | 3Qのみ |
| F6 | 増配/減配（非対称） | +1/-2/-3 | FDivAnn vs 前回予想。増配>+5%→+1、減配<-5%→-2、大幅減配<-20%→-3 | 全Q |
| F7 | 折込度合い | -1〜-3 | 20日モメンタム>+10%→-1、>+20%→-2、>+30%→-3(事前修正なし) or 出来高5日/20日>2倍(-1) | 全Q |
| F7g | 成長加速/減速 | ±1 | 翌期YoY(ODP、不在時OP) vs 基準YoY(baseline_yoy_op)。差分>+20%→+1、<-20%→-1 | FYのみ |
| F8 | 信用売り残倍率 | +0.5 | 貸借倍率<1（売り長） | 全Q |
| F8b | 記念配当/特別配当 | +1 | TDnet TITLE に "記念配当" or "特別配当" を含む | 全Q |
| F9 | ~~テーマブースト~~ | 廃止 | 旧: 高β×TOPIX+。βでは個人投資家関心度を代理できず廃止 | - |
| F10 | 自社株買い（スケール） | 0〜+4 | `_is_buyback_title()` で自社株買い開示を検知 → PDF 解析で発行済比率を取得し段階スコアリング。ToSTNeT-3/N-NET3 のみの場合はウェイト0（タグ表示のみ）。PDF 解析失敗時はフォールバック +2。閾値: <3%→+1, 3-5%→+3, >=5%→+4 | 全Q |
| F12 | PER割安度 PEG | +2/+1/-1 | FY時、翌期成長率(ODP、不在時OP)>0の場合: PEG=PER÷成長率(%)。PEG<0.5→+2、<1.0→+1、>2.0→-1。株式分割時は無効化 | FYのみ |
| F13 | QoQ OP急変 | +1/-2 | 前Q単独OP比。>+50%→+1、<-50%→-2。FY除外（4Q implied はノイジー→F15で代替）。小分母ガード: \|prev_q\|<年間参照値×5%時スキップ | 1Q-3Q |
| F14 | 株式分割 | +1 | 同一銘柄の TDnet 開示に "株式分割" を含む。F6/F12 を無効化する副作用あり | 全Q |
| F15 | 通期着地サプライズ | ±1/±2 | FY実績ODP vs 直前通期予想ODP（経常利益ベース、ODP不在時はOPフォールバック）。±20%超→±2、±5%超→±1。表示: `着地経↑/↓` | FYのみ |

**判定**: `>=3` STRONG_BUY / `2` BUY / `1` SLIGHT_BUY / `0` NEUTRAL / `-1` SLIGHT_SELL / `<=-2` SELL

**表示略称**（watch/review テーブルの Judge 列、2026-04-14 導入）:

| verdict | 略称 | 意味 |
|---|---|---|
| STRONG_BUY | `S-Buy` | Strong |
| BUY | `N-Buy` | Neutral（中位の買い） |
| SLIGHT_BUY | `W-Buy` | Weak |
| NEUTRAL | `中立` | 中立 |
| SLIGHT_SELL | `W-Sell` | Weak |
| SELL | `Sell` | 据え置き |

**テーブル列**: `Time / Score / Code / Name / Cap / Q / Judge / Pos / Neg`
- `Cap`: 時価総額（億円）。YF_STOCK_INFO.MARKET_CAP を1e8で割る
- `Pos` / `Neg`: factors を符号別に分離（下方・減配・進捗↓・QoQ-・折込・出尽くし等は Neg 側、残りは Pos 側）

**テーブルレイアウト（列幅）**: `zaraba_earnings.py` 冒頭の `WATCH_TABLE_WIDTH_*` 定数で定義。列幅変更はここだけ触れば全サブコマンド（watch/review/gcs-review）に反映される。

### EDA因子との対応（2026-05-12更新）

059 EDA因子は全て実装済み:
- **F4c コンセンサス乖離** — 同期済み（全Q経常利益ベース。059側もOPフォールバック廃止・当期フォールバック廃止で統一）
- **F4n 翌期コンセンサス乖離** — 実装済み（FYのみ、純利益ベース。表示: `翌コ純`）。059側はF4b(NET_PROFIT)として全Qで適用
- **F7g 成長加速/減速** — 実装済み（FYのみ、翌期YoY vs baseline_yoy_op）
- **F9 テーマブースト** — 059/066とも廃止（βでは個人投資家関心度を代理できず）
- **F12 PER割安度(PEG)** — 実装済み（FYのみ、株式分割時は無効化）

計画: `docs/plans/20260413_zaraba_factor_sync.md`

## 運用フロー（典型的な1日）

```
前日夜 or 当日朝:
  $ zaraba_earnings.py prepare --date 20260407
  → 事前サマリー確認

11:00 の決算発表を監視:
  $ zaraba_earnings.py watch --date 20260407
  → Ctrl+C で終了

14:00 の決算発表を監視（中断後再開）:
  $ zaraba_earnings.py catchup --date 20260407 --until 13:50
  $ zaraba_earnings.py watch --date 20260407
```

## 答え合わせ・精度改善ループ

→ `docs/knowledges/tools/066-2_zaraba_detail.md` §答え合わせ・精度改善ループ（役割分担・精度改善サイクル・パラメータ管理。ザラ場側にverifyサブコマンドは作らない方針）

## 反省会ログ

→ `docs/knowledges/tools/066-1_zaraba_retrospective.md`（運用手順・書き込みルール・銘柄別ログ）
