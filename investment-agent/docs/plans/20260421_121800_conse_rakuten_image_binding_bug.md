**作成日時**: 2026-04-21 12:18 JST
**対象ファイル**: `scripts/update_conse_rakuten.py`（current HEAD `d0e9eb0` + 本セッション未コミット diff — google-genai SDK 移行済み）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: 画像認証が「サソリ」のキーワードで「ホットドッグ」等まったく無関係な絵を選択する重度のミスマッチを解消する。意味分類のエラーではなく、**キーワードと画像 id の binding が崩れている**構造バグの疑いが濃厚。

## 前提サマリ

- 基準 commit: `d0e9eb0`（`scripts/update_conse_rakuten.py` は本セッションで google-genai SDK 移行済み、未コミット）
- 観測: 「サソリ」キーワードで明らかに別物（ホットドッグ）の画像を選んだ。**ユーザー評価は「迷うことのない画像一覧」** → Gemini の意味分類誤りではなく、id binding のずれ
- 現状の contents 構造:
  ```python
  contents = [prompt_text, *emoji_images]  # text + PIL Image × 10
  ```
  プロンプト側で「画像は提示順に id=0..9」と宣言しているが、**位置順とモデル内部の並び順が一致している保証は弱い**（特に新モデル `gemini-3-flash-preview` の multimodal 挙動は未検証）
- 直前のセッションで `google.generativeai` → `google-genai` に移行、モデルを `gemini-2.5-flash` → `gemini-3-flash-preview` に変更済み
- 移行前（`gemini-2.5-flash` + regex パース）でも「範囲外 10」を返す事象はあったが、**keyword ミスマッチまでは報告されていなかった** → モデル変更か SDK 切替か contents 形式かのいずれかで新規発症の可能性
- 実機検証: 再現取得済み（ユーザー目視）/ デバッグ PNG 保存は未実施

## 優先度の定義

- **P0**: ログインが通らない・誤認証でアカウント停止リスク（楽天証券は連続失敗でロック）
- **P1**: 精度改善・デバッグ性向上
- **P2**: モデル選定検証（gemini-2.5-flash との比較）

## P0-1. id binding を明示的にインターリーブする 🔗

**症状**: キーワード「サソリ」に対しホットドッグの画像（どう見ても別物）を選択。`EmojiChoice.id` の範囲は 0-9 に収まっているので schema 違反ではなく、**Gemini が返した id と emoji_buttons[id] の対応が崩れている**。

**該当**: `scripts/update_conse_rakuten.py:260-273`

```python:L260-L273
prompt_text = "\n".join([
    "【タスク】",
    f"これから並べて提示する画像の中から、単語「{alt_text}」を表すイラストとして最も適切なものを 1 枚だけ選んでください。",
    "",
    "【ルール】",
    "- 画像は提示順に id=0, id=1, id=2, id=3, id=4, id=5, id=6, id=7, id=8, id=9 が割り当てられています。",
    ...
])

contents = [prompt_text, *emoji_images]
```

**根本原因の仮説**:
1. `gemini-3-flash-preview` の multimodal では、複数画像を並べて渡したときの「位置順 → id」対応が不確実。モデル内部では画像を「集合」として扱い、位置情報が弱い可能性
2. 「提示順に id=0..9」という宣言文だけでは、モデルが各画像と id の binding を明示的に関連付けられない
3. （副次）google-genai SDK が PIL Image を受け取る際、`contents` 内の挿入順を API リクエストでそのまま保持しているかは未検証

**修正方針**: **contents をインターリーブ構造に変更**。各画像の直前に「image id=N:」の短いテキストパートを差し込むことで、id ↔ 画像の対応を入力ストリームで明示する。

Before:
```python
contents = [prompt_text, *emoji_images]
```

After:
```python
contents = [prompt_text]
for i, img in enumerate(emoji_images):
    contents.append(f"image id={i}:")
    contents.append(img)
contents.append(f"上記 10 枚の中から、単語「{alt_text}」に対応する id を選んで {{\"id\": N}} 形式で返してください。")
```

**呼び出し側への波及**: 無し（`perform_image_authentication` 内部）

