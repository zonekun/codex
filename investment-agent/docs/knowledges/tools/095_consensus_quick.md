# QUICKコンセンサス取得スクリプト（松井証券リサーチネット経由）

**カテゴリ**: tools
**作成日**: 2026-05-05
**更新日**: 2026-05-05
**ステータス**: Phase 2 完了（BQ再構成 + RAKU廃止 + 読み取り側v3対応）
**関連ファイル**: `scripts/update_conse_quick.py`（本番）、`scripts/test_conse_quick.py`（テスト）

## 概要

QUICKコンセンサス予想を松井証券リサーチネット経由でスクレイピング取得し、BQ `STOCK.CONSENSUS` テーブルに INSERT する。IFISが経常利益のみなのに対し、QUICKは売上高・営業利益・経常利益・純利益・EPSの5項目を取得できる。

**RAKU廃止済み（2026-05-05）**: `update_conse_rakuten.py` は削除済み。QUICK + IFIS の2ソース体制に移行完了。

| 項目 | 内容 |
|------|------|
| ソース | QUICK（松井証券リサーチネット印刷フォーマット経由） |
| スクリプト | `scripts/update_conse_quick.py` |
| 取得方法 | Selenium（Edge + 既存プロファイル + SMS OTP認証） |
| 取得項目 | 売上高, 営業利益, 経常利益, 純利益, EPS |
| 年度 | [CON]行に存在する全予想期（当期・来期・再来期） |
| SOURCE列 | `QUICK` |
| 出力先 | BQ `STOCK.CONSENSUS`（streaming insert） |
| PERIOD_REL判定 | BQには保存しない。下流スクリプトがFIN_STATEMENTSルックアップで判定（C案） |

## 取得項目

印刷フォーマットページの `[CON]` マーカー行から以下5項目を抽出:

| # | 項目 | HTMLパターン | 単位 |
|---|------|-------------|------|
| 1 | 売上高 | `tableTdI("6,449,900")` 1番目 | 百万円 |
| 2 | 営業利益 | `tableTdI("304,400")` 2番目 | 百万円 |
| 3 | 経常利益 | `tableTdI("326,800")` 3番目 | 百万円 |
| 4 | 純利益 | `tableTdI("306,100")` 4番目 | 百万円 |
| 5 | EPS | `tableTdI("191.04")` 5番目 | 円 |

前年比データ（売上高が小数点付き）は除外し、実数データのみ抽出する。

## 実行方法

```bash
# 通常実行（中断時は自動再開）
PYTHONUTF8=1 python scripts/update_conse_quick.py

# 強制新規実行（再開状態を破棄）
PYTHONUTF8=1 python scripts/update_conse_quick.py --fresh

# 特定銘柄のみ
PYTHONUTF8=1 python scripts/update_conse_quick.py --ticker 7203,9984

# dry-run
PYTHONUTF8=1 python scripts/update_conse_quick.py --dry-run
```

随時実行メニューにも追加（RAKU/IFIS同等）。

## 画面遷移フロー

1. **ログイン**: `https://www.deal.matsui.co.jp/ITS/login/MemberLogin.jsp` → ID/PASS入力 → ログインボタン
2. **SMS OTP認証**: OTPファイル待ち（`OTP_FILE_PATH`）→ `authNo` 入力 → 「認証する」クリック
3. **リサーチネット起動**: GMフレーム「情報検索」→ LMフレーム「リサーチネット」→ CTフレーム `img[alt='起動する']` → 別ウィンドウ
4. **銘柄検索**: `#text input[type='text']` にTICKER入力 → `input[alt='検索']` クリック
5. **銘柄選択**: `qrn_frame_main` 内の `a[href*='report_summary'][href*='rcode=TICKER']` クリック
6. **決算・財務**: `qrn_frame_main` 内の「決算・財務」リンククリック
7. **印刷フォーマット**: `qrn_frame_main` 内の「印刷フォーマット表示」ボタン → 別ウィンドウ
8. **データ抽出**: `[CON]` マーカー行から `tableTdI()` の値を正規表現で抽出

