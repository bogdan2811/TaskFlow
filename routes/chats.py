from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from extensions import get_db
from models.chat import Chat, ChatParticipant
from models.message import Message
from models.task import Task, TaskAssignee
from models.user import User
from routes.auth import _get_current_user
from system_events import add_system_message, broadcast_system_message
from ws_manager import manager

router = APIRouter(prefix='/api/chats', tags=['chats'])


class CreateChatRequest(BaseModel):
    name: str
    participant_ids: list[int] = Field(default_factory=list)


class UpdateChatRequest(BaseModel):
    name: str


class SendMessageRequest(BaseModel):
    content: str


class UpdateMessageRequest(BaseModel):
    content: str


class AddParticipantRequest(BaseModel):
    user_id: Optional[int] = None
    participant_ids: list[int] = Field(default_factory=list)


def _require_participant(user_id: int, chat_id: int, db: Session) -> Chat:
    chat = db.query(Chat).filter_by(chat_id=chat_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail={'error': 'Chat not found'})
    if not db.query(ChatParticipant).filter_by(chat_id=chat_id, user_id=user_id).first():
        raise HTTPException(status_code=403, detail={'error': 'You do not have permission to perform this action'})
    return chat


def _require_owner(user_id: int, chat: Chat):
    if chat.created_by != user_id:
        raise HTTPException(status_code=403, detail={'error': 'Only the chat owner can perform this action'})


def _participant_ids(chat_id: int, db: Session) -> list[int]:
    rows = db.query(ChatParticipant).filter_by(chat_id=chat_id).order_by(ChatParticipant.joined_at).all()
    return [row.user_id for row in rows]


def _users_by_id(user_ids: list[int], db: Session) -> list[User]:
    if not user_ids:
        return []
    users = db.query(User).filter(User.user_id.in_(user_ids)).all()
    return sorted(users, key=lambda user: user_ids.index(user.user_id))


def _ensure_users_exist(user_ids: set[int], db: Session):
    if not user_ids:
        return
    existing_ids = {
        row.user_id
        for row in db.query(User.user_id).filter(User.user_id.in_(user_ids)).all()
    }
    missing_ids = sorted(user_ids - existing_ids)
    if missing_ids:
        raise HTTPException(status_code=404, detail={'error': f'Users not found: {missing_ids}'})


def _reassign_tasks_before_participant_removal(chat_id: int, user_id: int, new_owner_id: Optional[int], db: Session):
    tasks = db.query(Task).filter_by(chat_id=chat_id).all()
    task_ids = [task.task_id for task in tasks]
    if task_ids:
        db.query(TaskAssignee).filter(
            TaskAssignee.task_id.in_(task_ids),
            TaskAssignee.user_id == user_id,
        ).delete(synchronize_session=False)

    if new_owner_id is None:
        return

    for task in tasks:
        if task.creator_id == user_id:
            task.creator_id = new_owner_id
            task.version += 1
            task.updated_at = datetime.now(timezone.utc)


def _chat_payload(chat: Chat, db: Session, include_participants: bool = False) -> dict:
    participant_ids = _participant_ids(chat.chat_id, db)
    last_message = (
        db.query(Message)
        .filter_by(chat_id=chat.chat_id)
        .order_by(Message.message_id.desc())
        .first()
    )

    data = chat.to_dict()
    data['participantIds'] = participant_ids
    data['participantCount'] = len(participant_ids)
    if last_message:
        sender = db.query(User).filter_by(user_id=last_message.sender_id).first()
        data['lastMessage'] = last_message.to_dict(sender)
    else:
        data['lastMessage'] = None

    if include_participants:
        data['participants'] = [user.to_dict() for user in _users_by_id(participant_ids, db)]

    return data


def _message_for_chat(chat_id: int, message_id: int, db: Session) -> Message:
    message = db.query(Message).filter_by(chat_id=chat_id, message_id=message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail={'error': 'Message not found'})
    return message


def _create_message(chat_id: int, sender_id: int, content: str, db: Session) -> Message:
    content = content.strip()
    if not content:
        raise HTTPException(status_code=422, detail={'field': 'content', 'error': 'Message cannot be empty'})

    message = Message(chat_id=chat_id, sender_id=sender_id, content=content)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def _names_for_user_ids(user_ids: list[int], db: Session) -> list[str]:
    users = _users_by_id(user_ids, db)
    return [user.username for user in users]


async def _broadcast_chat(chat_id: int, event_type: str, payload: dict):
    await manager.broadcast(chat_id, {'type': event_type, **payload})


@router.get('')
def list_chats(db: Session = Depends(get_db), current_user: User = Depends(_get_current_user)):
    rows = db.query(ChatParticipant).filter_by(user_id=current_user.user_id).all()
    chat_ids = [row.chat_id for row in rows]
    if not chat_ids:
        return {'chats': []}

    chats = db.query(Chat).filter(Chat.chat_id.in_(chat_ids)).all()
    payloads = [_chat_payload(chat, db) for chat in chats]
    payloads.sort(
        key=lambda chat_data: (
            chat_data['lastMessage']['sentAt'] if chat_data['lastMessage'] else chat_data['createdAt']
        ),
        reverse=True,
    )
    return {'chats': payloads}


