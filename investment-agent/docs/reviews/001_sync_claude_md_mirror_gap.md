# コードレビュー: `sync_claude_md.py` の mirror 同期不適合

- 日時: 2026-04-25 19:35 JST
- 対象: `scripts/sync_claude_md.py`
- パターン: 1
- レビュアー: Claude (code-reviewer runbook)

---

## サマリー
- 現行の `scripts/sync_claude_md.py` は manifest ベースの安全取り込みツールであり、Claude 側を正とした大量の Markdown 追加・削除・改名をそのまま Codex 側へ反映する「mirror sync」用途には不適合だった。
- そのため実作業では別の現物比較方式で同期を行い、後から `scripts/sync_markdown_mirror.py` を追加した。
- 併せて、運用側でも「既存ツールを使わず別方式へ切り替える理由」をユーザーへ事前説明しないまま進めた点は問題だった。

## 主要所見

### #1 mirror 同期の要件を満たさない
- 位置: `scripts/sync_claude_md.py:28-35`, `scripts/sync_claude_md.py:275-289`
- 症状: デフォルト対象と除外パターンが固定で、さらに baseline との差分分類を前提にしているため、Claude 側で大量の追加・削除・改名が発生したケースで「Claude 側の現物状態をそのまま同期する」用途に使えない。
- トリガー: `DEFAULT_EXCLUDES` による除外、manifest 前提、`classify()` の `SAFE_IMPORT/CONFLICT/CODEX_ONLY` モデル。
- 影響: 実ファイル比較で一括ミラーしたい場面でも、未同期ファイルが残る、削除が別挙動になる、rename が add+delete として自然に扱えない、という運用ギャップが出る。
- 改善:
  - `sync_claude_md.py` に `--mode mirror` を追加して実ファイル比較モードを持たせる。
  - あるいは mirror 用スクリプトを正式に別名で提供し、README/運用文書で用途を明確に分ける。
  - デフォルト説明文にも「これは mirror sync ではない」と明記する。

### #2 stale manifest を検知せず、現況とずれた baseline を前提に動けてしまう
- 位置: `scripts/sync_claude_md.py:91-95`, `scripts/sync_claude_md.py:283-307`
- 症状: manifest の `updated_at_jst` や `source_root` を読み込んでも、古すぎることや運用実態とずれていることを警告しない。
- トリガー: `load_manifest()` は JSON を返すだけで、`main()` 側にも freshness/source validation がない。
- 影響: 実ファイルはすでに別手段で同期済みでも、manifest だけ古いまま残り、次回 `sync_claude_md.py` を使ったときに分類結果が運用者の期待とずれる。今回も manifest は `2026-04-22` のまま残った。
- 改善:
  - `updated_at_jst` が一定日数より古ければ fail fast して `--init-baseline` を促す。
  - `source_root` 不一致時は即エラーにする。
  - `--check-manifest` を追加して freshness / source / file count の健全性だけ検査できるようにする。

### #3 dry-run / apply の出力が弱く、mirror ではないことが利用者に伝わりにくい
- 位置: `scripts/sync_claude_md.py:169-177`, `scripts/sync_claude_md.py:292-308`
- 症状: 出力がステータス列挙中心で、`add / modify / delete / conflict` の件数集計や「`--apply` でも conflict は解決しない」「`--delete` を付けない限り削除は反映されない」という要点が弱い。
- トリガー: `print_rows()` が行単位出力のみ、`apply_safe_rows()` が safe row のみ適用。
- 影響: 利用者が「これで Claude 側と一致する」と誤認しやすい。今回も事前に既存スクリプトの適否説明が必要だった。
- 改善:
  - dry-run 冒頭に件数サマリーを出す。
  - `--apply` 実行前に「これは mirror sync ではない」「未解決 conflict 件数」「削除未反映件数」を明示する。
  - `--verbose` なしでも top-N の add/modify/delete を出す。

## プロセス上の問題

### #1 既存手段を回避して代替手段へ切り替える際の事前説明不足
- 事象: 既存手段をそのまま使わず、別手段へ切り替えて作業を進めたが、その理由とリスクを実行前にユーザーへ説明しなかった。
- 問題: 既存手段が期待動作を満たさない、または満たすか不確実だと判断した時点で、「なぜ切り替えるか」「代替手段で何が変わるか」「内部状態や後続運用へ何が残るか」を先に共有すべきだった。
- 改善:
  - 既存手段を回避するときは、実行前に少なくとも次の 3 点を通知する。
  - 1. 既存手段を採用しない理由
  - 2. 代替手段が実施する操作の範囲
  - 3. 実行後に残る状態差分や後続作業への影響
  - 同種の運用判断は handoff / review にも残す。

## 提案アクション
- [ ] `scripts/sync_claude_md.py` に mirror 非対応の明示と manifest stale 検知を追加
- [ ] 既存ツールの用途を「安全取り込み」、`sync_markdown_mirror.py` の用途を「現物ミラー同期」と文書化
- [ ] 実行前説明の運用ルールを `docs/knowledges/tools/083_codex_collaboration.md` か同等の運用文書に追記
