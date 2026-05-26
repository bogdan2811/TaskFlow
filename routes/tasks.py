from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from extensions import get_db
from models.chat import Chat, ChatParticipant
from models.message import Message
from models.task import Task, TaskAssignee, TASK_STATUSES, TASK_PRIORITIES
from models.user import User
from routes.auth import _get_current_user
from ws_manager import manager

router = APIRouter(prefix='/api', tags=['tasks'])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class CreateTaskRequest(BaseModel):
    chat_id: int
    source_message_id: Optional[int] = None
    title: str
    description: str
    assignee_ids: list[int]
    deadline: str
    priority: str = 'medium'
    category: Optional[str] = None
    status: str = 'todo'


class UpdateTaskRequest(BaseModel):
    version: int
    source_message_id: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None
    assignee_ids: Optional[list[int]] = None
    deadline: Optional[str] = None
    priority: Optional[str] = None
    category: Optional[str] = None
    status: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_chat_participant(user_id: int, chat_id: int, db: Session) -> Chat:
    chat = db.query(Chat).filter_by(chat_id=chat_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail={'error': 'Chat not found'})
    if not db.query(ChatParticipant).filter_by(chat_id=chat_id, user_id=user_id).first():
        raise HTTPException(status_code=403, detail={'error': 'You do not have permission to modify this task.'})
    return chat


def _parse_deadline(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail={'field': 'deadline', 'error': 'Invalid date format'})


def _validate_assignees(assignee_ids: list[int], chat_id: int, db: Session):
    unique_ids = set(assignee_ids)
    participant_ids = {
        row.user_id
        for row in db.query(ChatParticipant.user_id)
        .filter(ChatParticipant.chat_id == chat_id, ChatParticipant.user_id.in_(unique_ids))
        .all()
    }
    missing_ids = sorted(unique_ids - participant_ids)
    if missing_ids:
        raise HTTPException(
            status_code=422,
            detail={
                'field': 'assignees',
                'error': 'Selected user is not part of this conversation',
                'userIds': missing_ids,
            },
        )


def _validate_source_message(source_message_id: Optional[int], chat_id: int, db: Session):
    if source_message_id is None:
        return
    message = db.query(Message).filter_by(
        message_id=source_message_id, chat_id=chat_id
    ).first()
    if not message:
        raise HTTPException(status_code=404, detail={'error': 'Source message not found'})


def _normalize_category(category: Optional[str]) -> Optional[str]:
    if category is None:
        return None
    category = category.strip()
    return category or None


def _field_was_sent(body: BaseModel, field_name: str) -> bool:
    field_set = getattr(body, 'model_fields_set', getattr(body, '__fields_set__', set()))
    return field_name in field_set


def _can_edit_task(task: Task, user_id: int, db: Session) -> bool:
    if db.query(Chat).filter_by(chat_id=task.chat_id, created_by=user_id).first():
        return True
    return db.query(TaskAssignee).filter_by(task_id=task.task_id, user_id=user_id).first() is not None


