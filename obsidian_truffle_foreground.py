"""Foreground app entrypoint — MCP tool server for Obsidian vaults."""

from __future__ import annotations

import atexit
import asyncio
import logging
from typing import Any

import httpx
from app_runtime.mcp import create_mcp_server, run_mcp_server

from config import (
    NodeConfig, get_node, parse_nodes,
    add_local_node, add_remote_node, remove_node as config_remove_node,
)
from local_client import LocalVaultClient
from obsidian_client import ObsidianClient

logger = logging.getLogger("obsidian.foreground")
logger.setLevel(logging.INFO)

mcp = create_mcp_server("obsidian")

_clients: dict[str, ObsidianClient | LocalVaultClient] = {}


def _error(message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": "error", "message": message}
    payload.update(extra)
    return payload


def _success(message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": "success", "message": message}
    payload.update(extra)
    return payload


def _get_client(node: NodeConfig) -> ObsidianClient | LocalVaultClient:
    """Get or create a client for the given node."""
    if node.name not in _clients:
        if node.type == "local":
            _clients[node.name] = LocalVaultClient(node)
        else:
            _clients[node.name] = ObsidianClient(node)
    return _clients[node.name]


def _require_node(node_name: str) -> NodeConfig:
    """Look up a node by name or raise ValueError."""
    node = get_node(node_name)
    if node is None:
        names = [n.name for n in parse_nodes()]
        raise ValueError(
            f"Unknown node '{node_name}'. Available: {', '.join(names) or '(none)'}"
        )
    return node


@mcp.tool(
    "list_nodes",
    description=(
        "List all configured Obsidian vault nodes with reachability status. "
        "Parameters: none. "
        "Returns: JSON with nodes array (name, host, port, reachable)."
    ),
)
async def list_nodes() -> dict[str, Any]:
    try:
        nodes = parse_nodes()
        if not nodes:
            return _success("No Obsidian nodes configured", nodes=[])

        results = []
        for node in nodes:
            client = _get_client(node)
            reachable = await client.ping()
            info: dict[str, Any] = {
                "name": node.name,
                "type": node.type,
                "reachable": reachable,
            }
            if node.type == "local":
                info["path"] = node.path
            else:
                info["host"] = node.host
                info["port"] = node.port
            results.append(info)
        online = sum(1 for r in results if r["reachable"])
        return _success(
            f"{online}/{len(results)} nodes reachable",
            nodes=results,
            total=len(results),
            online=online,
        )
    except Exception as e:
        return _error(str(e))


@mcp.tool(
    "create_vault",
    description=(
        "Create a new local Obsidian vault on this device. "
        "Parameters: name (str, required — vault label like 'main' or 'work'), "
        "path (str, optional — custom directory path; defaults to /data/vaults/<name>). "
        "Returns: confirmation with vault path. The vault is immediately usable "
        "with read_note, write_note, search_vault, etc."
    ),
)
async def create_vault(
    name: str,
    path: str | None = None,
) -> dict[str, Any]:
    try:
        # Evict stale client if one exists
        if name in _clients:
            try:
                await _clients[name].close()
            except Exception:
                pass
            del _clients[name]
        node = add_local_node(name, path)
        client = _get_client(node)
        reachable = await client.ping()
        return _success(
            f"Vault '{name}' created at {node.path}",
            node={"name": name, "type": "local", "path": node.path, "reachable": reachable},
        )
    except Exception as e:
        return _error(str(e))


@mcp.tool(
    "add_remote_node",
    description=(
        "Register a remote Obsidian vault (on another computer running the "
        "Local REST API plugin over Tailscale). Persists across restarts. "
        "Parameters: name (str, required — friendly label like 'desktop'), "
        "host (str, required — Tailscale IP or hostname), "
        "api_key (str, required — Bearer token from the Obsidian Local REST API plugin), "
        "port (int, optional, default 27124). "
        "Returns: confirmation with node details and reachability."
    ),
)
async def add_remote_node_tool(
    name: str,
    host: str,
    api_key: str,
    port: int = 27124,
) -> dict[str, Any]:
    try:
        # Evict stale client if one exists
        if name in _clients:
            try:
                await _clients[name].close()
            except Exception:
                pass
            del _clients[name]
        node = add_remote_node(name, host, api_key, port)
        client = _get_client(node)
        reachable = await client.ping()
        return _success(
            f"Remote node '{name}' added ({host}:{port}, reachable={reachable})",
            node={"name": name, "type": "remote", "host": host, "port": port, "reachable": reachable},
        )
    except Exception as e:
        return _error(str(e))


@mcp.tool(
    "remove_node",
    description=(
        "Remove an Obsidian vault node from the persistent config. "
        "Parameters: name (str, required — the node name to remove). "
        "Returns: confirmation or error if node not found."
    ),
)
async def remove_node(name: str) -> dict[str, Any]:
    try:
        removed = config_remove_node(name)
        if not removed:
            return _error(f"Node '{name}' not found in persistent config")
        # Clean up client
        if name in _clients:
            try:
                await _clients[name].close()
            except Exception:
                pass
            del _clients[name]
        return _success(f"Node '{name}' removed")
    except Exception as e:
        return _error(str(e))


@mcp.tool(
    "list_vault_files",
    description=(
        "List files and directories in an Obsidian vault path. "
        "Parameters: node_name (str, required), directory (str, optional, default '/'). "
        "Returns: JSON with files array."
    ),
)
async def list_vault_files(
    node_name: str,
    directory: str = "/",
) -> dict[str, Any]:
    try:
        node = _require_node(node_name)
        client = _get_client(node)
        files = await client.list_files(directory)
        return _success(
            f"{len(files)} entries in {node_name}:{directory}",
            node=node_name,
            directory=directory,
            files=files,
        )
    except httpx.HTTPStatusError as e:
        return _error(f"Obsidian API error: {e.response.status_code}")
    except ValueError as e:
        return _error(str(e))
    except Exception as e:
        return _error(str(e))


@mcp.tool(
    "read_note",
    description=(
        "Read a note from an Obsidian vault with metadata (frontmatter, tags, stat). "
        "Parameters: node_name (str, required), file_path (str, required). "
        "Returns: JSON with content, frontmatter, tags, stat."
    ),
)
async def read_note(node_name: str, file_path: str) -> dict[str, Any]:
    try:
        node = _require_node(node_name)
        client = _get_client(node)
        data = await client.get_file(file_path)
        return _success(
            f"Read {file_path} from {node_name}",
            node=node_name,
            file_path=file_path,
            content=data.get("content", ""),
            frontmatter=data.get("frontmatter"),
            tags=data.get("tags"),
            stat=data.get("stat"),
        )
    except httpx.HTTPStatusError as e:
        return _error(f"Obsidian API error: {e.response.status_code}")
    except ValueError as e:
        return _error(str(e))
    except Exception as e:
        return _error(str(e))


@mcp.tool(
    "write_note",
    description=(
        "Create, overwrite, or append to a note in an Obsidian vault. "
        "Parameters: node_name (str, required), file_path (str, required), "
        "content (str, required), append (bool, optional, default false). "
        "Returns: confirmation of write."
    ),
)
async def write_note(
    node_name: str,
    file_path: str,
    content: str,
    append: bool = False,
) -> dict[str, Any]:
    try:
        node = _require_node(node_name)
        client = _get_client(node)
        if append:
            await client.append_file(file_path, content)
            action = "Appended to"
        else:
            await client.put_file(file_path, content)
            action = "Wrote"
        return _success(f"{action} {file_path} on {node_name}")
    except httpx.HTTPStatusError as e:
        return _error(f"Obsidian API error: {e.response.status_code}")
    except ValueError as e:
        return _error(str(e))
    except Exception as e:
        return _error(str(e))


@mcp.tool(
    "search_vault",
    description=(
        "Full-text search across an Obsidian vault. "
        "Parameters: node_name (str, required), query (str, required), "
        "context_length (int, optional, default 100). "
        "Returns: JSON with search results."
    ),
)
async def search_vault(
    node_name: str,
    query: str,
    context_length: int = 100,
) -> dict[str, Any]:
    try:
        node = _require_node(node_name)
        client = _get_client(node)
        results = await client.search(query, context_length=context_length)
        return _success(
            f"{len(results)} results for '{query}' in {node_name}",
            node=node_name,
            query=query,
            results=results,
        )
    except httpx.HTTPStatusError as e:
        return _error(f"Obsidian API error: {e.response.status_code}")
    except ValueError as e:
        return _error(str(e))
    except Exception as e:
        return _error(str(e))


@mcp.tool(
    "push_note",
    description=(
        "Copy a note from one Obsidian vault to another. "
        "Parameters: source_node (str, required), dest_node (str, required), "
        "file_path (str, required), dest_path (str, optional, defaults to file_path). "
        "Returns: confirmation of copy."
    ),
)
async def push_note(
    source_node: str,
    dest_node: str,
    file_path: str,
    dest_path: str | None = None,
) -> dict[str, Any]:
    try:
        src = _require_node(source_node)
        dst = _require_node(dest_node)
        src_client = _get_client(src)
        dst_client = _get_client(dst)

        content = await src_client.get_file_content(file_path)
        target_path = dest_path or file_path
        await dst_client.put_file(target_path, content)

        return _success(
            f"Copied {file_path} from {source_node} to {dest_node}:{target_path}"
        )
    except httpx.HTTPStatusError as e:
        return _error(f"Obsidian API error: {e.response.status_code}")
    except ValueError as e:
        return _error(str(e))
    except Exception as e:
        return _error(str(e))


@mcp.tool(
    "pull_note",
    description=(
        "Pull a note from a remote Obsidian vault to a local vault. "
        "Convenience alias for push_note with reversed source/dest semantics. "
        "Parameters: remote_node (str, required), local_node (str, required), "
        "file_path (str, required), local_path (str, optional, defaults to file_path). "
        "Returns: confirmation of copy."
    ),
)
async def pull_note(
    remote_node: str,
    local_node: str,
    file_path: str,
    local_path: str | None = None,
) -> dict[str, Any]:
    return await push_note(
        source_node=remote_node,
        dest_node=local_node,
        file_path=file_path,
        dest_path=local_path,
    )


@mcp.tool(
    "delete_note",
    description=(
        "Delete a note from an Obsidian vault. "
        "Parameters: node_name (str, required), file_path (str, required). "
        "Returns: confirmation of deletion."
    ),
)
async def delete_note(node_name: str, file_path: str) -> dict[str, Any]:
    try:
        node = _require_node(node_name)
        client = _get_client(node)
        await client.delete_file(file_path)
        return _success(f"Deleted {file_path} from {node_name}")
    except httpx.HTTPStatusError as e:
        return _error(f"Obsidian API error: {e.response.status_code}")
    except ValueError as e:
        return _error(str(e))
    except Exception as e:
        return _error(str(e))


@mcp.tool(
    "sync_note",
    description=(
        "Compare a note between two Obsidian vaults. Shows whether the file "
        "exists on both nodes and whether the content matches. "
        "Parameters: node_a (str, required), node_b (str, required), "
        "file_path (str, required). "
        "Returns: JSON with existence, content match, and metadata from both nodes."
    ),
)
async def sync_note(
    node_a: str,
    node_b: str,
    file_path: str,
) -> dict[str, Any]:
    try:
        na = _require_node(node_a)
        nb = _require_node(node_b)
        client_a = _get_client(na)
        client_b = _get_client(nb)

        data_a: dict[str, Any] | None = None
        data_b: dict[str, Any] | None = None
        error_a: str | None = None
        error_b: str | None = None

        try:
            data_a = await client_a.get_file(file_path)
        except httpx.HTTPStatusError as e:
            error_a = f"HTTP {e.response.status_code}"
        except Exception as e:
            error_a = str(e)

        try:
            data_b = await client_b.get_file(file_path)
        except httpx.HTTPStatusError as e:
            error_b = f"HTTP {e.response.status_code}"
        except Exception as e:
            error_b = str(e)

        exists_a = data_a is not None
        exists_b = data_b is not None

        content_match = None
        if exists_a and exists_b:
            content_match = data_a.get("content") == data_b.get("content")

        result: dict[str, Any] = {
            "file_path": file_path,
            "node_a": {
                "name": node_a,
                "exists": exists_a,
                "stat": data_a.get("stat") if data_a else None,
                "error": error_a,
            },
            "node_b": {
                "name": node_b,
                "exists": exists_b,
                "stat": data_b.get("stat") if data_b else None,
                "error": error_b,
            },
            "content_match": content_match,
        }

        if content_match is True:
            msg = f"{file_path}: identical on {node_a} and {node_b}"
        elif content_match is False:
            msg = f"{file_path}: differs between {node_a} and {node_b}"
        elif exists_a and not exists_b:
            msg = f"{file_path}: exists on {node_a} but not {node_b}"
        elif exists_b and not exists_a:
            msg = f"{file_path}: exists on {node_b} but not {node_a}"
        else:
            msg = f"{file_path}: not found on either node"

        return _success(msg, **result)
    except ValueError as e:
        return _error(str(e))
    except Exception as e:
        return _error(str(e))


def _cleanup() -> None:
    global _clients
    for client in _clients.values():
        try:
            asyncio.run(client.close())
        except Exception:
            pass
    _clients = {}


def main() -> None:
    atexit.register(_cleanup)
    run_mcp_server(mcp, logger)


if __name__ == "__main__":
    main()
