# Claude Code 緊急復旧

## `.claude.json` 破損時の復旧

### 自動バックアップ場所

`C:\Users\zonekun\.claude\backups\.claude.json.backup.<epoch_ms>`

- Claude Code が定期的に自動バックアップを取っている
- 破損時は `.claude.json.corrupted.<epoch_ms>` としてリネーム保管される
- 数世代分残る（古いほうから消えていく）
- **GCS 同期対象外**: `scripts/sync_push.sh` は `.claude.json` を同期していない。復旧はローカルバックアップのみ

### 復旧手順

1. **最新の正常バックアップを特定**:
   ```bash
   ls -la /c/Users/zonekun/.claude/backups/
   ```
   サイズが 0 や 50 bytes のものは破損済み。**30KB 前後**あるファイルが正常。

2. **安全な場所に保険コピー**（Claude Code が書き戻す前に）:
   ```bash
   cp /c/Users/zonekun/.claude/backups/.claude.json.backup.<epoch_ms> /c/tmp/claude_json_safe.json
   ```

3. **現ファイルに上書き復元**:
   ```bash
   cp /c/tmp/claude_json_safe.json /c/Users/zonekun/.claude.json
   ```

4. **Claude Code を終了**（メモリ内容で書き戻されるのを防ぐ）:
   - `/exit` コマンドか `Ctrl+C` 2回
   - ターミナル × ボタンは NG（書き戻し処理が走る）

5. **終了後、再度復元実行**（書き戻されていた場合）:
   ```bash
   cp /c/tmp/claude_json_safe.json /c/Users/zonekun/.claude.json
   ```

6. **Claude Code を起動し直す**

### 読み取り専用化で強制保護

```bash
chmod 444 /c/Users/zonekun/.claude.json   # 書き込み不可
# 再起動後
chmod 644 /c/Users/zonekun/.claude.json   # 通常に戻す
```

### 事例

- 2026-04-20 17:18 に破損（0 bytes化）。17:06 の 30,451 bytes バックアップから復旧成功
