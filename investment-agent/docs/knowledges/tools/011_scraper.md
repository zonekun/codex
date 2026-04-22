# スクレイパー使用メモ

**カテゴリ**: tools
**作成日**: 2026-02-23
**ステータス**: 有効
**関連ファイル**: `src/collector/regional_exchange.py`, `scripts/update_regional_codes.py`, `C:\Users\zonekun\Dropbox\stock\script\claude-investment-agent.ps1`（ローカル実行メニュー）

---

## 新規スクレイパー開発の進め方

### 標準的な開発フロー

```
1. ページ構造の調査
   └─ ブラウザの開発者ツールで HTML 構造を確認
       → 静的 HTML か JS レンダリングかを判断（curl/requests で確認）
       → 静的なら requests + BS4 / JS なら Playwright

2. スクレイパー関数の実装
   └─ src/collector/<モジュール名>.py に追加
       → 戻り値は list[dict]（ticker, name, industry, market の dict）
       → ログは structlog で。print 禁止

3. ドライラン（--dry-run）で動作確認
   └─ BQ 書き込みなし。件数と内容を目視確認
       → 文字化けするので heredoc 形式で実行すること

4. BQ 本番ロード
   └─ load_to_bq() を呼ぶ（DELETE → WRITE_APPEND パターン）
       → ロード後に SELECT COUNT(*) で件数確認

5. data_catalog.md の更新
   └─ 対象テーブルの備考・更新スクリプト・実測データを更新する
       → 新テーブルの場合はスキーマ定義も追記
       → 既存テーブルに取引所を追加した場合は件数内訳を更新
```

### BQ ロードの設計パターン（DELETE → INSERT）

既存レコードを取引所単位で一括削除してから再挿入する。冪等性があり、再実行しても重複しない。

```python
# 既存レコードを削除
bq_client.query(f"DELETE FROM `{table_id}` WHERE EXCHANGE = '{exchange}'").result()

# 新規レコードを挿入（WRITE_APPEND）
job = bq_client.load_table_from_dataframe(df, table_id, job_config=job_config)
job.result()
```

### ページ構造の調査ポイント

| 確認事項 | 方法 |
|---------|------|
| 静的 / JS レンダリング | `curl URL` してコードが HTML に含まれるか確認 |
| テーブル構造 | `soup.find_all("table")` の数・ヘッダ行を確認 |
| ページネーション | URL パラメータ（`?page=N`）の有無 |
| 銘柄コード形式 | 4桁か5桁か。英字混在の有無 |
| 業種情報の有無 | 一覧ページに業種列があるか（ない場合は NULL） |

### よく遭遇するパターンと対処

| パターン | 対処 |
|---------|------|
| 1社 = 1 `<table>` | `soup.find_all("table")` でループ。列インデックスではなく th をキーにして辞書化 |
| `dl/dt/dd` 形式 | セクション見出しの `<dt>` を起点に `.find_next_sibling("dd")` で配下を取得 |
| JS レンダリング | Playwright で `page.goto(url, wait_until="networkidle")` → `page.content()` を BS4 に渡す |
| 検索フォームが必要 | フォーム送信後の URL を特定し直接アクセス（フォーム操作より安定） |
| コードに suffix あり | 正規表現で先頭 N 文字を取り出す |
| 業種名が TSE と異なる | `_ALIAS` 辞書でエイリアス変換 → `_NAME_TO_33` で 33業種コードに変換 |
| 東証重複銘柄の除外 | BQ から TSE の TICKER 一覧を取得してセット差分で除外 |

### Playwright 利用時の注意

```bash
# 事前インストール（プロジェクトごとに1回）
uv add playwright
uv run playwright install chromium

# 並列実行すると __dirlock エラーが出る
# → プロセスを終了させてから再実行する
```

- `sync_playwright` を使う（asyncio 不要のため）
- `wait_until="networkidle"` + `time.sleep(1)` でレンダリング完了を待つ
- `page.set_extra_http_headers({"Accept-Language": "ja-JP,ja;q=0.9"})` で日本語ページを取得

---

## 共通注意事項

### 文字コード（Windows Git Bash での実行）

Bash / Git Bash 上で日本語を含む Python スクリプトを実行すると、**ターミナル出力が文字化けする**。
BQ ロードや dry-run の結果確認など、日本語出力が必要な場合は以下のいずれかを使うこと。

```python
# 方法A: ヒアドキュメントで Python ファイルとして渡す（推奨）
uv run python << 'PYEOF'
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
# ... 日本語出力を含むコード ...
PYEOF

# 方法B: .py ファイルに書いて実行する
uv run python scripts/foo.py
```

`uv run python -c "..."` の形式（インラインコマンド）は文字化けするため避ける。

---

## 地方証券取引所スクレイパー

`src/collector/regional_exchange.py` に実装。
東証との重複上場銘柄を除いた**単独上場銘柄のみ**を取得し、`STOCK.STOCK_CODE_LIST` に登録する。

