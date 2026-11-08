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


def english_pass(P, job, what, only=None, kind="english", texts=None):
    """English fields on the visible form that still hold Tamil (copied from the linked MODT):
    ask the portal (Claude) for the English form and fill only those fields. Tamil fields stay as they are."""
    texts = texts if texts is not None else P.p.evaluate(r"""()=>{const o={};document.querySelectorAll('input[type=text],textarea').forEach(e=>{
        if(!e.offsetParent||!e.id||/LL$|tamil|_ta$|Tamil/i.test(e.id)) return; const v=(e.value||'').trim();
        if(/[஀-௿]/.test(v)) o[e.id]=v}); return o}""")
    if only:
        texts = {k: v for k, v in texts.items() if re.search(only, k, re.I)}
    if not texts:
        return {}
    tr = uuid.uuid4().hex[:8]
    job.upd(status="translate", toEnglish=texts, english=None, trId=tr, trWhat=what, trKind=kind, at=now())
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
    if kind == "tamil":
        return {k: v for k, v in eng.items() if k in texts and v and TAMIL_RE.search(str(v))}
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
    link_doc(P, job, page, g(t, "SRO"), g(t, "Book") or "1", g(t, "Doc No"), g(t, "Year"), g(t, "Document type") or "Regular Document", "MODT")
    if g(t, "Supp MODT Doc No"):
        # Supplementary MODT: same party / property / company - link it too (loan amounts are added later)
        link_doc(P, job, page, g(t, "Supp MODT SRO") or g(t, "SRO"), g(t, "Supp MODT Book") or "1", g(t, "Supp MODT Doc No"),
                 g(t, "Supp MODT Year") or g(t, "Year"), "Regular Document", "Supplementary MODT")
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


def link_doc(P, job, page, sro, book, no, year, dtype, label):
    """Search one registered document (SRO / Book / Doc No / Year), tick its row and press Add so it is saved as linked."""
    job.step(f"1 · Linking {label} {sro} {no}/{year}", page)
    P.fuzzy("sub_reg_off", sro, 2)
    P.sel("sub_book_no", f"Book {book}$", 0.5)
    P.set("sub_doc_no", no)
    P.sel("sub_year", f"^{year}$", 0.5)
    P.sel("cmb_doc_type", "^" + dtype, 0.5)
    P.click("changeBookDocSearchForLinkDoc", 5)
    ok = P.js("""(()=>{const t=[...document.querySelectorAll('table')].find(t=>t.offsetParent && /Nature of Document/.test(t.innerText));
        if(!t) return 'NOTFOUND'; const r=t.querySelector('input[type=radio],input[type=checkbox]'); if(!r) return 'NOROW'; if(!r.checked) r.click(); return t.innerText.split('\\n')[1]})()""")
    if ok in ("NOTFOUND", "NOROW"):
        job.step(f"{label} not found in search", page)
        raise Stop(f"TNREGINET could not find the {label} {sro} {no}/{year}. Check SRO, Doc No and Year in Drafting.")
    P.wait(1)
    # The linked document must be ADDED (saved) before Proceed - otherwise the draft has no linked document.
    added = click_btn_text(P, r"^(add|link|add document|சேர்)$")
    P.wait(3); pm = P.popup_ok()
    job.step(f"Linked {label} added ({added or 'no Add button'}) {pm[:80]}", page)
    if not added:
        P.warn.append(f"Link {label}: no Add button seen after the search")


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


EXEC_ROWS_JS = """()=>{const t=document.getElementById('tableForExecutantDtls');if(!t||!t.offsetParent)return [];
  return [...t.rows].slice(1).filter(r=>r.cells.length>2).map(r=>r.cells[1].innerText.trim())}"""
EDIT_EXEC_ROW_JS = """(i)=>{const t=document.getElementById('tableForExecutantDtls');if(!t)return '';const r=[...t.rows].slice(1).filter(r=>r.cells.length>2)[i];if(!r)return '';
  const e=[...r.querySelectorAll('a,button,i,img,span')].find(e=>/edit|pencil|modify/i.test((e.className||'')+' '+(e.title||'')+' '+(e.getAttribute('onclick')||'')+' '+(e.getAttribute('src')||'')))||r.cells[r.cells.length-1].querySelector('a,button,i,img');
  if(!e)return '';(e.closest('a,button')||e).click();return r.innerText.replace(/\\s+/g,' ').slice(0,80)}"""


