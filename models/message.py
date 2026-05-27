from datetime import datetime, timezone
from sqlalchemy import Integer, Text, DateTime, ForeignKey
from sqlalchemy.orm import mapped_column, Mapped, relationship
from extensions import Base
from models.datetime_utils import utc_isoformat
from models.user import User

SYSTEM_USERNAME = 'System'
SYSTEM_EMAIL = 'system@taskflow.local'


class Message(Base):
    __tablename__ = 'messages'

    message_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, ForeignKey('chats.chat_id', ondelete='CASCADE'), nullable=False)
    sender_id: Mapped[int] = mapped_column(Integer, ForeignKey('users.user_id', ondelete='RESTRICT'), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self, sender: User | None = None) -> dict:
        sender_name = None
        sender_deleted = False
        sender_system = False
        if sender is not None:
            sender_system = sender.username == SYSTEM_USERNAME and sender.email == SYSTEM_EMAIL
            sender_deleted = sender.username.startswith('deleted_') or sender.email.endswith('@deleted.taskflow.local')
            sender_name = 'System' if sender_system else ('Deleted User' if sender_deleted else sender.username)

        return {
            'id': self.message_id,
            'chatId': self.chat_id,
            'senderId': self.sender_id,
            'senderName': sender_name,
            'senderDeleted': sender_deleted,
            'senderSystem': sender_system,
            'content': self.content,
            'sentAt': utc_isoformat(self.sent_at),
            'updatedAt': utc_isoformat(self.updated_at),
        }
