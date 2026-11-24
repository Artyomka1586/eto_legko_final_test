from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Campaign(Base):
    """Локальное представление кампании Keitaro (созданной обёрткой или открытой в редакторе)."""

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    keitaro_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    geo: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    streams: Mapped[list["Stream"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", order_by="Stream.position"
    )


class Stream(Base):
    """Поток кампании. dirty = есть непубликoванные локальные изменения (жёлтое состояние)."""

    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    keitaro_stream_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer, default=0)
    has_offers: Mapped[bool] = mapped_column(Boolean, default=False)
    dirty: Mapped[bool] = mapped_column(Boolean, default=False)

    campaign: Mapped[Campaign] = relationship(back_populates="streams")
    offers: Mapped[list["StreamOffer"]] = relationship(
        back_populates="stream", cascade="all, delete-orphan", order_by="StreamOffer.id"
    )


class StreamOffer(Base):
    """Оффер потока: локальный (staging) и опубликованный в Keitarо наборы значений.

    share/status — то, что видит пользователь (staging);
    published_share — последний известный вес в Keitaro (None = оффера там сейчас нет).
    """

    __tablename__ = "stream_offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id", ondelete="CASCADE"), index=True)
    keitaro_offer_id: Mapped[int] = mapped_column(Integer, index=True)
    offer_name: Mapped[str] = mapped_column(String(255), default="")
    share: Mapped[int] = mapped_column(Integer, default=0)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | removed
    published_share: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Был ли оффер когда-либо опубликован в Keitaro. False = добавлен локально и не пушнут:
    # такая запись при удалении/отмене исчезает полностью, а не уходит в серый архив.
    ever_published: Mapped[bool] = mapped_column(Boolean, default=False)

    stream: Mapped[Stream] = relationship(back_populates="offers")