def mortgagor(P, job, page, t):
    job.step("3 · Mortgagor (borrower)", page)
    wait_for(P, "()=>{const e=document.getElementById('partyType');return !!(e&&e.offsetParent)}", 15)
    rows = page.evaluate(EXEC_ROWS_JS)
    if rows:
        # The MODT link saved EVERY mortgagor with Tamil name/address: edit each one and turn only the English fields to English.
        for attempt in range(2):
            for i in range(len(rows)):
                if attempt and not TAMIL_RE.search(rows[i]):
                    continue  # second pass: only rows that still show Tamil
                row = page.evaluate(EDIT_EXEC_ROW_JS, i)
                if not row:
                    continue
                wait_for(P, "()=>{const e=document.getElementById('fullName');return !!(e&&e.offsetParent&&e.value)}", 15)
                P.wait(1.5)
                job.step(f"3 · Mortgagor {i + 1} of {len(rows)}: {row[:60]}", page)
                english_pass(P, job, f"Mortgagor {i + 1}")
                current_age(P, t)
                P.js("tick('aadhaarConsentCheckBox')")
                b = click_btn_text(P, r"^update$")
                P.wait(3); msg = P.popup_ok()
                if not b:
                    P.warn.append(f"Mortgagor {i + 1}: no Update button")
                elif msg and not re.search(r"success|updated|added|also going to execute", msg, re.I):
                    P.warn.append(f"CHECK Mortgagor {i + 1}: TNREGINET said '{msg[:120]}'")
                wait_for(P, "()=>{const t=document.getElementById('tableForExecutantDtls');return !!(t&&t.offsetParent)}", 10)
            rows = page.evaluate(EXEC_ROWS_JS)
            if not any(TAMIL_RE.search(r) for r in rows):
                break
        left = [r for r in rows if TAMIL_RE.search(r)]
        if left:
            P.warn.append("CHECK Mortgagor: still in Tamil - " + ", ".join(left))
        job.step(f"3 · {len(rows)} mortgagor(s) updated", page)
        P.click("btnChildNext", 4)
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


UPNAME = {"rep_kyc": "REP_KYC", "rep_id": "REP_ID", "w1_kyc": "WITNESS1_KYC", "w2_kyc": "WITNESS2_KYC", "modt": "MODT", "noc": "NOC"}


def get_file(case, key, folder):
    d = ((case.get("docs") or {}).get(key) or [])
    if not d:
        return None
    f = d[0]
    ext = (re.search(r"\.(pdf|jpe?g|png)$", f.get("name") or "", re.I) or [".pdf"])[0].lower()
    return download(f.get("url", ""), os.path.join(folder, UPNAME.get(key, key.upper()) + ext))


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
    supp = re.sub(r"[^\d.]", "", g(t, "Supp loan amount"))
    if supp and amt:
        tot = float(amt) + float(supp)
        amt = str(int(tot)) if tot == int(tot) else f"{tot:.2f}"
        comments.append(f"Loan amount = MODT {g(t, 'Loan Amount', 'Loan amount')} + supplementary {g(t, 'Supp loan amount')} = {amt}")
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
    if not sro_ta and sro:
        sro_ta = english_pass(P, job, "SRO Tamil", kind="tamil", texts={"sro": sro}).get("sro", "")
        if sro_ta:
            comments.append(f"SRO Tamil was empty - used '{sro_ta}', please check")
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
    # 12. Create Document. Pressed again ONLY when TNREGINET refused it with the survey-number check
    #     and no TP number came (MD's rule), at most 3 presses in all - never a duplicate TP.
    tp = tp_on_page(page)
    if not tp:
        if job.fresh().get("createClicked"):
            raise Stop("Create Document was already pressed for this job. Check My Documents before trying again.")
        for attempt in range(1, 4):
            tp, said = press_create(P, job, page, attempt)
            if tp:
                break
            if not re.search(r"survey", said, re.I) or attempt == 3:
                job.step("TP number not seen", page)
                raise Stop(f"Create Document was pressed but no TP number appeared ({said[:150]}). Check My Documents (do NOT press Create again).")
            comments.append(f"Create Document attempt {attempt}: survey number check - retried")
            survey_fix(P, job, page, edit=(attempt == 2))
    job.upd(tpNo=tp)
    kv_merge("cases", job.d.get("case", ""), {"tpNo": tp})
    job.step(f"12 · TP created: {tp}", page)
    try:
        page.locator("button:has-text('Close'):visible").first.click(timeout=3000)
    except Exception:
        pass
    return tp


def press_create(P, job, page, attempt):
    """Press Create Document once; return (tp, what TNREGINET said)."""
    btn = page.locator("#btnCreateDocument:visible, input[value='Create Document']:visible, button:has-text('Create Document'):visible")
    wait_for(P, "()=>[...document.querySelectorAll('#btnCreateDocument,input[value=\"Create Document\"],button')].some(b=>b.offsetParent&&/Create Document/i.test(b.value||b.innerText||''))", 25)
    if not btn.count():
        job.step("Create Document button not found", page)
        raise Stop("The Create Document button did not appear. The draft is saved - send the screen to Claude.")
    job.upd(createClicked=now(), createCount=attempt)
    job.step(f"12 · Create Document (press {attempt})", page)
    btn.first.click()
    wait_loader(page)
    said, tp = "", ""
    for _ in range(20):
        P.wait(1.5)
        said += " " + P.popup_ok()
        tp = tp_on_page(page)
        if tp:
            break
        c2 = page.locator("button:has-text('Continue'):visible, input[value='Continue']:visible")
        if c2.count() and not job.d.get(f"continueClicked{attempt}"):
            job.upd(**{f"continueClicked{attempt}": now()})
            c2.first.click()
        if re.search(r"survey", said, re.I):
            break
    if not tp:
        said += " " + page.evaluate("()=>[...document.querySelectorAll('.error,.text-danger,.alert,.modal,.swal2-popup,[role=alert]')].filter(e=>e.offsetParent).map(e=>e.innerText).join(' ').slice(0,400)")
    return tp, said.strip()


def go_step(P, page, pat):
    """Click a main step in TNREGINET's top stepper (Property Details, Document Preview ...)."""
    page.evaluate("""(pat)=>{const re=new RegExp(pat,'i');const e=[...document.querySelectorAll('a,li,span,div,button')].filter(x=>x.offsetParent&&re.test((x.textContent||'').trim())&&(x.textContent||'').trim().length<40)
        .sort((a,b)=>a.textContent.length-b.textContent.length)[0];if(e)(e.closest('a,li,button')||e).click()}""", pat)
    wait_loader(page); P.wait(3); P.popup_ok()


