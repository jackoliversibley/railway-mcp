from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Any
import httpx

DEFAULTTOKEN = os.getenv("RAILWAYTOKEN", "")
APIURL = "https://backboard.railway.com/graphql/v2"

class RailwayError(RuntimeError):
    pass

@dataclass
class RailwayClient:
    token: str = DEFAULTTOKEN
    apiurl: str = APIURL
    _client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "RailwayClient":
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        return self

    async def __aexit__(self, exctype, excval, exctb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Railway client not initialized")
        return self._client

    async def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.token:
            raise RailwayError("RAILWAYTOKEN is not set")
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        response = await self.client.post(self.apiurl, json=payload)
        if response.status_code == 401:
            raise RailwayError("Invalid Railway API token")
        if response.status_code != 200:
            raise RailwayError(f"HTTP {response.status_code}: {response.text}")
        result = response.json()
        if "errors" in result:
            raise RailwayError(result["errors"][0].get("message", "GraphQL error"))
        return result.get("data", {})

    async def verifytoken(self) -> dict[str, Any]:
        data = await self.execute("query { me { id name email } }")
        user = data.get("me")
        if not user:
            raise RailwayError("Verification failed")
        return user

    async def listdeployments(self, projectid: str, serviceid: str, limit: int = 10) -> list[dict[str, Any]]:
        query = """
        query Deployments($projectId: String!, $serviceId: String!, $first: Int!) {
            deployments(input: { projectId: $projectId, serviceId: $serviceId }, first: $first) {
                edges { node { id status createdAt updatedAt } }
            }
        }
        """
        data = await self.execute(query, {"projectId": projectid, "serviceId": serviceid, "first": limit})
        edges = data.get("deployments", {}).get("edges", [])
        return [edge["node"] for edge in edges]

    async def getlogs(self, deploymentid: str, logtype: str = "deployment", limit: int = 100) -> list[dict[str, Any]]:
        field = "deploymentLogs" if logtype == "deployment" else "buildLogs"
        query = f"query Logs($id: String!) {{ {field}(deploymentId: $id) {{ timestamp message severity }} }}"
        data = await self.execute(query, {"id": deploymentid})
        logs = data.get(field, [])
        return logs[-limit:] if limit else logs
