from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from authlib.common.security import generate_token
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from mcp.server.fastmcp import FastMCP

from app.railway_client import RailwayClient, RailwayError

app = FastAPI()
mcp = FastMCP("railway-mcp")

OPEN_PATHS = ["/", "/health", "/openapi.json", "/docs", "/register", "/authorize", "/token"]
OPEN_PREFIXES = ["/.well-known/"]

ACCESS_TOKENS: dict[str, dict[str, Any]] = {}
REFRESH_TOKENS: dict[str, dict[str, Any]] = {}
AUTHORIZATION_CODES: dict[str, dict[str, Any]] = {}
REGISTERED_CLIENTS: dict[str, dict[str, Any]] = {}

DEFAULT_SCOPE = "deployments logs"
DEFAULT_TOKEN_TTL = 3600
DEFAULT_REFRESH_TTL = 60 * 60 * 24 * 30
DEFAULT_CODE_TTL = 600


@dataclass
class OAuthClient:
    client_id: str
    client_secret: str
    redirect_uris: list[str] = field(default_factory=list)
    grant_types: list[str] = field(default_factory=lambda: ["authorization_code", "refresh_token"])
    response_types: list[str] = field(default_factory=lambda: ["code"])
    token_endpoint_auth_method: str = "client_secret_post"
    scope: str = DEFAULT_SCOPE

    def check_client_secret(self, client_secret: str) -> bool:
        return hmac.compare_digest(self.client_secret, client_secret)

    def check_redirect_uri(self, redirect_uri: str) -> bool:
        return redirect_uri in self.redirect_uris

    def check_grant_type(self, grant_type: str) -> bool:
        return grant_type in self.grant_types

    def check_response_type(self, response_type: str) -> bool:
        return response_type in self.response_types


@dataclass
class OAuthCode:
    code: str
    client_id: str
    redirect_uri: str
    scope: str
    code_challenge: str | None
    code_challenge_method: str | None
    expires_at: float
    subject: str

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at


@dataclass
class OAuthToken:
    access_token: str
    refresh_token: str
    client_id: str
    scope: str
    expires_at: float
    subject: str

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at


def base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def is_admin_authorized(request: Request) -> bool:
    expected_token = os.getenv("MCPAUTHTOKEN", "")
    if not expected_token:
        return True
    auth_header = request.headers.get("Authorization", "")
    return auth_header == f"Bearer {expected_token}"


def require_admin_authorization(request: Request) -> JSONResponse | None:
    if is_admin_authorized(request):
        return None
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Unauthorized"},
        headers={"WWW-Authenticate": 'Bearer realm="mcp-admin"'},
    )


def normalize_scope(scope: str | None) -> str:
    if not scope:
        return DEFAULT_SCOPE
    parts = [part for part in scope.split() if part]
    return " ".join(dict.fromkeys(parts)) or DEFAULT_SCOPE


def parse_basic_auth(request: Request) -> tuple[str | None, str | None]:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Basic "):
        return None, None
    try:
        decoded = base64.b64decode(authorization.removeprefix("Basic ").strip()).decode("utf-8")
        client_id, client_secret = decoded.split(":", 1)
        return client_id, client_secret
    except Exception:
        return None, None


def get_client(client_id: str | None) -> OAuthClient | None:
    if not client_id:
        return None
    raw = REGISTERED_CLIENTS.get(client_id)
    if raw is None:
        return None
    return OAuthClient(**raw)


def store_client(client: OAuthClient) -> None:
    REGISTERED_CLIENTS[client.client_id] = {
        "client_id": client.client_id,
        "client_secret": client.client_secret,
        "redirect_uris": client.redirect_uris,
        "grant_types": client.grant_types,
        "response_types": client.response_types,
        "token_endpoint_auth_method": client.token_endpoint_auth_method,
        "scope": client.scope,
    }


def create_client_registration(payload: dict[str, Any], origin: str) -> dict[str, Any]:
    redirect_uris = payload.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        raise ValueError("redirect_uris must be a non-empty list")
    client = OAuthClient(
        client_id=generate_token(32),
        client_secret=generate_token(48),
        redirect_uris=[str(uri) for uri in redirect_uris],
        grant_types=[str(item) for item in payload.get("grant_types", ["authorization_code", "refresh_token"])],
        response_types=[str(item) for item in payload.get("response_types", ["code"])],
        token_endpoint_auth_method=str(payload.get("token_endpoint_auth_method", "client_secret_post")),
        scope=normalize_scope(str(payload.get("scope", DEFAULT_SCOPE))),
    )
    store_client(client)
    return {
        "client_id": client.client_id,
        "client_secret": client.client_secret,
        "client_name": payload.get("client_name", "railway-mcp"),
        "grant_types": client.grant_types,
        "response_types": client.response_types,
        "redirect_uris": client.redirect_uris,
        "scope": client.scope,
        "token_endpoint_auth_method": client.token_endpoint_auth_method,
        "registration_access_token": generate_token(40),
        "registration_client_uri": f"{origin}/register?client_id={client.client_id}",
    }


