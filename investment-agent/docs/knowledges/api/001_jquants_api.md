# J-Quants API 使用方法

**カテゴリ**: api
**作成日**: 2026-02-23
**ステータス**: 有効

---

## 契約プラン（2026-03-07 確認）

**現在の契約: Standard プラン**

| プラン | 分足データ | 日足 | 財務データ |
|--------|-----------|------|-----------|
| Free   | ❌        | ✅   | ❌        |
| Light  | ❌        | ✅   | 一部      |
| Standard | ❌      | ✅   | ✅        |
| Premium | ✅       | ✅   | ✅        |

**分足データ（`get_eq_bars_minute` 等）は Standard では取得不可（403）。Premium プランが必要。**

---

## ⚠️ pip install パッケージ名に注意

```bash
# ✅ 正しい（PyPI パッケージ名）
pip install jquants-api-client

# ❌ 存在しない（import 名であり PyPI 名ではない）
pip install jquantsapi
```

import 名は `jquantsapi`（`import jquantsapi`）だが、pip install 時は `jquants-api-client` を使う。

---

## ⚠️ V1 API / ClientV1 は廃止。V2 のみ使用すること

**V1 API (`/v1/...`) は使用禁止。** 2025/12/22以降の新規登録者には利用不可（廃止済み）。V1エンドポイントへのリクエストは 400 を返す。

- エンドポイント: `/v2/indices/bars/daily`, `/v2/equities/bars/daily` 等の **V2 パス**を使う
- 認証: `x-api-key` ヘッダーに `JQUANTS_API_KEY` 環境変数（IDトークン方式は廃止）
- レスポンスキー: V2 は `"data"` キー（V1 の `"indices"` / `"quotes"` 等ではない）

`jquantsapi.Client`（V1）も **Deprecated**。コード実行時に `DeprecationWarning` が出る。
新規コードでは必ず `jquantsapi.ClientV2` を使うこと。

```python
# ❌ 古い（V1 / 廃止予定）
import jquantsapi
cli = jquantsapi.Client(...)  # DeprecationWarning が出る

# ✅ 正しい（V2）
import jquantsapi
cli = jquantsapi.ClientV2(api_key=os.environ["JQUANTS_API_KEY"])
```

### ClientV2 の初期化

```python
import os
import jquantsapi

cli = jquantsapi.ClientV2(api_key=os.environ["JQUANTS_API_KEY"])
```

環境変数 `JQUANTS_API_KEY` は `.env` に設定済み（`ClientV2` の `api_key` 引数として渡す）。

### ClientV2 の主なメソッド（Standard プランで利用可能なもの）

| メソッド | 内容 |
|---------|------|
| `get_eq_bars_daily(code, date_yyyymmdd)` | 日足 OHLCV（調整済み含む） |
| `get_eq_bars_daily_range(code, from_, to_)` | 日足 期間指定 |
| `get_fin_summary(code, date_yyyymmdd)` | 財務サマリー |
| `get_fin_details(code, date_yyyymmdd)` | 財務詳細 |
| `get_eq_master(date_yyyymmdd)` | 銘柄マスタ |
| `get_mkt_short_ratio(date_yyyymmdd)` | 空売り比率 |

---

## J-Quants MCP サーバー（エンドポイント・仕様の調査に使う）

分析に J-Quants のデータが必要なとき、どのエンドポイントで取得できるか不明な場合は
**jquants-doc MCP サーバー**に聞くと即座に仕様とサンプルコードが得られる。

```
# Claude Code 上で使えるツール（.mcp.json 設定済み）
search_endpoints(keyword="財務")         # エンドポイントをキーワード検索
describe_endpoint("fins-statements")    # パラメータ・レスポンス詳細
generate_sample_code("fins-statements") # 実行可能な Python コード生成
answer_question("ページネーションの方法は？")
```

詳細: `docs/knowledges/tools/027_jquants_mcp_server.md`

---

## 共通モジュール: `scripts/jquants_common.py`

J-Quants 系スクリプトでは **`jquants_common.py`** をインポートして使う。
直接 `requests` を書かず、以下の共通関数を利用すること。

```python
from jquants_common import get_jquants_api_key, jquants_get
```

### API キー取得（環境自動判別）

```python
def get_jquants_api_key(runtime: str) -> str:
    """実行環境に応じて J-Quants API キーを取得する."""
    if runtime in ("colab_personal", "colab_enterprise"):
        from google.colab import userdata
        return userdata.get("JQUANTS_API_KEY")
    else:  # cloudrun / local
        return os.environ["JQUANTS_API_KEY"]
```

| 実行環境 | キー取得元 |
|---------|-----------|
| `colab_personal` / `colab_enterprise` | Colab Secrets の `JQUANTS_API_KEY` |
| `cloudrun` / `local` | 環境変数 `JQUANTS_API_KEY` |

### paginated GET（レートリミット自動リトライ付き）

```python
def jquants_get(
    endpoint: str,
    params: dict,
    headers: dict,
    sleep_sec: float = 0.5,
    retry_wait_sec: float = 30.0,
) -> list[dict]:
    ...
```

**使い方**:
```python
headers = {"x-api-key": get_jquants_api_key(RUNTIME)}

# 1日分の /fins/summary を取得（ページネーション自動処理）
records = jquants_get("/fins/summary", {"date": "20260307"}, headers)

# 銘柄マスタ（/listed/info）を全件取得
records = jquants_get("/listed/info", {}, headers)
```

**動作**:
- リクエスト前に `sleep_sec` 待機
- 429 受信時は `retry_wait_sec`（デフォルト30s）待機してリトライ
- `pagination_key` が返る限り自動でページ送り
- 400/500 系エラーはログ出力してその時点で取得済み分を返す

