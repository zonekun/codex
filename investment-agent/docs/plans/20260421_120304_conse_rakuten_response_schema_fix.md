**作成日時**: 2026-04-21 12:03 JST
**対象ファイル**: `scripts/update_conse_rakuten.py`（435 行、commit `d0e9eb0` + 作業ツリー未コミット変更）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: 画像認証 Gemini 呼出しの `response_schema` を legacy SDK 互換形式に修正し、`TypeError: bad argument type for built-in operation` でログインが落ちる事象を解消する。

## 前提サマリ

- 基準 commit: `d0e9eb0`（`scripts/update_conse_rakuten.py` は作業ツリーに未コミット変更あり）
- 直前の改修（本セッション）で `response_schema` に `{"type": "integer", "enum": [0..9]}` を指定 → `protos.Schema` 構築時に `TypeError`
- インストール済み `google.generativeai`（`C:\Users\zonekun\AppData\Roaming\Python\Python312\site-packages\google\generativeai\...`）の `Schema` proto では、`enum` フィールドは `repeated string` 専用。INTEGER 型の enum は proto 側で非対応（`List[int]` を渡すと marshal が落ちる）
- エラートレース（抜粋）:
  ```
  File "google/generativeai/types/generation_types.py", line 202, in _normalize_schema
      generation_config["response_schema"] = protos.Schema(response_schema)
  ...
  TypeError: bad argument type for built-in operation
  ```
- 実機検証: 未（Selenium 起動 → 楽天証券ログイン → 画像認証発火が前提。ユーザー貼付スタックトレースで再現確認済み）
- 当初の事象（本修正の起点）: Gemini が `範囲外 10` を返す頻度が上がったため、プロンプト＋`response_schema` 化で固めようとした

## 優先度の定義

- **P0**: ブロッカー（スクリプト起動時に `TypeError` で落ちる。ログインフロー全停止）
- **P1**: `google-genai` SDK へのマイグレーション（CLAUDE.md 準拠。P0 確定後に別セッションで対応）

## P0-1. response_schema を STRING enum に変更 🔧

**症状**: `model = genai.GenerativeModel('gemini-2.5-flash', generation_config=generation_config)` の時点で `TypeError: bad argument type for built-in operation`。`perform_image_authentication` 冒頭で例外 → `login()` がこの先に進めず → スクリプト終了。

**該当**: `scripts/update_conse_rakuten.py:221-237`

```python:L221-L237
generation_config = {
    "response_mime_type": "application/json",
    "response_schema": {
        "type": "object",
        "properties": {
            "id": {
                "type": "integer",
                "enum": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
            },
        },
        "required": ["id"],
    },
}
model = genai.GenerativeModel(
    'gemini-2.5-flash',
    generation_config=generation_config,
)
```

**根本原因**: `google.generativeai.protos.Schema` の `enum` フィールドは `repeated string`。INTEGER 型 + 整数 enum の組合せは SDK が proto へ marshal する時点で TypeError。
- 004 対応: B-3（外部ライブラリの非対応入力を渡した）
- T-x 対応: T-6（外部ライブラリ仕様の事前確認不足）

**修正方針**: `id` の型を STRING に変更し、enum を `["0".."9"]` の文字列配列にする。応答 JSON の `id` は文字列で返るので `int()` で数値化してから `emoji_buttons[best_index]` に使う。プロンプト出力形式例も文字列表記に合わせる。

Before（L226-L229）:
```python
"id": {
    "type": "integer",
    "enum": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
},
```

After:
```python
"id": {
    "type": "string",
    "enum": ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"],
    "description": "選んだ画像の id（0〜9 のいずれかの文字列）",
},
```

Before（プロンプト出力形式例、L275）:
```python
'{"id": <選んだ画像の id (0〜9 の整数)>}',
```

After:
```python
'{"id": "<選んだ画像の id を 0〜9 の文字列で 1 つ>"}',
```

Before（パース部 L285-L286）:
```python
data = json.loads(result_text)
best_index = int(data["id"])
```

After（実質同一だが、STRING 前提をコメントで明示）:
```python
data = json.loads(result_text)
best_index = int(data["id"])  # schema 側で "0"〜"9" に制約済み
```

**呼び出し側への波及**: 無し（`perform_image_authentication` 内部完結。他ファイルから model は共有されない）

