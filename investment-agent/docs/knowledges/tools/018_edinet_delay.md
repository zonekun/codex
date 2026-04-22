# edinet_delay.py — EDINET 大量保有変更報告書 遅延提出チェック

**カテゴリ**: tools
**作成日**: 2026-03-02
**ステータス**: 有効
**関連ファイル**: `scripts/edinet_delay.py`, `scripts/notify.py`

## 概要

EDINET の大量保有変更報告書（docTypeCode=350）のうち、義務発生日から提出日まで
60日以上経過した遅延提出を検索し、結果をメール送信および Dropbox の Excel に追記する
スタンドアロンの情報収集ツール。

**位置づけ**: 株価アイデア分析・バックテスト・データカタログとは独立した監視ツール。

---

## 実行環境

| 環境 | 動作 |
|------|------|
| Google Colab（個人） | ✅ |
| Google Colab Enterprise | ✅ |
| Cloud Run Job | ✅（`edinet-delay`） |

---

## Cloud Run Job 情報

| 項目 | 値 |
|------|-----|
| Job 名 | `edinet-delay` |
| Artifact Registry | `us-west1-docker.pkg.dev/gmailpj-357912/edinet/edinet-delay` |
| Dockerfile | `docker/Dockerfile.edinet-delay` |
| cloudbuild | `cloudbuild/cloudbuild.edinet-delay.yaml` |
| メモリ | 512Mi |
| タイムアウト | 600s（10分）|
| スケジュール | 毎営業日（月〜金）18:30 JST（`edinet-delay-daily`） |

```bash
# 即時実行（今日分・デフォルト MODE 3）
gcloud run jobs execute edinet-delay --region us-west1

# ログ確認
gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=edinet-delay" \
  --limit=30 --format="value(textPayload)" --project=gmailpj-357912
```

---

## 設定項目（スクリプト冒頭）

| 変数 | デフォルト | 説明 |
|------|-----------|------|
| `SEARCH_MODE` | `3` | 1=特定日付 / 2=範囲 / 3=今日 |
| `SPECIFIC_DATE` | `"2025/12/17"` | MODE 1 用 |
| `START_DATE` / `END_DATE` | `"2025/01/01"` / `"2025/12/17"` | MODE 2 用 |
| `DELAY_THRESHOLD_DAYS` | `60` | 遅延とみなす日数（義務発生日→提出日） |
| `EDINET_API_KEY` | 環境変数 or ハードコード | EDINET API キー |
| `DBX_APP_KEY` 等 | ハードコード | Dropbox OAuth2 認証情報 |
| `DBX_FILE_PATH` | `/stock/AI分析優待/Edinet遅延.xlsx` | Dropbox 上の Excel パス |

---

## 依存パッケージ

```
requests>=2.31
dropbox>=11.36
openpyxl>=3.1
```

標準ライブラリ: `xml.etree.ElementTree`, `zipfile`, `io`, `os`, `sys`, `traceback`, `datetime`

---

## メール通知フロー

```
[開始] send_mail("[EDINET_DELAY] 開始", ...)
  ↓
  処理（EDINET API → XBRL解析 → 遅延判定）
  ↓
[結果] send_mail("EDINET遅延報告 {日付} ({件数}件)", ...) ← 別便
       update_dropbox_excel(...)
  ↓
[完了] send_mail("[EDINET_DELAY] 完了 ...", ..., attachment_text=log_text) ← 別便
```

エラー時:
```
[エラー] send_mail("[EDINET_DELAY] エラー", ..., attachment_text=log_text)
```

- 処理結果メールと[完了]メールは**別便**（意図的な設計）
- メール送信・ログキャプチャは `notify.py` の共通関数を使用

---

## Excel 追記フォーマット

| 列 | 内容 |
|----|------|
| A | 銘柄コード（4桁） |
| B | 発行体名 |
| C | 提出者名（大量保有者） |
| D | 義務発生日（YYYY/MM/DD） |
| E | 提出日（YYYY/MM/DD） |
| F | 遅延日数（例: `65日`） |

既存ファイルの書式・コメントは維持される（openpyxl の `load_workbook` で読み込み後に追記）。
