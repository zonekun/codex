# アクティビスト検出ツール群

**カテゴリ**: tools
**作成日**: 2026-04-03
**ステータス**: 有効
**関連ファイル**:
- `scripts/flag_activists_in_list.py`
- `scripts/generate_activist_aliases.py`
- `scripts/activist_edinet_scan.py`
- `data/master/activists.csv`
- `data/master/activist_aliases.csv`

## 概要

四季報大株主リストやEDINET大量保有報告書からアクティビストファンドの保有を検出する3つのスクリプト群。`activists.csv`（38ファンド）+ `activist_aliases.csv`（300+エイリアス）をマスタとして、正規化+ファジーマッチングで判定する。

## スクリプト一覧

### 1. flag_activists_in_list.py — 四季報大株主フラグ付与

四季報の大株主リスト（テキストファイル、1行1株主名）にアクティビストフラグを付与してCSV出力。

```bash
# デフォルト入出力
PYTHONUTF8=1 python scripts/flag_activists_in_list.py

# 入出力指定
PYTHONUTF8=1 python scripts/flag_activists_in_list.py -i path/to/input.txt -o path/to/output.csv
```

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| `--input`, `-i` | `C:\Users\zonekun\Dropbox\shikihokabu.txt` | 入力テキストファイル |
| `--output`, `-o` | `data/csv/shareholders_activist_flag.csv` | 出力CSV |

**出力カラム**: shareholder_name, is_activist, activist_name, activist_region, match_method, match_score

**マスタ**: `activists.csv` + `activist_aliases.csv` 両方を使用

### 2. generate_activist_aliases.py — エイリアス生成（Gemini）

`activists.csv` の各ファンドについて Gemini 2.5 Pro に関連法人名・ファンドビークル名・SPC名・個人名義を生成させ、`activist_aliases.csv` に追記する。

```bash
PYTHONUTF8=1 python scripts/generate_activist_aliases.py
PYTHONUTF8=1 python scripts/generate_activist_aliases.py --dry-run  # 生成結果を確認のみ
```

### 3. activist_edinet_scan.py — EDINET大量保有報告書スキャン

EDINET API で大量保有報告書（府令コード060）を日付ループで取得し、提出者名（filerName/submitterName）をファジーマッチングでアクティビスト判定。

```bash
PYTHONUTF8=1 python scripts/activist_edinet_scan.py --start 20240101
PYTHONUTF8=1 python scripts/activist_edinet_scan.py --start 20240101 --end 20260329
```

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| `--start` | `20230101` | 開始日 YYYYMMDD |
| `--end` | 今日 | 終了日 YYYYMMDD |

**出力**: `data/logs/activist_edinet_scan.csv`
**所要時間目安**: 営業日数 x 0.3秒（2年分で約3分）

## マッチングロジック（共通）

テキスト正規化（全角/半角統一、カタカナ統一、法人格除去）後、以下の順で判定:

| 優先度 | method | score | 条件 |
|--------|--------|-------|------|
| 1 | `exact` | 100 | 正規化後の完全一致 |
| 2 | `partial` | 95 | アクティビスト名（4文字以上）が株主名に含まれる |
| 3 | `word_start` | 93 | アクティビスト名（3文字以下）が先頭単語と一致 |
| 4 | `partial_rev` | 90 | 株主名（8文字以上）がアクティビスト名に含まれる（ブロックリスト除外） |
| 5 | `fuzzy` | 85+ | rapidfuzz ratio（`activist_edinet_scan.py` のみ） |

## マスタデータ

### activists.csv（38ファンド）

| 地域 | 件数 | 主なファンド |
|------|------|------------|
| 国内 | 14 | 光通信、シティインデックスイレブンス、ストラテジックキャピタル、南青山不動産 |
| シンガポール | 6 | 3D、エフィッシモ、ひびき、シルバーケイプ |
| 香港 | 6 | オアシス、LIM、MCD |
| 米国 | 5 | バリューアクト、ダルトン、ブランデス |
| 英国 | 4 | NAVF、AVI、シルチェスター、ゼナーアセット |

### activist_aliases.csv（300+エイリアス）

Gemini 2.5 Pro で生成。ファンドビークル名・SPC名・個人名義（日英両方）を収録。
光通信グループだけで重田一族個人名、UH Partners 1〜6、エスアイエル、プレミアムウォーターHD等を網羅。

## 既知の注意点

- `プレミアムウォーター(株)` が光通信として `partial_rev` マッチする（光通信子会社だが株主としてはアクティビストではない）。現状は許容
- `flag_activists_in_list.py` の `SHAREHOLDER_EXCLUSIONS` に汎用的な組合名（「2投資事業有限責任組合」等）を除外登録済み
- `PARTIAL_REV_BLOCKLIST` に INVESTMENTS, MANAGEMENT 等の一般用語を登録し、逆方向マッチの誤検出を抑制
