from datetime import datetime, timezone
from sqlalchemy import Integer, Text, DateTime, ForeignKey
from sqlalchemy.orm import mapped_column, Mapped, relationship
from extensions import Base


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

    def to_dict(self) -> dict:
        return {
            'id': self.message_id,
            'chatId': self.chat_id,
            'senderId': self.sender_id,
            'content': self.content,
            'sentAt': self.sent_at.isoformat(),
        }
