# コードレビュー: download_monthly.py follow_links_save_html フラグ追加

- 日時: 2026-05-08 14:55 JST
- 対象: `scripts/download_monthly.py` 未コミット差分（`follow_links_save_html` 機能追加）
- パターン: 3 (ad-hoc)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: `follow_links` サブページ巡回ループ内で、サブページの HTML 本体を GCS/ローカルに保存するオプション `follow_links_save_html` を追加。既存の PDF 等ファイルリンク収集ロジックと並行して動作する。
- 品質評価: **B** — 既存パスへの悪影響は限定的で、保存ロジックのパターンも既存コードの踏襲で一貫性がある。ただし、edge case でデータ不整合・二重カウント・未定義動作が生じる経路がある。
- 主要リスク:
  1. `follow_links=false` + `follow_links_save_html=true` の矛盾設定時にサイレント無効化（意図不明確）
  2. `stats["downloaded"]` が HTML と PDF 両方で加算され、サマリーログの件数の解釈が曖昧になる
  3. ログメッセージの件数計算 `len(seen_sub)` が「HTML保存成功数」ではなく「巡回サブページ数」であり、実際のHTML保存数と乖離する

---

## 【重大な指摘】（即修正）

### #1 `follow_links=false` + `follow_links_save_html=true` 時のサイレント無効化

- 箇所: `scripts/download_monthly.py:885,897`
- 事象: `follow_links_save_html=true` でも `follow_links=false` であれば L897 の `if follow_links:` ブロック自体に入らないため、HTML保存は一切発動しない。adapter にこの矛盾設定が入った場合、エラーもワーニングも出ずにサイレントに何もしない。
- トリガー: adapter JSON で `"follow_links": false, "follow_links_save_html": true` と設定された場合（手動設定ミスやテンプレートのコピペ誤り）。
- 影響: ユーザーがHTML保存を意図して設定したのに無視される。dry-run でも件数 0 で気付きにくい。
- 根拠: `follow_links_save_html` の読み取り（L885）は `if follow_links:` ブロック（L897）の外だが、使用箇所（L937）は `if follow_links:` ブロックの内部にある。
- 推奨対応: 以下のいずれか:
  - (A) `follow_links_save_html=true` かつ `follow_links=false` の場合に `logger.warning` で矛盾を通知する（ガード追加）
  - (B) `follow_links_save_html=true` なら `follow_links` を暗黙的に `true` として扱う（しかし副作用が大きいため (A) が安全）

### #2 `stats["downloaded"]` の二重カウント（HTML + PDF）

- 箇所: `scripts/download_monthly.py:949,962` (HTML保存) と `scripts/download_monthly.py:1064,1090` (PDFダウンロード)
- 事象: `follow_links_save_html=true` の場合、同一サブページについて HTML保存（L949/L962）で `stats["downloaded"] += 1` が加算され、さらにそのサブページ内から抽出された PDF リンクがダウンロード成功した場合にも `stats["downloaded"] += 1`（L1064/L1090）が加算される。サマリーログの `downloaded=N` が「何件のファイルをDLしたか」の意味が曖昧になる。
- トリガー: `follow_links_save_html=true` で、サブページに PDF リンクもある通常の IR ページ。
- 影響: ログの `downloaded` 数が膨らみ、過去実績との比較・異常検知の閾値がずれる。BQ の `file_log` 集計でも HTML と PDF が混在する。ただしデータ損失は起きない。
- 根拠: HTML保存の `stats["downloaded"] += 1`（L949,L962）はサブページループ内、PDF保存の `stats["downloaded"] += 1`（L1064,L1090）は `target_links` ループ内で、両者は独立して加算される。
- 推奨対応: (A) `stats["html_saved"]` を新設して HTML 保存件数を分離する、または (B) `file_log` のエントリには既に `file` フィールドに `.html` 拡張子が入るため、集計側で区別できると割り切り、ログメッセージにのみ `(HTML: M, ファイル: N)` の内訳を出す。

### #3 サマリーログの件数計算が不正確

