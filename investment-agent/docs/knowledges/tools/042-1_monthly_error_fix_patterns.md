# 月次開示エラー修復パターン集

**カテゴリ**: tools
**作成日**: 2026-05-05
**ステータス**: 有効
**関連ファイル**: `skills/monthly-error-autofix.md`, `docs/knowledges/tools/042_monthly_disclosure_master.md`
**子MD**: [`042-1-1_monthly_error_autofix_skill_design.md`](042-1-1_monthly_error_autofix_skill_design.md)（スキル設計リファレンス）
**用途**: monthly-error-autofix スキルの Layer 1 即答DB。エラー症状から修復手順への最短経路を提供。

---

## 使い方

1. エラーログから「一致キー」でgrepして該当パターンを特定
2. 「確認条件」を現物で検証（ログ一致だけでは即適用しない）
3. 「除外条件」に該当しないことを確認
4. 「修復手順」に従って修正 → ローカル検証 → 本番投入

---

## DL系パターン

### D1-1: ドメイン変更（企業合併・社名変更）

- **一致キー**: `HTTP 404|status_code=404|全.*件.*失敗`
- **確認条件**: 旧ドメインが応答しない（curl で確認）かつ企業が存続している
- **除外条件**: 一部URLのみ404（→ ページ構造変更 D2 の可能性）/ サーバー一時停止（翌日再確認で復旧）
- **事例**: 8515 アイフル aiful.co.jp → muninova.co.jp (2026-05-05)。28ファイル全404。持株会社移行に伴うドメイン変更。
- **根本原因**: 企業の組織変更（合併・持株会社化・社名変更）でIRサイトのドメインが変わる
- **修復手順**:
  1. WebSearchで「{企業名} IR 月次」検索 → 新ドメイン特定
  2. 新IRページのリンク構造を確認（link_href_pattern用）
  3. url_adapter.json を更新: `ir_page_url`, `link_href_pattern`
  4. ローカルでDLテスト: `python download_monthly.py --tickers <t> --dry-run`
- **参照ソース**: `meta/monthly/8515_url_adapter.json`（修正後の実例）
- **注意**: link_href_patternはドメイン部分だけでなくパス構造も変わることがある

---

## Extract系パターン

### E1-1: regex不一致（記号と数値の間にスペース）

- **一致キー**: `一致なし|抽出結果.*0件|regex.*fail`
- **確認条件**: PDFテキストにはデータが存在する（pdfplumber抽出で目視）
- **除外条件**: PDF構造自体が変更されている（フォーマット改定 → E3検討）
- **事例**: 3086 J.フロントリテイリング (2026-05-05)。`▲ 0.7` — ▲と数字の間にスペースがあり `([▲\d.]+)` がマッチしない
- **根本原因**: PDF生成時のフォント・スペーシングが変わり、記号と数値の間に空白文字が入る
- **修復手順**:
  1. GCSからPDF 1件DL → pdfplumber でテキスト抽出
  2. 期待する数値がテキスト内に存在することを確認
  3. 現行regexで何故マッチしないか特定（スペース/全角/改行等）
  4. `\s*` を可変位置に挿入して修正
  5. extract_adapter.json の `row_label_regex` を更新
- **参照ソース**: `meta/monthly/3086_extract_adapter.json`（修正後）
- **汎用regex**: `[▲△]?\s*\d+[\d,.]*` — 符号オプション+スペース許容+数値
- **注意**: `\s*` を入れすぎると別の行にマッチするリスクあり。最小限に

### E2-1: 年月検出失敗（空文字regexキー）

- **一致キー**: `バッチリクエスト.*0.*件|ym.*None|年月.*検出.*失敗`
- **確認条件**: adapter内に `year_from_title_regex: ""` または `month_from_title_regex: ""` が存在する
- **除外条件**: adapterに空文字キーがない（→ PDFファイル名/タイトルの問題。別パターン）
- **事例**: 7455 パリミキHD (2026-05-05)。`year_from_title_regex: ""` がデフォルトregexのフォールバックを阻害
- **根本原因**: Python `dict.get(key, default)` はキーが存在すれば空文字でもそれを返す。空文字regexは位置0でマッチするがgroup(1)が取れない
- **修復手順**:
  1. adapter内の regex 系キーに空文字 `""` がないか確認
  2. 空文字キーを削除（コード側 `adapter.get(key) or default` で修正済みだが、アダプター側も整理）