def survey_fix(P, job, page, edit=False):
    """MD's rule for the survey-number check: back to Property Details → Survey Details
    (2nd time: press Edit on the survey row, change nothing) → Document Preview → Create Document again."""
    job.step("12 · Survey number check: back to Survey Details" + (" (Edit, no change)" if edit else ""), page)
    go_step(P, page, r"^Property Details")
    if not page.evaluate("()=>{const b=document.getElementById('btnChildNext');return !!(b&&b.offsetParent)}"):
        page.evaluate(EDIT_ROW_JS, 0)  # open the saved property
        wait_for(P, "()=>{const b=document.getElementById('btnChildNext');return !!(b&&b.offsetParent)}", 25)
        P.wait(2)
    page.evaluate("""()=>{const a=[...document.querySelectorAll('a')].find(x=>x.offsetParent&&/^Survey (Number )?Details$/i.test(x.textContent.replace(/["\\s]+/g,' ').trim()));if(a)a.click()}""")
    wait_loader(page); P.wait(3); P.popup_ok()
    if edit:
        page.evaluate(EDIT_ROW_JS, 0)  # Edit on the survey row, nothing changed
        wait_loader(page); P.wait(3); P.popup_ok()
    job.step("12 · Survey Details opened, going to Document Preview", page)
    go_step(P, page, r"^Document Pre")
    P.wait(2)
    if not page.locator("#btnCreateDocument:visible, input[value='Create Document']:visible").count() and \
            page.evaluate("()=>{const b=document.getElementById('btnMainNext');return !!(b&&b.offsetParent)}"):
        main_next(P, page, 5)


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


UPLOAD_DIAG_JS = """()=>({files:[...document.querySelectorAll('input[type=file]')].map(e=>(e.id||e.name||'?')+(e.offsetParent?' visible':' hidden')+' accept='+(e.accept||'')),
  buttons:[...document.querySelectorAll('button,input[type=button],input[type=submit],a.btn,label')].filter(b=>b.offsetParent).map(b=>(b.id||'')+'['+((b.value||b.innerText||'').trim().slice(0,30))+']').slice(0,60),
  selects:[...document.querySelectorAll('select')].filter(x=>x.offsetParent).map(x=>x.id+': '+[...x.options].map(o=>o.text.trim()).slice(0,12).join('|')),
  text:document.body.innerText.replace(/\\s+/g,' ').slice(0,1500)})"""


UP_ROWS_JS = """()=>{const t=[...document.querySelectorAll('table')].find(x=>x.offsetParent&&/Uploaded Document/i.test(x.innerText));if(!t)return [];
  return [...t.rows].slice(1).filter(r=>r.cells.length>=4).map((r,i)=>({i,name:r.cells[1].innerText.trim(),type:r.cells[2].innerText.trim(),file:r.cells[3].innerText.trim()}))}"""
DEL_ROW_JS = """(i)=>{const t=[...document.querySelectorAll('table')].find(x=>x.offsetParent&&/Uploaded Document/i.test(x.innerText));if(!t)return '';const r=[...t.rows].slice(1)[i];if(!r)return '';
  const e=[...r.querySelectorAll('a,button,i,img,span')].find(e=>/delete|trash|remove|del/i.test((e.className||'')+' '+(e.title||'')+' '+(e.getAttribute('onclick')||'')+' '+(e.getAttribute('src')||'')))||r.cells[r.cells.length-1].querySelector('a,button,i,img');
  if(!e)return '';(e.closest('a,button')||e).click();return r.innerText.replace(/\\s+/g,' ').slice(0,80)}"""


def nz(x):
    return re.sub(r"[^a-z0-9]", "", str(x).lower())


