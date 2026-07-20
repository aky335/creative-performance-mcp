"""
Creative Performance MCP  (COK-KIRACILI / multi-tenant, token tabanli)
======================================================================
Meta (Facebook & Instagram) reklam KREATIFLERININ performansini ve GORSELINI
CRO odakli degerlendirir. SADECE kreatif konusur.

Cok-kiracili model (public OAuth YOK -> App Review beklemesi YOK):
- Her musteri KENDI URL'i ile baglanir:  https://.../t/<TENANT_KEY>/mcp
- Sunucu bu key'den musteriyi tanir ve TUM ciktilari YALNIZCA o musterinin
  reklam hesab(lar)i ile sinirlar. Ajansin diger hesaplari / diger musteriler
  hicbir kiraciya gorunmez.
- Meta erisimi bir System User token'i ile yapilir (ajans geneli SHARED_TOKEN
  ya da kiraci-bazli token). Bu token hicbir musteriye asla dondurulmez;
  sunucu her cagriyi beyaz-listeye gore kilitler.

Env:
  META_API_VERSION   (varsayilan v21.0)
  META_SYSTEM_TOKEN  (ajans geneli System User token; kiracida token yoksa kullanilir)
  TENANTS            (JSON) -> { "<key>": {"name": "...", "accounts": ["act_.."], "token": "..(ops)"} }
Uc nokta:  /t/<TENANT_KEY>/mcp
"""

from __future__ import annotations

import os
import re
import json
import contextvars

import httpx
from fastmcp import FastMCP
from fastmcp.utilities.types import Image

# ----------------------------------------------------------------- config
VER = os.getenv("META_API_VERSION", "v21.0")
GRAPH = "https://graph.facebook.com"
HERE = os.path.dirname(os.path.abspath(__file__))
SHARED_TOKEN = os.getenv("META_SYSTEM_TOKEN", "")


def _norm_acct(a: str) -> str:
    a = str(a)
    return a if a.startswith("act_") else "act_" + a


