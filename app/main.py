from __future__ import annotations

import inspect
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP

from .railway_client import DEFAULT_RAILWAY_API_URL, RailwayClient

logger = logging.getLogger(__name__)

mcp = FastMCP("railway-mcp")


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _get_tool_names() -> list[str]:
    list_tools = getattr(mcp._mcp_server, "list_tools", None)
    if list_tools is None or not callable(list_tools):
        raise TypeError(f"list_tools is not callable: {type(list_tools)!r}")
    tools = await _maybe_await(list_tools())
    return [tool.name for tool in tools]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("starting railway-mcp on port %s", os.environ.get("PORT", "8080"))
    try:
        tool_names = await _get_tool_names()
        logger.info("registered MCP tools: %s", tool_names)
    except Exception:
        logger.exception("startup tool verification failed")
    yield


app = FastAPI(title="railway-mcp", version="0.1.0", lifespan=lifespan)


@app.get("/")
async def root() -> dict[str, str]:
    return {"name": "railway-mcp", "status": "ok"}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "railway_token_configured": bool(os.getenv("RAILWAY_TOKEN")),
        "railway_api_url": os.getenv("RAILWAY_API_URL", DEFAULT_RAILWAY_API_URL),
    }


@app.middleware("http")
async def verify_mcp_auth_token(request: Request, call_next):
    if request.url.path not in {"/", "/health"}:
        expected_token = os.getenv("MCP_AUTH_TOKEN", "")
        authorization = request.headers.get("authorization", "")
        if not expected_token:
            return JSONResponse(
                {"detail": "MCP_AUTH_TOKEN is not configured"},
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        if not authorization.lower().startswith("bearer "):
            return JSONResponse(
                {"detail": "Missing Bearer token"},
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Bearer"},
            )
        provided_token = authorization.split(" ", 1)[1].strip()
        if provided_token != expected_token:
            return JSONResponse(
                {"detail": "Invalid Bearer token"},
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Bearer"},
            )
    return await call_next(request)


@mcp.tool()
async def list_deployments(project_id: str, service_id: str, limit: int = 10) -> dict[str, Any]:
    async with RailwayClient(
        token=os.getenv("RAILWAY_TOKEN", ""),
        api_url=os.getenv("RAILWAY_API_URL", DEFAULT_RAILWAY_API_URL),
    ) as client:
        deployments = await client.list_deployments(project_id=project_id, service_id=service_id, limit=limit)
        return {"deployments": deployments}


@mcp.tool()
async def get_logs(
    deployment_id: str,
    log_type: Literal["deployment", "build"] = "deployment",
    limit: int = 100,
) -> dict[str, Any]:
    async with RailwayClient(
        token=os.getenv("RAILWAY_TOKEN", ""),
        api_url=os.getenv("RAILWAY_API_URL", DEFAULT_RAILWAY_API_URL),
    ) as client:
        logs = await client.get_logs(deployment_id=deployment_id, log_type=log_type, limit=limit)
        return {"logs": logs}


app.mount("/", mcp.sse_app())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="info",
    )
