# Claude Code / Codex 並走運用ルール

作成日: 2026-04-22  
対象プロジェクト: `investment-agent`

## 1. 目的

本書は、`investment-agent` を Claude Code と Codex で並走運用するためのルールを定める。  
当面の本運用は Claude Code 側で継続し、Codex 側は同一 Git を前提とした改修、検証、運用知識移植のための作業系として扱う。

## 2. 対象環境

- Claude Code マスタフォルダ: `G:\マイドライブ\claude`
- Claude Code プロジェクト: `G:\マイドライブ\claude\investment-agent`
- Claude Code 実運用参照パス: `C:\gdrive\claude\investment-agent`
- Codex マスタフォルダ: `C:\Users\zonekun\Documents\codex`
- Codex プロジェクト: `C:\Users\zonekun\Documents\codex\investment-agent`
- Codex Git ルート: `C:\Users\zonekun\Documents\codex`

## 3. 基本方針

- 当面の本運用は Claude Code 側を正系とする
- Codex 側は並走運用の作業環境として利用する
- プログラムの Git リポジトリは Claude Code / Codex で共有する
- Git の管理単位は `investment-agent` 単体ではなく `C:\Users\zonekun\Documents\codex` 全体とする
- Codex 側で行った改修は Codex 専用ブランチに反映する
- そのブランチの確認、レビュー、取り込みは Claude Code 側で行う
- 本番相当の最終判断は Claude Code 側で行う

## 4. フォルダ配置ルール

- フォルダ構成、主要配置、運用上の前提は `CLAUDE.md` の定義に従う
- 日本語パス回避のため、Claude Code 側の実運用参照は `C:\gdrive\claude\investment-agent` を優先する
- Codex 側への取り込みでも、原則として `CLAUDE.md` に記載された構成を崩さない
- 例外的な配置変更が必要な場合は、Codex 側の独断では行わず、Claude Code 側の運用前提に合わせて判断する

## 5. 取り込み対象

Codex 側には `investment-agent` の構成を原則として全部取り込む。

対象には以下を含む。

- アプリケーションコード
- `scripts/`
- `src/`
- `tests/`
- `docs/`
- `config/`
- `functions/`
- `skills/`
- `reference_code/`
- `.env`
- `.mcp.json`
- `keys/`
- GCP 関連キー
- その他の認証情報
- 運用ドキュメント
- Claude Code 側で運用上参照している関連ファイル一式

除外や間引きは、現時点では行わない方針とする。

## 6. Git 運用ルール

- 共通 Git を使用する
- Codex 側の常設作業ブランチは `codex/integration` を推奨する
- 必要に応じて個別作業ブランチ `codex/feature-xxxx` または `codex/fix-xxxx` を作成する
- Codex 側は原則として `main` 相当の本流ブランチへ直接反映しない
- Claude Code 側が `codex/*` ブランチを確認し、必要な変更のみ取り込む

## 7. Codex 側で許可する作業

- コード修正
- 調査
- リファクタリング
- テスト実行
- MCP 設定確認
- 運用知識の文書化
- スクリプトの動作確認
- handoff 文書の作成

## 8. Codex 側で注意する事項

- 本運用の正系判断は Claude Code 側に残す
- 重要変更は Codex 側だけで完結させない
- secrets、keys、認証情報は欠落なく移すが、扱いは既存運用に従う
- MCP、GCP、ブラウザ操作、定期実行などの運用系変更は、Claude Code 側での取り込み前提で整理する
- `CLAUDE.md` にある運用知識は、Codex 用に読み替えて文書化するが、元の運用ルールを勝手に破棄しない

## 9. Claude Code 側の取り込み手順

- `codex/integration` または対象の `codex/*` ブランチを確認する
- 変更内容をレビューする
- 必要に応じてテスト、主要スクリプト、MCP の確認を行う
- 問題がなければ Claude Code 側で本流へ取り込む
- 本流取り込みの最終判断は Claude Code 側で行う

## 10. 今後の扱い

- 当面は Claude Code を本運用として継続する
- Codex が実運用可能と判断できるまでは並走運用とする
- Codex の本稼働 GO 条件は別途定義する
- 将来の G ドライブ移設方針は別途検討する
