# 端末間引き継ぎボード

別端末のClaude Codeに作業を引き継ぐためのファイル。
git push/pull で同期される。全操作 `printf >>` 追記のみ（Read/Edit 不要）。

## Format Rules

### New task entry

```
## TASK: <short-id> <timestamp>
- from: <Windows | Linux VM>
- to: <Windows | Linux VM>

<task body>
```

### Mark done

Append one line (do NOT edit the original task block):

```
DONE: <short-id> <timestamp> [optional one-line summary]
```

### Result report

```
## RESULT: <short-id> <timestamp>

<result body>
```

## Conventions

- short-id: kebab-case, unique enough
- timestamp: YYYY-MM-DD HH:MM JST
- 過去エントリの編集・削除禁止。追記のみ
- アクティブ判定: `## TASK:` 行があり対応する `DONE:` 行がないもの
- `from` / `to` は Claude Code 端末（Windows / Linux VM）に限定。Codex 宛は `docs/knowledges/tools/083_codex_collaboration.md` 参照
- 追記後は即 commit & push

---
