"""
Round9 adapter fixes (2026-03-24): スキップ18社のextract_adapter修正

Issues found:
  - row_label_regex の `^` アンカーは text.splitlines() の各行にのみ有効
  - チャンク結合順序が不定のため `建設エンジニア在籍` より前に数値が来る場合がある
  - 月固定regex (^11月, ^2月 等) がhardcoded

Companies fixed:
  2154: construction_engineer_headcount → 在籍数(名)稼働率アンカーに変更
  3093: all_store_sales_yoy / existing_store_sales_yoy → コメント文から抽出
  3181: all_store_sales_yoy / existing_store_sales_yoy → 位置ベース
  4058: consolidated_sales → use_last_number
  4776: consolidated_sales → year セクション + use_last_number
  7059: グループ合計 → ^ 除去 + \s* + use_last_number
  7640: 位置ベース + 既存店 regex
  8255: Markdown table {month_num} アプローチ
  8914: 稼働率フィールド追加（現在0フィールド）
  6561: 前年比フィールド追加（現在0フィールド）
  9022: Q3決算 → 運輸業収益比追加
  9519: Q3決算 → 設備容量フィールド追加

実行:
  PYTHONUTF8=1 uv run python scripts/apply_batch_fixes_round9.py
"""
import json
import datetime
from google.oauth2 import service_account
from google.cloud import storage

KEY_FILE = r"G:\マイドライブ\claude\investment-agent\keys\gcp-service-account.json"
BUCKET = "stock_data_1930932"

creds = service_account.Credentials.from_service_account_file(KEY_FILE)
gcs = storage.Client(credentials=creds, project="gmailpj-357912")
bucket = gcs.bucket(BUCKET)

now = datetime.date.today().isoformat()
ok = 0
errors = 0


def upload_adapter(ticker: str, adapter: dict):
    global ok, errors
    adapter["updated_at"] = now
    adapter["updated_by"] = "round9_batch_fix"
    path = f"monthlydata/{ticker}/extract_adapter.json"
    try:
        blob = bucket.blob(path)
        blob.upload_from_string(
            json.dumps(adapter, ensure_ascii=False, indent=2),
            content_type="application/json",
        )
        print(f"  [{ticker}] {adapter.get('company_name', '')} → uploaded ✅")
        ok += 1
    except Exception as e:
        print(f"  [{ticker}] ERROR: {e}")
        errors += 1


