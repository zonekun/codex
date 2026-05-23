# 205_cr_tdnet_orders_commander_skill

**提出日**: 2026-05-18
**提出者**: Claude (メインエージェント)
**レビュースキル**: code-reviewer
**レビューパターン**: 1（新規実装の妥当性レビュー）

---

## レビュー対象ファイル

| パス | 役割 |
|------|------|
| `skills/tdnet_orders_commander.md` | スキル正本（オーケストレータ手順定義） |
| `.claude/commands/tdnet-orders-commander.md` | スラッシュコマンドラッパー（Agent 起動・引数受け渡し） |

参考情報（編集対象外、コンテキスト用）:
- `skills/tdnet_orders_extract.md` — 呼び出し対象の受注高抽出ソルジャー（既にレビュー済み: `204_cr_tdnet_orders_extract_skill.md`）
- `.claude/commands/tdnet-orders-extract.md` — ソルジャー側ラッパー
- `docs/plans/ad-hoc_tdnet_orders_extract_20260517_214618.md` — 元プランMD（Phase 2 全銘柄処理の上位コンテキスト）

---

## 事象・背景

### なぜ作ったか

受注高抽出ソルジャー（`skills/tdnet_orders_extract.md`）は「1 銘柄 1 起動」設計のため、
複数銘柄を順次処理するには **オーケストレータ** が別途必要。本スキルがその役割を担う。

ユーザーから LINE 経由で以下の要件を受領した（5/18 セッション）:
- 対象一覧（ticker リスト）の生成
- ソルジャーの並列呼び出し
- ソルジャー回答を 1 本のインデックス CSV に集約（status 列で 7 状態表現）
- クラッシュリカバリ（途中 ticker からの再開）
- 起動時引数: モード（build / resume）と並列数
- 進捗 LINE 通知は不要、ログには逐次記載
- 細部設計は任意（assistant に一任）

### 設計上の重要決定

1. **コマンダー / ソルジャー責務分離** — 本スキルはインデックス管理と並列起動のみ。PDF 読み・JSON 生成はソルジャーに完全委譲（Agent ツール経由起動）
2. **1 ファイル統合インデックス** — `orders_index.csv` 1 本に status 列で 7 状態（pending / completed / completed_partial / failed_* 4 種）を表現（ユーザー指示「ファイル 1 つのみ」）
3. **build / resume 二モード** — build は BQ 取り直し + 全件 pending 初期化、resume は既存 `status=pending` のみ処理
4. **failed_* は resume で再投入しない** — ユーザー指示「failed系スキップ」。再試行したい場合は手動で pending に書き戻す前提
5. **バッチ並列方式採用** — 動的補充ではなく「N 件同時起動 → 全完了待ち → 次 N 件」。Claude Code の Agent ツール 1 メッセージ内並列起動の特性に合わせた
6. **インデックス更新は全行書き直し + 原子置換** — 並列ソルジャー戻り値は ticker 単位で排他なので、バッチ完了時に 1 回まとめて書き直し（append 衝突を回避）
7. **ログは別ファイル append-only TSV** — `orders_log.tsv` に 1 イベント 1 行で逐次記録（ユーザー指示「ログに逐次記載」）
8. **ソルジャー応答異常は据え置き** — STATUS パース不能等の異常は `pending` のまま据え置き、コマンダーは中断せず次バッチへ。次回 resume で自動再投入

---

## レビュー観点

### 1. 責務分離（コマンダー / ソルジャー境界）

- ソルジャー（PDF 読み / JSON 生成 / STATUS 判定）の **領域を踏まない** 構造になっているか
- 「ソルジャーは Agent ツール経由で起動、直接 Read/Write/Bash で代行しない」明示の徹底ぶり
- ソルジャー戻り値（`TICKER:` / `STATUS:` 等）への依存が、ソルジャー §Step 8 と完全一致しているか

### 2. インデックス CSV 設計の整合

- 7 状態（pending / completed / completed_partial / failed_no_bq_records / failed_no_gcs_files / failed_pdf_unreadable / failed_no_data）が **ソルジャー §STATUS 一覧と整合**しているか（過不足・名前ずれなし）
- 列定義（ticker / status / docs_found / docs_read / docs_with_data / json_path / json_bytes / reason / updated_at）が **ソルジャー戻り値から一意にマップ**できるか
- `reason` の取り方（failed_* 時のみ ID 文字列、それ以外は空文字）が運用可能か
- UTF-8 BOM なし / `\n` 改行 / カンマ区切り / クォーティング省略の決定が、後工程で pandas 等で読まれた時に問題を起こさないか

