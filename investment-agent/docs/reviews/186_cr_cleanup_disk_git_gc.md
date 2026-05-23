# コードレビュー: cleanup_disk.py Git GC 機能追加

- 日時: 2026-05-16 18:17 JST
- 対象: `scripts/cleanup_disk.py` (新規関数: `find_git_repos`, `check_gitignore`, `run_git_gc`), `docs/knowledges/tools/077_cleanup_disk.md`
- パターン: 1 (新規機能レビュー)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: cleanup_disk.py にGit GCサブコマンドを追加。指定ディレクトリ直下のGitリポジトリを列挙し、`git gc --aggressive --prune=now` を実行。`.gitignore` 推奨パターンのチェック機能付き。知見MD (077) にも対応する仕様セクションを追加。
- 品質評価: **B** -- 基本機能は動作するが、エッジケース処理の不足と既存スクリプト規約違反が複数ある。
- 主要リスク:
  1. `git gc` 実行中に `.git/index.lock` が存在する場合のハンドリングなし（GC失敗 or データ破損リスク）
  2. `print()` 直接使用（CLAUDE.md §7 の structlog 規約違反）
  3. `--git-gc` と `--execute` の関係が曖昧（docstring では「`--execute` 不要」だが、コード上は `--execute` が必要）

## 【重大な指摘】（即修正）

### #1 `--git-gc` と `--execute` フラグの矛盾（docstring vs 実装）

- 箇所: `scripts/cleanup_disk.py:23` (docstring) / `scripts/cleanup_disk.py:487` (main内)
- 事象: docstring の L23 では `--git-gc   # Git GC実行（--execute不要）` と記述しているが、実装 L487 では `run_git_gc(repos, execute=args.execute)` であり、`--execute` なしでは dry-run になる。ユーザーが docstring を信じて `--git-gc` のみで実行すると、GC は実行されず dry-run になる。
- トリガー: `python scripts/cleanup_disk.py --git-gc` を docstring を読んで実行した場合
- 影響: 期待と異なる動作（GC されない）。データ損失はないが、誤解を招く。
- 根拠: L23 `# Git GC実行（--execute不要）` vs L487 `run_git_gc(repos, execute=args.execute)`
- 推奨対応: **[検証済み]** docstring L23 を `# Git GC実行（dry-run → --execute で実行）` に修正。077 知見MD の「使い方」セクションは既に正しい記述（dry-run と --execute の使い分けが書かれている）。

### #2 ロック中リポジトリでの `git gc` 失敗時の不完全なエラーハンドリング

- 箇所: `scripts/cleanup_disk.py:354-360`
- 事象: `git gc` は `.git/index.lock` や `.git/gc.pid` が存在する場合に失敗する。現在のコードは `result.returncode != 0` でエラー出力を表示して `continue` するが、`git gc --aggressive` は途中で中断した場合にパックファイルの中間状態を残すことがある。特に `--aggressive` は全オブジェクトを再パックするため、中断後に `.git/objects/pack/` に不整合が残る可能性がある。
- トリガー: 別プロセスが同じリポジトリで git 操作中（例: Claude Code の別セッション、VSCode の git 拡張）
- 影響: `.git/` の不整合により後続の git 操作が失敗する可能性
- 根拠: `git gc --aggressive` は全オブジェクトの再パックを行い、途中中断時の recovery は git の内部メカニズムに依存する。returncode != 0 のケースで `total_after += before` としているが、実際の `.git/` サイズは中間状態で before と異なる可能性がある。
- 推奨対応: **[方向性]** GC 実行前に `.git/index.lock` と `.git/gc.pid` の存在をチェックし、存在する場合はスキップ + 警告を出す。中断後のサイズ測定は実測値（`dir_size(git_dir)` を再呼び出し）を使うべき。

### #3 シンボリックリンク / ジャンクション先の `.git` 誤検出

