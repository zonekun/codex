# Segment Structure Company Agent

対象: Codexサブエージェントによる1社分のセグメント構成変化分析

## Input

呼び出し元は、次を渡す。

- ticker
- company
- market_cap_oku
- period
- public-sector exclusion result
- source pack path or BQ reference

会社ごとの作業用MDは作らない。エージェントはtickerをキーに、既存パックまたはBQから対象会社1社分の最新版資料と候補チャンクを参照する。

## Task

対象会社1社について、セグメント構成変化を分析する。

スコアはセグメント構成変化だけで付ける。需要先、用途先、地域、顧客、収益モデルの変化だけではスコアを上げない。

ただし、テーマ外でも投資上大きい発見があれば `surprise` に明示する。低スコアでも、別軸の驚きは強く書く。

## Output

JSON 1件だけを返す。Markdown表や箇条書きでは返さない。

```json
{
  "ticker": "5332",
  "company": "TOTO",
  "market_cap_oku": 11213.0,
  "score": 5,
  "score_label": "変化した",
  "evidence_strength": "strong",
  "content": "...",
  "evidence": "...",
  "continuity": "...",
  "surprise": "...",
  "investment_view": "...",
  "risks": "..."
}
```

## Score Criteria

| score | label | criteria |
|---:|---|---|
| 1 | 変化なし | 全社業績や既存主力の延長で、事業構成の変化が数字で読めない |
| 2 | 兆しあり | 新領域、成長投資、セグメント変更などの兆しはあるが、売上または利益貢献はまだ小さい |
| 3 | 変化余地あり | 成長領域の数字は読めるが、利益中核の転換までは未確認 |
| 4 | 変化進行中 | 成長領域が売上または利益で明確に伸び、会社計画でも拡大が確認できる |
| 5 | 変化した | 利益中核の入れ替わり、または主力事業を上回る利益貢献が数字で確認できる |

`evidence_strength` は次のいずれかにする。

- `strong`: セグメント別または事業別の売上、利益、構成比、前年差、計画値が複数読める。
- `medium`: 主要な数字は読めるが、利益中核判定に一部不足がある。
- `weak`: 数字根拠が薄く、分析対象として弱い。

