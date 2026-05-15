# コードレビュー: 上場廃止マスタ最新化パイプライン（松井TOB + JPX + classify-tob + レビュー検出hook）

- 日時: 2026-05-15 JST
- 対象: `scripts/review_agent_gate.py`, `scripts/scrape_matsui_delisted.py`, `scripts/scrape_jpx_delisted.py`, `skills/classify_tob.md`
- パターン: 1 (新規)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: 松井証券TOBページから廃止予定銘柄を先行INSERT、JPX廃止一覧で本登録＋先行分UPDATE、IS_TOB_MBO判定をスキル分離、レビュー検出hookを追加。
- 品質評価: **B** — 全体構成は妥当でデータフロー設計も明確だが、SQL injection・ロギング規約違反・ファイル名不一致・エラー握り潰しなど即修正すべき項目が複数存在する。
- 主要リスク:
  1. `scrape_jpx_delisted.py` の `update_pending()` で TICKER/REASON を f-string で SQL 組立 → SQL injection / SQL 破壊（C-1違反）
  2. `scrape_jpx_delisted.py` が `print()` + 自作 `log()` を使用し `structlog` 未使用（CLAUDE.md §7 違反）
  3. `scrape_jpx_delisted.py` の `get_existing_keys()` / `get_pending_tickers()` が BQ クエリ失敗時に空 set を返して処理続行（B-3 アンチパターン）

## 【重大な指摘】（即修正）

### #1 SQL injection: `update_pending()` で TICKER / DELISTING_REASON を f-string 展開

- 箇所: `scripts/scrape_jpx_delisted.py:143-159`
- 事象: `TICKER` と `DELISTING_REASON` をクォートなしの f-string で SQL に直接埋め込んでいる。TICKER は `_normalize_ticker()` で `^\d{4}[A-Z]?$` に制約されるため injection リスクは低いが、`DELISTING_REASON` はスクレイピング結果の生テキストがそのまま展開される。シングルクォートやバックスラッシュを含む理由文字列が来ると SQL が壊れる（例: 「○○'s の完全子会社化」）。最悪ケースでは DML injection により意図しない UPDATE が発生する。
- トリガー: JPX の廃止理由テキストにシングルクォート `'` が含まれる場合。
- 影響: SQL 実行エラーによる UPDATE 失敗、またはデータ破壊。
- 根拠: L144-153 で `f"WHEN '{r['TICKER']}' THEN ..."` / `f"WHEN '{r['TICKER']}' THEN '{r['DELISTING_REASON']}'"` を文字列結合。004 §C-1「SQL を f-string で組み立てる → parametrize」に明確に違反。
- 推奨対応: **[方向性]** `MERGE` 文 + `QueryJobConfig(query_parameters=[...])` でパラメタライズする。CASE 式で多行 UPDATE する構造自体を `MERGE` に置き換えれば、パラメタライズも容易になる。

### #2 BQ クエリ失敗時の空 set フォールバック（B-3 違反）

- 箇所: `scripts/scrape_jpx_delisted.py:120-122`, `scripts/scrape_jpx_delisted.py:131-137`
- 事象: `get_existing_keys()` と `get_pending_tickers()` が `except Exception: return set()` で BQ クエリ失敗を握り潰している。テーブル未存在時の初回実行は想定されるが、認証エラー・ネットワーク障害・クォータ超過でも空 set を返すため、冪等性判定が崩壊し**全行が「新規」と誤判定されて重複 INSERT が発生する**。
- トリガー: BQ クエリが認証失敗・タイムアウト等で例外を投げた場合。
- 影響: 既存レコードとの重複 INSERT（PK は NOT ENFORCED のため BQ 側で防げない）。
- 根拠: 004 §B-3「既存キー取得の BQ クエリ失敗時に空 set を返して処理続行 → 失敗は raise して job を止める」に該当。
- 推奨対応: **[方向性]** テーブル未存在のみ `google.api_core.exceptions.NotFound` でキャッチし空 set を返す。それ以外の例外は re-raise してジョブを停止させる。

### #3 `scrape_jpx_delisted.py` が `print()` / 自作 `log()` を使用（structlog 未使用）

