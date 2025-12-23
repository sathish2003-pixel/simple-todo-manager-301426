import os
import sqlite3
from datetime import datetime, timezone
from typing import List

from fastapi import FastAPI, HTTPException, Path, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Database configuration
# PUBLIC_INTERFACE
def get_database_path() -> str:
    """Return the path to the SQLite database used by the shared database container.
    This uses an absolute path to the database container's SQLite file.
    Environment variables can optionally override this via SQLITE_DB.
    """
    # Allow override by env var if present
    env_db = os.getenv("SQLITE_DB")
    if env_db and env_db.strip():
        return env_db
    
    # Default path: calculate absolute path to the shared database
    # From todo_backend directory, go up two levels then into database container
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Navigate from src/api/ up to workspace root
    workspace_root = os.path.abspath(os.path.join(current_dir, "..", "..", "..", ".."))
    default_path = os.path.join(workspace_root, "simple-todo-manager-301425", "database", "myapp.db")
    return default_path


def get_connection() -> sqlite3.Connection:
    """Create a SQLite connection with row_factory set to sqlite3.Row to ease dict conversion."""
    db_path = get_database_path()
    # Ensure the directory exists (best-effort)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize the tasks table if it does not exist yet."""
    conn = get_connection()
    try:
        # Safe create table if not exists (matches expected schema)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def utc_now_iso() -> str:
    """Get current UTC time in ISO8601 string format."""
    return datetime.now(timezone.utc).isoformat()


# Pydantic models
class TaskBase(BaseModel):
    title: str = Field(..., description="The title of the task")


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    title: str = Field(..., description="The new title of the task")


class Task(BaseModel):
    id: int = Field(..., description="Unique identifier of the task")
    title: str = Field(..., description="The title of the task")
    completed: bool = Field(..., description="Completion status of the task")
    created_at: str = Field(..., description="ISO8601 creation timestamp")
    updated_at: str = Field(..., description="ISO8601 last update timestamp")


# FastAPI app with metadata and tags
app = FastAPI(
    title="ToDo Backend API",
    description="REST API for managing ToDo tasks with SQLite persistence.",
    version="1.0.0",
    openapi_tags=[
        {
            "name": "Tasks",
            "description": "Operations for listing, creating, updating, completing, and deleting tasks.",
        },
        {
            "name": "System",
            "description": "System and health endpoints.",
        },
    ],
)

# CORS: allow frontend origin as requested
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Ensure DB exists on startup
@app.on_event("startup")
def on_startup() -> None:
    init_db()


# Helpers
def row_to_task(row: sqlite3.Row) -> Task:
    """Convert a sqlite row to a Task model."""
    return Task(
        id=row["id"],
        title=row["title"],
        completed=bool(row["completed"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# Routes

# PUBLIC_INTERFACE
@app.get("/", tags=["System"], summary="Health Check", description="Simple health check endpoint.")
def health_check():
    """Health check endpoint to verify the service is running."""
    return {"message": "Healthy"}


# PUBLIC_INTERFACE
@app.get(
    "/tasks",
    response_model=List[Task],
    tags=["Tasks"],
    summary="List tasks",
    description="Retrieve all tasks ordered by created_at descending.",
)
def list_tasks() -> List[Task]:
    """List all tasks."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id, title, completed, created_at, updated_at FROM tasks ORDER BY datetime(created_at) DESC"
        )
        rows = cur.fetchall()
        return [row_to_task(r) for r in rows]
    finally:
        conn.close()


# PUBLIC_INTERFACE
@app.post(
    "/tasks",
    response_model=Task,
    status_code=201,
    tags=["Tasks"],
    summary="Create task",
    description="Create a new task with the given title.",
)
def create_task(payload: TaskCreate) -> Task:
    """Create a new task with a title."""
    now = utc_now_iso()
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO tasks (title, completed, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (payload.title, 0, now, now),
        )
        task_id = cur.lastrowid
        conn.commit()
        cur = conn.execute(
            "SELECT id, title, completed, created_at, updated_at FROM tasks WHERE id = ?",
            (task_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Failed to create task")
        return row_to_task(row)
    finally:
        conn.close()


# PUBLIC_INTERFACE
@app.put(
    "/tasks/{task_id}",
    response_model=Task,
    tags=["Tasks"],
    summary="Update task title",
    description="Update the title of an existing task.",
)
def update_task(
    task_id: int = Path(..., description="The ID of the task to update"),
    payload: TaskUpdate = Body(...),
) -> Task:
    """Update the title of an existing task."""
    now = utc_now_iso()
    conn = get_connection()
    try:
        # Ensure exists
        cur = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Task not found")

        conn.execute(
            "UPDATE tasks SET title = ?, updated_at = ? WHERE id = ?",
            (payload.title, now, task_id),
        )
        conn.commit()
        cur = conn.execute(
            "SELECT id, title, completed, created_at, updated_at FROM tasks WHERE id = ?",
            (task_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found after update")
        return row_to_task(row)
    finally:
        conn.close()


# PUBLIC_INTERFACE
@app.patch(
    "/tasks/{task_id}/complete",
    response_model=Task,
    tags=["Tasks"],
    summary="Toggle task completion",
    description="Toggle the completion status of a task and update its updated_at timestamp.",
)
def toggle_complete(
    task_id: int = Path(..., description="The ID of the task to toggle completion"),
) -> Task:
    """Toggle the 'completed' status of a task."""
    now = utc_now_iso()
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id, title, completed, created_at, updated_at FROM tasks WHERE id = ?",
            (task_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")

        new_completed = 0 if bool(row["completed"]) else 1
        conn.execute(
            "UPDATE tasks SET completed = ?, updated_at = ? WHERE id = ?",
            (new_completed, now, task_id),
        )
        conn.commit()
        cur = conn.execute(
            "SELECT id, title, completed, created_at, updated_at FROM tasks WHERE id = ?",
            (task_id,),
        )
        updated_row = cur.fetchone()
        if not updated_row:
            raise HTTPException(status_code=404, detail="Task not found after toggle")
        return row_to_task(updated_row)
    finally:
        conn.close()


# PUBLIC_INTERFACE
@app.delete(
    "/tasks/{task_id}",
    status_code=204,
    tags=["Tasks"],
    summary="Delete task",
    description="Delete a task by its ID.",
)
def delete_task(
    task_id: int = Path(..., description="The ID of the task to delete"),
) -> None:
    """Delete a task."""
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Task not found")
        # 204 no content response
        return None
    finally:
        conn.close()
