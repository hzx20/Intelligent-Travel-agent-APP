"""Explicit, private visited-place records, independent of itinerary item lifetime."""
from datetime import datetime

from sqlalchemy import Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models import now_local


class TripFootprint(Base):
    __tablename__ = 'trip_footprints'
    __table_args__ = (UniqueConstraint('user_id', 'trip_id', 'item_id', name='uq_user_trip_item_footprint'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey('trips.id'), index=True)
    # Keep an item snapshot if the itinerary entry is later edited or deleted.
    item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    day_number: Mapped[int] = mapped_column(Integer)
    place_name: Mapped[str] = mapped_column(String(160))
    address: Mapped[str] = mapped_column(String(300), default='')
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    amap_id: Mapped[str] = mapped_column(String(100), default='')
    city: Mapped[str] = mapped_column(String(100), default='')
    visited_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    note: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(default=now_local)
    photos: Mapped[list['FootprintPhoto']] = relationship(cascade='all, delete-orphan', order_by='FootprintPhoto.id', lazy='selectin')


class FootprintPhoto(Base):
    __tablename__ = 'footprint_photos'
    id: Mapped[int] = mapped_column(primary_key=True)
    footprint_id: Mapped[int] = mapped_column(ForeignKey('trip_footprints.id'), index=True)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    created_at: Mapped[datetime] = mapped_column(default=now_local)