### 3. 並列実行の安全性

- バッチ並列（同時 N 件 → 全完了待ち → 次 N 件）でインデックス CSV に書き込み衝突が発生しないか
- 1 ticker = 1 行という ticker 単位排他で「同じ行を複数ソルジャーが触る」状況が起き得ないか
- 「全行読み込み → 該当行を新 STATUS で上書き → `.tmp` → `mv`」の原子置換が、途中クラッシュで CSV を破損させないか
- `parallel >= 10` でログ警告のみで上限キャップしない方針が運用上妥当か（ユーザー指示「上限なし」だが、API/BQ 側の現実的な懸念はないか）

### 4. resume モードの正しさ

- 既存 `orders_index.csv` から **`status=pending` のみ**抽出する仕様が、`failed_*` スキップ要件を満たすか
- `mode=resume` でファイル無し → エラー終了の挙動が、ユーザーの「build → resume」の使い方を守れるか
- `mode=build` で既存ファイル上書きする挙動が、再ビルド時に **過去の completed を消す副作用**を持たないか（消えるが意図通りか）
- ソルジャー応答異常 → `pending` 据え置き → 次回 resume で再投入、というフローが「`failed_*` スキップ」と矛盾しないか

### 5. ラッパー（command）との整合

- `.claude/commands/tdnet-orders-commander.md` の引数受け渡し規約（`mode=build parallel=3` / `build 3` / Agent prompt 直書き）が **3 形式すべて解釈可能**な書き方か
- スキル本体の役割境界がラッパーにも明記されているか（ソルジャーは必ず Agent ツール経由）
- 冒頭出力規約 `🎯 [tdnet-orders-commander] mode={mode} parallel={parallel}` がスキル本体・ラッパー両方に書かれているか

### 6. 禁止事項の網羅性

- 7 項目の禁止事項（`failed_*` 再試行禁止 / `parallel` キャップ禁止 / ソルジャー肩代わり禁止 / Python ワーカー禁止 / バッチ同時実行禁止 / インデックス append 禁止 / resume 時インデックス上書き禁止）に **過不足がないか**
- 「failed_* 再試行禁止」と「ソルジャー応答異常 → pending 据え置き → resume で再投入」が **矛盾していないか**（前者は failed_* STATUS、後者は STATUS 未確定なので別物）

---

## スコープ外（レビュー対象外）

- ソルジャー本体（`skills/tdnet_orders_extract.md` / `.claude/commands/tdnet-orders-extract.md`）の内容（204 でレビュー済み）
- 既存 60 社分の JSON データ品質
- BQ テーブルスキーマ・GCS バケット配置（プランMDで確定済み）
- 089/042 知見MD の内容（ユーザー指示で参照禁止）
- LINE 会話モード関連の運用ルール（068 知見MDの範囲）

---

## レビュー結果記入欄

（code-reviewer から返却された指摘・提案を以下に追記）

---

# コードレビュー: TDnet 受注高抽出コマンダー（/tdnet-orders-commander）

- 日時: 2026-05-18 (JST)
- 対象:
  - `skills/tdnet_orders_commander.md`
  - `.claude/commands/tdnet-orders-commander.md`
- パターン: 1（新規実装の妥当性レビュー）
- レビュアー: Claude (code-reviewer runbook, sub-agent)

---

## 【サマリー】

- 変更の要約: 受注高抽出ソルジャー（1 銘柄 1 起動）を並列起動して結果を 1 本の `orders_index.csv` に集約するオーケストレータを新規作成。`mode=build|resume`・`parallel` の 2 入力で動作し、バッチ並列（同時 N 件 → 全完了待ち → 次 N 件）で進む。インデックス更新は全行書き直し + `mv` 原子置換、ログは `orders_log.tsv` に append-only TSV。failed_* は resume で再投入しない。
- 品質評価: **B** — 責務分離・7 状態の整合・バッチ並列方針はほぼ妥当で、ソルジャー（204）と二人三脚で動く設計の骨格は通っている。ただし (1) 並列ソルジャー戻り値と起動時 ticker の照合手順が未定義、(2) `mode=build` の破壊性（既存 completed/failed が全て pending に戻る）が CLAUDE.md §4.4 破壊的操作原則と整合していない、(3) `reason` カラムにソルジャーの「reason 列値（`no_bq_records`）」を入れるのか「failed prefix 付きの STATUS 名」を入れるのかが曖昧、(4) 3 形式引数の解釈手順がスキル本体に明文化されておらず assistant の解釈に依存、(5) ログ TSV のヘッダ書き出しタイミング未定義（resume 時の二重ヘッダリスク）— の 5 点で運用前に詰めるべき。
- 主要リスク:
  1. 並列起動した Agent の戻り値で「ticker A 用に起動したのに `TICKER: B` が返る」事故を防ぐ照合ステップが Step 4 にない（バッチ並列の根幹）
  2. `mode=build` が既存 completed 数百件を即座に pending に戻す副作用を持ち、警告も dry-run もない（ユーザー指示「上書き」を技術的には満たすが §4.4 違反）
  3. `reason` フィールドの「ソルジャー reason 列値（`no_bq_records`）」と「インデックス status カラム値（`failed_no_bq_records`）」の対応が、コマンダー本文 §4 と §1 で別の語彙を使い、JSON マップ方向がブレている

