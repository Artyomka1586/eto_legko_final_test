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
            raise HTTPException(status_code=404, detail=f"Campaign {keitaro_id} not found in Keitaro")
        raise HTTPException(status_code=502, detail=f"Keitaro unavailable: {exc}")
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
        raise HTTPException(status_code=404, detail="Stream not found")
    return stream


def _campaign_ctx(
    request: Request,
    campaign: Campaign,
    message: str | None = None,
    error: str | None = None,
) -> dict:
    """Контекст страницы кампании: локальная запись + параметры кампании из Keitaro."""
    try:
        kt_campaign = get_client().get_campaign(campaign.keitaro_id)
    except KeitaroError:
        kt_campaign = None
    try:
        catalogs = services.campaign_catalogs()
    except KeitaroError:
        catalogs = None
    return {
        "request": request,
        "campaign": campaign,
        "message": message,
        "error": error,
        "kt_campaign": kt_campaign,
        "catalogs": catalogs,
    }


def _campaign_response(request: Request, campaign: Campaign, message: str | None = None, error: str | None = None):
    """HTMX-запросам отдаём тело страницы кампании, обычным — редирект (PRG)."""
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request,
            "_campaign_body.html",
            _campaign_ctx(request, campaign, message, error),
        )
    return RedirectResponse(f"/campaigns/{campaign.keitaro_id}", status_code=303)


def _offers_response(request: Request, campaign: Campaign, message: str | None = None, error: str | None = None):
    """HTMX-запросам отдаём тело редактора офферов, обычным — редирект (PRG)."""
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request,
            "_offers_body.html",
            {"request": request, "campaign": campaign, "message": message, "error": error},
        )
    return RedirectResponse(f"/campaigns/{campaign.keitaro_id}/offers", status_code=303)


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
            {"request": request, "campaigns": [], "error": f"Keitaro unavailable: {exc}"},
        )
    return templates.TemplateResponse(
        request, "index.html", {"request": request, "campaigns": campaigns, "error": error}
    )


@router.post("/campaigns")
def create_campaign(
    name: str = Form(""),
    geo: str = Form(""),
    offer_id: str = Form(""),
    db: Session = Depends(get_db),
):
    kt = get_client()
    name = name.strip()
    geo = geo.strip().upper()
    offer_id = offer_id.strip()
    if not name:
        return RedirectResponse(f"/?error={quote('Enter a campaign name')}", 303)
    if len(geo) != 2 or not geo.isalpha():
        return RedirectResponse(f"/?error={quote('Country code: two Latin letters, e.g. US')}", 303)
    if not offer_id.isdigit() or int(offer_id) not in services.offer_catalog():
        return RedirectResponse(f"/?error={quote('Pick an offer from the search')}", 303)
    try:
        campaign = services.create_campaign(db, kt, name, geo, int(offer_id))
    except (KeitaroError, ValueError) as exc:
        return RedirectResponse(f"/?error={quote(str(exc))}", 303)
    return RedirectResponse(f"/campaigns/{campaign.keitaro_id}", 303)


@router.get("/campaigns/{keitaro_id}")
def campaign_page(request: Request, keitaro_id: int, db: Session = Depends(get_db)):
    campaign = _get_or_create_campaign(db, get_client(), keitaro_id)
    return templates.TemplateResponse(request, "campaign.html", _campaign_ctx(request, campaign))


@router.get("/campaigns/{keitaro_id}/offers")
def offers_page(request: Request, keitaro_id: int, db: Session = Depends(get_db)):
    campaign = _get_or_create_campaign(db, get_client(), keitaro_id)
    return templates.TemplateResponse(
        request, "offers.html", {"request": request, "campaign": campaign}
    )


# --- действия уровня кампании ----------------------------------------------


@router.post("/campaigns/{keitaro_id}/fetch")
def fetch_streams(
    keitaro_id: int,
    request: Request,
    view: str = "campaign",
    db: Session = Depends(get_db),
):
    campaign = _get_or_create_campaign(db, get_client(), keitaro_id)
    message = None
    error = None
    try:
        services.sync_campaign_streams(db, get_client(), campaign)
        message = "Streams synced with Keitaro"
    except KeitaroError as exc:
        error = f"Keitaro: {exc}"
    if view == "offers":
        return _offers_response(request, campaign, message=message, error=error)
    return _campaign_response(request, campaign, message=message, error=error)


