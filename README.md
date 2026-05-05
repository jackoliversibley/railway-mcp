# railway-mcp

A small FastAPI scaffold for Railway operations. It provides two tools:

- list_deployments
- get_logs

The app is designed to run on Railway and uses Railway's GraphQL API through a lightweight async client. If you want to wire in a specific Railway SDK later, the client layer is isolated in app/railway_client.py.

## Environment variables

- RAILWAY_API_TOKEN - required for Railway API access
- RAILWAY_API_URL - optional, defaults to https://backboard.railway.com/graphql/v2

## Local run

    pip install -r requirements.txt
    uvicorn app.main:app --host 0.0.0.0 --port 8080

## Endpoints

- GET /health
- GET /mcp/tools
- POST /mcp/tools/call
- POST /tools/list-deployments
- POST /tools/get-logs

<!-- redeploy trigger -->
<!-- east region redeploy trigger -->
