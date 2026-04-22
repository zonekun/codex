# xbrl-reader — EDINET XBRL 決算データ CSV 変換ツール

> **著者**: teatime77（[@teatime77](https://twitter.com/teatime77)）
> **GitHub**: https://github.com/teatime77/xbrl-reader
> **ライセンス**: MIT

## 何ができるか

- EDINET API で全上場企業の XBRL ファイルを一括ダウンロード
- XBRL → CSV 変換（1行＝1決算、連結決算値）
- インライン XBRL（iXBRL）のブラウザ表示・数値確認
- XBRL → JSON 変換 → Web ページ表示（別アプリ）
- Plotly.js による財務分析 Web アプリ（[xbrl-chart](https://github.com/teatime77/xbrl-chart)）

## キーワード

EDINET, EDINET API, XBRL, iXBRL, インラインXBRL, 決算書, 財務諸表,
貸借対照表, 損益計算書, 連結決算, CSV変換, JSON変換,
EDINETコード, 有価証券報告書, 全上場企業, Python, Plotly.js

## 技術スタック

| 項目 | 内容 |
|------|------|
| 言語 | Python 53.3%, Jupyter Notebook 37.7%, HTML 7.0%, JS 2.0% |
| データソース | EDINET API（金融庁） |
| 入力形式 | XBRL（ZIP内 `XBRL/PublicDoc/*ixbrl.htm`） |
| 出力形式 | CSV（`summary-join.csv`） / JSON |
| 可視化 | Plotly.js（xbrl-chart） |

## XBRL ファイル構造メモ

- ZIP は提出年月日ごとにフォルダ保存: `zip/download/YYYY/MM/DD/`
- ZIP ファイル名先頭6桁 = EDINET コード（例: `E00012`）
- インライン XBRL: `XBRL/PublicDoc/` 内の `*ixbrl.htm`
- BS/PL は `0105010` / `0105020` で始まる HTM に記載（例外あり）

## 関連リソース

| リソース | URL |
|----------|-----|
| Qiita 記事（CSV変換） | https://qiita.com/teatime77/items/e5aa2d9027749768f50d |
| Qiita 記事（XBRL→JSON） | https://qiita.com/teatime77/items/3ed6d4cd27f6440e163a |
| 処理説明ドキュメント | http://lkzf.info/xbrl/doc |
| 財務分析 Web アプリ | http://lkzf.info/xbrl/chart/ |
| 公開 CSV | http://lkzf.info/xbrl/data/summary-join.csv |
| GitHub wiki（JSON変換） | [XBRLファイルをJSONに変換してウェブページに表示](https://github.com/teatime77/xbrl-reader/wiki/) |

## このフォルダ内のファイル

| ファイル | 内容 |
|----------|------|
| [qiita_teatime77_xbrl_csv.md](qiita_teatime77_xbrl_csv.md) | Qiita 記事全文（XBRL→CSV変換の解説） |
| [github_teatime77_xbrl_reader_README.md](github_teatime77_xbrl_reader_README.md) | GitHub README 原文 |
