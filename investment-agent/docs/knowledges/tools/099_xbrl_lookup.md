# XBRL四半期推移ツール（xbrl_lookup）

**カテゴリ**: tools
**作成日**: 2026-05-12
**ステータス**: 有効

## 概要

TDnet XBRLから最新決算データを取得し、BQ `fin_summary` の過去データと組み合わせてQ standalone推移テーブルを表示するツール。ザラ場ツール（→ `066_zaraba_tool.md`）のXBRL抽出ロジックを流用。

## 関連ファイル

- `scripts/xbrl_lookup.py` — 本体
- `scripts/zaraba_tdnet_poller.py` — XBRL抽出エンジン（**共有コード**。変更時は本ツール・ザラ場ツール両方に影響）
- PSメニュー: [`023_powershell_menu.md`](023_powershell_menu.md)「最新決算表示」

## 共有コード依存（変更時の同時更新義務）

| 共有モジュール | 依存先 | 変更時の確認先 |
|---------------|--------|-------------|
| `zaraba_tdnet_poller.py` — `TdnetHtmlPoller` | xbrl_lookup, zaraba_earnings | 両方のテスト |
| `zaraba_tdnet_poller.py` — `XbrlExtractor` | xbrl_lookup, zaraba_earnings | 両方のテスト |
| `zaraba_tdnet_poller.py` — `TDNET_TAG_MAP` | xbrl_lookup, zaraba_earnings | 抽出項目の整合 |
| `zaraba_tdnet_poller.py` — `_parse_ixbrl` / `_extract_tdnet_pl` | xbrl_lookup, zaraba_earnings | パースロジック |

## 使い方

```bash
# 銘柄コード1つを指定（常に当日の開示を取得）
PYTHONUTF8=1 python scripts/xbrl_lookup.py 6445
```

PS1メニューから「最新決算表示」で実行。銘柄コードを対話入力。
表示完了後、別の銘柄コードを入力すると連続表示。Enter で終了。

## データソース

| データ | ソース | 用途 |
|--------|--------|------|
| 最新Q実績 | TDnet XBRL（iXBRL） | 最新決算の累計値 |
| 過去Q実績 | BQ `STOCK.fin_summary` | 過去3年の累計値 |

XBRL ZIP 保存先: `C:\Users\zonekun\Dropbox\stock\temp\xbrl_lookup\{YYYYMMDD}\`（ザラ場ツールの `C:\tmp\zaraba_cache` とは独立）

## 表示項目

累計値からQ standalone を算出して表示。全FYに累計行を挿入（Q≥2の場合）。ラベルは `2累`/`3累`/`Y累`。最新FYでは先頭、過去FYでは累計対象Q群の直前（例: 3累なら4Qと3Qの間）に配置。YoY は前年同期間の累計と比較。

| 列 | 内容 |
|----|------|
| 売上高 | 百万円 |
| 営業利益 | 百万円 |
| 経常利益 | 百万円 |
| 純利益 | 百万円 |
| EPS | 円（小数2桁） |
| 営業率 | 営業利益 / 売上高 |
| 経常率 | 経常利益 / 売上高 |
| 売上YoY | 前年同Q比 |
| 営業YoY | 前年同Q比 |
| 経常YoY | 前年同Q比 |
| 純利YoY | 前年同Q比（赤転/黒転表示） |

## 翌期会社予想（FY開示時のみ）

FY決算XBRL取得時、翌期の会社予想（`NEXT_YEAR_FORECAST_*`）をテーブル先頭行に黄色で表示。
表示項目は売上高・営業利益・経常利益・純利益・EPSの5列のみ（率・YoYなし）。
非開示項目は `NA` 表示。全項目が非開示の場合は予想行自体を非表示。

## standalone 計算ロジック

```
1Q standalone = 1Q累計（そのまま）
2Q standalone = 2Q累計 - 1Q累計
3Q standalone = 3Q累計 - 2Q累計
4Q standalone = FY累計 - 3Q累計
```

BQ `fin_summary` の累計値を使い、最新Qのみ XBRL から取得。EPS も同様に累計差分で standalone 化。

## 注意事項

- XBRL取得にはTDnetアクセスが必要（開示日当日 or 過去数日分が有効）
- `fin_summary` にまだ反映されていない最新決算を XBRL で補完する設計
- 変則決算期の企業は FY判定がずれる場合がある