## 【重大な指摘】（即修正）

### #1 ソルジャー戻り値 TICKER と起動時 ticker の照合が抜けている（並列誤マップ事故）

- 箇所: `skills/tdnet_orders_commander.md:144-160`（Step 3 バッチ並列ループ）/ `skills/tdnet_orders_commander.md:176-196`（Step 4 ソルジャー戻り値の解析）
- 事象: 1 メッセージで `parallel` 個の Agent ツールを並列起動し、`parallel` 個の戻り値を受信したあと「ticker 単位で行を更新」と書かれているが、**戻り値の `TICKER:` 行と起動時に指定した ticker が一致するかの検証ステップが無い**。「並列ソルジャー間で同じ行を書き合うことは無い（ticker 単位で排他）ため衝突なし」(L171) は前提として、その前提を**検証する手順そのものが書かれていない**。
- トリガー:
  - (a) Agent ツールが戻り値を入れ違って返した場合（並列実行の戻り値順は仕様上保証されないことがある）
  - (b) ソルジャー側のコピペバグで `TICKER:` 行に別 ticker を埋め込んでしまった場合（ソルジャー §Step 8 の `TICKER:` は実装側の自由記述）
  - (c) ソルジャーが文字列テンプレートのまま `TICKER: {ticker}` を返してしまった（プレースホルダ未展開バグ）
- 影響: ticker A の処理結果（`failed_no_data` 等）が ticker B の行に書き込まれ、インデックスが**サイレントに破損**する。バッチ並列 N=10 で 10 件並べると、特に Claude Code の Agent 並列実行で起こる「戻り値の呼応関係が prompt 順と同じ」前提が崩れた瞬間に発火する。インデックス全体の信頼性が失われる。
- 根拠: Step 3 の prompt テンプレートは `ticker={ticker}` を含むが、Step 4 §「ソルジャー戻り値の解析」(L176-196) では「各行を `KEY: VALUE` で分解」「`STATUS` が...の場合 → `reason` は...」とフィールドごとのマップ規約しか書かれず、`TICKER: XXXX` と起動時 ticker が一致しているかの照合チェックが無い。L171 の「ticker 単位で排他」が破綻すれば全体が崩れる構造。
- 推奨対応 (**[方向性]**):
  - Step 4 冒頭に以下のような **TICKER 照合ステップ**を追加:
    > 各 Agent 戻り値について、`TICKER:` 行の値が起動時の `ticker` と一致するかを最優先で検証する。不一致 or `TICKER:` 行欠落の場合は、その戻り値を**信頼せず破棄**し、当該 ticker（起動時に指定した方）を `pending` 据え置きとしてログ `error ticker=XXXX kind=ticker_mismatch returned=YYYY` を append。
  - 起動時に「ticker と Agent 呼び出しを 1:1 で対応付ける」内部マップ（例: `pending_batch = {ticker_A: agent_call_1, ticker_B: agent_call_2, ...}`）を保持し、戻り値を**起動順ではなく `TICKER:` 値で**辞書引きする手順を Step 3 末尾に追加
  - 上記検証で破棄したケースは「ソルジャー応答異常時」(L197-205) と同じ `pending` 据え置きフローに合流可能（ロジック追加は照合 + ログ kind の追加のみで足りる）

### #2 `mode=build` の破壊性が CLAUDE.md §4.4 破壊的操作原則と整合していない