@router.post('', status_code=201)
async def create_chat(
    body: CreateChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail={'field': 'name', 'error': 'Chat name is required'})

    participant_ids = set(body.participant_ids) | {current_user.user_id}
    _ensure_users_exist(participant_ids, db)

    chat = Chat(name=name, created_by=current_user.user_id)
    db.add(chat)
    db.flush()

    for user_id in sorted(participant_ids):
        db.add(ChatParticipant(chat_id=chat.chat_id, user_id=user_id))

    system_message = add_system_message(chat.chat_id, f'{current_user.username} created the conversation.', db)
    db.commit()
    db.refresh(chat)
    db.refresh(system_message)
    payload = _chat_payload(chat, db, include_participants=True)
    await manager.broadcast_users(participant_ids, {'type': 'chat_created', 'chat': payload})
    await broadcast_system_message(system_message, db, list(participant_ids))
    return {'message': 'Chat created successfully', 'chat': payload}


@router.get('/{chat_id}')
def get_chat(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    chat = _require_participant(current_user.user_id, chat_id, db)
    return {'chat': _chat_payload(chat, db, include_participants=True)}


@router.patch('/{chat_id}')
async def update_chat(
    chat_id: int,
    body: UpdateChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    chat = _require_participant(current_user.user_id, chat_id, db)
    _require_owner(current_user.user_id, chat)

    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail={'field': 'name', 'error': 'Chat name is required'})

    old_name = chat.name
    chat.name = name
    system_message = None
    if old_name != name:
        system_message = add_system_message(
            chat_id,
            f'{current_user.username} renamed the conversation from "{old_name}" to "{name}".',
            db,
        )
    db.commit()
    db.refresh(chat)
    if system_message:
        db.refresh(system_message)

    payload = _chat_payload(chat, db, include_participants=True)
    await _broadcast_chat(chat_id, 'chat_updated', {'chat': payload})
    if system_message:
        await broadcast_system_message(system_message, db)
    return {'message': 'Chat updated successfully', 'chat': payload}


@router.delete('/{chat_id}')
async def delete_chat(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    chat = _require_participant(current_user.user_id, chat_id, db)
    _require_owner(current_user.user_id, chat)

    payload = _chat_payload(chat, db, include_participants=True)
    db.delete(chat)
    db.commit()

    await _broadcast_chat(chat_id, 'chat_deleted', {'chat': payload})
    await manager.disconnect_chat(chat_id, reason='Chat deleted')
    return {'message': 'Chat deleted successfully'}


@router.get('/{chat_id}/participants')
def list_participants(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    _require_participant(current_user.user_id, chat_id, db)
    participant_ids = _participant_ids(chat_id, db)
    users = _users_by_id(participant_ids, db)
    return {'participants': [user.to_dict() for user in users]}


@router.post('/{chat_id}/participants', status_code=201)
async def add_participants(
    chat_id: int,
    body: AddParticipantRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    chat = _require_participant(current_user.user_id, chat_id, db)
    _require_owner(current_user.user_id, chat)

    new_ids = set(body.participant_ids)
    if body.user_id is not None:
        new_ids.add(body.user_id)
    if not new_ids:
        raise HTTPException(status_code=422, detail={'field': 'participants', 'error': 'At least one user is required'})

    _ensure_users_exist(new_ids, db)

    existing_ids = set(_participant_ids(chat_id, db))
    already_present = sorted(new_ids & existing_ids)
    if already_present:
        raise HTTPException(status_code=409, detail={'error': f'Users already in chat: {already_present}'})

    for user_id in sorted(new_ids):
        db.add(ChatParticipant(chat_id=chat_id, user_id=user_id))

    added_names = ', '.join(_names_for_user_ids(sorted(new_ids), db))
    system_message = add_system_message(chat_id, f'{current_user.username} added {added_names} to the conversation.', db)
    db.commit()
    db.refresh(chat)
    db.refresh(system_message)

    payload = _chat_payload(chat, db, include_participants=True)
    await _broadcast_chat(chat_id, 'participants_added', {'userIds': sorted(new_ids), 'chat': payload})
    await manager.broadcast_users(new_ids, {'type': 'chat_created', 'chat': payload})
    await manager.broadcast_users(existing_ids, {'type': 'chat_participants_added', 'userIds': sorted(new_ids), 'chat': payload})
    await broadcast_system_message(system_message, db)
    return {'message': 'Participants added', 'chat': payload}


@router.delete('/{chat_id}/participants/me')
async def leave_chat(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    chat = _require_participant(current_user.user_id, chat_id, db)
    participant = db.query(ChatParticipant).filter_by(chat_id=chat_id, user_id=current_user.user_id).first()
    remaining = [
        row
        for row in db.query(ChatParticipant)
        .filter_by(chat_id=chat_id)
        .order_by(ChatParticipant.joined_at, ChatParticipant.chat_participant_id)
        .all()
        if row.user_id != current_user.user_id
    ]

    if not remaining:
        db.delete(chat)
        db.commit()
        await manager.disconnect_user(chat_id, current_user.user_id, reason='Left chat')
        return {'message': 'Left chat and deleted empty chat'}

    new_owner_id = remaining[0].user_id
    _reassign_tasks_before_participant_removal(chat_id, current_user.user_id, new_owner_id, db)

    if chat.created_by == current_user.user_id:
        chat.created_by = new_owner_id

    system_message = add_system_message(chat_id, f'{current_user.username} left the conversation.', db)
    db.delete(participant)
    db.commit()
    db.refresh(chat)
    db.refresh(system_message)

    payload = _chat_payload(chat, db, include_participants=True)
    await _broadcast_chat(chat_id, 'participant_left', {'userId': current_user.user_id, 'chat': payload})
    await broadcast_system_message(system_message, db, [row.user_id for row in remaining])
    await manager.disconnect_user(chat_id, current_user.user_id, reason='Left chat')
    return {'message': 'Left chat successfully'}


@router.delete('/{chat_id}/participants/{user_id}')
async def remove_participant(
    chat_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    chat = _require_participant(current_user.user_id, chat_id, db)

    if user_id == current_user.user_id:
        raise HTTPException(status_code=400, detail={'error': 'Use the leave chat endpoint to remove yourself'})
    _require_owner(current_user.user_id, chat)

    participant = db.query(ChatParticipant).filter_by(chat_id=chat_id, user_id=user_id).first()
    if not participant:
        raise HTTPException(status_code=404, detail={'error': 'Participant not found'})

    removed_user = db.query(User).filter_by(user_id=user_id).first()
    _reassign_tasks_before_participant_removal(chat_id, user_id, chat.created_by, db)

    system_message = add_system_message(
        chat_id,
        f'{current_user.username} removed {removed_user.username if removed_user else "a user"} from the conversation.',
        db,
    )
    db.delete(participant)
    db.commit()
    db.refresh(chat)
    db.refresh(system_message)

    payload = _chat_payload(chat, db, include_participants=True)
    await _broadcast_chat(chat_id, 'participant_removed', {'userId': user_id, 'chat': payload})
    await broadcast_system_message(system_message, db)
    await manager.disconnect_user(chat_id, user_id, reason='Removed from chat')
    return {'message': 'Participant removed'}


@router.get('/{chat_id}/messages')
def list_messages(
    chat_id: int,
    before_id: Optional[int] = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    _require_participant(current_user.user_id, chat_id, db)

    query = db.query(Message).filter_by(chat_id=chat_id)
    if before_id is not None:
        query = query.filter(Message.message_id < before_id)

    messages = query.order_by(Message.message_id.desc()).limit(limit).all()
    messages.reverse()

    sender_ids = {message.sender_id for message in messages}
    senders = {
        user.user_id: user
        for user in db.query(User).filter(User.user_id.in_(sender_ids)).all()
    } if sender_ids else {}

    return {'messages': [message.to_dict(senders.get(message.sender_id)) for message in messages]}


@router.post('/{chat_id}/messages', status_code=201)
async def send_message(
    chat_id: int,
    body: SendMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    _require_participant(current_user.user_id, chat_id, db)
    message = _create_message(chat_id, current_user.user_id, body.content, db)

    message_payload = message.to_dict(current_user)
    await _broadcast_chat(chat_id, 'message_created', {'message': message_payload})
    await manager.broadcast_users(
        _participant_ids(chat_id, db),
        {'type': 'chat_message_created', 'chatId': chat_id, 'message': message_payload},
    )
    return {'message': message_payload}


@router.patch('/{chat_id}/messages/{message_id}')
async def update_message(
    chat_id: int,
    message_id: int,
    body: UpdateMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    _require_participant(current_user.user_id, chat_id, db)
    message = _message_for_chat(chat_id, message_id, db)
    if message.sender_id != current_user.user_id:
        raise HTTPException(status_code=403, detail={'error': 'Only the sender can edit this message'})

    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail={'field': 'content', 'error': 'Message cannot be empty'})

    message.content = content
    message.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(message)

    message_payload = message.to_dict(current_user)
    await _broadcast_chat(chat_id, 'message_updated', {'message': message_payload})
    await manager.broadcast_users(
        _participant_ids(chat_id, db),
        {'type': 'chat_message_updated', 'chatId': chat_id, 'message': message_payload},
    )
    return {'message': message_payload}


@router.delete('/{chat_id}/messages/{message_id}')
async def delete_message(
    chat_id: int,
    message_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    _require_participant(current_user.user_id, chat_id, db)
    message = _message_for_chat(chat_id, message_id, db)
    if message.sender_id != current_user.user_id:
        raise HTTPException(status_code=403, detail={'error': 'Only the sender can delete this message'})

    sender = db.query(User).filter_by(user_id=message.sender_id).first()
    payload = message.to_dict(sender)
    db.delete(message)
    db.commit()

    await _broadcast_chat(chat_id, 'message_deleted', {'message': payload})
    await manager.broadcast_users(
        _participant_ids(chat_id, db),
        {'type': 'chat_message_deleted', 'chatId': chat_id, 'message': payload},
    )
    return {'message': 'Message deleted successfully'}