def _load_tenants() -> dict:
    """TENANTS env (JSON) -> normalize edilmis kiraci defterine."""
    raw = os.getenv("TENANTS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    out = {}
    for key, t in (data or {}).items():
        if not isinstance(t, dict):
            continue
        accts = [_norm_acct(a) for a in (t.get("accounts") or [])]
        out[str(key)] = {
            "name": t.get("name") or "Musteri",
            "accounts": accts,
            "token": t.get("token") or "",
        }
    return out


TENANTS = _load_tenants()

# Aktif istegin kiracisi (ASGI router her istek basinda set eder)
_TENANT: contextvars.ContextVar = contextvars.ContextVar("tenant", default=None)


def _current():
    return _TENANT.get()


def _token_for(tenant) -> str:
    return (tenant or {}).get("token") or SHARED_TOKEN or ""


def _allowed(tenant) -> list:
    return list((tenant or {}).get("accounts") or [])


INSTRUCTIONS = (
    "Sen bir REKLAM KREATIFI (gorsel/banner) PERFORMANS ve CRO ANALISTISIN. "
    "Yalnizca Meta (Facebook & Instagram) reklam kreatiflerini degerlendirir, "
    "GORSELIN KENDISINI inceleyip donusum (CRO) odakli, uygulanabilir oneriler "
    "verirsin. Metrikleri (CTR, thumbstop, hold, CVR, CPA, ROAS, frequency) gorsel "
    "bulgularla birlestir. KAPSAM DISI konular (butce, teklif, kampanya yapisi, "
    "hedefleme, yerlesim optimizasyonu, pixel/teknik) sorulursa kibarca reddet ve "
    "konuyu kreatiflere getir.\n\n"
    "ONEMLI - ILK KARSILAMA: Kullanici bu baglayiciyla YENI bir konusmaya basladiginda, "
    "ilk selamlastiginda, 'merhaba', 'ne yapabilirsin', 'nasil kullanirim' gibi bir sey "
    "dediginde ya da henuz hicbir arac cagirmadiysan; ONCE 'welcome' aracini cagir ve "
    "donen hos geldin metnini kullaniciya oldugu gibi, sicak bir sekilde sun. Analize "
    "gecmeden once kullaniciyi kisaca yonlendir."
)

WELCOME = (
    "Hos geldin! Ben **D-Option Kreatif CRO Analistiyim** - Meta (Facebook & Instagram) "
    "reklam gorsellerinin/banner'larinin performansini analiz eder ve donusumu (CRO) "
    "artiracak, uygulanabilir oneriler veririm.\n\n"
    "NELER YAPARIM:\n"
    "- Banner/gorselin KENDISINI degerlendiririm (hook, hiyerarsi, kontrast, CTA, marka, mobil okunabilirlik)\n"
    "- Metriklerle birlestiririm (CTR, thumbstop, hold, CVR, CPA, ROAS, frequency)\n"
    "- Kreatifleri kiyaslar, kazanani ve nedenlerini gosteririm\n"
    "- Her gozlemi somut, test edilebilir bir iyilestirme onerisine baglarim\n\n"
    "NELER YAPMAM (kapsam disi):\n"
    "Butce, teklif/bidding, hedefleme, kampanya yapisi, yerlesim optimizasyonu, pixel/teknik "
    "kurulum - bunlarda yorum yapmam, konuyu hep kreatife getiririm.\n\n"
    "HIZLI BASLANGIC (3 adim):\n"
    "1) 'Reklam hesaplarimi listele'  -> hesaplarini gorurum\n"
    "2) 'Su hesaptaki kreatifleri listele'  -> banner'lari metrikleriyle siralarim\n"
    "3) 'Su reklami analiz et' ya da 'Su iki kreatifi karsilastir'  -> derin CRO analizi\n\n"
    "Ornek: \"Son 30 gunde en dusuk CTR'li 3 banner'i bul ve nasil iyilestirebilecegimi soyle.\"\n\n"
    "Hazirsan, hangi reklam hesabina bakalim?"
)

CRO_RUBRIC = (
    "[GOREV] Ekteki banner GORSELINI ve metrikleri birlikte degerlendir. Kisa ve "
    "aksiyon odakli: 1) HOOK & ilk izlenim 2) GORSEL HIYERARSI (kontrast, metin "
    "yogunlugu, marka, mobil) 3) CTA & DONUSUM (CRO surtunmeleri) 4) METRIK BAGLANTISI "
    "(dusuk thumbstop->hook zayif; yuksek CTR+dusuk CVR->vaat/landing uyumsuz; yuksek "
    "frequency+dusen CTR->kreatif yorgunlugu) 5) EN IYI KULLANIM SENARYOSU (huni asamasi "
    "+ Feed/Reels/Stories) 6) 3-5 ONCELIKLI, test edilebilir oneri. SADECE kreatif/gorsel."
)

# ----------------------------------------------------------------- demo data (token yoksa)
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


mcp = FastMCP(name="creative-performance-mcp", instructions=INSTRUCTIONS)

# ----------------------------------------------------------------- Graph helpers
FIELDS = ("ad_id,ad_name,impressions,inline_link_clicks,ctr,inline_link_click_ctr,"
          "cpc,cpm,spend,frequency,actions,action_values,video_play_actions,"
          "video_p100_watched_actions")


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


def _acct_name(acct, token):
    try:
        return _graph(acct, {"fields": "name"}, token).get("name")
    except Exception:
        return None


def _ad_account(ad_id, token):
    """Reklam ID'sinin ait oldugu hesabi (act_...) dondurur; guvenlik icin sahiplik dogrulamasi."""
    try:
        d = _graph(ad_id, {"fields": "account_id"}, token)
        acc = d.get("account_id")
        return _norm_acct(acc) if acc else None
    except Exception:
        return None


def get_insights(token, accounts, date_preset="last_14d", ad_id=None):
    """accounts = izin verilen hesap listesi (kiracinin beyaz listesi)."""
    if not token:  # DEMO
        if ad_id:
            return [r for r in DEMO_ROWS if r["ad_id"] == ad_id] or [DEMO_ROWS[0]]
        return list(DEMO_ROWS)
    if ad_id:
        data = _graph(f"{ad_id}/insights", {"level": "ad", "fields": FIELDS,
                      "date_preset": date_preset, "limit": 200}, token)
        return [_normalize(r) for r in data.get("data", [])]
    rows = []
    for acct in accounts:
        try:
            data = _graph(f"{acct}/insights", {"level": "ad", "fields": FIELDS,
                          "date_preset": date_preset, "limit": 100}, token)
        except Exception:
            continue
        for r in data.get("data", []):
            m = _normalize(r)
            m["account"] = acct
            rows.append(m)
    return rows


def get_image(token, ad_id):
    """(bytes, format, url) dondurur; yoksa None."""
    if not token:  # DEMO
        d = _demo_image()
        return (d[0], d[1], None) if d else None
    afs_fields = ("asset_feed_spec{images{url,hash},"
                  "videos{video_id,thumbnail_url}}")
    fields = ("account_id,creative{image_url,thumbnail_url,image_hash,video_id,"
              "object_story_spec{link_data{picture,image_hash},"
              "photo_data{url,image_hash},video_data{video_id,image_url}},"
              + afs_fields + "}")
    # thumbnail_width/height -> Meta 64x64 yerine buyuk (tam cozunurluge yakin) onizleme doner
    data = _graph(ad_id, {"fields": fields,
                          "thumbnail_width": 1080, "thumbnail_height": 1080}, token)
    acct = data.get("account_id")
    cr = data.get("creative", {}) or {}
    oss = cr.get("object_story_spec", {}) or {}
    ld = oss.get("link_data", {}) or {}
    pd = oss.get("photo_data", {}) or {}
    vd = oss.get("video_data", {}) or {}
    afs_img = (cr.get("asset_feed_spec", {}) or {}).get("images", []) or []
    afs_vid = (cr.get("asset_feed_spec", {}) or {}).get("videos", []) or []

    # 1) STATIK TAM BOY: image_hash -> adimages permalink_url (reklamverenin yukledigi orijinal)
    hashes = []
    for h in (cr.get("image_hash"), ld.get("image_hash"), pd.get("image_hash")):
        if h:
            hashes.append(h)
    for im in afs_img:
        if im.get("hash"):
            hashes.append(im["hash"])
    full_url = None
    if acct and hashes:
        try:
            d = _graph(f"act_{acct}/adimages",
                       {"hashes": json.dumps([hashes[0]]),
                        "fields": "permalink_url,url"}, token)
            rows = d.get("data", [])
            if rows:
                full_url = rows[0].get("permalink_url") or rows[0].get("url")
        except Exception:
            pass

    # 2) VIDEO TAM KARE: video_id -> /thumbnails -> is_preferred uri (64x64 degil, gercek kare)
    video_url = None
    if not full_url:
        vids = []
        for v in (cr.get("video_id"), vd.get("video_id")):
            if v:
                vids.append(v)
        for v in afs_vid:
            if v.get("video_id"):
                vids.append(v["video_id"])
        for vid in vids:
            try:
                d = _graph(f"{vid}/thumbnails",
                           {"fields": "uri,is_preferred,width,height"}, token)
                thumbs = d.get("data", []) or []
                if thumbs:
                    pref = [t for t in thumbs if t.get("is_preferred")]
                    pick = (pref or sorted(
                        thumbs, key=lambda t: (t.get("width") or 0), reverse=True))[0]
                    if pick.get("uri"):
                        video_url = pick["uri"]
                        break
            except Exception:
                continue

    # 3) Buyutulmus thumbnail_url + diger adaylar (p64x64 olmayan tercih edilir)
    cands = []
    if ld.get("picture"):
        cands.append(ld["picture"])
    if pd.get("url"):
        cands.append(pd["url"])
    if vd.get("image_url"):
        cands.append(vd["image_url"])
    for im in afs_img:
        if im.get("url"):
            cands.append(im["url"])
    for v in afs_vid:
        if v.get("thumbnail_url"):
            cands.append(v["thumbnail_url"])
    if cr.get("image_url"):
        cands.append(cr["image_url"])
    if cr.get("thumbnail_url"):
        cands.append(cr["thumbnail_url"])
    non_thumb = [u for u in cands if "p64x64" not in u]
    url = (full_url or video_url or (non_thumb[0] if non_thumb else
           (cands[0] if cands else None)))
    if not url:
        return None
    try:
        r = httpx.get(url, timeout=45, follow_redirects=True)
        r.raise_for_status()
        ct = r.headers.get("content-type", "image/jpeg").split(";")[0]
        fmt = {"image/jpeg": "jpeg", "image/png": "png", "image/gif": "gif",
               "image/webp": "webp"}.get(ct, "jpeg")
        return r.content, fmt, url
    except Exception:
        return None


def fmt(m):
    def g(k, s=""):
        v = m.get(k)
        return f"{v}{s}" if v is not None else "-"
    acc = f" [{m['account']}]" if m.get("account") else ""
    return (f"- {m.get('ad_name') or m.get('ad_id')} (id:{m.get('ad_id')}){acc}\n"
            f"  Gosterim:{g('impressions')} Harcama:{g('spend')} | CTR(link):{g('link_ctr_pct','%')} "
            f"CTR:{g('ctr_all_pct','%')} CPC:{g('cpc')} CPM:{g('cpm')}\n"
            f"  Thumbstop:{g('thumbstop_pct','%')} Hold:{g('hold_rate_pct','%')} Freq:{g('frequency')} | "
            f"Donusum:{g('conversions')} CVR:{g('cvr_pct','%')} CPA:{g('cpa')} ROAS:{g('roas','x')}")


# ----------------------------------------------------------------- tools
@mcp.tool
def welcome():
    """Yeni bir konusmanin basinda cagir: kullaniciya bu aracin ne yaptigini, neyi
    yapmadigini ve nasil baslayacagini anlatan sicak bir HOS GELDIN metni doner.
    Kullanici selamlastiginda / 'ne yapabilirsin' dediginde de kullan."""
    return WELCOME


@mcp.tool
def list_ad_accounts():
    """Bu musterinin erisebildigi reklam hesab(lar)ini listeler. (Yalnizca kendi
    hesabini gorur; baska musteri/hesap gorunmez.)"""
    tenant = _current()
    if tenant is None:
        return "Yetkisiz: gecerli musteri baglantisi bulunamadi."
    allowed = _allowed(tenant)
    if not allowed:
        return "Bu musteri icin tanimli reklam hesabi yok. Lutfen operatore bildirin."
    token = _token_for(tenant)
    lines = []
    for acct in allowed:
        name = _acct_name(acct, token) if token else None
        lines.append(f"- {name or tenant['name']}  (id: {acct})")
    return f"{len(allowed)} reklam hesabi:\n" + "\n".join(lines)


@mcp.tool
def list_creatives(date_preset: str = "last_14d", account_id: str = ""):
    """Kreatifleri (banner) metrikleriyle listeler, link CTR'a gore siralar. account_id
    verilmezse bu musterinin tum hesaplarini tarar. Analiz oncesi bunu cagir."""
    tenant = _current()
    if tenant is None:
        return "Yetkisiz: gecerli musteri baglantisi bulunamadi."
    allowed = _allowed(tenant)
    token = _token_for(tenant)
    if account_id:
        acct = _norm_acct(account_id)
        if token and acct not in allowed:
            return "Bu hesaba erisiminiz yok."
        scan = [acct]
    else:
        scan = allowed
    try:
        rows = get_insights(token, scan, date_preset=date_preset)
    except Exception as e:
        return f"HATA: {e}"
    if not rows:
        return "Bu aralikta kreatif verisi bulunamadi. Tarih araligini genisletmeyi deneyin (or. last_30d/last_90d)."
    rows = sorted(rows, key=lambda r: (r.get("link_ctr_pct") or 0), reverse=True)
    tag = "" if token else " [DEMO]"
    return f"{len(rows)} kreatif{tag} ({date_preset}), link CTR'a gore sirali:\n\n" + \
        "\n\n".join(fmt(r) for r in rows)


@mcp.tool
def analyze_creative(ad_id: str, date_preset: str = "last_14d"):
    """Tek kreatifi derin analiz eder: metrikleri ceker VE banner'in kendisini GORSEL
    olarak arac sonucuna ekler; sen gorseli gorup CRO odakli oneriler verirsin."""
    tenant = _current()
    if tenant is None:
        return "Yetkisiz: gecerli musteri baglantisi bulunamadi."
    allowed = _allowed(tenant)
    token = _token_for(tenant)
    # Guvenlik: reklam bu musterinin hesabina ait mi?
    if token:
        owner = _ad_account(ad_id, token)
        if owner and owner not in allowed:
            return "Bu reklam sizin hesabiniza ait degil; erisim reddedildi."
    try:
        ins = get_insights(token, allowed, date_preset=date_preset, ad_id=ad_id)
        img = get_image(token, ad_id)
    except Exception as e:
        return f"HATA: {e}"
    m = ins[0] if ins else {"ad_id": ad_id}
    parts = ["=== METRIKLER ===\n" + fmt(m)]
    if img:
        parts.append("\n=== BANNER GORSELI (asagida) ===")
        parts.append(Image(data=img[0], format=img[1]))
        if len(img) > 2 and img[2]:
            parts.append(f"Gorsel URL: {img[2]}")
    else:
        parts.append("\n(Bu kreatif icin gorsel eklenemedi.)")
    parts.append("\n" + CRO_RUBRIC)
    return parts


@mcp.tool
def compare_creatives(ad_ids: list, date_preset: str = "last_14d"):
    """2-5 kreatifi karsilastirir: her birinin metrik + GORSELINI ekler; sen kazanani
    secip metriksel + gorsel/CRO nedenlerini aciklar, zayifa oneri verirsin."""
    tenant = _current()
    if tenant is None:
        return "Yetkisiz: gecerli musteri baglantisi bulunamadi."
    allowed = _allowed(tenant)
    token = _token_for(tenant)
    ad_ids = ad_ids[:5]
    if len(ad_ids) < 2:
        return "Karsilastirma icin en az 2 reklam id'si gerekir."
    parts = [f"{len(ad_ids)} kreatif karsilastirmasi ({date_preset}):"]
    for aid in ad_ids:
        if token:
            owner = _ad_account(aid, token)
            if owner and owner not in allowed:
                parts.append(f"\n[{aid}] Bu reklam sizin hesabiniza ait degil; atlandi.")
                continue
        try:
            ins = get_insights(token, allowed, date_preset=date_preset, ad_id=aid)
            img = get_image(token, aid)
        except Exception as e:
            parts.append(f"\n[{aid}] HATA: {e}")
            continue
        m = ins[0] if ins else {"ad_id": aid}
        parts.append("\n----------------------------------------\n" + fmt(m))
        if img:
            parts.append(Image(data=img[0], format=img[1]))
            if len(img) > 2 and img[2]:
                parts.append(f"Gorsel URL: {img[2]}")
    parts.append("\n" + CRO_RUBRIC + "\n\nSON OLARAK: kazanani sec ve zayif kreatif icin "
                 "en yuksek etkili 2-3 iyilestirmeyi belirt.")
    return parts


# ----------------------------------------------------------------- ASGI (per-tenant URL router)
mcp_app = mcp.http_app(stateless_http=True)

_TPATH = re.compile(r"^/t/([^/]+)(/mcp.*)$")


async def _send_json(send, status, body):
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(data)).encode())]})
    await send({"type": "http.response.body", "body": data})


