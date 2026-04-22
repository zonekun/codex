---
source_type: tweet
url: https://x.com/aiba_algorithm/status/2032981545011859559?s=20
author: @aiba_algorithm
posted_at: 2026-03-23（推定）
retrieved_at: 2026-03-23
cited_in: docs/knowledges/tools/045_backtest_evaluation_metrics.md
---

# 原文

バックテストの9割はオーバーフィットで死んでる説

原因の1つ：指標自体に予測力があるか先に確認してないから。

やった方がいいこと：
・将来リターン × 指標の散布図を描く
・回帰係数・相関係数を確認
・有意でない指標は即切り

この前処理を挟むだけで「たまたまフィットしただけ」の戦略をかなり弾ける。
バックテストって最適化の自由度が高い分、オーバーフィットしやすいんよな。散布図で先に機械的に確認はした方がいい。
