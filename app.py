"""
Creative Performance MCP  (fastmcp v2 + Meta/Facebook OAuth, cok-kiracili)
=========================================================================
Meta (Facebook & Instagram) reklam KREATIFLERININ performansini ve GORSELINI
CRO odakli degerlendirir. SADECE kreatif konusur.

- Her musteri Claude/ChatGPT'de tek URL'yi ekler, Facebook ile giris yapar,
  ads_read izni verir; sunucu O MUSTERININ token'iyla kendi reklam hesabini okur.
- Gorsel analizi API kullanmaz: banner arac sonucunda GORSEL olarak doner,
  musterinin kendi modeli yorumlar (operatore ek maliyet yok).

Auth, META_APP_ID + META_APP_SECRET tanimliysa acilir. Tanimli degilse DEMO modu
(ornek veriyle) calisir.  Uc nokta: /mcp
"""

from __future__ import annotations

import os
import time
import contextlib

import httpx
from fastmcp import FastMCP
from fastmcp.utilities.types import Image
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy

# ----------------------------------------------------------------- config
APP_ID = os.getenv("META_APP_ID", "")
APP_SECRET = os.getenv("META_APP_SECRET", "")
VER = os.getenv("META_API_VERSION", "v21.0")
PUBLIC_URL = (os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL")
              or "http://localhost:8080").rstrip("/")
AUTH_ENABLED = bool(APP_ID and APP_SECRET)
GRAPH = "https://graph.facebook.com"
HERE = os.path.dirname(os.path.abspath(__file__))

INSTRUCTIONS = (
    "Sen bir REKLAM KREATIFI (gorsel/banner) PERFORMANS ve CRO ANALISTISIN. "
    "Yalnizca Meta (Facebook & Instagram) reklam kreatiflerini degerlendirir, "
    "GORSELIN KENDISINI inceleyip donusum (CRO) odakli, uygulanabilir oneriler "
    "verirsin. Metrikleri (CTR, thumbstop, hold, CVR, CPA, ROAS, frequency) gorsel "
    "bulgularla birlestir. KAPSAM DISI konular (butce, teklif, kampanya yapisi, "
    "hedefleme, yerlesim optimizasyonu, pixel/teknik) sorulursa kibarca reddet ve "
    "konuyu kreatiflere getir."
)