**検証**:
1. `raku.txt` に3キーワード書いて実行 → `raw response: {"id": N}` 形式でログ出力、かつ実際にクリックされた画像 index の **PNG を目視確認**（P0-2 の保存が前提）
2. 意味的に明らかに異なるキーワード（サソリ / ホットドッグ / サクラ 等）で10回試行 → 的中率が 50% 以上（position binding が効いていれば実質 90%+ 期待）

**ロールバック**: `contents = [prompt_text, *emoji_images]` に戻す。

## P0-2. デバッグ用 PNG 保存（検証前提） 🗂️

**症状**: 現状、取得した emoji 画像の中身を確認する手段が無い。「Gemini が間違った」のか「そもそもキャプチャが壊れている」のかを切り分けられない。

**該当**: `scripts/update_conse_rakuten.py:242-250`（画像キャプチャ部）

```python:L242-L250
for i in range(10):
    element_id = f"emoji_{i}"
    try:
        button = driver.find_element(By.ID, element_id)
        emoji_buttons.append(button)
        img_element = button.find_element(By.TAG_NAME, "img")
        png_data = img_element.screenshot_as_png
        image = Image.open(io.BytesIO(png_data))
        emoji_images.append(image)
```

**根本原因**: `img_element.screenshot_as_png` が画像読み込み完了前に発火すると空 PNG になる可能性あり。また、`img` タグではなく親 div のサイズで切り取られる場合もある。

**修正方針**: 起動時に `C:\tmp\rakuten_auth_<yyyymmdd_HHMMSS>\` を作成し、`emoji_0.png`〜`emoji_9.png` を保存。Gemini の判定結果と突き合わせるための参考資料とする。

Before:
```python
png_data = img_element.screenshot_as_png
image = Image.open(io.BytesIO(png_data))
emoji_images.append(image)
```

After:
```python
png_data = img_element.screenshot_as_png
image = Image.open(io.BytesIO(png_data))
emoji_images.append(image)
# デバッグ: PNG をローカルに保存
if _debug_dir is not None:
    image.save(os.path.join(_debug_dir, f"emoji_{i}.png"))
