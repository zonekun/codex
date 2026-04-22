"""知見管理モジュール.

docs/knowledges/ 配下の知見mdファイルを管理する。
- カテゴリ別の保存・取得
- 一覧表示（タイトル・ステータス付き）
- 削除（個別/廃止一括）
- 次の連番の自動採番

知見カテゴリ:
  analysis  - 分析で得られた知見
  data      - 取得済みデータ情報・定期取得ジョブ情報
  tools     - Pythonパッケージ・スクリプトの使用方法
  api       - 外部API仕様
  strategies - 投資戦略知見
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from src.core.config import app_config
from src.core.logger import get_logger

log = get_logger(__name__)

VALID_CATEGORIES = ["analysis", "data", "tools", "api", "strategies"]


class KnowledgeManager:
    """docs/knowledges/ の知見ファイルを管理する."""

    def __init__(self) -> None:
        self.base_dir = Path(app_config["knowledge"]["base_dir"])
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _next_number(self, category: str) -> int:
        """カテゴリ内の次の連番を返す."""
        cat_dir = self.base_dir / category
        if not cat_dir.exists():
            return 1
        existing = sorted(cat_dir.glob("*.md"))
        max_num = 0
        for f in existing:
            match = re.match(r"(\d{3})_", f.name)
            if match:
                max_num = max(max_num, int(match.group(1)))
        return max_num + 1

    def save(
        self,
        category: str,
        slug: str,
        title: str,
        content: str,
        status: str = "有効",
        related_files: str = "",
    ) -> Path:
        """知見を保存する.

        Args:
            category: カテゴリ名（analysis, data, tools, api, strategies）
            slug: ファイル名スラッグ（英語スネークケース）
            title: 知見タイトル
            content: 知見の本文（markdown）
            status: ステータス（有効/要検証/廃止）
            related_files: 関連ファイルパス

        Returns:
            保存先パス
        """
        if category not in VALID_CATEGORIES:
            raise ValueError(f"無効なカテゴリ: {category}. 有効: {VALID_CATEGORIES}")

        num = self._next_number(category)
        filename = f"{num:03d}_{slug}.md"
        filepath = self.base_dir / category / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        md = f"""# {title}

**カテゴリ**: {category}
**作成日**: {datetime.now().strftime('%Y-%m-%d')}
**ステータス**: {status}
**関連ファイル**: {related_files}

{content}
"""
        filepath.write_text(md, encoding="utf-8")
        log.info("knowledge_saved", path=str(filepath), category=category)
        return filepath

    def list_all(self, category: str | None = None) -> list[dict]:
        """知見一覧を取得する.

        Args:
            category: カテゴリで絞込み（省略時は全カテゴリ）

        Returns:
            [{"path": Path, "category": str, "filename": str,
              "title": str, "status": str, "created": str}]
        """
        results = []
        categories = [category] if category else VALID_CATEGORIES

        for cat in categories:
            cat_dir = self.base_dir / cat
            if not cat_dir.exists():
                continue
            for f in sorted(cat_dir.glob("*.md")):
                if f.name == "README.md":
                    continue
                info = self._parse_header(f)
                info["path"] = f
                info["category"] = cat
                info["filename"] = f.name
                results.append(info)

        return results

    def _parse_header(self, filepath: Path) -> dict:
        """mdファイルのヘッダからタイトル・ステータス・作成日を抽出する."""
        text = filepath.read_text(encoding="utf-8")
        title = ""
        status = ""
        created = ""

        for line in text.split("\n")[:10]:
            if line.startswith("# "):
                title = line[2:].strip()
            elif "**ステータス**:" in line:
                status = line.split(":", 1)[1].strip().strip("*")
            elif "**作成日**:" in line:
                created = line.split(":", 1)[1].strip().strip("*")

        return {"title": title, "status": status, "created": created}

    def delete(self, filepath: str | Path) -> bool:
        """指定した知見ファイルを削除する."""
        path = Path(filepath)
        if path.exists():
            path.unlink()
            log.info("knowledge_deleted", path=str(path))
            return True
        log.warn("knowledge_not_found", path=str(path))
        return False

    def delete_by_status(self, status: str = "廃止") -> int:
        """指定ステータスの知見をすべて削除する."""
        count = 0
        for item in self.list_all():
            if item["status"] == status:
                self.delete(item["path"])
                count += 1
        log.info("knowledge_bulk_deleted", status=status, count=count)
        return count

    def get_recent(self, category: str | None = None, limit: int = 10) -> list[str]:
        """最近の知見のテキストを取得する（LLMコンテキスト用）."""
        items = self.list_all(category)
        # 有効なもののみ、更新日時の新しい順
        valid = [i for i in items if i.get("status") != "廃止"]
        valid.sort(key=lambda x: x["path"].stat().st_mtime, reverse=True)
        return [i["path"].read_text(encoding="utf-8") for i in valid[:limit]]