- 箇所: `scripts/download_monthly.py:978`
- 事象: `len(seen_sub) if follow_links_save_html else 0` は「巡回したサブページの総数」であり、「実際に HTML 保存に成功した数」ではない。既存ハッシュでスキップされた場合や dry-run のカウントも含む一方、取得失敗（except ブロックで continue）した場合も `seen_sub` には含まれている。
- トリガー: 一部のサブページが既に GCS に存在する状態で再実行した場合。
- 影響: ログに表示される件数と実際の保存件数が乖離し、運用時の判断を誤る可能性がある。
- 根拠: L911-915 で `seen_sub.add(sub_url)` は無条件に追加されるが、HTML 保存はスキップ・dry-run・例外の各分岐で実際に書き込まれないケースがある。
- 推奨対応: `html_saved_count` 変数を追加し、実際に保存成功した件数のみカウントしてログに使う。

---

## 【改善提案】（可読性・保守性）

### #1 HTML 保存ロジックの関数抽出

- 箇所: `scripts/download_monthly.py:937-964`
- 現状: HTML 保存の 28 行ブロックが `follow_links` サブページループの内部にインラインで展開されており、既に長大な `handle_scrape_links` 関数（約 300 行）がさらに膨らんでいる。スキップ判定→dry-run→GCS/ローカル保存の分岐パターンは PDF ダウンロード部分（L1062-1102）とほぼ同一構造。
- 提案: `_save_sub_html(sub_soup, sub_url, ticker, company_dir, ...)` のようなヘルパーに抽出すると、テスト容易性と可読性が向上する。PDF 保存ロジックとの共通パターンも将来的に統合しやすくなる。

### #2 `_extract_yyyymm` に `sub_url` だけを渡しているケースの考慮

- 箇所: `scripts/download_monthly.py:941`
- 現状: `sub_yyyymm = _extract_yyyymm(sub_title_text, sub_url)` で、`sub_title_text` が `"subpage"` フォールバック値のとき、年月抽出は URL のみに依存する。サブページの `<title>` が空で、かつ URL にも年月情報がない場合、`000000` が返りファイル名が `000000_TICKER_subpage_HASH.html` になる。
- 提案: これ自体はバグではないが（既存の PDF パスも同様に `000000` になりうる）、HTML の場合はサブページの `<h1>` やリンク元の `text` からも年月を試行するとより正確なファイル名になる。

### #3 `str(sub_soup).encode("utf-8")` でのHTML再構成

- 箇所: `scripts/download_monthly.py:944`
- 現状: `str(sub_soup)` は BeautifulSoup が内部で再構成した HTML であり、元の生 HTML と完全には一致しない（属性順序の変更、閉じタグの補完、エンティティのデコード等）。後段の `extract` 処理が BeautifulSoup を再パースする前提なら問題ないが、元の HTML を保存する目的であれば `resp.content` / `sub_cf.content` / `sub_resp.content` を直接保存する方が忠実。
- 提案: Playwright パス（`sub_html` が `str`）と requests パス（`sub_resp.content` が `bytes`）で元データの型が異なるため、統一するなら分岐が必要。現状でも実用上は問題ないが、完全性を求めるなら検討の余地あり。

---

## 【確認できなかった事項】

- **実際にこのフラグを使う adapter が既に存在するか**: adapter JSON 内に `follow_links_save_html: true` を持つエントリがあるかどうかは未確認。存在しなければ現時点では dead code。
- **後段の extract パイプライン（`build_monthly_extractor.py` 等）が `.html` ファイルを処理できるか**: `scrape_links` タイプで保存された `.html` ファイルが extract パイプラインに流れた場合の挙動は、extract 側のコードを読まないと判定できない。`html_table` タイプ用の `.html` ファイルとは保存経緯が異なるため、extract 側で区別が必要になる可能性がある。
- **GCS パスの衝突リスク**: `follow_links_save_html` で保存される HTML と、`html_table` タイプで保存される HTML が同一 ticker ディレクトリに共存した場合のファイル名衝突可能性。ハッシュが異なれば衝突しないが、同一 URL を両方式で保存するケースがあるかは adapter 設計依存。
