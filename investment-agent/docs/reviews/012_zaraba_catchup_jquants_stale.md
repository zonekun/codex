# MD AI可読性レビュー: ザラ場ツール catchup J-Quants 放置バグ — 知見ファイル構成と VM 同期誤認の原因分析

- 日時: 2026-04-28 13:15 JST
- 対象: `docs/knowledges/tools/066_zaraba_tool.md`, `docs/knowledges/tools/051_windows_linux_vm_guide.md`, `scripts/zaraba_earnings.py`
- パターン: 2 (誤読・ミス原因レビュー)
- レビュアー: Claude (md-reviewer skill)
- 出力先: `docs/reviews/012_zaraba_catchup_jquants_stale.md`
- 提出 MD 内容: メインエージェントからの事象報告（catchup が J-Quants のまま放置 + Google Drive 同期誤認）

---

## 【サマリー】

- レビュー対象の要約: ザラ場ツール知見ファイル (066) が watch の TDnet 移行を記載しつつ catchup の整合性に触れず、加えて AI が VM 同期を「Google Drive 経由」と誤認した事象の原因を、066 と 051 の構成から分析する
- AI可読性評価: **C** — 066 は複数箇所で旧データソース（J-Quants）を現行仕様として記載しており、サブコマンド間のデータソース不整合が構造的に不可視
- 誤読リスク評価: **C** — 「既知の問題」見出しが watch 専用に見え catchup の同種問題を想起させない。051 は Google Drive に言及しないが、CLAUDE.md のプロジェクトパス `C:\gdrive\...` が Google Drive 同期の連想を誘発する
- 主要リスク:
  - 066 の概要・サブコマンド表・データソース節・スクリプト docstring が J-Quants を現行として記載し、AI がこれらを正とみなす
  - 066「既知の問題: watch コマンドが機能しない」が watch 限定見出しのため catchup の同種バグに気付く導線がない
  - 066 に「サブコマンド間のデータソース整合性」という横断的な検証観点が欠落
  - CLAUDE.md `C:\gdrive\...` パスが Google Drive 同期の存在を暗示し、051 を読まなくても AI が「Drive 経由で自動同期」と推測する余地がある

---

## 【Markdown 品質評価】

### Accuracy / 正確性

**066_zaraba_tool.md**:
- L24「概要」: 「J-Quants API でポーリングし」は watch/catchup とも旧実装の記述。現行は TDnet HTML ポーラー + XBRL 抽出。**実コードと不一致**
- L32 サブコマンド表: `watch` の用途が「J-Quants ポーリング + スコアリング + リアルタイム表示」。コード (L1149-1188) は TDnet ベース。**不一致**
- L31 サブコマンド表: `catchup` の用途が「発表済み DiscNo をキャッシュに記録」。修正後コード (L939-1054) は TDnet 取得 + XBRL 抽出 + スコアリング + results 追加。用途説明が不完全
- L72-75「当日ポーリング（J-Quants API）」: watch/catchup とも TDnet に移行済みだが、見出し・内容とも J-Quants を現行として記載。**stale**
- L79-93 累計→Q単独変換: 「J-Quants `/v2/fins/summary` の OP・NP 等は累計値」は XBRL 経由でも同じロジック適用のため結論は変わらないが、データ出所の記述が旧い
- L217「既知の問題: watch コマンドが機能しない」: ステータスに「ほぼ開発完了（2026-04-13）」「残作業: watch サブコマンドへの統合テスト」とあるが、実際には watch は既に TDnet 統合済み (2026-04-14)。**stale ステータス**
- L384「将来 TODO」: 「【ブロッカー】watch を TDnet XBRL ベースに改造」は既に完了済み。**stale TODO**

**scripts/zaraba_earnings.py docstring** (L2-12):
- L4「J-Quants API でポーリングし」、L10「watch: ザラバ監視（J-Quants ポーリング + スコアリング + rich Live 表示）」はいずれも旧実装。AI がソースの docstring を信頼して J-Quants 前提で修正を試みるリスク

