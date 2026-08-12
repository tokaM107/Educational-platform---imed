from fastapi import FastAPI
from routers import auth_api, content, agent_api

app = FastAPI(title="educational AI Platform")


@app.get("/health")
def health_check():
    return {"status": "ok"}

app.include_router(auth_api.router)
app.include_router(content.router)
app.include_router(agent_api.router)