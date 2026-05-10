from datetime import datetime, timezone
from sqlalchemy import Integer, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from extensions import Base


class Chat(Base):
    __tablename__ = 'chats'

    chat_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey('users.user_id', ondelete='RESTRICT'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    participants: Mapped[list['ChatParticipant']] = relationship('ChatParticipant', back_populates='chat', cascade='all, delete-orphan')

    def to_dict(self) -> dict:
        return {
            'id': self.chat_id,
            'name': self.name,
            'createdBy': self.created_by,
            'createdAt': self.created_at.isoformat(),
        }


class ChatParticipant(Base):
    __tablename__ = 'chatparticipants'
    __table_args__ = (UniqueConstraint('chat_id', 'user_id', name='uq_chatparticipants'),)

    chat_participant_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, ForeignKey('chats.chat_id', ondelete='CASCADE'), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    chat: Mapped['Chat'] = relationship('Chat', back_populates='participants')

    def to_dict(self) -> dict:
        return {
            'chatId': self.chat_id,
            'userId': self.user_id,
            'joinedAt': self.joined_at.isoformat(),
        }