**051_windows_linux_vm_guide.md**:
- 内容自体に Google Drive の言及はなく、git + GCS の2方式を正確に記載。051 単体の正確性は問題なし

### Completeness / 完全性

**066**: watch → TDnet 移行の事実は「既知の問題」「設計方針: TDnet ポーリングソースの二層構成」「落とし穴: yanoshin の遅延」に分散記載されているが、**catchup のデータソースがどこか**は一切記載されていない。catch up の実装詳細セクションが存在しない

**066**: サブコマンド間でデータソースが異なる場合の警告・注意事項がない。「watch は TDnet、catchup は J-Quants」という不整合状態が発生しても、066 を読んだ AI には検知手段がない

**051**: VM への同期方法（git push/pull + GCS secrets sync）は完全に記載。ただし「Google Drive は VM に入っていない」「VM にはファイルシステム自動同期の仕組みがない」という**否定情報が明示されていない**

### Relevance / 関連性

**066**: 「既知の問題」セクションが watch 限定の見出しになっており、J-Quants の根本問題（リアルタイム更新されない）が影響する全サブコマンドへの横展開視点が欠落。修正履歴（落とし穴セクション群）が 066 の過半を占め、現行仕様の簡潔な記述が埋もれている

### Actionability / 実行可能性

**066**: AI が「catchup を修正して」と依頼された場合、概要 (L24) → サブコマンド表 (L31) → データソース (L72-75) の順で読み、**全て J-Quants を指している**ため、J-Quants ベースで修正を試みる。「既知の問題」(L217) と「設計方針: TDnet」(L234) まで読み進めても、それらは watch 限定の文脈で書かれており catchup への適用が不明確

---

## 【AI 誤読リスク】

1. **066 L24 / L32 / L72-75**: 「J-Quants API でポーリング」が概要・サブコマンド表・データソース節の3箇所で現行仕様として記載。AI が 066 を参照して catchup を改修する場合、TDnet ポーラーではなく J-Quants API を使う修正を生成する高リスク

2. **066 L31**: catchup の用途が「DiscNo をキャッシュに記録」のみ。修正後の catchup はスコアリング + results 保存も行うため、AI が「catchup はキャッシュ記録だけ」と理解して不完全な修正を行うリスク

3. **066 L217**: 「既知の問題: watch コマンドが機能しない」は watch 限定見出し。AI が「catchup は J-Quants だが watch は TDnet に移行済み」と正しく認識した場合でも、**catchup にも同じ問題が適用される**ことを 066 から導出できない

4. **CLAUDE.md L119 `C:\gdrive\...`**: プロジェクトパスが `gdrive`（= Google Drive のジャンクション）であることから、AI が「このプロジェクトは Google Drive 上にある → Google Drive が同期してくれる → VM にも自動反映」と推論する。051 を事前に読んでいなければこの推論を止める情報がない

---

## 【MD 構成リスク】

1. **066 の構成: 旧情報が先頭、現行情報が後半に分散**
   - L24 概要（J-Quants）→ L26 サブコマンド表（J-Quants）→ L48 データソース（J-Quants）→ ... → L217 既知の問題（watch のみ TDnet 移行言及）→ L234 設計方針 TDnet
   - AI は先頭から読むため、概要とサブコマンド表で J-Quants が現行と確信した後、後半の TDnet 関連情報を「watch 限定の例外」と解釈する

2. **066 の構成: catchup の実装詳細セクションが不在**
   - watch は「設計方針: watch 時のデータ全件メモリロード」「設計方針: TDnet ポーリングソースの二層構成」で詳述
   - catchup は L31 のサブコマンド表 1 行と L162 の使い方例のみ。実装の詳細（データソース・スコアリング有無・results との結合ロジック）が記載されていない

3. **066「既知の問題」見出しの粒度**
   - 「既知の問題: watch コマンドが機能しない」は watch 固有の問題に見える
   - 実際には「J-Quants がリアルタイム更新されない」が根本原因であり、J-Quants を使う全サブコマンドに影響する。見出しが個別サブコマンドに絞られているため横展開の視点が遮断される

