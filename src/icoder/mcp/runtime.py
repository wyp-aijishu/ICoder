"""Thread-backed lifecycle manager for stdio MCP sessions."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any, Mapping

from icoder.mcp.config import McpServerConfig


class McpRuntimeError(RuntimeError):
    """Raised when a configured MCP server cannot serve a request."""


@dataclass(slots=True)
class _Connection:
    config: McpServerConfig
    session: Any
    stack: AsyncExitStack
    tools: tuple[Any, ...]


class McpRuntime:
    """Own stdio MCP sessions in one background asyncio event loop."""

    def __init__(self, workspace: str | Path, servers: tuple[McpServerConfig, ...]) -> None:
        self._workspace = Path(workspace).expanduser().resolve()
        self._servers = servers
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: Thread | None = None
        self._ready = Event()
        self._lock = RLock()
        self._connections: dict[str, _Connection] = {}
        self._failures: dict[str, str] = {}

    @property
    def failures(self) -> Mapping[str, str]:
        return dict(self._failures)

    def status(self) -> tuple[tuple[str, bool, int, str | None], ...]:
        """Return configured server name, connection state, tool count and failure."""
        result: list[tuple[str, bool, int, str | None]] = []
        for server in self._servers:
            connection = self._connections.get(server.name)
            result.append(
                (
                    server.name,
                    connection is not None,
                    len(connection.tools) if connection is not None else 0,
                    self._failures.get(server.name),
                )
            )
        return tuple(result)

    def tools_for(self, server_name: str) -> tuple[Any, ...]:
        connection = self._connections.get(server_name)
        return connection.tools if connection is not None else ()

    def start(self) -> None:
        """Start the event loop and connect each configured server independently."""
        if not self._servers:
            return
        with self._lock:
            if self._loop is not None:
                return
            self._thread = Thread(target=self._run_loop, name="icoder-mcp", daemon=True)
            self._thread.start()
        if not self._ready.wait(timeout=max(server.startup_timeout_seconds for server in self._servers)):
            raise McpRuntimeError("MCP runtime event loop did not start")
        for server in self._servers:
            try:
                self._submit(self._connect(server), server.startup_timeout_seconds)
            except Exception as exc:
                self._failures[server.name] = _error_message(exc)

    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> Any:
        """Synchronously invoke a tool on an already initialized MCP session."""
        connection = self._connections.get(server_name)
        if connection is None:
            detail = self._failures.get(server_name, "server is not connected")
            raise McpRuntimeError(f"MCP server '{server_name}' is unavailable: {detail}")
        try:
            return self._submit(
                connection.session.call_tool(tool_name, arguments=dict(arguments)),
                connection.config.tool_timeout_seconds,
            )
        except Exception as exc:
            raise McpRuntimeError(f"MCP tool '{server_name}/{tool_name}' failed: {_error_message(exc)}") from exc

    def close(self) -> None:
        """Close sessions and stop the owned event loop; safe to call repeatedly."""
        with self._lock:
            loop = self._loop
            thread = self._thread
            if loop is None:
                return
        try:
            self._submit(self._close_connections(), 10.0)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=10.0)
        with self._lock:
            self._loop = None
            self._thread = None
            self._connections.clear()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._loop = loop
        self._ready.set()
        loop.run_forever()
        loop.close()

    def _submit(self, coroutine: Any, timeout: float) -> Any:
        loop = self._loop
        if loop is None:
            raise McpRuntimeError("MCP runtime is not running")
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        return future.result(timeout=timeout)

    async def _connect(self, config: McpServerConfig) -> None:
        from mcp import ClientSession, StdioServerParameters, types
        from mcp.client.stdio import stdio_client
        from mcp.shared.context import RequestContext

        async def list_roots(_context: RequestContext[ClientSession, None]) -> types.ListRootsResult:
            return types.ListRootsResult(roots=[types.Root(uri=self._workspace.as_uri(), name=self._workspace.name)])

        stack = AsyncExitStack()
        try:
            streams = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=config.command,
                        args=list(config.args),
                        env=dict(config.env or {}),
                        cwd=self._workspace,
                    )
                )
            )
            read_stream, write_stream = streams
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream, list_roots_callback=list_roots)
            )
            await session.initialize()
            response = await session.list_tools()
            self._connections[config.name] = _Connection(config, session, stack, tuple(response.tools))
        except Exception:
            await stack.aclose()
            raise

    async def _close_connections(self) -> None:
        connections = tuple(self._connections.values())
        self._connections.clear()
        for connection in connections:
            await connection.stack.aclose()


def _error_message(exc: Exception) -> str:
    text = str(exc).strip()
    return text or type(exc).__name__