def upload_docs(P, job, page, t, case, comments):
    """Upload Documents screen: Upload Document type (docFor) → Document Name → Choose File → Add, one by one.
    Rows already there are kept if right, removed if wrong/duplicate; Save only when exactly the 5 right rows are present."""
    tp = job.d.get("tpNo")
    job.step("13 · Upload documents", page)
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files", job.id[:8])
    os.makedirs(folder, exist_ok=True)
    rep_k, rep_i = get_file(case, "rep_kyc", folder), get_file(case, "rep_id", folder)
    rep_pdf = None
    if rep_k or rep_i:
        try:
            rep_pdf = to_pdf([rep_k, rep_i], os.path.join(folder, "REP_KYC_ID.pdf"))
        except Exception as e:
            P.warn.append(f"Rep KYC+ID join failed ({e}); uploading the KYC only")
            rep_pdf = rep_k or rep_i
    plan = [  # (type option, document name (as TNREGINET lists it), file, label, file tag)
        ("Identification", g(t, "Rep name"), rep_pdf, "Bank rep KYC + ID", "REP_KYC"),
        ("Identification", g(t, "Witness 1 name"), get_file(case, "w1_kyc", folder), "Witness 1 KYC", "WITNESS1"),
        ("Identification", g(t, "Witness 2 name"), get_file(case, "w2_kyc", folder), "Witness 2 KYC", "WITNESS2"),
        ("Reference", "Reference Original Document", get_file(case, "modt", folder), "MODT", "MODT"),
        ("Reference", "Reference Bank Certificate", get_file(case, "noc", folder), "NOC", "NOC"),
    ]
    open_my_docs_row(P, job, page, tp)
    if not click_first(page, ["#upload_All_Documents:visible", "button:has-text('Upload Documents'):visible", "input[value='Upload Documents']:visible"]):
        job.step("Upload Documents button not found", page)
        raise Stop("TP created, but the Upload Documents button was not found. Upload by hand.")
    wait_loader(page)
    wait_for(P, "()=>{const s=document.getElementById('docFor');return !!(s&&s.offsetParent)}", 20)
    P.wait(2)

    def right_file(row, tag, name):
        f = row["file"].upper()
        old_style = {"REP_KYC": "REP_KYC", "WITNESS1": "W1_KYC", "WITNESS2": "W2_KYC", "MODT": "MODT", "NOC": "NOC"}[tag]
        return tag in f or old_style in f

    # 1. clean up rows from earlier tries: keep one right row per document, remove wrong / duplicate rows
    for _ in range(12):
        rows = page.evaluate(UP_ROWS_JS)
        bad = None
        seen = set()
        for r in rows:
            item = next((x for x in plan if x[1] and (nz(x[1]) == nz(r["name"]) or (x[0] == "Identification" and nz(x[1])[:6] and nz(x[1])[:6] in nz(r["name"])))), None)
            if not item or not right_file(r, item[4], item[1]) or item[3] in seen:
                bad = r
                break
            seen.add(item[3])
        if not bad:
            break
        job.step(f"13 · Removing wrong/duplicate row: {bad['name']} · {bad['file']}", page)
        page.evaluate(DEL_ROW_JS, bad["i"])
        wait_loader(page); P.wait(2); P.popup_ok(); P.wait(1.5)

    # 2. add the documents that are still missing
    done = []
    for typ, who, f, what, tag in plan:
        rows = page.evaluate(UP_ROWS_JS)
        if any(nz(who) and (nz(who) == nz(r["name"]) or (typ == "Identification" and nz(who)[:6] in nz(r["name"]))) and right_file(r, tag, who) for r in rows):
            done.append(what)
            continue
        if not f:
            comments.append(f"{what}: file missing in the case - not uploaded")
            continue
        job.step(f"13 · Uploading {what}", page)
        P.js(f"selre('docFor',{typ!r})")
        wait_loader(page)
        ok = wait_for(P, "()=>[...document.querySelectorAll('select')].some(x=>x.offsetParent&&x.id!=='docFor'&&x.options.length>1)", 15)
        P.wait(1)
        pick = page.evaluate("""([who,typ])=>{const nz=x=>String(x).toLowerCase().replace(/[^a-z0-9]/g,'');const s=[...document.querySelectorAll('select')].find(x=>x.offsetParent&&x.id!=='docFor'&&x.options.length>1);
            if(!s)return 'NOSEL';const w=nz(who);let o=[...s.options].find(o=>nz(o.text)===w)||[...s.options].find(o=>w&&nz(o.text)&&(nz(o.text).includes(w)||w.includes(nz(o.text))&&nz(o.text).length>3));
            if(!o&&typ==='Identification'){const tk=String(who).toLowerCase().split(/[^a-z]+/).filter(x=>x.length>2);o=[...s.options].find(o=>tk.length&&tk.every(k=>o.text.toLowerCase().includes(k)))}
            if(!o)return 'NOOPT: '+[...s.options].map(o=>o.text.trim()).join('|');
            if(window.jQuery)jQuery(s).val(o.value).trigger('change').trigger('chosen:updated');else{s.value=o.value;s.dispatchEvent(new Event('change',{bubbles:true}))}return 'ok '+o.text}""", [who, typ])
        if not str(pick).startswith("ok"):
            comments.append(f"{what}: '{who}' not in Document Name list ({str(pick)[:150]})")
            continue
        P.wait(1)
        try:
            page.locator("#importFilesupport, input[type=file]").first.set_input_files(f)
        except Exception:
            try:
                with page.expect_file_chooser(timeout=8000) as fc:
                    page.locator("#chooseFile").click()
                fc.value.set_files(f)
            except Exception as e:
                comments.append(f"{what}: could not attach the file ({e})")
                continue
        P.wait(1.5); P.popup_ok()
        # 'Upload Document' must be pressed after Choose File, otherwise Add says 'Please Upload Document'
        up = page.evaluate("""()=>{const b=[...document.querySelectorAll('button,input[type=button],input[type=submit],a.btn')].find(x=>x.offsetParent&&/^\\s*upload\\s*document\\s*$/i.test(x.value||x.innerText||''));if(!b)return '';b.click();return b.id||'upload'}""")
        wait_loader(page); P.wait(3); m0 = P.popup_ok()
        if not up:
            comments.append(f"{what}: Upload Document button not found")
            job.upd(uploadDiag=page.evaluate(UPLOAD_DIAG_JS))
            continue
        if m0 and re.search(r"invalid|not allowed|corrupt|size|select", m0, re.I):
            comments.append(f"{what}: TNREGINET said '{m0[:100]}'")
            continue
        page.evaluate("()=>{const b=document.getElementById('btnAdd');if(b)b.click()}")
        wait_loader(page)
        fname = os.path.basename(f).upper()
        got = wait_for(P, f"()=>[...document.querySelectorAll('table')].some(x=>x.offsetParent&&x.innerText.toUpperCase().includes({fname!r}))", 15)
        m = P.popup_ok()
        if got:
            done.append(what)
            job.step(f"13 · {what} added", page)
        else:
            comments.append(f"{what}: not added ({m[:100]})")
            job.upd(uploadDiag=page.evaluate(UPLOAD_DIAG_JS))
    rows = page.evaluate(UP_ROWS_JS)
    job.step(f"13 · {len(done)} of {len(plan)} documents in the list", page)
    if len(done) == len(plan) and len(rows) == len(plan):
        page.evaluate("()=>{const b=document.getElementById('submitUploadApp');if(b)b.click()}")
        wait_loader(page); P.wait(3); m = P.popup_ok()
        job.step(f"13 · Upload saved {m[:60]}", page)
    else:
        comments.append(f"Uploads NOT saved: {len(rows)} rows in the list, {len(done)} right. Check the list and press Save by hand.")
    return done