4. **066「将来 TODO」に完了済み項目が残存**
   - L384「【ブロッカー】watch を TDnet XBRL ベースに改造」は完了済み。AI がこの TODO を見て「まだ未実装」と判断し、watch を J-Quants から TDnet に変える作業を重複実行するリスク

---

## 【指示優先順位・文脈境界】

1. **066 と 051 の責務境界**: 066 は「推奨 VM スペック」(L293) を記載し、Linux VM での運用を前提としているが、**VM への同期方法の参照先（051）への明示的リンクがない**。066 を読んだ AI が VM 運用まで想定しても、同期方法を 051 で確認する導線がない

2. **CLAUDE.md と 051 の正本関係**: CLAUDE.md の「端末間の作業移管」(L160-) が正本で、051 は詳細ガイド。CLAUDE.md には「git push/pull + sync_push.sh/sync_pull.sh」が明記されているが、**「Google Drive は VM 同期に使わない」という否定ルールは CLAUDE.md にも 051 にもない**。AI は明示的に禁止されていないことを推測で補完する傾向があり、`gdrive` パスからの連想を止める情報が不在

3. **スクリプト docstring と 066 の不整合**: `zaraba_earnings.py` の docstring (L2-12) が J-Quants を記載し、066 の概要 (L24) と一致している。AI は「docstring と知見ファイルが一致 = 正しい」と確信を強める。実コードとの乖離に気付くには、`cmd_watch` / `cmd_catchup` の実装 (L939+, L1149+) まで読む必要があるが、docstring で確信した AI はそこまで読まない可能性がある

---

## 【パターン 2: 誤読・ミス原因分析】

### 事象

1. `zaraba_earnings.py` の `cmd_catchup` が J-Quants API のまま放置され、ザラバ中に 0 件が返るバグがあった（修正済み）
2. 修正後、メインエージェントが「Google Drive 同期で VM にも反映される」と誤発言した

### 読み手がどう解釈した可能性があるか

**事象 1（catchup 放置）**:
- 066 の概要 (L24)・サブコマンド表 (L31-32)・データソース (L72-75) がすべて J-Quants を現行として記載
- 「既知の問題」(L217) は watch 限定の見出し。catchup にも同じ問題が波及するという横展開の記載なし
- AI が 066 を参照して catchup を改修・保守しても、J-Quants が正しいデータソースと認識する
- watch の TDnet 移行を行った開発者（AI エージェント）が catchup の存在を認知しつつ、catchup のデータソースを同時に更新する義務を 066 から読み取れなかった

**事象 2（Google Drive 誤認）**:
- CLAUDE.md L119 `C:\gdrive\claude\investment-agent`（ジャンクション経由の ASCII パス）を読んだ AI が、`gdrive` = Google Drive マウントポイントと認識
- Windows 上で Google Drive が動作していることは事実（`G:\マイドライブ` のジャンクション先）
- AI が「Google Drive で同期 → VM にも反映」と推論するには、「VM にも Google Drive がある」という前提が必要だが、051 に「VM には Google Drive がない」とは書かれていない
- 051 を読んでいない状態では、この推論を阻止する情報がない

### 直接原因

**事象 1**:
- 066 L24, L31-32, L72-75 が J-Quants を現行データソースとして記載し続けている
- 066 L217「既知の問題」が watch 限定見出しで、catchup への波及を記載していない
- watch の TDnet 移行時に catchup の整合性チェックを促す記載が 066 にない

**事象 2**:
- 051 に「VM には Google Drive が入っていない」「ファイルシステム自動同期はない」という否定情報がない
- CLAUDE.md のパス `C:\gdrive\...` が Google Drive の存在を暗示し、AI が同期メカニズムを推測で補完した
- 066 が VM 運用（推奨 VM スペック L293）に言及しながら、VM への同期手順の参照先を示していない