- **参照ソース**: `scripts/extract_monthly_data.py` L883, L888, L3661-3662（`or default` パターン修正）
- **注意**: コード修正済み（2026-05-05）のため今後の新規発生率は低い。ただし古いadapterに残存している可能性あり
- **再発防止**: コード側で対策済み。アダプター側の空文字は見つけ次第削除

### E3-1: regex限界 → Gemini変換（複雑レイアウトPDF）

- **一致キー**: `一致なし|抽出結果.*0件`（E1と同じだがPDF確認で判定）
- **確認条件**: PDFが複雑レイアウト（複数テーブル/不規則配置/テキスト+表混在）でregex 2パターン以上失敗
- **除外条件**: 単純テーブルでregex修正可能（→ E1）
- **事例**:
  - 9519 レノバ: 月次実績売電量。テーブル構造が不規則
  - 7177 GMO-FH: 株式売買代金・約定件数。複数セグメントの表
  - 8267 イオン: リテール全店/既存店の前年同月比。複合テーブル
- **根本原因**: PDF内のデータ配置が定型的でなく、固定regexでは対応困難
- **修復手順**:
  1. structure.jsonからフィールド名を取得
  2. extract_adapter.json に以下を設定:
     - `extraction_method: "gemini"`
     - `fields`: structure.jsonのフィールドに対応するkey/bc_key定義
     - `gemini_prompt`: PDFから何を抽出するかの明示的指示
  3. 累積年次PDFの場合は追加: `gemini_multi_month: true`, `overwrite_past_months: true`
  4. ローカルテスト: `python extract_monthly_data.py --tickers <t> --no-batch`
- **参照ソース**: `meta/monthly/9519_extract_adapter.json`, `7177_extract_adapter.json`, `8267_extract_adapter.json`
- **判断基準**:
  - 複数テーブル/不規則配置 → gemini
  - 累積年次PDF（1ファイルに全期間データ）→ gemini + gemini_multi_month + overwrite_past_months
  - 単純テーブル → regex（geminiは過剰）
- **注意**: gemini_multi_monthはJSON配列で全月分を一括返す。overwrite_past_monthsは個別月PDFがない企業のみ

### E3-2: regex限界 → Gemini変換（累積型年次PDF）

- **一致キー**: `バッチリクエスト.*0.*件|一致なし`
- **確認条件**: 企業が1つのPDFに全期間データを記載するスタイル（個別月次PDFが存在しない）
- **除外条件**: 月ごとに個別PDFがある企業（→ E1 or E3-1）
- **事例**: 7455 パリミキHD (2026-05-05)。`過去の月次売上高成長率` 1ファイルに2020-04〜2025-03の全60ヶ月分
- **根本原因**: 1ファイルから複数年月のデータを抽出する必要がある。通常の「1PDF=1月」の前提と合わない
- **修復手順**:
  1. `extraction_method: "gemini"` + `gemini_multi_month: true` + `overwrite_past_months: true`
  2. gemini_prompt に「全月分をJSON配列で返せ」と明記
  3. 出力形式を明示: `[{"year": 2024, "month": 4, "field_name": value}, ...]`
- **参照ソース**: `meta/monthly/7455_extract_adapter.json`（修正後）
- **注意**: overwrite_past_monthsは既存データを上書きするため、個別月PDFがある企業に設定すると過去データが消える

### E4-1: Gemini応答パースエラー（list型レスポンス）

- **一致キー**: `AttributeError.*list.*get|json.*parse.*error|Gemini.*応答.*異常`
- **確認条件**: Geminiがdict期待箇所でlistを返している
- **除外条件**: response_schemaで型を強制している場合（→ プロンプト側の問題）
- **事例**: extract_monthly_data.py L625 `_parse_batch_result_single` でlist応答
- **根本原因**: response_schema未指定時、Geminiが `[{...}]` (list) を返すことがある
- **修復手順**:
  1. adapter の gemini_prompt に「必ずJSON objectで返せ（配列不可）」を追記
  2. 可能であれば response_schema で型を強制
  3. コード側にも `isinstance(data, list)` ガードあり（2026-05-05修正済み）
- **参照ソース**: `scripts/tdnet_load_parallel.py` L1027-1028（同パターンの既存ガード）
- **注意**: コード側ガードはフォールバック。プロンプト/schema修正が正攻法

### E3-3: pdfplumber表破損 → Gemini変換

