from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .railway_client import DEFAULT_RAILWAY_API_URL, RailwayClient, RailwayError


class ListDeploymentsRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    service_id: str = Field(..., min_length=1)
    limit: int = Field(default=10, ge=1, le=100)


class GetLogsRequest(BaseModel):
    deployment_id: str = Field(..., min_length=1)
    log_type: str = Field(default="deployment", pattern="^(deployment|build)$")
    limit: int = Field(default=100, ge=1, le=500)


class ToolCallRequest(BaseModel):
    name: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.railway_client = RailwayClient(
        token=os.getenv("RAILWAY_API_TOKEN", ""),
        api_url=os.getenv("RAILWAY_API_URL", DEFAULT_RAILWAY_API_URL),
    )
    yield


app = FastAPI(title="railway-mcp", version="0.1.0", lifespan=lifespan)


TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_deployments",
        "description": "List deployments for a Railway project service.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "service_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
            },
            "required": ["project_id", "service_id"],
        },
    },
    {
        "name": "get_logs",
        "description": "Fetch logs for a Railway deployment.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "deployment_id": {"type": "string"},
                "log_type": {"type": "string", "enum": ["deployment", "build"], "default": "deployment"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            },
            "required": ["deployment_id"],
        },
    },
]


@app.get("/")
async def root() -> dict[str, Any]:
    return {"name": "railway-mcp", "status": "ok"}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "railway_token_configured": bool(os.getenv("RAILWAY_API_TOKEN")),
        "railway_api_url": os.getenv("RAILWAY_API_URL", DEFAULT_RAILWAY_API_URL),
    }


@app.get("/mcp/tools")
async def list_tools() -> dict[str, Any]:
    return {"tools": TOOLS}


async def _get_client() -> RailwayClient:
    return app.state.railway_client


@app.post("/mcp/tools/call")
async def call_tool(payload: ToolCallRequest) -> dict[str, Any]:
    if payload.name == "list_deployments":
        request = ListDeploymentsRequest(**payload.arguments)
        return await list_deployments(request)
    if payload.name == "get_logs":
        request = GetLogsRequest(**payload.arguments)
        return await get_logs(request)
    raise HTTPException(status_code=404, detail=f"Unknown tool: {payload.name}")


@app.post("/tools/list-deployments")
async def list_deployments(request: ListDeploymentsRequest) -> dict[str, Any]:
    client = await _get_client()
    try:
        async with client as railway:
            deployments = await railway.list_deployments(
                project_id=request.project_id,
                service_id=request.service_id,
                limit=request.limit,
            )
        return {"deployments": deployments}
    except RailwayError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/tools/get-logs")
async def get_logs(request: GetLogsRequest) -> dict[str, Any]:
    client = await _get_client()
    try:
        async with client as railway:
            logs = await railway.get_logs(
                deployment_id=request.deployment_id,
                log_type=request.log_type,
                limit=request.limit,
            )
        return {"logs": logs}
    except RailwayError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
