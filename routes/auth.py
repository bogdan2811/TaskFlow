import re
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from jose import jwt, JWTError
from passlib.context import CryptContext

from extensions import get_db
from models.chat import Chat, ChatParticipant
from models.task import Task, TaskAssignee
from models.user import User
from system_events import is_system_user
from ws_manager import manager

router = APIRouter(prefix='/api/auth', tags=['auth'])

pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')

JWT_ALGORITHM = 'HS256'
JWT_EXPIRE_MINUTES = 60

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    confirmPassword: str
    acceptTerms: bool


class LoginRequest(BaseModel):
    email: str
    password: str


class EditAccountRequest(BaseModel):
    currentPassword: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    newPassword: Optional[str] = None
    confirmNewPassword: Optional[str] = None


class DeleteAccountRequest(BaseModel):
    currentPassword: str


security = HTTPBearer()


def _jwt_secret() -> str:
    secret = os.environ.get('JWT_SECRET_KEY')
    if not secret:
        raise RuntimeError('JWT_SECRET_KEY environment variable is required')
    return secret


def _decode_token_subject(token: str) -> int:
    payload = jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])
    return int(payload['sub'])


def _is_deleted_user(user: User) -> bool:
    return user.username.startswith('deleted_') or user.email.endswith('@deleted.taskflow.local')


def _get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    try:
        user_id = _decode_token_subject(credentials.credentials)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail={'error': str(exc)})
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail={'error': 'Invalid or expired token'})

    user = db.query(User).filter_by(user_id=user_id).first()
    if not user or _is_deleted_user(user) or is_system_user(user):
        raise HTTPException(status_code=401, detail={'error': 'User not found'})
    return user


def _validate_username(username: str):
    if len(username) > 20:
        return False, 'Provide a shorter username'
    if len(username) < 5 or ' ' in username:
        return False, 'Please enter a valid username'
    return True, None


def _validate_email(email: str):
    if len(email) > 254:
        return False, 'Provide a shorter email address'
    if len(email) < 5 or not _EMAIL_RE.match(email):
        return False, 'Please enter a valid email address'
    return True, None


def _validate_password(password: str):
    if len(password) < 8 or len(password) > 64:
        return False, 'Password must be between 8 and 64 characters'
    if ' ' in password:
        return False, 'Password must not contain spaces'
    if not re.search(r'[A-Z]', password):
        return False, 'Password must contain at least one uppercase letter'
    if not re.search(r'[a-z]', password):
        return False, 'Password must contain at least one lowercase letter'
    if not re.search(r'[0-9]', password):
        return False, 'Password must contain at least one digit'
    return True, None


def _participant_ids(chat_id: int, db: Session) -> list[int]:
    rows = db.query(ChatParticipant).filter_by(chat_id=chat_id).order_by(
        ChatParticipant.joined_at,
        ChatParticipant.chat_participant_id,
    ).all()
    return [row.user_id for row in rows]


def _transfer_or_remove_chat_memberships(user_id: int, db: Session) -> list[dict]:
    events = []
    memberships = db.query(ChatParticipant).filter_by(user_id=user_id).all()

    for membership in memberships:
        chat = db.query(Chat).filter_by(chat_id=membership.chat_id).first()
        if not chat:
            continue

        remaining_ids = [uid for uid in _participant_ids(chat.chat_id, db) if uid != user_id]
        if not remaining_ids:
            events.append({'type': 'chat_deleted', 'chatId': chat.chat_id, 'participantIds': [user_id]})
            db.delete(chat)
            continue

        new_owner_id = remaining_ids[0]
        if chat.created_by == user_id:
            chat.created_by = new_owner_id

        tasks = db.query(Task).filter_by(chat_id=chat.chat_id).all()
        for task in tasks:
            if task.creator_id == user_id:
                task.creator_id = new_owner_id
                task.version += 1
                task.updated_at = datetime.now(timezone.utc)

        task_ids = [task.task_id for task in tasks]
        if task_ids:
            db.query(TaskAssignee).filter(
                TaskAssignee.task_id.in_(task_ids),
                TaskAssignee.assigned_by == user_id,
            ).update({TaskAssignee.assigned_by: new_owner_id}, synchronize_session=False)
            db.query(TaskAssignee).filter(
                TaskAssignee.task_id.in_(task_ids),
                TaskAssignee.user_id == user_id,
            ).delete(synchronize_session=False)

        db.delete(membership)
        events.append({
            'type': 'participant_left',
            'chatId': chat.chat_id,
            'userId': user_id,
            'participantIds': remaining_ids,
        })

    return events


@router.get('/health')
def health():
    return {'status': 'ok'}


@router.get('/me')
def get_me(current_user: User = Depends(_get_current_user)):
    return {'user': current_user.to_dict()}


@router.get('/users')
def get_users(db: Session = Depends(get_db), current_user: User = Depends(_get_current_user)):
    users = db.query(User).filter(~User.username.like('deleted_%'), User.email != 'system@taskflow.local').all()
    return [u.to_dict() for u in users]


