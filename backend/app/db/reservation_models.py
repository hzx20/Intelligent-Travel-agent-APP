"""User-entered trip reservations; no provider booking or payment is performed."""
from datetime import datetime
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.database import Base
from app.db.models import now_local


class TripReservation(Base):
    __tablename__ = 'trip_reservations'
    id: Mapped[int] = mapped_column(primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey('trips.id'), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20))
    start_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    end_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item_name: Mapped[str] = mapped_column(String(160), default='')
    reference: Mapped[str] = mapped_column(String(150), default='')
    note: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(default=now_local)
