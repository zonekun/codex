-- fn_consensus_merged_asof: 指定日時点のQUICK優先・IFIS補完マージTVF
-- TARGET列廃止、5項目対応
-- 実行日: 2026-05-05

CREATE OR REPLACE TABLE FUNCTION `gmailpj-357912.STOCK.fn_consensus_merged_asof`(
  as_of_date DATE
) AS (
  WITH quick_latest AS (
    SELECT
      TICKER, FY, QUARTER,
      REVENUE, OP_PROFIT, ORD_PROFIT, NET_PROFIT, EPS
    FROM `gmailpj-357912.STOCK.CONSENSUS`
    WHERE SOURCE = 'QUICK'
      AND DATAAT <= as_of_date
    QUALIFY ROW_NUMBER() OVER (
      PARTITION BY TICKER, FY, QUARTER
      ORDER BY DATAAT DESC
    ) = 1
  ),
  ifis_latest AS (
    SELECT
      TICKER, FY, QUARTER,
      ORD_PROFIT
    FROM `gmailpj-357912.STOCK.CONSENSUS`
    WHERE SOURCE = 'IFIS'
      AND DATAAT <= as_of_date
    QUALIFY ROW_NUMBER() OVER (
      PARTITION BY TICKER, FY, QUARTER
      ORDER BY DATAAT DESC
    ) = 1
  )
  SELECT
    COALESCE(q.TICKER, i.TICKER) AS TICKER,
    COALESCE(q.FY, i.FY) AS FY,
    COALESCE(q.QUARTER, i.QUARTER) AS QUARTER,
    q.REVENUE,
    q.OP_PROFIT,
    COALESCE(q.ORD_PROFIT, i.ORD_PROFIT) AS ORD_PROFIT,
    q.NET_PROFIT,
    q.EPS,
    as_of_date AS AS_OF_DATE
  FROM quick_latest q
  FULL OUTER JOIN ifis_latest i
    ON q.TICKER = i.TICKER
    AND q.FY = i.FY
    AND q.QUARTER = i.QUARTER
);
