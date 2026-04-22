# irbank.net 経由 TDnet 過去 PDF ダウンロード

**カテゴリ**: tools
**作成日**: 2026-03-04
**ステータス**: 有効
**関連ファイル**: `scripts/irbank_tdnet_download.py`

## 概要

TDnet 公式サイトの PDF 保持期間（30〜90日）を超えた過去 PDF を取得するスクリプト。
irbank.net（IR情報サイト）が TDnet PDF を自社 CDN (`f.irbank.net`) に保持しているため、
少なくとも 2014年〜現在まで取得可能。

## TDnet 公式との違い

| 項目 | TDnet 公式 | irbank.net |
|------|-----------|------------|
| PDF 保持期間 | 30〜90日 | 少なくとも 2014年〜現在 |
| メタデータ取得 | HTML スクレイピング | yanoshin 非公式 API |
| PDF 取得 | `release.tdnet.info/inbs/DOCID.pdf` | `f.irbank.net/pdf/YYYYMMDD/DOCID.pdf` |
| 2025年 PDF | ❌ 全件 404 | ✅ 全件取得可能（実証済み） |

## irbank.net URL 仕様

### PDF 直接取得 URL

```
https://f.irbank.net/pdf/{YYYYMMDD}/{doc_id}.pdf
```

- `YYYYMMDD`: 開示日（yanoshin の `pubdate` フィールドから取得）
- `doc_id`: TDnet ドキュメントID（例: `140120250730523265`）

### フォルダ日付の決め方

yanoshin の `pubdate`（例: `"2025-07-31 16:30:00"`）の YYYYMMDD 部分を使う。
doc_id 内の日付（`1401YYYYMMDDNNNNNN` の 4〜12 文字目）とは 1日ずれることがある。

スクリプトでは pubdate 当日 → +1日 → -1日 → doc_id 内日付 の順で試みる。

### リスト・詳細 URL

```
# 銘柄別 TDnet 開示一覧
https://irbank.net/{EDINET_CODE}/tdnet       (例: /E04498/tdnet)
https://irbank.net/{stock_code}/tdnet        (例: /9501/tdnet)

# ページネーション（過去ページ）
https://irbank.net/{EDINET_CODE}/tdnet?y={unix_timestamp}

# 個別 詳細ページ
https://irbank.net/{stock_code}/{doc_id}     (例: /9501/140120250730523265)
```

## 実行方法

```bash
# Cloud Run Job として実行（推奨）
gcloud run jobs execute irbank-tdnet-download --region us-west1 \
  --args="--from=20250101,--to=20250131"

# ローカル実行
PYTHONUTF8=1 python scripts/irbank_tdnet_download.py --from 20250101 --to 20250131
```

### 2025年全期間バッチ実行スクリプト

```bash
# 月次に分けて順次実行（約18時間）
bash scripts/run_irbank_2025.sh >> data/logs/irbank_2025.log 2>&1 &
```

## レート制限

| 対象 | 間隔 | 変更不可 |
|------|------|---------|
| yanoshin API | 2秒 | ban 対策のため変更禁止 |
| irbank PDF DL | 3秒 | ban 対策のため変更禁止 |
| 日付間 | 2秒 | 追加スリープ |

## 出力

GCS: `gs://stock_data_1930932/tdnet/{証券コード4桁}/{filename}.pdf`（既存 tdnet/ と同じ場所）
インデックス CSV: `gs://stock_data_1930932/tdnet/index_irbank_{from}_{to}.csv`

ファイル名形式は `tdnet_download.py` と同じ:
```
{YYYYMMDD}_{証券コード4桁}_{会社名}_{カテゴリ}_{タイトル}_{doc_id}.pdf
```

## 実績（2026-03-04 確認）

| 期間 | yanoshin 取得 | DL 成功 | irbank に PDF なし | 実行時間 |
|------|-------------|---------|-----------------|---------|
| 2025-01-06 ～ 2025-01-10（5営業日） | 1,363件 | 1,175件 | **0件** | 1h21m |

`irbank に PDF なし: 0件` → **irbank.net は全 TDnet PDF を保持している**ことを確認。

## マルチプラットフォーム対応

### parse_args の Colab 対応

Colab 環境では `sys.argv` にスクリプト引数が渡らないため、環境変数から読み取る:

```python
def parse_args():
    runtime = detect_runtime()
    if runtime in ("colab_personal", "colab_enterprise"):
        date_from = os.environ.get("IRBANK_DATE_FROM")
        date_to = os.environ.get("IRBANK_DATE_TO")
        if not date_from or not date_to:
            raise ValueError("Colab 環境では IRBANK_DATE_FROM / IRBANK_DATE_TO を設定してください")
        return Namespace(date_from=date_from, date_to=date_to)
    else:
        parser = argparse.ArgumentParser()
        parser.add_argument("--from", dest="date_from", required=True)
        parser.add_argument("--to",   dest="date_to",   required=True)
        return parser.parse_args()
```

### GCS クライアントのローカル認証

```python
def _get_gcs_client():
    runtime = detect_runtime()
    if runtime == "colab_personal":
        key_info = json.loads(userdata.get("GCP_SA_KEY"))
        creds = service_account.Credentials.from_service_account_info(key_info)
    elif runtime == "colab_enterprise":
        import google.auth
        creds, _ = google.auth.default()
    else:  # local または cloudrun
        key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
        creds = service_account.Credentials.from_service_account_file(key_path)
    return storage.Client(credentials=creds, project="gmailpj-357912")
```

---

## UA・TLS フィンガープリントローテーション

ban 対策として、リクエストごとに UA と `impersonate` プロファイルをランダム切替する。

```python
import itertools, random
from curl_cffi import requests as curl_requests

_UA_POOL: list[tuple[str, str]] = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/124.0.0.0 Safari/537.36", "chrome124"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/120.0.0.0 Safari/537.36", "chrome120"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/116.0.0.0 Safari/537.36", "chrome116"),
    ("Mozilla/5.0 (Macintosh; ...) ... Chrome/124.0.0.0 Safari/537.36", "chrome124"),
    ("Mozilla/5.0 (Macintosh; ...) Safari/605.1.15", "safari17_0"),
    ("Mozilla/5.0 (Windows NT 10.0; ...) Firefox/125.0", "chrome124"),  # Firefox UA だが impersonate は chrome124
]

def _next_request_kwargs() -> dict:
    ua, impersonate = random.choice(_UA_POOL)
    return {"headers": {"User-Agent": ua}, "impersonate": impersonate}

# 呼び出し側
resp = curl_requests.get(url, timeout=30, **_next_request_kwargs())
```

> Firefox の impersonate 値は curl_cffi に存在しないため `"chrome124"` を代入する。

### IP ローテーション（オプション）

`IRBANK_PROXIES` 環境変数にプロキシリスト（カンマ区切り）を設定するとラウンドロビンで切替:

```bash
export IRBANK_PROXIES="http://proxy1:8080,http://proxy2:8080"
```

```python
def _load_proxies() -> list[dict]:
    raw = os.environ.get("IRBANK_PROXIES", "")
    return [{"http": p, "https": p} for p in raw.split(",") if p.strip()]

_PROXY_LIST = _load_proxies()
_proxy_cycle = itertools.cycle(_PROXY_LIST) if _PROXY_LIST else None

def _next_request_kwargs() -> dict:
    ua, impersonate = random.choice(_UA_POOL)
    kwargs = {"headers": {"User-Agent": ua}, "impersonate": impersonate}
    if _proxy_cycle is not None:
        kwargs["proxies"] = next(_proxy_cycle)
    return kwargs
```

**実運用での判断**:
- Cloud Run は実行ごとに異なる外部 IP を使うため、バッチジョブでは `IRBANK_PROXIES` 未設定で十分。
- 無料プロキシは不安定・HTTPS 非対応が多いため**推奨しない**。

---

## 注意事項

- `--set-secrets` は不要（`notify.py` が SMTP 認証情報をハードコード済み）
- Secret Manager API を有効化しなくても動作する
- カテゴリ分類・フィルタリングロジックは `tdnet_download.py` と同じ
- 再開ログ: `gs://stock_data_1930932/tdnet/_resume_irbank_{from}_{to}.txt`
  → 中断後も同じ引数で実行すれば完了済み日付をスキップして再開

## 関連ファイル

- `scripts/irbank_tdnet_download.py` — 実装本体
- `scripts/run_irbank_2025.sh` — 2025年全期間バッチ実行シェルスクリプト
- `docker/Dockerfile.irbank-tdnet` — Docker イメージ定義
- `cloudbuild/cloudbuild.irbank-tdnet.yaml` — Cloud Build 設定
- `docs/knowledges/tools/003_tdnet_download.md` — TDnet 公式版
- `docs/knowledges/api/003_tdnet_official_scraping.md` — TDnet・yanoshin API 仕様
