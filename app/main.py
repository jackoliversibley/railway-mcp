import os
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from mcp.server.fastmcp import FastMCP

app = FastAPI()
mcp = FastMCP("railway-mcp")

OPENPATHS = ["/", "/health", "/openapi.json", "/docs", "/register", "/authorize", "/token"]
OPENPREFIXES = ["/.well-known/", "/mcp/"]

def baseurl(request: Request) -> str:
    return str(request.base_url).rstrip("/")

@app.middleware("http")
async def authmiddleware(request: Request, call_next):
    path = request.url.path
    if any(path == p for p in OPENPATHS) or any(path.startswith(p) for p in OPENPREFIXES):
        return await call_next(request)
    
    authheader = request.headers.get("Authorization")
    expectedtoken = os.getenv("MCPAUTHTOKEN", "")
    
    if not authheader or authheader != f"Bearer {expectedtoken}":
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Unauthorized"}
        )
    return await call_next(request)

@app.get("/")
async def root():
    return {"status": "ok"}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.api_route("/register", methods=["GET", "POST"])
async def register(request: Request):
    origin = baseurl(request)
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
async def oauthprotectedresource(request: Request):
    origin = baseurl(request)
    return {
        "resource": origin,
        "authorization_servers": [origin],
        "bearer_methods_supported": ["header"],
    }

@app.get("/.well-known/oauth-authorization-server")
async def oauthauthorizationserver(request: Request):
    origin = baseurl(request)
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
    redirecturi = request.query_params.get("redirect_uri") or request.query_params.get("callback")
    state = request.query_params.get("state")
    if not redirecturi:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Missing redirect_uri"}
        )
    target = f"{redirecturi}{'&' if '?' in redirecturi else '?'}code=mock-auth-code"
    if state:
        target = f"{target}&state={state}"
    return RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)

@app.post("/token")
async def token():
    return {
        "access_token": os.getenv("MCPAUTHTOKEN", ""),
        "token_type": "bearer",
        "expires_in": 3600,
        "refresh_token": "mock-refresh-token",
    }

@mcp.tool()
async def listdeployments():
    return "tools coming soon"

app.mount("/mcp", mcp.sse_app())