def _click_text_any(ctx, pattern):
    """Click the smallest visible element whose text matches, in any open tab. Returns the page it was on."""
    for pg in reversed(ctx.pages):
        try:
            hit = pg.evaluate("""(pat)=>{const re=new RegExp(pat,'i');const els=[...document.querySelectorAll('a,button,input[type=button],span,div,td,li')]
                .filter(e=>e.offsetParent&&re.test((e.value||e.textContent||'').trim()));els.sort((a,b)=>(a.textContent||'').length-(b.textContent||'').length);
                const e=els[0];if(!e)return '';(e.closest('a,button')||e).click();return (e.value||e.textContent||'').trim().slice(0,60)}""", pattern)
            if hit:
                return pg
        except Exception:
            continue
    return None


def save_print(P, job, page, case_id, tp):
    """My Documents → select TP → Print → Acknowledgement 'Document Created Successfully!' (TP no. must match)
    → Preview Document Details → 'Your PDF has been generated successfully! Click here to download PDF'
    → fetch that PDF with the signed-in session → save into the case as 'TP copy'."""
    job.step("14 · Print", page)
    open_my_docs_row(P, job, page, tp)
    if not click_first(page, ["#print_my_doc:visible", "input[value='Print']:visible", "button:text-is('Print'):visible"]):
        job.upd(printDiag=page.evaluate(UPLOAD_DIAG_JS))
        return "Print button not found"
    wait_loader(page)
    if not wait_for(P, "()=>/Document Created Successfully|Temporary Document Number/i.test(document.body.innerText)", 40):
        job.step("Print acknowledgement did not appear", page)
        return "Print acknowledgement did not appear"
    if tp not in page.inner_text("body"):
        job.step("Print: TP number does not match", page)
        return f"The acknowledgement does not show {tp} - print not downloaded"
    job.step(f"14 · Acknowledgement shows {tp}", page)
    if not click_first(page, ["button:has-text('Preview Document Details'):visible", "input[value='Preview Document Details']:visible", "a:has-text('Preview Document Details'):visible"]):
        return "Preview Document Details button not found"
    wait_loader(page)
    if not wait_for(P, "()=>[...document.querySelectorAll('a')].some(a=>a.offsetParent&&/^\\s*click here\\s*$/i.test(a.innerText))", 60):
        job.step("PDF link did not appear", page)
        return "'Click here to download PDF' did not appear"
    href = page.evaluate("()=>{const a=[...document.querySelectorAll('a')].find(a=>a.offsetParent&&/^\\s*click here\\s*$/i.test(a.innerText));return a?a.href:''}")
    job.step("14 · PDF generated, downloading", page)
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files", job.id[:8])
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, tp.replace("/", "_") + ".pdf")
    ctx = page.context
    cap = {}

    def on_resp(resp):
        try:
            ct = (resp.headers.get("content-type") or "").lower()
            cd = (resp.headers.get("content-disposition") or "").lower()
            if "pdf" in ct or ".pdf" in cd or "octet-stream" in ct:
                b = resp.body()
                if b[:4] == b"%PDF" and "pdf" not in cap:
                    cap["pdf"] = b
        except Exception:
            pass

    def on_dl(d):
        cap.setdefault("dl", d)

    ctx.on("response", on_resp)
    ctx.on("page", lambda pg: pg.on("download", on_dl))
    page.on("download", on_dl)
    diag = {}
    try:
        r = ctx.request.get(href, timeout=90000, headers={"Referer": page.url})
        body = r.body()
        diag["get"] = f"{r.status} {r.headers.get('content-type', '')} {body[:20]!r}"
        if body[:4] == b"%PDF":
            cap["pdf"] = body
    except Exception as e:
        diag["get"] = str(e)[:120]
    if "pdf" not in cap:
        page.evaluate("()=>{const a=[...document.querySelectorAll('a')].find(a=>a.offsetParent&&/^\\s*click here\\s*$/i.test(a.innerText));if(a)a.click()}")
        for _ in range(60):
            time.sleep(1)
            if cap.get("pdf") or cap.get("dl"):
                break
        if not cap.get("pdf") and not cap.get("dl") and len(ctx.pages) > 1:
            pv = ctx.pages[-1]
            diag["tab"] = pv.url[:60] + " | " + (pv.title() or "")[:40]
            try:
                # the tab may show the PDF inside an <embed>/<iframe>: fetch that source with the session
                src = pv.evaluate("()=>{const e=document.querySelector('embed,iframe,object');return e?(e.src||e.data||''):''}")
                if src and src.startswith("http"):
                    b = ctx.request.get(src, timeout=90000).body()
                    if b[:4] == b"%PDF":
                        cap["pdf"] = b
            except Exception as e:
                diag["tab_err"] = str(e)[:80]
    try:
        if cap.get("pdf"):
            open(out, "wb").write(cap["pdf"])
        elif cap.get("dl"):
            cap["dl"].save_as(out)
        else:
            job.upd(printDiag=diag)
            return "Print PDF did not download"
    except Exception as e:
        return f"Print PDF not saved ({str(e)[:120]})"
    if os.path.getsize(out) < 2000 or open(out, "rb").read(4) != b"%PDF":
        return "Downloaded print is not a PDF"
    for p2 in ctx.pages[1:]:
        try:
            p2.close()
        except Exception:
            pass
    path = f"tp/{time.strftime('%Y-%m')}/{tp.replace('/', '_')}_{job.id[:6]}.pdf"
    url = sb_upload(out, path)
    c = kv_get("cases", case_id) or {}
    docs = c.get("docs") or {}
    docs["tpcopy"] = [{"name": tp.replace("/", "_") + " print.pdf", "url": url, "at": now()}]
    kv_merge("cases", case_id, {"docs": docs, "tpPrint": url})
    return ""