### 根本原因

**事象 1 — サブコマンド間整合性の管理不在**:
- 066 は watch の TDnet 移行について詳細に記載した（設計方針・落とし穴・コミット履歴）が、その移行が他サブコマンドに波及するかの検証観点がテンプレートにもスキルにも存在しない
- 知見ファイルのテンプレート (`src/knowledge/templates/tools.md`) にサブコマンド間の整合性チェック項目がない
- データソース変更時の横展開チェックリストが 066 にない

**事象 2 — 否定情報の不記載**:
- 051 は「何を使って同期するか」を記載しているが、「何を使わないか」（Google Drive, rsync, scp 等）を記載していない
- AI は明示的に否定されていない手段を「使える可能性がある」と判断する。特に `gdrive` パスという強い手がかりがある場合、肯定側に振れやすい

### 誤読を許した MD 上の原因

| # | ファイル:行 | 問題 |
|---|------------|------|
| 1 | `066:24` | 概要の「J-Quants API でポーリング」が全サブコマンド共通の記述に見え、watch/catchup 個別のデータソースが不明 |
| 2 | `066:31-32` | サブコマンド表で catchup/watch ともデータソースの明示なし。用途のみ |
| 3 | `066:72-75` | 「当日ポーリング（J-Quants API）」が現行見出しとして残存。TDnet 移行後の更新漏れ |
| 4 | `066:217` | 「既知の問題: **watch** コマンドが機能しない」— watch 限定見出しが catchup への横展開を遮断 |
| 5 | `066:384` | 「【ブロッカー】watch を TDnet XBRL ベースに改造」完了済み TODO の残存 |
| 6 | `066` 全体 | catchup の実装詳細（データソース・スコアリング有無）が記載されたセクションが不在 |
| 7 | `051` 全体 | 「VM には Google Drive がない」という否定情報の不在 |
| 8 | `zaraba_earnings.py:2-12` | docstring が J-Quants を現行として記載。066 と一致するため AI の確信を強化 |

### 再発防止の方向性

**066 の構成改善**:
1. 概要 (L24) とサブコマンド表 (L31-32) を TDnet ベースの現行仕様に更新
2. 「当日ポーリング（J-Quants API）」(L72-75) セクションを「当日ポーリング（TDnet HTML + XBRL）」に書き換え
3. 「既知の問題: watch コマンドが機能しない」を「解決済み」マークに変更するか、「解決履歴」に移動
4. 「将来 TODO」から完了済み項目を削除
5. catchup サブコマンドの実装概要セクションを追加（データソース・スコアリング有無・results 結合ロジック）
6. **サブコマンド間整合性チェック注意事項を追加**: 「データソースを変更した場合、同じデータソースを使う全サブコマンドで整合性を確認すること」

**051 の補強**:
7. 「同期の基本方針」(L64-) に否定情報を追加: 「VM には Google Drive は入っていない。git + GCS 以外の自動同期手段はない」

**スクリプト docstring の更新**:
8. `zaraba_earnings.py` L2-12 の docstring を TDnet ベースに更新

---

## 【重大な指摘】（即修正）

### #1 066 の概要・サブコマンド表・データソース節が J-Quants を現行として記載

- 箇所: `docs/knowledges/tools/066_zaraba_tool.md:24`, `066:31-32`, `066:72-75`
- 問題: watch も catchup も TDnet HTML + XBRL に移行済みだが、概要・表・データソース節がすべて J-Quants を記載
- AI の誤読パターン: 066 を参照して catchup を修正する際、J-Quants API を使う修正を生成する
- トリガー: 「catchup を修正して」「ザラ場ツールのデータソースを確認して」等の指示
- 影響: 旧データソースに基づく修正が行われ、ザラバ中に 0 件が返るバグが再発する
- 根拠: `scripts/zaraba_earnings.py` L939-1054 (catchup) と L1149-1208 (watch) はいずれも `create_poller(poller_source)` + `XbrlExtractor` を使用。J-Quants は prepare 段階の BQ キャッシュのみ
- 推奨対応: 概要 (L24) を「TDnet 適時開示ポーリング + XBRL 数値抽出でリアルタイム検知し」に更新。サブコマンド表 (L32) を「TDnet ポーリング + XBRL 抽出 + スコアリング + リアルタイム表示」に更新。データソース節 (L72-75) を TDnet HTML ベースの記述に書き換え
- MD 修正だけで足りるか: 足りない。`zaraba_earnings.py` の docstring (L2-12) も同時に更新が必要。docstring が 066 と一致したまま残ると、AI が両方を参照して旧情報を確信する

