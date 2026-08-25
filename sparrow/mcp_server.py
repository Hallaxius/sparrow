from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sparrow.routing.engine import RoutingEngine
    from sparrow.routing.health import RouteHealthTracker
    from sparrow.stats import StatsTracker


class MCPServer:
    def __init__(
        self,
        *,
        stats_tracker: StatsTracker | None = None,
        health_tracker: RouteHealthTracker | None = None,
        routing_engine: RoutingEngine | None = None,
    ) -> None:
        self._stats = stats_tracker
        self._health = health_tracker
        self._routing = routing_engine
        self._server_info = {"name": "sparrow-mcp", "version": "0.1.0"}

    def _handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        if method == "initialize":
            return self._respond(req_id, self._initialize(params))
        if method == "tools/list":
            return self._respond(req_id, self._list_tools())
        if method == "tools/call":
            return self._respond(req_id, self._call_tool(params))
        if method == "notifications/initialized":
            return {}
        return self._error(req_id, -32601, f"Method not found: {method}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": self._server_info,
        }

    def _list_tools(self) -> dict[str, Any]:
        tools = [
            {
                "name": "sparrow_status",
                "description": "Get sparrow proxy status including uptime, total requests, and provider summary",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "sparrow_health",
                "description": "Get circuit breaker health status for all routes",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "provider_id": {
                            "type": "string",
                            "description": "Filter by provider ID",
                        }
                    },
                },
            },
            {
                "name": "sparrow_routes",
                "description": "List all registered routes with their configuration",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "model": {
                            "type": "string",
                            "description": "Filter by model name",
                        }
                    },
                },
            },
            {
                "name": "sparrow_route",
                "description": "Get the best route for a given model and optional task description",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "model": {
                            "type": "string",
                            "description": "Model name or alias",
                        },
                        "task": {
                            "type": "string",
                            "description": "Task description for task-aware routing (optional)",
                        },
                        "max_tokens": {
                            "type": "integer",
                            "description": "Required context window size (optional)",
                        },
                    },
                    "required": ["model"],
                },
            },
            {
                "name": "sparrow_config",
                "description": "Get current sparrow configuration summary",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "sparrow_context_limits",
                "description": "Get learned context window limits for providers",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "provider_id": {
                            "type": "string",
                            "description": "Filter by provider ID",
                        }
                    },
                },
            },
        ]
        return {"tools": tools}

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        try:
            if tool_name == "sparrow_status":
                result = self._tool_status()
            elif tool_name == "sparrow_health":
                result = self._tool_health(arguments.get("provider_id"))
            elif tool_name == "sparrow_routes":
                result = self._tool_routes(arguments.get("model"))
            elif tool_name == "sparrow_route":
                result = self._tool_route(
                    arguments["model"],
                    arguments.get("task"),
                    arguments.get("max_tokens"),
                )
            elif tool_name == "sparrow_config":
                result = self._tool_config()
            elif tool_name == "sparrow_context_limits":
                result = self._tool_context_limits(arguments.get("provider_id"))
            else:
                return {
                    "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                    "isError": True,
                }
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": f"Error: {exc}"}],
                "isError": True,
            }

    def _tool_status(self) -> dict[str, Any]:
        if self._stats is None:
            return {"error": "Stats tracker not available"}
        return self._stats.get_summary()

    def _tool_health(self, provider_id: str | None) -> dict[str, Any]:
        if self._health is None:
            return {"error": "Health tracker not available"}
        breakers = self._health._breakers
        result: dict[str, Any] = {}
        for key, breaker in breakers.items():
            if provider_id and not key.startswith(provider_id):
                continue
            result[key] = {
                "state": breaker._state,
                "failures": breaker._failures,
                "threshold": breaker._failure_threshold,
            }
        return result

    def _tool_routes(self, model: str | None) -> dict[str, Any]:
        if self._routing is None:
            return {"error": "Routing engine not available"}
        routes = self._routing._routes
        result = []
        for route in routes:
            if model and model not in route.model_id and model != route.provider_id:
                continue
            result.append(
                {
                    "provider_id": route.provider_id,
                    "model_id": route.model_id,
                    "quality": route.quality,
                    "context_window": route.context_window,
                    "avg_latency_ms": route.avg_latency_ms,
                }
            )
        return {"routes": result, "total": len(result)}

    def _tool_route(
        self,
        model: str,
        task: str | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        if self._routing is None:
            return {"error": "Routing engine not available"}
        candidates = self._routing.get_candidates(model, max_tokens=max_tokens)
        if not candidates:
            return {"error": f"No route found for model: {model}"}
        ordered = self._routing.ordered_candidates(
            model, max_tokens=max_tokens, mode=self._routing._mode
        )
        best = ordered[0] if ordered else candidates[0]
        return {
            "model": model,
            "best_route": {
                "provider_id": best.provider_id,
                "model_id": best.model_id,
                "quality": best.quality,
            },
            "total_candidates": len(candidates),
            "mode": self._routing._mode.value,
        }

    def _tool_config(self) -> dict[str, Any]:
        if self._routing is None:
            return {"error": "Routing engine not available"}
        return {
            "mode": self._routing._mode.value,
            "routes_count": self._routing.route_count,
        }

    def _tool_context_limits(self, provider_id: str | None) -> dict[str, Any]:
        if self._routing is None or not hasattr(self._routing, "_context_learner"):
            return {"error": "Context learner not available"}
        learner = self._routing._context_learner
        if learner is None:
            return {"error": "Context learner not initialized"}
        result: dict[str, Any] = {}
        for key, limit in learner._limits.items():
            if provider_id and not key.startswith(provider_id):
                continue
            result[key] = {
                "tokens": limit.tokens,
                "source": limit.source,
                "expires_at": limit.expires_at,
            }
        return result

    def _respond(self, req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _error(self, req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }

    def run_stdio(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue
            response = self._handle_request(request)
            if response:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