- 箇所: `scripts/cleanup_disk.py:299-310` (`find_git_repos`)
- 事象: `target.iterdir()` はシンボリックリンク / ジャンクションも列挙する。`entry.is_dir()` はシンボリックリンク先がディレクトリなら True を返す。結果として、同一の物理リポジトリがシンボリックリンク経由で複数回列挙される可能性がある。`sorted(set(repos))` で Path の重複排除はしているが、シンボリックリンクと実体パスは異なる Path オブジェクトなので set で排除されない。
- トリガー: `DEFAULT_GIT_TARGETS` に `C:\gdrive\claude` が含まれており、これはジャンクション。ジャンクション先と実パスが別パスとして扱われる場合、同一リポジトリに対して `git gc --aggressive` が二重実行される。
- 影響: 同一リポジトリへの二重 GC 実行。1回目の GC 完了後に2回目が走るだけなので致命的ではないが、不要な処理時間の浪費。最悪ケースでは、1回目の GC 中に2回目が開始されてロック競合が起きる（ただし `sorted` で逐次実行なので現実的には起きにくい）。
- 根拠: L47 `Path(r"C:\gdrive\claude")` はジャンクション。`resolve()` を呼んでいないため、シンボリックリンク先の実パスと解決前パスが混在する。
- 推奨対応: **[検証済み]** `find_git_repos` 内で `repo.resolve()` を使って正規化してから `set` に入れる。`return sorted({r.resolve() for r in repos})` に変更。

### #4 `git` コマンドが PATH に存在しない場合の早期 `return` によるサイレントスキップ

- 箇所: `scripts/cleanup_disk.py:369-370`
- 事象: `FileNotFoundError` を catch して `print` + `return` しているが、この `return` は `run_git_gc` 関数からの `return None` であり、`main()` の戻り値 `0` には影響しない。つまり git が未インストール環境で `--git-gc --execute` を実行すると、エラーメッセージは出るが exit code 0 で成功扱いになる。
- トリガー: git が PATH に存在しない環境（Docker コンテナ等）で実行
- 影響: 自動化スクリプトから呼ばれた場合に失敗を検知できない
- 根拠: L369-370 の `return` が `main()` の `return 0` に対して何も伝搬しない
- 推奨対応: **[方向性]** `run_git_gc` に戻り値（成功/失敗）を追加するか、例外を上位に伝搬させる。ただし cleanup_disk.py の他のセクション（ファイル削除等）も同様に失敗を exit code に反映していないため、ツール全体の exit code 設計として検討が必要。

### #5 `.gitignore` チェックの誤検出パターン

- 箇所: `scripts/cleanup_disk.py:322-328` (`check_gitignore`)
- 事象: `.gitignore` のパターンマッチングが単純な文字列一致で行われている。例えば `node_modules` が `.gitignore` に `**/node_modules` や `node_modules/**` の形式で記述されている場合、現在の3パターン比較（`bare`, `/bare`, `bare/`）では検出できず、false positive の警告が出る。
- トリガー: `.gitignore` に `**/node_modules` や `!node_modules/important/` 等の glob パターンが使われている場合
- 影響: 不要な警告が表示される（実害は警告のみ）
- 根拠: L323 の `lines = {line.strip().rstrip("/") ...}` と L326-327 の比較は、gitignore の glob 構文（`**`, `!`, `[...]` 等）を解釈しない
- 推奨対応: **[方向性]** 現在の実装はヒューリスティックとして許容範囲。完全な gitignore パーサーは過剰。ただし、チェック結果が「推奨」であり「必須」でない旨を出力メッセージに含めるとよい（現在は `[warn]` 表記）。

## 【改善提案】（可読性・保守性）

### #1 `print()` 使用 -- CLAUDE.md §7 structlog 規約違反

