"""Юнит-тесты пересчёта весов: чистая логика, без БД и сети."""

from app.models import Stream, StreamOffer
from app.services import recalc_weights


def make_stream(specs):
    """specs: список кортежей (share, pinned, status)."""
    stream = Stream(
        id=1, campaign_id=1, keitaro_stream_id=1, name="s", position=1,
        has_offers=True, dirty=False,
    )
    stream.offers = [
        StreamOffer(
            id=i + 1,
            stream_id=1,
            keitaro_offer_id=100 + i,
            offer_name=f"offer {i}",
            share=share,
            pinned=pinned,
            status=status,
        )
        for i, (share, pinned, status) in enumerate(specs)
    ]
    return stream


def shares(stream):
    return [offer.share for offer in stream.offers]


def test_two_offers_split_evenly():
    stream = make_stream([(0, False, "active"), (0, False, "active")])
    recalc_weights(stream)
    assert shares(stream) == [50, 50]


def test_three_offers_remainder_goes_to_first():
    stream = make_stream([(0, False, "active")] * 3)
    recalc_weights(stream)
    assert shares(stream) == [34, 33, 33]


def test_pin_34_then_add_fourth():
    """3 оффера -> 34/33/33; пиним 34, добавляем четвёртый -> 34/22/22/22."""
    stream = make_stream(
        [(34, True, "active"), (33, False, "active"), (33, False, "active"), (0, False, "active")]
    )
    recalc_weights(stream)
    assert shares(stream) == [34, 22, 22, 22]


def test_two_pins_remainder_split():
    """Запинены 50 и 25 — двое свободных делят 25."""
    stream = make_stream(
        [(50, True, "active"), (25, True, "active"), (0, False, "active"), (0, False, "active")]
    )
    recalc_weights(stream)
    assert shares(stream) == [50, 25, 13, 12]


def test_removed_offers_do_not_participate():
    stream = make_stream([(0, False, "removed"), (0, False, "active"), (0, False, "active")])
    recalc_weights(stream)
    assert shares(stream) == [0, 50, 50]


def test_all_pinned_new_offer_gets_zero():
    stream = make_stream([(50, True, "active"), (50, True, "active"), (0, False, "active")])
    recalc_weights(stream)
    assert shares(stream) == [50, 50, 0]


def test_no_free_offers_changes_nothing():
    stream = make_stream([(50, True, "active"), (50, True, "active")])
    recalc_weights(stream)
    assert shares(stream) == [50, 50]


class _Db:
    """toggle_pin нужен только db.commit() — БД здесь не требуется."""

    def commit(self):
        pass


def test_unpin_without_weight_change_keeps_stream_clean():
    """34/33/33, пин 34 снят: пересчёт даёт те же веса — поток не «грязный»."""
    stream = make_stream([(34, True, "active"), (33, False, "active"), (33, False, "active")])
    from app.services import toggle_pin

    toggle_pin(_Db(), stream.offers[0])
    assert shares(stream) == [34, 33, 33]
    assert stream.dirty is False


def test_unpin_with_weight_change_marks_stream_dirty():
    """34/22/22/22 (34 запинен): снятие пина пересчитывает всех поровну — «грязный»."""
    stream = make_stream(
        [(34, True, "active"), (22, False, "active"), (22, False, "active"), (22, False, "active")]
    )
    from app.services import toggle_pin

    toggle_pin(_Db(), stream.offers[0])
    assert shares(stream) == [25, 25, 25, 25]
    assert stream.dirty is True