### 新しい J-Quants スクリプトを書く際のテンプレート

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jquants_common import get_jquants_api_key, jquants_get

RUNTIME = detect_runtime()   # scripts/jquants_common.py は detect_runtime は含まない

headers = {"x-api-key": get_jquants_api_key(RUNTIME)}

# エンドポイント呼び出し
records = jquants_get("/fins/statements", {"code": "75500"}, headers)
```

---

## インデックス日次データ（v2 API）

### 確認済みメソッド（2026-03-10）

```python
cli = jquantsapi.ClientV2(api_key=api_key)

# TOPIX 専用（OHLC、2016-03-10〜）
df = cli.get_idx_bars_daily_topix(from_yyyymmdd="20230601", to_yyyymmdd="20260101")
# → columns: Date, O, H, L, C

# 全インデックス一括（74指数、Code列付き）
df = cli.get_idx_bars_daily(date_yyyymmdd="20260107")
# → columns: Date, Code, O, H, L, C
# Code=0000 = TOPIX
```

### 日経225 は J-Quants 未提供

日経225 は Nikkei Inc. のライセンスデータのため J-Quants では提供されない（403エラー）。
yfinance `^N225` で代替する。詳細: `docs/knowledges/api/004_yfinance_index_tickers.md`

### 取得可能な主なインデックスコード（確認済み）

| Code | 内容 |
|------|------|
| `0000` | TOPIX |
| その他 73指数 | TOPIX系サブインデックス等（コード一覧は `get_idx_bars_daily()` で取得） |

---

## 認証: ClientV2（jquants-api-client）について

```python
# ⚠️ jquants-api-client（ClientV2）は現在使用していない
# V2 API の全エンドポイントに対応していないため requests で直叩きしている
import jquantsapi
client = jquantsapi.ClientV2(api_key="B0k4DU...")  # 使わない
```

**理由**: `jquants-api-client` ライブラリは V2 API の一部エンドポイント（`/fins/summary` 等）に未対応のため、`requests` + `jquants_common.py` で直接アクセスする方針に統一している。

---

## 銘柄コードの形式

| 用途 | 形式 | 例 |
|------|------|-----|
| J-Quants API | **5桁**（4桁 + "0"） | `"75500"`（ゼンショーHD） |
| BigQuery STOCK_PRICE | **4桁** | `"7550"` |
| 変換 | `ticker_4 + "0"` | `"7550"` → `"75500"` |

---

## 主要メソッド

### 銘柄マスタ
```python
df = client.get_eq_master()
# → 全上場銘柄の一覧（銘柄コード、銘柄名、業種等）
```

### 四半期財務データ
```python
fin = client.get_fin_summary(code="75500")  # 5桁コード
# → 累積値で返ってくる（Q1=Q1, Q2=Q1+Q2, Q3=Q1+Q2+Q3）
# → 単独四半期値に変換するには差分計算が必要

# ⚠️ 財務列は object 型で返る → 数値化が必要
fin["OperatingProfit"] = pd.to_numeric(fin["OperatingProfit"], errors="coerce")
```

#### 単独四半期への変換例
```python
def calc_standalone(df, col):
    """累積値 → 単独四半期値に変換（Q1は累積=単独）。"""
    result = []
    for _, grp in df.groupby("Code"):
        grp = grp.sort_values("FiscalQuarterEnd")
        vals = grp[col].values.copy()
        for i in range(len(vals) - 1, 0, -1):
            if grp["FiscalQuarter"].iloc[i] != 1:  # Q1 以外は差分
                vals[i] = vals[i] - vals[i-1]
        result.append(pd.Series(vals, index=grp.index))
    return pd.concat(result)
```

---

## BigQuery との組み合わせ: UNNEST パラメータ化クエリ

複数ティッカーを BigQuery に渡す際の標準パターン。

```python
from google.cloud import bigquery

SQL = """
SELECT *
FROM `gmailpj-357912.STOCK.STOCK_PRICE`
WHERE TICKER IN UNNEST(@tickers)
  AND YEARDATE >= '2017-01-01'
"""

tickers = ["7550", "3197", "9861"]  # 4桁コード

cfg = bigquery.QueryJobConfig(
    query_parameters=[
        bigquery.ArrayQueryParameter("tickers", "STRING", tickers)
    ]
)
df = client.query(SQL, job_config=cfg).to_dataframe()
```

- `UNNEST(@tickers)` で配列を展開してIN句として使う
- `ArrayQueryParameter("param_name", "STRING", list)` で渡す
- SQL内のバッククォート `` ` `` はシングルクォート文字列内に書く（エスケープ不要）

---

## 注意事項

- `get_fin_summary` は**累積値**で返る点に注意（単独四半期への変換が必要）
- 財務データの列型は `object`（文字列）で返ることが多い → `pd.to_numeric` で変換
- レートリミット: 詳細は公式ドキュメント参照

---

## プラン別データ取得範囲

### 現在のプラン（2026-03-07 確認）

**取得可能期間: 2016-03-05 以降**

2016-03-05 より前の日付でデータを取得しようとすると Error 400 が返る:
```
{"message": "Your subscription covers the following dates: 2016-03-05 ~ . If you want more data, please check other plans:https://jpx-jquants.com/#dataset"}
```

### jquants-fin-summary ジョブでの影響

- `--from=20140101 --to=20151231` などで 2016-03-05 以前を指定すると全日付で Error 400
- スクリプトはエラーをスキップしながら進むため、取得件数 0 のまま 600 秒タイムアウトで終了
- **再実行しても結果は変わらない**。プランアップグレードが必要

### 安全な開始日

```python
# 2016-03-05 以降を指定すること
--from=20160305
# 余裕を持つなら
--from=20160401  # 4月始まりで確実
```
