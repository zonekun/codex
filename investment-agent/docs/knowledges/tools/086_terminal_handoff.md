# 端末間の作業移管

**カテゴリ**: tools
**作成日**: 2026-04-29
**ステータス**: 有効
**適用タイミング**: 端末間で作業を引き継ぐ時、またはセッション開始時の terminal-relay.md 確認

## 引き継ぎボード（`docs/terminal-relay.md`）

別端末から作業依頼がある場合、このファイルに `status: pending` のエントリが存在する。

- **セッション開始時に必ず確認する**。pending エントリがあればユーザーに報告する
- 送り出し側: エントリを追記して git push
- 受け取り側: git pull 後に確認 → 実行 → `status: done` に更新して git push
- 不要になったエントリは削除してよい

## 共通手順（送り出し側）

```bash
# Step 0: 受け取り側に未pushの変更がないか確認（コンフリクト防止）
# Step 1: git commit & push
git status && git diff --stat
git add -u
git add <新規ファイル>   # .claude/ data/ は除外
git commit -m "..."
git push origin master

# Step 2: 非gitファイルをGCSにプッシュ
bash scripts/sync_push.sh
```

## 受け取り側コマンド

- **Windows**: `cd /c/gdrive/claude/investment-agent && git pull origin master && bash scripts/sync_pull.sh`
- **Linux VM**: `cd ~/project/claude/investment-agent && git pull origin master && bash scripts/sync_pull.sh`

## 複数端末同時編集の注意

- **別端末にpush指示を出す前に、ローカルの未コミット変更を先にcommit & pushする**
- ローカルに未コミット変更がある状態で `git pull` すると**コンフリクトが発生する**（特に知見MDは複数エージェントが触りやすい）
- やむを得ず未コミット変更がある場合: `git stash → git pull → git stash pop`（コンフリクト時は手動マージ）

## 同期確認の手順（誤判断防止）

`git pull` が "Already up to date" を返した場合、**「相手がpushしていない」とは限らない。既に取り込み済みの可能性がある**。同期状況の確認は必ず `git log --oneline -3` でHEADのコミットハッシュを確認してから判断すること。

```bash
# NG: pull結果だけで判断
git pull origin master  # → "Already up to date" → 「まだpushされてない」は誤り

# OK: logで実際のHEADを確認してから判断
git log --oneline -3    # → HEADが期待のコミットか確認
git pull origin master  # → 差分があれば取り込み、なければ確認済み
```