- 箇所: `skills/tdnet_orders_commander.md:100-129`（Step 2 `mode=build`）/ `skills/tdnet_orders_commander.md:247`（禁止事項末尾の「resume で上書きしない」）
- 事象: `mode=build` は「BQ で対象 ticker を全件取得して `orders_index.csv` を**既存ファイル上書き**」「すべて `status=pending` で書き直す」設計（L102, L122-127）。**過去の完了済み行（数百件規模の `completed` / `failed_*`）が無警告で pending に戻され**、続けて Step 3 が走るとソルジャーが全件再起動されて重複処理する。
- トリガー: ユーザーが「resume と build を打ち間違えた」「最初から build したつもりが 2 回目に build を再実行した」「既存ファイル有無を確認せず build を撃った」のいずれかで発火。Phase 2 で 1000+ 銘柄を回した直後に発火するとソルジャー再起動コスト（PDF DL 含む）と Agent token 消費が数百回分やり直しになる。
- 影響:
  - データ損失: 既存の `failed_no_data`（偽陽性業種の判定結果）が消滅し、次の build → 全件 pending → 再処理で同じ判定を再度通すコスト発生
  - 課金増: ソルジャー 1 回 = BQ + GCS DL + Agent token。1000 件全件再起動で再度全コストが発生
  - 監査不能: build 直後にログ `run_start mode=build` のみ追記されるが、上書きされた既存ファイル内容のバックアップは取らない（L102 の「既存ファイル上書き」のみ）
- 根拠: L102「既存ファイル上書き」L120「すべて `status=pending`」L129「ログに `run_start mode=build target_count=N`」のみで、既存ファイルの有無確認・バックアップ・確認プロンプト・dry-run のいずれもなし。L247 の禁止事項に「`mode=resume` で上書きしない」はあるが、`mode=build` 側には**既存内容の保護機構が一切ない**。
- 推奨対応 (**[方向性]**):
  - Step 2 `mode=build` 冒頭に **既存ファイル退避ステップ**を必須化:
    > `orders_index.csv` が既に存在する場合は、上書き前に `orders_index_YYYYMMDD_HHMMSS.csv.bak` にコピー退避する（`cp` で原本残し）。ログに `index_backup path=...` を append。
  - 「build による上書きは破壊的操作」を禁止事項に追加 (`build 実行前にユーザーから「全消し OK」の明示同意を取る、または上記退避を必須化のいずれか`)
  - **`mode=build` 完了直後 / Step 3 開始直前に**「既存 completed/failed の件数」と「新しく pending 化された件数」を出力 → ユーザーが Ctrl+C で止める余地を残す（推奨ではあるが手順違反になり得るので「方向性」止め）
  - CLAUDE.md §4.4「dry-run → 小範囲10件確認 → 全件展開」の建付けと整合するため、build 後に Step 3 の最初のバッチを実行する前に「既存ファイルが上書きされた事実 + 退避先パス + 新規 pending 件数」をユーザーに 1 回報告するゲートを設ける

### #3 `reason` カラムの語彙が status カラムと食い違う（マッピング方向の曖昧さ）

- 箇所: `skills/tdnet_orders_commander.md:51`（reason 列定義）/ `skills/tdnet_orders_commander.md:190-194`（戻り値解析の reason マップ規約）/ ソルジャー側 `skills/tdnet_orders_extract.md:333-342` STATUS 表
- 事象: コマンダー §1 のインデックス列定義で `status` カラムは `failed_no_bq_records` 等の **prefix 付き 7 値**、`reason` カラムは「`failed_*` 時のみ簡潔な英語 reason（ソルジャー §STATUS 一覧参照）」(L51) と書かれている。ソルジャー §STATUS 一覧の `reason` 列は `no_bq_records` / `no_gcs_files` / `pdf_unreadable` / `no_data_classified_as_false_positive` （**prefix 無し**）。コマンダー §4 §「ソルジャー戻り値の解析」では「`STATUS` が `failed_*` の場合 → `reason` を §STATUS 一覧の reason 列（`no_bq_records` 等）にマップ」(L194) と書かれており、**status と reason が prefix 有り/無しで非対称**になる。
- トリガー: pandas で `orders_index.csv` を読んで集計するときに、`reason` 列の値ドメインが「status 列から `failed_` を除去した文字列」と一致するか/しないかが判定不能。`failed_no_data` だけ reason 側が `no_data_classified_as_false_positive` という独自命名（ソルジャー §STATUS 一覧）で、機械的な `lstrip('failed_')` では復元できない。
- 影響:
  - 集計コードが status と reason の対応を「失敗カテゴリの正規キーがどっち？」で迷う。status を正規キーとするか reason を正規キーとするかが定義されない
  - 失敗 4 種のうち 3 種は prefix 除去で対応するが、`failed_no_data` の reason だけが `no_data_classified_as_false_positive` で対応がズレるため、後工程の `reason == "no_data"` のような単純なフィルタが効かない