def upload_only(page, job, case):
    """TP already created: log in, upload the 5 documents, save the print. Never creates a TP."""
    t = case.get("tp") or {}
    tp = case.get("tpNo") or ""
    if not tp.startswith("TP/"):
        raise Stop("This case has no TP number yet.")
    P = Portal(page, job)
    job.upd(tpNo=tp)
    comments = []
    to_english(page)
    done = upload_docs(P, job, page, t, case, comments)
    err = save_print(P, job, page, job.d.get("case", ""), tp)
    if err:
        comments.append(err)
    note = f"{tp}: uploaded {', '.join(done) or 'none'}" + ("" if err else "; TP print saved in the case (TP copy)") + (". Only biometric + payment pending. " if not err and len(done) == 5 else ". ")
    if comments:
        note += "CHECK: " + " · ".join(comments)
    job.step("Upload run finished", page)
    job.upd(status="done", message=note.strip(), comments=comments)
    return tp


GEO = {}


def geo_sro(name):
    """(zone id, district id, sro id, sro name) from our SRO list (same ids TNREGINET uses)."""
    if not GEO:
        try:
            GEO.update(HTTP.get("https://raw.githubusercontent.com/vgrlegal/portal/main/sro/geo_en.json", timeout=60).json())
        except Exception:
            return None
    n = re.sub(r"[^a-z]", "", (name or "").lower())
    rows = GEO.get("t", [])
    r = next((x for x in rows if re.sub(r"[^a-z]", "", x[5].lower()) == n), None) or \
        next((x for x in rows if n and (n in re.sub(r"[^a-z]", "", x[5].lower()) or re.sub(r"[^a-z]", "", x[5].lower()) in n)), None)
    return (r[0], r[2], r[4], r[5]) if r else None


def survey_list(t):
    """'123/1A, 124/2B' (old and new) -> [('123','1A'), ('124','2B')]"""
    raw = " , ".join(x for x in (g(t, "Survey number"), g(t, "Old survey no"), g(t, "New survey no")) if x)
    out = []
    for tok in re.split(r"[,;&]|\band\b|\bold\b|\bnew\b|S\.?\s*F\.?\s*No\.?|Survey\s*No\.?|R\.?S\.?\s*No\.?", raw, flags=re.I):
        tok = tok.strip(" .:-()")
        m = re.match(r"^(\d+[A-Za-z]?)(?:\s*/\s*([0-9A-Za-z]+(?:\s*/\s*[0-9A-Za-z]+)*))?", tok)
        if m:
            pair = (m.group(1), (m.group(2) or "").replace(" ", ""))
            if pair not in out:
                out.append(pair)
    return out


def ask_captcha(page, job, img_sel, label):
    """Show a captcha image in the portal and wait for the person to type it."""
    img = page.locator(img_sel).first
    img.scroll_into_view_if_needed()
    job.upd(status="captcha", captchaImg="data:image/png;base64," + base64.b64encode(img.screenshot()).decode(),
            captcha="", step=label, at=now())
    t0 = time.time()
    while True:
        d = job.fresh()
        if d.get("status") == "cancel":
            raise Stop("Stopped from the portal")
        if d.get("status") == "captcha_sent" and d.get("captcha"):
            job.upd(status="filling", captchaImg="")
            return d["captcha"].strip()
        if time.time() - t0 > CAPTCHA_WAIT:
            raise Stop("Nobody typed the captcha for 10 minutes")
        time.sleep(1.5)


