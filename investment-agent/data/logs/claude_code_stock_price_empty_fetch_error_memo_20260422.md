# Claude Code 伝言メモ: stock-price-load 空取得時の異常終了化

作成日: 2026-04-22 JST

## 背景

`stock-price-load` の 2026-04-20 実行 `stock-price-load-2p7nd` は Cloud Run Job としては成功扱いだったが、実処理では yfinance から株価データを取得できず、GCS / BigQuery / Dropbox への保存が全てスキップされていた。

確認ログ:

- 実行: `stock-price-load-2p7nd`
- JST: 2026-04-20 16:00:18 開始、16:06:01 完了
- Cloud Run: `Completed=True`, `succeededCount=1`, `Container called exit(0).`
- アプリログ: `データが取得できませんでした。`
- 直前に yfinance の `possibly delisted; no price data found (1d 2026-04-20 -> 2026-04-21)` が大量発生

原因は Cloud Run / GCS / BigQuery ではなく、`scripts/stock_price_load.py` が `target_data.empty` のケースを正常終了として扱っていたこと。

## 実施した改修

対象: `scripts/stock_price_load.py`

`main()` で `fetch_stock_data()` 後に `target_data.empty` を検知したら `RuntimeError` を raise するように変更。

これにより、空取得時は既存の `except Exception` に入り、以下が発生する。

- `[STOCK_PRICE] エラー` メール送信
- ログ本文に traceback と取得対象日・銘柄数を含める
- 例外を再 raise するため Cloud Run Job も失敗扱いになる
- GCS / BigQuery / Dropbox 保存は実行されない

## 期待挙動

今後 yfinance 側の障害や市場データ未取得などで全銘柄の取得結果が空になった場合、ジョブは成功扱いにならず、エラーメールで検知できる。

休場日は `resolve_target_date()` の休日スキップ分岐で従来通り正常スキップされる想定。今回の変更は取得対象日が決まった後、実データが空だった場合のみ異常化する。

## 確認済み

```text
python -m py_compile scripts\stock_price_load.py
```

構文チェックは成功。

## 注意

作業ツリーに未追跡の `scripts/gcloud.cmd` があるが、今回の改修とは無関係のためコミット対象外。
