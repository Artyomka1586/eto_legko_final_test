from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import services
from app.db import SessionLocal
from app.keitaro import KeitaroClient, KeitaroError, get_client
from app.models import Campaign, Stream, StreamOffer

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- вспомогательные -------------------------------------------------------


def _get_or_create_campaign(db: Session, kt: KeitaroClient, keitaro_id: int) -> Campaign:
    campaign = db.scalar(select(Campaign).where(Campaign.keitaro_id == keitaro_id))
    if campaign is not None:
        return campaign
    try:
        remote = kt.get_campaign(keitaro_id)
    except KeitaroError as exc:
        if exc.status == 404:
            raise HTTPException(status_code=404, detail=f"Кампания {keitaro_id} не найдена в Keitaro")
        raise HTTPException(status_code=502, detail=f"Keitaro недоступен: {exc}")
    campaign = Campaign(keitaro_id=remote["id"], name=remote.get("name") or "")
    db.add(campaign)
    db.flush()
    services.sync_campaign_streams(db, kt, campaign)
    campaign.geo = services.campaign_geo_from_streams(kt.list_streams(keitaro_id))
    db.commit()
    db.refresh(campaign)
    return campaign


def _get_stream(db: Session, campaign: Campaign, stream_id: int) -> Stream:
    stream = db.get(Stream, stream_id)
    if stream is None or stream.campaign_id != campaign.id:
        raise HTTPException(status_code=404, detail="Поток не найден")
    return stream


def _campaign_response(request: Request, campaign: Campaign, message: str | None = None, error: str | None = None):
    """HTMX-запросам отдаём тело страницы кампании, обычным — редирект (PRG)."""
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request,
            "_campaign_body.html",
            {"request": request, "campaign": campaign, "message": message, "error": error},
        )
    return RedirectResponse(f"/campaigns/{campaign.keitaro_id}", status_code=303)


def _stream_response(request: Request, stream: Stream, message: str | None = None, error: str | None = None):
    """HTMX-запросам отдаём тело страницы потока, обычным — редирект (PRG)."""
    campaign = stream.campaign
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request,
            "_stream_body.html",
            {"request": request, "stream": stream, "message": message, "error": error},
        )
    return RedirectResponse(f"/campaigns/{campaign.keitaro_id}/streams/{stream.id}", status_code=303)


# --- страницы ---------------------------------------------------------------


@router.get("/")
def index(request: Request, db: Session = Depends(get_db), error: str | None = None):
    kt = get_client()
    try:
        campaigns = kt.list_campaigns()
    except KeitaroError as exc:
        return templates.TemplateResponse(
            request,
            "index.html",
            {"request": request, "campaigns": [], "error": f"Keitaro недоступен: {exc}"},
        )
    return templates.TemplateResponse(
        request, "index.html", {"request": request, "campaigns": campaigns, "error": error}
    )


@router.post("/campaigns")
def create_campaign(
    name: str = Form(...),
    geo: str = Form(...),
    offer_id: str = Form(""),
    db: Session = Depends(get_db),
):
    kt = get_client()
    name = name.strip()
    geo = geo.strip().upper()
    offer_id = offer_id.strip()
    if not name:
        return RedirectResponse(f"/?error={quote('Укажите название кампании')}", 303)
    if len(geo) != 2 or not geo.isalpha():
        return RedirectResponse(f"/?error={quote('Гео-код: две латинские буквы, например US')}", 303)
    if not offer_id.isdigit() or int(offer_id) not in services.offer_catalog():
        return RedirectResponse(f"/?error={quote('Выберите оффер из поиска')}", 303)
    try:
        campaign = services.create_campaign(db, kt, name, geo, int(offer_id))
    except (KeitaroError, ValueError) as exc:
        return RedirectResponse(f"/?error={quote(str(exc))}", 303)
    return RedirectResponse(f"/campaigns/{campaign.keitaro_id}", 303)


@router.get("/campaigns/{keitaro_id}")
def campaign_page(request: Request, keitaro_id: int, db: Session = Depends(get_db)):
    campaign = _get_or_create_campaign(db, get_client(), keitaro_id)
    return templates.TemplateResponse(
        request, "campaign.html", {"request": request, "campaign": campaign}
    )


@router.get("/campaigns/{keitaro_id}/streams/{stream_id}")
def stream_page(request: Request, keitaro_id: int, stream_id: int, db: Session = Depends(get_db)):
    campaign = _get_or_create_campaign(db, get_client(), keitaro_id)
    stream = _get_stream(db, campaign, stream_id)
    return templates.TemplateResponse(
        request, "stream.html", {"request": request, "campaign": campaign, "stream": stream}
    )


# --- действия уровня кампании ----------------------------------------------


