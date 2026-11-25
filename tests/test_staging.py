"""Интеграционные тесты staging-логики: in-memory SQLite, Keitaro-клиент — заглушка."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 — регистрация таблиц в метаданных
from app import services
from app.db import Base
from app.keitaro import KeitaroError
from app.models import Campaign, Stream, StreamOffer

CATALOG = {18001: "offer 1", 18002: "offer 2", 18003: "offer 3"}


@pytest.fixture(autouse=True)
def fake_catalog(monkeypatch):
    """Все тесты модуля работают с подменённым каталогом — сеть не нужна."""
    monkeypatch.setattr(services, "offer_catalog", lambda: CATALOG)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


class FakeKeitaro:
    """Заглушка Admin API: успех по умолчанию, опционально падает на потоке с именем fail_on_stream."""

    def __init__(self, fail_on_stream=None):
        self.fail_on_stream = fail_on_stream
        self.deleted_campaigns = []
        self.updated_streams = {}
        self.next_stream_id = 901

    def list_domains(self):
        return [{"id": 1}]

    def list_groups(self):
        return [{"id": 2}]

    def list_traffic_sources(self):
        return [{"id": 3}]

    def create_campaign(self, **fields):
        return {"id": 500, "name": fields.get("name", "")}

    def create_stream(self, payload):
        if payload["name"] == self.fail_on_stream:
            raise KeitaroError(500, "keitaro is down")
        self.next_stream_id += 1
        return {"id": self.next_stream_id, "position": self.next_stream_id - 900}

    def update_stream(self, stream_id, payload):
        self.updated_streams[stream_id] = payload

    def delete_campaign(self, campaign_id):
        self.deleted_campaigns.append(campaign_id)


def make_campaign_with_stream(db):
    campaign = Campaign(keitaro_id=500, name="test_campaign", geo="US")
    stream = Stream(
        campaign=campaign, keitaro_stream_id=901, name="Offers", position=1, has_offers=True
    )
    stream.offers = [
        StreamOffer(
            keitaro_offer_id=18001,
            offer_name="offer 1",
            share=100,
            published_share=100,
            ever_published=True,
        )
    ]
    db.add(campaign)
    db.commit()
    return campaign, stream


def test_add_offer_marks_dirty_and_recalculates(db):
    _, stream = make_campaign_with_stream(db)
    services.add_offer(db, stream, 18002)
    assert stream.dirty is True
    assert [offer.share for offer in stream.offers] == [50, 50]
    added = stream.offers[1]
    assert added.ever_published is False and added.published_share is None


def test_remove_never_published_deletes_row(db):
    _, stream = make_campaign_with_stream(db)
    services.add_offer(db, stream, 18002)
    services.remove_offer(db, stream.offers[1])
    assert [offer.keitaro_offer_id for offer in stream.offers] == [18001]
    assert stream.offers[0].share == 100


def test_remove_published_archives(db):
    _, stream = make_campaign_with_stream(db)
    published = stream.offers[0]
    services.remove_offer(db, published)
    assert published.status == "removed" and published.share == 0
    assert published.ever_published is True
    assert db.get(StreamOffer, published.id) is not None


def test_cancel_restores_published_and_deletes_local(db):
    _, stream = make_campaign_with_stream(db)
    services.add_offer(db, stream, 18002)            # локальное, непушнутое
    services.remove_offer(db, stream.offers[0])      # опубликованное -> серый архив
    services.cancel_stream_changes(db, stream)
    assert [offer.keitaro_offer_id for offer in stream.offers] == [18001]
    assert stream.offers[0].status == "active"
    assert stream.offers[0].share == 100
    assert stream.dirty is False


def test_cancel_restores_pin_made_before_push(db):
    """Пин, сделанный до Push, выживает после remove + Cancel (пин — часть сохранённого)."""
    _, stream = make_campaign_with_stream(db)
    services.toggle_pin(db, stream.offers[0])
    services.add_offer(db, stream, 18002)
    services.push_stream(db, FakeKeitaro(), stream)  # опубликовано: 100 (пин) / 0
    services.remove_offer(db, stream.offers[1])      # удаление без сохранения
    services.cancel_stream_changes(db, stream)
    assert stream.offers[0].pinned is True
    assert stream.offers[0].share == 100
    assert [offer.status for offer in stream.offers] == ["active", "active"]
    assert [offer.share for offer in stream.offers] == [100, 0]


def test_cancel_reverts_unpin_made_after_push(db):
    """Анпин после Push — несохранённое изменение, Cancel возвращает пин на место."""
    _, stream = make_campaign_with_stream(db)
    services.toggle_pin(db, stream.offers[0])
    services.add_offer(db, stream, 18002)
    services.push_stream(db, FakeKeitaro(), stream)
    services.toggle_pin(db, stream.offers[0])         # анпин без сохранения
    services.cancel_stream_changes(db, stream)
    assert stream.offers[0].pinned is True


def test_push_publishes_active_offers(db):
    _, stream = make_campaign_with_stream(db)
    services.add_offer(db, stream, 18002)
    fake = FakeKeitaro()
    services.push_stream(db, fake, stream)
    assert fake.updated_streams[901] == {
        "offers": [{"offer_id": 18001, "share": 50}, {"offer_id": 18002, "share": 50}]
    }
    assert all(offer.published_share == 50 for offer in stream.offers)
    assert all(offer.ever_published for offer in stream.offers)
    assert stream.dirty is False


def test_push_skips_removed_offers(db):
    _, stream = make_campaign_with_stream(db)
    services.add_offer(db, stream, 18002)
    services.remove_offer(db, stream.offers[0])      # серый архив
    fake = FakeKeitaro()
    services.push_stream(db, fake, stream)
    assert fake.updated_streams[901] == {"offers": [{"offer_id": 18002, "share": 100}]}
    assert stream.offers[0].published_share is None  # архив без опубликованного состояния


def test_pin_freezes_share(db):
    _, stream = make_campaign_with_stream(db)
    services.add_offer(db, stream, 18002)            # 50/50
    services.toggle_pin(db, stream.offers[0])        # пин 50
    services.add_offer(db, stream, 18003)            # 50/25/25
    assert [offer.share for offer in stream.offers] == [50, 25, 25]


def test_unpin_recalculates(db):
    _, stream = make_campaign_with_stream(db)
    services.add_offer(db, stream, 18002)
    services.toggle_pin(db, stream.offers[0])        # пин 50
    services.toggle_pin(db, stream.offers[0])        # анпин -> пересчёт
    assert [offer.share for offer in stream.offers] == [50, 50]


def test_create_campaign_creates_two_streams(db):
    fake = FakeKeitaro()
    campaign = services.create_campaign(db, fake, "ok", "DE", 18001)
    assert campaign.name == "test_adulybin_ok"
    assert campaign.geo == "DE"
    google, offers = campaign.streams
    assert google.name == "Google" and google.has_offers is False
    assert google.redirect_url == "https://www.google.com/"
    assert offers.offers[0].keitaro_offer_id == 18001
    assert offers.offers[0].share == 100 and offers.offers[0].ever_published


def test_create_campaign_compensates_on_failure(db):
    """Keitaro упал посреди создания — кампания откатывается, локально ничего не остаётся."""
    fake = FakeKeitaro(fail_on_stream="Offers")
    with pytest.raises(KeitaroError):
        services.create_campaign(db, fake, "broken", "US", 18001)
    assert fake.deleted_campaigns == [500]
    assert db.scalars(select(Campaign)).all() == []