### 実行コマンド

```bash
# 全取引所を更新（FSE → SSE → NSE の順）
uv run python scripts/update_regional_codes.py

# 個別取引所
uv run python scripts/update_regional_codes.py --exchange FSE  # 福証
uv run python scripts/update_regional_codes.py --exchange SSE  # 札証
uv run python scripts/update_regional_codes.py --exchange NSE  # 名証（Playwright 必須）

# ドライラン（BQ 書き込みなし・標準出力に表示）
uv run python scripts/update_regional_codes.py --dry-run
uv run python scripts/update_regional_codes.py --exchange NSE --dry-run
```

### 事前準備（名証のみ）

名証（NSE）は JS レンダリングページのため Playwright が必要。

```bash
uv run playwright install chromium
```

Chromium v1208（約 280 MB）がインストールされる。一度やれば再実行不要。

---

### FSE（福証）

- **URL**: `https://www.fse.or.jp/listed/single.php`
- **手法**: requests + BeautifulSoup
- **ページ構造**: 1社 = 1 `<table>`。合計約 29 table が並ぶ
  - Row 0: `<th>業種</th><td>建設業</td><th>コード</th><td>1771</td>...` （th/td 交互ペア）
  - Row 1: `<th>会社名</th><td><a><img alt="会社名"/></a></td>`
- **取得データ**: TICKER, STOCK_NAME, 業種（TSE 33/17 変換済み）, 市場区分（本則 / Q-Board / Fukuoka PRO Market）
- **実績**: 29 件（2026-02-23）

### SSE（札証）

- **URL**: `https://www.sse.or.jp/listing/list`
- **手法**: requests + BeautifulSoup
- **ページ構造**: `dl/dt/dd` 形式
  - セクション見出し（`<dt>`）: "単独上場会社 - 本則市場" / "単独上場会社 - アンビシャス"
  - 銘柄エントリ（`<dl>`内）: `<dt>1449</dt><dd><a><img alt="株式会社FUJIジャパン"/></a></dd>`
- **取得データ**: TICKER, STOCK_NAME, 市場区分（本則市場 / アンビシャス）
- **業種情報なし**: 一覧ページに業種の記載がないため `INDUSTRY_*` はすべて NULL
- **実績**: 18 件（本則市場 10 件 + アンビシャス 8 件）（2026-02-23）

#### 取得できる SSE 単独上場銘柄（2026-02-23 時点）

| TICKER | 市場区分 | 銘柄名 |
|--------|---------|--------|
| 1449 | 本則市場 | 株式会社FUJIジャパン |
| 1832 | 本則市場 | 株式会社北海電工 |
| 2172 | 本則市場 | 株式会社インサイト |
| 2218 | 本則市場 | 日糧製パン株式会社 |
| 3055 | 本則市場 | 株式会社ほくやく・竹山ホールディングス |
| 4834 | 本則市場 | キャリアバンク株式会社 |
| 5579 | 本則市場 | 株式会社GSI |
| 8594 | 本則市場 | 中道リース株式会社 |
| 9027 | 本則市場 | 株式会社ロジネットジャパン |
| 9085 | 本則市場 | 北海道中央バス株式会社 |
| 2137 | アンビシャス | 株式会社光ハイツ・ヴェラス |
| 2928 | アンビシャス | RIZAPグループ株式会社 |
| 2976 | アンビシャス | 日本グランデ株式会社 |
| 353A | アンビシャス | エレベーターコミュニケーションズ株式会社 |
| 3849 | アンビシャス | 日本テクノ・ラボ株式会社 |
| 3977 | アンビシャス | フュージョン株式会社 |
| 5039 | アンビシャス | 株式会社キットアライブ |
| 7118 | アンビシャス | 株式会社伸和ホールディングス |

### NSE（名証）

- **URL**: `https://www.nse.or.jp/listing/search/list.html?...&page={N}`
- **手法**: Playwright（headless Chromium）+ BeautifulSoup
- **理由**: 検索フォームが JS レンダリング。静的 URL にアクセスすることで直接結果を取得
- **ページ構造**: `<table>` 形式。ヘッダ: `[単独, 銘柄名（コード）, 市場区分, 業種, 決算期, 売買単位]`
- **コード形式**: 4文字コード + `0` suffix（例: TSE `138A` → NSE 表示 `138A0`）。先頭4文字を TICKER とする
- **ページネーション**: 1ページ最大100件。100件未満になったら最終ページ。全315件 / 4ページ
- **取得データ**: TICKER, STOCK_NAME, 業種（TSE 33/17 変換済み）, 市場区分（プレミア / メイン / ネクスト）
- **業種 NULL になるケース**: ETF 等は業種欄が "－" → NULL として扱う
- **実績**: 315件取得 → 東証重複 256件除外 → 59件を NSE 単独として登録（2026-02-23）
