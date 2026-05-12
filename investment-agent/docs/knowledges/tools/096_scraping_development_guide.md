# スクレイピング新規開発ガイド

**カテゴリ**: tools
**作成日**: 2026-05-05
**ステータス**: 有効
**根拠**: QUICKコンセンサス取得スクリプト開発時の事故・教訓（セッション 2026-05-05）

## 概要

証券サイト等のスクレイピングスクリプトを新規開発する際の手順・落とし穴・ツール選定ガイド。

---

## ツール選定

| ツール | 用途 | 判断基準 |
|--------|------|---------|
| Selenium + Edge | 証券サイト（bot検知あり・既存プロファイル必要） | ログイン済みCookie・OTP認証・フレームセット |
| Selenium + Chrome | 証券サイト（Chrome専用セッション） | SeleniumProfile等で運用中のもの |
| requests | 認証不要 or Cookie手動セットで十分なサイト | IFIS株予報のようなシンプルなHTML |
| Playwright | **本プロジェクトでは使わない** | bot検知が厳しいサイトでは不利。元ネタがSeleniumならSelenium |

### Playwright を採用しない理由（2026-05-05 確定）

- 本プロジェクトの証券サイト操作は全て Selenium + 既存ブラウザプロファイル方式
- Playwright は独自ブラウザインスタンスを起動するため、既存ログインセッション・プロファイルの引き継ぎが困難
- 元ネタ（`OrderForMABatch.py` 等）が Selenium で書かれており、認証系コードの移植ルール上 Selenium を維持する必要がある
- ChatGPT 等が Playwright codegen を提案することがあるが、本プロジェクトでは不採用

---

## 開発手順（推奨フロー）

### Phase 1: 調査・テストスクリプト

1. **元ネタの認証コードを特定し、そのままコピーする**（CLAUDE.md §既存コード移植ルール）
2. **録画（codegen）ではなく、手動操作＋HTMLダンプで正解ルートを確認する**
3. テストスクリプトを作成し、1銘柄で全ステップを通す
4. 各ステップで HTMLダンプ を取得し、セレクタ・フレーム構造を正確に把握する

### Phase 2: 本番スクリプト

5. IFIS スクリプトの構造（structlog・Counter・再開・CLI引数）をベースに構築
6. テストスクリプトで確認済みのセレクタ・遷移ロジックをそのまま移植
7. dry-run → 2-3銘柄テスト → 全件実行

---

## フレームセットサイトの攻略

### 鉄則: 必ずHTMLダンプを取ってからセレクタを書く

フレームセットサイト（松井証券リサーチネット等）では、**推測でセレクタを書くと確実に失敗する**。

```python
def dump_all_frames(driver, prefix: str) -> None:
    """現在のウィンドウの全フレームをダンプ。"""
    driver.switch_to.default_content()
    frames = driver.find_elements(By.TAG_NAME, "frame") + driver.find_elements(By.TAG_NAME, "iframe")
    frame_names = []
    for i, f in enumerate(frames):
        name = f.get_attribute("name") or f"frame{i}"
        frame_names.append(name)

    for name in frame_names:
        driver.switch_to.default_content()
        driver.switch_to.frame(name)
        html = driver.page_source
        with open(rf"C:\tmp\{prefix}_{name}.html", "w", encoding="utf-8") as f:
            f.write(html)
```

### 落とし穴

| 問題 | 原因 | 対処 |
|------|------|------|
| `NoSuchFrameException` | ページ遷移後にフレームが変わっている | ダンプで現在のフレーム構成を確認 |
| `StaleElementReferenceException` | frame要素を保持したまま別frameに切り替えた | **frame名リストを先に収集し、名前で再検索** |
| 要素が見つからない | 対象が別フレーム内にある | ダンプHTMLを grep して正しいフレームを特定 |
| 検索ボックスの位置変化 | 初回（フレームなし）と2回目以降（frameset）で構造が異なる | `find_elements("frame")` で判定し分岐 |

### フレーム操作の定石

```python
# 毎回 default_content() に戻ってからフレームに入る
driver.switch_to.default_content()
driver.switch_to.frame("target_frame_name")

# ネストフレームの場合は段階的に
driver.switch_to.default_content()
driver.switch_to.frame("parent_frame")
driver.switch_to.frame("child_frame")
```

---

## セレクタ選定の原則

### テキストベースのセレクタは使わない

