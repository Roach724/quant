"""Quant Admin Platform — FastAPI server."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_serializer
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional

from admin.models import init_db, get_session, Task

DB_SESSION_DEP = Depends(get_session)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Quant Admin", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / response schemas ────────────────────────────────────────────────

class TaskCreate(BaseModel):
    type: str = "shell"
    command: str


class TaskOut(BaseModel):
    id: int
    type: str
    status: str
    params: Optional[dict] = None
    result: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "started_at", "finished_at")
    def serialize_dt(self, dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat() if dt else None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/tasks")
def list_tasks(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None),
    db: Session = DB_SESSION_DEP,
):
    q = db.query(Task).order_by(Task.created_at.desc())
    if status:
        q = q.filter(Task.status == status)
    total = q.count()
    tasks = q.offset(offset).limit(limit).all()
    return {"total": total, "tasks": [TaskOut.model_validate(t) for t in tasks]}


@app.post("/api/tasks")
def create_task(body: TaskCreate, db: Session = DB_SESSION_DEP):
    task = Task(
        type=body.type,
        status="pending",
        params={"command": body.command},
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return TaskOut.model_validate(task)


@app.get("/api/admin/tasks/{task_id}")
def get_task(task_id: int, db: Session = DB_SESSION_DEP):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskOut.model_validate(task)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("admin.server:app", host="0.0.0.0", port=8092, reload=True)