- **一致キー**: `一致なし|抽出結果.*0件`
- **確認条件**: `page.extract_tables()[0][0]` のCol 0が10文字以上で、複数サブカテゴリキーワードを同時に含む（例: "戸建集合分譲住宅計マンション合計"）
- **除外条件**: Col 0が正常に分離されている（1キーワードのみ）→ E1のregex修正で対応可能
- **事例**: 6752 パナソニックHD 月次受注速報 (2026-04-15)。全サブカテゴリラベルがCol 0に連結され、row_label_regexでどのキーワードを指定しても1行目にマッチ
- **根本原因**: pdfplumberがPDF内部構造を誤解釈し、複数のセルを1セルに結合して返す。PyMuPDFテキスト抽出でもラベルと数値が別行に分離されregex不能
- **修復手順**:
  1. `page.extract_tables()[0]` で実データを確認し、Col 0の連結を検出
  2. PyMuPDF `page.get_text("text")` でも分離確認
  3. 両方NG → `extraction_method: "gemini"` に変更
  4. fields定義でサブカテゴリを明示（geminiに構造を伝える）
- **参照ソース**: `meta/monthly/6752_extract_adapter.json`
- **注意**: PyMuPDFでも解決しないことを確認してからGemini変換する（PyMuPDFで解ける場合はコスト節約）

### E3-4: 英語のみ公開に切替わった企業 → Gemini+日英対応プロンプト

- **一致キー**: `一致なし|0件|DL.*0件|link.*未検出`
- **確認条件**: 企業IRページに英語PDFのみ存在し、日本語PDFが停止されている
- **除外条件**: 日本語PDFが存在するが別URL（→ D1ドメイン変更）/ 日英両方あるがDL設定ミス（→ D2）
- **事例**: 3561 力の源HD (2026-04-15)。2026-03から英語版のみ公開、日本語版停止。IR国際化トレンド
- **根本原因**: url_adapterが日本語キーワード(`月次|業績動向`)のみでフィルタ → 英語PDFがDL対象外。extract_adapterも日本語regex前提
- **修復手順**:
  1. url_adapter の `link_text_pattern` に英日両対応キーワード追加（例: `月次|Monthly|業績動向|Store Performance`）。`manual_override: true` セット
  2. extract_adapter を `extraction_method: "gemini"` + `gemini_multi_month: true` に変更
  3. `gemini_custom_prompt` に以下5項目を**全て**含める:
     - 日英ヘッダー対応表（Apr=4月, May=5月, ...）
     - 会計年度の境界（例: Fiscal Year ending March 31, 2026 = 2025年4月〜2026年3月）
     - 海外店舗テーブル除外指示（国内のみ抽出）
     - `Year total` / `Annual` 列除外
     - ラベル階層の明記（例: Store count は All stores を選ぶ）
  4. fields[].key は structure.json の日本語 key に完全一致させる（BC突合のため）
  5. `year_from_title_regex` は**削除**（英語ファイル名で失敗する。ファイル名先頭 `YYYYMM_` ヒューリスティックに委ねる）
- **参照ソース**: `meta/monthly/3561_extract_adapter.json`, `meta/monthly/3561_url_adapter.json`
- **注意**: 5項目のうち1つでも欠けると抽出エラーになる。特に「海外除外」を忘れると国内+海外が混在して数値が2倍になる

### E5-1: adapter fields=[]（抽出項目未定義）

- **一致キー**: `adapter_no_fields|adapter fields=\[\]`
- **error_type**: `adapter_no_fields`
- **確認条件**: extract_adapter.json の `fields` が空リスト `[]`
- **除外条件**: `_excluded=true` の銘柄（検証対象外として先にスキップ済み）
- **事例**: 60社（2026-05-07 INDEX整合チェックで検出）。structure.json にメトリクス定義はあるが extract_adapter の fields が未設定
- **根本原因**: adapter生成時にfields設定が漏れた。従来は `no_records` としてサイレントスキップされ、PDFなしと区別不能だった
- **修復手順**:
  1. structure.json のメトリクス定義を確認（`meta/monthly/{ticker}/structure.json`）
  2. extract_adapter の fields を structure.json に基づいて構築（Codex委譲可）
  3. extraction_method の判定: regex 5条件すべて満たす場合のみ regex、それ以外は gemini
