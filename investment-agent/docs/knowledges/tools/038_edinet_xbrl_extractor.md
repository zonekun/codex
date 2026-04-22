# EDINET XBRL 現金・有価証券抽出ツール（edinet_xbrl_extractor.py）

**カテゴリ**: tools
**作成日**: 2026-03-10
**ステータス**: 有効
**関連ファイル**:
- `scripts/edinet_xbrl_extractor.py` — メインスクリプト

---

## 概要

EDINET の有価証券報告書（XBRL）から **現金** と **有価証券（持合い含む）** を
全上場銘柄分抽出し TSV ファイルに出力するツール。

清原スクリーニング等で現金・有価証券の高鮮度データが必要な場合に使用する。
四季報よりデータ鮮度が高く、B/S の実際の数値を直接取得できる。

---

## 使い方

```bash
PYTHONUTF8=1 python scripts/edinet_xbrl_extractor.py
```

### 設定変更（スクリプト冒頭）

```python
SEARCH_START_DATE = "2025-01-01"  # 検索開始日
DAYS_TO_SCAN      = 365           # 検索日数（1年分）
TEST_MODE_LIMIT   = 0             # 0=全件 / N=テスト用N件で打ち切り
OUTPUT_TSV_PATH   = "edinet_financial_data_2025_final_v2.tsv"  # 出力先
```

---

## 出力フォーマット

TSV（タブ区切り）、`utf-8-sig` エンコード

| カラム | 型 | 説明 |
|--------|-----|------|
| 証券コード | STRING | 4桁銘柄コード |
| 会社名 | STRING | 提出者名 |
| 提出日 | DATE | 有価証券報告書の提出日（YYYY-MM-DD） |
| 現金(百万円) | INTEGER | 現金・預金（連結優先） |
| 有価証券(百万円) | INTEGER | 投資有価証券等（連結優先） |

---

## データ取得ロジック

### 銘柄リスト
JPX サイト（`data_j.xls`）から最新の上場銘柄リストを取得。
ETF・ETN / PRO Market / REIT・インフラファンド等は除外。

### EDINET API
- `GET /api/v2/documents.json?date=YYYY-MM-DD&type=2` で提出書類一覧取得
- タイトルに「有価証券報告書」を含み「訂正」を含まないものを対象
- `GET /api/v2/documents/{docID}?type=1` で ZIP（XBRL）をダウンロード

### XBRL タグ

**現金タグ（CASH_TAGS）:**
```
CashAndDeposits, CashAndDepositsAssetsINS, CashAssetsBNK,
CashAssetsINS, DepositsAssetsINS, DepositsCAFND
```

**有価証券タグ（SECURITY_TAGS）:**
```
InvestmentSecurities, InvestmentSecuritiesOfSubsidiariesAndAffiliates,
ShortTermInvestmentSecurities, SecuritiesAssetsBNK, SecuritiesAssetsINS,
OperationalInvestmentSecuritiesCA
```

### 連結 vs 単体
- `contextRef` に `CurrentYearInstant` を含む要素のみ対象（期末残高）
- 連結（Consolidated）データがあれば優先採用
- なければ単体（NonConsolidated）データを使用
- セグメント別 Member / Domain は除外（NonConsolidated は許可）

---

## 通信安定化

- `requests.Session` + `urllib3.Retry`（最大5回、バックオフ1秒）
- `status_forcelist=[500, 502, 503, 504, 429]`
- リクエスト間 `time.sleep(0.05)` でレートリミット配慮
- タイムアウト 30秒

---

## 清原スクリーニングとの連携

`scripts/kiyohara_screening.py` との対応関係:

| スクリーニング項目 | 本ツール出力カラム |
|------------------|--------------------|
| 現金（cash） | `現金(百万円)` × 1,000,000 |
| 有価証券（securities） | `有価証券(百万円)` × 1,000,000 |

本ツールで取得した TSV を `kiyohara_screening.py` に組み込む場合は、
四季報フォールバックより本ツール出力を優先すること（鮮度・精度ともに優位）。

---

## EDINET API キーについて

`.env` の `EDINET_API_KEY=your_edinet_api_key` は**プレースホルダー**。実際のキーは
`scripts/edinet_download.py` のデフォルト値に埋め込まれている（`os.environ.get("EDINET_API_KEY", "<実キー>")`）。

Cloud Run Job 作成時はこのキーを環境変数にセットすること。有効なキーなしでは
EDINET API が空のレスポンスを返し、0件取得になる（エラーにはならないため気づきにくい）。

```bash
# edinet_download.py からキーを取得してジョブにセット
REAL_KEY=$(python -c "import re; content=open('scripts/edinet_download.py',encoding='utf-8').read(); m=re.search(r'get\(\"EDINET_API_KEY\",\s*\"([^\"]+)\"', content); print(m.group(1))")
gcloud run jobs update edinet-xbrl-extractor --region us-west1 --update-env-vars "EDINET_API_KEY=${REAL_KEY}"
```

---

## 四季報との精度比較（2026-03-10 検証）

5銘柄で kiyohara_screening.py（四季報ベース）と XBRL 抽出値を比較した結果:

| コード | 会社名 | 四季報 現預金(百万) | XBRL 現金(百万) | 差異 |
|--------|--------|------------------:|---------------:|-----:|
| 6087 | アビスト | 4,143 | 4,489 | +8.4% |
| 2874 | 横浜冷凍 | 3,600 | 3,481 | -3.3% |
| 4707 | キタック | 155 | 214 | +38.1% |
| 7856 | 萩原工業 | 4,859 | 5,601 | +15.3% |
| 9678 | カナモト | 50,500 | 51,104 | +1.2% |

差異は主に四季報の鮮度（発刊タイミング）によるもの。XBRL は有価証券報告書の実際の BS 値であり精度・鮮度ともに優位。

---

## 注意事項

- **EDINET_API_KEY が必要**: 上記「EDINET API キーについて」を参照。`.env` は無効なプレースホルダー
- **実行時間**: 365日分を全件取得すると数時間かかる場合がある。`TEST_MODE_LIMIT=10` でテスト確認推奨
- **有価証券報告書の提出タイミング**: 3月決算企業は6月末〆、各社の決算期によって提出時期が異なる。直近1年分をスキャンすれば大半の銘柄をカバーできる
- **Colab 対応**: Colab で実行した場合は `files.download()` で自動ダウンロードされる