### フレーム構成

松井証券本体:
- `GM` — グローバルメニュー
- `LM` — ローカルメニュー
- `CT` — コンテンツ（リサーチネット起動ボタン）

リサーチネット（別ウィンドウ）:
- `qrn_frame_menu` — メニュー
- `qrn_frame_main` — メインコンテンツ（検索結果・銘柄情報・決算データ）
- `qrn_bg_left`, `qrn_bg_right` — 背景

## 銘柄リスト取得

RAKU/IFISと同一方式: JPX上場銘柄一覧Excelからプライム・スタンダード・グロースの内国株式を取得（約3768銘柄）。詳細は `022_consensus_load.md` §銘柄リスト取得 を参照。

## 出力先

### BQ `STOCK.CONSENSUS`（実装済み）

| カラム | 値 |
|--------|-----|
| DATAAT | 取得日（YYYY-MM-DD） |
| TICKER | 銘柄コード（4桁） |
| FY | 決算期（YYYYMM） |
| QUARTER | `FY` 固定 |
| REVENUE | 売上高（百万円、NULL可） |
| OP_PROFIT | 営業利益（百万円、NULL可） |
| ORD_PROFIT | 経常利益（百万円、NULL可） |
| NET_PROFIT | 純利益（百万円、NULL可） |
| EPS | EPS（円、NULL可） |
| SOURCE | `QUICK` 固定 |

### merged CSV（実装完了）

`run()` 末尾で `export_consensus_csv(bq_client, CSV_PATH)` を呼び出し、`V_CONSENSUS_MERGED` VIEW → C案 PERIOD_REL 判定 → ヘッダなし cp932 6列CSV を出力する。出力先: `C:\Users\zonekun\Dropbox\stock\py\conse_quick.csv`。判定ロジックは `lib_conse_csv_from_view.py` に実装（`fin_summary` の `MAX(CURRENT_FISCAL_YEAR_END_DATE) WHERE TYPE_OF_CURRENT_PERIOD='FY'` でCURRENT/NEXT判定）。

## 再開機能

RAKU/IFISと同一方式:

| 項目 | 内容 |
|------|------|
| 状態ファイル | `data/logs/conse_quick_resume.json` |
| 形式 | `{"dataat": "2026-05-05", "last_ticker": "7203"}` |
| 動作 | 起動時に状態ファイルがあれば `last_ticker` の次から再開 |
| 完了時 | 状態ファイルを自動削除 |
| `--fresh` | 状態ファイルを無視・削除して新規実行 |

## エラーハンドリング

- **検索結果なし**: 銘柄がリサーチネットに存在しない場合はスキップ（正常続行）
- **[CON]データなし**: 印刷フォーマットに[CON]行がない場合はスキップ
- **ページ遷移エラー**: フレーム切替・要素検索の失敗時はスキップしてログ出力
- **セッション切断**: リサーチネットのセッション切れは再起動（要検討）

## 前提条件

- Edge + EdgeDriver インストール済み
- 既存Edgeプロファイル（`C:\Users\zonekun\AppData\Local\Microsoft\Edge\User Data`）
- 松井証券口座（SMS OTP認証）
- OTPファイル自動連携: `C:\Users\zonekun\Dropbox\アプリ\kabucom\matsui.txt`

## ログイン関数の移植元

`C:\Users\zonekun\Dropbox\stock\py\OrderForMABatch.py` の `setup_driver()` と `login_matsui()` をそのまま使用。変更禁止（CLAUDE.md §既存コード移植ルール・§認証系コードの特別扱い）。

## RAKU廃止完了（2026-05-05）

以下すべて実施済み:

1. [x] `update_conse_rakuten.py` 削除（git rm）
2. [x] 随時メニューからRAKU項目削除（PS1メニュー番号振り直し）
3. [x] `022_consensus_load.md` 更新（QUICK/IFIS 2ソース体制）
4. [x] `data_catalog.md` 更新（新スキーマ5項目、VIEW仕様）