- **参照ソース**: `scripts/extract_monthly_data.py` L3325-L3349（検証ロジック）
- **注意**: P0-1実装（2026-05-07）により、従来の `no_records` に紛れていた本パターンが独立error_typeとして可視化された

### E5-2: adapter fields不足（structure.json未カバー）

- **一致キー**: `adapter_fields_incomplete|adapter fields 不足`
- **error_type**: `adapter_fields_incomplete`
- **確認条件**: structure.json に定義された metrics のうち、extract_adapter.fields でカバーされていないものがある
- **除外条件**: structure.json 自体が存在しない銘柄（突合不能）/ fields=[]（→ E5-1）
- **動作**: **会社スキップなし**。WARNINGログ + error_entry記録のみ。既存fieldsで抽出続行
- **事例**: adapter が structure.json metrics の一部のみ定義しているケース
- **根本原因**: adapter生成時にstructure.jsonの全メトリクスをカバーしきれなかった。本来はPDFから抽出不可能な指標も adapter で定義し「抽出不可能」と明示すべき
- **修復手順**:
  1. 不足メトリクス一覧はログの WARNING に出力される
  2. 各メトリクスについてPDFで抽出可能か確認
  3. 可能 → fields に追加。不可能 → fields に定義しつつ抽出不可と記録
- **参照ソース**: `scripts/extract_monthly_data.py` L3350-L3362（突合ロジック）
- **注意**: 設計意図として「adapter間の設定アンマッチ検出」が目的。会社スキップではなく項目レベルの警告

### E6-1: GCS文書種別ミスマッチ（GCSに別種文書のみ）

- **一致キー**: `bc_ignore|GCS.*文書.*不一致|文書種別.*ミスマッチ`
- **確認条件**: GCSに文書は存在するが月次データと無関係（FAX注文書・レジ袋削減PDF・ニュースリリース等）
- **除外条件**: 文書内に月次売上データが含まれる（→ E1-E3のregex/Gemini問題）
- **事例**: 9994 やまや — GCSにchainstore_FAX_order PDFのみ。月次売上データなし
- **根本原因**: url_adapterのlink_href_patternが月次売上PDFでなく別種文書にマッチ
- **修復手順**:
  1. GCS文書一覧で内容種別を確認（ファイル名だけで判断せず中身を確認）
  2. url_adapterのlink_href_pattern/link_text_patternを確認
  3. 企業IRに月次売上PDFが実在 → url_adapter修正（D系）
  4. 企業IRに月次売上文書なし → extract_adapter全field bc_ignore
- **参照ソース**: `meta/monthly/9994_extract_adapter.json`
- **注意**: bc_ignore前にurl_adapter上流チェック必須（skills/monthly-error-autofix.md Step 3C参照）

### E6-2: HTML空テーブル（JSレンダリング不可）

- **一致キー**: `bc_ignore|table.*0件|テーブル.*なし|HTML.*空`
- **確認条件**: GCSのHTML内にテーブルタグが0個。Drupal/React等のJS動的ロード
- **除外条件**: テーブルタグはあるが中身が空（→ E6-3）/ iframe経由（→ D2-2）
- **事例**: 9974 ベルク — monthly_table.htmlは「月次売上情報」ページだがDrupal CMSで動的読み込み、テーブル0件
- **根本原因**: download_monthly.pyのSSR取得ではJSが実行されず空HTMLが保存される
- **修復手順**:
  1. HTMLテーブル数カウントでテーブル0件を確認
  2. Playwright等JS実行環境で同URLを確認 → データが表示される場合はD2-2（iframe/JS）として修復
  3. JS実行でもデータなし → bc_ignore
- **参照ソース**: `meta/monthly/9974_extract_adapter.json`

### E6-3: HTML内容ミスマッチ（テーブルはあるが月次データでない）

- **一致キー**: `bc_ignore|HTML.*内容.*不一致|月次指標.*なし`
- **確認条件**: HTMLにテーブルはあるが、ヘッダが月次売上指標（売上/客数/客単価/前年同月比）と無関係
- **除外条件**: ヘッダに月次指標が含まれる（→ E1のregex問題）
- **事例**: 9517 イーレックス — HTMLに23テーブルあるがすべてニュースリリース一覧（日付/タイトル列）
- **根本原因**: url_adapterがIRニュースページ等の非月次ページを月次データとしてDL
- **修復手順**:
  1. HTMLテーブルのヘッダを確認
  2. 月次指標ヘッダなし → url_adapter確認 → 正しいIRページ特定 → url_adapter修正（D系）
  3. 企業が月次HTMLテーブルを公開していない → bc_ignore