```

モジュール上部に:
```python
from datetime import datetime
_DEBUG_ROOT = r"C:\tmp"
_debug_dir = os.path.join(_DEBUG_ROOT, f"rakuten_auth_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
os.makedirs(_debug_dir, exist_ok=True)
```

CLAUDE.md §「ローカルDL保存先」規約に従い `C:\tmp\` 配下に出力。検証完了後は手動で削除する。

**呼び出し側への波及**: 無し。環境依存（`C:\tmp` の存在前提。既に他のスクリプトで使用しているため存在は保証される）

**検証**:
1. `raku.txt` に1キーワード書いて実行 → `C:\tmp\rakuten_auth_<ts>\emoji_0.png`〜`emoji_9.png` の10枚が保存される
2. 全 PNG を秀丸/エクスプローラで確認し、**人間の目で見て各画像が区別可能かつ emoji_N.png の N と楽天の id=N が一致しているか**を確認
3. 1 と Gemini の選択結果（ログ: `🤖 Geminiの判定: ID N が 'キーワード' です`）を突き合わせ、binding ずれか意味分類ミスかを切り分け

**ロールバック**: `image.save(...)` 行と `_debug_dir` 初期化を削除。既存の PNG ファイルは手動削除。

## P1-1. モデル切り戻し検証（別枠） 🔄

**症状**: `gemini-3-flash-preview` は新モデルで multimodal 挙動の実績が薄い。P0-1 適用後も精度が出ない場合、モデル側の問題を疑う必要がある。

**該当**: `scripts/update_conse_rakuten.py:58`

```python:L58
GEMINI_MODEL   = "gemini-3-flash-preview"
```

**根本原因**: メモリ `feedback_gemini_3_flash_local.md` は**月次チェック**用の指示。画像認証は月次チェックではないため、より実績のある `gemini-2.5-flash`（前バージョン）を使う選択肢がある。移行前のバージョン（`gemini-2.5-flash` + regex）では keyword ミスマッチは観測されていなかった。

**修正方針**: P0-1 / P0-2 適用後も精度が改善しない場合、`GEMINI_MODEL = "gemini-2.5-flash"` に切り戻して比較する。切戻し時は SDK は google-genai のまま維持（SDK が原因の可能性は低いため）。

Before/After:
```python
# P1-1 適用時
GEMINI_MODEL = "gemini-2.5-flash"  # P0 で改善しない場合の fallback
```

**呼び出し側への波及**: 無し

**検証**: P0-1 適用下で 10 キーワード試して 5 件以下しか当たらなければ P1-1 も適用して再テスト。

**ロールバック**: 元に戻す（1 行の変更）。

## P2-1. 画像への id 焼き込み（最終手段） 🎨

**症状**: P0-1 (インターリーブ) + P1-1 (モデル切戻し) でも binding ずれが残る場合の最終手段。

**該当**: `scripts/update_conse_rakuten.py:242-250`（画像キャプチャ後の前処理ステップ追加）

**根本原因**: 位置・テキスト宣言のいずれの手法でも id ↔ 画像の対応が崩れる場合、画像自体に id を視覚的に焼き込むのが最も頑健。

**修正方針**: `PIL.ImageDraw` で各 emoji 画像の左上に id 番号（0-9）を赤字で描画してから Gemini に渡す。プロンプトを「画像内に書かれた数字をそのまま id として返してください」に変更。

Before:
```python
emoji_images.append(image)
```

After:
```python
annotated = image.copy()
draw = ImageDraw.Draw(annotated)
draw.text((2, 2), str(i), fill="red")  # font 指定省略時はデフォルト
emoji_images.append(annotated)
```

**呼び出し側への波及**: プロンプトテキストも「画像内の数字を読んで返してください」形式に合わせる必要あり。

**検証**: P0/P1 で未解決時のみ適用。数字焼き込みで的中率が跳ね上がれば multimodal の ordinal 認識が弱いと断定できる。

**ロールバック**: annotate 部を削除して元のプロンプトに戻す。

## 対応アンチパターン

| plan ID | 004 | T-x | G-x |
|---|---|---|---|
| P0-1 | — | T-6 | G-3 |
| P0-2 | — | — | — |
| P1-1 | — | T-6 | — |
| P2-1 | — | T-6 | — |

（タグは 004-1 のカタログに合わせて後で追記）

## 検証戦略

1. **smoke test**: `raku.txt` に 3 キーワード（意味的に明らかに違うもの、例: サソリ / サクラ / クジラ）書いて起動。P0-1 (インターリーブ) + P0-2 (PNG保存) を適用後、`C:\tmp\rakuten_auth_<ts>\emoji_N.png` を目視確認し、Gemini が選んだ id が妥当かチェック
2. **dev 実機**: 10 キーワード分のテストパターン（明確に別物な絵柄）を用意し、的中率を計測。50% 未満なら P1-1 モデル切戻し、70% 未満なら P2-1 焼き込み
3. **本番適用判断基準**: smoke で 10 連続正解を確認してから楽天ログイン本番で実行。**失敗時はアカウントロックのリスク**があるため、smoke 未通過では prod 禁止
4. **回収手順**: 誤クリックが本番で発生した場合、即座に認証ボタンを押さず画面を閉じて手動ログインに切替。`git checkout scripts/update_conse_rakuten.py` で `d0e9eb0` 状態に戻すとログイン自体は可能（旧 regex 版、10 問題は残る）

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/022_conse_rakuten.md`（§「画像認証への対応」）
- CLAUDE.md: 「Gemini ライブラリ」「Gemini 応答安定化」
- Memory:
  - `feedback_gemini_prompt_first.md`（プロンプト＋schema で固める方針）
  - `feedback_gemini_3_flash_local.md`（ローカルは gemini-3-flash-preview、ただし**月次チェック限定**）
- 関連 plan: `docs/plans/20260421_120304_conse_rakuten_response_schema_fix.md`（SDK 移行 + schema 化。本プランはその後続バグ対応）
- 関連 commit: `d0e9eb0`（現 HEAD）、本セッション未コミット diff
- 実機ログ: 2026-04-21 ユーザー報告（「サソリ」で「ホットドッグ」を選択）
