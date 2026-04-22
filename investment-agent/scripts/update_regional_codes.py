"""地方証券取引所（名証・福証・札証）の単独上場銘柄を STOCK_CODE_LIST に登録するスクリプト。

使い方:
    uv run python scripts/update_regional_codes.py               # 全取引所
    uv run python scripts/update_regional_codes.py --exchange FSE  # 福証のみ
    uv run python scripts/update_regional_codes.py --exchange SSE  # 札証のみ
    uv run python scripts/update_regional_codes.py --exchange NSE  # 名証のみ
    uv run python scripts/update_regional_codes.py --dry-run       # BQ 書き込みなし

名証（NSE）を実行する場合は事前に以下が必要:
    uv add playwright
    uv run playwright install chromium
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加（scripts/ から直接実行する場合に必要）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.cloud import bigquery  # noqa: E402
from google.oauth2 import service_account  # noqa: E402

from src.collector.regional_exchange import (  # noqa: E402
    build_df,
    get_tse_tickers,
    load_to_bq,
    scrape_fse,
    scrape_nse,
    scrape_sse,
)
from src.core.config import settings  # noqa: E402
from src.core.logger import get_logger  # noqa: E402

log = get_logger(__name__)

# 取引所コード → (表示名, スクレイパー関数)
EXCHANGE_SCRAPERS = {
    "FSE": ("福岡証券取引所（福証）", scrape_fse),
    "SSE": ("札幌証券取引所（札証）", scrape_sse),
    "NSE": ("名古屋証券取引所（名証）", scrape_nse),
}


def _get_bq_client() -> bigquery.Client:
    """サービスアカウントキーを使って BigQuery クライアントを生成する。"""
    creds = service_account.Credentials.from_service_account_file(
        settings.google_application_credentials
    )
    return bigquery.Client(project="gmailpj-357912", credentials=creds)


def main() -> None:
    """エントリポイント。"""
    parser = argparse.ArgumentParser(
        description="地方証券取引所（名証・福証・札証）の単独上場銘柄を BQ に登録する"
    )
    parser.add_argument(
        "--exchange",
        choices=list(EXCHANGE_SCRAPERS.keys()),
        help="対象取引所コード（省略時は全取引所）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="BQ への書き込みを行わず、結果を標準出力に表示する",
    )
    args = parser.parse_args()

    targets = (
        {args.exchange: EXCHANGE_SCRAPERS[args.exchange]}
        if args.exchange
        else EXCHANGE_SCRAPERS
    )

    bq_client = _get_bq_client()

    log.info("fetch_tse_tickers_start")
    tse_tickers = get_tse_tickers(bq_client)
    log.info("fetch_tse_tickers_done", count=len(tse_tickers))

    for exchange, (label, scraper_fn) in targets.items():
        log.info("exchange_start", exchange=exchange, label=label)
        try:
            records = scraper_fn()
            df = build_df(records, exchange, tse_tickers)
            load_to_bq(df, exchange, bq_client, dry_run=args.dry_run)
            log.info("exchange_done", exchange=exchange, loaded=len(df))
        except Exception:
            log.exception("exchange_failed", exchange=exchange)
            raise


if __name__ == "__main__":
    main()
