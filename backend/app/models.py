from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "application_users"
    age_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_restricted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = {"schema": "auth"}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(20), default="active")
    display_name: Mapped[str] = mapped_column(String(100), default="")
    bank_username: Mapped[str | None] = mapped_column(String(24))
    language: Mapped[str] = mapped_column(String(5), default="ru")
    banking_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExternalIdentity(Base):
    __tablename__ = "external_identities"
    __table_args__ = (UniqueConstraint("provider", "external_id"), {"schema": "auth"})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.application_users.id", ondelete="RESTRICT"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(100))
    username: Mapped[str] = mapped_column(String(100), default="")
    avatar_hash: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OAuthState(Base):
    __tablename__ = "oauth_states"
    age_confirmed: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    __table_args__ = {"schema": "auth"}
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    redirect_uri: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purpose: Mapped[str] = mapped_column(String(10), server_default="login")
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("auth.application_users.id"))
    session_hash: Mapped[str | None] = mapped_column(String(64))


class PasswordCredential(Base):
    __tablename__ = "password_credentials"
    __table_args__ = {"schema": "auth"}
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.application_users.id"), primary_key=True)
    login: Mapped[str] = mapped_column(String(24), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = {"schema": "auth"}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth.application_users.id", ondelete="RESTRICT"), index=True
    )
    prefix: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    secret_hash: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(100))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    account_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("auth.organizations.id"))


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = {"schema": "auth"}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.application_users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("organization_id", "user_id"), {"schema": "auth"})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.organizations.id", ondelete="RESTRICT"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.application_users.id", ondelete="RESTRICT"))
    role: Mapped[str] = mapped_column(String(50), default="member")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = {"schema": "auth"}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(100))
    target_type: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[str] = mapped_column(String(100))
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MinecraftLink(Base):
    __tablename__ = "minecraft_links"
    __table_args__ = (UniqueConstraint("nickname"), {"schema": "auth"})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.application_users.id", ondelete="RESTRICT"))
    nickname: Mapped[str] = mapped_column(String(16))
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0)


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = {"schema": "auth"}
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.application_users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    public_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), server_default=text("gen_random_uuid()"))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()+interval '90 days'")
    )
    client_info: Mapped[str] = mapped_column(String(180), server_default="")
    auth_method: Mapped[str] = mapped_column(String(10), server_default="discord")


class MinecraftChallenge(Base):
    __tablename__ = "minecraft_link_challenges"
    __table_args__ = {"schema": "auth"}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.application_users.id", ondelete="RESTRICT"))
    code_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MinecraftVerifiedLink(Base):
    __tablename__ = "minecraft_verified_links"
    __table_args__ = {"schema": "auth"}
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.application_users.id"), primary_key=True)
    player_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    nickname: Mapped[str] = mapped_column(String(16))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    authentication: Mapped[str] = mapped_column(String(16), server_default="online-mode")
