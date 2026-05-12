# コードレビュー: download_monthly.py follow_links_save_html — レビュー指摘3件修正後の最終状態

- 日時: 2026-05-08 15:21 JST
- 対象: `scripts/download_monthly.py` L884-889, L941-968, L982-984, L1112-1114（`follow_links_save_html` 機能追加 — 前回110レビュー指摘の修正後）
- パターン: 3 (ad-hoc)
- レビュアー: Claude (code-reviewer runbook)
- 前回レビュー: `docs/reviews/110_cr_follow_links_save_html.md`

---

## 【サマリー】

- 変更の要約: 前回レビュー(110)の3件の指摘（サイレント無効化ガード未設置 / stats["downloaded"]二重カウント / サマリーログ件数不正確）に対する修正が適用された最終状態。ガード追加・カウンタ分離・ログ修正の3点が実装されている。
- 品質評価: **A** — 前回指摘3件は全て適切に修正されており、既存パスへの悪影響もない。残存する問題は1件（重大度: 低〜中）と改善提案2件のみ。
- 主要リスク:
  1. `target_links` が空の early return (L1039) で `html_saved_count` が `stats` に反映されない
  2. `str(sub_soup).encode("utf-8")` による再シリアライズ品質（前回110#3の指摘から継続、実用上問題ないレベル）

---

## 前回レビュー(110)指摘の修正評価

### 110#1 `follow_links=false` + `follow_links_save_html=true` 時のサイレント無効化 → **修正済み・適切**

- 修正箇所: L886-888
- 修正内容: `follow_links_save_html and not follow_links` の場合に `logger.warning` で矛盾を通知し、`follow_links_save_html = False` に強制リセット
- 評価: 前回推奨案 (A) の通り。ガードロジックは正しい。`follow_links=true` + `follow_links_save_html=true` の正常パスには影響しない（`not follow_links` が False になるため分岐に入らない）。`follow_links=true` + `follow_links_save_html=false`（デフォルト）の正常パスも影響なし。

### 110#2 `stats["downloaded"]` の二重カウント → **修正済み・適切**

- 修正箇所: L889 (`html_saved_count = 0`), L953/L966 (`html_saved_count += 1`), L1112-1113 (`stats["html_saved"]`)
- 修正内容: `html_saved_count` を独立変数として管理し、`stats["downloaded"]` には一切加算しない。関数末尾（L1112-1113）で `html_saved_count > 0` の場合のみ `stats["html_saved"]` をセット。
- 評価: 前回推奨案 (A) の通り。分離は完全。`stats["downloaded"]` には HTML 保存の行は一切触れておらず、PDF/ファイルダウンロードのみをカウントする既存挙動を維持。呼び出し元（L1775-1784 の `results.append`）は `stats["downloaded"]` / `stats["skipped"]` / `stats["failed"]` の3キーのみを参照しており、`stats["html_saved"]` を参照するコードは現時点でプロジェクト内に存在しない。これは問題ではない — `stats` は dict であり、未参照キーがあっても害がなく、将来の集計拡張で使える。

### 110#3 サマリーログの件数計算が不正確 → **修正済み・適切**

- 修正箇所: L982-984
- 修正内容: `len(seen_sub) if follow_links_save_html else 0` を `html_saved_count` に変更。f-string で ` + HTML保存 {html_saved_count} 件` を追加表示。
- 評価: 前回推奨案の通り。`html_saved_count` は実際に保存成功した件数（dry-run含む）のみをカウントしており、既存ハッシュでスキップされた場合は加算されない。ログの意味が正確になった。f-string 構築も安全（`html_saved_count` は int、`msg` は str 結合のみ）。

---

## 【重大な指摘】（即修正）

### #1 early return パス (L1039) で `html_saved_count` が `stats` に反映されない

