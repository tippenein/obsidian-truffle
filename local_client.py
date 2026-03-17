"""Filesystem-based client for local Obsidian vaults (no REST API needed)."""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any

from config import NodeConfig

logger = logging.getLogger("obsidian.local_client")


class LocalVaultClient:
    """Manages a local vault as a directory of markdown files."""

    def __init__(self, node: NodeConfig) -> None:
        self.node = node
        self._root = Path(node.path)

    async def ping(self) -> bool:
        """Check if the vault directory exists."""
        return self._root.is_dir()

    async def list_files(self, directory: str = "/") -> list[str]:
        """List files in a vault subdirectory."""
        target = self._root / directory.strip("/")
        if not target.is_dir():
            return []
        results: list[str] = []
        for entry in sorted(target.iterdir()):
            # Skip hidden dirs like .obsidian, .trash
            if entry.name.startswith("."):
                continue
            rel = str(entry.relative_to(self._root))
            if entry.is_dir():
                results.append(rel + "/")
            else:
                results.append(rel)
        return results

    async def get_file(self, file_path: str) -> dict[str, Any]:
        """Read a note and return structured data."""
        full = self._root / file_path.strip("/")
        if not full.is_file():
            raise FileNotFoundError(f"Not found: {file_path}")
        content = full.read_text(encoding="utf-8")
        stat = full.stat()
        return {
            "content": content,
            "frontmatter": None,
            "tags": None,
            "stat": {
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
                "ctime": int(stat.st_ctime),
            },
        }

    async def get_file_content(self, file_path: str) -> str:
        """Get raw markdown content."""
        full = self._root / file_path.strip("/")
        if not full.is_file():
            raise FileNotFoundError(f"Not found: {file_path}")
        return full.read_text(encoding="utf-8")

    async def put_file(self, file_path: str, content: str) -> None:
        """Create or overwrite a note."""
        full = self._root / file_path.strip("/")
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    async def append_file(self, file_path: str, content: str) -> None:
        """Append content to a note."""
        full = self._root / file_path.strip("/")
        full.parent.mkdir(parents=True, exist_ok=True)
        with open(full, "a", encoding="utf-8") as f:
            f.write(content)

    async def delete_file(self, file_path: str) -> None:
        """Delete a note."""
        full = self._root / file_path.strip("/")
        if not full.is_file():
            raise FileNotFoundError(f"Not found: {file_path}")
        full.unlink()

    async def search(self, query: str, context_length: int = 100) -> list[dict[str, Any]]:
        """Simple text search across all markdown files in the vault."""
        results: list[dict[str, Any]] = []
        query_lower = query.lower()
        for md_file in self._root.rglob("*.md"):
            if md_file.name.startswith("."):
                continue
            # Skip hidden directories
            rel = md_file.relative_to(self._root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            try:
                text = md_file.read_text(encoding="utf-8")
            except Exception:
                continue
            text_lower = text.lower()
            idx = text_lower.find(query_lower)
            if idx == -1:
                continue
            # Build context matches
            matches: list[dict[str, Any]] = []
            search_from = 0
            while True:
                pos = text_lower.find(query_lower, search_from)
                if pos == -1:
                    break
                start = max(0, pos - context_length)
                end = min(len(text), pos + len(query) + context_length)
                matches.append({
                    "match": {"start": pos, "end": pos + len(query)},
                    "context": text[start:end],
                })
                search_from = pos + 1
            results.append({
                "filename": str(rel),
                "matches": matches,
            })
        return results

    async def vault_stats(self) -> dict[str, Any]:
        """Get vault stats for background monitoring."""
        if not self._root.is_dir():
            return {"reachable": False, "error": "vault directory missing"}
        count = sum(
            1 for f in self._root.rglob("*.md")
            if not any(p.startswith(".") for p in f.relative_to(self._root).parts)
        )
        return {"reachable": True, "file_count": count}

    async def close(self) -> None:
        """No-op for local vaults."""
        pass