- 根拠: コマンダー §1 の `status` カラム値ドメインが `failed_no_bq_records` 等の 4 値、ソルジャー §STATUS 一覧 §「reason（_failed.csv 用）」列が `no_bq_records` / `no_gcs_files` / `pdf_unreadable` / `no_data_classified_as_false_positive` の 4 値で、最後の 1 つだけが status 名と非対称。`completed` / `completed_partial` のときに reason が空文字で十分な根拠も §1 のみで、§4 マップ規約には書かれていない。
- 推奨対応 (**[方向性]**):
  - **(A 案)** `reason` カラムを廃止し、`status` カラム 1 本に集約する。`failed_*` STATUS は自己説明的なので reason 重複は冗長。`completed_partial` の補足情報（`partial; 3_of_6_docs_read` 等）は別カラム `note`（ソルジャー JSON の `note` フィールドをそのままコピー）に移す
  - **(B 案)** `reason` カラムを残すなら、ドメインを「status から `failed_` を除去した値」と**機械的に定義**し直す。`failed_no_data` の reason も `no_data` に統一（ソルジャー側 §STATUS 一覧の reason 値命名を直すか、コマンダー側で `failed_no_data` → reason=`no_data` を独自マップ）
  - どちらの案を採るかは「ソルジャー側の reason 列の語彙を変えていいか」次第。ソルジャー側を直すなら 204 のレビュー指摘 #1 (note の契約) と同じく「JSON 契約の確定」が先。
  - **記載先**: 採用案を決めたら、コマンダー §1 列定義表とソルジャー §STATUS 一覧の reason 列を**同時に**改訂する（片方だけだと再発）

### #4 3 形式の引数（`mode=K=V` / 位置引数 / Agent prompt 直書き）の解釈手順がスキル本文に未定義

- 箇所: `skills/tdnet_orders_commander.md:24-30`（呼び出し方）/ `.claude/commands/tdnet-orders-commander.md:1-12`
- 事象: スキル本文 L24-27 で「スラッシュコマンド / 位置引数 / Agent prompt 直書き」の 3 形式が呼び出し例として書かれているが、**assistant がこれらをどうパースして `mode` と `parallel` を取り出すかの手順が無い**。ラッパー側 `.claude/commands/tdnet-orders-commander.md:9-12` でも例だけ並んでいて解釈規約が無い。
- トリガー:
  - (a) ユーザーが `/tdnet-orders-commander build` のように parallel を省略した場合、デフォルト `3` を当てる手順が無い（L21 の表に「デフォルト 3」とは書かれているが、入力パース手順に組み込まれていない）
  - (b) ユーザーが `/tdnet-orders-commander mode=resume` だけ書いた場合（位置引数なし、key=value 1 つ）の解釈
  - (c) `mode=BUILD`（大文字）や `mode=Build` の正規化要否
  - (d) `parallel=0` や `parallel=-1` の境界値処理
- 影響: assistant が初回起動時に「mode 引数が無いように見えるので確認しますか」のような対話に陥る、または不正値（`parallel=0`）で空ループに入る可能性。Phase 2 で 1000+ 銘柄を回す前提なら、入力 I/F は assistant の解釈に依存させず明示すべき。
- 根拠: スキル本文 §「呼び出し方」(L24-30) は「呼び出し例」のリストのみで、パース規約・デフォルト適用順・正規化（lower-case 化、trim）・境界値（parallel >= 1）の検証手順が無い。
- 推奨対応 (**[方向性]**):
  - スキル本文 §入力 直下に「**引数解釈の手順**」セクションを追加:
    1. 渡された prompt 内に `mode=X` `parallel=Y` の `key=value` 表現があれば優先採用
    2. 上記が無く位置引数のみの場合は、第 1 引数を `mode`、第 2 引数を `parallel` として解釈
    3. `mode` が `build` / `resume` 以外（大文字含む正規化後）ならエラー終了
    4. `parallel` が未指定なら `3`、`< 1` ならエラー終了、`>= 10` なら警告（既存 L162-163 と統合）
  - 上記をラッパー側にも 1 ブロックコピー、または「§引数解釈はスキル本文に従う」と委譲明示

### #5 ログ TSV のヘッダ書き出しタイミング未定義（resume 時の二重ヘッダリスク）