- **参照ソース**: `meta/monthly/9517_extract_adapter.json`

### E6-4: セグメントミスマッチ（月次PDFにBC対象セグメントなし）

- **一致キー**: `bc_ignore|セグメント.*不在|対象.*セグメント.*なし`
- **確認条件**: 月次PDFは取得できるが、structure.jsonのメトリクスに対応するセグメントがPDF内にない
- **除外条件**: PDFにデータはあるがregexが合わない（→ E1）
- **事例**: 9041 近鉄GHD — 月次営業概況PDFは鉄道データのみ。BC structure.jsonの航空メトリクス(近鉄エクスプレス由来)はPDFに記載なし → 航空fieldのみbc_ignore、鉄道fieldは抽出可能
- **根本原因**: 企業グループ内の複数セグメントのうち一部のみ月次開示。BCが全セグメントのメトリクスを定義していても、月次PDFでカバーされないセグメントがある
- **修復手順**:
  1. PDFから実際に記載されているセグメント/指標を確認
  2. structure.jsonの全メトリクスと照合
  3. PDF記載あり → field定義して抽出（extraction_method判定）
  4. PDF記載なし → 当該fieldのみbc_ignore（`bc_ignore_reason`にセグメント不在を明記）
- **参照ソース**: `meta/monthly/9041_extract_adapter.json`
- **注意**: 全fieldではなく一部fieldのみbc_ignoreになるケース。安易に全field bc_ignoreにしない

### E6-5: 開示中断・停止

- **一致キー**: `bc_ignore|開示.*中断|開示.*停止|GCS.*古いファイルのみ`
- **確認条件**: 企業が月次開示を過去は行っていたが現在停止している（GCSに古いファイルのみ）
- **除外条件**: 最新月のファイルがある（→ E1-E4）/ ドメイン変更で新URLにある（→ D1）
- **事例**: 9979 大庄 — 2020年4月〜月次開示中断、2021年8月に再開。中断期間のデータはbc_ignore相当
- **根本原因**: COVID-19等の外部要因で月次開示を一時停止する企業がある。恒久停止の場合もある
- **修復手順**:
  1. GCSファイルの日付範囲を確認（最新ファイルの年月）
  2. 企業IRページで現在の開示状況を確認（WebSearch/WebFetch）
  3. 一時停止→再開済み: extraction_notesに停止期間を記録、抽出はファイルのある期間のみ
  4. 恒久停止: 全field bc_ignore + extraction_notesに停止時期・理由を記録
- **参照ソース**: `meta/monthly/9979_extract_adapter.json`
- **注意**: 中断→再開のパターンがあるため、安易に恒久停止と判断しない

---

## DL系パターン（追加）

### D2-1: adapter type誤設定

- **一致キー**: `DL.*0件|リンク.*なし|eIR.*404`
- **確認条件**: IRページを実際にWebFetch/curl_cffiで取得し、(a) HTML内にテーブルがあるのにtype=scrape_links (b) PDFリンクがあるのにtype=html_table (c) eIRウィジェットでないのにtype=eir_api
- **除外条件**: ページ自体が404（→ D1ドメイン変更）
- **事例**: html_tableをeir_apiと誤設定 → eIR API 404で0件
- **根本原因**: url_adapter生成時のページ分析が不正確で、実態と異なるtypeが設定された
- **修復手順**:
  1. IRページをWebFetch/curl_cffiで取得
  2. ページ内容から正しいtype判定: PDFリンク有→scrape_links / テーブル有→html_table / eIRウィジェット有→eir_api
  3. url_adapter の `type` を修正
  4. 必要に応じて `css_selector`, `link_href_pattern` も調整
- **参照ソース**: `scripts/download_monthly.py` のtype別処理（`handle_scrape_links`/`handle_html_table`/`handle_eir_api`）
- **注意**: 同一ページにPDFリンクとHTMLテーブルの両方がある場合はPDF(scrape_links)を優先

### D2-2: iframe/JSレンダリングで取得不能

