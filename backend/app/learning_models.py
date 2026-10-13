"""Durable course data; registered in the shared SQLAlchemy metadata."""
from datetime import datetime
from typing import Optional
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db.models import Base

class LearningProfile(Base):
    __tablename__ = 'learning_profiles'
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id',ondelete='CASCADE'),primary_key=True)
    goal: Mapped[str] = mapped_column(String(16))
    level: Mapped[str] = mapped_column(String(4))
    completed_count: Mapped[int] = mapped_column(Integer,default=0)
    checkpoint_count: Mapped[int] = mapped_column(Integer,default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)

class LearningLesson(Base):
    __tablename__ = 'learning_lessons'
    __table_args__ = (UniqueConstraint('user_id','ordinal',name='uq_learning_ordinal'),)
    id: Mapped[str] = mapped_column(String(36),primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger,ForeignKey('users.id',ondelete='CASCADE'))
    ordinal: Mapped[int] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(String(4))
    topic_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16),default='prepared')
    step_index: Mapped[int] = mapped_column(Integer,default=0)
    content: Mapped[dict] = mapped_column(JSON)
    answers: Mapped[dict] = mapped_column(JSON)
    conversation: Mapped[list] = mapped_column(JSON)
    used_seconds: Mapped[int] = mapped_column(Integer,default=0)
    session_id: Mapped[Optional[int]] = mapped_column(BigInteger,ForeignKey('sessions.id',ondelete='SET NULL'))
    generation_status: Mapped[str] = mapped_column(String(16),default='pending')
    generation_attempts: Mapped[int] = mapped_column(Integer,default=0)
    generation_lease: Mapped[Optional[datetime]] = mapped_column(DateTime)
    instance_id: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

class LearningSkill(Base):
    __tablename__ = 'learning_skills'
    user_id: Mapped[int] = mapped_column(BigInteger,ForeignKey('users.id',ondelete='CASCADE'),primary_key=True)
    skill_key: Mapped[str] = mapped_column(String(64),primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer,default=0)
    correct_count: Mapped[int] = mapped_column(Integer,default=0)
    evidence: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime)

class LearningCheckpoint(Base):
    __tablename__ = 'learning_checkpoints'
    __table_args__ = (UniqueConstraint('user_id','after_count',name='uq_learning_checkpoint'),)
    id: Mapped[str] = mapped_column(String(36),primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger,ForeignKey('users.id',ondelete='CASCADE'))
    after_count: Mapped[int] = mapped_column(Integer)
    level_before: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(16),default='active')
    questions: Mapped[list] = mapped_column(JSON)
    answers: Mapped[dict] = mapped_column(JSON)
    result: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
