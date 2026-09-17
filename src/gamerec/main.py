from fastapi import FastAPI

app = FastAPI(title="GameRec", description="A recommendation system for games", version="0.1.0")

@app.get("/health")
def get_health():
    return {"status": "healthy"}