CRO_RUBRIC = (
    "[GOREV] Ekteki banner GORSELINI ve metrikleri birlikte degerlendir. Kisa ve "
    "aksiyon odakli: 1) HOOK & ilk izlenim 2) GORSEL HIYERARSI (kontrast, metin "
    "yogunlugu, marka, mobil) 3) CTA & DONUSUM (CRO surtunmeleri) 4) METRIK BAGLANTISI "
    "(dusuk thumbstop->hook zayif; yuksek CTR+dusuk CVR->vaat/landing uyumsuz; yuksek "
    "frequency+dusen CTR->kreatif yorgunlugu) 5) EN IYI KULLANIM SENARYOSU (huni asamasi "
    "+ Feed/Reels/Stories) 6) 3-5 ONCELIKLI, test edilebilir oneri. SADECE kreatif/gorsel."
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
    try:
        with open(os.path.join(HERE, "sample_banner.jpg"), "rb") as f:
            return f.read(), "jpeg"
    except Exception:
        return None


# ----------------------------------------------------------------- Facebook OAuth
class FacebookTokenVerifier(TokenVerifier):
    """FB kullanici token'ini debug_token ile dogrular, scope'lari cikarir."""

    def __init__(self, app_id: str, app_secret: str,
                 required_scopes: list[str] | None = None, timeout: int = 10):
        super().__init__(required_scopes=required_scopes)
        self.app_id = app_id
        self.app_secret = app_secret
        self.timeout = timeout

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.get(
                    f"{GRAPH}/debug_token",
                    params={"input_token": token,
                            "access_token": f"{self.app_id}|{self.app_secret}"},
                )
            if r.status_code != 200:
                return None
            data = r.json().get("data", {})
            if not data.get("is_valid"):
                return None
            scopes = data.get("scopes", []) or []
            if self.required_scopes and not set(self.required_scopes).issubset(set(scopes)):
                return None
            exp = data.get("expires_at") or None
            return AccessToken(
                token=token,
                client_id=str(data.get("user_id", "fb_user")),
                scopes=scopes,
                expires_at=int(exp) if exp else None,
                claims={"user_id": data.get("user_id"), "app_id": data.get("app_id")},
            )
        except Exception:
            return None


def _build_auth():
    from key_value.aio.stores.memory import MemoryStore
    verifier = FacebookTokenVerifier(APP_ID, APP_SECRET, required_scopes=["ads_read"])
    return OAuthProxy(
        upstream_authorization_endpoint=f"https://www.facebook.com/{VER}/dialog/oauth",
        upstream_token_endpoint=f"{GRAPH}/{VER}/oauth/access_token",
        upstream_client_id=APP_ID,
        upstream_client_secret=APP_SECRET,
        token_verifier=verifier,
        base_url=PUBLIC_URL,
        redirect_path="/auth/callback",
        valid_scopes=["ads_read"],
        token_endpoint_auth_method="client_secret_post",
        jwt_signing_key=APP_SECRET,
        require_authorization_consent=False,
        client_storage=MemoryStore(),
    )


auth = _build_auth() if AUTH_ENABLED else None
mcp = FastMCP(name="creative-performance-mcp", instructions=INSTRUCTIONS, auth=auth)


# ----------------------------------------------------------------- Graph helpers
FIELDS = ("ad_id,ad_name,impressions,inline_link_clicks,ctr,inline_link_click_ctr,"
          "cpc,cpm,spend,frequency,actions,action_values,video_play_actions,"
          "video_p100_watched_actions")


def _token() -> str | None:
    if not AUTH_ENABLED:
        return None
    at = get_access_token()
    return at.token if at else None


def _graph(path, params, token):
    p = {**params, "access_token": token}
    r = httpx.get(f"{GRAPH}/{VER}/{path}", params=p, timeout=60)
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


def _first_account(token):
    d = _graph("me/adaccounts", {"fields": "account_id,name", "limit": 1}, token)
    rows = d.get("data", [])
    if not rows:
        raise RuntimeError("Bu hesaba bagli reklam hesabi bulunamadi.")
    return "act_" + str(rows[0]["account_id"])


def get_insights(date_preset="last_14d", ad_id=None):
    token = _token()
    if not token:  # DEMO
        if ad_id:
            return [r for r in DEMO_ROWS if r["ad_id"] == ad_id] or [DEMO_ROWS[0]]
        return list(DEMO_ROWS)
    if ad_id:
        path = f"{ad_id}/insights"
    else:
        path = f"{_first_account(token)}/insights"
    data = _graph(path, {"level": "ad", "fields": FIELDS,
                         "date_preset": date_preset, "limit": 200}, token)
    return [_normalize(r) for r in data.get("data", [])]


def get_image(ad_id):
    token = _token()
    if not token:  # DEMO
        return _demo_image()
    data = _graph(ad_id, {"fields": "creative{image_url,thumbnail_url,asset_feed_spec{images}}"}, token)
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
@mcp.tool
def list_creatives(date_preset: str = "last_14d"):
    """Baglanan Meta reklam hesabindaki kreatifleri (banner) metrikleriyle listeler,
    link CTR'a gore siralar. Analiz oncesi bunu cagir."""
    try:
        rows = get_insights(date_preset)
    except Exception as e:
        return f"HATA: {e}"
    if not rows:
        return "Bu tarih araliginda kreatif verisi yok."
    rows = sorted(rows, key=lambda r: (r.get("link_ctr_pct") or 0), reverse=True)
    tag = "" if AUTH_ENABLED else " [DEMO]"
    return f"{len(rows)} kreatif{tag} ({date_preset}), link CTR'a gore sirali:\n\n" + \
        "\n\n".join(fmt(r) for r in rows)


@mcp.tool
def analyze_creative(ad_id: str, date_preset: str = "last_14d"):
    """Tek kreatifi derin analiz eder: metrikleri ceker VE banner'in kendisini GORSEL
    olarak arac sonucuna ekler; sen gorseli gorup CRO odakli oneriler verirsin."""
    try:
        ins = get_insights(date_preset, ad_id)
        img = get_image(ad_id)
    except Exception as e:
        return f"HATA: {e}"
    m = ins[0] if ins else {"ad_id": ad_id}
    parts = ["=== METRIKLER ===\n" + fmt(m)]
    if img:
        parts.append("\n=== BANNER GORSELI (asagida) ===")
        parts.append(Image(data=img[0], format=img[1]))
    else:
        parts.append("\n(Bu kreatif icin gorsel eklenemedi.)")
    parts.append("\n" + CRO_RUBRIC)
    return parts


@mcp.tool
def compare_creatives(ad_ids: list, date_preset: str = "last_14d"):
    """2-5 kreatifi karsilastirir: her birinin metrik + GORSELINI ekler; sen kazanani
    secip metriksel + gorsel/CRO nedenlerini aciklar, zayifa oneri verirsin."""
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
    parts.append("\n" + CRO_RUBRIC + "\n\nSON OLARAK: kazanani sec ve zayif kreatif icin "
                 "en yuksek etkili 2-3 iyilestirmeyi belirt.")
    return parts


# ----------------------------------------------------------------- app
app = mcp.http_app(allowed_hosts=["*"], allowed_origins=["*"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