class TenantRouter:
    """/t/<key>/mcp -> key'i cozer, kiraciyi contextvar'a koyar, path'i /mcp'ye
    yeniden yazip ic MCP uygulamasina devreder. Gecersiz key -> 401."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        m = _TPATH.match(path)
        if m:
            key, rest = m.group(1), m.group(2)
            tenant = TENANTS.get(key)
            if not tenant:
                await _send_json(send, 401, {"error": "unauthorized"})
                return
            new_scope = dict(scope)
            new_scope["path"] = rest
            rp = scope.get("raw_path")
            if rp:
                prefix = ("/t/" + key).encode()
                if isinstance(rp, (bytes, bytearray)) and rp.startswith(prefix):
                    new_scope["raw_path"] = rp[len(prefix):]
            tok = _TENANT.set(tenant)
            try:
                await self.app(new_scope, receive, send)
            finally:
                _TENANT.reset(tok)
            return
        if path == "/" or path == "/health":
            await _send_json(send, 200, {"ok": True, "service": "creative-performance-mcp",
                                         "tenants": len(TENANTS)})
            return
        if path.startswith("/mcp"):
            await _send_json(send, 401,
                             {"error": "unauthorized - use your tenant URL: /t/<key>/mcp"})
            return
        await self.app(scope, receive, send)


app = TenantRouter(mcp_app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