- 箇所: `scripts/scrape_jpx_delisted.py:88-91` (自作 `log()` 定義), および全体で約 20 箇所の `log()` 呼び出し
- 事象: CLAUDE.md §7「ロギング: print 禁止。structlog を使用」に違反。自作 `log()` 関数は `print()` のラッパーであり、構造化ログにならない。
- トリガー: 常時。
- 影響: Cloud Run Job 等での監視クエリ対応不可。他スクリプト（`scrape_matsui_delisted.py` は正しく `structlog` を使用）との一貫性欠如。
- 根拠: CLAUDE.md §7 および 004 チェックリスト「CLAUDE.md§コーディング規約: print() 不使用、structlog でロギング」。
- 推奨対応: **[検証済み]** `scrape_matsui_delisted.py` と同様に `import structlog; logger = structlog.get_logger()` に置き換える。`log(f"...")` 呼び出しを `logger.info("event_name", key=value)` 形式に変更する。

### #4 `scrape_matsui_delisted.py` のファイル名と Usage 行の不一致

- 箇所: `scripts/scrape_matsui_delisted.py:21-22`
- 事象: docstring の Usage 行が `python scripts/scrape_matsui_tob.py` と記載されているが、実際のファイル名は `scrape_matsui_delisted.py`。コピペで実行すると `ModuleNotFoundError` / `No such file` になる。
- トリガー: ユーザーが docstring の Usage をそのままコピーして実行した場合。
- 影響: 実行不能（軽微だが即修正すべき誤り）。
- 根拠: L21-22 `python scripts/scrape_matsui_tob.py` vs 実ファイル名 `scrape_matsui_delisted.py`。
- 推奨対応: **[検証済み]** L21-22 を `python scripts/scrape_matsui_delisted.py` に修正する。

### #5 `scrape_jpx_delisted.py` の `main()` に exit code 制御なし（A-1 / A-7 違反）

- 箇所: `scripts/scrape_jpx_delisted.py:283-378`
- 事象: `main()` に `errors` カウントが存在せず、スクレイピングや BQ 操作が部分的に失敗しても常に exit 0 で終了する。また終了時の `processed` / `skipped` / `errors` サマリ出力がない。
- トリガー: `scrape_year()` で一部年度のテーブル抽出が失敗した場合や、`insert_to_bq()` / `update_pending()` が例外を投げた場合。
- 影響: 上流スケジューラ/Workflows が成功と誤判定。データ欠損の検知遅延。
- 根拠: 004 §A-1「`main()` 末尾で `if errors > 0: sys.exit(1)` を徹底」、§A-7「終了時 processed/skipped/errors サマリを必ず出力」。
- 推奨対応: **[方向性]** `scrape_matsui_delisted.py` の `main()` も同様に `errors` カウントと `sys.exit(1 if errors else 0)` が欠如しているため、両スクリプトとも対応が必要。

### #6 `classify_tob.md` Step 2 の SQL に直接変数展開（C-1 相当）

- 箇所: `skills/classify_tob.md:48-51` (Step 2 の SQL テンプレート), `skills/classify_tob.md:74-85` (Step 3 の UPDATE SQL)
- 事象: SQL テンプレートで `'{ticker}'`, `'{delisting_date}'`, `'{true/false}'` を直接埋め込む記法を示している。スキル実行時にAIが `client.query()` で実行する際、パラメタライズを使わず f-string で組み立てる誘導になっている。
- トリガー: COMPANY_NAME やその他フィールドにシングルクォートを含む銘柄が存在する場合、または AIが忠実にテンプレートを再現した場合。
- 影響: SQL 実行エラー。TICKER は4桁コードなので injection リスクは低いが、プロジェクト規約（004 §C-1）との不整合。
- 根拠: 004 §C-1。ただしスキルMDはAIへの指示テンプレートであり、AIがパラメタライズで実装する可能性もあるため影響度は中程度。
- 推奨対応: **[方向性]** SQL テンプレートにパラメタライズの使用を明示的に注記するか、`QueryJobConfig` を使用する例を示す。少なくとも Step 3 の CASE式 UPDATE は `MERGE` + パラメタライズの記法に書き換えることを推奨。

## 【改善提案】（可読性・保守性）

### #1 `scrape_matsui_delisted.py` の docstring に型ヒント欠落

- 箇所: `scripts/scrape_matsui_delisted.py:122-129` (`_extract_company_name`)
- 現状: 引数 `td` に型ヒントがない。BeautifulSoup の `Tag` 型を受け取ることが docstring からは推測できるが、コードからは `Any` に見える。
- 提案: `from bs4 import Tag` して `def _extract_company_name(td: Tag) -> str:` とする。CLAUDE.md §7「型ヒント必須」。

### #2 `scrape_jpx_delisted.py` にも `get_bq()` / `get_existing_keys()` に docstring がない