### #2 「既知の問題: watch コマンドが機能しない」が watch 限定見出しで catchup への横展開を遮断

- 箇所: `docs/knowledges/tools/066_zaraba_tool.md:217-224`
- 問題: J-Quants がリアルタイム更新されない問題は watch だけでなく catchup にも影響するが、見出しが「watch コマンド」に限定されている
- AI の誤読パターン: 「watch は TDnet に移行した。catchup は J-Quants のまま。catchup に問題があるとは 066 に書かれていない」と解釈
- トリガー: catchup 関連の修正・保守時に 066 の「既知の問題」を参照
- 影響: catchup が J-Quants のまま放置される（今回の事故の再発パターン）
- 根拠: 根本原因は「J-Quants がリアルタイム更新されない」であり watch 限定ではない
- 推奨対応: 見出しを「解決済み: J-Quants リアルタイム更新不可 → TDnet 移行」に変更し、影響サブコマンド（watch, catchup）を明示。ステータスを「解決済み（2026-04-14 watch / 2026-04-28 catchup）」に更新
- MD 修正だけで足りるか: 足りる

### #3 066 に catchup の実装詳細セクションが不在

- 箇所: `docs/knowledges/tools/066_zaraba_tool.md` 全体
- 問題: watch には「設計方針: watch 時のデータ全件メモリロード」「設計方針: TDnet ポーリングソースの二層構成」等の詳細セクションがあるが、catchup には L31 の 1 行説明と L162 の使い方例しかない
- AI の誤読パターン: catchup の動作を概要 (L24) と使い方例 (L162) から推測し、「seen に記録するだけ」と理解。実際にはスコアリング + results 追加 + XBRL 並列処理も行うが、それを知る手段がない
- トリガー: catchup の修正・機能追加時
- 影響: 修正が不完全になる（スコアリング部分の考慮漏れ等）
- 根拠: 修正後 `cmd_catchup` (L939-1054) は XBRL 並列ダウンロード・`_score_record` 呼び出し・`_save_results` による results.csv 追記を含む。サブコマンド表 L31 の「DiscNo をキャッシュに記録」だけでは不十分
- 推奨対応: catchup セクションを追加し、「TDnet 取得 → XBRL 並列抽出 → スコアリング → results.csv 追記 → seen 更新」のフローを記載。watch との共通点（同じ poller / extractor / scorer）と差異（バッチ vs リアルタイム、並列 vs 逐次）を明記
- MD 修正だけで足りるか: 足りる

### #4 「将来 TODO」に完了済み項目が残存

- 箇所: `docs/knowledges/tools/066_zaraba_tool.md:384`
- 問題: 「【ブロッカー】watch を TDnet XBRL ベースに改造」は 2026-04-14 に完了済みだが TODO に残っている
- AI の誤読パターン: 「watch はまだ J-Quants ベースで、TDnet 移行は未着手」と判断
- トリガー: ザラ場ツール全般の保守・改修時に TODO を参照
- 影響: 既に完了した移行作業を再実行しようとする。または「未移行のため J-Quants が正しい」という誤認を強化する
- 根拠: L1149-1192 で watch は `create_poller` + `XbrlExtractor` を使用済み
- 推奨対応: L384 を削除するか「完了（2026-04-14）」マークに変更
- MD 修正だけで足りるか: 足りる