- **一致キー**: `DL.*0件|リンク.*なし|table.*なし`
- **確認条件**: IRページをcurl_cffiで取得したHTMLが空 or iframeのみ。Playwright（JS実行）で取得すると内容が見える
- **除外条件**: curl_cffiでも内容が取れる（→ D2-1 type誤設定 or E1 regex問題）
- **事例**: XJ-Storage経由のeIRウィジェット（JS動的描画、iframe内）
- **根本原因**: データがiframe内 or JavaScript動的ロードでSSR HTMLに含まれない
- **修復手順**:
  1. IRページをPlaywrightで開き、`page.frames` を確認
  2. データがiframe内なら、iframe srcのURLをurl_adapterに直接設定
  3. JS動的ロードなら、Playwrightで描画後のHTMLを取得する方式に切替え（type変更 or カスタム処理）
- **参照ソース**: `scripts/download_monthly.py` `_follow_iframe()`, Playwright フォールバック処理
- **注意**: 別ドメインiframeはCORS制約でframe contentを直接取れない場合あり

### D2-3: TLSフィンガープリント拒否

- **一致キー**: `DL.*0件|リンク.*なし|リンク未検出`
- **確認条件**: `requests` でIRページ取得が失敗/空 & `curl_cffi` で同じURLが取得できる
- **除外条件**: curl_cffiでも取得不能（→ D1ドメイン変更 or D3 DNS障害）
- **事例**: download_monthly.py改修前（2026-04-12以前）。requestsのPython TLSフィンガープリントがbot判定される
- **根本原因**: `requests` はPython固有のTLSフィンガープリントを持ち、多くの企業IRサイトでbot検知される。`curl_cffi` はChrome TLSを偽装するため通過する
- **修復手順**:
  1. 現行スクリプトのHTTP取得方式を確認（requests / curl_cffi / Playwright）
  2. requestsのみの場合 → curl_cffiプライマリ+requestsフォールバック構成に変更
  3. 2026-04-12改修済みのため、最新コードでは通常発生しない。古いバージョン実行時のみ
- **参照ソース**: `scripts/download_monthly.py` `_fetch_page_content()`（curl_cffiプライマリ改修済み）
- **注意**: 改修済みのため新規発生率は低い。ただしurl_adapter更新スクリプト(update_monthly_adapters.py)側はPlaywright使用で別経路

### D2-4: DLファイル名年月プレフィックス `000000` 化

- **一致キー**: `000000|年月.*不明|ym.*None|プレフィックス.*異常`
- **確認条件**: GCS docs内の全ファイル名が `000000_{ticker}_...` で年月が `000000`。ファイル自体は存在しDL成功しているが、下流のextractで年月特定不能
- **除外条件**: 一部ファイルのみ000000（→ 個別ファイルの問題）/ ファイル自体がない（→ D1-D3）
- **事例**: 9042 阪急阪神HD — ホテル営業概況PDF 50件以上DL済みだが全件 `000000` プレフィックス。url_adapterのlink_href_patternからファイル名年月を抽出できないパス構造
- **根本原因**: download_monthly.pyがPDFリンクURLから年月を抽出してファイル名プレフィックスに付与するが、リンクURLのパス構造がパターンに合わず `000000` にフォールバック
- **修復手順**:
  1. GCSファイル名一覧で `000000` プレフィックスの件数を確認
  2. url_adapterの `link_href_pattern` を確認 → URLパス構造から年月を抽出可能な正規表現に修正
  3. 修正後にDLテスト → ファイル名に正しい年月プレフィックスが付くことを確認
  4. 既存 `000000` ファイルのリネームまたは再DLが必要
- **参照ソース**: `scripts/download_monthly.py` のファイル名生成ロジック
- **注意**: DLは成功しているためD系ログでエラーとして検出されないサイレント不具合。extractのno_records/年月不明でのみ表面化する

### E3-5: regex列番号(group)ハードコード → Gemini変換

- **一致キー**: `group.*固定|列番号.*固定|月度.*列.*不一致`
- **確認条件**: adapter.fields に `"group": N` が固定値で設定されており、Nが特定月度の列位置にハードコードされている。異なる月度のPDF/テキストでは対象列が変わるため抽出ゼロ or 誤値
- **除外条件**: 単月テーブル（列が1つしかない）→ group固定で正しい
- **事例**: 2736 フェスタリアHD — `group:6`(2月度固定)。1月度テキストでは1月データが5列目にあるが6列目を取得→誤値。7679 薬王堂HD — `group:12`(2月度固定)。他月度では列位置が異なる
- **根本原因**: adapter生成時のサンプルが特定月度のPDFで、その月度の列位置をgroup値にハードコードした。月次累積型テーブルでは列位置が月ごとに変わる
- **修復手順**:
  1. adapter の fields で `group` ハードコードを確認
  2. テーブル構造を確認: 月次累積型（4月〜対象月まで列が並ぶ）→ group固定は不可
  3. `extraction_method: "gemini"` に変更
  4. `custom_prompt` で「タイトルに記載された月度の列の値を読み取る」旨を指示
  5. group / use_last_number / row_label_regex を削除し、description で指示
