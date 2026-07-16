"""
Creative Performance MCP
========================
Meta (Facebook & Instagram) reklam KREATIFLERININ (gorsel/banner) performansini
yorumlar ve GORSELIN KENDISI uzerinden CRO odakli, uygulanabilir iyilestirme
onerileri verir. SADECE kreatif konusur; butce/hedefleme/teklif gibi konulara girmez.

Gorsel analizi API kullanmaz: banner'in kendisi arac sonucunda GORSEL olarak
dondurulur; musterinin kendi modeli (Claude/ChatGPT) gorseli gorup yorumlar.
Boylece operatore ek maliyet cikmaz.

Hem Claude hem ChatGPT'ye remote MCP olarak baglanir.  Uc nokta: /mcp
"""

import os
import httpx
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# ----------------------------------------------------------------- config
TOKEN = os.getenv("META_ACCESS_TOKEN", "")
ACCT = os.getenv("META_AD_ACCOUNT_ID", "")
VER = os.getenv("META_API_VERSION", "v21.0")
CONN = os.getenv("CONNECTOR_TOKEN", "")
DEMO = not (TOKEN and ACCT)
GRAPH = "https://graph.facebook.com"
HERE = os.path.dirname(os.path.abspath(__file__))

INSTRUCTIONS = (
    "Sen bir REKLAM KREATIFI (gorsel/banner) PERFORMANS ve CRO ANALISTISIN. "
    "Yalnizca Meta (Facebook & Instagram) reklam kreatiflerini degerlendirir, "
    "GORSELIN KENDISINI inceleyip donusum (CRO) odakli, uygulanabilir iyilestirme "
    "onerileri verirsin. Metrikleri (CTR, thumbstop, hold, CVR, CPA, ROAS, frequency) "
    "gorsel bulgularla birlestir; her oneriyi bir metrige veya net bir CRO gerekcesine "
    "bagla. KAPSAM DISI konular (butce, teklif/bidding, kampanya yapisi, hedefleme/"
    "audience, yerlesim optimizasyonu, pixel/teknik kurulum, hesap yonetimi) sorulursa "
    "kibarca 'bu konu kapsamim disinda' de ve konuyu kreatiflere geri getir."
)

# CRO degerlendirme cercevesi -- her analiz ciktisina eklenir, modeli yonlendirir.
CRO_RUBRIC = (
    "[GOREV] Ekteki banner GORSELINI ve yukaridaki metrikleri birlikte degerlendir. "
    "Su adimlarla, kisa ve aksiyon odakli yaz:\n"
    "1) HOOK & ILK IZLENIM: Ilk 1 saniyede dikkat ceken ne? Mesaj/urun net mi?\n"
    "2) GORSEL HIYERARSI: Goz sirasi dogru mu (once vaat, sonra urun, sonra CTA)? "
    "Kontrast, metin yogunlugu, okunabilirlik, marka gorunurlugu, mobilde durum.\n"
    "3) CTA & DONUSUM (CRO): CTA belirgin mi, tiklamaya davet ediyor mu? Gorseldeki "
    "vaat ile olasi landing/teklif uyumlu mu? Donusumu dusuren gorsel surtunmeler ne?\n"
    "4) METRIK BAGLANTISI: Gozlemleri metrige bagla -- dusuk thumbstop->hook zayif; "
    "yuksek CTR+dusuk CVR->gorsel vaat ile landing uyumsuz; yuksek frequency+dusen "
    "CTR->kreatif yorgunlugu, yenile.\n"
    "5) EN IYI KULLANIM SENARYOSU: Bu kreatif hangi huni asamasina (farkindalik/"
    "degerlendirme/donusum) ve hangi yerlesime (Feed / Reels / Stories; statik/video) "
    "en uygun? Nerede israf olur?\n"
    "6) ONCELIKLI ONERILER: 3-5 maddede 'sunu soyle degistir' biciminde somut, test "
    "edilebilir iyilestirme (A/B onerisi dahil). SADECE kreatif/gorsel; butce/hedefleme yok."
)