def run_ec(page, job, case):
    """Free EC from TNREGINET's home page (no login): View EC → Survey wise → zone / district / SRO from our list →
    period 1 January of the MODT year to yesterday → each survey number (village, S.No, subdivision) Add →
    captcha typed by the person → Search → 'Click here' PDF → saved in the case."""
    t = case.get("tp") or {}
    P = Portal(page, job)
    y = re.sub(r"\D", "", g(t, "Year"))[:4] or time.strftime("%Y")
    yd = time.localtime(time.time() - 86400)
    frm, to = f"01-01-{y}", time.strftime("%d-%m-%Y", yd)
    sro_name = g(t, "Property SRO") or g(t, "SRO")
    geo = geo_sro(sro_name)
    if not geo:
        raise Stop(f"SRO '{sro_name}' is not in our SRO list. Pick the Property SRO in Drafting.")
    surveys = survey_list(t)
    if not surveys:
        raise Stop("No survey number in Drafting (Property · Survey number).")
    village = g(t, "Village")
    job.upd(ecFrom=frm, ecTo=to)
    job.step(f"EC · {geo[3]} · {village} · {', '.join(a + ('/' + b if b else '') for a, b in surveys)} · {frm} to {to}")
    page.goto("https://tnreginet.gov.in/portal/", wait_until="domcontentloaded", timeout=90000)
    to_english(page)
    page.wait_for_timeout(2000)
    page.evaluate("""()=>document.querySelectorAll('.modal').forEach(m=>{const x=m.querySelector('.close,[data-dismiss]');if(x)x.click()})""")
    page.evaluate("""()=>{const a=[...document.querySelectorAll('a')].find(a=>/View.*Encumbrance|வில்லங்கச் சான்று விவரம் பார்வை/i.test(a.textContent));if(a)a.click()}""")
    if not wait_for(P, "()=>{const e=document.getElementById('cmb_Zone');return !!(e&&e.offsetParent)}", 40):
        job.step("EC page did not open", page)
        raise Stop("TNREGINET's View EC page did not open. Try again in a minute.")
    P.js("(()=>{const r=document.getElementById('YEAR_WISE');if(r&&!r.checked)r.click();const g=document.getElementById('REGISTRATION_VILLAGE');if(g&&!g.checked)g.click()})()")
    P.wait(1.5)

    def pick(sel_id, val_prefix, wait_next=None):
        ok = page.evaluate("""([id,v])=>{const s=document.getElementById(id);const o=[...s.options].find(o=>o.value===v||o.value.split(':')[0]===v);
            if(!o)return false;s.value=o.value;try{s.dispatchEvent(new Event('change',{bubbles:true}))}catch(e){}return true}""", [sel_id, str(val_prefix)])
        if wait_next:
            wait_for(P, f"()=>document.getElementById('{wait_next}').options.length>1", 25)
        return ok
    if not (pick("cmb_Zone", geo[0], "cmb_District") and pick("cmb_District", geo[1], "cmb_SroName") and pick("cmb_SroName", geo[2], "cmb_Village")):
        job.step("EC · zone / district / SRO not accepted", page)
        raise Stop(f"TNREGINET did not accept SRO {geo[3]} on the EC page.")
    page.evaluate("""([a,b])=>{const p=s=>{const [d,m,y]=s.split('-').map(Number);return new Date(y,m-1,d)};
        jQuery('#txt_PeriodStartDt').datepick('setDate',p(a));jQuery('#txt_PeriodEndDt').datepick('setDate',p(b))}""", [frm, to])
    vopt = page.evaluate("""(v)=>{const s=document.getElementById('cmb_Village');const nz=x=>String(x).toLowerCase().replace(/[^a-z]/g,'');const w=nz(v);
        let o=[...s.options].find(o=>nz(o.text)===w)||[...s.options].find(o=>w&&nz(o.text)&&(nz(o.text).startsWith(w)||w.startsWith(nz(o.text))&&nz(o.text).length>3))||[...s.options].find(o=>w&&nz(o.text).includes(w));
        return o&&o.value!=='-1'&&o.value!==''?o.value+'|'+o.text:''}""", village)
    if not vopt:
        job.step("EC · village not in TNREGINET list", page)
        raise Stop(f"Village '{village}' is not in TNREGINET's list for {geo[3]}. Correct the Village in Drafting.")
    vval, vtext = vopt.split("|", 1)
    added = []
    for sno, sub in surveys:
        page.evaluate("""([v,a,b])=>{const s=document.getElementById('cmb_Village');s.value=v;try{s.dispatchEvent(new Event('change',{bubbles:true}))}catch(e){}
            const f=(id,x)=>{const e=document.getElementById(id);e.value=x;['input','change'].forEach(t=>{try{e.dispatchEvent(new Event(t,{bubbles:true}))}catch(_){}})};
            f('txt_SurveyNo',a);f('txt_SubDivisionNo',b);try{document.getElementById('btn_AddSurvey').click()}catch(e){}}""", [vval, sno, sub])
        P.wait(1.5); m = P.popup_ok()
        if page.evaluate("(a)=>{const t=document.getElementById('multiAddSurvey');return !!(t&&t.innerText.includes(a))}", sno):
            added.append(sno + ("/" + sub if sub else ""))
        else:
            P.warn.append(f"Survey {sno}/{sub} not added ({m[:80]})")
    if not added:
        job.step("EC · no survey number added", page)
        raise Stop("TNREGINET did not take any survey number. Check the survey numbers in Drafting.")
    job.step(f"EC · added {', '.join(added)} ({vtext})", page)
    # captcha typed by the person, then Search (one retry with a fresh captcha if it was wrong)
    for attempt in range(3):
        code = ask_captcha(page, job, "#captcha", "Type the EC captcha")
        page.fill("#txt_Captcha", code)
        DLG.clear()
        page.evaluate("()=>document.getElementById('btn_SearchDoc').click()")
        wait_loader(page); P.wait(4)
        said = " ".join(DLG) + " " + P.popup_ok()
        if re.search(r"captcha", said, re.I) and attempt < 2:
            page.evaluate("()=>{const r=document.getElementById('refresh');if(r)r.click();else{const i=document.getElementById('captcha');i.src=i.src.split('?')[0]+'?'+Date.now()}}")
            P.wait(2)
            continue
        break
    job.step("EC · searched", page)
    # result: either a PDF link straight away, or a result list with a View/Print/Download button first
    if not page.evaluate("()=>[...document.querySelectorAll('a')].some(a=>a.offsetParent&&/^\\s*click here\\s*$/i.test(a.innerText))"):
        page.evaluate("""()=>{const b=[...document.querySelectorAll('button,input[type=button],a.btn,a')].find(x=>x.offsetParent&&/^(view( ec)?|print|download|generate( pdf)?|view & download)$/i.test((x.value||x.innerText||'').trim()));if(b)b.click()}""")
        wait_loader(page); P.wait(4); P.popup_ok()
    if not wait_for(P, "()=>[...document.querySelectorAll('a')].some(a=>a.offsetParent&&/^\\s*click here\\s*$/i.test(a.innerText))", 60):
        txt = page.evaluate("()=>document.body.innerText.replace(/\\s+/g,' ')")
        nil = re.search(r"no (records|documents|transactions)|nil", txt, re.I)
        job.upd(ecFields=page.evaluate(FIELDS_JS))
        job.step("EC · result (no PDF link)", page)
        job.upd(status="review", message=("EC search done: TNREGINET shows no transactions for this period. " if nil else
                                          "EC searched but the PDF link did not appear; the result screen is recorded for Claude. ")
                + f"Survey {', '.join(added)}, {vtext}, {frm} to {to}." + (" Warnings: " + "; ".join(P.warn) if P.warn else ""))
        return
    href = page.evaluate("()=>{const a=[...document.querySelectorAll('a')].find(a=>a.offsetParent&&/^\\s*click here\\s*$/i.test(a.innerText));return a?a.href:''}")
    body = page.context.request.get(href, timeout=90000, headers={"Referer": page.url}).body()
    if body[:4] != b"%PDF":
        got = {}
        page.context.on("page", lambda pg: pg.on("download", lambda d: got.setdefault("dl", d)))
        page.on("download", lambda d: got.setdefault("dl", d))
        page.evaluate("()=>{const a=[...document.querySelectorAll('a')].find(a=>a.offsetParent&&/^\\s*click here\\s*$/i.test(a.innerText));if(a)a.click()}")
        for _ in range(40):
            time.sleep(1)
            if got.get("dl"):
                break
        if not got.get("dl"):
            job.step("EC PDF did not download", page)
            raise Stop("The EC PDF did not download.")
        folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files", job.id[:8])
        os.makedirs(folder, exist_ok=True)
        tmp = os.path.join(folder, "ec.pdf")
        got["dl"].save_as(tmp)
        body = open(tmp, "rb").read()
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files", job.id[:8])
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, f"EC_{geo[3]}_{added[0].replace('/', '-')}.pdf")
    open(out, "wb").write(body)
    path = f"ec/{time.strftime('%Y-%m')}/{job.id[:8]}_{os.path.basename(out).replace(' ', '_')}"
    url = sb_upload(out, path)
    c = kv_get("cases", job.d.get("case", "")) or {}
    docs = c.get("docs") or {}
    docs["ec"] = (docs.get("ec") or []) + [{"name": f"EC {geo[3]} {', '.join(added)} ({frm} to {to}).pdf", "url": url, "at": now()}]
    kv_merge("cases", job.d.get("case", ""), {"docs": docs})
    job.step("EC saved in the case", page)
    job.upd(status="done", message=f"EC saved in the case: {geo[3]}, {vtext}, survey {', '.join(added)}, {frm} to {to}."
            + (" Warnings: " + "; ".join(P.warn) if P.warn else ""))