async def _broadcast(chat_id: int, event_type: str, task_data: dict):
    await manager.broadcast(chat_id, {'type': event_type, 'task': task_data})


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post('/tasks', status_code=201)
async def create_task(
    body: CreateTaskRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    _require_chat_participant(current_user.user_id, body.chat_id, db)

    _validate_source_message(body.source_message_id, body.chat_id, db)

    if not body.title or not body.title.strip():
        raise HTTPException(status_code=422, detail={'field': 'title', 'error': 'Task title is required'})

    if not body.description or not body.description.strip():
        raise HTTPException(status_code=422, detail={'field': 'description', 'error': 'Task description is required'})

    if not body.assignee_ids:
        raise HTTPException(status_code=422, detail={'field': 'assignees', 'error': 'At least one assignee is required'})
    assignee_ids = list(dict.fromkeys(body.assignee_ids))
    _validate_assignees(assignee_ids, body.chat_id, db)

    due_date = _parse_deadline(body.deadline)

    if body.priority not in TASK_PRIORITIES:
        raise HTTPException(status_code=422, detail={'field': 'priority', 'error': 'Invalid priority'})

    if body.status not in TASK_STATUSES:
        raise HTTPException(status_code=422, detail={'field': 'status', 'error': 'Invalid status'})

    task = Task(
        title=body.title.strip(),
        description=body.description.strip(),
        status=body.status,
        priority=body.priority,
        category=_normalize_category(body.category),
        creator_id=current_user.user_id,
        chat_id=body.chat_id,
        source_message_id=body.source_message_id,
        due_date=due_date,
        version=1,
    )
    db.add(task)
    db.flush()

    for uid in assignee_ids:
        db.add(TaskAssignee(task_id=task.task_id, user_id=uid, assigned_by=current_user.user_id))

    db.commit()
    db.refresh(task)

    await _broadcast(task.chat_id, 'task_created', task.to_dict())
    return {'message': 'Task created successfully', 'task': task.to_dict()}


@router.patch('/tasks/{task_id}')
async def update_task(
    task_id: int,
    body: UpdateTaskRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    task = db.query(Task).filter_by(task_id=task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail={'error': 'Task not found.'})

    _require_chat_participant(current_user.user_id, task.chat_id, db)

    if not _can_edit_task(task, current_user.user_id, db):
        raise HTTPException(status_code=403, detail={'error': 'You do not have permission to modify this task.'})

    if body.version != task.version:
        raise HTTPException(status_code=409, detail={'error': 'Something went wrong. Please refresh.'})

    if _field_was_sent(body, 'source_message_id'):
        _validate_source_message(body.source_message_id, task.chat_id, db)
        task.source_message_id = body.source_message_id

    if body.title is not None:
        if not body.title.strip():
            raise HTTPException(status_code=422, detail={'field': 'title', 'error': 'Task title is required'})
        task.title = body.title.strip()

    if body.description is not None:
        if not body.description.strip():
            raise HTTPException(status_code=422, detail={'field': 'description', 'error': 'Task description is required'})
        task.description = body.description.strip()

    if body.deadline is not None:
        task.due_date = _parse_deadline(body.deadline)

    if body.priority is not None:
        if body.priority not in TASK_PRIORITIES:
            raise HTTPException(status_code=422, detail={'field': 'priority', 'error': 'Invalid priority'})
        task.priority = body.priority

    if body.status is not None:
        if body.status not in TASK_STATUSES:
            raise HTTPException(status_code=422, detail={'field': 'status', 'error': 'Invalid status'})
        task.status = body.status

    if body.category is not None:
        task.category = _normalize_category(body.category)

    if body.assignee_ids is not None:
        if not body.assignee_ids:
            raise HTTPException(status_code=422, detail={'field': 'assignees', 'error': 'At least one assignee is required'})
        assignee_ids = list(dict.fromkeys(body.assignee_ids))
        _validate_assignees(assignee_ids, task.chat_id, db)
        db.query(TaskAssignee).filter_by(task_id=task_id).delete()
        for uid in assignee_ids:
            db.add(TaskAssignee(task_id=task_id, user_id=uid, assigned_by=current_user.user_id))

    task.version += 1
    task.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(task)

    await _broadcast(task.chat_id, 'task_updated', task.to_dict())
    return {'message': 'Task updated successfully', 'task': task.to_dict()}


@router.get('/tasks/{task_id}')
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    task = db.query(Task).filter_by(task_id=task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail={'error': 'Task not found.'})
    _require_chat_participant(current_user.user_id, task.chat_id, db)
    return {'task': task.to_dict()}


@router.get('/chats/{chat_id}/task-categories')
def get_chat_task_categories(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    _require_chat_participant(current_user.user_id, chat_id, db)
    rows = (
        db.query(Task.category)
        .filter(Task.chat_id == chat_id, Task.category.isnot(None), func.trim(Task.category) != '')
        .distinct()
        .order_by(Task.category)
        .all()
    )
    return {'categories': [row.category for row in rows]}


@router.get('/chats/{chat_id}/tasks')
def get_chat_tasks(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    _require_chat_participant(current_user.user_id, chat_id, db)
    tasks = db.query(Task).filter_by(chat_id=chat_id).order_by(Task.created_at).all()
    return {'tasks': [t.to_dict() for t in tasks]}


@router.delete('/tasks/{task_id}', status_code=200)
async def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    task = db.query(Task).filter_by(task_id=task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail={'error': 'Task not found.'})

    _require_chat_participant(current_user.user_id, task.chat_id, db)

    if task.creator_id != current_user.user_id:
        raise HTTPException(status_code=403, detail={'error': 'You do not have permission to delete this task.'})

    chat_id = task.chat_id
    db.delete(task)
    db.commit()

    await manager.broadcast(chat_id, {'type': 'task_deleted', 'taskId': task_id, 'chatId': chat_id})
    return {'message': 'Task deleted successfully', 'taskId': task_id}


@router.get('/tasks')
def list_all_tasks(
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    participant_rows = db.query(ChatParticipant).filter_by(user_id=current_user.user_id).all()
    chat_ids = [r.chat_id for r in participant_rows]
    if not chat_ids:
        return {'tasks': []}
    tasks = db.query(Task).filter(Task.chat_id.in_(chat_ids)).order_by(Task.created_at).all()
    return {'tasks': [t.to_dict() for t in tasks]}