- **参照ソース**: `meta/monthly/2736_extract_adapter.json`, `meta/monthly/7679_extract_adapter.json`
- **注意**: TDnet source の場合、ローカルGemini text extractionはタイムアウトしやすい。Cloud Runでの実行を前提とする

### E3-6: 決算期年(year_from_title_regex)と暦年の混同

- **一致キー**: `2027.*混入|年月.*不一致|fiscal.*year|決算期年.*暦年`
- **確認条件**: monthly_records.json に未来年月（例: 2027-03）が混入。doc_titleが「2027年2月期 3月度」のような決算期表記で、year_from_title_regexが2027を抽出するが、実際の報告月は2026年3月
- **除外条件**: year_from_title_regex未設定（提出日ヒューリスティックで自動補正される）
- **事例**: 9876 コックス — `year_from_title_regex: "(\d{4})年\d{1,2}月期"` が2027年2月期から2027を抽出し、3月度=2027-03として記録。正しくは2026-03
- **根本原因**: 「YYYY年M月期」はfiscal year end（決算期終了年月）であり、報告月がM月より後の場合は暦年=YYYY-1。adapter生成時にこの変換が考慮されていない
- **修復手順**:
  1. year_from_title_regexを**削除**し、提出日ヒューリスティック（_parse_year_monthのStep 4）に委ねる
  2. または `use_fy_history_correction: true` を設定（BQマスタ参照で自動補正）
  3. monthly_records.json の誤年月エントリはCloud Run再実行で上書きされる
- **参照ソース**: `meta/monthly/9876_extract_adapter.json`, `scripts/extract_monthly_data.py` L863-971 `_parse_year_month()`
- **注意**: 提出日ヒューリスティックは day<=15 で前月扱い。大半の月次開示は翌月10日前後提出のため正確に動作する

### E4-2: Gemini表内セグメント/会社行の誤認

- **一致キー**: `Gemini.*誤認|セグメント.*混同|会社.*行.*誤|値.*大幅差`
- **確認条件**: Gemini抽出値とBC値の差が大きく（diff>5%）、PDFを目視すると別の会社/セグメント行の値を取得している
- **除外条件**: 値が近い（diff<2%）→ 四捨五入等の軽微差（許容範囲）
- **事例**: 8267 イオン — PDFに9社の全店/既存店行が縦に並ぶ。Geminiが「イオンリテール」ではなく「ジーフット」や「コックス」の行を取得し、97.3を返す（正解は102.6）
- **根本原因**: Geminiの表解析で、対象行を特定する指示が曖昧。複数会社が同一テーブル内に並ぶ場合、行の位置や会社名の指定が不十分
- **修復手順**:
  1. PDFを目視して表構造を確認（複数会社/セグメントが並んでいるか）
  2. custom_promptで対象会社/セグメントを明確に指定:
     - 「一番上の行」「最初の会社」等の位置指定
     - 正式名称（イオンリテール㈱ AEON RETAIL CO., LTD.）の明記
     - 「他の会社（XXX, YYY等）の値は無視」の排除指示
  3. fields[].descriptionにも「表のN行目」等の位置情報を追加
- **参照ソース**: `meta/monthly/8267_extract_adapter.json`
- **注意**: 単にセグメント名を書くだけでは不十分。PDFの表レイアウトに応じた位置指定が重要

### E4-3: BCのフィールド定義とソースのセグメント階層不一致

