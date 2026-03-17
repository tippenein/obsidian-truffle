"""Configuration for the Obsidian Truffle app."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from pathlib import Path

logger = logging.getLogger("obsidian.config")

# JSON array of node configs:
# [{"name": "desktop", "host": "100.64.0.1", "port": 27124, "api_key": "..."}]
OBSIDIAN_NODES_RAW: str = os.getenv("OBSIDIAN_NODES", "[]")

# Persistent file for nodes added at runtime via MCP tools
_NODES_FILE = Path(os.getenv("OBSIDIAN_NODES_FILE", "/data/obsidian_nodes.json"))


DEFAULT_VAULTS_DIR = Path(os.getenv("OBSIDIAN_VAULTS_DIR", "/data/vaults"))


@dataclass(frozen=True)
class NodeConfig:
    name: str
    type: str = "local"        # "local" or "remote"
    path: str = ""             # filesystem path for local vaults
    host: str = ""             # Tailscale IP/hostname for remote nodes
    api_key: str = ""          # Bearer token for remote nodes
    port: int = 27124          # REST API port for remote nodes


def _parse_entries(raw: list) -> list[NodeConfig]:
    """Parse a list of dicts into NodeConfig objects."""
    nodes: list[NodeConfig] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            logger.warning("Skipping node entry [%d]: not a dict", i)
            continue
        if "name" not in entry:
            logger.warning("Skipping node entry [%d]: missing 'name'", i)
            continue
        node_type = entry.get("type", "remote")
        if node_type == "local":
            nodes.append(NodeConfig(
                name=entry["name"],
                type="local",
                path=entry.get("path", ""),
            ))
        else:
            try:
                nodes.append(NodeConfig(
                    name=entry["name"],
                    type="remote",
                    host=entry["host"],
                    api_key=entry["api_key"],
                    port=entry.get("port", 27124),
                ))
            except KeyError as e:
                logger.warning("Skipping remote node entry [%d]: missing key %s", i, e)
    return nodes


def _load_env_nodes() -> list[NodeConfig]:
    """Parse nodes from OBSIDIAN_NODES env var."""
    try:
        raw = json.loads(OBSIDIAN_NODES_RAW)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse OBSIDIAN_NODES: %s", e)
        return []

    if not isinstance(raw, list):
        logger.error("OBSIDIAN_NODES must be a JSON array, got %s", type(raw).__name__)
        return []

    return _parse_entries(raw)


def _load_file_nodes() -> list[NodeConfig]:
    """Load nodes from persistent JSON file."""
    if not _NODES_FILE.exists():
        return []
    try:
        raw = json.loads(_NODES_FILE.read_text())
        if not isinstance(raw, list):
            return []
        return _parse_entries(raw)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load %s: %s", _NODES_FILE, e)
        return []


def _save_file_nodes(nodes: list[NodeConfig]) -> None:
    """Persist nodes to JSON file."""
    _NODES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _NODES_FILE.write_text(json.dumps([asdict(n) for n in nodes], indent=2))


def parse_nodes() -> list[NodeConfig]:
    """Return merged nodes from env var + persistent file.

    File-based nodes take precedence (by name) over env var nodes.
    """
    env_nodes = _load_env_nodes()
    file_nodes = _load_file_nodes()

    # File nodes win on name collision
    seen: dict[str, NodeConfig] = {}
    for node in env_nodes:
        seen[node.name.lower()] = node
    for node in file_nodes:
        seen[node.name.lower()] = node

    return list(seen.values())


def get_node(name: str) -> NodeConfig | None:
    """Look up a node by name (case-insensitive)."""
    lower = name.lower()
    for node in parse_nodes():
        if node.name.lower() == lower:
            return node
    return None


def _upsert_file_node(node: NodeConfig) -> None:
    """Insert or replace a node in the persistent file by name."""
    file_nodes = _load_file_nodes()
    lower = node.name.lower()
    file_nodes = [n for n in file_nodes if n.name.lower() != lower]
    file_nodes.append(node)
    _save_file_nodes(file_nodes)


def add_local_node(name: str, path: str | None = None) -> NodeConfig:
    """Add a local vault node. Creates the vault directory if needed."""
    vault_path = Path(path) if path else DEFAULT_VAULTS_DIR / name
    vault_path.mkdir(parents=True, exist_ok=True)
    node = NodeConfig(name=name, type="local", path=str(vault_path))
    _upsert_file_node(node)
    return node


def add_remote_node(name: str, host: str, api_key: str, port: int = 27124) -> NodeConfig:
    """Add a remote Obsidian REST API node."""
    node = NodeConfig(name=name, type="remote", host=host, api_key=api_key, port=port)
    _upsert_file_node(node)
    return node


def remove_node(name: str) -> bool:
    """Remove a node from the persistent config file. Returns True if found."""
    file_nodes = _load_file_nodes()
    lower = name.lower()
    filtered = [n for n in file_nodes if n.name.lower() != lower]
    if len(filtered) == len(file_nodes):
        return False
    _save_file_nodes(filtered)
    return True
