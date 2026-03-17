"""Configuration for the Obsidian Truffle app."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("obsidian.config")

# JSON array of node configs:
# [{"name": "desktop", "host": "100.64.0.1", "port": 27124, "api_key": "..."}]
OBSIDIAN_NODES_RAW: str = os.getenv("OBSIDIAN_NODES", "[]")


@dataclass(frozen=True)
class NodeConfig:
    name: str
    host: str
    api_key: str
    port: int = 27124


def parse_nodes() -> list[NodeConfig]:
    """Parse OBSIDIAN_NODES env var into a list of NodeConfig objects."""
    try:
        raw = json.loads(OBSIDIAN_NODES_RAW)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse OBSIDIAN_NODES: %s", e)
        return []

    if not isinstance(raw, list):
        logger.error("OBSIDIAN_NODES must be a JSON array, got %s", type(raw).__name__)
        return []

    nodes: list[NodeConfig] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            logger.warning("Skipping OBSIDIAN_NODES[%d]: not a dict", i)
            continue
        try:
            nodes.append(NodeConfig(
                name=entry["name"],
                host=entry["host"],
                api_key=entry["api_key"],
                port=entry.get("port", 27124),
            ))
        except KeyError as e:
            logger.warning("Skipping OBSIDIAN_NODES[%d]: missing key %s", i, e)
    return nodes


def get_node(name: str) -> NodeConfig | None:
    """Look up a node by name (case-insensitive)."""
    lower = name.lower()
    for node in parse_nodes():
        if node.name.lower() == lower:
            return node
    return None