- 箇所: `scripts/cleanup_disk.py` 全体（L14, L337, L338, L349, L355, L358, L363, L366, L370, L380-385, L401, L422, L445-454, L457-459, L462, L466, L469, L471, L475, L476, L478, L480, L487 付近）
- 現状: スクリプト全体で `print()` を直接使用。CLAUDE.md §7 は「print禁止。structlogを使用」と規定。ただし、本スクリプトは既存の全関数が `print()` を使用しており、Git GC 部分だけの問題ではない。
- 提案: 既存コードとの一貫性を考えると、Git GC 部分のみ structlog に変えるのは逆に不整合。スクリプト全体で structlog 移行するか、cleanup_disk.py をCLI出力ツールとして print 使用の例外扱いにするか、方針をユーザーと決定すべき。

### #2 `find_git_repos` の探索深度が1階層に限定されている点のドキュメント不足

- 箇所: `scripts/cleanup_disk.py:299-310`
- 現状: docstring に「対象ディレクトリ直下（1階層）」と書かれているが、077 知見MD では「対象ディレクトリ配下の `.git/`」とだけ記述されており、1階層制限が明示されていない。`C:\gdrive\claude` 配下に `subdir/project/.git` のような2階層目のリポジトリがある場合、GC 対象にならない。
- 提案: 077 知見MD の「Git GC の仕様」セクションに「直下1階層のみ探索。2階層以上の入れ子リポジトリには --git-targets で個別指定が必要」と追記。

### #3 `run_git_gc` の `--aggressive` オプションの説明不足

- 箇所: `scripts/cleanup_disk.py:355` / `docs/knowledges/tools/077_cleanup_disk.md:83`
- 現状: `--aggressive` は通常の `git gc` よりも大幅に時間がかかる（大規模リポジトリで数十分）。077 知見MD に「タイムアウト: リポジトリあたり 5 分」とあるが、`--aggressive` は大きなリポジトリで5分を超える可能性がある。タイムアウト値のトレードオフが明示されていない。
- 提案: 077 知見MD に「`--aggressive` は全オブジェクトを再パックするため大規模リポジトリでは5分を超える場合がある。タイムアウト時は通常 gc（`--aggressive` なし）を個別実行する」旨を注記。

### #4 `DEFAULT_GIT_TARGETS` のホームディレクトリ配下の `.claude` が GC 対象として妥当か

- 箇所: `scripts/cleanup_disk.py:47-50`
- 現状: `~/.claude` が GC 対象に含まれている。`.claude/` 配下に git リポジトリが存在するケース（例: plugins のローカル clone）はあり得るが、Claude Code の内部管理ファイル配下で `git gc --aggressive` を実行する必要性は低い。
- 提案: `~/.claude` を GC 対象に含める理由を 077 知見MD に明記するか、不要なら `DEFAULT_GIT_TARGETS` から除外を検討。

### #5 知見MD (077) の「全部入り」コマンド例で `--skip-git` の挙動が不明確

- 箇所: `docs/knowledges/tools/077_cleanup_disk.md:38`
- 現状: L38 に「全部入り（ファイル削除 + Git GC + DB VACUUM）」と書かれたコマンド例がある。一方 `--skip-git` フラグは `--git-gc` 指定時にのみ意味があるが、`--git-gc` なしで `--skip-git` を渡した場合にエラーにならない（単に無視される）。この組み合わせの挙動がドキュメントに明示されていない。
- 提案: `--skip-git` は `--git-gc` と併用時のみ有効である旨を使い方セクションに注記。

## 【確認できなかった事項】

- `C:\gdrive\claude` ジャンクションの実体パスが `G:\マイドライブ\claude` であることは CLAUDE.md の記述から推定しているが、ジャンクション解決後の `resolve()` がこの環境で正しく動作するかは実行して確認が必要
- `git gc --aggressive --prune=now` が Windows 環境の Google Drive 同期フォルダ上で問題なく動作するか（ファイルロック競合の可能性）は実機テストが必要
- `subprocess.run` の `text=True` が Windows 環境で非 ASCII パスを含むリポジトリの stderr を正しくデコードするかは実行確認が必要（`encoding` パラメータが未指定のため、システムデフォルトエンコーディング依存）
