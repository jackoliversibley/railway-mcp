import os
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP

app = FastAPI()
mcp = FastMCP("railway-mcp")

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.url.path in ["/", "/health", "/openapi.json", "/docs"]:
        return await call_next(request)
    auth_header = request.headers.get("Authorization")
    expected_token = os.getenv("MCP_AUTH_TOKEN", "")
    if not auth_header or auth_header != f"Bearer {expected_token}":
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)

@app.get("/health")
async def health():
    return {"status": "ok"}

@mcp.tool()
async def list_deployments():
    return "tools coming soon"

app.mount("/", mcp.sse_app())
