from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from extensions import get_db
from models.chat import Chat, ChatParticipant
from models.message import Message
from models.user import User
from routes.auth import _get_current_user
from ws_manager import manager

router = APIRouter(prefix='/api/chats', tags=['chats'])


class CreateChatRequest(BaseModel):
    name: str
    participant_ids: list[int] = []


class SendMessageRequest(BaseModel):
    content: str


class AddParticipantRequest(BaseModel):
    user_id: int


def _require_participant(user_id: int, chat_id: int, db: Session) -> Chat:
    chat = db.query(Chat).filter_by(chat_id=chat_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail={'error': 'Chat not found'})
    if not db.query(ChatParticipant).filter_by(chat_id=chat_id, user_id=user_id).first():
        raise HTTPException(status_code=403, detail={'error': 'You do not have permission to perform this action'})
    return chat


@router.get('')
def list_chats(db: Session = Depends(get_db), current_user: User = Depends(_get_current_user)):
    rows = db.query(ChatParticipant).filter_by(user_id=current_user.user_id).all()
    chat_ids = [r.chat_id for r in rows]
    chats = db.query(Chat).filter(Chat.chat_id.in_(chat_ids)).all()
    return {'chats': [c.to_dict() for c in chats]}


@router.post('', status_code=201)
def create_chat(
    body: CreateChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail={'field': 'name', 'error': 'Chat name is required'})

    chat = Chat(name=name, created_by=current_user.user_id)
    db.add(chat)
    db.flush()

    participant_ids = set(body.participant_ids) | {current_user.user_id}
    for uid in participant_ids:
        if not db.query(User).filter_by(user_id=uid).first():
            raise HTTPException(status_code=404, detail={'error': f'User {uid} not found'})
        db.add(ChatParticipant(chat_id=chat.chat_id, user_id=uid))

    db.commit()
    db.refresh(chat)
    return {'message': 'Chat created successfully', 'chat': chat.to_dict()}


@router.get('/{chat_id}/participants')
def list_participants(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    _require_participant(current_user.user_id, chat_id, db)
    rows = db.query(ChatParticipant).filter_by(chat_id=chat_id).all()
    user_ids = [r.user_id for r in rows]
    users = db.query(User).filter(User.user_id.in_(user_ids)).all()
    return {'participants': [u.to_dict() for u in users]}


@router.post('/{chat_id}/participants', status_code=201)
def add_participant(
    chat_id: int,
    body: AddParticipantRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    chat = _require_participant(current_user.user_id, chat_id, db)
    if not db.query(User).filter_by(user_id=body.user_id).first():
        raise HTTPException(status_code=404, detail={'error': 'User not found'})
    if db.query(ChatParticipant).filter_by(chat_id=chat_id, user_id=body.user_id).first():
        raise HTTPException(status_code=409, detail={'error': 'User is already a participant'})
    db.add(ChatParticipant(chat_id=chat_id, user_id=body.user_id))
    db.commit()
    return {'message': 'Participant added'}


@router.get('/{chat_id}/messages')
def list_messages(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    _require_participant(current_user.user_id, chat_id, db)
    messages = db.query(Message).filter_by(chat_id=chat_id).order_by(Message.sent_at).all()
    return {'messages': [m.to_dict() for m in messages]}


@router.post('/{chat_id}/messages', status_code=201)
async def send_message(
    chat_id: int,
    body: SendMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    _require_participant(current_user.user_id, chat_id, db)
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail={'field': 'content', 'error': 'Message cannot be empty'})

    msg = Message(chat_id=chat_id, sender_id=current_user.user_id, content=content)
    db.add(msg)
    db.commit()
    db.refresh(msg)

    await manager.broadcast(chat_id, {'type': 'message_created', 'message': msg.to_dict()})
    return {'message': msg.to_dict()}
