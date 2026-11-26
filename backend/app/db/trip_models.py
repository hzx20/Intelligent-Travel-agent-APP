"""Editable trips, separate from immutable AI planning snapshots."""
from datetime import datetime, timezone

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models import now_local


class Trip(Base):
    __tablename__ = "trips"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(100))
    destination: Mapped[str] = mapped_column(String(100), default="")
    start_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="planning")
    walking_preferences: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}', nullable=False)
    budget_review: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}', nullable=False)
    source_plan_id: Mapped[int | None] = mapped_column(ForeignKey("ai_plans.id"), unique=True, nullable=True)
    creation_requests: Mapped[list['TripCreationRequest']] = relationship(back_populates='trip')
    plan_conversions: Mapped[list['PlanTripConversion']] = relationship(back_populates='trip')
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(default=now_local)
    updated_at: Mapped[datetime] = mapped_column(default=now_local, onupdate=now_local)
    days: Mapped[list["TripDay"]] = relationship(cascade="all, delete-orphan", order_by="TripDay.day_number")
    places: Mapped[list["TripPlace"]] = relationship(cascade="all, delete-orphan", order_by="TripPlace.id")


class TripDay(Base):
    __tablename__ = "trip_days"
    __table_args__ = (UniqueConstraint("trip_id", "day_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id"), index=True)
    day_number: Mapped[int] = mapped_column(Integer)
    theme: Mapped[str] = mapped_column(String(100), default="")
    items: Mapped[list["TripItem"]] = relationship(cascade="all, delete-orphan", order_by="TripItem.sort_order")
    route_preferences: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}', nullable=False)


class TripItem(Base):
    __tablename__ = "trip_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    day_id: Mapped[int] = mapped_column(ForeignKey("trip_days.id"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(30), default="地点")
    start_time: Mapped[str] = mapped_column(String(5), default="")
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    address: Mapped[str] = mapped_column(String(300), default="")
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    amap_id: Mapped[str] = mapped_column(String(100), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    activity: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(200), default="")
    location_candidates: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default='[]', nullable=False)


class TripPlace(Base):
    __tablename__ = "trip_places"
    id: Mapped[int] = mapped_column(primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    address: Mapped[str] = mapped_column(String(300), default="")
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    amap_id: Mapped[str] = mapped_column(String(100), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(200), default="")


class TripProposal(Base):
    __tablename__ = "trip_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id"), index=True)
    base_version: Mapped[int] = mapped_column(Integer)
    request: Mapped[str] = mapped_column(String(500))
    operations_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(default=now_local)


class TripAiJob(Base):
    __tablename__ = "trip_ai_jobs"
    __table_args__ = (UniqueConstraint('user_id', 'trip_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey('trips.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    request: Mapped[str] = mapped_column(String(500))
    snapshot_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default='queued', index=True)
    lease_token: Mapped[str] = mapped_column(String(32), default='')
    lease_until: Mapped[datetime | None] = mapped_column(nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey('trip_proposals.id'), nullable=True)
    error: Mapped[str] = mapped_column(String(200), default='')
    updated_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class TripCreationRequest(Base):
    """Atomically map an account's creation attempt to its resulting trip."""
    __tablename__ = 'trip_creation_requests'
    __table_args__ = (UniqueConstraint('user_id', 'request_id', name='uq_trip_creation_request'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    payload_hash: Mapped[str] = mapped_column(String(64))
    trip_id: Mapped[int | None] = mapped_column(ForeignKey('trips.id', ondelete='SET NULL'), nullable=True)
    trip: Mapped[Trip | None] = relationship(back_populates='creation_requests')


class PlanTripConversion(Base):
    """Keep the source identity even if the resulting trip is removed."""
    __tablename__ = 'plan_trip_conversions'
    __table_args__ = (UniqueConstraint('user_id', 'plan_revision', name='uq_plan_trip_conversion'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    plan_id: Mapped[int] = mapped_column(Integer)
    plan_revision: Mapped[str] = mapped_column(String(36))
    source_version: Mapped[int] = mapped_column(Integer)
    trip_id: Mapped[int | None] = mapped_column(ForeignKey('trips.id', ondelete='SET NULL'), nullable=True)
    trip: Mapped[Trip | None] = relationship(back_populates='plan_conversions')