```python
# ❌ 銘柄名はコードごとに異なる
link = driver.find_element(By.XPATH, "//a[contains(., '東電力HD')]")

# ✅ href属性でコード指定（全銘柄で動作する）
link = driver.find_element(By.XPATH,
    f"//a[contains(@href, 'report_summary') and contains(@href, 'rcode={ticker}')]")
```

### 録画コード（codegen）のセレクタを鵜呑みにしない

Playwright codegen や手動で特定した要素のセレクタが、**別の銘柄・別のページ状態で動くとは限らない**。ダンプHTMLで汎用性を確認してから採用する。

---

## 認証系コードの移植ルール（再掲）

CLAUDE.md に明記されているが、事故が多いため再掲:

1. 元ネタのコードを Read し、**関数単位でそのままコピー**する
2. ブラウザ種別・ライブラリ・API・オプション・タイムアウト値を**一切変更しない**
3. 元ネタにない処理（wait、デバッグ出力、エラーハンドリング等）を**追加しない**
4. 技術的に「より良い」代替手段があっても**採用しない**
5. 変更が必要と判断した場合は、**実装前にユーザーに確認**する

### 事故事例（2026-05-05）

- `login_matsui()` を「移植」と称しながら独自にセレクタを書き換え → 口座ロック2回
- OTP認証後の画面遷移待ちを省略 → フレーム未ロードで後続処理全滅
- **教訓**: 認証系は1回の失敗でロックの可能性がある。テスト実行前にユーザー確認が必要

---

## bot検知対策

### 必須（既存プロファイル使用時）

```python
# setup_driver() 内（元ネタからコピー）
options.add_argument('--disable-blink-features=AutomationControlled')
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option('useAutomationExtension', False)
```

### 追加（ドライバ起動後）

```python
# navigator.webdriver フラグ隠蔽
driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
    "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
})
```

### アクセス間隔

- 銘柄間: 1秒以上（`time.sleep(1)`）
- ページ遷移: 2秒以上（`time.sleep(2)`）
- 実質 1銘柄あたり 8-10秒以上が安全圏（RAKU/IFIS同等）

---

## ウィンドウ管理（別ウィンドウが開く場合）

```python
handles_before = set(driver.window_handles)

# ... ボタンクリックで別ウィンドウが開く操作 ...
time.sleep(2)

# 新しいウィンドウに切り替え
new_windows = set(driver.window_handles) - handles_before
if new_windows:
    driver.switch_to.window(new_windows.pop())

# データ取得後、新しいウィンドウを閉じて元に戻る
# finally ブロックで確実に実行
for h in set(driver.window_handles) - handles_before:
    driver.switch_to.window(h)
    driver.close()
driver.switch_to.window(original_handle)
```

---

## テスト実行のプロトコル

### バージョン保管（git使うまでもない段階）

```python
# 実行前にコピー
cp "scripts/test_xxx.py" "C:/tmp/test_xxx_YYYYMMDD_HHMMSS.py"

# 正常終了したら全削除
rm -f C:/tmp/test_xxx_*.py
```

### 段階的検証

1. **ログインのみ** → OTP通過確認
2. **画面遷移のみ** → 各ステップでダンプ取得
3. **データ抽出のみ** → 1銘柄で正規表現・セレクタ確認
4. **エラーケース** → 存在しない銘柄でスキップ確認
5. **複数銘柄** → 2回目以降の検索が安定するか確認

---

## 正規表現によるデータ抽出

BeautifulSoup で十分な場合はそちらを使うが、JavaScriptで描画されるサイト（`tableTdI()` 等のJS関数呼び出し）ではHTMLソースから正規表現で抽出する。

### 注意点

- **前年比（YoY）行と実数行の区別**: 小数点の有無で判定（例: 売上高に `.` があればYoY行）
- **`re.DOTALL` を使う**: HTML内の改行・空白をまたぐパターンに対応
- **グループの意味をコメントしない**: 変数名で意味を表す（`revenue_raw = m.group(2)`）

---

## 事故を防ぐチェックリスト

- [ ] 認証コードは元ネタから関数単位でコピーしたか？独自改変していないか？
- [ ] フレーム構成をHTMLダンプで確認したか？推測でframe名を書いていないか？
- [ ] セレクタは特定銘柄のテキストに依存していないか？
- [ ] 初回と2回目以降でページ構造が変わる場合に対応しているか？
- [ ] 別ウィンドウの開閉を finally で確実に処理しているか？
- [ ] アクセス間隔は十分か（1銘柄8秒以上）？
- [ ] bot検知対策は入っているか？
- [ ] OTP/認証系のテスト実行前にユーザーに確認したか？
