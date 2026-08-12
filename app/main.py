from fastapi import FastAPI

app = FastAPI(title="educational AI Platform")


@app.get("/health")
def health_check():
    return {"status": "ok"}