def create_authorization_code(client_id: str, redirect_uri: str, scope: str, code_challenge: str | None, code_challenge_method: str | None, subject: str) -> OAuthCode:
    code = generate_token(42)
    authorization_code = OAuthCode(
        code=code,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        expires_at=time.time() + DEFAULT_CODE_TTL,
        subject=subject,
    )
    AUTHORIZATION_CODES[code] = {
        "code": authorization_code.code,
        "client_id": authorization_code.client_id,
        "redirect_uri": authorization_code.redirect_uri,
        "scope": authorization_code.scope,
        "code_challenge": authorization_code.code_challenge,
        "code_challenge_method": authorization_code.code_challenge_method,
        "expires_at": authorization_code.expires_at,
        "subject": authorization_code.subject,
    }
    return authorization_code


def verify_code_verifier(code_challenge: str | None, code_challenge_method: str | None, code_verifier: str | None) -> bool:
    if not code_challenge:
        return True
    if not code_verifier:
        return False
    method = (code_challenge_method or "plain").lower()
    if method == "plain":
        return hmac.compare_digest(code_challenge, code_verifier)
    if method != "s256":
        return False
    verifier_digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    computed = base64.urlsafe_b64encode(verifier_digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(code_challenge, computed)


def issue_token(client_id: str, scope: str, subject: str, refresh_token: str | None = None) -> OAuthToken:
    access_token = generate_token(48)
    token = OAuthToken(
        access_token=access_token,
        refresh_token=refresh_token or generate_token(48),
        client_id=client_id,
        scope=scope,
        expires_at=time.time() + DEFAULT_TOKEN_TTL,
        subject=subject,
    )
    ACCESS_TOKENS[token.access_token] = {
        "access_token": token.access_token,
        "refresh_token": token.refresh_token,
        "client_id": token.client_id,
        "scope": token.scope,
        "expires_at": token.expires_at,
        "subject": token.subject,
    }
    REFRESH_TOKENS[token.refresh_token] = {
        "access_token": token.access_token,
        "client_id": token.client_id,
        "scope": token.scope,
        "expires_at": time.time() + DEFAULT_REFRESH_TTL,
        "subject": token.subject,
        "revoked": False,
    }
    return token


def validate_bearer_token(token: str) -> dict[str, Any] | None:
    data = ACCESS_TOKENS.get(token)
    if data is None:
        return None
    if time.time() >= float(data["expires_at"]):
        return None
    return data


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if any(path == open_path for open_path in OPEN_PATHS) or any(
        path.startswith(prefix) for prefix in OPEN_PREFIXES
    ):
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Unauthorized"},
        )

    token = auth_header.removeprefix("Bearer ").strip()
    if validate_bearer_token(token) is None:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Unauthorized"},
        )
    return await call_next(request)


@app.get("/")
async def root():
    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}


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
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic", "none"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "scopes_supported": ["deployments", "logs"],
    }


@app.api_route("/register", methods=["GET", "POST"])
async def register(request: Request):
    origin = base_url(request)
    admin_error = require_admin_authorization(request)
    if admin_error is not None:
        return admin_error

    if request.method == "GET":
        client_id = request.query_params.get("client_id")
        if not client_id:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Missing client_id"},
            )
        client = get_client(client_id)
        if client is None:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": "Client not found"},
            )
        return {
            "client_id": client.client_id,
            "client_name": "railway-mcp",
            "grant_types": client.grant_types,
            "response_types": client.response_types,
            "redirect_uris": client.redirect_uris,
            "scope": client.scope,
            "token_endpoint_auth_method": client.token_endpoint_auth_method,
            "registration_client_uri": f"{origin}/register?client_id={client.client_id}",
        }

    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return create_client_registration(payload, origin)
    except ValueError as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )


@app.get("/authorize")
async def authorize(request: Request):
    admin_error = require_admin_authorization(request)
    if admin_error is not None:
        return admin_error

    client_id = request.query_params.get("client_id")
    redirect_uri = request.query_params.get("redirect_uri") or request.query_params.get("callback")
    response_type = request.query_params.get("response_type", "code")
    scope = normalize_scope(request.query_params.get("scope"))
    state = request.query_params.get("state")
    code_challenge = request.query_params.get("code_challenge")
    code_challenge_method = request.query_params.get("code_challenge_method")

    client = get_client(client_id)
    if client is None:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Unknown client_id"},
        )
    if response_type != "code":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Unsupported response_type"},
        )
    if redirect_uri is None or not client.check_redirect_uri(redirect_uri):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Invalid redirect_uri"},
        )

    authorization_code = create_authorization_code(
        client_id=client.client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        subject="railway-mcp-user",
    )
    params = {"code": authorization_code.code}
    if state:
        params["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        url=f"{redirect_uri}{separator}{urlencode(params)}",
        status_code=status.HTTP_302_FOUND,
    )


@app.post("/token")
async def token(request: Request):
    form = await request.form()
    grant_type = str(form.get("grant_type", ""))
    code_verifier = form.get("code_verifier")
    client_id, client_secret = parse_basic_auth(request)
    if client_id is None:
        client_id = form.get("client_id")
    if client_secret is None:
        client_secret = form.get("client_secret")

    client = get_client(client_id)
    if client is None:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "invalid_client", "error_description": "Unknown client"},
        )
    if client.token_endpoint_auth_method != "none" and not client.check_client_secret(str(client_secret or "")):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "invalid_client", "error_description": "Invalid client credentials"},
        )

    if grant_type == "authorization_code":
        code = str(form.get("code", ""))
        redirect_uri = str(form.get("redirect_uri", ""))
        stored_code = AUTHORIZATION_CODES.get(code)
        if stored_code is None or stored_code["client_id"] != client.client_id:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "invalid_grant", "error_description": "Invalid authorization code"},
            )
        if stored_code["redirect_uri"] != redirect_uri:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "invalid_grant", "error_description": "redirect_uri mismatch"},
            )
        if time.time() >= float(stored_code["expires_at"]):
            AUTHORIZATION_CODES.pop(code, None)
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "invalid_grant", "error_description": "Authorization code expired"},
            )
        if not verify_code_verifier(
            stored_code.get("code_challenge"),
            stored_code.get("code_challenge_method"),
            str(code_verifier) if code_verifier is not None else None,
        ):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "invalid_grant", "error_description": "Invalid code_verifier"},
            )

        AUTHORIZATION_CODES.pop(code, None)
        token_value = issue_token(
            client_id=client.client_id,
            scope=stored_code["scope"],
            subject=stored_code["subject"],
        )
        return {
            "access_token": token_value.access_token,
            "token_type": "bearer",
            "expires_in": DEFAULT_TOKEN_TTL,
            "refresh_token": token_value.refresh_token,
            "scope": token_value.scope,
        }

    if grant_type == "refresh_token":
        refresh_token = str(form.get("refresh_token", ""))
        stored_refresh = REFRESH_TOKENS.get(refresh_token)
        if stored_refresh is None or stored_refresh.get("revoked"):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "invalid_grant", "error_description": "Invalid refresh token"},
            )
        if stored_refresh["client_id"] != client.client_id:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "invalid_grant", "error_description": "refresh_token client mismatch"},
            )
        if time.time() >= float(stored_refresh["expires_at"]):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "invalid_grant", "error_description": "Refresh token expired"},
            )

        stored_refresh["revoked"] = True
        new_token = issue_token(
            client_id=client.client_id,
            scope=stored_refresh["scope"],
            subject=stored_refresh["subject"],
        )
        return {
            "access_token": new_token.access_token,
            "token_type": "bearer",
            "expires_in": DEFAULT_TOKEN_TTL,
            "refresh_token": new_token.refresh_token,
            "scope": new_token.scope,
        }

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": "unsupported_grant_type", "error_description": "Unsupported grant_type"},
    )


@mcp.tool()
async def list_deployments(project_id: str, service_id: str, limit: int = 10) -> list[dict[str, Any]]:
    async with RailwayClient() as client:
        return await client.list_deployments(project_id=project_id, service_id=service_id, limit=limit)


@mcp.tool()
async def get_logs(deployment_id: str, log_type: str = "deployment", limit: int = 100) -> list[dict[str, Any]]:
    async with RailwayClient() as client:
        return await client.get_logs(deployment_id=deployment_id, log_type=log_type, limit=limit)


app.mount("/mcp", mcp.sse_app())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
