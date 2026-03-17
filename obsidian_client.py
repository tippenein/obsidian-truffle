"""Async client wrapping the Obsidian Local REST API."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from config import NodeConfig

logger = logging.getLogger("obsidian.client")


class ObsidianClient:
    """One instance per Obsidian node, wrapping httpx.AsyncClient(verify=False)."""

    def __init__(self, node: NodeConfig, timeout: float = 15.0) -> None:
        self.node = node
        self._base_url = f"https://{node.host}:{node.port}"
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            verify=False,
            timeout=timeout,
            headers={"Authorization": f"Bearer {node.api_key}"},
        )

    async def ping(self) -> bool:
        """Check if the Obsidian REST API is reachable."""
        try:
            resp = await self._http.get("/")
            return resp.status_code == 200
        except Exception:
            return False

    async def list_files(self, directory: str = "/") -> list[dict[str, Any]]:
        """List files and directories in a vault path.

        The API requires a trailing slash on directory paths.
        """
        path = directory.rstrip("/") + "/"
        resp = await self._http.get(
            f"/vault/{path}",
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json().get("files", [])

    async def get_file(self, file_path: str) -> dict[str, Any]:
        """Get structured note data (content, frontmatter, stat).

        Uses application/vnd.olrapi.note+json for rich response.
        """
        resp = await self._http.get(
            f"/vault/{file_path}",
            headers={"Accept": "application/vnd.olrapi.note+json"},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_file_content(self, file_path: str) -> str:
        """Get raw markdown content of a note."""
        resp = await self._http.get(
            f"/vault/{file_path}",
            headers={"Accept": "text/markdown"},
        )
        resp.raise_for_status()
        return resp.text

    async def put_file(self, file_path: str, content: str) -> None:
        """Create or overwrite a note with markdown content."""
        resp = await self._http.put(
            f"/vault/{file_path}",
            content=content,
            headers={"Content-Type": "text/markdown"},
        )
        resp.raise_for_status()

    async def append_file(self, file_path: str, content: str) -> None:
        """Append markdown content to an existing note."""
        resp = await self._http.post(
            f"/vault/{file_path}",
            content=content,
            headers={"Content-Type": "text/markdown"},
        )
        resp.raise_for_status()

    async def delete_file(self, file_path: str) -> None:
        """Delete a note from the vault."""
        resp = await self._http.delete(f"/vault/{file_path}")
        resp.raise_for_status()

    async def patch_file(
        self,
        file_path: str,
        operation: str,
        target_type: str,
        target: str,
        content: str,
    ) -> None:
        """Patch a note using the Obsidian REST API's PATCH semantics.

        Uses custom HTTP headers: Operation, Target-Type, Target.
        """
        resp = await self._http.patch(
            f"/vault/{file_path}",
            content=content,
            headers={
                "Content-Type": "text/markdown",
                "Operation": operation,
                "Target-Type": target_type,
                "Target": target,
            },
        )
        resp.raise_for_status()

    async def search(self, query: str, context_length: int = 100) -> list[dict[str, Any]]:
        """Full-text search across the vault.

        POST /search/simple/ with text/plain body.
        """
        resp = await self._http.post(
            "/search/simple/",
            content=query,
            params={"contextLength": context_length},
            headers={"Content-Type": "text/plain"},
        )
        resp.raise_for_status()
        return resp.json()

    async def vault_stats(self) -> dict[str, Any]:
        """Get vault statistics for background monitoring.

        Returns file count and reachability info.
        """
        try:
            files = await self.list_files("/")
            return {
                "reachable": True,
                "file_count": len(files),
            }
        except Exception as e:
            return {
                "reachable": False,
                "error": str(e),
            }

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        try:
            await self._http.aclose()
        except Exception:
            pass