**検証**:
1. `C:\Users\zonekun\Dropbox\アプリ\kabucom\raku.txt` に1行だけキーワードを書いて保存
2. `PYTHONUTF8=1 python scripts/update_conse_rakuten.py` を実行
3. `TypeError` が出ないこと（model 構築通過）
4. ログに `raw response: {"id": "<N>"}` 形式で出ること
5. `best_index` が 0-9 の int に変換され、`emoji_buttons[best_index]` クリックまで到達

**ロールバック**: `git checkout scripts/update_conse_rakuten.py` で commit `d0e9eb0` の状態（`re.search(r'\d+', result_text)` 版）に戻す。範囲外 10 問題は再発するが、ログイン自体は通る。

## P1-1. google-genai SDK への移行 📦

**症状**: CLAUDE.md §「Gemini ライブラリ」は `google.generativeai` を非推奨としており、改修時は `google-genai`（`from google import genai`）を使うべき。現行 image auth は legacy SDK 依存のまま。

**該当**: 
- `scripts/update_conse_rakuten.py:29` — `import google.generativeai as genai`
- `scripts/update_conse_rakuten.py:61` — `genai.configure(api_key=GEMINI_API_KEY)`
- `scripts/update_conse_rakuten.py:221-300` — `generation_config` 構築・`GenerativeModel` 生成・`generate_content` 呼出し

```python:L29
import google.generativeai as genai
```
```python:L61
genai.configure(api_key=GEMINI_API_KEY)
```

**根本原因**: `google-genai` 新 SDK なら response_schema を pydantic モデルで型安全に渡せ、整数 enum 等も pydantic 側で検証できる（legacy SDK の proto 制約を回避）。P0-1 で採用する STRING enum 経由 `int()` 変換は形式上の妥協策なので、将来的には SDK 移行で整理したい。

**修正方針**（別セッションで実施、本プランではスコープ外）:
- `from google import genai` に切替
- `client = genai.Client(api_key=GEMINI_API_KEY)` をモジュールレベルに
- `perform_image_authentication` 内で `client.models.generate_content(model='gemini-2.5-flash', contents=[...], config=GenerateContentConfig(response_mime_type=..., response_schema=<pydantic model>))` へ置換
- pydantic モデル例: `class AuthChoice(BaseModel): id: int = Field(..., ge=0, le=9)`

**呼び出し側への波及**: 同ファイル内 3 箇所のみ。他ファイルからは `perform_image_authentication` も `genai` モジュールも呼ばれない（`grep -rn "perform_image_authentication\|import google.generativeai" scripts/` で確認する）。

**検証**:
1. P0 と同じ smoke test
2. 追加で pydantic バリデーション動作確認（`id=10` を模擬した応答を投げて schema エラーになること）
3. 依存パッケージ: `pip show google-genai`（未導入なら `pip install google-genai`）

**ロールバック**: 単一コミットで移行 → 問題時は `git revert <hash>`。

## 対応アンチパターン

| plan ID | 004 | T-x | G-x |
|---|---|---|---|
| P0-1 | B-3 | T-6 | — |
| P1-1 | — | T-6 | — |

## 検証戦略

1. **smoke test**: `raku.txt` に1キーワードのみで起動 → `TypeError` が出ないこと＋`raw response` ログが出ること＋認証ボタンクリックまで到達すること
2. **dev 実機**: 画像認証が発火するログイン機会で実行 → 3〜4 キーワード指定時に全て schema 通りの応答（STRING `"0"`〜`"9"`）が返り、`best_index` が正しい画像をクリックすること
3. **本番適用判断基準**: smoke PASS + 実ログイン成功 + `STOCK.CONSENSUS` に 1 銘柄分 insert が確認できた段階で prod 採用（Selenium 本番 UI 相手のため別 dev 環境は無し）
4. **回収手順**: ログイン失敗時は即 `git checkout scripts/update_conse_rakuten.py` で `d0e9eb0` 状態に戻す。範囲外 10 問題は残るが、ログイン経路自体は復旧。

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/022_conse_rakuten.md`（画像認証仕様・画像認証対応節）
- CLAUDE.md: §「Gemini ライブラリ」「Gemini 応答安定化」
- Memory: `feedback_gemini_prompt_first.md`（try/except で吸収せず schema/prompt で直す方針）・`feedback_gemini_local_personal.md`（ローカルは個人キー）
- 関連 commit: `d0e9eb0`（現 HEAD）、本セッション未コミット diff が P0 対象
- 実機ログ: 2026-04-21 ユーザー貼付スタックトレース（`protos.Schema(response_schema)` 起点の `TypeError`）