@router.post("/campaigns/{keitaro_id}/fetch")
def fetch_streams(keitaro_id: int, request: Request, db: Session = Depends(get_db)):
    campaign = _get_or_create_campaign(db, get_client(), keitaro_id)
    try:
        services.sync_campaign_streams(db, get_client(), campaign)
    except KeitaroError as exc:
        return _campaign_response(request, campaign, error=f"Keitaro: {exc}")
    return _campaign_response(request, campaign, message="Потоки синхронизированы с Keitaro")


@router.post("/campaigns/{keitaro_id}/streams")
def add_stream(
    keitaro_id: int,
    request: Request,
    name: str = Form(...),
    kind: str = Form("offers"),
    url: str = Form(""),
    geo: str = Form(""),
    db: Session = Depends(get_db),
):
    campaign = _get_or_create_campaign(db, get_client(), keitaro_id)
    name = name.strip()
    url = url.strip()
    geo = geo.strip().upper()
    if not name:
        return _campaign_response(request, campaign, error="Укажите название потока")
    if kind not in ("offers", "redirect"):
        return _campaign_response(request, campaign, error="Тип потока: офферный или редирект")
    if kind == "redirect" and not url:
        return _campaign_response(request, campaign, error="Для потока-редиректа укажите URL")
    if geo and (len(geo) != 2 or not geo.isalpha()):
        return _campaign_response(request, campaign, error="Гео-фильтр: две латинские буквы")
    try:
        services.add_stream(db, get_client(), campaign, name, kind, url, geo or None)
    except KeitaroError as exc:
        return _campaign_response(request, campaign, error=f"Keitaro: {exc}")
    return _campaign_response(request, campaign, message=f"Поток «{name}» добавлен в Keitaro")


# --- действия уровня потока -------------------------------------------------


@router.post("/campaigns/{keitaro_id}/offers")
def add_offer(
    keitaro_id: int,
    request: Request,
    stream_id: int = Form(...),
    offer_id: int = Form(0),
    db: Session = Depends(get_db),
):
    campaign = _get_or_create_campaign(db, get_client(), keitaro_id)
    stream = _get_stream(db, campaign, stream_id)
    if offer_id <= 0 or offer_id not in services.offer_catalog():
        return _stream_response(request, stream, error=f"Оффер {offer_id} не найден в Keitaro")
    services.add_offer(db, stream, offer_id)
    return _stream_response(
        request, stream, message=f"Оффер {offer_id} добавлен (не опубликовано — нажмите Push)"
    )


@router.post("/offers/{offer_row_id}/remove")
def remove_offer(offer_row_id: int, request: Request, db: Session = Depends(get_db)):
    offer = db.get(StreamOffer, offer_row_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="Оффер не найден")
    stream = offer.stream
    services.remove_offer(db, offer)
    return _stream_response(request, stream, message="Оффер удалён (не опубликовано)")


@router.post("/offers/{offer_row_id}/restore")
def restore_offer(offer_row_id: int, request: Request, db: Session = Depends(get_db)):
    offer = db.get(StreamOffer, offer_row_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="Оффер не найден")
    stream = offer.stream
    services.restore_offer(db, offer)
    return _stream_response(request, stream, message="Оффер возвращён (не опубликовано)")


@router.post("/offers/{offer_row_id}/pin")
def toggle_pin(offer_row_id: int, request: Request, db: Session = Depends(get_db)):
    offer = db.get(StreamOffer, offer_row_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="Оффер не найден")
    stream = offer.stream
    services.toggle_pin(db, offer)
    return _stream_response(request, stream)


@router.post("/streams/{stream_id}/push")
def push_stream(stream_id: int, request: Request, db: Session = Depends(get_db)):
    stream = db.get(Stream, stream_id)
    if stream is None:
        raise HTTPException(status_code=404, detail="Поток не найден")
    try:
        services.push_stream(db, get_client(), stream)
    except KeitaroError as exc:
        return _stream_response(request, stream, error=f"Keitaro: {exc}")
    return _stream_response(request, stream, message="Изменения опубликованы в Keitaro")


@router.post("/streams/{stream_id}/cancel")
def cancel_stream(stream_id: int, request: Request, db: Session = Depends(get_db)):
    stream = db.get(Stream, stream_id)
    if stream is None:
        raise HTTPException(status_code=404, detail="Поток не найден")
    services.cancel_stream_changes(db, stream)
    return _stream_response(request, stream, message="Несохранённые изменения отменены")


# --- API для поиска офферов --------------------------------------------------


@router.get("/api/offers")
def search_offers(q: str = ""):
    catalog = services.offer_catalog()
    query = q.strip().lower()
    matches = [
        (offer_id, name)
        for offer_id, name in catalog.items()
        if not query or query in name.lower() or query in str(offer_id)
    ]
    matches.sort(key=lambda pair: pair[1].lower())
    return JSONResponse([{"id": oid, "name": name} for oid, name in matches[:20]])