- 箇所: `skills/tdnet_orders_commander.md:68-79`（ログ TSV 仕様）/ `skills/tdnet_orders_commander.md:85-96`（Step 1 事前ディレクトリ作成）/ `skills/tdnet_orders_commander.md:129, 141`（run_start ログ append）
- 事象: ログ TSV `orders_log.tsv` は「append-only、ヘッダ行 1 行目に書く」(L79) と仕様にあるが、**ファイル新規/既存判定とヘッダ書き出しタイミングの規約が無い**。Step 1 ではディレクトリ作成のみ（L86-96）、Step 2 `mode=resume` では `run_start mode=resume target_count=N` を append するだけ（L141）。
- トリガー:
  - (a) 初回 `mode=build` 起動時にヘッダなしで `run_start` が書き込まれる → CSV ライブラリで読むと 1 行目が `run_start` 行と誤認される
  - (b) `mode=resume` の 2 回目以降、ヘッダ追加処理が条件分岐されていないと「既存ファイル末尾にヘッダ行が混ざる」可能性
  - (c) ログを後工程で pandas `read_csv(sep='\t')` する際に、ヘッダ位置が一意でないとカラム名が空文字になる
- 影響: ログがあとから機械的に集計できなくなる（ヘッダ位置がランダム）。または「ファイルが空ならヘッダ → append」のロジックを assistant が独自実装してバージョン違いの実装が混在する。
- 根拠: L79「ヘッダ行 1 行目に書く」の指示と Step 1/2 の手順記述に「ファイル存在チェック → 空ならヘッダ書き出し → 以降 append」の手順が組み込まれていない。
- 推奨対応 (**[方向性]**):
  - Step 1 末尾に「**ログファイル初期化**」サブステップを追加:
    > `orders_log.tsv` が存在しない、または `0 バイト` の場合のみヘッダ行 `timestamp_jst\tticker\tevent\tdetail\n` を書く。既存ファイルがあればそのまま append。
  - Step 2 の `run_start` append はその後で行う
  - 同様の規約をインデックス CSV ヘッダにも適用済みかを確認（`mode=build` で全行書き直しの中にヘッダ 1 行目を含めれば自然に揃うが、明文化はあると親切）

## 【改善提案】（可読性・保守性）

### #1 `parallel >= 10` 警告ログの追記タイミングが「Step 1 のログ」と曖昧

- 箇所: `skills/tdnet_orders_commander.md:162-163`
- 現状: 「`parallel >= 10` の場合は Step 1 のログに `parallel_high parallel={N}` を追記」と書かれているが、Step 1 はディレクトリ作成のみ。実際は `run_start` 直後（Step 2 末尾 L129/141）または Step 3 開始直前のほうが意味的に正しい。
- 提案: 「Step 2 の `run_start ...` ログの直後に `parallel_high parallel={N}` を 1 行 append」へ修正。

### #2 `mode=build` で BQ 取得結果 0 件のときの挙動が未定義

- 箇所: `skills/tdnet_orders_commander.md:100-129`
- 現状: BQ から 0 件が返った場合、`orders_index.csv` をヘッダ 1 行のみで書き出し、Step 3 のバッチループは空回り → Step 5 で TOTAL=0 のサマリーが出る、と推測されるが明示されていない。
- 提案: Step 2 `mode=build` 末尾に「BQ 結果が 0 件の場合は `run_end target_count=0` をログに書いて即終了。Step 3 はスキップ」と 1 行追加。

### #3 並列上限の Claude Code harness 側制約への言及なし

- 箇所: `skills/tdnet_orders_commander.md:162-163`
- 現状: 「`parallel` を勝手にキャップしない」(L242) が原則で、上限なしとされているが、Claude Code の Agent ツール 1 メッセージ並列起動には harness 側で実用上の上限（数十〜100 程度）がある。`parallel=200` 等の極端値で harness 側がリジェクトする挙動は assistant が事前に知っておくと安全。
- 提案: 注意事項に「`parallel` は harness の Agent 並列起動上限を超えて指定された場合の挙動は未確認。実用上は 1〜10 を推奨」と 1 行追加（指示違反ではなく注意喚起）。

### #4 ソルジャー戻り値の `JSON_BYTES` パース失敗時の挙動が未定義

- 箇所: `skills/tdnet_orders_commander.md:191-196`
- 現状: 「`JSON_PATH: (none)` または欠落 → `json_path` は空文字、`json_bytes` は `0`」(L195) は書かれているが、`JSON_BYTES:` 行があっても非数値（`JSON_BYTES: abc`）のケースが未定義。
- 提案: 「`JSON_BYTES` がパース不可なら `0` 扱い + ログに `error kind=invalid_json_bytes` を append」と 1 行追加。

### #5 インデックス CSV クォーティング省略の前提が脆い