### #5 051 に「VM には Google Drive がない」という否定情報が不在

- 箇所: `docs/knowledges/tools/051_windows_linux_vm_guide.md:64-70`（同期の基本方針）
- 問題: 同期方法として git と GCS のみを記載しているが、「Google Drive / rsync / scp 等の自動同期手段はない」という否定情報がない
- AI の誤読パターン: CLAUDE.md L119 `C:\gdrive\...` から Google Drive の存在を認識 → 051 で否定されていない → 「Google Drive 経由で VM にも同期される」と推論
- トリガー: Windows でコード修正後に「VM にも反映」を意識する場面
- 影響: ユーザーに誤った同期方法を案内し、VM 側のコードが更新されない
- 根拠: 051 L64-70 は「コード → git」「.env, keys → GCS」のみ列挙。「それ以外の自動同期手段はない」の明示がない。今回 AI が Google Drive 同期を誤認した事実が証拠
- 推奨対応: L64-70 の同期方針テーブルの下に注記を追加: 「VM には Google Drive が入っていない。Windows の `C:\gdrive\...` パスは Google Drive のローカルジャンクションだが、VM には自動同期されない。同期は上記の git + GCS のみ」
- MD 修正だけで足りるか: 足りる

---

## 【改善提案】（中優先度）

### #1 066 サブコマンド表にデータソース列を追加

- 箇所: `docs/knowledges/tools/066_zaraba_tool.md:28-33`
- 現状: サブコマンド表に用途のみ記載。どのサブコマンドがどのデータソースを使うか不明
- 提案: テーブルに「データソース」列を追加し、各サブコマンドのデータ取得元を明示する
- 期待効果: データソース変更時に、影響するサブコマンドを一覧で把握できる。今回のような不整合の見落としを防止

### #2 066 に「サブコマンド間整合性チェック」注意事項を追加

- 箇所: `docs/knowledges/tools/066_zaraba_tool.md`（新規セクション）
- 現状: データソースやインターフェースの変更が個別サブコマンドの記述にとどまり、横展開の視点がない
- 提案: 「注意: データソース・共通ロジック変更時のチェックリスト」セクションを追加。「watch のデータソースを変更したら catchup も確認」「共通関数を変更したら全サブコマンドのテスト」等
- 期待効果: 次回のデータソース変更時に横展開漏れを防止

### #3 066 の「既知の問題」を「解決履歴」に構成変更

- 箇所: `docs/knowledges/tools/066_zaraba_tool.md:217-224`
- 現状: 「既知の問題: watch コマンドが機能しない」は解決済みだが見出しが「既知の問題」のまま。AI が「現在も問題がある」と解釈するリスク
- 提案: 「解決済みの問題」または「変更履歴: J-Quants → TDnet 移行」に見出しを変更。解決日・影響範囲・対策を記載
- 期待効果: AI が現在のステータスを正確に把握できる

### #4 066 に 051 への参照リンクを追加

- 箇所: `docs/knowledges/tools/066_zaraba_tool.md:293`（推奨 VM スペック）
- 現状: VM スペックは記載あるが、VM への同期方法の参照先がない
- 提案: 推奨 VM スペック節の末尾に「VM への同期手順: `docs/knowledges/tools/051_windows_linux_vm_guide.md` を参照」を追加
- 期待効果: 066 を読んだ AI が VM 運用を意識した際に、正しい同期手順に到達できる

---

## 【ソースコード・仕組み側への波及】

- 対象: `scripts/zaraba_earnings.py` docstring (L2-12)
- 理由: docstring が J-Quants を現行として記載しており、066 と合わせて AI の誤認を二重に強化する。MD だけ直しても docstring が残ると、AI がコードの docstring を読んで再び誤認する
- 推奨対応: docstring の L4 を「ザラバ中の決算発表を TDnet 適時開示ポーリング + XBRL 抽出で検知し、」に、L10 を「watch: ザラバ監視（TDnet ポーリング + XBRL 抽出 + スコアリング + rich Live 表示）」に更新。L9 も「catchup: 指定時刻までの決算を TDnet から取得しスコアリング + results 追記」に更新
- 検証方法: `grep -n "J-Quants" scripts/zaraba_earnings.py` で docstring 以外に J-Quants 参照が残っていないことを確認（prepare は J-Quants 経由の BQ キャッシュなので正当な参照）