- 箇所: `scripts/download_monthly.py:1039` vs `scripts/download_monthly.py:1112-1113`
- 事象: `follow_links=true` + `follow_links_save_html=true` の場合、サブページループ (L916-980) で HTML が保存された後、`target_links` の構築に進む。ここで `target_links` が空（サブページに PDF リンクが一切ない場合）だと L1039 で `return stats` されるが、`stats["html_saved"] = html_saved_count` のセット (L1112-1113) はこの `return` より後にある。結果、HTML は保存されたにもかかわらず `stats` に `html_saved` キーが含まれない `return` が行われる。
- トリガー: サブページが HTML のみ（PDF/Excel 等のダウンロード可能ファイルへのリンクを含まない IR ページ）で、`follow_links_save_html=true` が設定されている場合。例えば、テキスト形式で業績情報を掲載しているページ（PDFなしの月次売上報告など）。
- 影響: `stats` の `html_saved` キーが欠落するため、呼び出し元が将来 `html_saved` を参照するロジックを追加した場合に KeyError またはカウント漏れが発生する。現時点では呼び出し元が `html_saved` を参照していないため実害は無いが、`file_log` には正しく記録されている（L954-955, L967-968）ため、ログベースの集計との不整合が生じる。またサマリーログ (L982-984) ではカウントが正しく表示されるため、ログ上は問題が見えない。
- 根拠: L1112-1113 の `if html_saved_count: stats["html_saved"] = html_saved_count` は L1114 `return stats` の直前にあるが、L1039 の `return stats` はこれより上にある。
- 推奨対応: L1039 の `return stats` の直前に、L1112-1113 と同じガードを追加する。

```python
# L1039 の return stats の直前に追加:
if html_saved_count:
    stats["html_saved"] = html_saved_count
return stats
```

あるいは、L1112-1113 を関数内の全 `return stats` の前に統一的に適用するヘルパーを設けるか、`stats["html_saved"]` を L889 の初期化時点で `stats` に含める（`stats = {"downloaded": 0, "skipped": 0, "failed": 0, "html_saved": 0}` として、L1112 の条件分岐を除去）。後者は呼び出し元の `results.append` で未使用キーが常に含まれる点がトレードオフだが、early return での漏れが構造的に防げる。

**重大度判定**: 低〜中。現時点で実害はないが、将来の拡張時に確実にバグ化するパターン。

---

## 【改善提案】（可読性・保守性）

### #1 `_ensure_dir(company_dir)` の呼び出しタイミング

- 箇所: `scripts/download_monthly.py:963` と `scripts/download_monthly.py:1042-1043`
- 現状: HTML 保存 (L963) でローカルモード時に `_ensure_dir(company_dir)` を呼び出している。一方、後段の PDF ダウンロードループの前 (L1042-1043) でも `if not (IS_CLOUD_RUN and bucket): _ensure_dir(company_dir)` が呼ばれる。`_ensure_dir` は `mkdir(parents=True, exist_ok=True)` であるため、2回呼んでも問題はない。
- 提案: 動作に問題はないが、HTML が先に来て company_dir が作られた後に PDF ループの `_ensure_dir` が冗長に呼ばれる構造は、読者を一瞬混乱させる。コメントで「HTML 保存で作成済みの場合あり」等を付けておくと可読性が上がる。ただし、`target_links` が空で early return した場合にも company_dir が作られる（HTML 保存のために L963 で作成される）点は、副作用として認識しておくべき。

### #2 `str(sub_soup).encode("utf-8")` の再シリアライズ（前回110改善提案#3から継続）

- 箇所: `scripts/download_monthly.py:948`
- 現状: 前回レビューで指摘された通り、BeautifulSoup の再シリアライズは元の HTML と完全には一致しない。Playwright パス (`sub_html: str`) と requests パス (`sub_cf.content` / `sub_resp.content: bytes`) で元データの型が異なるため、統一的に元データを保存するには分岐が必要。
- 提案: 実用上は問題ないレベルだが、もし将来 HTML の元構造が重要になる場合（差分検知など）は、`sub_raw_content` 変数に元の bytes/str を分岐で保持する方式に変更する価値がある。現時点では優先度低。

---

## 【確認できなかった事項】

- **`_save_run_log` (L1807) が `stats["html_saved"]` を GCS ログに含めるか**: `_save_run_log` の実装を確認していないため、ログ出力に `html_saved` が反映されるかは未確認。`results` dict に `html_saved` キーが含まれていないため（L1775-1784 で `downloaded` / `skipped` / `failed` / `error` / `files` のみ）、GCS ログには `html_saved` は記録されない。`file_log`（`files` キー経由）には個別エントリとして記録されているため、集計は可能だが、サマリーレベルでの `html_saved` は GCS ログに欠落する。
- **extract パイプラインが `.html` ファイルを処理できるか**: 前回110の確認できなかった事項と同一。extract 側の挙動は未確認。
