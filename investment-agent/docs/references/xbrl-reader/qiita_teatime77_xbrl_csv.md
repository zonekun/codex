# 全上場企業の過去５年間の決算情報をCSVファイルに変換

> **ソース**: https://qiita.com/teatime77/items/e5aa2d9027749768f50d
> **著者**: @teatime77
> **公開日**: 2020-03-19 / **最終更新**: 2022-11-19
> **タグ**: Python, 株価, 財務分析, XBRL, EDINET

---

## はじめに

前回の記事では金融庁の[EDINET](http://disclosure.edinet-fsa.go.jp/)というサイトから得たXBRL形式のデータから決算書の情報を得る方法について書きました。

[XBRLから上場企業の決算書の情報を得る。](https://qiita.com/teatime77/items/3ed6d4cd27f6440e163a)

その後EDINETでは **EDINET API** というWebAPIが公開され自動でXBRLファイルをダウンロードできるようになりました。

今回は全上場企業の過去５年間のXBRLファイルをダウンロードして、CSVファイルに変換するプログラムを作ってみました。

処理結果のCSVファイルは以下にあります。
[http://lkzf.info/xbrl/data/summary-join.csv](http://lkzf.info/xbrl/data/summary-join.csv)
以下のように１行ごとに決算情報が書かれています。
決算の数値は連結決算の値です。
![集計.png](https://qiita-user-contents.imgix.net/https%3A%2F%2Fqiita-image-store.s3.ap-northeast-1.amazonaws.com%2F0%2F118740%2Ff6cc4f1f-a3ec-5856-e0d9-cbb68c92f9c5.png?ixlib=rb-4.0.0&auto=format&gif-q=60&q=75&s=555a719b9a6e9f178735573d90bfa1ee)

## 簡単な応用例

表計算ソフト(LibreOffice Calc)を使ってCSVファイルから売上総利益率(粗利率)の度数分布表を作ってみました。
ほかにもいろんな使い方があると思います。
![hist.png](https://qiita-user-contents.imgix.net/https%3A%2F%2Fqiita-image-store.s3.ap-northeast-1.amazonaws.com%2F0%2F118740%2F84b70e08-3cc2-0de7-bda5-e09fd6c902b1.png?ixlib=rb-4.0.0&auto=format&gif-q=60&q=75&s=0573f31f506f1a9719c1c6d2a6f1144f)

## CSVファイルの作成手順

アプリのソースは前回の記事と同じGitHubに入っています。
[https://github.com/teatime77/xbrl-reader](https://github.com/teatime77/xbrl-reader)

以下に処理内容の説明があります。
[http://lkzf.info/xbrl/doc](http://lkzf.info/xbrl/doc)

## 財務分析のサンプルアプリ

CSVファイルのデータを使って財務分析をするウェブアプリも作ってみました。
以下のURLからアプリにアクセスできます。
[http://lkzf.info/xbrl/chart/](http://lkzf.info/xbrl/chart/)
![app.png](https://qiita-user-contents.imgix.net/https%3A%2F%2Fqiita-image-store.s3.ap-northeast-1.amazonaws.com%2F0%2F118740%2F550f5f84-1fdc-66c6-cc2c-a0584b72abf5.png?ixlib=rb-4.0.0&auto=format&gif-q=60&q=75&s=31448d7b21513016f196875ac6ff1335)
ChromeとFireFoxで動作を確認しています。
グラフの描画に[Plotly.js](https://plot.ly/javascript/)を使いました。
ソースはGitHubにあります。
[https://github.com/teatime77/xbrl-chart](https://github.com/teatime77/xbrl-chart)

## インラインXBRLファイルで決算情報を確認

EDINETからダウンロードしたZIPファイルの中には、HTMLファイルにXBRLタグを埋め込んだ **インラインXBRLファイル** があります。
このファイルは下図のようにブラウザで開いて内容が見れるので、数値の確認ができます。
![財務諸表.png](https://qiita-user-contents.imgix.net/https%3A%2F%2Fqiita-image-store.s3.ap-northeast-1.amazonaws.com%2F0%2F118740%2Fd190a78a-6d33-3ba8-1af8-84b1d07d266d.png?ixlib=rb-4.0.0&auto=format&gif-q=60&q=75&s=5927a0d78a2d0ce72ed31acf2281181d)

ZIPファイルは提出年月日ごとにフォルダに入っています。
例えば提出年月日が **2022/06/24** なら、下図のように **xbrl-reader\\zip\\download\\2022\\06\\24** にZIPファイルがあります。

ZIPのファイル名の先頭6桁がEDINETコードです。この例では **E00012** がEDINETコードです。

ZIPファイルの中の **XBRL\\PublicDoc** の中で末尾が **ixbrl.htm** のファイルが **インラインXBRLファイル** です。
![エクスプローラー.png](https://qiita-user-contents.imgix.net/https%3A%2F%2Fqiita-image-store.s3.ap-northeast-1.amazonaws.com%2F0%2F118740%2F0fe148a7-4178-b752-ae5d-aa2894330b47.png?ixlib=rb-4.0.0&auto=format&gif-q=60&q=75&s=a2e0da9ff14c36301e3e2bac849c553c)

貸借対照表や損益計算書は **0105010** や **0105020** で始まる HTMファイルにあることが多いです。 (例外もあります。)

## おわりに

まちがいなどがあれば、コメント欄に書かれるかTwitterに連絡をお願いします。
Twitterでアプリの更新のお知らせもしていきます。
[teatime77](https://twitter.com/teatime77)

**ここまで読んでいただき、ありがとうございました。**