- 箇所: `skills/tdnet_orders_commander.md:260`
- 現状: 「`reason` が `,` を含まない短い英語 ID のみなのでクォーティング省略で十分」(L260) と書かれているが、ソルジャー側で reason 文字列を将来拡張した際に `,` が混入する可能性は残る。会社名（n）も `ticker` 行には入らないので現状はリスク低だが、将来 `note` カラムを足すと爆発。
- 提案: 注意事項に「将来 `reason` / 新規カラムに `,` を含む可能性が生じた段階で pandas の `to_csv(quoting=QUOTE_MINIMAL)` 相当に切り替える」前提を 1 行追加。または最初から minimum quoting で書く規約に変える。

### #6 Step 4「全行書き直し + mv 原子置換」の Windows 環境での非保証

- 箇所: `skills/tdnet_orders_commander.md:167-172`
- 現状: 「`orders_index.csv.tmp` に書いて `mv` で原子置換」とあるが、Windows の NTFS では宛先存在時の `rename(2)` は POSIX のような原子的な置換を保証しない（CRT 実装によっては「削除 → 移動」になる）。途中クラッシュで `orders_index.csv` が消失 → 次回 `mode=resume` でエラー終了の可能性。
- 提案:
  - 注意事項に「Windows 環境の `mv` は POSIX 原子置換を保証しない。クラッシュ復旧時は `orders_index.csv` が無くても直前のバックアップ（推奨指摘 #2 で導入する `.bak`）から復元する」と 1 行追加
  - またはコマンダー側で書き直し直前に毎回 `orders_index.csv.bak` を作る（バックアップ 2 重化）。コストは小さい（数百行の CSV）

### #7 ソルジャー応答異常時のログ `raw_excerpt` でセンシティブ情報がログに混入し得る

- 箇所: `skills/tdnet_orders_commander.md:204`
- 現状: 「ログに `error ticker=XXXX kind=soldier_response_invalid raw_excerpt="..."`（最初 100 文字）」と書かれているが、ソルジャーが返す可能性のあるエラー本文（API key 漏洩 / GCS URL に含まれる token 等）が混入する場合の検閲規約なし。
- 提案: 「`raw_excerpt` は 100 文字に切ったうえで `gs://` / `key=` / `token=` / `Bearer ` を含む場合は当該部分を `[REDACTED]` に置換」と 1 行追加。優先度低（実環境でこれら混入の可能性は薄い）。

## 【確認できなかった事項】

- **Claude Code Agent ツールの並列実行戻り値の順序保証**: 重大指摘 #1 の前提「Agent ツールが 1 メッセージで N 個並列起動した場合、戻り値順は呼び出し順と一致するか」は harness 仕様に依存し、本レビューでは確認できない。仮に「呼び出し順 == 戻り値順」が harness で保証されていれば #1 のリスクは半減するが、その前提が明文化されていない場合は照合ステップを入れる方が安全側。
- **Windows Git Bash `mv` の置換セマンティクス**: 改善提案 #6 の根拠となる「Git Bash の `mv` が Windows 上で原子置換を保証するか」は msys2 / cygwin の実装差で揺れる。実環境で `dd` 等でファイル破壊を模擬して挙動確認する必要があるが、本スキル開発の範囲を超えるので「方向性」止め。
- **ソルジャー側 reason 列の `no_data_classified_as_false_positive` という長い命名**: ソルジャー 204 のレビュー時に決まった命名だが、コマンダーとの整合性（重大指摘 #3）の観点で再考の余地がある。ソルジャー側を直すか、コマンダー側でマップするかは本タスク（コマンダー spec）の範囲を超える。
- **`mode=build` 後の Step 3 開始までのユーザー確認ゲート**: 重大指摘 #2 の推奨にあるゲート設計は、現在のスキル MD ではユーザー対話を前提にしていないので、自動実行モードとの整合（バッチ夜間実行で人がいない場合の挙動）が未確認。
- **`parallel >= 10` の運用妥当性**: ユーザー指示「上限なし」だが、Phase 2 の実運用で `parallel=20` 等を試した実測値が無い。実測後、注意事項に推奨値を追記する余地あり。

---


---

## レビュー対応記録（提出元）

**対応日**: 2026-05-18
**対応者**: Claude (メインエージェント)
**スキル命名**: 受注高抽出コマンダー（ユーザー指示で既定）

### 重大指摘 5 件