@router.post("/campaigns/{keitaro_id}/streams")
def add_stream(
    keitaro_id: int,
    request: Request,
    name: str = Form(""),
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
        return _campaign_response(request, campaign, error="Enter a stream name")
    if kind not in ("offers", "redirect"):
        return _campaign_response(request, campaign, error="Stream type: offers or redirect")
    if kind == "redirect" and not url:
        return _campaign_response(request, campaign, error="Redirect streams require a URL")
    if geo and (len(geo) != 2 or not geo.isalpha()):
        return _campaign_response(request, campaign, error="Geo filter: two Latin letters")
    try:
        services.add_stream(db, get_client(), campaign, name, kind, url, geo or None)
    except KeitaroError as exc:
        return _campaign_response(request, campaign, error=f"Keitaro: {exc}")
    return _campaign_response(request, campaign, message=f'Stream "{name}" added to Keitaro')


@router.post("/streams/{stream_id}/delete")
def delete_stream(stream_id: int, request: Request, db: Session = Depends(get_db)):
    stream = db.get(Stream, stream_id)
    if stream is None:
        raise HTTPException(status_code=404, detail="Stream not found")
    campaign = stream.campaign
    name = stream.name
    try:
        services.delete_stream(db, get_client(), stream)
    except KeitaroError as exc:
        return _campaign_response(request, campaign, error=f"Keitaro: {exc}")
    return _campaign_response(request, campaign, message=f'Stream "{name}" deleted from Keitaro')


@router.post("/campaigns/{keitaro_id}/params")
def save_campaign_params(
    keitaro_id: int,
    request: Request,
    domain_id: int = Form(...),
    group_id: int = Form(...),
    traffic_source_id: int = Form(...),
    db: Session = Depends(get_db),
):
    campaign = _get_or_create_campaign(db, get_client(), keitaro_id)
    try:
        get_client().update_campaign(
            keitaro_id,
            domain_id=domain_id,
            group_id=group_id,
            traffic_source_id=traffic_source_id,
        )
    except KeitaroError as exc:
        return _campaign_response(request, campaign, error=f"Keitaro: {exc}")
    return _campaign_response(request, campaign, message="Campaign settings updated in Keitaro")



# --- действия уровня потока (редактор офферов) ------------------------------


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
        return _offers_response(request, campaign, error=f"Offer {offer_id} not found in Keitaro")
    services.add_offer(db, stream, offer_id)
    return _offers_response(
        request, campaign, message=f"Offer {offer_id} added (not published yet — click Push)"
    )


@router.post("/offers/{offer_row_id}/remove")
def remove_offer(offer_row_id: int, request: Request, db: Session = Depends(get_db)):
    offer = db.get(StreamOffer, offer_row_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="Offer not found")
    campaign = offer.stream.campaign
    services.remove_offer(db, offer)
    return _offers_response(request, campaign, message="Offer removed (not published yet)")


@router.post("/offers/{offer_row_id}/restore")
def restore_offer(offer_row_id: int, request: Request, db: Session = Depends(get_db)):
    offer = db.get(StreamOffer, offer_row_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="Offer not found")
    campaign = offer.stream.campaign
    services.restore_offer(db, offer)
    return _offers_response(request, campaign, message="Offer restored (not published yet)")


@router.post("/offers/{offer_row_id}/pin")
def toggle_pin(offer_row_id: int, request: Request, db: Session = Depends(get_db)):
    offer = db.get(StreamOffer, offer_row_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="Offer not found")
    campaign = offer.stream.campaign
    services.toggle_pin(db, offer)
    return _offers_response(request, campaign)


@router.post("/streams/{stream_id}/push")
def push_stream(stream_id: int, request: Request, db: Session = Depends(get_db)):
    stream = db.get(Stream, stream_id)
    if stream is None:
        raise HTTPException(status_code=404, detail="Stream not found")
    campaign = stream.campaign
    try:
        services.push_stream(db, get_client(), stream)
    except KeitaroError as exc:
        return _offers_response(request, campaign, error=f"Keitaro: {exc}")
    return _offers_response(request, campaign, message="Changes published to Keitaro")


@router.post("/streams/{stream_id}/cancel")
def cancel_stream(stream_id: int, request: Request, db: Session = Depends(get_db)):
    stream = db.get(Stream, stream_id)
    if stream is None:
        raise HTTPException(status_code=404, detail="Stream not found")
    campaign = stream.campaign
    services.cancel_stream_changes(db, stream)
    return _offers_response(request, campaign, message="Unsaved changes reverted")


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