- 箇所: `scripts/scrape_jpx_delisted.py:97-99` (`get_bq`), `scripts/scrape_jpx_delisted.py:113-122` (`get_existing_keys`)
- 現状: `get_bq()` は1行のみ、`get_existing_keys()` は docstring あり。しかし `create_driver()`, `normalize_columns()`, `main()` に docstring がない。
- 提案: CLAUDE.md §7「docstring 必須（Google style）」に従い、全関数に docstring を追加する。

### #3 `scrape_jpx_delisted.py` の SSL パッチがモジュールレベルでモンキーパッチ

- 箇所: `scripts/scrape_jpx_delisted.py:37-49`
- 現状: `requests.Session.__init__` をモンキーパッチして全 HTTPS リクエストの SSL 検証を無効化している。ローカル実行での Windows SSL 問題回避と理解できるが、import した他モジュール（BQ client 含む）にも波及する。
- 提案: `requests.Session` の global パッチではなく、JPX 取得専用の Session インスタンスに限定する。ただし Selenium の ChromeDriverManager が内部で requests を使うため、この変更には注意が必要。少なくとも docstring にスコープと影響を明記すべき。

### #4 `scrape_jpx_delisted.py` の `update_pending()` 戻り値が不正確

- 箇所: `scripts/scrape_jpx_delisted.py:162-163`
- 現状: `return len(rows)` を返しているが、BQ の UPDATE が実際に何行更新したかは `job.num_dml_affected_rows` で確認すべき。SQL の `WHERE DELISTING_DATE IS NULL` 条件により、既に別プロセスで UPDATE 済みの行はスキップされるが、戻り値は入力件数をそのまま返す。
- 提案: `result = client.query(sql)` の後に `job = result` として `job.num_dml_affected_rows` を使用する（004 §D-4 参照）。

### #5 `scrape_matsui_delisted.py` の `get_existing_tickers()` で SQL を f-string 展開

- 箇所: `scripts/scrape_matsui_delisted.py:145-150`
- 現状: `f"SELECT ... FROM \`{TABLE_ID}\`"` で TABLE_ID を f-string 展開している。TABLE_ID はハードコード定数なのでinjection リスクは実質ゼロだが、004 §C-1 の精神に反する。
- 提案: テーブル名の f-string 展開は BQ の制約上パラメタライズ不可（テーブル名はパラメータにできない）なので、現状維持でも許容範囲。ただしコメントでその旨を明記するとよい。

### #6 `review_agent_gate.py` で `print()` 使用

- 箇所: `scripts/review_agent_gate.py:27`
- 現状: hook スクリプトの出力に `print(REMINDER, end="")` を使用。hook のプロトコル上 stdout がメッセージ出力チャネルであるため、これは `structlog` ではなく `print()` が正しい。
- 提案: hook プロトコルの仕様上 `print()` は正当な使用。コメントで「hook protocol: stdout = message output」と明記すると保守性が向上する。改修不要。

### #7 `scrape_matsui_delisted.py` の `main()` に `sys.exit()` なし

- 箇所: `scripts/scrape_matsui_delisted.py:187-216`
- 現状: 重大指摘 #5 でも言及したが、`main()` が `errors` カウントを持たず、`fetch_tob_page()` が `raise_for_status()` で例外を投げた場合はトレースバックで exit 1 になるものの、`parse_tob_rows()` が空を返す場合（HTML構造変更等）は正常終了扱いになる。
- 提案: `processed` / `inserted` / `skipped` のサマリ出力と `sys.exit(1 if errors else 0)` を追加する。

## 【確認できなかった事項】

- 松井証券ページ（`https://ca.image.jp/matsui/?type=9`）の実際の HTML 構造。`class_="commontbl"` のテーブルが存在するか、td の列数が 8 以上あるか、備考列が index 7 であるかは実行して確認しないと断定不能。
- JPX ページの HTML が `pd.read_html()` で正しくパースされるかの実地確認。年度ごとにカラム構成が異なる可能性がある。
- BQ テーブル `STOCK.DELISTED_STOCKS` に `TOB_PRICE` / `TOB_ANNOUNCEMENT_DATE` カラムが実際に存在するか。data_catalog には記載があるが、`scrape_jpx_delisted.py` の `BQ_SCHEMA` にはこれらのカラムが含まれていない（松井スクリプトのスキーマには含まれている）。スキーマ不一致で Load Job がエラーになる可能性がある。
- `review_agent_gate.py` が settings.json の hooks セクションに正しく登録されているか（hook として動作するには登録が必要）。
