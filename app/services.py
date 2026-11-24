"""Бизнес-логика обёртки.

Локальная БД — staging-состояние (то, что видит и меняет пользователь),
Keitaro — опубликованное состояние. Push переносит staging в Keitaro,
cancel откатывает staging к последнему опубликованному.
"""

from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.keitaro import KeitaroClient
from app.models import Campaign, Stream, StreamOffer

GOOGLE_STREAM_NAME = "Google"
OFFERS_STREAM_NAME = "Offers"


# --- каталог офферов -------------------------------------------------------


@lru_cache(maxsize=1)
def offer_catalog() -> dict[int, str]:
    """Каталог офферов Keitaro. В эмуляторе каталог read-only, поэтому кэш безопасен."""
    return {o["id"]: o["name"] for o in KeitaroClient().list_offers()}


# --- пересчёт весов --------------------------------------------------------


def recalc_weights(stream: Stream) -> None:
    """Делит (100 − сумма запиненных весов) поровну между незапиненными активными офферами.

    Остаток от деления (например, 100 на 3) уходит первым офферам по порядку.
    """
    active = [o for o in stream.offers if o.status == "active"]
    pinned_sum = sum(o.share for o in active if o.pinned)
    free = [o for o in active if not o.pinned]
    if not free:
        return
    remainder = max(0, 100 - pinned_sum)
    base, extra = divmod(remainder, len(free))
    for index, offer in enumerate(free):
        offer.share = base + (1 if index < extra else 0)


# --- часть 1: создание кампании --------------------------------------------


def create_campaign(db: Session, kt: KeitaroClient, name: str, geo: str, offer_id: int) -> Campaign:
    """Кампания с двумя потоками: (1) гео-фильтр → Google, (2) выбранный оффер."""
    domain = kt.list_domains()[0]
    group = kt.list_groups()[0]
    source = kt.list_traffic_sources()[0]
    catalog = offer_catalog()
    if offer_id not in catalog:
        raise ValueError(f"Оффер {offer_id} не найден в Keitaro")

    full_name = f"{get_settings().campaign_name_prefix}{name}"
    campaign = kt.create_campaign(
        name=full_name,
        group_id=group["id"],
        traffic_source_id=source["id"],
        domain_id=domain["id"],
    )
    google_stream = kt.create_stream(
        {
            "campaign_id": campaign["id"],
            "name": GOOGLE_STREAM_NAME,
            "schema": "redirect",
            "action_options": {"url": "https://www.google.com/"},
            "filters": [{"name": "country", "mode": "accept", "payload": [geo.upper()]}],
        }
    )
    offers_stream = kt.create_stream(
        {
            "campaign_id": campaign["id"],
            "name": OFFERS_STREAM_NAME,
            "schema": "landings",
            "offers": [{"offer_id": offer_id, "share": 100}],
        }
    )

    local = Campaign(keitaro_id=campaign["id"], name=full_name, geo=geo.upper())
    local.streams = [
        Stream(
            keitaro_stream_id=google_stream["id"],
            name=GOOGLE_STREAM_NAME,
            position=google_stream.get("position") or 0,
            has_offers=False,
            redirect_url="https://www.google.com/",
        ),
        Stream(
            keitaro_stream_id=offers_stream["id"],
            name=OFFERS_STREAM_NAME,
            position=offers_stream.get("position") or 0,
            has_offers=True,
        ),
    ]
    local.streams[1].offers = [
        StreamOffer(
            keitaro_offer_id=offer_id,
            offer_name=catalog[offer_id],
            share=100,
            published_share=100,
            status="active",
            pinned=False,
            ever_published=True,
        )
    ]
    db.add(local)
    db.commit()
    return local


# --- часть 2: синхронизация и редактор -------------------------------------


def add_stream(
    db: Session,
    kt: KeitaroClient,
    campaign: Campaign,
    name: str,
    kind: str,
    url: str | None = None,
    geo: str | None = None,
) -> Stream:
    """Новый поток кампании: офферный (пустой) или редирект на URL (с гео-фильтром)."""
    if kind == "redirect":
        payload = {
            "campaign_id": campaign.keitaro_id,
            "name": name,
            "schema": "redirect",
            "action_options": {"url": url},
        }
        if geo:
            payload["filters"] = [{"name": "country", "mode": "accept", "payload": [geo.upper()]}]
    else:
        payload = {"campaign_id": campaign.keitaro_id, "name": name, "schema": "landings", "offers": []}
    remote = kt.create_stream(payload)
    stream = Stream(
        campaign_id=campaign.id,
        keitaro_stream_id=remote["id"],
        name=name,
        position=remote.get("position") or 0,
        has_offers=(kind != "redirect"),
        redirect_url=url if kind == "redirect" else None,
    )
    db.add(stream)
    db.commit()
    return stream


def campaign_geo_from_streams(kt_streams: list[dict]) -> str | None:
    """Гео кампании берём из country-фильтра её потоков (для кампаний, созданных вне обёртки)."""
    for stream in kt_streams:
        for flt in stream.get("filters") or []:
            if flt.get("name") == "country":
                payload = flt.get("payload") or []
                if payload and isinstance(payload[0], str):
                    return payload[0].upper()
    return None


