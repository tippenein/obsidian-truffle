"""Background worker for Obsidian vault monitoring with change detection."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from config import NodeConfig, parse_nodes
from local_client import LocalVaultClient
from obsidian_client import ObsidianClient

logger = logging.getLogger("obsidian.bg_worker")


@dataclass
class BackgroundDigest:
    generated_at: str
    vault_summary: str = ""
    node_alerts: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


class ObsidianBackgroundWorker:
    def __init__(self) -> None:
        self._nodes: list[NodeConfig] = parse_nodes()
        self._clients: dict[str, ObsidianClient | LocalVaultClient] = {}
        for node in self._nodes:
            if node.type == "local":
                self._clients[node.name] = LocalVaultClient(node)
            else:
                self._clients[node.name] = ObsidianClient(node)
        self._last_reachable: dict[str, bool] = {}
        self._last_file_counts: dict[str, int] = {}
        self._is_seeded: bool = False

    async def verify(self) -> tuple[bool, str]:
        """Check if at least one configured node is reachable."""
        if not self._nodes:
            return False, "No Obsidian nodes configured (OBSIDIAN_NODES is empty)"

        results: list[str] = []
        any_ok = False
        for name, client in self._clients.items():
            reachable = await client.ping()
            status = "reachable" if reachable else "unreachable"
            results.append(f"  {name}: {status}")
            if reachable:
                any_ok = True

        summary = "\n".join(results)
        if any_ok:
            return True, f"Obsidian nodes:\n{summary}"
        return False, f"No Obsidian nodes reachable:\n{summary}"

    async def run_cycle(self) -> BackgroundDigest:
        generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()

        if not self._nodes:
            return BackgroundDigest(
                generated_at=generated_at,
                error="No Obsidian nodes configured",
            )

        node_alerts: list[dict[str, Any]] = []
        node_summaries: list[str] = []

        for name, client in self._clients.items():
            stats = await client.vault_stats()
            reachable = stats.get("reachable", False)
            file_count = stats.get("file_count", 0)

            # Detect reachability changes
            if name in self._last_reachable:
                was_reachable = self._last_reachable[name]
                if was_reachable and not reachable:
                    node_alerts.append({
                        "type": "went_offline",
                        "node": name,
                    })
                elif not was_reachable and reachable:
                    node_alerts.append({
                        "type": "came_online",
                        "node": name,
                    })

            # Detect file count changes
            if reachable and name in self._last_file_counts:
                old_count = self._last_file_counts[name]
                if old_count != file_count:
                    node_alerts.append({
                        "type": "vault_changed",
                        "node": name,
                        "previous_count": old_count,
                        "current_count": file_count,
                    })

            self._last_reachable[name] = reachable
            if reachable:
                self._last_file_counts[name] = file_count

            status = f"{file_count} files" if reachable else "offline"
            node_summaries.append(f"{name}: {status}")

        vault_summary = "Obsidian vaults: " + ", ".join(node_summaries)

        # First cycle seeds state without emitting alerts
        if not self._is_seeded:
            self._is_seeded = True
            return BackgroundDigest(
                generated_at=generated_at,
                vault_summary=vault_summary,
            )

        return BackgroundDigest(
            generated_at=generated_at,
            vault_summary=vault_summary,
            node_alerts=node_alerts,
        )

    async def close(self) -> None:
        """Close all underlying HTTP clients."""
        for client in self._clients.values():
            await client.close()