- **#1 TICKER 照合手順なし** → [採用] Step 3 末尾に内部マップ `expected_tickers` 保持、Step 4A に照合ステップ追加。不一致 / `TICKER:` 欠落は当該戻り値を破棄して `pending` 据え置き + ログ `ticker_mismatch returned=YYYY`。harness の戻り値順序保証が不明な前提で安全側に倒す。
- **#2 mode=build 破壊性** → [採用] Step 2A に既存ファイル退避（`orders_index_YYYYMMDD_HHMMSS.csv.bak`）必須化、退避失敗時 build 中止、Step 2D にユーザー確認ゲート（既存件数 + 退避先 + 新規 pending 件数を画面出力）。禁止事項に「バックアップ取らずに上書きしない」追加。CLAUDE.md §4.4 整合。
- **#3 reason 語彙非対称** → [採用 C案] reason 列ドメインを「`status` から `failed_` を除去した形」（`no_bq_records` / `no_gcs_files` / `pdf_unreadable` / `no_data`）に統一、ソルジャー側 reason `no_data_classified_as_false_positive` は **コマンダー側で `no_data` に機械マップで吸収**。ソルジャー本体不変、コマンダー §1 reason 列ドメイン表 + Step 4B で明示。
- **#4 引数解釈手順未定義** → [採用] §入力直下に「引数解釈の手順」5 ステップ（key=value 優先 → 位置引数 fallback → mode 正規化 + 検証 → parallel default + 検証 → 冒頭出力）を追加。ラッパー側は「正本はスキル本文」と委譲明示。
- **#5 ログヘッダタイミング** → [採用] Step 1 末尾に「ログファイル初期化」サブステップ追加。`orders_log.tsv` が存在しない or 0 バイトの時のみヘッダ書き出し、既存（>0）は追加しない。resume 二重ヘッダ防止。

### 改善提案 7 件

- **#1 parallel>=10 警告タイミング** → [採用] Step 1 ではなく Step 2 `run_start` 直後に `parallel_high parallel={N}` を append、画面にも 1 行警告。
- **#2 BQ 0 件挙動** → [採用] Step 2E に明示。`orders_index.csv` をヘッダ 1 行のみで書き、`run_end target_count=0 reason=no_target` をログ append して即終了。
- **#3 harness 並列上限** → [採用] 注意事項に「実用は 1〜10 を推奨。`parallel=20` 等は harness 側 reject 可能性あり」追記。
- **#4 JSON_BYTES パース失敗** → [採用] Step 4B に「非数値は 0 扱い + ログ `invalid_number field=... raw=...`」明記。同手順を DOCS_FOUND / DOCS_READ / DOCS_WITH_DATA にも適用。
- **#5 CSV クォーティング** → [採用] §1 ファイル仕様で `pandas.to_csv(quoting=csv.QUOTE_MINIMAL)` 相当を最初から採用。注意事項にも記載。
- **#6 Windows mv 原子性** → [採用] Step 4C で書き直し直前にも最新 `orders_index.csv.bak` を作る二重バックアップ。注意事項に「Git Bash `mv` は NTFS で POSIX 原子性非保証」記載 + 復元ルール（最新 .bak or 起動時 timestamp .bak から復元）追記。
- **#7 raw_excerpt センシティブ情報** → [採用] Step 4D で `gs://` / `key=` / `token=` / `Bearer ` を含む部分は `[REDACTED]` 置換明記。

### 確認不能 5 件

本タスクの範囲外（外部依存事項）。本対応では以下のように「不明前提で安全側に倒す」設計判断で吸収した:

- **A. harness 並列戻り値順序保証**: 不明前提で TICKER 照合（重大#1）を必須化
- **B. Windows mv 原子性**: 不明前提でバックアップ二重化（改善#6）を必須化
- **C. ソルジャー reason 命名再考**: コマンダー側マップ吸収（重大#3 C案）でソルジャー触らずに解決
- **D. mode=build 確認ゲートと自動実行整合**: 画面出力 + ログ両方残しで事後検証可能化（重大#2）
- **E. parallel>=10 実測値なし**: 注意喚起のみ（改善#3）、実測後に推奨値再評価

### 変更ファイル

- `skills/tdnet_orders_commander.md` — 全面改訂
- `.claude/commands/tdnet-orders-commander.md` — ラッパー側引数解釈の委譲明示
- Dropbox 同期: `C:/Users/zonekun/Dropbox/stock/temp/tdnet_orders_commander/` に 2 ファイル転送済み

---

**2026-05-18 ファイル名変更（候補C 採用 / ユーザー指示）**:
- `skills/tdnet_orders_commander.md` → `skills/orders_commander.md`
- `.claude/commands/tdnet-orders-commander.md` → `.claude/commands/orders-commander.md`
- スラッシュコマンド: `/tdnet-orders-commander` → `/orders-commander`