def sync_campaign_streams(db: Session, kt: KeitaroClient, campaign: Campaign) -> None:
    """Fetch Streams: подтягивает потоки и офферы из Keitaro в локальный staging.

    Правила:
    * потоков, исчезнувших из Keitaro, локально нет;
    * оффер есть в Keitaro → обновляем published_share; если поток не dirty,
      staging зеркалит опубликованное состояние;
    * оффер исчез из Keitaro → published_share=None (в UI — серый, с bring back);
    * новых офферов из Keitaro добавляем в staging с их весами;
    * если поток dirty, локальные staging-изменения не трогаем.
    """
    remote_streams = kt.list_streams(campaign.keitaro_id)
    remote_by_id = {s["id"]: s for s in remote_streams}

    for stream in db.scalars(select(Stream).where(Stream.campaign_id == campaign.id)).all():
        if stream.keitaro_stream_id not in remote_by_id:
            db.delete(stream)
    db.flush()

    for remote in sorted(remote_streams, key=lambda s: s.get("position") or 0):
        remote_offers = {o["offer_id"]: o["share"] for o in (remote.get("offers") or [])}
        redirect_url = (remote.get("action_options") or {}).get("url")
        is_offers_stream = remote.get("schema") == "landings"
        stream = db.scalar(
            select(Stream).where(
                Stream.campaign_id == campaign.id,
                Stream.keitaro_stream_id == remote["id"],
            )
        )
        if stream is None:
            stream = Stream(
                campaign_id=campaign.id,
                keitaro_stream_id=remote["id"],
                name=remote.get("name") or "",
                position=remote.get("position") or 0,
                has_offers=is_offers_stream,
                redirect_url=redirect_url,
            )
            db.add(stream)
            db.flush()
        else:
            stream.name = remote.get("name") or stream.name
            stream.position = remote.get("position") or stream.position
            stream.has_offers = is_offers_stream
            stream.redirect_url = redirect_url

        for offer in db.scalars(
            select(StreamOffer).where(StreamOffer.stream_id == stream.id)
        ).all():
            if offer.keitaro_offer_id in remote_offers:
                offer.published_share = remote_offers.pop(offer.keitaro_offer_id)
                offer.ever_published = True
                if not stream.dirty:
                    offer.status = "active"
                    offer.share = offer.published_share
            else:
                offer.published_share = None
                if not stream.dirty:
                    offer.status = "removed"
                    offer.share = 0
        for offer_id, share in remote_offers.items():
            db.add(
                StreamOffer(
                    stream_id=stream.id,
                    keitaro_offer_id=offer_id,
                    offer_name=offer_catalog().get(offer_id, str(offer_id)),
                    share=share,
                    published_share=share,
                    status="active",
                    pinned=False,
                    ever_published=True,
                )
            )
    db.commit()


def add_offer(db: Session, stream: Stream, offer_id: int) -> None:
    catalog = offer_catalog()
    existing = next((o for o in stream.offers if o.keitaro_offer_id == offer_id), None)
    if existing is not None:
        existing.status = "active"
        existing.share = 0
        existing.pinned = False
    else:
        stream.offers.append(
            StreamOffer(
                keitaro_offer_id=offer_id,
                offer_name=catalog.get(offer_id, str(offer_id)),
                share=0,
                status="active",
                pinned=False,
            )
        )
    stream.has_offers = True
    stream.dirty = True
    recalc_weights(stream)
    db.commit()


def remove_offer(db: Session, offer: StreamOffer) -> None:
    if not offer.ever_published:
        # Локальная, никогда не пушнутая запись удаляется полностью.
        stream = offer.stream
        stream.offers.remove(offer)
        stream.dirty = True
        recalc_weights(stream)
    else:
        offer.status = "removed"
        offer.share = 0
        offer.stream.dirty = True
        recalc_weights(offer.stream)
    db.commit()


def restore_offer(db: Session, offer: StreamOffer) -> None:
    offer.status = "active"
    offer.share = 0
    offer.pinned = False
    offer.stream.dirty = True
    recalc_weights(offer.stream)
    db.commit()


def toggle_pin(db: Session, offer: StreamOffer) -> None:
    offer.pinned = not offer.pinned
    if not offer.pinned:
        # снятие пина возвращает вес в общий пул — пересчитываем
        offer.stream.dirty = True
        recalc_weights(offer.stream)
    db.commit()


def push_stream(db: Session, kt: KeitaroClient, stream: Stream) -> None:
    """Публикация: заменяет офферы и веса потока в Keitaro локальным состоянием."""
    active = [o for o in stream.offers if o.status == "active"]
    kt.update_stream(
        stream.keitaro_stream_id,
        {"offers": [{"offer_id": o.keitaro_offer_id, "share": o.share} for o in active]},
    )
    for offer in stream.offers:
        if offer.status == "active":
            offer.published_share = offer.share
            offer.ever_published = True
        else:
            offer.published_share = None
    stream.dirty = False
    db.commit()


def cancel_stream_changes(db: Session, stream: Stream) -> None:
    """Откат всех несохранённых изменений к последнему опубликованному состоянию."""
    for offer in list(stream.offers):
        if offer.published_share is not None:
            offer.share = offer.published_share
            offer.status = "active"
            offer.pinned = False
        elif offer.ever_published:
            # Был в Keitaro, но удалён там — остаётся серым архивом с bring back.
            offer.status = "removed"
            offer.share = 0
            offer.pinned = False
        else:
            # Никогда не публиковался — привязка удаляется полностью.
            stream.offers.remove(offer)
    stream.dirty = False
    db.commit()
