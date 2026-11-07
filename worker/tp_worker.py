#!/usr/bin/env python3
"""
VGR Legal - TP worker (runs 24/7 on the cloud computer).

Watches the portal's tp_jobs. For each job it:
  1. opens TNREGINET, fills the saved login ID + password, and sends the captcha picture to the portal
  2. waits for the MD to type the captcha in the portal, signs in
  3. fills the Deed of Receipt screens from the case's Drafting data
  4. stops before any payment / final submit and reports back (with a screenshot)

Never clicks E-Payment or final Submit.
Config: /opt/vgr/worker.env  (SUPABASE_URL, SUPABASE_SERVICE_KEY)
"""
import base64, datetime, json, os, re, sys, time, traceback
import requests
import uuid
from playwright.sync_api import sync_playwright

_here = os.path.dirname(os.path.abspath(__file__))
ENV = os.environ.get("VGR_ENV") or (os.path.join(_here, "worker.env") if os.path.exists(os.path.join(_here, "worker.env")) else "/opt/vgr/worker.env")
for line in open(ENV) if os.path.exists(ENV) else []:
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.strip().split("=", 1)
        os.environ.setdefault(k, v)
SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
# New Supabase secret keys (sb_secret_...) go in the apikey header only; old service_role JWTs also as Bearer.
AUTH = {"apikey": KEY} if KEY.startswith("sb_") else {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
H = dict(AUTH, **{"Content-Type": "application/json"})
URL = "https://tnreginet.gov.in/portal/"
CAPTCHA_WAIT = 600  # seconds


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(*a):
    print(now(), *a, flush=True)


# ---------------- Supabase ----------------
# One shared session that retries dropped connections (home Wi-Fi / Supabase hiccups must not kill a TP run).
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
HTTP = requests.Session()
HTTP.mount("https://", HTTPAdapter(max_retries=Retry(total=6, connect=6, read=4, backoff_factor=1.5,
                                                     status_forcelist=(500, 502, 503, 504, 520, 522, 524),
                                                     allowed_methods=None, raise_on_status=False)))
def kv_get(col, id_):
    r = HTTP.get(f"{SB}/rest/v1/kv", headers=H, params={"collection": f"eq.{col}", "id": f"eq.{id_}", "select": "id,data"}, timeout=20)
    r.raise_for_status()
    rows = r.json()
    return rows[0]["data"] if rows else None


def kv_list(col):
    r = HTTP.get(f"{SB}/rest/v1/kv", headers=H, params={"collection": f"eq.{col}", "select": "id,data"}, timeout=20)
    r.raise_for_status()
    return r.json()


def kv_merge(col, id_, patch):
    r = HTTP.post(f"{SB}/rest/v1/rpc/kv_merge", headers=H, json={"p_collection": col, "p_id": id_, "p_patch": patch}, timeout=20)
    r.raise_for_status()


def kv_set(col, id_, data):
    h = dict(H, Prefer="resolution=merge-duplicates")
    r = HTTP.post(f"{SB}/rest/v1/kv", headers=h, json={"collection": col, "id": id_, "data": data, "updated_at": now()}, timeout=20)
    r.raise_for_status()


def login_of(id_):
    r = HTTP.get(f"{SB}/rest/v1/tn_logins", headers=H, params={"id": f"eq.{id_}", "select": "username,password,client"}, timeout=20)
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


def download(url, dest):
    """Portal stores files as 'sb:<path>' in the private 'docs' bucket."""
    if not url.startswith("sb:"):
        return None
    r = HTTP.get(f"{SB}/storage/v1/object/docs/{url[3:]}", headers=AUTH, timeout=60)
    r.raise_for_status()
    open(dest, "wb").write(r.content)
    return dest


# ---------------- job state ----------------
class Job:
    def __init__(self, jid, data):
        self.id, self.d = jid, data

    def upd(self, **kv):
        self.d.update(kv)
        for i in range(4):
            try:
                kv_merge("tp_jobs", self.id, kv)
                return
            except Exception as e:
                log(self.id[:8], "update failed:", e)
                time.sleep(3 * (i + 1))

    def step(self, text, page=None):
        log(self.id[:8], text)
        patch = {"step": text, "at": now()}
        if page is not None:
            try:
                patch["shot"] = "data:image/jpeg;base64," + base64.b64encode(page.screenshot(type="jpeg", quality=55, full_page=False)).decode()
            except Exception:
                pass
        self.upd(**patch)

    def fresh(self):
        try:
            self.d = kv_get("tp_jobs", self.id) or self.d
        except Exception as e:  # a network blip while polling must not stop the TP
            log(self.id[:8], "poll failed:", e)
        return self.d

    def cancelled(self):
        return self.fresh().get("status") == "cancel"


DLG = []  # browser alert messages (e.g. "Invalid captcha")


class Stop(Exception):
    pass


# ---------------- TNREGINET helpers (same field ids as tp_dor.py) ----------------
JS_HELPERS = r"""
window.vis = id => [...document.querySelectorAll('[id="'+id+'"]')].find(e=>e.offsetParent) || document.getElementById(id);
window.setv = (id,v) => {const e=vis(id); if(!e) return 'NOFIELD '+id; e.value=v;
  e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true})); return 'ok'};
window.selre = (id,pat) => {const s=vis(id); if(!s) return 'NOFIELD '+id; const re=new RegExp(pat,'i');
  const o=[...s.options].find(o=>re.test(o.text.trim())); if(!o) return 'NOOPT '+id+' '+pat;
  if(window.jQuery) jQuery(s).val(o.value).trigger('change').trigger('chosen:updated'); else {s.value=o.value; s.dispatchEvent(new Event('change',{bubbles:true}))}
  return 'ok '+o.text};
window.selfuzzy = (id,txt) => {const s=vis(id); if(!s) return 'NOFIELD '+id; const t=String(txt).toLowerCase().replace(/[^a-z]/g,'');
  let best=null,score=0; for(const o of s.options){const n=o.text.toLowerCase().replace(/[^a-z]/g,''); if(!n) continue;
    let m=0; for(let i=0;i<Math.min(n.length,t.length);i++) if(n[i]==t[i]) m++; const sc=m/Math.max(n.length,t.length); if(sc>score){score=sc;best=o}}
  if(!best||score<0.5) return 'NOOPT '+id+' '+txt;
  if(window.jQuery) jQuery(s).val(best.value).trigger('change').trigger('chosen:updated'); else {s.value=best.value; s.dispatchEvent(new Event('change',{bubbles:true}))}
  return 'ok '+best.text+' '+score.toFixed(2)};
window.selco = (id,txt) => {const s=vis(id); if(!s) return 'NOFIELD '+id;
  const nz=x=>String(x).toLowerCase().replace(/aadhaar/g,'aadhar').replace(/\blimited\b/g,'ltd').replace(/\bprivate\b/g,'pvt').replace(/\bcompany\b/g,'co').replace(/[^a-z0-9 ]/g,' ').replace(/\s+/g,' ').trim();
  const t=nz(txt), tw=new Set(t.split(' ')); let best=null,score=0;
  for(const o of s.options){const n=nz(o.text); if(!n||/select/.test(n)) continue; if(n===t){best=o;score=1;break}
    const nw=n.split(' '); const hit=nw.filter(w=>tw.has(w)).length; const sc=hit/Math.max(nw.length,tw.size); if(sc>score){score=sc;best=o}}
  if(!best||score<0.6) return 'NOOPT '+id+' '+txt;
  if(window.jQuery) jQuery(s).val(best.value).trigger('change').trigger('chosen:updated'); else {s.value=best.value; s.dispatchEvent(new Event('change',{bubbles:true}))}
  return 'ok '+best.text+' '+score.toFixed(2)};
window.tick = id => {const c=vis(id); if(c && !c.checked) c.click(); return c? 'ok':'NOFIELD '+id};
"""


class Portal:
    def __init__(self, page, job):
        self.p, self.job, self.warn = page, job, []

    def js(self, code):
        self.p.evaluate(JS_HELPERS)
        return self.p.evaluate(code)

    def wait(self, s=1.5):
        time.sleep(s)
        if self.job.cancelled():
            raise Stop("Stopped from the portal")

    def popup_ok(self):
        """Read and accept TNREGINET pop-ups (OK / Yes - e.g. 'Is the Mortgagor also going to execute ... Proceed?')."""
        txt = ""
        for _ in range(3):
            r = self.p.evaluate(r"""()=>{const btns=[...document.querySelectorAll('button,input[type=button],a.btn')].filter(b=>b.offsetParent&&/^\s*(OK|Ok|Yes|YES|சரி|ஆம்)\s*$/.test(b.value||b.innerText||''));
                const b=btns.find(b=>b.closest('.modal,.swal2-popup,.jconfirm-box,.bootbox,.ui-dialog,[role=dialog],.popup,.alert'))||btns.find(b=>{let e=b;while(e&&e!==document.body){const st=getComputedStyle(e);if(st.position==='fixed'||+st.zIndex>100)return true;e=e.parentElement}return false});
                if(!b) return null; const box=b.closest('.modal,.swal2-popup,.jconfirm-box,.bootbox,.ui-dialog,[role=dialog],.popup,.alert')||b.parentElement.parentElement;
                const t=(box.innerText||'').replace(/\s+/g,' ').trim().slice(0,300); b.click(); return t}""")
            if not r:
                break
            if re.search(r"payment|pay now|e-?pay", r, re.I):
                txt += (" | " if txt else "") + "[payment pop-up left for the MD] " + r
                break
            txt += (" | " if txt else "") + r
            time.sleep(1.2)
        return txt

    def set(self, fid, val):
        if val in ("", None):
            return
        r = self.js(f"setv({fid!r},{str(val)!r})")
        if r != "ok":
            self.warn.append(r)

    def sel(self, fid, pattern, wait=1.5):
        r = self.js(f"selre({fid!r},{pattern!r})")
        self.wait(wait)
        if not str(r).startswith("ok"):
            self.warn.append(r)
        return r

    def fuzzy(self, fid, text, wait=2):
        if not text:
            return
        r = self.js(f"selfuzzy({fid!r},{str(text)!r})")
        self.wait(wait)
        if not str(r).startswith("ok"):
            self.warn.append(r)
        return r

    def tamil(self, fid, tamil_text, english_text):
        if tamil_text and re.search(r"[஀-௿]", tamil_text):
            self.set(fid, tamil_text)
            return
        if not english_text:
            return
        if not self.p.evaluate(f"()=>{{const e=document.getElementById({fid!r});return !!(e&&e.offsetParent)}}"):
            return
        try:
            loc = self.p.locator(f"[id='{fid}']:visible").first
            loc.click(timeout=5000)
            self.p.keyboard.press("F8")
            loc.fill("")
            loc.type(english_text + " ", delay=40)
            time.sleep(0.5)
        except Exception as e:
            self.warn.append(f"TAMIL {fid}: {e}")

    def click(self, fid, wait=3):
        self.js(f"vis({fid!r}) && vis({fid!r}).click()")
        self.wait(wait)
        return self.popup_ok()


def logged_in(page):
    """Signed in = a Logout / வெளியேறு item exists (often inside the user-name menu) and no visible login box."""
    try:
        return bool(page.evaluate("""()=>{const t=document.body.textContent||'';
            const out=/log ?out|sign ?out|வெளியேறு/i.test(t)||!!document.querySelector('a[href*="ogout"],[onclick*="ogout"]');
            const box=[...document.querySelectorAll('[id="username"]')].some(e=>e.offsetParent);
            const signin=[...document.querySelectorAll('a,button')].some(e=>e.offsetParent&&/^\\s*(sign ?in|உள்நுழைவு)\\s*$/i.test(e.textContent));
            return out&&!box&&!signin}"""))
    except Exception:
        return False


def captcha_png(page):
    img = page.locator("#captcha_login:visible")
    if img.count():
        img.first.scroll_into_view_if_needed()
        return "data:image/png;base64," + base64.b64encode(img.first.screenshot()).decode()
    # hidden image: copy its pixels through a canvas
    return page.evaluate("""()=>{const i=[...document.querySelectorAll('[id="captcha_login"]')].find(x=>x.naturalWidth)||document.getElementById('captcha_login');
        if(!i) return ''; const c=document.createElement('canvas'); c.width=i.naturalWidth||160; c.height=i.naturalHeight||50; c.getContext('2d').drawImage(i,0,0); return c.toDataURL('image/png')}""")


def open_login_form(page):
    """Open TNREGINET's LOGIN dialog (showLoginDialog('ROD')) above any promo pop-up, with a fresh captcha."""
    # close promo / notice pop-ups (anything modal that is not the login dialog)
    page.evaluate("""()=>{const login=document.getElementById('username');
        document.querySelectorAll('.modal,.popup,[role=dialog]').forEach(m=>{if(!m.offsetParent||(login&&m.contains(login)))return;
          const x=[...m.querySelectorAll('button,a,span,i,div')].find(e=>e.offsetParent&&(/^\\s*[×x✕]\\s*$/i.test(e.textContent)||/close/i.test(e.className+' '+(e.getAttribute('aria-label')||''))));
          if(x)x.click(); else m.style.display='none'})}""")
    page.wait_for_timeout(800)
    for _ in range(4):
        if page.locator("#username:visible").count():
            break
        page.evaluate("""()=>{if(typeof showLoginDialog==='function'){showLoginDialog('ROD');return}
            const re=/^\\s*(sign ?in|log ?in|login|உள்நுழைவு)\\s*$/i;
            const b=[...document.querySelectorAll('a,button,span,li,input[type=button]')].find(e=>e.offsetParent&&re.test((e.value||e.textContent||'').trim())&&e.children.length<3);
            if(b) b.click()}""")
        page.wait_for_timeout(2500)
    ok = page.locator("#username:visible").count() > 0
    if ok:
        uncover(page)
        # ask TNREGINET for a new login captcha so it is the one the server expects
        page.evaluate("""()=>{const i=document.getElementById('captcha_login'); if(!i) return;
            const box=i.closest('div,td,form')||i.parentElement;
            const r=[...box.parentElement.querySelectorAll('*')].find(e=>e!==i&&e.offsetParent&&/refresh|reload|captcha/i.test((e.getAttribute('onclick')||'')+' '+e.className+' '+e.id+' '+(e.getAttribute('src')||'')+' '+(e.getAttribute('title')||''))&&e.tagName!=='INPUT');
            if(r) r.click(); else i.src=i.src.split('?')[0]+'?'+Date.now()}""")
        page.wait_for_timeout(1500)
        try:
            page.wait_for_function("()=>{const i=document.getElementById('captcha_login');return i&&i.complete&&i.naturalWidth>0}", timeout=15000)
        except Exception:
            pass
    return ok


def uncover(page):
    """Hide whatever sits on top of the login box (e.g. the Presence Less Registration banner)."""
    for _ in range(6):
        hid = page.evaluate("""()=>{const u=[...document.querySelectorAll('[id="username"]')].find(e=>e.offsetParent); if(!u) return 'nouser';
            const login=u.closest('.modal,[role=dialog]')||u.parentElement; const r=u.getBoundingClientRect();
            const top=document.elementFromPoint(r.left+r.width/2, r.top+r.height/2);
            if(!top||top===u||login.contains(top)) return 'clear';
            let n=top; while(n.parentElement&&n.parentElement!==document.body&&!n.parentElement.contains(login)) n=n.parentElement;
            n.style.setProperty('display','none','important'); return 'hid '+(n.id||n.className||n.tagName).toString().slice(0,40)}""")
        if hid in ("clear", "nouser"):
            return hid
        page.wait_for_timeout(300)
    return "covered"


def type_into(page, sel, val):
    loc = page.locator(f"{sel}:visible")
    if loc.count():
        loc.first.fill(val)
        return
    page.evaluate("""([sel,val])=>document.querySelectorAll(sel).forEach(e=>{e.value=val;e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))})""", [sel, val])


def do_login(page, job, acc):
    job.upd(status="starting", step="Opening TNREGINET", at=now())
    page.goto(URL, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(3000)
    for attempt in range(5):
        if logged_in(page):
            job.upd(status="filling", captchaImg="", message="", step="Signed in as " + acc["username"], at=now())
            return
        if attempt:
            # a wrong captcha: load the page again so TNREGINET gives a brand-new captcha
            job.upd(captchaImg="", step="Getting a new captcha", at=now())
            page.goto(URL, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(3000)
        if not open_login_form(page):
            job.step("Login box not visible, filling it directly", page)
        type_into(page, "#username", acc["username"])
        type_into(page, "#password", acc["password"])
        job.upd(status="captcha", captchaImg=captcha_png(page), captcha="", step="Waiting for the captcha", at=now())
        t0 = time.time()
        while True:
            d = job.fresh()
            if d.get("status") == "cancel":
                raise Stop("Stopped from the portal")
            if d.get("status") == "captcha_sent" and d.get("captcha"):
                break
            if time.time() - t0 > CAPTCHA_WAIT:
                raise Stop("Nobody typed the captcha for 10 minutes")
            time.sleep(1.5)
        type_into(page, "#txt_Captcha_login", d["captcha"].strip())
        DLG.clear()
        clicked = page.evaluate("""()=>{const re=/sign ?in|log ?in|உள்நுழை/i, no=/registration|பதிவு|forgot|unlock|மறந்து/i;
            const u=[...document.querySelectorAll('[id="username"]')].find(e=>e.offsetParent)||document.getElementById('username'); let f=u;
            for(let i=0;i<10&&f;i++){f=f.parentElement; if(f&&f.querySelector('#txt_Captcha_login')&&[...f.querySelectorAll('button,input[type=submit],input[type=button],a.btn')].some(b=>b.offsetParent&&re.test(b.value||b.textContent)&&!no.test(b.value||b.textContent))) break;}
            const bs=[...(f||document).querySelectorAll('button,input[type=submit],input[type=button],a.btn')].filter(b=>b.offsetParent);
            const b=bs.find(b=>re.test(b.value||b.textContent)&&!no.test(b.value||b.textContent));
            if(b){b.click();return (b.value||b.textContent).trim()} return ''}""")
        if not clicked:
            page.locator("#txt_Captcha_login:visible").first.press("Enter")
        page.wait_for_timeout(6000)
        if logged_in(page):
            job.upd(status="filling", captchaImg="", message="", step="Signed in as " + acc["username"], at=now())
            return
        msg = " | ".join(DLG) or page.evaluate("()=>[...document.querySelectorAll('.alert,.error,.text-danger,.swal2-popup,.jconfirm-box,.bootbox,.modal.show .modal-body')].filter(e=>e.offsetParent&&e.innerText.trim().length<200&&!/PRESENCE/i.test(e.innerText)).map(e=>e.innerText.trim()).join(' | ').slice(0,200)")
        job.step(f"Login try {attempt + 1}: button '{clicked or 'Enter key'}'", page)
        for sel in ["button:has-text('OK')", "button:has-text('Ok')", ".swal2-confirm"]:
            b = page.locator(sel)
            if b.count() and b.first.is_visible():
                try:
                    b.first.click(timeout=2000)
                except Exception:
                    pass
        job.upd(message=(msg or "Login did not go through. Check the captcha and try again.") + f" (try {attempt + 2} of 6)")
    raise Stop("Login failed 5 times. Check the login ID and password in Clients & Staff.")


# ---------------- Deed of Receipt ----------------
def g(t, *keys):
    for k in keys:
        v = t.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def sal_re(s):
    return {"mr": "^Mr\\.?$|^Mr\\.", "mrs": "^Mrs", "ms": "^Ms", "miss": "^Ms", "m/s": "^M/s"}.get(s.lower().strip(". "), "^Mr\\.?")


def rel_re(s):
    s = s.lower()
    return "^Wife of" if s.startswith("w") else "^Daughter of" if s.startswith("d") else "^Care of" if s.startswith("c") else "^Son of"


def person(P, t, pre, designation=True):
    sal = g(t, f"{pre} salutation") or "Mr"
    P.sel("salutationId", sal_re(sal))
    P.set("fullName", g(t, f"{pre} name", pre))
    P.tamil("fullNameLL", g(t, f"{pre} name (Tamil)"), g(t, f"{pre} name", pre))
    P.sel("firstRelationTypeId", rel_re(g(t, f"{pre} relation") or "Son of"))
    P.set("firstRelativeName", g(t, f"{pre} relative name"))
    P.tamil("firstRelativeNameLL", g(t, f"{pre} relative name (Tamil)"), g(t, f"{pre} relative name"))
    P.set("age", re.sub(r"\D", "", g(t, f"{pre} age", f"{pre} Age")))
    gen = g(t, f"{pre} gender") or ("Female" if sal.lower().startswith(("mrs", "ms", "miss")) else "Male")
    P.sel("genderId", "^" + gen)
    ms = g(t, f"{pre} marital status")
    if ms:
        P.sel("martialStatusId", "^" + ms)
    P.sel("identificationTypeId", "Aadhaar")
    P.set("identificationNo", re.sub(r"\D", "", g(t, f"{pre} Aadhaar")))
    if designation and g(t, f"{pre} designation"):
        P.set("designation", g(t, f"{pre} designation"))
        P.tamil("designationLL", "", g(t, f"{pre} designation"))
    P.js("tick('aadhaarConsentCheckBox')")


def address(P, state="Tamil Nadu", district="", taluk="", village="", addr="", addr_ta="", pin="", mobile=""):
    st = (state or "Tamil Nadu").strip()
    if re.sub(r"[^a-z]", "", st.lower()) == "tamilnadu":
        st = "Tamil Nadu"
    P.sel("cmb_State", "^" + re.escape(st).replace("\\ ", " ?"), 2)
    if district:
        P.fuzzy("cmb_District", district, 2.5)
    if taluk:
        P.fuzzy("cmb_Taluk", taluk, 2.5)
    if village:
        P.fuzzy("cmb_Village", village, 1)
    P.set("txt_FullAddress", addr)
    P.tamil("multi_tamil_fullAddress", addr_ta, addr)
    P.set("txt_PinCode", pin)
    if mobile:
        for f in ("mobileNo", "mobileNo_1"):
            P.set(f, mobile)


def add_and_next(P, what):
    t = P.click("addExecutantDetailsInGrid", 3)
    if t and not re.search(r"success|added", t, re.I):
        raise Stop(f"{what}: TNREGINET said '{t[:160]}'")
    P.click("btnChildNext", 4)


def wait_for(P, js_cond, secs=25):
    """Poll until a JS condition is true (TNREGINET loads sub-tabs and lists by ajax)."""
    for _ in range(int(secs * 2)):
        try:
            if P.p.evaluate(js_cond):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def open_subtab(P, name, marker):
    """Open a Party Details sub-tab and wait until its form has loaded."""
    for attempt in range(3):
        P.js(f"(()=>{{const a=[...document.querySelectorAll('a')].find(x=>x.offsetParent&&x.textContent.replace(/[\"\\s]+/g,' ').trim()==={name!r});if(a)a.click()}})()")
        P.wait(1.5)
        P.popup_ok()
        if wait_for(P, f"()=>{{const e=document.getElementById({marker!r});return !!(e&&e.offsetParent)}}", 20):
            P.wait(1)
            return True
    P.job.step(f"{name} tab did not open", P.p)
    raise Stop(f"The {name} tab did not open on TNREGINET. Send the screen to Claude.")


def click_text(page, pattern):
    """Click the smallest visible element whose text matches (Tamil or English)."""
    return page.evaluate("""(pat)=>{const re=new RegExp(pat,'i');
        const els=[...document.querySelectorAll('a,button,div,span,li,p,h1,h2,h3,h4,h5,h6,label')].filter(e=>e.offsetParent&&re.test(e.textContent||''));
        els.sort((a,b)=>(a.textContent||'').length-(b.textContent||'').length);
        const e=els[0]; if(!e) return ''; (e.closest('a,button,[onclick],.card,.tile')||e).click(); return (e.textContent||'').trim().slice(0,60)}""", pattern)


def to_english(page):
    """TNREGINET remembers the language; the form option names must be English for our matching."""
    if page.evaluate("()=>/[\\u0B80-\\u0BFF]{4}/.test((document.querySelector('h1,h2,h3,.breadcrumb,nav')||document.body).innerText)"):
        hit = page.evaluate("""()=>{const e=[...document.querySelectorAll('a,button,span')].find(x=>x.offsetParent&&/^\\s*English\\s*$/i.test(x.textContent));if(e){e.click();return true}return false}""")
        if hit:
            page.wait_for_timeout(1500)
            # TNREGINET asks "change the language to English?" - press OK
            page.evaluate("""()=>{const b=[...document.querySelectorAll('button,input[type=button],a')].find(x=>x.offsetParent&&/^\\s*(OK|சரி)\\s*$/i.test(x.value||x.textContent));if(b)b.click()}""")
            page.wait_for_timeout(4500)


def open_presenceless(page, job):
    """From the signed-in home page, open Presence-less registration (Tamil or English menu)."""
    to_english(page)
    if page.locator("#deedType").count():
        return
    for attempt in range(3):
        if attempt or "error" in page.url.lower() or page.evaluate("()=>/தவறு ஏற்பட்டுள்ளது|error occurred/i.test(document.body.innerText)"):
            page.goto(URL, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(3000)
        hit = click_text(page, "நேரடி வருகை இல்லா|presence ?less")
        page.wait_for_timeout(4000)
        if page.locator("#deedType").count():
            return
        # some versions show a second choice / proceed step
        click_text(page, "ஆவணம் உருவாக்க|create document|புதிய ஆவணம்|new document|proceed|தொடர")
        page.wait_for_timeout(3500)
        if page.locator("#deedType").count():
            return
        job.step(f"Looking for Presence-less registration (clicked: {hit or 'nothing'})", page)


TAMIL_RE = re.compile(r"[஀-௿]")


def english_pass(P, job, what, only=None):
    """English fields on the visible form that still hold Tamil (copied from the linked MODT):
    ask the portal (Claude) for the English form and fill only those fields. Tamil fields stay as they are."""
    texts = P.p.evaluate(r"""()=>{const o={};document.querySelectorAll('input[type=text],textarea').forEach(e=>{
        if(!e.offsetParent||!e.id||/LL$|tamil|_ta$|Tamil/i.test(e.id)) return; const v=(e.value||'').trim();
        if(/[஀-௿]/.test(v)) o[e.id]=v}); return o}""")
    if only:
        texts = {k: v for k, v in texts.items() if re.search(only, k, re.I)}
    if not texts:
        return {}
    tr = uuid.uuid4().hex[:8]
    job.upd(status="translate", toEnglish=texts, english=None, trId=tr, trWhat=what, at=now())
    eng = None
    for _ in range(120):
        d = job.fresh()
        if d.get("status") == "cancel":
            raise Stop("Stopped from the portal")
        if d.get("english") is not None and d.get("trId") == tr:
            eng = d.get("english") or {}
            break
        time.sleep(1)
    job.upd(status="filling", toEnglish=None)
    if eng is None:
        P.warn.append(f"{what}: Tamil not converted (keep the portal open during TP)")
        return {}
    done = {}
    for k, v in eng.items():
        if k in texts and v and not TAMIL_RE.search(str(v)):
            P.set(k, str(v).strip())
            done[k] = v
    missed = [k for k in texts if k not in done]
    if missed:
        P.warn.append(f"{what}: still Tamil in {', '.join(missed)}")
    return done


def click_btn_text(P, pattern):
    return P.p.evaluate("""(pat)=>{const re=new RegExp(pat,'i');const b=[...document.querySelectorAll('button,input[type=button],input[type=submit],a.btn')]
        .find(x=>x.offsetParent&&re.test((x.value||x.innerText||'').trim()));if(!b)return '';b.click();return (b.id||'')+':'+(b.value||b.innerText||'').trim()}""", pattern)


def edit_first_row(P, table_id="tableForExecutantDtls", row_re=""):
    """Press the edit (pencil) icon of the first matching saved row. Returns the row text or ''."""
    return P.p.evaluate("""([tid,rr])=>{const t=document.getElementById(tid)||[...document.querySelectorAll('table')].find(x=>x.offsetParent&&x.rows.length>1);
        if(!t||!t.offsetParent||t.rows.length<2)return '';const re=rr?new RegExp(rr,'i'):null;
        for(const r of [...t.rows].slice(1)){if(re&&!re.test(r.innerText))continue;
          const els=[...r.querySelectorAll('a,button,i,img,span')];
          const e=els.find(x=>/edit|pencil|modify/i.test((x.className||'')+' '+(x.title||'')+' '+(x.getAttribute('onclick')||'')+' '+(x.getAttribute('src')||'')))||r.cells[r.cells.length-1].querySelector('a,button,i,img');
          if(e){(e.closest('a,button')||e).click();return r.innerText.replace(/\\s+/g,' ').slice(0,120)}}return ''}""", [table_id, row_re])


def current_age(P, t):
    """Age in the saved entry is the age at the MODT; add the years since the MODT."""
    try:
        a = int(re.sub(r"\D", "", P.p.evaluate("()=>{const e=document.getElementById('age');return e?e.value:''}") or "") or
                re.sub(r"\D", "", g(t, "Mortgagor age")) or 0)
    except ValueError:
        return
    y = re.sub(r"\D", "", g(t, "Year"))
    if not a:
        return
    if y and len(y) == 4:
        a += max(0, time.localtime().tm_year - int(y))
    P.set("age", str(a))



def run_dor(page, job, case):
    t = case.get("tp") or {}
    P = Portal(page, job)
    # 1. create & link previous document
    job.step("1 · Create document & link the MODT")
    open_presenceless(page, job)
    if not page.locator("#deedType").count():
        job.step("Create Document page not found", page)
        raise Stop("The Create Document page did not open. Send the screen to Claude.")
    P.sel("deedType", "^Deed Of Receipt")
    P.js("tick('isPrevDocReg')"); P.wait(1.5)
    P.js("tick('linkPrevDoc')"); P.wait(2)
    r = P.fuzzy("sub_reg_off", g(t, "SRO"), 2)
    P.sel("sub_book_no", f"Book {g(t, 'Book') or 1}$", 0.5)
    P.set("sub_doc_no", g(t, "Doc No"))
    P.sel("sub_year", f"^{g(t, 'Year')}$", 0.5)
    P.sel("cmb_doc_type", "^" + (g(t, "Document type") or "Regular Document"), 0.5)
    P.click("changeBookDocSearchForLinkDoc", 5)
    ok = P.js("""(()=>{const t=[...document.querySelectorAll('table')].find(t=>t.offsetParent && /Nature of Document/.test(t.innerText));
        if(!t) return 'NOTFOUND'; const r=t.querySelector('input[type=radio],input[type=checkbox]'); if(!r) return 'NOROW'; r.click(); return t.innerText.split('\\n')[1]})()""")
    if ok in ("NOTFOUND", "NOROW"):
        job.step("MODT not found in search", page)
        raise Stop(f"TNREGINET could not find the MODT {g(t,'SRO')} {g(t,'Doc No')}/{g(t,'Year')}. Check SRO, Doc No and Year in Drafting.")
    P.wait(1)
    # The linked MODT must be ADDED (saved) before Proceed - otherwise the draft has no linked document.
    added = click_btn_text(P, r"^(add|link|add document|சேர்)$")
    P.wait(3); pm = P.popup_ok()
    job.step(f"Linked MODT added ({added or 'no Add button'}) {pm[:80]}", page)
    if not added:
        P.warn.append("Link: no Add button seen after the search")
    P.click("createDocProceed", 6)
    job.step("2 · Mortgagee (company)", page)
    if P.p.evaluate("()=>{const t=document.getElementById('tableForExecutantDtls');return !!(t&&t.offsetParent&&t.rows.length>1)}"):
        mortgagee_edit(P, job, page, t)
    else:
        mortgagee_new(P, job, page, t)
    mortgagor(P, job, page, t)
    rep_witness_presenter(P, job, page, t)
    comments, _seen, _done = property_step(P, job, page, t)
    return finish_dor(P, job, page, t, case, comments)


def mortgagee_edit(P, job, page, t):
    """The linked MODT saves only the company name. Open that entry and type the rest (address, pincode, contact)."""
    row = edit_first_row(P)
    wait_for(P, "()=>{const e=document.getElementById('partyType');return !!(e&&e.offsetParent&&e.value)}", 15)
    P.wait(2)
    job.step(f"2 · Mortgagee: filling details for {row[:60]}", page)
    if P.p.evaluate("()=>{const g=document.getElementById('govtBodyList'),o=document.getElementById('txt_OrgName');return !!(g&&g.offsetParent&&!g.value&&!(o&&o.offsetParent&&o.value))}"):
        P.js(f"selco('govtBodyList',{g(t, 'Company name')!r})"); P.wait(2)
    address(P, g(t, "Company state") or "Tamil Nadu", g(t, "Company district"), addr=g(t, "Company address"),
            addr_ta=g(t, "Company address (Tamil)"), pin=g(t, "Company pincode"))
    for f in ("mobileNo_1", "mobileNo"):
        if g(t, "Company mobile"):
            P.set(f, g(t, "Company mobile"))
    english_pass(P, job, "Mortgagee")
    b = click_btn_text(P, r"^update$")
    P.wait(3); msg = P.popup_ok()
    if not b:
        P.warn.append("Mortgagee: no Update button")
    elif msg and not re.search(r"success|updated|added", msg, re.I):
        raise Stop(f"Mortgagee update: TNREGINET said '{msg[:160]}'")
    job.step("2 · Mortgagee updated", page)
    P.click("btnChildNext", 4)


def mortgagee_new(P, job, page, t):
    P.sel("partyType", "Company", 3)
    co = g(t, "Company name")
    r = P.js(f"selco('govtBodyList',{co!r})")
    P.wait(2)
    if not str(r).startswith("ok"):
        job.step("Company not found in TNREGINET list", page)
        raise Stop(f"TNREGINET's company list has no match for '{co}'. Check the company name in Drafting.")
    address(P, g(t, "Company state") or "Tamil Nadu", g(t, "Company district"), addr=g(t, "Company address"),
            addr_ta=g(t, "Company address (Tamil)"), pin=g(t, "Company pincode"))
    add_and_next(P, "Company")


def mortgagor(P, job, page, t):
    job.step("3 · Mortgagor (borrower)", page)
    wait_for(P, "()=>{const e=document.getElementById('partyType');return !!(e&&e.offsetParent)}", 15)
    row = edit_first_row(P)
    if row:
        # The MODT link already saved the mortgagor with Tamil name/address: only turn the English fields to English.
        wait_for(P, "()=>{const e=document.getElementById('fullName');return !!(e&&e.offsetParent&&e.value)}", 15)
        P.wait(1.5)
        job.step(f"3 · Editing saved mortgagor: {row[:60]}", page)
        english_pass(P, job, "Mortgagor")
        current_age(P, t)
        P.js("tick('aadhaarConsentCheckBox')")
        b = click_btn_text(P, r"^update$")
        P.wait(3); msg = P.popup_ok()
        if not b:
            P.warn.append("Mortgagor: no Update button")
        elif msg and not re.search(r"success|updated|added", msg, re.I):
            raise Stop(f"Mortgagor update: TNREGINET said '{msg[:160]}'")
        job.step("3 · Mortgagor updated", page)
        return
    P.sel("partyType", "Individual")
    person(P, t, "Mortgagor", designation=False)
    P.sel("professionId", "Others")
    P.set("professionOther", g(t, "Mortgagor profession") or ("Housewife" if g(t, "Mortgagor relation").lower().startswith("w") else "Others"))
    address(P, "Tamil Nadu", g(t, "Mortgagor district"), g(t, "Mortgagor taluk"), g(t, "Mortgagor village"),
            g(t, "Mortgagor address"), "", g(t, "Mortgagor pincode"), g(t, "Mortgagor mobile"))
    add_and_next(P, "Mortgagor")


def rep_witness_presenter(P, job, page, t):
    job.step("4 · Company representative", page)
    open_subtab(P, "Representative Details", "representedParty")
    P.sel("representedParty", "^Mortgagee", 1)
    wait_for(P, "()=>!!document.querySelector('[id^=representedPartyNames_]')", 15)
    P.js("[...document.querySelectorAll('[id^=representedPartyNames_]')].forEach(c=>{if(!c.checked)c.click()})")
    P.wait(2)
    wait_for(P, "()=>document.getElementById('representationBy').options.length>1", 15)
    P.sel("representationBy", "Company Representative", 1)
    wait_for(P, "()=>{const e=document.getElementById('designation');return !!(e&&e.offsetParent)}", 15)
    person(P, t, "Rep")
    address(P, g(t, "Rep state") or "Tamil Nadu", g(t, "Rep district"), addr=g(t, "Rep address"),
            addr_ta=g(t, "Rep address (Tamil)"), pin=g(t, "Rep pincode"), mobile=g(t, "Rep mobile"))
    msg = P.click("addReperstativeDtls", 3)
    P.wait(1.5); msg += " " + P.popup_ok()
    if not wait_for(P, "()=>{const t=document.getElementById('tableForRepresentativeDtls');return !!(t&&t.rows.length>1)}", 10):
        job.step("Representative not added", page)
        raise Stop(f"Representative was not added. TNREGINET said '{msg.strip()[:200]}'")
    job.step("4 · Representative added", page)
    open_subtab(P, "Witness Details", "chkWitnessType0")
    for n in (1, 2):
        if not g(t, f"Witness {n} name"):
            continue
        job.step(f"{4 + n} · Witness {n}", page)
        P.js("tick('chkWitnessType0')")
        person(P, t, f"Witness {n}", designation=False)
        address(P, "Tamil Nadu", g(t, f"Witness {n} district"), addr=g(t, f"Witness {n} address"), pin=g(t, f"Witness {n} pincode"))
        msg = P.click("addExecutantDetailsInGrid", 3)
        if msg and not re.search(r"success|added", msg, re.I):
            raise Stop(f"Witness {n}: TNREGINET said '{msg[:160]}'")
    open_subtab(P, "Presenter Details", "cmb_presenter")
    job.step("7 · Presenter", page)
    wait_for(P, "()=>document.getElementById('cmb_presenter').options.length>1", 15)
    P.fuzzy("cmb_presenter", g(t, "Rep name"), 1)
    P.click("btnMainSaveAsDraft", 4)


ACTIVE_TAB_JS = """()=>{const clean=x=>(x.textContent||'').replace(/["\\s]+/g,' ').trim();
  const tabs=[...document.querySelectorAll('a')].filter(a=>a.offsetParent&&/^(Property|Survey|Boundary|Building|Linear|Flat|Land|Other|Patta|Remarks?)\\b.*(Details|Measurement|Remarks?)?$/i.test(clean(a))&&clean(a).length<40);
  const act=tabs.find(a=>/active|selected|current/i.test(a.className+' '+(a.parentElement||{}).className)||a.getAttribute('aria-selected')==='true');
  if(act) return clean(act);
  const heads=[...document.querySelectorAll('h1,h2,h3,h4,h5,h6,b,strong,.title,legend')].filter(h=>h.offsetParent).map(clean);
  const hit=tabs.map(clean).find(n=>heads.includes(n)); return hit||''}"""
FIELDS_JS = """()=>[...document.querySelectorAll('input,select,textarea,button')].filter(e=>e.offsetParent&&e.type!=='hidden').map(e=>{
  let l=e.labels&&e.labels[0]?e.labels[0].innerText.trim():'';if(!l){let p=e.parentElement;for(let i=0;i<3&&p&&!l;i++){const x=p.querySelector('label');if(x)l=x.innerText.trim();p=p.parentElement}}
  const v=e.tagName==='SELECT'?((e.selectedOptions[0]||{}).text||'')+' {'+[...e.options].slice(0,10).map(o=>o.text.trim()).join('|')+'}':(e.type==='password'?'':(e.type==='checkbox'||e.type==='radio'?String(e.checked):(e.value||e.innerText||'')));
  return (e.id||e.name)+' | '+(e.type||e.tagName)+' | '+l.slice(0,50)+' | '+String(v).slice(0,140)}).slice(0,150)"""
ACTIVE_STEP_JS = """()=>{const h=[...document.querySelectorAll('a,li,h4,h5,b,strong')].filter(e=>e.offsetParent&&/^(Loan Repayment|Supporting Doc|Document Preview|Document Val|Property Details)/i.test((e.textContent||'').trim())&&/active|current/i.test(e.className+' '+(e.parentElement||{}).className));return h.length?h[0].textContent.trim().slice(0,40):''}"""
ROWS_JS = """()=>{const t=[...document.querySelectorAll('table')].filter(x=>x.offsetParent&&x.rows.length>1);let n=0;
    t.forEach(x=>[...x.rows].slice(1).forEach(r=>{if([...r.querySelectorAll('a,button,i,img,span')].some(e=>/edit|pencil|modify/i.test((e.className||'')+' '+(e.title||'')+' '+(e.getAttribute('onclick')||'')+' '+(e.getAttribute('src')||''))))n++}));return n}"""
EDIT_ROW_JS = """(i)=>{const rows=[];[...document.querySelectorAll('table')].filter(x=>x.offsetParent&&x.rows.length>1).forEach(x=>[...x.rows].slice(1).forEach(r=>{
    const e=[...r.querySelectorAll('a,button,i,img,span')].find(e=>/edit|pencil|modify/i.test((e.className||'')+' '+(e.title||'')+' '+(e.getAttribute('onclick')||'')+' '+(e.getAttribute('src')||'')));if(e)rows.push([r,e])}));
    const x=rows[i];if(!x)return '';(x[1].closest('a,button')||x[1]).click();return x[0].innerText.replace(/\\s+/g,' ').slice(0,100)}"""
PAGE_TXT_JS = """()=>[...document.querySelectorAll('input,select,textarea')].filter(e=>e.offsetParent&&e.type!=='password').map(e=>e.tagName==='SELECT'?((e.selectedOptions[0]||{}).text||''):e.value).join(' | ')+' | '+[...document.querySelectorAll('table')].filter(x=>x.offsetParent).map(x=>x.innerText).join(' | ')"""


def property_step(P, job, page, t):
    """Property details come from the linked MODT. Walk every property sub-tab (Property, Survey, Boundary,
    Building, ...): for saved rows press Edit, turn the Tamil English-fields (incl. 4 boundaries & remarks) into
    English and Update. Compare with the MODT, note mismatches as comments, save the draft and stop for the MD."""
    job.step("8 · Property details", page)
    P.click("btnMainNext", 5)
    wait_for(P, "()=>/Property Details/i.test(document.body.innerText)&&[...document.querySelectorAll('input,select,textarea')].filter(e=>e.offsetParent).length>4", 25)
    P.wait(2)
    # The linked MODT's property is already in "List of all added property": open it with Edit.
    if not page.evaluate("()=>{const b=document.getElementById('btnChildNext');return !!(b&&b.offsetParent)}"):
        row = page.evaluate(EDIT_ROW_JS, 0)
        job.step(f"8 · Editing saved property: {row[:80] or 'no saved property row'}", page)
        if not row:
            raise Stop("No saved property came from the linked MODT. Check the link (SRO / Doc No / Year).")
        if not wait_for(P, "()=>{const b=document.getElementById('btnChildNext');return !!(b&&b.offsetParent)}", 25):
            job.step("Property sub-tabs did not open", page)
            raise Stop("The saved property did not open for editing on TNREGINET. Send the screen to Claude.")
        P.wait(2)
    comments, done_all, seen, all_txt = [], {}, [], ""
    for _ in range(10):
        tab = page.evaluate(ACTIVE_TAB_JS) or f"tab {len(seen) + 1}"
        if tab in seen:
            break
        seen.append(tab)
        job.step(f"8 · Property · {tab}", page)
        bound_page = page.evaluate("()=>[...document.querySelectorAll('input,textarea')].some(e=>e.offsetParent&&/north|south|east|west|bound/i.test(e.id||''))")
        if re.search(r"bound", tab, re.I) or bound_page:
            # Boundaries: only the upper (English) field of each side becomes English; Tamil stays.
            n = page.evaluate(ROWS_JS)
            if n:
                for i in range(min(n, 8)):
                    row = page.evaluate(EDIT_ROW_JS, i)
                    if not row:
                        break
                    P.wait(2.5)
                    d = english_pass(P, job, f"{tab} row {i + 1}")
                    done_all.update({f"{tab}:{k}": v for k, v in d.items()})
                    b = click_btn_text(P, r"^(update|save)$") if d else click_btn_text(P, r"^cancel$")
                    P.wait(2.5); msg = P.popup_ok()
                    if d and msg and not re.search(r"success|updated|saved", msg, re.I):
                        comments.append(f"{tab} row {i + 1}: TNREGINET said '{msg[:100]}'")
            else:
                d = english_pass(P, job, tab)
                done_all.update({f"{tab}:{k}": v for k, v in d.items()})
        else:
            # Property / Survey / Building / Linear measurement: verify only, never change.
            # Only a Property Remarks field (upper, English one) is turned into English.
            d = english_pass(P, job, tab, only=r"remark")
            done_all.update({f"{tab}:{k}": v for k, v in d.items()})
        all_txt += " | " + page.evaluate(PAGE_TXT_JS)
        if not page.evaluate("()=>{const b=document.getElementById('btnChildNext');return !!(b&&b.offsetParent)}"):
            break
        P.click("btnChildNext", 3)
        P.wait(1.5)
    if done_all:
        b = click_btn_text(P, r"^update$")
        if b:
            P.wait(3); msg = P.popup_ok()
            if msg and not re.search(r"success|updated|saved", msg, re.I):
                comments.append(f"Property update: TNREGINET said '{msg[:100]}'")
    # mismatch check against what Claude read from the MODT (numbers must appear somewhere on the property tabs)
    nums = lambda x: re.findall(r"\d+(?:\.\d+)?", str(x))
    page_nums = set(nums(all_txt))
    low = all_txt.lower()
    for key in ("Survey No", "Old survey no", "New survey no", "Extent"):
        v = g(t, key)
        miss = [n for n in nums(v) if n not in page_nums]
        if v and miss:
            comments.append(f"{key}: MODT '{v}' – {', '.join(miss)} not on TNREGINET")
    for key in ("Property SRO",):
        v = g(t, key)
        if v and re.sub(r"[^a-z]", "", v.lower())[:5] not in re.sub(r"[^a-z]", "", low):
            comments.append(f"SRO: MODT '{v}' not on TNREGINET property")
            break
    vil = g(t, "Village")
    if vil and re.sub(r"[^a-z]", "", vil.lower())[:6] not in re.sub(r"[^a-z]", "", low):
        comments.append(f"Village: MODT '{vil}' not on TNREGINET")
    P.click("btnMainSaveAsDraft", 4)
    job.upd(comments=comments, propTabs=seen)
    return comments, seen, done_all


# ---------------- Loan → Place of execution → Preview → Create Document → Uploads → Print ----------------
def label_fill(page, label_re, value):
    """Fill the visible textbox whose label matches (TNREGINET labels are long: 'Loan Amount as per Regist .. *')."""
    if value in ("", None):
        return False
    return page.evaluate("""([pat,v])=>{const re=new RegExp(pat,'i');
        const box=[...document.querySelectorAll('input[type=text],input:not([type]),textarea')].find(e=>{if(!e.offsetParent)return false;
          let l=e.labels&&e.labels[0]?e.labels[0].innerText:'';if(!l){let p=e.parentElement;for(let i=0;i<3&&p&&!l;i++){const x=p.querySelector('label');if(x)l=x.innerText;p=p.parentElement}}
          return re.test(l)||re.test(e.placeholder||'')||re.test(e.id||'')});
        if(!box)return false;box.value=v;['input','change','blur'].forEach(t=>box.dispatchEvent(new Event(t,{bubbles:true})));return box.id||true}""", [label_re, str(value)])


def click_first(page, sels, timeout=5000):
    """Click the first selector (in this order) that has a visible match."""
    for sel in sels:
        loc = page.locator(sel)
        if loc.count():
            try:
                loc.first.click(timeout=timeout)
                return sel
            except Exception:
                continue
    return ""


def wait_loader(page):
    try:
        page.locator("img[src*='Loader'], img[src*='loader']").first.wait_for(state="hidden", timeout=20000)
    except Exception:
        pass


def main_next(P, page, wait=4):
    msg = P.click("btnMainNext", wait)
    wait_loader(page)
    P.wait(1.5)
    return msg or ""


def tp_on_page(page):
    m = re.search(r"TP/\d+/\d{4}", page.inner_text("body"))
    return m.group(0) if m else ""


def get_file(case, key, folder):
    d = ((case.get("docs") or {}).get(key) or [])
    if not d:
        return None
    f = d[0]
    name = re.sub(r"[^\w.\- ]+", "_", f.get("name") or key)
    if not re.search(r"\.(pdf|jpe?g|png)$", name, re.I):
        name += ".pdf"
    return download(f.get("url", ""), os.path.join(folder, f"{key}_{name}"))


def ensure_pdf_tools():
    try:
        import pypdf, PIL  # noqa: F401
    except ImportError:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pypdf", "pillow"], timeout=300)


def to_pdf(paths, out):
    """Join PDFs / images into one PDF (rep Aadhaar KYC + bank ID card)."""
    ensure_pdf_tools()
    from pypdf import PdfWriter, PdfReader
    from PIL import Image
    w = PdfWriter()
    for pth in paths:
        if not pth:
            continue
        if pth.lower().endswith(".pdf"):
            for pg in PdfReader(pth).pages:
                w.add_page(pg)
        else:
            tmp = pth + ".pdf"
            Image.open(pth).convert("RGB").save(tmp, "PDF", resolution=150)
            for pg in PdfReader(tmp).pages:
                w.add_page(pg)
    with open(out, "wb") as fh:
        w.write(fh)
    return out


def sb_upload(local, path, ctype="application/pdf"):
    with open(local, "rb") as fh:
        r = HTTP.post(f"{SB}/storage/v1/object/docs/{path}", headers={**AUTH, "Content-Type": ctype, "x-upsert": "true"}, data=fh.read(), timeout=120)
    r.raise_for_status()
    return "sb:" + path


def loan_to_create(P, job, page, t, case, comments):
    # 9. Loan repayment: loan amount exactly as in the MODT
    job.step("9 · Loan repayment", page)
    main_next(P, page, 6)
    wait_for(P, "()=>/Loan Amount/i.test(document.body.innerText)", 20)
    amt = re.sub(r"[^\d.]", "", g(t, "Loan Amount", "Loan amount"))
    if not label_fill(page, r"Loan Amount", amt):
        comments.append("Loan amount box not found")
    elif not amt:
        comments.append("Loan amount missing in Drafting")
    P.wait(1)
    job.step(f"9 · Loan amount {amt}", page)
    msg = main_next(P, page, 5)
    if msg and re.search(r"mandatory|enter|invalid|required", msg, re.I):
        raise Stop(f"Loan page: TNREGINET said '{msg[:160]}'")
    # 10. Supporting documents: change nothing; only Place of execution (SRO) in English and Tamil
    job.step("10 · Supporting details · place of execution", page)
    wait_for(P, "()=>/Place of Execution|place of exec/i.test(document.body.innerText)", 20)
    sro = g(t, "Execution SRO", "SRO")
    sro_ta = g(t, "SRO Tamil", "SRO (Tamil)")
    if not label_fill(page, r"State of execution|Place of\s*Execution(?!.*Tam)", sro):
        comments.append("Place of execution (English) box not found")
    if sro_ta:
        if not label_fill(page, r"Place of Execution in Tam", sro_ta):
            comments.append("Place of execution (Tamil) box not found")
    else:
        comments.append("SRO Tamil name missing in Drafting - Place of execution (Tamil) left empty")
    P.wait(1)
    job.step("10 · Place of execution filled", page)
    msg = main_next(P, page, 5)
    if msg and re.search(r"mandatory|enter|invalid|required", msg, re.I):
        raise Stop(f"Place of execution: TNREGINET said '{msg[:160]}'")
    # 11. Print preview: look, then Next (Continue only once)
    job.step("11 · Document preview", page)
    P.wait(3)
    if page.evaluate("()=>{const b=document.getElementById('btnMainNext');return !!(b&&b.offsetParent)}"):
        main_next(P, page, 5)
    # 12. Create Document - pressed ONE time only, never again (no duplicate TP)
    tp = tp_on_page(page)
    if not tp:
        if job.fresh().get("createClicked"):
            raise Stop("Create Document was already pressed once for this job. Check My Documents before trying again.")
        btn = page.locator("#btnCreateDocument:visible, input[value='Create Document']:visible, button:has-text('Create Document'):visible")
        wait_for(P, "()=>[...document.querySelectorAll('#btnCreateDocument,input[value=\"Create Document\"],button')].some(b=>b.offsetParent&&/Create Document/i.test(b.value||b.innerText||''))", 20)
        if not btn.count():
            job.step("Create Document button not found", page)
            raise Stop("The Create Document button did not appear. The draft is saved - send the screen to Claude.")
        job.upd(createClicked=now())
        job.step("12 · Create Document (pressed once)", page)
        btn.first.click()
        wait_loader(page)
        for _ in range(20):
            P.wait(1.5)
            P.popup_ok()  # 'Continue' / OK confirmations - each dialog answered once
            tp = tp_on_page(page)
            if tp:
                break
            c2 = page.locator("button:has-text('Continue'):visible, input[value='Continue']:visible")
            if c2.count() and not job.d.get("continueClicked"):
                job.upd(continueClicked=now())
                c2.first.click()
        if not tp:
            job.step("TP number not seen", page)
            raise Stop("Create Document was pressed but no TP number appeared. Check My Documents (do NOT press Create again).")
    job.upd(tpNo=tp)
    kv_merge("cases", job.d.get("case", ""), {"tpNo": tp})
    job.step(f"12 · TP created: {tp}", page)
    try:
        page.locator("button:has-text('Close'):visible").first.click(timeout=3000)
    except Exception:
        pass
    return tp


def open_my_docs_row(P, job, page, tp):
    page.evaluate("""()=>{const a=[...document.querySelectorAll('a,[title]')].find(e=>/^My Documents?$/i.test((e.title||e.textContent||'').trim()));if(a)a.click()}""")
    wait_loader(page); P.wait(3)
    no = tp.split("/")[1] if "/" in tp else tp
    for _ in range(3):
        ok = page.evaluate("""(no)=>{const tr=[...document.querySelectorAll('tr')].find(r=>r.offsetParent&&r.innerText.includes(no));
            if(!tr)return false;const i=tr.querySelector('input[type=checkbox],input[type=radio]');if(!i)return false;if(!i.checked)i.click();return true}""", no)
        if ok:
            return True
        P.wait(3)
    job.step(f"{tp} not found in My Documents", page)
    raise Stop(f"{tp} was created but not found in My Documents. Upload the documents by hand.")


def upload_docs(P, job, page, t, case, comments):
    tp = job.d.get("tpNo")
    job.step("13 · Upload documents", page)
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files", job.id[:8])
    os.makedirs(folder, exist_ok=True)
    rep_k, rep_i = get_file(case, "rep_kyc", folder), get_file(case, "rep_id", folder)
    rep_pdf = None
    if rep_k or rep_i:
        try:
            rep_pdf = to_pdf([rep_k, rep_i], os.path.join(folder, "Rep_KYC_and_ID.pdf"))
        except Exception as e:
            P.warn.append(f"Rep KYC+ID join failed ({e}); uploading the KYC only")
            rep_pdf = rep_k or rep_i
    first = lambda n: (n or "").split()[0] if n else ""
    plan = [
        ("Identification", g(t, "Rep name"), rep_pdf, "Bank rep KYC + ID"),
        ("Identification", g(t, "Witness 1 name"), get_file(case, "w1_kyc", folder), "Witness 1 KYC"),
        ("Identification", g(t, "Witness 2 name"), get_file(case, "w2_kyc", folder), "Witness 2 KYC"),
        ("Reference", "Reference Original", get_file(case, "modt", folder), "MODT"),
        ("Reference", "Reference Bank Cert|Bank Cert", get_file(case, "noc", folder), "NOC"),
    ]
    open_my_docs_row(P, job, page, tp)
    if not click_first(page, ["button:has-text('Upload Documents'):visible", "input[value='Upload Documents']:visible", "a:has-text('Upload Documents'):visible"]):
        job.step("Upload Documents button not found", page)
        raise Stop("TP created, but the Upload Documents button was not found. Upload by hand.")
    wait_loader(page); P.wait(2)
    done = []
    for typ, who, f, what in plan:
        if not f:
            comments.append(f"{what}: file missing in the case - not uploaded")
            continue
        job.step(f"13 · Uploading {what}", page)
        r = P.js(f"(()=>{{const s=[...document.querySelectorAll('select')].find(x=>x.offsetParent&&[...x.options].some(o=>/Identification/i.test(o.text))&&[...x.options].some(o=>/Reference/i.test(o.text)));if(!s)return 'NOSEL';window.__ut=s.id;return selre(s.id,{typ!r})}})()")
        wait_loader(page); P.wait(1.5)
        pat = re.escape(first(who)) if typ == "Identification" else who
        r2 = P.js(f"(()=>{{const s=[...document.querySelectorAll('select')].find(x=>x.offsetParent&&x.id!==window.__ut&&x.options.length>1);if(!s)return 'NOSEL';return selre(s.id,{pat!r})}})()")
        if not str(r2).startswith("ok"):
            comments.append(f"{what}: document name '{who}' not in TNREGINET list ({r2})")
            continue
        inp = page.locator("#chooseFile, input[type=file]").first
        try:
            inp.set_input_files(f)
        except Exception:
            with page.expect_file_chooser() as fc:
                page.locator("#chooseFile").click()
            fc.value.set_files(f)
        P.wait(1)
        click_first(page, ["input[value='Upload Document']:visible", "button:text-is('Upload Document'):visible", "#btnUpload:visible"])
        wait_loader(page); P.wait(2); m1 = P.popup_ok()
        before = page.evaluate("()=>[...document.querySelectorAll('table')].filter(x=>x.offsetParent).reduce((n,x)=>n+x.rows.length,0)")
        try:
            click_first(page, ["#btnAdd:visible", "button:text-is('Add'):visible", "input[value='Add']:visible"])
        except Exception:
            pass
        wait_loader(page); P.wait(2); m2 = P.popup_ok()
        after = page.evaluate("()=>[...document.querySelectorAll('table')].filter(x=>x.offsetParent).reduce((n,x)=>n+x.rows.length,0)")
        if after > before:
            done.append(what)
        else:
            comments.append(f"{what}: not added ({(m1 + ' ' + m2).strip()[:100]})")
    job.step(f"13 · Uploaded {len(done)} of {len(plan)}", page)
    if len(done) == len(plan):
        click_first(page, ["#submitUploadApp:visible", "button:text-is('Save'):visible", "input[value='Save']:visible"])
        wait_loader(page); P.wait(2); P.popup_ok()
        job.step("13 · Upload saved", page)
    else:
        comments.append("Uploads NOT saved - only " + ", ".join(done) + " added. Finish uploads by hand and press Save.")
    return done


def save_print(P, job, page, case_id, tp):
    """My Documents → TP → Print Preview → save the PDF into the case."""
    job.step("14 · Print preview", page)
    open_my_docs_row(P, job, page, tp)
    btn = page.locator("button:has-text('Print Preview'):visible, input[value='Print Preview']:visible, a:has-text('Print Preview'):visible")
    if not btn.count():
        return "Print Preview button not found"
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files", job.id[:8])
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, tp.replace("/", "_") + ".pdf")
    try:
        with page.expect_download(timeout=40000) as dl:
            btn.first.click()
        dl.value.save_as(out)
    except Exception:
        # some versions open the PDF in a new tab
        try:
            pages = page.context.pages
            pv = pages[-1] if len(pages) > 1 else None
            if not pv:
                return "Print preview did not download"
            pv.wait_for_load_state(timeout=30000)
            resp = page.context.request.get(pv.url)
            open(out, "wb").write(resp.body())
            pv.close()
        except Exception as e:
            return f"Print preview not saved ({e})"
    path = f"tp/{time.strftime('%Y-%m')}/{tp.replace('/', '_')}_{job.id[:6]}.pdf"
    url = sb_upload(out, path)
    c = kv_get("cases", case_id) or {}
    docs = c.get("docs") or {}
    docs["tpcopy"] = [{"name": tp.replace("/", "_") + " print.pdf", "url": url, "at": now()}]
    kv_merge("cases", case_id, {"docs": docs, "tpPrint": url})
    return ""


def finish_dor(P, job, page, t, case, comments):
    tp = loan_to_create(P, job, page, t, case, comments)
    done = upload_docs(P, job, page, t, case, comments)
    err = save_print(P, job, page, job.d.get("case", ""), tp)
    if err:
        comments.append(err)
    note = f"TP created: {tp}. Uploaded: {', '.join(done) or 'none'}. Print saved in the case. Do biometric + payment yourself. "
    if comments:
        note += "CHECK: " + " · ".join(comments) + ". "
    if P.warn:
        note += "Warnings: " + "; ".join(P.warn[:8])
    job.step("Finished - stopped before payment", page)
    job.upd(status="done", tpNo=tp, message=note.strip(), comments=comments)
    return tp


def handle(pw, jid, data):
    job = Job(jid, data)
    job.upd(status="starting", pickedAt=now(), message="")
    case = kv_get("cases", data.get("case", ""))
    if not case:
        raise Stop("Case not found")
    acc = login_of(data.get("login", ""))
    if not acc:
        raise Stop("Login ID not found. Add it in Clients & Staff → TNREGINET login IDs.")
    browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="en-IN", timezone_id="Asia/Kolkata",
                              user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    page = ctx.new_page()
    page.on("dialog", lambda d: (DLG.append(d.message[:150]), d.accept()))
    try:
        try:
            do_login(page, job, acc)
        except Exception:
            job.step("Problem at login", page)
            raise
        if data.get("kind", "dor") != "dor":
            raise Stop("Only Deed of Receipt is automated so far")
        try:
            tp = run_dor(page, job, case)
        except Exception:
            job.step("Stopped here", page)
            raise
        if tp and tp.startswith("TP/"):
            kv_merge("cases", data["case"], {"tpNo": tp})
        # keep the browser open a while so a follow-up step can continue (future); then close
    finally:
        try:
            job.d["shot"] = None
            ctx.close(); browser.close()
        except Exception:
            pass


SRC = "https://raw.githubusercontent.com/vgrlegal/portal/main/worker/tp_worker.py"


def self_update():
    """Pick up new versions pushed to GitHub (systemd restarts us)."""
    try:
        r = HTTP.get(SRC, timeout=20)
        me = os.path.abspath(__file__)
        if r.ok and "def main()" in r.text and r.text != open(me).read():
            open(me, "w").write(r.text)
            log("updated from GitHub, restarting")
            sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        log("update check failed", e)


PROBE = {"at": 0, "res": "", "ok": False}


def probe():
    """Can this computer reach TNREGINET? (reported in the heartbeat)"""
    out = []
    for u in ("https://tnreginet.gov.in/portal/", "https://tnreginet.gov.in/", "https://www.google.com/"):
        t = time.time()
        try:
            r = requests.get(u, timeout=25, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"})
            out.append(f"{u.split('/')[2]}{'/portal' if 'portal' in u else ''}: {r.status_code} {time.time()-t:.1f}s")
        except Exception as e:
            out.append(f"{u.split('/')[2]}{'/portal' if 'portal' in u else ''}: {type(e).__name__} {time.time()-t:.1f}s")
    try:
        ip = requests.get("https://ipinfo.io/json", timeout=10).json()
        out.append(f"ip {ip.get('ip')} {ip.get('country')} {ip.get('org','')[:40]}")
    except Exception:
        pass
    PROBE.update(at=time.time(), res=" | ".join(out), ok=any(x.startswith("tnreginet") and ": 200" in x for x in out))
    log("probe", PROBE["res"])


def main():
    log("TP worker started", "key type:", "secret" if KEY.startswith("sb_secret_") else "jwt" if KEY.startswith("eyJ") else ("EMPTY" if not KEY else "unknown"))
    with sync_playwright() as pw:
        last_beat, last_upd = 0, time.time()
        while True:
            if time.time() - last_upd > 300:
                last_upd = time.time()
                self_update()
            try:
                if time.time() - PROBE["at"] > (1800 if PROBE["ok"] else 300):
                    probe()
                if not PROBE["ok"]:
                    # this computer cannot open TNREGINET: stay out of the way (another computer may take the jobs)
                    kv_set("tp_jobs", "_blocked_" + os.uname().nodename, {"at": now(), "probe": PROBE["res"]})
                    time.sleep(30)
                    continue
                if not PROBE.get("cleaned"):
                    # jobs left half-way by an earlier run of this worker can never continue: close them
                    for row in kv_list("tp_jobs"):
                        st = (row["data"] or {}).get("status")
                        if not row["id"].startswith("_") and st in ("starting", "captcha", "captcha_sent", "filling", "uploading", "cancel"):
                            kv_merge("tp_jobs", row["id"], {"status": "cancelled", "message": "Stopped (the TP computer restarted). Start again.", "at": now()})
                    PROBE["cleaned"] = True
                if time.time() - last_beat > 30:
                    kv_set("tp_jobs", "_worker", {"at": now(), "host": os.uname().nodename, "probe": PROBE["res"]})
                    last_beat = time.time()
                for row in kv_list("tp_jobs"):
                    if row["id"].startswith("_") or (row["data"] or {}).get("status") != "queued":
                        continue
                    jid, data = row["id"], row["data"]
                    try:
                        handle(pw, jid, data)
                    except Stop as e:
                        kv_merge("tp_jobs", jid, {"status": "cancelled" if "portal" in str(e) else "error", "message": str(e), "at": now()})
                    except Exception as e:
                        traceback.print_exc()
                        kv_merge("tp_jobs", jid, {"status": "error", "message": f"Unexpected problem: {e}"[:300], "at": now()})
                    last_beat = 0
            except Exception as e:
                log("loop error", e)
                time.sleep(10)
            time.sleep(3)


if __name__ == "__main__":
    main()