@router.post('/register', status_code=201)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    username_raw = body.username.strip()
    email_raw = body.email.strip().lower()
    password = body.password
    confirm_password = body.confirmPassword
    accept_terms = body.acceptTerms

    if not accept_terms:
        raise HTTPException(status_code=400, detail={'field': 'acceptTerms', 'error': 'You must accept the Terms & Conditions'})

    ok, err = _validate_username(username_raw)
    if not ok:
        raise HTTPException(status_code=422, detail={'field': 'username', 'error': err})

    ok, err = _validate_email(email_raw)
    if not ok:
        raise HTTPException(status_code=422, detail={'field': 'email', 'error': err})

    ok, err = _validate_password(password)
    if not ok:
        raise HTTPException(status_code=422, detail={'field': 'password', 'error': err})

    if password != confirm_password:
        raise HTTPException(status_code=422, detail={'field': 'confirmPassword', 'error': 'Passwords do not match'})

    if db.query(User).filter(func.lower(User.username) == username_raw.lower()).first():
        raise HTTPException(status_code=409, detail={'field': 'username', 'error': 'Username already exists'})

    if db.query(User).filter_by(email=email_raw).first():
        raise HTTPException(status_code=409, detail={'field': 'email', 'error': 'An account with this email already exists'})

    user = User(
        username=username_raw,
        email=email_raw,
        password_hash=pwd_context.hash(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {'message': 'Account created successfully', 'user': user.to_dict()}


@router.post('/login')
def login(body: LoginRequest, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    password = body.password

    if not email or not password:
        raise HTTPException(status_code=400, detail={'error': 'Missing fields'})

    user = db.query(User).filter_by(email=email).first()

    if not user or _is_deleted_user(user) or is_system_user(user) or not pwd_context.verify(password, user.password_hash):
        raise HTTPException(status_code=401, detail={'error': 'Invalid email or password'})

    try:
        token = jwt.encode(
            {'sub': str(user.user_id), 'exp': datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)},
            _jwt_secret(),
            algorithm=JWT_ALGORITHM,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail={'error': str(exc)})

    return {'message': 'Login successful', 'token': token, 'user': user.to_dict()}


@router.patch('/account')
def edit_account(
    body: EditAccountRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    if body.username is not None:
        username_raw = body.username.strip()
        ok, err = _validate_username(username_raw)
        if not ok:
            raise HTTPException(status_code=422, detail={'field': 'username', 'error': err})
        if db.query(User).filter(func.lower(User.username) == username_raw.lower(), User.user_id != current_user.user_id).first():
            raise HTTPException(status_code=409, detail={'field': 'username', 'error': 'Username already exists'})
        current_user.username = username_raw

    if body.email is not None:
        email_raw = body.email.strip().lower()
        ok, err = _validate_email(email_raw)
        if not ok:
            raise HTTPException(status_code=422, detail={'field': 'email', 'error': err})
        if db.query(User).filter(User.email == email_raw, User.user_id != current_user.user_id).first():
            raise HTTPException(status_code=409, detail={'field': 'email', 'error': 'Email already exists'})
        current_user.email = email_raw

    if body.newPassword is not None:
        if not body.currentPassword:
            raise HTTPException(status_code=400, detail={'field': 'currentPassword', 'error': 'Current password is required'})
        if not pwd_context.verify(body.currentPassword, current_user.password_hash):
            raise HTTPException(status_code=401, detail={'field': 'currentPassword', 'error': 'Incorrect password'})
        ok, err = _validate_password(body.newPassword)
        if not ok:
            raise HTTPException(status_code=422, detail={'field': 'newPassword', 'error': err})
        if body.newPassword != body.confirmNewPassword:
            raise HTTPException(status_code=422, detail={'field': 'confirmNewPassword', 'error': 'Passwords do not match'})
        current_user.password_hash = pwd_context.hash(body.newPassword)

    db.commit()
    db.refresh(current_user)

    return {'message': 'Account updated successfully', 'user': current_user.to_dict()}


@router.delete('/account')
async def delete_account(
    body: DeleteAccountRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    if not body.currentPassword:
        raise HTTPException(status_code=400, detail={'field': 'currentPassword', 'error': 'Current password is required'})
    if not pwd_context.verify(body.currentPassword, current_user.password_hash):
        raise HTTPException(status_code=401, detail={'field': 'currentPassword', 'error': 'Incorrect password'})

    user_id = current_user.user_id
    events = _transfer_or_remove_chat_memberships(user_id, db)

    remaining_assignee_links = db.query(TaskAssignee).filter_by(user_id=user_id).all()
    for link in remaining_assignee_links:
        db.delete(link)

    remaining_assigned_by_links = db.query(TaskAssignee).filter_by(assigned_by=user_id).all()
    for link in remaining_assigned_by_links:
        task = db.query(Task).filter_by(task_id=link.task_id).first()
        if task and task.creator_id != user_id:
            link.assigned_by = task.creator_id
        else:
            db.delete(link)

    remaining_created_tasks = db.query(Task).filter_by(creator_id=user_id).all()
    for task in remaining_created_tasks:
        participant_ids = _participant_ids(task.chat_id, db)
        if participant_ids:
            task.creator_id = participant_ids[0]
            task.version += 1
            task.updated_at = datetime.now(timezone.utc)
        else:
            task.creator_id = user_id

    current_user.username = f'deleted_{user_id}'
    current_user.email = f'deleted_{user_id}@deleted.taskflow.local'
    current_user.password_hash = pwd_context.hash(os.urandom(32).hex())
    current_user.updated_at = datetime.now(timezone.utc)
    db.commit()

    for event in events:
        if event['type'] == 'chat_deleted':
            await manager.broadcast_users(event['participantIds'], {'type': 'chat_deleted', 'chatId': event['chatId']})
            await manager.disconnect_chat(event['chatId'], reason='Chat deleted')
            continue
        await manager.broadcast_users(
            event['participantIds'],
            {'type': 'participant_left', 'chatId': event['chatId'], 'userId': user_id},
        )
        await manager.disconnect_user(event['chatId'], user_id, reason='Account deleted')

    await manager.disconnect_all_user_sockets(user_id, reason='Account deleted')
    return {'message': 'Account deleted successfully'}