def patch_adapter(ticker: str, field_patches: dict) -> bool:
    """GCS から extract_adapter.json を読み込み、fieldsのregexを変更して書き戻す。
    field_patches: {field_key: {attr: value}} の形式
    """
    global ok, errors
    blob = bucket.blob(f"monthlydata/{ticker}/extract_adapter.json")
    try:
        data = json.loads(blob.download_as_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [{ticker}] 読み込み失敗: {e}")
        errors += 1
        return False

    for field in data.get("fields", []):
        key = field.get("key", "")
        if key in field_patches:
            for attr, val in field_patches[key].items():
                field[attr] = val
                print(f"  [{ticker}] {key}.{attr} → {repr(val)[:60]}")

    data["updated_at"] = now
    data["updated_by"] = "round9_batch_fix"
    try:
        blob.upload_from_string(
            json.dumps(data, ensure_ascii=False, indent=2),
            content_type="application/json",
        )
        ok += 1
        return True
    except Exception as e:
        print(f"  [{ticker}] 書き込み失敗: {e}")
        errors += 1
        return False


print("=== Round9 adapter fixes ===\n")

# ------------------------------------------------------------------
# 2154 オープンアップグループ
# construction_engineer_headcount: チャンク順序問題
# → 「在籍数 (名) 稼働率 {year}年{month}月末時点」アンカーに変更
# ------------------------------------------------------------------
print("--- 2154 オープンアップグループ ---")
patch_adapter("2154", {
    "construction_engineer_headcount": {
        "row_label_regex": (
            r"在籍数\s*[(（]名[)）]\s*稼働率\s*"
            r"\d{4}年\d{1,2}月末時点\s+"
            r"(\d{1,3}(?:,\d{3})*)\s+\d{2,3}\.\d"
        ),
        "notes": (
            "建設エンジニア在籍数。'在籍数(名)稼働率 YYYY年M月末時点'アンカーで"
            "チャンク順序問題を回避 (round9)"
        ),
    }
})

# ------------------------------------------------------------------
# 3093 トレジャー・ファクトリー
# all_store_sales_yoy / existing_store_sales_yoy:
#   `^2月\s+` は行頭アンカーで単一行テキストでは機能しない
# → 月次コメント文「全店で前年同月比 X%、既存店で同 Y%」から抽出
# total_stores: 全店舗数テーブルから最後の値を取得
# ------------------------------------------------------------------
print("\n--- 3093 トレジャー・ファクトリー ---")
patch_adapter("3093", {
    "all_store_sales_yoy": {
        "row_label_regex": r"全店で前年同月比\s*([\d.]+)%",
        "notes": "月次コメント文から全店前年同月比を抽出 (round9)",
    },
    "existing_store_sales_yoy": {
        "row_label_regex": r"既存店で同\s*([\d.]+)%",
        "notes": "月次コメント文から既存店前年同月比を抽出 (round9)",
    },
    "total_stores": {
        "row_label_regex": r"全店舗数.*?(\d+)\(\d+\)",
        "use_last_number": True,
        "notes": "全店舗数テーブルから最新月の店舗数(NNN(M)形式)を取得 (round9)",
    },
})

# ------------------------------------------------------------------
# 3181 買取王国
# all_store_sales_yoy / existing_store_sales_yoy:
#   `^全店`, `^既存店` が機能しない
# → 下半期テーブルの最後の月値(全店/既存店の3rd from last = 当月)を取得
# ------------------------------------------------------------------
print("\n--- 3181 買取王国 ---")
patch_adapter("3181", {
    "existing_store_sales_yoy": {
        "row_label_regex": (
            r"下半期.*?通\s*期\s+既存店\s+[\s\S]*?"
            r"([\d.]+)%\s+([\d.]+)%\s+([\d.]+)%\s*全店"
        ),
        "notes": (
            "下半期テーブルから既存店の当月値(下半期・通期の直前値)を取得 (round9)"
        ),
    },
    "all_store_sales_yoy": {
        "row_label_regex": (
            r"全店\s+[\s\S]*?"
            r"([\d.]+)%\s+([\d.]+)%\s+([\d.]+)%\s*文書タイトル"
        ),
        "notes": (
            "下半期テーブルから全店の当月値(テーブル末尾から3番目)を取得 (round9)"
        ),
    },
    "total_stores": {
        "row_label_regex": r"店舗数\s+(\d+)(?:\s*店)?",
        "use_last_number": True,
        "notes": "店舗数の最新値を取得 (round9)",
    },
})

# ------------------------------------------------------------------
# 4058 トヨクモ
# consolidated_sales_million_yen / consolidated_sales_yoy:
#   `売上高\s+[\d,]+` が参考データ(前年)にも一致して失敗
#   ％ が全角だった可能性(正規化後は % になる)
# → 「月次売上 前年同月比 X Y%」の最後の値を取得
# ------------------------------------------------------------------
print("\n--- 4058 トヨクモ ---")
patch_adapter("4058", {
    "consolidated_sales_million_yen": {
        "row_label_regex": r"月次売上\s+前年同月比\s+([\d,]+)\s+[\d.]+%",
        "use_last_number": True,
        "notes": "月次売上の最新月分(百万円) - use_last_number で最新値取得 (round9)",
    },
    "consolidated_sales_yoy": {
        "row_label_regex": r"月次売上\s+前年同月比\s+[\d,]+\s+([\d.]+)%",
        "use_last_number": True,
        "notes": "月次売上前年同月比(%) - use_last_number で最新値取得 (round9)",
    },
})

# ------------------------------------------------------------------
# 4776 サイボウズ
# consolidated_sales_million_yen / consolidated_sales_yoy:
#   `^■` が行頭アンカーで機能しない
# → {year}年セクションの月次クラウド収益から最新月値を取得
# ------------------------------------------------------------------
print("\n--- 4776 サイボウズ ---")
patch_adapter("4776", {
    "consolidated_sales_million_yen": {
        "row_label_regex": (
            r"{year}年.*?月次クラウド関連事業売上高.*?"
            r"[(（]百万円[)）]\s*([\d,]+)\s*前年同期比"
        ),
        "use_last_number": True,
        "notes": "{year}年セクションの月次クラウド売上(百万円) use_last_number (round9)",
    },
    "consolidated_sales_yoy": {
        "row_label_regex": (
            r"{year}年.*?月次クラウド関連事業売上高.*?"
            r"前年同期比\s+([\d.]+)%"
        ),
        "use_last_number": True,
        "notes": "{year}年セクションの月次クラウド売上前年比(%) use_last_number (round9)",
    },
})

# ------------------------------------------------------------------
# 7059 コプロ・ホールディングス
# 全8フィールドが `^1\. グループ 合計` 等の行頭アンカーで失敗
# → ^ 除去、OCR スペース対応 (\s*)、use_last_number=True
# ------------------------------------------------------------------
print("\n--- 7059 コプロ・ホールディングス ---")
patch_adapter("7059", {
    "group_total_engineers_count": {
        "row_label_regex": (
            r"(?:1[.．]\s*)?グループ\s*合計[\s\S]*?"
            r"在籍技術者数\s*([\d,\s]+)稼働技術者数"
        ),
        "use_last_number": True,
        "notes": "グループ合計の最新月在籍技術者数 (round9)",
    },
    "group_total_engineer_utilization_rate": {
        "row_label_regex": (
            r"(?:1[.．]\s*)?グループ\s*合計[\s\S]*?"
            r"稼働率\s*([\d.%\s]+)(?:稼働率\(研修|2024年|$)"
        ),
        "use_last_number": True,
        "notes": "グループ合計の最新月稼働率 (round9)",
    },
    "construction_engineers_count": {
        "row_label_regex": (
            r"(?:2[.．]\s*)?建設技術者派遣[\s\S]*?"
            r"在籍技術者数\s*([\d,\s]+)稼働技術者数"
        ),
        "use_last_number": True,
        "notes": "建設技術者派遣の最新月在籍技術者数 (round9)",
    },
    "construction_engineer_utilization_rate": {
        "row_label_regex": (
            r"(?:2[.．]\s*)?建設技術者派遣[\s\S]*?"
            r"稼働率\s*([\d.%\s]+)(?:稼働率\(研修|2024年|$)"
        ),
        "use_last_number": True,
        "notes": "建設技術者派遣の最新月稼働率 (round9)",
    },
    "mechatronics_semiconductor_engineers_count": {
        "row_label_regex": (
            r"(?:3[.．]\s*)?機電.{0,10}半導体[\s\S]*?"
            r"在籍技術者数\s*([\d,\s]+)稼働技術者数"
        ),
        "use_last_number": True,
        "notes": "機電・半導体の最新月在籍技術者数 (round9)",
    },
    "mechatronics_semiconductor_engineer_utilization_rate": {
        "row_label_regex": (
            r"(?:3[.．]\s*)?機電.{0,10}半導体[\s\S]*?"
            r"稼働率\s*([\d.%\s]+)(?:稼働率\(研修|2024年|$)"
        ),
        "use_last_number": True,
        "notes": "機電・半導体の最新月稼働率 (round9)",
    },
    "it_engineers_count": {
        "row_label_regex": (
            r"(?:4[.．]\s*)?IT技術者派遣[\s\S]*?"
            r"在籍技術者数\s*([\d,\s]+)稼働技術者数"
        ),
        "use_last_number": True,
        "notes": "IT技術者派遣の最新月在籍技術者数 (round9)",
    },
    "it_engineer_utilization_rate": {
        "row_label_regex": (
            r"(?:4[.．]\s*)?IT技術者派遣[\s\S]*?"
            r"稼働率\s*([\d.%\s]+)(?:稼働率\(研修|2024年|$)"
        ),
        "use_last_number": True,
        "notes": "IT技術者派遣の最新月稼働率 (round9)",
    },
})

# ------------------------------------------------------------------
# 7640 トップカルチャー (蔦屋書店)
# `^全店計`, `^店舗数`, `^既存店` が行頭アンカーで機能しない
# → 位置ベースで {month_num} 月の値を取得
# ------------------------------------------------------------------
print("\n--- 7640 トップカルチャー ---")
patch_adapter("7640", {
    "tsutaya_all_store_sales_yoy": {
        "row_label_regex": (
            r"全店計\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)"
        ),
        "group": 5,
        "notes": "全店計の5番目の値(2月分) - 11月/12月/1月/1Q累計/2月の順 (round9)",
    },
    "tsutaya_existing_store_sales_yoy": {
        "row_label_regex": (
            r"既存店\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)"
        ),
        "group": 5,
        "notes": "既存店の5番目の値(2月分) (round9)",
    },
    "all_store_count": {
        "row_label_regex": r"店舗数\s+(\d+)\s+店",
        "use_last_number": True,
        "notes": "全店舗数の最新値(店) (round9)",
    },
})

# ------------------------------------------------------------------
# 8255 アクシアル リテイリング
# `^11月` が hardcoded 月固定 + 行頭アンカーで機能しない
# → Markdown テーブルで {month_num}月 行から各指標を取得
# ------------------------------------------------------------------
print("\n--- 8255 アクシアル リテイリング ---")
patch_adapter("8255", {
    "food_super_all_store_sales_yoy": {
        "row_label_regex": r"\|\s*{month_num}月\s*\|\s*([\d.]+)",
        "notes": "Markdownテーブルから{month_num}月の全店売上高前年比 (round9)",
    },
    "food_super_existing_store_sales_yoy": {
        "row_label_regex": r"\|\s*{month_num}月\s*\|\s*[\d.]+\s*\|\s*([\d.]+)",
        "notes": "Markdownテーブルから{month_num}月の既存店売上高前年比 (round9)",
    },
    "food_super_all_store_customer_count_yoy": {
        "row_label_regex": (
            r"\|\s*{month_num}月\s*\|\s*[\d.]+\s*\|\s*[\d.]+\s*\|\s*([\d.]+)"
        ),
        "notes": "Markdownテーブルから{month_num}月の全店客数前年比 (round9)",
    },
    "food_super_existing_store_customer_count_yoy": {
        "row_label_regex": (
            r"\|\s*{month_num}月\s*\|\s*[\d.]+\s*\|\s*[\d.]+\s*\|\s*[\d.]+\s*\|\s*([\d.]+)"
        ),
        "notes": "Markdownテーブルから{month_num}月の既存店客数前年比 (round9)",
    },
    "food_super_all_store_customer_spend_yoy": {
        "row_label_regex": (
            r"\|\s*{month_num}月\s*\|\s*[\d.]+\s*\|\s*[\d.]+\s*\|\s*[\d.]+\s*\|\s*[\d.]+\s*\|\s*([\d.]+)"
        ),
        "notes": "Markdownテーブルから{month_num}月の全店客単価前年比 (round9)",
    },
    "food_super_existing_store_customer_spend_yoy": {
        "row_label_regex": (
            r"\|\s*{month_num}月\s*\|\s*[\d.]+\s*\|\s*[\d.]+\s*\|\s*[\d.]+\s*\|\s*[\d.]+\s*\|\s*[\d.]+\s*\|\s*([\d.]+)"
        ),
        "notes": "Markdownテーブルから{month_num}月の既存店客単価前年比 (round9)",
    },
})

# ------------------------------------------------------------------
# 8914 エリアリンク (現在 fields=0)
# 月次実績から稼働率を抽出
# ------------------------------------------------------------------
print("\n--- 8914 エリアリンク (fields追加) ---")
blob_8914 = bucket.blob("monthlydata/8914/extract_adapter.json")
try:
    data_8914 = json.loads(blob_8914.download_as_text(encoding="utf-8"))
except Exception:
    data_8914 = {"ticker": "8914", "source": "tdnet"}

data_8914["company_name"] = "エリアリンク株式会社"
data_8914["source"] = "tdnet"
data_8914["month_from_title_regex"] = "(?P<month>\\d{1,2})月度"
data_8914["fields"] = [
    {
        "key": "total_rooms",
        "name": "総室数",
        "row_label_regex": r"総室数:([\d,]+)室",
        "value_type": "integer",
        "notes": "ハローストレージ総室数 (round9)",
    },
    {
        "key": "occupied_rooms",
        "name": "稼働室数",
        "row_label_regex": r"稼働室数:([\d,]+)室",
        "value_type": "integer",
        "notes": "ハローストレージ稼働室数 (round9)",
    },
    {
        "key": "occupancy_rate_pct",
        "name": "稼働率(%)",
        "row_label_regex": r"稼働率:([\d.]+)%",
        "value_type": "float",
        "notes": "ハローストレージ全体稼働率(%) (round9)",
    },
    {
        "key": "existing_occupancy_rate_pct",
        "name": "既存稼働率(%)",
        "row_label_regex": r"既存稼働率:([\d.]+)%",
        "value_type": "float",
        "notes": "ハローストレージ既存物件稼働率(%) (round9)",
    },
    {
        "key": "new_occupancy_rate_pct",
        "name": "新規稼働率(%)",
        "row_label_regex": r"新規稼働率:([\d.]+)%",
        "value_type": "float",
        "notes": "ハローストレージ新規物件稼働率(%) (round9)",
    },
]
upload_adapter("8914", data_8914)

# ------------------------------------------------------------------
# 6561 (旅行系) 現在 fields=0
# 月次業績速報から前年比を抽出
# ------------------------------------------------------------------
print("\n--- 6561 (旅行系 - fields追加) ---")
blob_6561 = bucket.blob("monthlydata/6561/extract_adapter.json")
try:
    data_6561 = json.loads(blob_6561.download_as_text(encoding="utf-8"))
except Exception:
    data_6561 = {"ticker": "6561", "source": "tdnet"}

data_6561["source"] = "tdnet"
data_6561["month_from_title_regex"] = "(?P<month>\\d{1,2})月"
data_6561["fields"] = [
    {
        "key": "monthly_revenue_vs_prev_year_pct",
        "name": "前年比(%)",
        "row_label_regex": r"前年比.*?([\d.]+)%",
        "use_last_number": True,
        "value_type": "float",
        "notes": "月次業績速報の前年比(最新月) - use_last_number (round9)",
    },
]
upload_adapter("6561", data_6561)

# ------------------------------------------------------------------
# 9022 JR東海 (Q3決算資料 - 適切な月次データなし)
# 運輸業収益の前年比を代替指標として抽出
# ------------------------------------------------------------------
print("\n--- 9022 JR東海 (Q3決算代替指標) ---")
patch_adapter("9022", {
    "shinkansen_tokyo_gate_passengers_yoy": {
        "key": "transport_revenue_yoy",
        "row_label_regex": r"運輸業.*?(\d{2,3}\.\d)",
        "use_last_number": False,
        "value_type": "float",
        "notes": "Q3決算から運輸業収益前年比(%) - 月次開示ではなくQ3資料 (round9)",
    },
})

# ------------------------------------------------------------------
# 9519 レノバ (Q3決算補足資料 - 適切な月次データなし)
# 設備容量(GW)を代替指標として抽出
# ------------------------------------------------------------------
print("\n--- 9519 レノバ (Q3決算代替指標) ---")
patch_adapter("9519", {
    "renewable_energy_sales_kwh": {
        "key": "installed_capacity_gw",
        "row_label_regex": r"(\d+\.\d+)\s*GW\s*\(net",
        "value_type": "float",
        "notes": "Q3決算補足から運転中設備容量(GW) - 月次開示ではなくQ3資料 (round9)",
    },
})

print(f"\n=== 完了: {ok}件成功, {errors}件エラー ===")