def finish_dor(P, job, page, t, case, comments):
    tp = loan_to_create(P, job, page, t, case, comments)
    done = upload_docs(P, job, page, t, case, comments)
    err = save_print(P, job, page, job.d.get("case", ""), tp)
    if err:
        comments.append(err)
    note = f"TP created: {tp}. Uploaded: {', '.join(done) or 'none'}" + ("" if err else ". Print saved in the case") + ". Do biometric + payment yourself. "
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
    acc = login_of(data.get("login", "")) if data.get("kind") != "ec" else {"username": ""}
    if not acc:
        raise Stop("Login ID not found. Add it in Clients & Staff → TNREGINET login IDs.")
    browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="en-IN", timezone_id="Asia/Kolkata",
                              user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    page = ctx.new_page()
    page.on("dialog", lambda d: (DLG.append(d.message[:150]), d.accept()))
    try:
        if data.get("kind") == "ec":
            try:
                run_ec(page, job, case)
            except Exception:
                job.step("Stopped here", page)
                raise
            return
        try:
            do_login(page, job, acc)
        except Exception:
            job.step("Problem at login", page)
            raise
        if data.get("kind") == "dor_upload":
            upload_only(page, job, case)
            return
        if data.get("kind", "dor") != "dor":
            raise Stop("Only Deed of Receipt is automated so far")
        if str(case.get("tpNo") or "").startswith("TP/"):
            raise Stop(f"This case already has {case['tpNo']}. Use 'Upload documents only' - a new TP is not created.")
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