mcp = FastMCP(
    name="creative-performance-mcp",
    instructions=INSTRUCTIONS,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

# ----------------------------------------------------------------- demo data
DEMO_ROWS = [
    {"ad_id": "1202", "ad_name": "Yaz Indirimi - Statik Banner A", "impressions": 210500,
     "spend": 5620.0, "link_ctr_pct": 1.95, "ctr_all_pct": 2.19, "cpc": 1.37, "cpm": 26.7,
     "frequency": 2.1, "conversions": 190, "cvr_pct": 4.63, "cpa": 29.58, "roas": 3.4,
     "thumbstop_pct": None, "hold_rate_pct": None},
    {"ad_id": "1203", "ad_name": "Marka Bilinirlik - Karusel C", "impressions": 98000,
     "spend": 2100.0, "link_ctr_pct": 0.62, "ctr_all_pct": 0.80, "cpc": 3.44, "cpm": 21.4,
     "frequency": 5.6, "conversions": 12, "cvr_pct": 1.97, "cpa": 175.0, "roas": 0.75,
     "thumbstop_pct": None, "hold_rate_pct": None},
]


def _demo_image():
    """Demo modunda paketlenmis ornek banner'i (byte) dondurur; yoksa None."""
    p = os.path.join(HERE, "sample_banner.jpg")
    try:
        with open(p, "rb") as f:
            return f.read(), "jpeg"
    except Exception:
        return None


# ----------------------------------------------------------------- meta client
FIELDS = ("ad_id,ad_name,impressions,inline_link_clicks,ctr,inline_link_click_ctr,"
          "cpc,cpm,spend,frequency,actions,action_values,video_play_actions,"
          "video_p100_watched_actions")


def _graph(path, params):
    params = {**params, "access_token": TOKEN}
    r = httpx.get(f"{GRAPH}/{VER}/{path}", params=params, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"Meta API {r.status_code}: {r.text[:300]}")
    return r.json()


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _normalize(row):
    imp = _num(row.get("impressions"))
    lc = _num(row.get("inline_link_clicks"))
    sp = _num(row.get("spend"))
    conv = rev = 0.0
    for a in row.get("actions", []) or []:
        if "purchase" in a.get("action_type", ""):
            conv += _num(a.get("value"))
    for a in row.get("action_values", []) or []:
        if "purchase" in a.get("action_type", ""):
            rev += _num(a.get("value"))

    def fv(k):
        arr = row.get(k) or []
        return _num(arr[0].get("value")) if arr else 0.0

    plays, p100 = fv("video_play_actions"), fv("video_p100_watched_actions")
    return {
        "ad_id": row.get("ad_id"), "ad_name": row.get("ad_name"),
        "impressions": int(imp), "spend": round(sp, 2),
        "link_ctr_pct": round(_num(row.get("inline_link_click_ctr")), 3) or None,
        "ctr_all_pct": round(_num(row.get("ctr")), 3) or None,
        "cpc": round(_num(row.get("cpc")), 2) or None,
        "cpm": round(_num(row.get("cpm")), 2) or None,
        "frequency": round(_num(row.get("frequency")), 2) or None,
        "conversions": int(conv),
        "cvr_pct": round(conv / lc * 100, 2) if lc else None,
        "cpa": round(sp / conv, 2) if conv else None,
        "roas": round(rev / sp, 2) if sp else None,
        "thumbstop_pct": round(plays / imp * 100, 2) if imp else None,
        "hold_rate_pct": round(p100 / plays * 100, 2) if plays else None,
    }


def get_insights(date_preset="last_14d", ad_id=None):
    if DEMO:
        if ad_id:
            return [r for r in DEMO_ROWS if r["ad_id"] == ad_id] or [DEMO_ROWS[0]]
        return list(DEMO_ROWS)
    acct = ACCT if ACCT.startswith("act_") else "act_" + ACCT
    path = f"{ad_id}/insights" if ad_id else f"{acct}/insights"
    data = _graph(path, {"level": "ad", "fields": FIELDS,
                         "date_preset": date_preset, "limit": 200})
    return [_normalize(r) for r in data.get("data", [])]


def get_image(ad_id):
    """(bytes, format) dondurur; yoksa None. Gorsel araca eklenip modele gonderilir."""
    if DEMO:
        return _demo_image()
    data = _graph(ad_id, {"fields": "creative{image_url,thumbnail_url,asset_feed_spec{images}}"})
    cr = data.get("creative", {}) or {}
    url = cr.get("image_url")
    if not url:
        imgs = (cr.get("asset_feed_spec", {}) or {}).get("images") or []
        if imgs:
            url = imgs[0].get("url")
    url = url or cr.get("thumbnail_url")
    if not url:
        return None
    try:
        r = httpx.get(url, timeout=45, follow_redirects=True)
        r.raise_for_status()
        ct = r.headers.get("content-type", "image/jpeg").split(";")[0]
        fmt = {"image/jpeg": "jpeg", "image/png": "png", "image/gif": "gif",
               "image/webp": "webp"}.get(ct, "jpeg")
        return r.content, fmt
    except Exception:
        return None


# ----------------------------------------------------------------- format
def fmt(m):
    def g(k, s=""):
        v = m.get(k)
        return f"{v}{s}" if v is not None else "-"
    return (f"- {m.get('ad_name') or m.get('ad_id')} (id:{m.get('ad_id')})\n"
            f"  Gosterim:{g('impressions')} Harcama:{g('spend')} | CTR(link):{g('link_ctr_pct','%')} "
            f"CTR:{g('ctr_all_pct','%')} CPC:{g('cpc')} CPM:{g('cpm')}\n"
            f"  Thumbstop:{g('thumbstop_pct','%')} Hold:{g('hold_rate_pct','%')} Freq:{g('frequency')} | "
            f"Donusum:{g('conversions')} CVR:{g('cvr_pct','%')} CPA:{g('cpa')} ROAS:{g('roas','x')}")


# ----------------------------------------------------------------- tools
@mcp.tool()
def list_creatives(date_preset: str = "last_14d"):
    """Meta reklam hesabindaki kreatifleri (banner) metrikleriyle listeler, link CTR'a
    gore siralar. Kullanici hangi gorseli inceleyecegine karar vermeden once bunu cagir."""
    try:
        rows = get_insights(date_preset)
    except Exception as e:
        return f"HATA: {e}"
    if not rows:
        return "Bu tarih araliginda kreatif verisi yok."
    rows = sorted(rows, key=lambda r: (r.get("link_ctr_pct") or 0), reverse=True)
    tag = " [DEMO]" if DEMO else ""
    return f"{len(rows)} kreatif{tag} ({date_preset}), link CTR'a gore sirali:\n\n" + \
        "\n\n".join(fmt(r) for r in rows)


@mcp.tool()
def analyze_creative(ad_id: str, date_preset: str = "last_14d"):
    """Tek kreatifi derin analiz eder: metrikleri ceker VE banner'in kendisini GORSEL
    olarak arac sonucuna ekler; sen (Claude/ChatGPT) gorseli gorup CRO odakli, uygulanabilir
    oneriler verirsin. ad_id: list_creatives'teki reklam id'si."""
    try:
        ins = get_insights(date_preset, ad_id)
        img = get_image(ad_id)
    except Exception as e:
        return f"HATA: {e}"
    m = ins[0] if ins else {"ad_id": ad_id}
    parts = ["=== METRIKLER ===\n" + fmt(m)]
    if img:
        parts.append("\n=== BANNER GORSELI (asagida) — bu gorseli incele ===")
        parts.append(Image(data=img[0], format=img[1]))
    else:
        note = "" if DEMO else " (gercek gorsel bulunamadi.)"
        parts.append("\n(Bu kreatif icin gorsel eklenemedi." + note + ")")
    parts.append("\n" + CRO_RUBRIC)
    return parts


@mcp.tool()
def compare_creatives(ad_ids: list, date_preset: str = "last_14d"):
    """2-5 kreatifi yan yana karsilastirir: her birinin metriklerini ve GORSELINI arac
    sonucuna ekler; sen kazanani secip hem metriksel hem gorsel/CRO nedenlerini aciklar,
    zayif olana somut iyilestirme onerirsin. ad_ids: reklam id listesi (en fazla 5)."""
    ad_ids = ad_ids[:5]
    if len(ad_ids) < 2:
        return "Karsilastirma icin en az 2 reklam id'si gerekir."
    parts = [f"{len(ad_ids)} kreatif karsilastirmasi ({date_preset}):"]
    for aid in ad_ids:
        try:
            ins = get_insights(date_preset, aid)
            img = get_image(aid)
        except Exception as e:
            parts.append(f"\n[{aid}] HATA: {e}")
            continue
        m = ins[0] if ins else {"ad_id": aid}
        parts.append("\n----------------------------------------\n" + fmt(m))
        if img:
            parts.append(Image(data=img[0], format=img[1]))
    parts.append("\n" + CRO_RUBRIC +
                 "\n\nSON OLARAK: kazanan kreatifi sec; metriksel + gorsel/CRO nedenlerini "
                 "ozetle ve zayif kreatif icin en yuksek etkili 2-3 iyilestirmeyi belirt.")
    return parts


# ----------------------------------------------------------------- app
class BearerAuth(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if CONN and request.headers.get("authorization", "") != f"Bearer {CONN}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


app = mcp.streamable_http_app()
app.add_middleware(BearerAuth)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
