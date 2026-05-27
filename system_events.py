import os

from passlib.context import CryptContext
from sqlalchemy.orm import Session

from models.chat import ChatParticipant
from models.message import Message
from models.user import User
from ws_manager import manager

SYSTEM_USERNAME = 'System'
SYSTEM_EMAIL = 'system@taskflow.local'

pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')


def is_system_user(user: User | None) -> bool:
    return bool(user and user.username == SYSTEM_USERNAME and user.email == SYSTEM_EMAIL)


def get_system_user(db: Session) -> User:
    user = db.query(User).filter_by(email=SYSTEM_EMAIL).first()
    if user:
        return user

    user = User(
        username=SYSTEM_USERNAME,
        email=SYSTEM_EMAIL,
        password_hash=pwd_context.hash(os.urandom(32).hex()),
    )
    db.add(user)
    db.flush()
    return user


def chat_participant_ids(chat_id: int, db: Session) -> list[int]:
    return [
        row.user_id
        for row in db.query(ChatParticipant.user_id).filter_by(chat_id=chat_id).all()
    ]


def add_system_message(chat_id: int, content: str, db: Session) -> Message:
    system_user = get_system_user(db)
    message = Message(chat_id=chat_id, sender_id=system_user.user_id, content=content)
    db.add(message)
    db.flush()
    return message


async def broadcast_system_message(message: Message, db: Session, user_ids: list[int] | None = None):
    system_user = get_system_user(db)
    payload = message.to_dict(system_user)
    participant_ids = user_ids if user_ids is not None else chat_participant_ids(message.chat_id, db)
    await manager.broadcast(message.chat_id, {'type': 'message_created', 'message': payload})
    await manager.broadcast_users(
        participant_ids,
        {'type': 'chat_message_created', 'chatId': message.chat_id, 'message': payload},
    )
