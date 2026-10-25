from fastapi import FastAPI

app = FastAPI()


@app.post("/api/preferences")
def save_preferences():
    return {"saved": True}


@app.post("/api/preferences/audit")
def write_preferences_audit():
    return {"recorded": True}
