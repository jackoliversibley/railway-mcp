import os
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from mcp.server.fastmcp import FastMCP

app = FastAPI()
mcp = FastMCP("railway-mcp")

OPEN_PATHS = {
    "/",
    "/health",
    "/openapi.json",
    "/docs",
    "/register",
    "/authorize",
    "/token",
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-authorization-server",
}


def base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in OPEN_PATHS:
        return await call_next(request)
    if path.startswith("/mcp") and request.method in {"GET", "HEAD", "OPTIONS"}:
        return await call_next(request)
    auth_header = request.headers.get("Authorization")
    expected_token = os.getenv("MCPAUTH_TOKEN", "")
    if not auth_header or auth_header != f"Bearer {expected_token}":
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Unauthorized"})
    return await call_next(request)


@app.get("/")
async def root():
    return {"status": "ok", "mcp": "/mcp"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.api_route("/register", methods=["GET", "POST"])
async def register(request: Request):
    origin = base_url(request)
    return {
        "client_id": "railway-mcp",
        "client_name": "railway-mcp",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
        "registration_access_token": "mock-registration-token",
        "registration_client_uri": f"{origin}/register",
    }


@app.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource(request: Request):
    origin = base_url(request)
    return {
        "resource": origin,
        "authorization_servers": [origin],
        "bearer_methods_supported": ["header"],
    }


@app.get("/.well-known/oauth-authorization-server")
async def oauth_authorization_server(request: Request):
    origin = base_url(request)
    return {
        "issuer": origin,
        "authorization_endpoint": f"{origin}/authorize",
        "token_endpoint": f"{origin}/token",
        "registration_endpoint": f"{origin}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
    }


@app.get("/authorize")
async def authorize(request: Request):
    origin = base_url(request)
    redirect_uri = request.query_params.get("redirect_uri")
    state = request.query_params.get("state")
    if redirect_uri:
        redirect_target = f"{redirect_uri}{'&' if '?' in redirect_uri else '?'}code=mock-auth-code"
        if state:
            redirect_target = f"{redirect_target}&state={state}"
        return RedirectResponse(url=redirect_target, status_code=status.HTTP_302_FOUND)
    return {
        "authorization_endpoint": f"{origin}/authorize",
        "detail": "Mock authorization endpoint",
        "code": "mock-auth-code",
    }


@app.post("/token")
async def token(request: Request):
    return {
        "access_token": "mock-access-token",
        "token_type": "bearer",
        "expires_in": 3600,
        "refresh_token": "mock-refresh-token",
    }


@mcp.tool()
async def list_deployments():
    return "tools coming soon"


app.mount("/mcp", mcp.sse_app())
