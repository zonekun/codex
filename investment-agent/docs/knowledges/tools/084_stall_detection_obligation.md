# stall 検知義務（ジョブ監視）

**カテゴリ**: tools
**作成日**: 2026-04-29
**ステータス**: 有効
**適用タイミング**: ユーザーから「監視して」指示を受け、ジョブの監視プロセスを立ち上げる際

## 背景事故

2026-04-19 の TDnet batch#5-#11 監視で、chain スクリプトと monitor_backfill.py は「WF SUCCEEDED/FAILED 終局状態」しか検知せず、Workflows が **ACTIVE のまま 15時間 stuck** してもイベントが発火せず見落とした（TPU preempt で worker が callback 投げずに終了、WF 永久待機）。

## 恒久ルール

1. **stall 判定は「進捗メトリクスの不変時間」で行う（想定所要に依存しない）**
   - 進捗メトリクス = **処理済レコード件数 / doc 数 / BQ row count / 出力ログ行数 / step_id / state** のうちジョブに合う複数値
   - **いずれか 1 つでも動いていれば進捗中** とみなす（state=ACTIVE のままでも doc count が増えていれば OK）
   - **全メトリクスが N 分不変 → stall**（非零 exit で終了、通知手段はデフォルト手順に従う）
   - N は**絶対値でジョブ種別ごとに決める**（例: Workflow step 15分、Gemma doc count 30分、BQ insert 10分）。想定所要の倍率で決めない
2. **時間ベースは slow warning のみ（止めない）**
   - 実績 > 想定 × 1.5 → warning を記録するだけ。機械的に abort しない
   - 予実は外れることがあるので、進捗が動いているなら何倍かかっても許容
3. **サブ完了の整合性チェックを入れる**
   - 例: Gemma 完了したのに ai-finalize が 15 分以内に起動しない → stall
   - 例: step X の所要が想定の 1/4 以下 → データ不整合の疑いで doc 数等を検証
4. **`ScheduleWakeup` で自分を定期起こし**（1-2 時間おき）— 背景プロセスが異常を emit しない場合の最終防衛線
5. **完了時刻の見積もりを過ぎたら必ず中間状態を確認**
   - 「まだ処理中だろう」と都合よく解釈しない

## 失敗した監視ロジックの例（避けるべき）

```bash
while :; do
  STATE=$(... describe --format=value(state))
  case "$STATE" in
    SUCCEEDED) exit 0 ;;   # 終局のみ検知
    FAILED|CANCELLED) exit 1 ;;
    *) sleep 300 ;;        # state の不変も進捗メトリクスも見ない ← 失敗
  esac
done
```