- **一致キー**: `全店.*全業態|セグメント.*階層.*不一致|BC定義.*ソース定義.*異なる`
- **確認条件**: BC値と抽出値が系統的にずれ、ソースHTMLを読むと「全店（全業態）」と「国内外食（サブウェイ事業除く）全店」等の階層が存在し、adapter descriptionが下位階層を指している
- **除外条件**: 値が一致している → 階層は正しい / ソースに階層構造がない
- **事例**: 7522 ワタミ — BCの「国内外食 全店 売上」=110.1はHTML最上段「全店（全業態）」行の値。adapterが「国内外食（サブウェイ事業除く）全店」行の104.9を取得していた。descriptionに「全店（全業態）合計」と明記し、custom_promptで階層構造を説明して修復
- **根本原因**: BCが定義する「国内外食 全店」がソース上の最上位集計行を指すのに対し、adapter descriptionが文字通りの「国内外食」セクション配下の行を指定
- **修復手順**:
  1. BCの値とソース（HTML/PDF）の各セグメント行の値を突合し、どの行がBC定義に合致するか特定
  2. adapter fieldsのdescriptionを「表の最上段にある全店（全業態）行の値」等、BCが期待する行を正確に指す記述に修正
  3. custom_promptで階層構造を明示し、「（サブウェイ事業除く）」等の下位セグメントではないことを指示
  4. BCに存在しないセグメント（例: 既存店）にはbc_ignore=trueを付与
- **参照ソース**: `meta/monthly/7522_extract_adapter.json`
- **注意**: フィールド名（key/bc_key）はBC定義に合わせつつ、descriptionで実際のソース上の取得位置を指定する

### E4-4: 単月抽出パスでcustom_prompt未参照（コード制約）

- **一致キー**: `custom_prompt.*無視|単月.*プロンプト.*未参照|全年.*同一値`
- **確認条件**: adapter.custom_promptを設定したが、抽出結果に反映されていない。monthly_records.jsonの全年で同月の値が同一（例: 全年の2月=95.0）
- **除外条件**: gemini_multi_month=trueの場合 → multi_monthパスはgemini_custom_promptを参照する
- **事例**: 8267 イオン — custom_promptでイオンリテール行を指定したが、単月パス（_build_extract_prompt の is_multi_month=False分岐）にcustom_prompt注入コードがなく無視された。全年同月同一値（2月=95.0/93.9）が症状
- **根本原因**: extract_monthly_data.py の `_build_extract_prompt()` 関数、単月分岐（line 442-472）にcustom_prompt/gemini_custom_prompt の挿入がない（multi_monthパスにはgemini_custom_promptの挿入あり）
- **修復手順（adapter-onlyワークアラウンド）**:
  1. gemini_multi_month=true, overwrite_past_months=true に変更（多月パスに切り替え）
  2. `gemini_custom_prompt` キーにプロンプトを設定（multi_monthパスが参照するキー名）
  3. custom_promptも残す（HTML抽出パスは両方読む）
  4. 将来的にコード修正で単月パスにもcustom_prompt注入を追加すべき
- **参照ソース**: `meta/monthly/8267_extract_adapter.json`, `scripts/extract_monthly_data.py` line 423 vs 442
- **注意**: コード修正なしのワークアラウンド。multi_monthに切り替えるとバッチ予測のリクエスト数が減るが、1 PDFから全月抽出するため精度が変わる可能性あり

---

### E5-1: regex adapterローカルテストの落とし穴

- **一致キー**: `field未マッチ.*text_len=\d{4,5}` かつ extraction_method=regex
- **確認条件**: GCS `tdnet/{ticker}/` にPDF blobが存在し、`_extract_pdf_by_column` がNone返却しているか
- **除外条件**: extraction_method=gemini の場合は別問題
- **やってはいけない**: table_to_linesテキストにregexを直接当てて「全値一致」で完了とする。Cloud Run上では `_extract_pdf_by_column` → rec判定 → BQフォールバック分岐を経由するため、regex単体マッチでは実際の動作を検証できない
- **やるべきこと**: `_extract_pdf_by_column` → rec判定 → フォールバック分岐を含む実コードパスを通してテストする。regex単体マッチ ≠ Cloud Runで動く
- **参照ソース**: `scripts/extract_monthly_data.py` L3575-3635（regex抽出フロー）, L3611（BQフォールバック分岐）

---

## 蓄積ルール

1. **新パターン発見時に即追記**: Layer 2/3で解決した場合、成功した解法を上記フォーマットで追記
2. **重複判定**: 「一致キー」+「根本原因」が同じなら重複。事例のみ追加
3. **必須フィールド**: 一致キー・確認条件・除外条件・事例・修復手順・参照ソース（6点セット）
4. **解決できなかったケースも記録**: `### XX-N: [未解決] <概要>` で残す（エスカレーション理由・試したこと）