---

## 【推奨検証（Step 8）】

### 正本帰属チェック（8a）

- 推奨 #1-#4 は 066 の修正。066 はザラ場ツールの知見正本であり正本帰属に問題なし
- 推奨 #5 は 051 の修正。051 は VM 同期ガイドの正本であり正本帰属に問題なし
- ソースコード波及は `zaraba_earnings.py` の docstring 修正。コードの正本はコード自身であり問題なし
- memory への書き込み推奨は含まれていない。CLAUDE.md の memory 制約と整合

### 上位ルール整合性（8b）

- CLAUDE.md「知見ファイルは最後まで読む」ルール: 066 の先頭を J-Quants から TDnet に更新することで、最後まで読まなくても正しい情報に到達できるようになる。このルール自体は依然有効だが、先頭の正確性向上は AI 可読性の改善
- CLAUDE.md「データカタログファースト」「BQ SQL 発行前に data_catalog.md 確認」: ザラ場ツールの当日ポーリングは BQ ではなく TDnet HTML であるため、data_catalog.md の範囲外。整合性問題なし
- 推奨 #5（051 への否定情報追加）は CLAUDE.md の GCS 同期原則 (L140-) と整合。Google Drive が同期手段に含まれないことの明示は CLAUDE.md の方針と矛盾しない

### 副作用シミュレーション（8c）

- **推奨 #1 実施後**: 066 の概要が「TDnet + XBRL」になった場合、prepare（BQ キャッシュ）のデータソースとの混同リスクを検討。prepare は TDnet ではなく BQ なので、概要に「事前準備は BQ、当日検知は TDnet」の区分を明記する必要がある。推奨 #1 の文案が概要をすべて TDnet にすると prepare の記述が整合しなくなるため、概要は「事前準備を BQ で行い、当日の決算発表を TDnet 適時開示ポーリング + XBRL 数値抽出でリアルタイム検知する」のような二段記述が適切
- **推奨 #2（既知の問題→解決済み変更）実施後**: 「既知の問題」見出しが消えると、J-Quants の根本的制約（リアルタイム更新されない）の記録が薄くなるリスク。解決履歴として残すことで情報は保全される。問題なし
- **推奨 #5 実施後**: 051 に「Google Drive は VM にない」を追加した場合、将来 VM に Google Drive が導入された際に 051 の否定情報が stale になるリスク。ただし現時点ではそのような計画はなく、導入時に 051 を更新するのは通常の保守範囲

### 事後確認事項（8d）

- 066 修正後: `grep -n "J-Quants" docs/knowledges/tools/066_zaraba_tool.md` で、prepare 以外の J-Quants 参照が残っていないことを確認
- 066 修正後: サブコマンド表のデータソース列が実コードと一致していることを確認
- 051 修正後: 次回 VM 同期が必要な場面で AI が git + GCS を正しく使うことを確認
- docstring 修正後: `grep -n "J-Quants" scripts/zaraba_earnings.py` で docstring が更新されていることを確認

---

## 【確認できなかった事項】

- `zaraba_tdnet_poller.py` の `create_poller` / `XbrlExtractor` の実装詳細（本レビューでは閲読対象外としたが、066 の catchup セクション記載時に実装を確認する必要がある）
- 知見ファイルテンプレート `src/knowledge/templates/tools.md` に「サブコマンド間整合性」のチェック項目があるかどうか（テンプレート自体は本レビューでは未閲読）
- `docs/plans/20260407_zaraba_watch_tdnet_xbrl.md`（watch の TDnet 移行設計）が catchup への波及を記載しているかどうか
