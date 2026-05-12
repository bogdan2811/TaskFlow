from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Integer, String, Text, DateTime, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from extensions import Base

TASK_STATUSES = ('todo', 'in_progress', 'blocked', 'done')
TASK_PRIORITIES = ('low', 'medium', 'high', 'critical')


class Task(Base):
    __tablename__ = 'tasks'
    __table_args__ = (
        CheckConstraint("status IN ('todo','in_progress','blocked','done')", name='chk_tasks_status'),
        CheckConstraint("priority IN ('low','medium','high','critical')", name='chk_tasks_priority'),
    )

    task_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default='todo')
    priority: Mapped[str] = mapped_column(String(30), nullable=False, default='medium')
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    creator_id: Mapped[int] = mapped_column(Integer, ForeignKey('users.user_id', ondelete='RESTRICT'), nullable=False)
    chat_id: Mapped[int] = mapped_column(Integer, ForeignKey('chats.chat_id', ondelete='CASCADE'), nullable=False)
    source_message_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey('messages.message_id', ondelete='SET NULL'), nullable=True
    )
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    assignees: Mapped[list['TaskAssignee']] = relationship(
        'TaskAssignee', back_populates='task', cascade='all, delete-orphan'
    )

    def to_dict(self) -> dict:
        return {
            'id': self.task_id,
            'title': self.title,
            'description': self.description,
            'status': self.status,
            'priority': self.priority,
            'category': self.category,
            'creatorId': self.creator_id,
            'chatId': self.chat_id,
            'sourceMessageId': self.source_message_id,
            'dueDate': self.due_date.isoformat() if self.due_date else None,
            'version': self.version,
            'assigneeIds': [a.user_id for a in self.assignees],
            'createdAt': self.created_at.isoformat(),
            'updatedAt': self.updated_at.isoformat(),
        }


class TaskAssignee(Base):
    __tablename__ = 'taskassignees'
    __table_args__ = (UniqueConstraint('task_id', 'user_id', name='uq_taskassignees'),)

    task_assignee_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey('tasks.task_id', ondelete='CASCADE'), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False)
    assigned_by: Mapped[int] = mapped_column(Integer, ForeignKey('users.user_id', ondelete='RESTRICT'), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    task: Mapped['Task'] = relationship('Task', back_populates='assignees')
