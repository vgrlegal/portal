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
import base64, datetime, json, os, platform, re, sys, time, traceback
import requests
import uuid
from playwright.sync_api import sync_playwright

_here = os.path.dirname(os.path.abspath(__file__))
ENV = os.environ.get("VGR_ENV") or (os.path.join(_here, "worker.env") if os.path.exists(os.path.join(_here, "worker.env")) else "/opt/vgr/worker.env")
for line in open(ENV, encoding="utf-8-sig") if os.path.exists(ENV) else []:
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


def kv_status(col, id_):
    """Only the status of one row (a few bytes) - used for the frequent 'was it stopped?' check."""
    r = HTTP.get(f"{SB}/rest/v1/kv", headers=H, params={"collection": f"eq.{col}", "id": f"eq.{id_}", "select": "st:data->>status"}, timeout=20)
    r.raise_for_status()
    rows = r.json()
    return (rows[0] or {}).get("st") if rows else None


def job_poll(id_):
    """Status + typed captcha only (small), for the captcha wait loops."""
    r = HTTP.get(f"{SB}/rest/v1/kv", headers=H, params={"collection": "eq.tp_jobs", "id": f"eq.{id_}", "select": "status:data->>status,captcha:data->>captcha"}, timeout=20)
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else {}


def kv_list(col, **where):
    # where: data field -> value, e.g. status="queued" (filters on the server so only matching rows are downloaded)
    params = {"collection": f"eq.{col}", "select": "id,data"}
    for k, v in where.items():
        params[f"data->>{k}"] = f"eq.{v}"
    r = HTTP.get(f"{SB}/rest/v1/kv", headers=H, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


HOST = platform.node() or "tp-computer"


def claim_job(jid):
    """Take a queued job for THIS computer only (two TP computers never start the same job)."""
    try:
        r = HTTP.post(f"{SB}/rest/v1/rpc/claim_job", headers=H, json={"p_id": jid, "p_host": HOST}, timeout=20)
        if r.status_code == 404:
            return True  # older database without claim_job: one computer only
        r.raise_for_status()
        return bool(r.json())
    except Exception as e:
        log("claim failed", e)
        return False


def kv_insert_new(col, id_, data):
    """Create a row only if it does not exist yet (used for the daily biometric check, so two computers make it once)."""
    h = dict(H, Prefer="resolution=ignore-duplicates")
    r = HTTP.post(f"{SB}/rest/v1/kv", headers=h, json={"collection": col, "id": id_, "data": data, "updated_at": now()}, timeout=20)
    r.raise_for_status()


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
BUSY = {}  # job id -> what this computer is doing now (sent with every heartbeat, so the portal shows "Busy · case")


class Job:
    def __init__(self, jid, data):
        self.id, self.d = jid, data
        self.last = ""
        self.times = [("Start", time.time())]
        BUSY[jid] = {"job": jid, "case": data.get("caseId", ""), "kind": data.get("kind", ""), "login": data.get("loginUser", ""),
                     "status": "starting", "since": now(), "t": time.time()}

    def timing(self):
        """Seconds spent on each main step (the number before ' · '), for the case."""
        out, prev = [], None
        for text, t0 in self.times:
            key = text.split(" · ")[0] + (" · " + text.split(" · ")[1].split(":")[0][:30] if " · " in text else "")
            if prev and prev[0] == key:
                continue
            if prev:
                out.append({"step": prev[0], "sec": round(t0 - prev[1])})
            prev = (key, t0)
        if prev:
            out.append({"step": prev[0], "sec": round(time.time() - prev[1])})
        return {"total": round(time.time() - self.times[0][1]), "steps": out[-40:]}

    def upd(self, **kv):
        self.d.update(kv)
        if "status" in kv:
            if kv["status"] in ("done", "error", "cancelled"):
                BUSY.pop(self.id, None)
            elif self.id in BUSY:
                BUSY[self.id]["status"] = kv["status"]
        if "step" in kv and self.id in BUSY:
            BUSY[self.id]["step"] = str(kv["step"])[:80]
        for i in range(4):
            try:
                kv_merge("tp_jobs", self.id, kv)
                return
            except Exception as e:
                log(self.id[:8], "update failed:", e)
                time.sleep(3 * (i + 1))

    def step(self, text, page=None):
        log(self.id[:8], text)
        if text not in ("Stopped here", "Problem at login"):
            self.last = text
        self.times.append((text, time.time()))
        patch = {"step": text, "at": now()}
        if page is not None:
            try:
                # the screenshot lives in its own row, so polling the job row stays small
                kv_set("tp_shots", self.id, {"shot": "data:image/jpeg;base64," + base64.b64encode(page.screenshot(type="jpeg", quality=55, full_page=False)).decode(), "at": now()})
                patch["shotAt"] = now()
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
        # checked at every wait: fetch only the status, and at most every 4 seconds (the full row carried the screenshot)
        if time.time() - getattr(self, "_cc", 0) < 4:
            return False
        self._cc = time.time()
        try:
            return kv_status("tp_jobs", self.id) in ("cancel", "cancelled")
        except Exception as e:
            log(self.id[:8], "status poll failed:", e)
            return False


import threading


class _ThreadList:
    """Browser alert messages (e.g. "Invalid captcha"), kept per robot thread (several logins run side by side)."""
    _t = threading.local()

    def _l(self):
        if not hasattr(self._t, "l"):
            self._t.l = []
        return self._t.l

    def append(self, x): self._l().append(x)
    def clear(self): self._l().clear()
    def __iter__(self): return iter(list(self._l()))
    def __len__(self): return len(self._l())
    def __bool__(self): return bool(self._l())


DLG = _ThreadList()


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
window.lev = (a,b) => {const m=a.length,n=b.length;if(!m||!n)return m||n;let p=[...Array(n+1).keys()];for(let i=1;i<=m;i++){const q=[i];for(let j=1;j<=n;j++)q[j]=Math.min(p[j]+1,q[j-1]+1,p[j-1]+(a[i-1]===b[j-1]?0:1));p=q}return p[n]};
window.selfuzzy = (id,txt) => {const s=vis(id); if(!s) return 'NOFIELD '+id; const t=String(txt).toLowerCase().replace(/[^a-z]/g,'');
  let best=null,score=0; for(const o of s.options){const n=o.text.toLowerCase().replace(/[^a-z]/g,''); if(!n) continue;
    const sc=n===t?1:1-lev(n,t)/Math.max(n.length,t.length); if(sc>score){score=sc;best=o}}
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
        # the list (e.g. districts after the state) may still be loading: try again a few times
        for _ in range(4):
            r = self.js(f"selfuzzy({fid!r},{str(text)!r})")
            if str(r).startswith("ok") or str(r).startswith("NOFIELD"):
                break
            time.sleep(1.5)
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
            try:
                d = job_poll(job.id)
            except Exception as e:
                log(job.id[:8], "poll failed:", e)
                d = {}
            if d.get("status") in ("cancel", "cancelled"):
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
    s = s.lower().strip()
    return ("^Wife of" if s.startswith("w") else "^Daughter of" if s.startswith("d") else "^Husband of" if s.startswith("h")
            else "^Father of" if s.startswith("f") else "^Son of")


FORM_ERR_JS = """()=>[...document.querySelectorAll('span,div,label,small,p')].filter(e=>{if(!e.offsetParent||e.children.length||!/^\\s*(please (select|enter|choose)|invalid|is required)/i.test(e.textContent||''))return false;const c=getComputedStyle(e).color.match(/\\d+/g)||[0,0,0];return /error|invalid|danger|validat/i.test(e.className+' '+(e.parentElement||{}).className)||(+c[0]>150&&+c[1]<110&&+c[2]<110)}).map(e=>e.textContent.trim()).filter((x,i,a)=>a.indexOf(x)===i).slice(0,6)"""


def form_errors(P):
    """Red messages TNREGINET shows under fields it will not accept (e.g. 'Please select District')."""
    try:
        return P.p.evaluate(FORM_ERR_JS) or []
    except Exception:
        return []


def fill_missing_district(P, *names):
    """A party saved from the linked MODT can come without a district; TNREGINET then refuses Update. Pick it from our data."""
    empty = "()=>{const e=document.getElementById('cmb_District');return !!(e&&e.offsetParent&&!e.value)}"
    if not P.p.evaluate(empty):
        return
    for n in names:
        if n:
            P.fuzzy("cmb_District", n, 2.5)
            if not P.p.evaluate(empty):
                return


def person(P, t, pre, designation=True):
    sal = g(t, f"{pre} salutation") or "Mr"
    P.sel("salutationId", sal_re(sal))
    P.set("fullName", g(t, f"{pre} name", pre))
    P.tamil("fullNameLL", g(t, f"{pre} name (Tamil)"), g(t, f"{pre} name", pre))
    rel = g(t, f"{pre} relation") or "Son of"
    if rel.lower().startswith("c"):  # TNREGINET has no "Care of": Aadhaar C/O -> Son of for Mr, Daughter of for a woman
        fem0 = (g(t, f"{pre} gender") or "").lower().startswith("f") or g(t, f"{pre} salutation").lower().startswith(("mrs", "ms", "miss"))
        rel = "Daughter of" if fem0 else "Son of"
    r = P.sel("firstRelationTypeId", rel_re(rel))
    if not str(r).startswith("ok"):
        # TNREGINET has no "Care of" (Aadhaar C/O is usually the father): use Son of / Daughter of by gender
        fem = (g(t, f"{pre} gender") or "").lower().startswith("f") or g(t, f"{pre} salutation").lower().startswith(("mrs", "ms", "miss"))
        P.sel("firstRelationTypeId", "^Daughter of" if fem else "^Son of")
        P.warn.append(f"{pre}: relation '{rel}' is not on TNREGINET - used {'Daughter of' if fem else 'Son of'}, please check")
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
    # TNREGINET refuses ':' in addresses (e.g. "Branch:", "PO:"); "PO: Moongilpadi" is written "Moongilpadi (PO)"
    po = lambda a: ", ".join(re.sub(r"^P\.?\s*O\b\.?\s*[:：\-–]?\s*(\S.*)$", r"\1 (PO)", x.strip(), flags=re.I) for x in (a or "").split(","))
    addr = re.sub(r"\s+", " ", po(addr).replace(":", " ").replace("：", " ")).strip()
    addr_ta = re.sub(r"\s+", " ", po(addr_ta).replace(":", " ").replace("：", " ")).strip()
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
    errs = form_errors(P)
    raise Stop(f"The {name} tab did not open on TNREGINET" + (f": the page before it is not complete ({'; '.join(errs)})." if errs else ". Send the screen to Claude."))


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
        if d.get("status") in ("cancel", "cancelled"):
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



def rel_fix(r, gender):
    r = str(r or "").strip()
    m = re.sub(r"[^a-z]", "", r.lower())
    if not m:
        return ""
    if m.startswith("c"):
        return "Daughter of" if str(gender or "").lower().startswith("f") else "Son of"
    return {"w": "Wife of", "d": "Daughter of", "h": "Husband of", "f": "Father of"}.get(m[0], "Son of")


def apply_profile(case, t):
    """The saved client profile and witness list are the master copy: their rep / witness details replace the case's
    (a correction made once in Settings reaches every case, including ones started earlier)."""
    try:
        lists = kv_get("config", "lists") or {}
    except Exception as e:
        log("profile read failed", e)
        return t
    t = dict(t)
    dg = lambda x: re.sub(r"\D", "", str(x or ""))
    def put(k, v):
        if v not in (None, "") and str(v).strip():
            t[k] = str(v).strip()
    reps = (((lists.get("profiles") or {}).get(case.get("bank") or "") or {}).get("reps") or [])
    a = dg(t.get("Rep Aadhaar"))
    r = next((x for x in reps if a and dg(x.get("id_no")) == a), None) or \
        next((x for x in reps if x.get("name") and x.get("name") == t.get("Rep name")), None) or \
        next((x for x in reps if x.get("name") and x.get("name") == case.get("rep")), None) or (reps[0] if reps else None)
    if r:
        for k, v in (("Rep salutation", r.get("sal")), ("Rep name", r.get("name")), ("Rep name (Tamil)", r.get("name_ta")),
                     ("Rep relation", rel_fix(r.get("rel_type"), r.get("gender"))), ("Rep relative name", r.get("rel_name")),
                     ("Rep relative name (Tamil)", r.get("rel_ta")), ("Rep age", age_from(r.get("dob"))), ("Rep gender", r.get("gender")),
                     ("Rep Aadhaar", dg(r.get("id_no"))), ("Rep designation", r.get("designation")), ("Rep state", r.get("state")),
                     ("Rep district", r.get("district")), ("Rep address", r.get("addr")), ("Rep address (Tamil)", r.get("addr_ta")),
                     ("Rep pincode", r.get("pincode")), ("Rep mobile", r.get("mobile"))):
            put(k, v)
    wits = lists.get("wit") or []
    for n in (1, 2):
        a = dg(t.get(f"Witness {n} Aadhaar"))
        w = next((x for x in wits if a and dg(x.get("aadhaar")) == a), None) or \
            next((x for x in wits if x.get("name") and x.get("name") == case.get(f"w{n}")), None)
        if not w:
            continue
        for k, v in (("salutation", w.get("sal")), ("name", w.get("name")), ("name (Tamil)", w.get("name_ta")),
                     ("relative name (Tamil)", w.get("rel_ta")), ("relation", rel_fix(w.get("rel_type"), w.get("gender"))),
                     ("relative name", w.get("rel_name")), ("age", age_from(w.get("dob"))), ("gender", w.get("gender")),
                     ("Aadhaar", dg(w.get("aadhaar"))), ("district", w.get("district")), ("address", w.get("addr")),
                     ("pincode", w.get("pincode"))):
            put(f"Witness {n} {k}", v)
    return t


def part_survey(t):
    """'22(Part)' / '22 Pt' with no other (sub-divided) survey number: TNREGINET will not make the TP. Revenue record needed."""
    sv = [g(t, k) for k in ("Survey number", "Survey No", "New survey no") if g(t, k)]
    part = [x for x in sv if re.search(r"\bpart\b|\bpt\b|\(p\)", x, re.I)]
    other = [x for x in sv if re.search(r"\d", x) and not re.search(r"\bpart\b|\bpt\b|\(p\)", x, re.I)]
    return part[0] if part and not other else ""


def run_dor(page, job, case):
    t = apply_profile(case, case.get("tp") or {})
    ps = part_survey(t)
    if ps and not case.get("partOk"):
        raise Stop(f"Survey number is '{ps}' (Part). TNREGINET will not make a TP with a Part survey number. "
                   "Revenue document (patta / TSLR with the sub-divided survey number) is needed from the bank; "
                   "enter the new survey number in Drafting and start again.")
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
    grab_draft(P, job, P.click("createDocProceed", 6))
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
    fill_missing_district(P, g(t, "Company district"))
    b = click_btn_text(P, r"^update$")
    P.wait(3); msg = P.popup_ok()
    errs = [] if re.search(r"success|updated|added", msg or "", re.I) else form_errors(P)
    if errs:
        job.step("Mortgagee not accepted", page)
        raise Stop(f"Mortgagee (company): TNREGINET wants: {'; '.join(errs)}. Fill it in Drafting (Company state / district) and start again.")
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
                fill_missing_district(P, g(t, "Mortgagor district"), g(t, "Property district"))
                P.js("tick('aadhaarConsentCheckBox')")
                b = click_btn_text(P, r"^update$")
                P.wait(3); msg = P.popup_ok()
                errs = [] if re.search(r"success|updated|also going to execute", msg or "", re.I) else form_errors(P)
                if errs:
                    job.step(f"Mortgagor {i + 1} not accepted", page)
                    raise Stop(f"Mortgagor {i + 1}: TNREGINET wants: {'; '.join(errs)}. Fill it in Drafting (e.g. Mortgagor district) and start again.")
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
        errs = form_errors(P)
        raise Stop("Representative was not added. " + (f"TNREGINET wants: {'; '.join(errs)}." if errs else f"TNREGINET said '{msg.strip()[:200]}'"))
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
        errs = [] if re.search(r"success|added", msg or "", re.I) else form_errors(P)
        if errs:
            job.step(f"Witness {n} not accepted", page)
            raise Stop(f"Witness {n}: TNREGINET wants: {'; '.join(errs)}. Check the witness in Clients & Staff and start again.")
        if msg and not re.search(r"success|added", msg, re.I):
            raise Stop(f"Witness {n}: TNREGINET said '{msg[:160]}'")
    open_subtab(P, "Presenter Details", "cmb_presenter")
    job.step("7 · Presenter", page)
    wait_for(P, "()=>document.getElementById('cmb_presenter').options.length>1", 15)
    P.fuzzy("cmb_presenter", g(t, "Rep name"), 1)
    grab_draft(P, job, P.click("btnMainSaveAsDraft", 4))


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
    for key in ("Survey number", "Survey No", "Old survey no", "New survey no", "Extent"):
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


CLOSE_POP_JS = r"""()=>{const vis=e=>e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
  const inPop=b=>{let e=b;while(e&&e!==document.body){const st=getComputedStyle(e);if(e.matches('.modal,.swal2-popup,[role=dialog],.popup,.jconfirm-box,.bootbox,.ui-dialog')||st.position==='fixed'||+st.zIndex>100)return true;e=e.parentElement}return false};
  const b=[...document.querySelectorAll('button,input[type=button],a.btn')].filter(x=>vis(x)&&/^\s*(Close|CLOSE|close|மூடு)\s*$/.test(x.value||x.innerText||'')).find(inPop);
  if(!b)return '';const box=b.closest('.modal-content,.modal,.swal2-popup,[role=dialog],.popup,.jconfirm-box,.bootbox,.ui-dialog')||(()=>{let e=b;while(e&&e!==document.body){const st=getComputedStyle(e);if(st.position==='fixed'||+st.zIndex>100)return e;e=e.parentElement}return b.parentElement})();
  const t=(box.innerText||'').replace(/\s+/g,' ').replace(/\bClose\b\s*$/,'').trim().slice(0,300);b.click();return t}"""


def popup_close(page):
    """Read a TNREGINET message box that has only a Close button, close it, return its text."""
    try:
        return page.evaluate(CLOSE_POP_JS) or ""
    except Exception:
        return ""


def press_create(P, job, page, attempt):
    """Press Create Document once; return (tp, what TNREGINET said)."""
    btn = page.locator("#btnCreateDocument:visible, input[value='Create Document']:visible, button:has-text('Create Document'):visible")
    wait_for(P, "()=>[...document.querySelectorAll('#btnCreateDocument,input[value=\"Create Document\"],button')].some(b=>b.offsetParent&&/Create Document/i.test(b.value||b.innerText||''))", 25)
    if not btn.count():
        job.step("Create Document button not found", page)
        raise Stop("The Create Document button did not appear. The draft is saved - send the screen to Claude.")
    job.upd(createClicked=now(), createCount=attempt)
    try:
        kv_merge("cases", job.d.get("case", ""), {"createPressedAt": now()})
    except Exception:
        pass
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
        cl = popup_close(page)
        if cl:
            said += " " + cl
            m = re.search(r"TP/\d+/\d{4}", cl)
            if m:
                tp = m.group(0)
                break
        c2 = page.locator("button:has-text('Continue'):visible, input[value='Continue']:visible")
        if c2.count() and not job.d.get(f"continueClicked{attempt}"):
            job.upd(**{f"continueClicked{attempt}": now()})
            c2.first.click()
        if re.search(r"survey", said, re.I):
            break
    if not tp:
        said += " " + page.evaluate("()=>[...document.querySelectorAll('.error,.text-danger,.alert,.modal,.swal2-popup,[role=alert],[role=dialog]')].filter(e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden').map(e=>e.innerText).join(' ').slice(0,400)")
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


def upload_docs(P, job, page, t, case, comments, plan=None):
    """Upload Documents screen: Upload Document type (docFor) → Document Name → Choose File → Add, one by one.
    Rows already there are kept if right, removed if wrong/duplicate; Save only when exactly the 5 right rows are present."""
    tp = job.d.get("tpNo")
    job.step("13 · Upload documents", page)
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files", job.id[:8])
    os.makedirs(folder, exist_ok=True)
    # rep / witness KYC: from the case, else from the saved client profile and witness list (mail cases never copy them)
    lists = kv_get("config", "lists") or {}
    def saved(files, key):
        f = (files or [None])[0]
        if not f or not f.get("url"):
            return None
        ext = (re.search(r"\.(pdf|jpe?g|png)$", f.get("name") or "", re.I) or [".pdf"])[0].lower()
        try:
            return download(f["url"], os.path.join(folder, UPNAME.get(key, key.upper()) + ext))
        except Exception as e:
            P.warn.append(f"{key} download failed: {e}")
            return None
    prof = ((lists.get("profiles") or {}).get(case.get("bank") or "") or {})
    rep = next((r for r in (prof.get("reps") or []) if nz(r.get("name")) == nz(g(t, "Rep name"))), None) or ((prof.get("reps") or [None])[0])
    wit = {n: next((w for w in (lists.get("wit") or []) if nz(w.get("name")) == nz(g(t, f"Witness {n} name"))), None) for n in (1, 2)}
    rep_k = get_file(case, "rep_kyc", folder) or (rep and saved(rep.get("kyc"), "rep_kyc"))
    rep_i = get_file(case, "rep_id", folder) or (rep and saved(rep.get("idcard"), "rep_id"))
    w1_f = get_file(case, "w1_kyc", folder) or (wit[1] and saved(wit[1].get("kyc"), "w1_kyc"))
    w2_f = get_file(case, "w2_kyc", folder) or (wit[2] and saved(wit[2].get("kyc"), "w2_kyc"))
    rep_pdf = None
    if rep_k or rep_i:
        try:
            rep_pdf = to_pdf([rep_k, rep_i], os.path.join(folder, "REP_KYC_ID.pdf"))
        except Exception as e:
            P.warn.append(f"Rep KYC+ID join failed ({e}); uploading the KYC only")
            rep_pdf = rep_k or rep_i
    if plan is None:
        plan = [  # (type option, document name (as TNREGINET lists it), file, label, file tag)
            ("Identification", g(t, "Rep name"), rep_pdf, "Bank rep KYC + ID", "REP_KYC"),
            ("Identification", g(t, "Witness 1 name"), w1_f, "Witness 1 KYC", "WITNESS1"),
            ("Identification", g(t, "Witness 2 name"), w2_f, "Witness 2 KYC", "WITNESS2"),
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
        old_style = {"REP_KYC": "REP_KYC", "WITNESS1": "W1_KYC", "WITNESS2": "W2_KYC", "MODT": "MODT", "NOC": "NOC"}.get(tag, tag)
        return tag in f or old_style in f

    def who_ok(who, typ, name):
        if who.startswith("re:"):
            return bool(re.search(who[3:], name or "", re.I))
        return bool(nz(who)) and (nz(who) == nz(name) or (typ == "Identification" and nz(who)[:6] in nz(name)))

    # 1. clean up rows from earlier tries: keep one right row per document, remove wrong / duplicate rows
    for _ in range(12):
        rows = page.evaluate(UP_ROWS_JS)
        bad = None
        seen = set()
        for r in rows:
            item = next((x for x in plan if x[1] and who_ok(x[1], x[0], r["name"]) and right_file(r, x[4], x[1])), None) or \
                next((x for x in plan if x[1] and who_ok(x[1], x[0], r["name"])), None)
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
        if any(who_ok(who, typ, r["name"]) and right_file(r, tag, who) for r in rows):
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
            if(!s)return 'NOSEL';if(who.startsWith('re:')){const rx=new RegExp(who.slice(3),'i');const o2=[...s.options].find(o=>o.value&&rx.test(o.text.trim()));if(!o2)return 'NOOPT: '+[...s.options].map(o=>o.text.trim()).join('|');
              if(window.jQuery)jQuery(s).val(o2.value).trigger('change').trigger('chosen:updated');else{s.value=o2.value;s.dispatchEvent(new Event('change',{bubbles:true}))}return 'ok '+o2.text}
            const w=nz(who);let o=[...s.options].find(o=>nz(o.text)===w)||[...s.options].find(o=>w&&nz(o.text)&&(nz(o.text).includes(w)||w.includes(nz(o.text))&&nz(o.text).length>3));
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
    job.upPlan = len(plan)
    if len(done) == len(plan) and len(rows) == len(plan):
        page.evaluate("()=>{const b=document.getElementById('submitUploadApp');if(b)b.click()}")
        wait_loader(page); P.wait(3); m = P.popup_ok()
        job.upSaved = not re.search(r"error|fail|invalid|not saved|please", m or "", re.I)
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
    t = apply_profile(case, case.get("tp") or {})
    tp = case.get("tpNo") or ""
    if not tp.startswith("TP/"):
        raise Stop("This case has no TP number yet.")
    P = Portal(page, job)
    job.upd(tpNo=tp)
    comments = []
    to_english(page)
    modt = not re.search(r"receipt", case.get("type") or "", re.I)
    plan = None
    if modt:
        folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files", job.id[:8])
        os.makedirs(folder, exist_ok=True)
        plan = modt_plan(case, case.get("draft") or {}, folder, P, comments)
    done = upload_docs(P, job, page, t, case, comments, plan=plan)
    err = save_print(P, job, page, job.d.get("case", ""), tp)
    if err:
        comments.append(err)
    if modt:
        save_fees(job, job.d.get("case", ""), tp, (case.get("fees") or {}).get("items") or {}, comments)
    miss = tp_ready(job, job.d.get("case", ""), tp, done, err)
    if miss:
        comments.append("Stays at TP Creation, missing: " + ", ".join(miss))
    note = f"{tp}: uploaded {', '.join(done) or 'none'}" + ("" if err else "; TP print saved in the case (TP copy)") + (". Only biometric + payment pending. " if not err and len(done) == (len(plan) if plan else 5) else ". ")
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
        try:
            d = job_poll(job.id)
        except Exception as e:
            log(job.id[:8], "poll failed:", e)
            d = {}
        if d.get("status") in ("cancel", "cancelled"):
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


# ---------------- MODT (Deposit of Title Deeds) ----------------
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def dob_tn(d):
    """'04-01-1963' / '04/01/1963' / '1963-01-04' / '4 Jan 1963' -> '04-JAN-1963' (TNREGINET date format DD-MON-YYYY)."""
    d = str(d or "")
    iso = re.match(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})", d)
    if iso:
        d = f"{iso.group(3)}-{iso.group(2)}-{iso.group(1)}"
    m = re.match(r"^\s*(\d{1,2})[-/. ](\d{1,2}|[A-Za-z]{3})[A-Za-z]*[-/. ](\d{4})", d)
    if not m:
        return ""
    mo = m.group(2)
    mo = MON[int(mo) - 1] if mo.isdigit() and 1 <= int(mo) <= 12 else mo.upper()
    return f"{int(m.group(1)):02d}-{mo}-{m.group(3)}"


def age_from(raw):
    """Age from a date of birth, or from the year alone when the Aadhaar shows only the year of birth."""
    d = dob_tn(raw)
    if d:
        return age_of(d)
    m = re.match(r"^\s*(19\d{2}|20\d{2})\s*$", str(raw or ""))
    return str(datetime.date.today().year - int(m.group(1))) if m else ""


_VD = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
       [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1], [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
       [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_VP = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
       [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1], [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def aadhaar_ok(v):
    """12 digits, not starting with 0/1, Verhoeff check digit right (one wrong digit fails it)."""
    n = re.sub(r"\D", "", str(v or ""))
    if not re.fullmatch(r"[2-9]\d{11}", n):
        return False
    c = 0
    for i, x in enumerate(reversed(n)):
        c = _VD[c][_VP[i % 8][int(x)]]
    return c == 0


def modt_problems(case):
    """MODT checks before login: all four boundaries (English and Tamil), and the MODT extent not more than the patta's total."""
    d, out = case.get("draft") or {}, []
    miss = [f"{s}{' (Tamil)' if t else ''}" for s in ("North", "South", "East", "West") for t in ("", " Tamil") if d.get(f"Boundary {s}{t}") in (None, "")]
    if miss:
        out.append("boundary empty in Drafting: " + ", ".join(miss) + " (take it from the title deed)")
    docs, cw = case.get("docs") or {}, case.get("caseWit") or {}
    for n in (1, 2):
        if (docs.get(f"w{n}_kyc") or []) and not (cw.get(str(n)) or cw.get(n) or {}).get("name"):
            out.append(f"witness {n} Aadhaar is uploaded in the case ({(docs[f'w{n}_kyc'][0].get('name') or '')}) but not read - press 'Read customer's witness Aadhaar' in Drafting")
    tot, ext = area_sqm(g(d, "Total survey extent")), area_sqm(g(d, "Extent"))
    ptype = ptype_of(d) or ""
    if "Agri" in ptype and tot and ext and ext > tot * 1.002:
        u = case_unit(d)
        out.append(f"mortgaged extent {g(d, 'Extent')} is MORE than the patta total {area_in(g(d, 'Total survey extent'), u)} {u} - check the MODT / patta")
    return out


def aadhaar_problems(case, kind):
    """Every Aadhaar the robot will type for this case: [] when all are right."""
    out, people = [], []
    if kind == "modt":
        d = case.get("draft") or {}
        holders = int(re.sub(r"\D", "", g(d, "Title holders")) or "1")
        for n in range(1, min(holders, 4) + 1):
            if g(d, f"Mortgagor {n} name"):
                people.append((f"Title holder {n} ({g(d, f'Mortgagor {n} name')})", g(d, f"Mortgagor {n} Aadhaar")))
        lists = kv_get("config", "lists") or {}
        cw = case.get("caseWit") or {}
        for n in (1, 2):
            w = cw.get(str(n)) or cw.get(n)
            if not (w and w.get("name")):
                nm = g(d, f"Witness {n}") or case.get(f"w{n}") or ""
                w = next((x for x in (lists.get("wit") or []) if x.get("name") == nm), None)
            if w:
                people.append((f"Witness {n} ({w.get('name', '')})", w.get("aadhaar")))
    else:
        t = apply_profile(case, case.get("tp") or {})
        for pre in ("Rep", "Witness 1", "Witness 2"):
            if g(t, f"{pre} name"):
                people.append((f"{pre} ({g(t, f'{pre} name')})", g(t, f"{pre} Aadhaar")))
    for who, v in people:
        dg = re.sub(r"\D", "", str(v or ""))
        if not dg:
            out.append(f"{who}: Aadhaar number empty")
        elif len(dg) != 12:
            out.append(f"{who}: {len(dg)} digits, not 12")
        elif not aadhaar_ok(dg):
            out.append(f"{who}: {dg[:4]} {dg[4:8]} {dg[8:]} fails the Aadhaar check (a digit is wrong)")
    return out


def record_stop(job, case_id, reason):
    """Keep on the case where and why the robot stopped (and the TNREGINET draft number if one was seen)."""
    try:
        kv_merge("cases", case_id, {"tpStop": {"at": now(), "step": job.last, "reason": str(reason)[:400],
                                               "draftNo": job.d.get("draftNo", ""), "job": job.id, "timing": job.timing()}})
    except Exception as e:
        log("stop note failed", e)


DRAFT_RE = re.compile(r"(Draft/[A-Za-z ]{3,40}/\d{5,}/\d{4})|(?:draft|application|reference|ref)\s*(?:no|number|id)\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-]{5,}\d)", re.I)


def grab_draft(P, job, said=""):
    """TNREGINET shows a draft / document number after Proceed or Save as draft: keep it for the case."""
    try:
        txt = said + " " + P.p.evaluate("()=>document.body.innerText.slice(0,4000)")
    except Exception:
        txt = said
    m = DRAFT_RE.search(txt)
    dn = m and (m.group(1) or m.group(2))
    if dn and (not job.d.get("draftNo") or not str(job.d.get("draftNo")).startswith("Draft/") and dn.startswith("Draft/")):
        job.upd(draftNo=dn)
        try:
            kv_merge("cases", job.d.get("case", ""), {"tnDraftNo": dn})
        except Exception:
            pass


def age_of(dob):
    m = re.match(r"^(\d{2})-([A-Z]{3})-(\d{4})$", dob or "")
    if not m:
        return ""
    b = datetime.date(int(m.group(3)), MON.index(m.group(2)) + 1, int(m.group(1)))
    t = datetime.date.today()
    return str(t.year - b.year - ((t.month, t.day) < (b.month, b.day)))


TA_DIST = {"ariyalur": "அரியலூர்", "chengalpattu": "செங்கல்பட்டு", "chennai": "சென்னை", "coimbatore": "கோயம்புத்தூர்|கோவை", "cuddalore": "கடலூர்",
           "dharmapuri": "தருமபுரி|தர்மபுரி", "dindigul": "திண்டுக்கல்", "erode": "ஈரோடு", "kallakurichi": "கள்ளக்குறிச்சி", "kanchipuram": "காஞ்சிபுரம்",
           "kanniyakumari": "கன்னியாகுமரி", "kanyakumari": "கன்னியாகுமரி", "karur": "கரூர்", "krishnagiri": "கிருஷ்ணகிரி", "madurai": "மதுரை",
           "mayiladuthurai": "மயிலாடுதுறை", "nagapattinam": "நாகப்பட்டினம்", "namakkal": "நாமக்கல்", "nilgiris": "நீலகிரி", "perambalur": "பெரம்பலூர்",
           "pudukkottai": "புதுக்கோட்டை", "ramanathapuram": "இராமநாதபுரம்|ராமநாதபுரம்", "ranipet": "இராணிப்பேட்டை|ராணிப்பேட்டை", "salem": "சேலம்",
           "sivaganga": "சிவகங்கை", "tenkasi": "தென்காசி", "thanjavur": "தஞ்சாவூர்", "theni": "தேனி", "thoothukudi": "தூத்துக்குடி", "tuticorin": "தூத்துக்குடி",
           "tiruchirappalli": "திருச்சிராப்பள்ளி|திருச்சி", "trichy": "திருச்சிராப்பள்ளி|திருச்சி", "tirunelveli": "திருநெல்வேலி", "tirupathur": "திருப்பத்தூர்",
           "tirupattur": "திருப்பத்தூர்", "tiruppur": "திருப்பூர்", "tirupur": "திருப்பூர்", "tiruvallur": "திருவள்ளூர்", "thiruvallur": "திருவள்ளூர்",
           "tiruvannamalai": "திருவண்ணாமலை", "tiruvarur": "திருவாரூர்", "thiruvarur": "திருவாரூர்", "vellore": "வேலூர்", "viluppuram": "விழுப்புரம்",
           "villupuram": "விழுப்புரம்", "virudhunagar": "விருதுநகர்"}
_STATE = re.compile(r"^(tamil\s*nadu|tamilnadu|t\.?\s*n|தமிழ்\s*நாடு|india|இந்தியா)$", re.I)


def clean_addr(a, district="", ta=False):
    """Address only up to the district (same rules as the portal): no leading S/O …, no pincode, no Tamil Nadu / தமிழ்நாடு,
    no district at the end (English, or its Tamil name); no ':' characters."""
    a = re.sub(r"\s+", " ", (a or "").replace("：", " ").replace(":", " ")).strip()
    if not a:
        return a
    a = re.sub(r"^\s*[SWDC]\s*/\s*O\.?\s*-?\s*[^,]*,\s*", "", a, flags=re.I)
    p = [re.sub(r"^P\.?\s*O\b\.?\s*[-–]?\s*(\S.*)$", r"\1 (PO)", x, flags=re.I) for x in re.split(r"\s*,\s*", a)]
    p = [re.sub(r"[\s\-–.]+$", "", re.sub(r"(pin(\s*code)?\s*-?\s*)?\b\d{6}\b", "", x, flags=re.I)).strip() for x in p]
    p = [x for x in p if x]
    while len(p) > 1 and _STATE.match(p[-1]):
        p.pop()
    if len(p) > 1:
        dn = re.sub(r"\s*(dist(rict)?\.?)\s*$", "", (district or "").lower()).strip()
        last = p[-1].lower()
        bare = re.sub(r"^(dist(rict)?\.?|மாவட்டம்)\s*[-–]?\s*", "", re.sub(r"\s*(dist(rict)?\.?|மாவட்டம்)\s*$", "", last)).strip()
        tnames = TA_DIST.get(dn, "").split("|") if TA_DIST.get(dn) else ([] if dn else "|".join(TA_DIST.values()).split("|"))
        if (dn and bare == dn) or re.search(r"\bdist(rict)?\.?$|மாவட்டம்$|^dist(rict)?\b", last) or (ta and len(p) > 2 and bare in tnames):
            p.pop()
    return re.sub(r"\s+", " ", ", ".join(p)).strip(" ,-")


def same_len_tamil(ta, en):
    """The Tamil name must match the English one: 'Singumani' -> keep only the first Tamil word."""
    if not ta or not en:
        return ta
    n = len(en.split())
    w = ta.split()
    return " ".join(w[:n]) if len(w) > n else ta


def modt_party(P, job, page, d, n):
    pre = f"Mortgagor {n}"
    name = g(d, f"{pre} name")
    job.step(f"2 · Title holder {n}: {name}", page)
    P.sel("partyType", "Individual", 2)
    sal = g(d, f"{pre} salutation") or "Mr."
    P.sel("salutationId", sal_re(sal))
    P.set("fullName", name)
    P.tamil("fullNameLL", same_len_tamil(g(d, f"{pre} name Tamil"), name), name)
    rel = g(d, f"{pre} relation") or "S/o"
    if rel.lower().startswith("c"):  # TNREGINET has no Care of: C/O → Son of for a man, Daughter of for a woman
        rel = "D/o" if sal.lower().startswith(("mrs", "ms", "miss")) else "S/o"
    P.sel("firstRelationTypeId", rel_re(rel))
    P.set("firstRelativeName", g(d, f"{pre} relative name"))
    P.tamil("firstRelativeNameLL", g(d, f"{pre} relative name Tamil"), g(d, f"{pre} relative name"))
    dob = dob_tn(g(d, f"{pre} date of birth"))
    if dob:
        P.set("dob", dob)
        P.js("(()=>{const e=document.getElementById('dob');if(e){e.dispatchEvent(new Event('change',{bubbles:true}));e.dispatchEvent(new Event('blur',{bubbles:true}))}})()")
        P.wait(1)
        P.set("age", age_of(dob))
    elif age_from(g(d, f"{pre} date of birth")):
        P.set("age", age_from(g(d, f"{pre} date of birth")))  # Aadhaar shows only the year of birth
    P.sel("genderId", "^Female" if sal.lower().startswith(("mrs", "ms", "miss")) else "^Male")
    P.sel("identificationTypeId", "Aadhaar")
    P.set("identificationNo", re.sub(r"\D", "", g(d, f"{pre} Aadhaar")))
    P.js("tick('aadhaarConsentCheckBox')")
    dist = g(d, f"{pre} district")
    address(P, "Tamil Nadu", dist, addr=clean_addr(g(d, f"{pre} address"), dist),
            addr_ta=clean_addr(g(d, f"{pre} address Tamil"), dist, True), pin=g(d, f"{pre} pincode"), mobile=g(d, f"{pre} phone"))
    msg = P.click("addExecutantDetailsInGrid", 3)
    if msg and not re.search(r"success|added|also going", msg, re.I):
        raise Stop(f"Title holder {n}: TNREGINET said '{msg[:160]}'")


DRAFT_NO_JS = r"""()=>{const m=document.body.innerText.match(/Draft\s*Number\s*:?\s*(Draft\/[^\n]*?\/\d{5,}\/\d{4})/i);return m?m[1].replace(/\s+/g,' ').trim():''}"""
STAGES = ["parties", "property", "loan", "legal", "created"]


def keep_draft(page, job, stage):
    """TNREGINET shows 'Draft Number: Draft/Deposit Of Title Deeds/354418042/2026' on every stage: keep it on the case with how far we got."""
    try:
        dn = page.evaluate(DRAFT_NO_JS)
        cid = job.d.get("case") or job.d.get("caseId") or ""
        if dn:
            job.upd(draftNo=dn)
            if cid:
                kv_merge("cases", cid, {"tpDraft": dn, "tpDraftStage": stage, "tpDraftAt": now()})
        return dn
    except Exception as e:
        log("draft number:", e)
        return ""


OPEN_DRAFT_JS = r"""(num)=>{const vis=e=>!!e&&e.getClientRects().length>0;
  const cells=[...document.querySelectorAll('a,td,span,label,div')].filter(e=>vis(e)&&(e.innerText||'').includes(num)&&(e.innerText||'').length<200);
  cells.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);const c=cells[0];if(!c)return '';
  const row=c.closest('tr')||c.parentElement;const pick=(row&&row.querySelector('input[type=radio],input[type=checkbox]'));
  if(pick){pick.click();pick.dispatchEvent(new Event('change',{bubbles:true}))}else{(c.closest('a')||c.querySelector('a')||c).click()}
  return (row||c).innerText.replace(/\s+/g,' ').slice(0,160)}"""


def open_draft(P, job, page, dn):
    """Draft Listing (top of the Create Document page) → the draft number → Load. Stops with a question when it is not found."""
    num = (re.search(r"/(\d{5,})/", dn) or re.search(r"(\d{5,})", dn))
    num = num.group(1) if num else dn
    job.step(f"0 · Opening the saved draft {dn}", page)
    info = page.evaluate("""()=>{const a=[...document.querySelectorAll('a')].find(e=>/^\\s*draft\\s*listing\\s*$/i.test(e.textContent||''));
      return a?{href:a.href||'',target:a.target||'',onclick:(a.getAttribute('onclick')||'').slice(0,120)}:null}""") or {}
    job.step(f"0 · Draft Listing link: {str(info)[:160]}", page)
    hit = ""
    href = info.get("href") or ""
    if href.startswith("http") and not re.search(r"#\s*$|javascript:", href, re.I):
        page.goto(href, wait_until="domcontentloaded", timeout=90000)
        hit = "opened " + href.split("?")[0][-60:]
    else:
        n0 = len(page.context.pages)
        hit = click_text(page, r"^\s*draft\s*listing\s*$") or click_text(page, r"draft\s*list")
        P.wait(3)
        if len(page.context.pages) > n0:  # it opened in a new tab: take its address into this tab
            newp = page.context.pages[-1]
            try:
                newp.wait_for_load_state("domcontentloaded", timeout=30000)
                u = newp.url
                newp.close()
                page.goto(u, wait_until="domcontentloaded", timeout=90000)
                hit += " (new tab -> " + u.split("?")[0][-60:] + ")"
            except Exception as e:
                log("draft listing tab:", e)
    wait_loader(page); P.wait(3)
    job.step(f"0 · Draft Listing ({hit or 'link not found'})", page)
    row = page.evaluate(OPEN_DRAFT_JS, num)
    if not row:  # a search box on the listing
        page.evaluate("""(n)=>{const i=[...document.querySelectorAll('input[type=text],input:not([type])')].find(e=>e.getClientRects().length&&/draft/i.test((e.id||'')+(e.name||'')+(e.placeholder||'')));if(i){i.value=n;i.dispatchEvent(new Event('input',{bubbles:true}));i.dispatchEvent(new Event('change',{bubbles:true}))}}""", num)
        click_btn_text(P, r"^search$"); wait_loader(page); P.wait(3)
        row = page.evaluate(OPEN_DRAFT_JS, num)
    if not row:
        shot = survey_shot(page, job, 0, "draftlist")
        survey_question(job, f"Draft Listing: draft {dn} not found (clicked: {hit or 'no Draft Listing link'}). Open TNREGINET's Draft Listing and check the number, or clear it in TP Generate to start a fresh document.", shot)
        raise Stop(f"Saved draft {dn} not found in Draft Listing. A robot question with the screenshot is on the case.")
    P.wait(1.5)
    click_btn_text(P, r"^\s*load\s*$") or click_text(page, r"^\s*load\s*$")
    wait_loader(page); P.wait(3); P.popup_ok()
    wait_for(P, "()=>{const e=document.getElementById('partyType');return !!(e&&e.offsetParent)||/Draft\\s*Number/i.test(document.body.innerText)}", 30)
    got = page.evaluate(DRAFT_NO_JS)
    if num not in (got or ""):
        shot = survey_shot(page, job, 0, "draftload")
        survey_question(job, f"Draft {dn}: pressed Load but the page shows '{got or 'no draft number'}'. Check the screen.", shot)
        raise Stop(f"Draft {dn} did not open after Load. A robot question with the screenshot is on the case.")
    job.step(f"0 · Draft {got} loaded", page)
    return got


def run_modt(page, job, case):
    """MODT robot, part 1 (from the recording of 9 Nov): create document, link the title deed(s), title holders,
    mortgagee company, skip representative, witnesses, presenter; then property, loan, legal conditions, Create Document, uploads, print, fees."""
    d = case.get("draft") or {}
    P = Portal(page, job)
    dn, stg = (case.get("tpDraft") or "").strip(), case.get("tpDraftStage") or "parties"
    job.modt_stage = "start"
    if dn and stg == "start":
        log("saved draft", dn, "stopped before the parties were saved - a new document is made")
        dn = ""
    if dn and not case.get("tpNo"):
        if stg not in ("parties", "property"):
            raise Stop(f"Saved draft {dn} is already past the property stage ({stg}). Finish it by hand in Draft Listing, or clear the draft number in TP Generate to start fresh.")
        open_presenceless(page, job)
        open_draft(P, job, page, dn)
        comments = []
        holders = int(re.sub(r"\D", "", g(d, "Title holders")) or "1")
        P.wait(2)
        tbl = page.evaluate(TABLE_TEXT_JS, "Name") or ""
        miss = [g(d, f"Mortgagor {n} name") for n in range(1, min(holders, 4) + 1) if g(d, f"Mortgagor {n} name") and nz(g(d, f"Mortgagor {n} name"))[:6] not in nz(tbl)]
        if miss:
            comments.append("loaded draft: title holder(s) not seen in the party table - " + ", ".join(miss) + " (check)")
        job.step("2-6 · Parties already saved in the draft (not added again) - going to Property", page)
        job.modt_stage = "parties"
        return modt_after_parties(P, job, page, d, case, comments)
    job.step("1 · Create document & link the title deed(s)")
    open_presenceless(page, job)
    if not page.locator("#deedType").count():
        job.step("Create Document page not found", page)
        raise Stop("The Create Document page did not open. Send the screen to Claude.")
    P.sel("deedType", "Deposit of Title", 2)
    P.js("tick('linkPrevDoc')"); P.wait(2)
    deeds = [n for n in (1, 2, 3) if g(d, f"Deed {n} Doc No")]
    if not deeds:
        raise Stop("No title deed in Drafting (1 · Linked documents). Fill Deed 1 SRO, Doc No and Year.")
    comments = []
    for n in deeds:
        link_doc(P, job, page, g(d, f"Deed {n} SRO") or g(d, "Property SRO"), "1", g(d, f"Deed {n} Doc No"),
                 g(d, f"Deed {n} Year"), "Regular Document", f"title deed {n}")
    linked_check(page, d, deeds, comments)
    job.step("1 · Linked documents checked", page)
    P.click("createDocProceed", 6)
    keep_draft(page, job, "start")
    # 2. Mortgagors (title holders)
    wait_for(P, "()=>{const e=document.getElementById('partyType');return !!(e&&e.offsetParent)}", 20)
    holders = int(re.sub(r"\D", "", g(d, "Title holders")) or "1")
    P.wait(2)
    clear_copied_holders(P, job, page, d, holders, comments)
    for n in range(1, min(holders, 4) + 1):
        if g(d, f"Mortgagor {n} name"):
            modt_party(P, job, page, d, n)
    job.step("2 · Title holders added", page)
    P.click("btnChildNext", 4)
    # 3. Mortgagee company
    job.step("3 · Mortgagee (company)", page)
    wait_for(P, "()=>{const e=document.getElementById('partyType');return !!(e&&e.offsetParent)}", 15)
    P.sel("partyType", "Company", 3)
    co = g(d, "Govt Body Name")
    r = P.js(f"selco('govtBodyList',{co!r})"); P.wait(2)
    if not str(r).startswith("ok"):
        job.step("Company not found in TNREGINET list", page)
        raise Stop(f"TNREGINET's company list has no match for '{co}'. Check Govt Body Name in Drafting.")
    address(P, g(d, "Company state") or "Tamil Nadu", g(d, "Company district"), addr=g(d, "Company address").replace(":", " "),
            addr_ta=g(d, "Company address Tamil").replace(":", " "), pin=g(d, "Company pincode"))
    add_and_next(P, "Company")
    # 4. Representative: not needed for MODT
    job.step("4 · Representative: skipped for MODT", page)
    P.click("btnChildNext", 4)
    # 5. Witnesses from the saved witness list
    lists = kv_get("config", "lists") or {}
    wl = lists.get("wit") or []
    t = {}
    cw = case.get("caseWit") or {}
    for n in (1, 2):
        w = cw.get(str(n)) or cw.get(n)  # the customer's own witness, read from the Aadhaar in this case (this case only)
        nm = (w or {}).get("name") or g(d, f"Witness {n}") or case.get(f"w{n}") or ""
        if not (w and w.get("name")):
            w = next((x for x in wl if x.get("name") == nm), None)
        if not w:
            P.warn.append(f"Witness {n} '{nm}' not in the saved witness list")
            continue
        for k, v in (("salutation", w.get("sal")), ("name", w.get("name")), ("name (Tamil)", w.get("name_ta")), ("relation", w.get("rel_type")),
                     ("relative name", w.get("rel_name")), ("relative name (Tamil)", w.get("rel_ta")), ("gender", w.get("gender")),
                     ("Aadhaar", re.sub(r"\D", "", str(w.get("aadhaar") or ""))), ("district", w.get("district")), ("address", clean_addr(w.get("addr"), w.get("district"))), ("pincode", w.get("pincode"))):
            if v:
                t[f"Witness {n} {k}"] = str(v)
        a = age_from(str(w.get("dob") or ""))
        if a:
            t[f"Witness {n} age"] = a
    open_subtab(P, "Witness Details", "chkWitnessType0")
    for n in (1, 2):
        if not t.get(f"Witness {n} name"):
            continue
        job.step(f"5 · Witness {n}: {t[f'Witness {n} name']}", page)
        P.js("tick('chkWitnessType0')")
        person(P, t, f"Witness {n}", designation=False)
        address(P, "Tamil Nadu", t.get(f"Witness {n} district", ""), addr=t.get(f"Witness {n} address", ""), pin=t.get(f"Witness {n} pincode", ""))
        msg = P.click("addExecutantDetailsInGrid", 3)
        if msg and not re.search(r"success|added", msg, re.I):
            raise Stop(f"Witness {n}: TNREGINET said '{msg[:160]}'")
    # 6. Presenter: the chosen title holder (default the first)
    open_subtab(P, "Presenter Details", "cmb_presenter")
    job.step("6 · Presenter", page)
    wait_for(P, "()=>document.getElementById('cmb_presenter').options.length>1", 15)
    # presenter: the first title holder with a phone number (Drafting's choice when that holder has a phone)
    pn = re.sub(r"\D", "", g(d, "Presenter")) or "1"
    with_phone = [n for n in range(1, min(holders, 4) + 1) if g(d, f"Mortgagor {n} name") and len(re.sub(r"\D", "", g(d, f"Mortgagor {n} phone"))) >= 10]
    if with_phone and int(pn) not in with_phone:
        pn = str(with_phone[0])
    elif not with_phone:
        comments.append("no title holder has a phone number - presenter set to title holder " + pn)
    P.fuzzy("cmb_presenter", g(d, f"Mortgagor {pn} name") or g(d, "Mortgagor 1 name"), 1)
    P.click("btnMainSaveAsDraft", 4)
    job.step("Parties done - draft saved", page)
    job.modt_stage = "parties"
    keep_draft(page, job, "parties")
    return modt_after_parties(P, job, page, d, case, comments)


def modt_after_parties(P, job, page, d, case, comments):
    # 7. Property (MODT flow of 11 Nov 2026) → 8. loan … Create Document → uploads → print → fees
    try:
        modt_property(P, job, page, d, case, comments)
    except Stop as e:
        P.click("btnMainSaveAsDraft", 4)
        job.step("Property not done", page)
        note = ("MODT draft saved on TNREGINET: title deed linked, title holder(s), company, witnesses, presenter. Property NOT done: " + str(e) +
                " Continue by hand from Property in My Documents." + (" CHECK: " + " · ".join(comments) if comments else ""))
        if P.warn:
            note += " Warnings: " + "; ".join(P.warn[:8])
        keep_draft(page, job, "parties")
        job.upd(status="done", message=note, comments=comments)
        return
    keep_draft(page, job, "property")
    return modt_second_half(P, job, page, d, case, comments)


def label_select(page, label_re, opt_re):
    """Pick an option in the visible dropdown whose label matches (e.g. Purpose of Loan)."""
    return page.evaluate("""([pat,op])=>{const re=new RegExp(pat,'i'),ro=new RegExp(op,'i');
        const s=[...document.querySelectorAll('select')].find(e=>{if(!e.offsetParent)return false;let l=e.labels&&e.labels[0]?e.labels[0].innerText:'';
          if(!l){let p=e.parentElement;for(let i=0;i<3&&p&&!l;i++){const x=p.querySelector('label');if(x)l=x.innerText;p=p.parentElement}}return re.test(l)||re.test(e.id||'')});
        if(!s)return 'NOFIELD';const o=[...s.options].find(o=>ro.test(o.text));if(!o)return 'NOOPT '+[...s.options].map(o=>o.text.trim()).slice(0,8).join('|');
        if(window.jQuery)jQuery(s).val(o.value).trigger('change');else{s.value=o.value;s.dispatchEvent(new Event('change',{bubbles:true}))}return 'ok '+o.text}""", [label_re, opt_re])


def modt_next(P, job, page, what):
    """Save and Continue to the next main step; stop with TNREGINET's own words if it refuses."""
    msg = main_next(P, page, 5)
    errs = form_errors(P)
    if errs or (msg and re.search(r"mandatory|please enter|please select|invalid|required", msg, re.I)):
        job.step(f"{what}: not accepted", page)
        raise Stop(f"{what}: TNREGINET wants: {'; '.join(errs) or msg[:160]}. The draft is saved; finish this page by hand or fix Drafting and start again.")
    return msg


APPEND_LOD_JS = r"""(txt)=>{const html='<p>&nbsp;</p>'+txt.split(/\n/).map(l=>'<p>'+l.replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))+'</p>').join('');
  if(window.CKEDITOR&&CKEDITOR.instances){const k=Object.keys(CKEDITOR.instances).find(k=>{const e=CKEDITOR.instances[k];return e&&e.container&&e.container.$&&e.container.$.offsetParent});
    if(k){const e=CKEDITOR.instances[k];e.setData(e.getData()+html);return 'ckeditor '+k}}
  if(window.tinymce&&tinymce.activeEditor){tinymce.activeEditor.setContent(tinymce.activeEditor.getContent()+html);return 'tinymce'}
  const ce=[...document.querySelectorAll('[contenteditable=true]')].find(e=>e.offsetParent);if(ce){ce.insertAdjacentHTML('beforeend',html);ce.dispatchEvent(new Event('input',{bubbles:true}));return 'editable'}
  const ta=[...document.querySelectorAll('textarea')].filter(e=>e.offsetParent).pop();if(ta){ta.value+='\n'+txt;['input','change','blur'].forEach(t=>ta.dispatchEvent(new Event(t,{bubbles:true})));return 'textarea '+ta.id}
  return ''}"""


# ---------------- MODT property (rules of 11 Nov 2026, from the screenshots) ----------------
# Every helper finds things by the words printed on the screen, so small layout changes do not break it.
# Nothing here stops the robot for a mismatch: it is written into `comments` and shown on the case after the TP.

VIS_JS = r"""const vis=e=>!!e&&e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden';
const own=e=>[...e.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join(' ').replace(/\s+/g,' ').trim();
const labelOf=e=>{let l=e.labels&&e.labels[0]?e.labels[0].innerText:'';if(!l&&e.id){const x=document.querySelector('label[for="'+e.id+'"]');if(x)l=x.innerText}
  if(!l){const p=e.closest('label');if(p)l=p.innerText}
  if(!l){let s=e.nextSibling;while(s&&!l){l=(s.textContent||'').trim();s=s.nextSibling}}
  if(!l){let s=e.previousSibling;while(s&&!l){l=(s.textContent||'').trim();s=s.previousSibling}}
  if(!l&&e.parentElement)l=e.parentElement.innerText;return (l||'').replace(/\s+/g,' ').trim()};
const textEl=re=>{const els=[...document.querySelectorAll('label,span,div,p,b,strong,h1,h2,h3,h4,h5,h6,td,th,legend')].filter(e=>vis(e)&&re.test(own(e)||''));
  const w=e=>(own(e)||'').length+(/^T[HD]$/.test(e.tagName)?1000:0);els.sort((a,b)=>w(a)-w(b));return els[0]};
const after=(el,sel)=>[...document.querySelectorAll(sel)].find(x=>vis(x)&&(el.compareDocumentPosition(x)&Node.DOCUMENT_POSITION_FOLLOWING));
const fire=e=>['input','change','blur'].forEach(t=>e.dispatchEvent(new Event(t,{bubbles:true})));"""

RADIO_JS = "(pat)=>{" + VIS_JS + r"""const re=new RegExp(pat,'i');
  const r=[...document.querySelectorAll('input[type=radio]')].find(e=>(vis(e)||vis(e.parentElement))&&re.test(labelOf(e)));
  if(!r)return '';if(!r.checked){r.click();if(!r.checked){r.checked=true;fire(r)}}return labelOf(r).slice(0,60)||'ok'}"""

CHECK_JS = "([pat,on])=>{" + VIS_JS + r"""const re=new RegExp(pat,'i');
  const c=[...document.querySelectorAll('input[type=checkbox]')].find(e=>(vis(e)||vis(e.parentElement))&&re.test(labelOf(e)));
  if(!c)return '';if(c.checked!==on){c.click();if(c.checked!==on){c.checked=on;fire(c)}}return 'ok'}"""

# fill the first visible box that comes after the printed words (labels like "Mortgaged Survey Extent*" sit above a "Square Feet" sub-label)
FILL_AFTER_JS = "([pat,v,tag])=>{" + VIS_JS + r"""const t=textEl(new RegExp(pat,'i'));if(!t)return 'NOTEXT';
  const e=after(t,tag||'input[type=text],input[type=number],input:not([type]),textarea');if(!e)return 'NOBOX';
  e.focus();e.value=v;fire(e);return e.id||e.name||'ok'}"""

SELECT_AFTER_JS = "([pat,op])=>{" + VIS_JS + r"""const t=textEl(new RegExp(pat,'i'));if(!t)return 'NOTEXT';
  let s=after(t,'select');if(!s){s=[...document.querySelectorAll('select')].find(x=>t.compareDocumentPosition(x)&Node.DOCUMENT_POSITION_FOLLOWING&&vis(x.nextElementSibling||x.parentElement))}
  if(!s)return 'NOSEL';const ro=new RegExp(op,'i');const o=[...s.options].find(o=>ro.test(o.text.trim()));
  if(!o)return 'NOOPT '+[...s.options].map(o=>o.text.trim()).slice(0,25).join('|');
  if(window.jQuery){jQuery(s).val(o.value).trigger('change').trigger('chosen:updated')}else{s.value=o.value;fire(s)}return 'ok '+o.text.trim()}"""

OPTIONS_AFTER_JS = "(pat)=>{" + VIS_JS + r"""const t=textEl(new RegExp(pat,'i'));if(!t)return null;
  let s=after(t,'select');if(!s)s=[...document.querySelectorAll('select')].find(x=>t.compareDocumentPosition(x)&Node.DOCUMENT_POSITION_FOLLOWING);
  return s?[...s.options].map(o=>o.text.trim()):null}"""

CLICK_AFTER_JS = "([pat,btn])=>{" + VIS_JS + r"""const t=textEl(new RegExp(pat,'i'));if(!t)return '';const re=new RegExp(btn,'i');
  const b=[...document.querySelectorAll('button,input[type=button],input[type=submit],a.btn')].find(x=>vis(x)&&re.test((x.value||x.innerText||'').trim())&&(t.compareDocumentPosition(x)&Node.DOCUMENT_POSITION_FOLLOWING));
  if(!b)return '';b.click();return (b.id||'')+':'+(b.value||b.innerText||'').trim()}"""

# pick a row (radio) in the visible table whose header matches; prefer the row matching `want`, else the newest year
TABLE_PICK_JS = "([head,want,newest])=>{" + VIS_JS + r"""const hr=new RegExp(head,'i');
  const t=[...document.querySelectorAll('table')].find(x=>vis(x)&&x.rows.length>1&&hr.test(x.rows[0].innerText));if(!t)return null;
  const rows=[...t.rows].slice(1).filter(r=>r.querySelector('input[type=radio],input[type=checkbox]'));if(!rows.length)return null;
  let r=null;if(want){const w=new RegExp(want,'i');r=rows.find(x=>w.test(x.innerText))}
  if(!r&&newest){r=rows.slice().sort((a,b)=>{const y=x=>Math.max(0,...(x.innerText.match(/\b(19|20)\d\d\b/g)||[]).map(Number));return y(b)-y(a)})[0]}
  r=r||rows[0];const i=r.querySelector('input[type=radio],input[type=checkbox]');if(!i.checked)i.click();
  return {row:r.innerText.replace(/\s+/g,' ').trim().slice(0,200),n:rows.length}}"""

TABLE_TEXT_JS = "(head)=>{" + VIS_JS + r"""const hr=new RegExp(head,'i');
  return [...document.querySelectorAll('table')].filter(x=>vis(x)&&x.rows.length>1&&hr.test(x.rows[0].innerText)).map(x=>x.innerText.replace(/\s+/g,' ')).join(' || ')}"""

# delete every saved row of the visible tables (Building Details copied from the old deed)
# delete the saved rows of the visible table(s) whose header matches (old survey / property rows copied from the linked deed)
DEL_TABLE_ROW_JS = "(head)=>{" + VIS_JS + r"""const hr=new RegExp(head,'i');const t=[...document.querySelectorAll('table')].filter(x=>vis(x)&&x.rows.length>1&&hr.test(x.rows[0].innerText));
  for(const x of t){for(const r of [...x.rows].slice(1)){const e=[...r.querySelectorAll('a,button,i,img,span,input')].find(e=>/delete|trash|remove|bin/i.test((e.className||'')+' '+(e.title||'')+' '+(e.value||'')+' '+(e.getAttribute('onclick')||'')+' '+(e.getAttribute('src')||'')));
    if(e&&vis(e)){const txt=r.innerText.replace(/\s+/g,' ').slice(0,100);(e.closest('a,button')||e).click();return txt}}}return ''}"""

DEL_ICONS_JS = "()=>{" + VIS_JS + r"""const t=[...document.querySelectorAll('table')].filter(x=>vis(x)&&x.rows.length>1);
  for(const x of t){for(const r of [...x.rows].slice(1)){const e=[...r.querySelectorAll('a,button,i,img,span')].find(e=>/delete|trash|remove|bin/i.test((e.className||'')+' '+(e.title||'')+' '+(e.getAttribute('onclick')||'')+' '+(e.getAttribute('src')||'')));
    if(e&&vis(e)){(e.closest('a,button')||e).click();return r.innerText.replace(/\s+/g,' ').slice(0,100)}}}return ''}"""

SUBTAB_JS = "()=>{" + VIS_JS + r"""const names=['Property Details','Survey Details','Boundary Details','Building Details','Linear Measurement','Property Remarks','Property Items'];
  const a=[...document.querySelectorAll('a,li,button,span')].filter(e=>vis(e)&&names.includes((e.innerText||'').trim()));
  const isAct=e=>/active|selected|current/i.test((e.className||'')+' '+((e.parentElement||{}).className||''))||e.getAttribute('aria-selected')==='true';
  /* the green main-step chip "Property Details" at the top is also 'active': any other active tab name wins, then the section heading */
  const acts=a.filter(isAct),other=acts.find(e=>e.innerText.trim()!=='Property Details');if(other)return other.innerText.trim();
  const h=[...document.querySelectorAll('h1,h2,h3,h4,h5,h6,b,strong,legend')].filter(vis).map(x=>x.innerText.trim());
  const m={'Property Survey Details':'Survey Details','Property Remarks (if any)':'Property Remarks','Property Boundary Details':'Boundary Details','Linear Measurement Details':'Linear Measurement'};
  for(const x of h){if(m[x])return m[x];if(names.includes(x)&&x!=='Property Details')return x}
  if(/What is in the North/i.test(document.body.innerText))return 'Boundary Details';
  if(acts.length)return acts[0].innerText.trim();
  for(const x of h){if(names.includes(x))return x}
  if(/What is in the North/i.test(document.body.innerText))return 'Boundary Details';return ''}"""

LIST_PAGE_JS = "()=>{" + VIS_JS + r"""return !!textEl(/^List of all added property$/i)&&![...document.querySelectorAll('a,li,button,span')].some(e=>vis(e)&&/^(Survey Details|Boundary Details|Building Details|Property Remarks|Property Items)$/.test((e.innerText||'').trim()))}"""

PTYPES = ["Plot with Building", "Plot", "Flats", "Agricultural Land", "Agriculture Land with Building"]


def ptype_of(d):
    """Drafting's property type → TNREGINET's radio label (old cases said 'Vacant plot' etc.)."""
    v = g(d, "Property type").lower()
    if "agri" in v:
        return "Agriculture Land with Building" if "build" in v else "Agricultural Land"
    if "flat" in v:
        return "Flats"
    if "vacant" in v or (v.strip() == "plot"):
        return "Plot"
    return "Plot with Building" if v else ""


def unit_re(u):
    u = (u or "").lower()
    return ("Square\\s*Met|Sq\\.?\\s*M" if re.search(r"sq\.?\s*m|met", u) else "Hect" if "hect" in u else "Acre" if "acre" in u
            else "Cent" if "cent" in u else "Square\\s*Feet|Sq\\.?\\s*F")


PART_WORD = re.compile(r"\(?\s*\b(part|pt)\b\.?\s*\)?|\(p\)", re.I)


def strip_part(x):
    return re.sub(r"\s+", " ", PART_WORD.sub(" ", x)).strip(" ,-/")


def linked_check(page, d, deeds, comments):
    """After linking: the linked-documents table must show each deed's number, year and SRO (and its nature)."""
    txt = page.evaluate("()=>[...document.querySelectorAll('table')].filter(t=>t.getClientRects().length).map(t=>t.innerText.replace(/\\s+/g,' ')).join(' || ')")
    for n in deeds:
        no, yr, sro = num(g(d, f"Deed {n} Doc No")), g(d, f"Deed {n} Year"), g(d, f"Deed {n} SRO") or g(d, "Property SRO")
        rows = [r for r in re.split(r"\s\|\|\s|(?<=\d{4})\s(?=\d+\s)", txt) if re.search(rf"\b0*{no}\b", r)]
        if not rows:
            comments.append(f"Linked deed {n} ({no}/{yr}) not seen in the linked-documents table - check")
            continue
        row = rows[0]
        if yr and yr not in row:
            comments.append(f"Linked deed {n}: year {yr} not in the table row - check")
        if sro and nz(sro)[:5] not in nz(row):
            comments.append(f"Linked deed {n}: SRO '{sro}' not in the table row - check")
        if not re.search(r"sale|gift|settle|partition|release|deed|conveyance|exchange|will|agreement|document", row, re.I):
            comments.append(f"Linked deed {n}: nature of document not shown - check")


def clear_copied_holders(P, job, page, d, holders, comments):
    """Old deeds (before 2018) add their parties by themselves. Check the names, delete those rows, then each holder is
    added once, fresh, from our Aadhaar data."""
    ours = [nz(g(d, f"Mortgagor {n} name")) for n in range(1, min(holders, 4) + 1) if g(d, f"Mortgagor {n} name")]
    gone = []
    for _ in range(8):
        row = page.evaluate(DEL_ICONS_JS)
        if not row:
            break
        P.wait(1.5); P.popup_ok(); wait_loader(page); P.wait(1.5)
        gone.append(row)
    if gone:
        job.step(f"2 · {len(gone)} party row(s) copied from the old deed removed", page)
        for r in gone:
            if not any(o and (o[:6] in nz(r)) for o in ours):
                comments.append(f"copied party '{r[:60]}' from the old deed does not match our title holder names - check")
        comments.append(f"{len(gone)} party row(s) copied from the old deed were deleted and the title holder(s) added fresh from Aadhaar")


def surveys_of(d):
    """[(survey, subdivision, extent)] - the first is the main new survey number; more than one = Linked Survey."""
    out = []
    sn, sub = g(d, "New survey no"), g(d, "Subdivision")
    if sn:
        if not sub and "/" in sn:
            sn, sub = sn.split("/", 1)
        out.append((sn.strip(), sub.strip(), g(d, "Extent")))
    for tok in re.split(r"[,;\n]+", g(d, "Linked survey numbers")):
        tok = tok.strip()
        if not tok:
            continue
        ext = ""
        if "=" in tok:
            tok, ext = [x.strip() for x in tok.split("=", 1)]
        s, _, sd = tok.partition("/")
        if s and (s, sd) not in [(a, b) for a, b, _ in out]:
            out.append((s.strip(), sd.strip(), ext))
    return out


# land area: one unit for the whole property (the unit chosen at the top of TNREGINET's survey screen)
AREA_SQM = {"sqft": 0.09290304, "sqm": 1.0, "cent": 40.468564224, "acre": 4046.8564224, "hectare": 10000.0, "are": 100.0}
UNIT_LABEL = {"sqft": "Sq.ft", "sqm": "Sq.m", "cent": "Cent", "acre": "Acre", "hectare": "Hectare", "are": "Are"}


def unit_key(u):
    u = (u or "").lower()
    for k, rx in (("sqft", r"sq\.?\s*f|square\s*f|sft|ச\.?\s*அடி"), ("sqm", r"sq\.?\s*m|square\s*m|ச\.?\s*மீ"), ("hectare", r"hect|ஹெக்|\bha\b"),
                  ("acre", r"acre|ஏக்கர்|\bac\b"), ("cent", r"cent|சென்ட்|செண்ட்"), ("are", r"\bares?\b|ஏர்")):
        if re.search(rx, u):
            return k
    return ""


def area_sqm(t):
    """'0 - 2.23' (patta hectare-ares), '5.5 cents', '2 acres 30 cents', '0.0223 Hectare', '1200 sq.ft' -> square metres (None if no unit)."""
    t = str(t or "").replace(",", "").strip()
    if not t:
        return None
    lw = t.lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:acres?|ac\.?|ஏக்கர்)\s*(?:and|&)?\s*(\d+(?:\.\d+)?)\s*(?:cents?|சென்ட்|செண்ட்)", lw)
    if m:
        return (float(m.group(1)) * 100 + float(m.group(2))) * AREA_SQM["cent"]
    m = re.match(r"^\D*?(\d+)\s*[-–]\s*(\d+(?:\.\d+)?)", lw)
    if m and not re.search(r"acre|cent|sq|ஏக்கர்|சென்ட்", lw):
        return float(m.group(1)) * 10000 + float(m.group(2)) * 100
    k, m = unit_key(lw), re.search(r"\d+(?:\.\d+)?", lw)
    return float(m.group(0)) * AREA_SQM[k] if (k and m) else None


def area_in(t, unit):
    """The area in the case's unit, as TNREGINET wants it typed ('' when it cannot be worked out)."""
    k, s_ = unit_key(unit), area_sqm(t)
    if not k or s_ is None:
        return ""
    v = round(s_ / AREA_SQM[k], 4 if k in ("acre", "hectare") else 2)
    return ("%.4f" % v).rstrip("0").rstrip(".") if v else "0"


def case_unit(d):
    """The one unit of this property: Drafting's Unit, else the unit of the MODT extent."""
    return g(d, "Unit") or UNIT_LABEL.get(unit_key(g(d, "Extent")), "")


def num(x):
    m = re.search(r"\d+(?:\.\d+)?", str(x or "").replace(",", ""))
    return m.group(0) if m else ""


def child_next(P, page):
    if page.evaluate("()=>{const b=document.getElementById('btnChildNext');return !!(b&&b.offsetParent)}"):
        P.click("btnChildNext", 2)
    else:
        click_btn_text(P, r"^Next$")
    wait_loader(page)
    P.wait(2)
    return P.popup_ok()


def modt_property(P, job, page, d, case, comments):
    """Property, the MODT way (11 Nov 2026): type → copy from the newest linked deed → previous property → Copy → OK →
    Property Details (check, patta) → Survey → Boundary → Building (emptied) → Linear → Remarks → Items → list → Save and Continue."""
    note = lambda s: comments.append("Property: " + s)
    job.step("7 · Property details", page)
    main_next(P, page, 5)
    wait_for(P, "()=>/List of all added property|Property Type/i.test(document.body.innerText)", 25)
    P.wait(2)
    if page.evaluate(TABLE_TEXT_JS, "Mortgaged Survey Extent|Survey No"):
        for _ in range(4):
            gone = page.evaluate(DEL_TABLE_ROW_JS, "Mortgaged Survey Extent|Survey No|Property Type")
            if not gone:
                break
            P.wait(1.5); P.popup_ok(); wait_loader(page); P.wait(1.5)
            note(f"old property row from an earlier try deleted, added fresh ({gone[:70]})")
    ptype = ptype_of(d)
    if not ptype:
        ptype = "Plot with Building"
        note("property type empty in Drafting - Plot with Building used")
    if not page.evaluate(RADIO_JS, "^" + re.escape(ptype) + "$"):
        raise Stop(f"Property type '{ptype}' not found on TNREGINET. The draft is saved.")
    P.wait(1.5)
    job.step(f"7 · Property type: {ptype}", page)
    page.evaluate(CHECK_JS, ["Copy Property From Linked Document", True])
    P.wait(2.5)
    deed_no = g(d, "Deed 1 Doc No")
    pick = None
    for _ in range(10):
        pick = page.evaluate(TABLE_PICK_JS, ["Document No", "", True])
        if pick:
            break
        P.wait(1.5)
    if not pick:
        raise Stop("No linked document showed under 'Copy Property From Linked Document'. The draft is saved; check the linked title deed.")
    if pick["n"] > 1:
        note(f"{pick['n']} linked deeds listed - the newest was used ({pick['row'][:80]}); check with the legal report")
    job.step(f"7 · Linked deed: {pick['row'][:90]}", page)
    P.wait(2.5)
    vil = g(d, "Village")
    prev = None
    for _ in range(10):
        prev = page.evaluate(TABLE_PICK_JS, ["Registration Village|Local Body|Property Type", re.escape(vil[:6]) if vil else "", False])
        if prev:
            break
        P.wait(1.5)
    if not prev:
        raise Stop("'Previous Property Details' did not show for the linked deed. The draft is saved.")
    if prev["n"] > 1:
        note(f"{prev['n']} previous properties listed - '{prev['row'][:70]}' was copied; check")
    P.wait(1.5)
    if not click_btn_text(P, r"^Copy$"):
        raise Stop("The Copy button did not appear after choosing the previous property. The draft is saved.")
    wait_loader(page)
    P.wait(2.5)
    said = P.popup_ok()
    job.step(f"7 · Copied from the linked deed {('(' + said[:50] + ')') if said else ''}", page)
    wait_for(P, "()=>/Sub Registrar Office|Registration Village/i.test(document.body.innerText)", 25)
    P.wait(2)
    done, guard = [], 0
    while guard < 12:
        guard += 1
        if page.evaluate(LIST_PAGE_JS):
            break
        tab = page.evaluate(SUBTAB_JS) or ""
        if tab in done and tab != "Property Items":
            msg = form_errors(P)
            raise Stop(f"Property · {tab}: TNREGINET did not move on ({'; '.join(msg) or 'no message'}). The draft is saved.")
        done.append(tab)
        job.step(f"7 · Property · {tab or 'page'}", page)
        try:
            if tab == "Property Details":
                prop_details_tab(P, page, d, note)
            elif tab == "Survey Details":
                survey_tab(P, job, page, d, ptype, note)
            elif tab == "Boundary Details":
                boundary_tab(P, job, page, d, note)
            elif tab == "Building Details":
                for _ in range(8):
                    gone = page.evaluate(DEL_ICONS_JS)
                    if not gone:
                        break
                    P.wait(1.5); P.popup_ok(); wait_loader(page); P.wait(1.5)
                    note(f"building entry from the old deed removed ({gone[:60]})")
            elif tab == "Linear Measurement":
                linear_tab(P, page, d, note)
            elif tab == "Property Remarks":
                remarks_tab(P, job, page, d, note)
        except Stop:
            raise
        except Exception as e:
            note(f"{tab}: {str(e)[:120]}")
        job.step(f"7 · Property · {tab} done", page)
        said = child_next(P, page)
        if said and re.search(r"please|mandatory|invalid|required|select", said, re.I):
            note(f"{tab}: TNREGINET said '{said[:120]}'")
        if tab == "Property Items":
            break
    wait_for(P, LIST_PAGE_JS, 20)
    row = page.evaluate(TABLE_TEXT_JS, "Mortgaged Survey Extent|Survey No")
    sv = surveys_of(d)
    if sv and sv[0][0] and f"{sv[0][0]}/{sv[0][1]}".strip("/") not in row:
        note(f"survey {sv[0][0]}/{sv[0][1]} not seen in the property list ({row[:120]})")
    ex = area_in(g(d, "Extent"), case_unit(d)) or num(g(d, "Extent"))
    if ex and ex.split(".")[0] not in row:
        note(f"extent {g(d, 'Extent')} not seen in the property list")
    if ptype.lower() not in row.lower():
        note(f"type '{ptype}' not seen in the property list")
    job.step("7 · Property added: " + row[:120], page)
    return row


def prop_details_tab(P, page, d, note):
    txt = page.evaluate(PAGE_TXT_JS).lower()
    for key in ("Property SRO", "Village"):
        v = g(d, key)
        if v and re.sub(r"[^a-z]", "", v.lower())[:6] not in re.sub(r"[^a-z]", "", txt):
            note(f"{key}: Drafting says '{v}', TNREGINET copied something else - check")
    pt = g(d, "Patta No")
    if pt:
        r = page.evaluate(FILL_AFTER_JS, ["^Patta No\\.?$", pt, ""])
        if str(r).startswith("NO"):
            note(f"patta no. box not found ({r})")
        else:
            page.evaluate(CLICK_AFTER_JS, ["^Patta No\\.?$", "^Add$"])
            P.wait(2); m = P.popup_ok()
            if m and re.search(r"exist|invalid|not", m, re.I):
                note(f"patta {pt}: TNREGINET said '{m[:80]}'")


def norm_sv(x):
    """'140/2A2', '140 - 2a2', '0140/2A2' -> '1402a2' (to match TNREGINET's survey list)."""
    return re.sub(r"[^0-9a-z]", "", re.sub(r"(?<![0-9])0+(?=[0-9])", "", str(x or "").lower()))


def wait_options(P, page, pat, secs=30):
    """The real options of a dropdown (TNREGINET loads them late); [] after `secs`."""
    end, opts = time.time() + secs, []
    while time.time() < end:
        opts = [o for o in (page.evaluate(OPTIONS_AFTER_JS, pat) or []) if o and not re.match(r"^\W*select\W*$", o, re.I)]
        if opts:
            return opts
        P.wait(2)
    return opts


def pick_option(page, pat, opts, wants, starts=""):
    hit = next((o for o in opts if norm_sv(o) in wants and (not starts or re.match(r"^\D*0*" + re.escape(starts) + r"(\D|$)", o))), None) \
        or next((o for o in opts if norm_sv(o) in wants), None)
    if hit:
        page.evaluate(SELECT_AFTER_JS, [pat, "^" + re.escape(hit) + "$"])
    return hit


UNIT_NOW_JS = """()=>{const s=[...document.querySelectorAll('select')].find(e=>{if(!e.getClientRects().length)return false;let l=e.labels&&e.labels[0]?e.labels[0].innerText:'';
  if(!l){let p=e.parentElement;for(let i=0;i<3&&p&&!l;i++){const x=p.querySelector('label');if(x)l=x.innerText;p=p.parentElement}}return /Unit of Measurement/i.test(l)});
  if(!s)return null;const o=s.options[s.selectedIndex];return [o?o.text.trim():'',!!s.disabled]}"""

LAND_CLASS = {"punjai": r"punj|புன்|dry", "nanjai": r"nanj|நன்|wet", "manavari": r"manav|மானா", "natham": r"natham|நத்த"}


# extent boxes after a label: one box (Sq.ft, Cent…) or two (Acres + Cents, Hectare + Ares) when that unit is chosen
EXTENT_BOXES_JS = "([pat,vals])=>{" + VIS_JS + r"""const t=textEl(new RegExp(pat,'i'));if(!t)return 'NOTEXT';
  const ins=[...document.querySelectorAll('input[type=text],input:not([type]),input[type=number]')].filter(e=>vis(e)&&!e.readOnly&&(t.compareDocumentPosition(e)&Node.DOCUMENT_POSITION_FOLLOWING));
  if(!vals)return ins.slice(0,2).map(e=>{let l='';let p=e.parentElement;for(let i=0;i<3&&p&&!l;i++,p=p.parentElement){const x=[...p.querySelectorAll('label,span,div,p')].find(z=>z!==t&&vis(z)&&/^(acres?|cents?|hectares?|ares?|square\s*(feet|met(er|re)s?))$/i.test((z.innerText||'').trim()));if(x)l=x.innerText.trim()}return l});
  const take=ins.slice(0,vals.length);take.forEach((e,i)=>{e.focus();e.value=vals[i];fire(e)});return take.map(e=>e.id||e.name||'ok').join('|')}"""


def fill_extent(page, pat, v, u, note):
    """Type an extent already in unit u; for Acre (Acres + Cents boxes) or Hectare (Hectare + Ares) split it into the two boxes."""
    subs = [x.lower() for x in (page.evaluate(EXTENT_BOXES_JS, [pat, None]) or []) if isinstance(x, str)]
    k = unit_key(u)
    two = len(subs) == 2 and subs[0] and subs[1] and ((k == "acre" and subs[0].startswith("acre") and subs[1].startswith("cent")) or
                                                     (k == "hectare" and subs[0].startswith("hect") and subs[1].startswith("are")))
    if two:
        f = float(v or 0)
        big = int(f + 1e-9)
        small = round((f - big) * 100, 2)
        vals = [str(big), ("%.2f" % small).rstrip("0").rstrip(".") or "0"]
        page.evaluate(EXTENT_BOXES_JS, [pat, vals])
        note(f"{pat.strip('^')}: {v} {u} typed as {vals[0]} {subs[0].title()} + {vals[1]} {subs[1].title()}")
        return vals
    page.evaluate(FILL_AFTER_JS, [pat, v, ""])
    return [v]


def survey_shot(page, job, i, when):
    """Screenshot of TNREGINET's survey screen, kept with the case (docs/survey/<job>/...)."""
    try:
        path = f"survey/{job.id}/{i + 1}_{when}.jpg"
        HTTP.post(f"{SB}/storage/v1/object/docs/{path}", headers={**AUTH, "Content-Type": "image/jpeg", "x-upsert": "true"},
                  data=page.screenshot(type="jpeg", quality=60, full_page=True), timeout=60)
        cid = job.d.get("case") or job.d.get("caseId") or ""
        if cid:
            c = kv_get("cases", cid) or {}
            kv_merge("cases", cid, {"surveyShots": ((c.get("surveyShots") or []) + [{"at": now(), "job": job.id, "n": i + 1, "when": when, "url": "sb:" + path}])[-12:]})
        return "sb:" + path
    except Exception as e:
        log("survey shot failed", e)
        return ""


def survey_ok(job):
    cid = job.d.get("case") or job.d.get("caseId") or ""
    try:
        c = kv_get("cases", cid) or {}
        return any(q.get("kind") == "survey" and str(q.get("answer") or "").startswith("Looks right") for q in (c.get("robotQs") or []))
    except Exception:
        return False


def survey_question(job, q, shot, options=None, field=None):
    """Stop and ask the office on the case (Drafting → Robot questions), with the screenshot."""
    cid = job.d.get("case") or job.d.get("caseId") or ""
    if not cid:
        return
    try:
        c = kv_get("cases", cid) or {}
        qs = (c.get("robotQs") or []) + [{"id": str(uuid.uuid4()), "q": q[:400], "options": (options or ["Looks right - go on", "Wrong - I will correct Drafting"])[:30],
                                           "hint": "From the robot, on TNREGINET's survey screen", "src": "TNREGINET survey screen", "shot": shot,
                                           "kind": "survey" if not field else "survey-pick", **({"field": field} if field else {}), "at": now()}]
        kv_merge("cases", cid, {"robotQs": qs})
    except Exception as e:
        log("survey question failed", e)


def survey_tab(P, job, page, d, ptype, note):
    for _ in range(6):  # old survey / old survey number rows copied from the linked deed: delete, then add as per the current patta
        gone = page.evaluate(DEL_TABLE_ROW_JS, "Mortgaged Survey Extent|Survey No")
        if not gone:
            break
        P.wait(1.5); P.popup_ok(); wait_loader(page); P.wait(1.5)
        note(f"old survey row from the linked deed deleted ({gone[:70]})")
    cu = u = case_unit(d)
    r = label_select(page, r"Unit of Measurement", unit_re(u)) if u else "skip"
    P.wait(1)
    now_u = page.evaluate(UNIT_NOW_JS) or None
    if now_u and now_u[0] and unit_key(now_u[0]) and unit_key(now_u[0]) != unit_key(u):
        note(f"TNREGINET's unit is '{now_u[0]}'{' (locked)' if now_u[1] else ''} - our unit is {u or '-'}; every extent typed in {now_u[0]}")
        u = UNIT_LABEL.get(unit_key(now_u[0]), now_u[0])
    elif u and not str(r).startswith("ok"):
        note(f"unit '{u}' not set ({str(r)[:60]})")
    P.wait(1.5)
    pre = page.evaluate(TABLE_TEXT_JS, "Survey No|Extent")
    lt = g(d, "Land type") or ("Rural" if "Agri" in ptype else "")
    if not lt:
        lt = "Natham"
        note("Urban/Natham/Rural empty in Drafting - Natham used; check")
    if not page.evaluate(RADIO_JS, "^" + lt + "$"):
        note(f"'{lt}' button not found")
    P.wait(2.5); wait_loader(page)
    chosen = page.evaluate("""(want)=>{const r=[...document.querySelectorAll('input[type=radio]')].filter(e=>e.getClientRects().length&&e.checked);
      const lab=e=>((e.labels&&e.labels[0]&&e.labels[0].innerText)||(e.nextSibling&&e.nextSibling.textContent)||(e.parentElement&&e.parentElement.innerText)||e.value||'').trim();
      return r.map(lab).find(t=>/^(urban|natham|rural)$/i.test(t))||''}""", lt)
    if chosen.lower() != lt.lower():
        page.evaluate(RADIO_JS, "^" + lt + "$"); P.wait(2.5); wait_loader(page)
        note(f"Urban/Natham/Rural: TNREGINET showed '{chosen or 'none'}', set to {lt} from Drafting")
    if lt.lower() == "urban":
        for k in ("Ward", "Block"):
            v = g(d, k)
            if v:
                r = page.evaluate(SELECT_AFTER_JS, [f"^{k}\\b", f"^0*{re.escape(v)}\\b|\\b{re.escape(v)}$"])
                if not str(r).startswith("ok"):
                    note(f"{k} {v} not set ({str(r)[:80]})")
                P.wait(2)
    sv = surveys_of(d)
    if not sv:
        note("no new survey number in Drafting - survey left for you")
        return
    linked = len(sv) > 1
    page.evaluate(RADIO_JS, "^Linked Survey$" if linked else "^Single Survey$")
    P.wait(2)
    old = [x.strip() for x in re.split(r"[,;\n]+", g(d, "Old survey numbers")) if x.strip()]
    for i, (s, sd, ext) in enumerate(sv):
        job.step(f"7 · Survey {s}/{sd}", page)
        told = g(d, "TNREGINET survey option")
        # TNREGINET shows either one "Survey No./Sub Division" list (140/2A2) or a "Survey No." list (42) and then a "Sub Division No." list (28)
        sep = page.evaluate("()=>[...document.querySelectorAll('label,span,div')].some(e=>e.getClientRects().length&&/^Survey No\\.?\\s*\\*?$/i.test((e.innerText||'').trim()))")
        pat = "^Survey No\\.?\\s*\\*?$" if sep else "^Survey No\\.?/Sub Divi?si?on"
        opts = wait_options(P, page, pat, 30)
        hit = pick_option(page, pat, opts, {x for x in (norm_sv(f"{s}/{sd}" if sd else s), norm_sv(told)) if x}, s) if opts else None
        stage, sub_opts = "survey", []
        if not hit and opts and sd:  # survey number alone, then its sub-division
            hit = pick_option(page, pat, opts, {norm_sv(s)}, s)
            if hit:
                P.wait(2.5); wait_loader(page)
                stage = "sub-division"
                sub_opts = wait_options(P, page, "^Sub Divi?si?on", 20)
                hit = pick_option(page, "^Sub Divi?si?on", sub_opts, {x for x in (norm_sv(sd), norm_sv(told.split("/")[-1]) if told else "") if x}) if sub_opts else None
        if not hit:
            lst = sub_opts if stage == "sub-division" else opts
            shot = survey_shot(page, job, i, "nolist" if not lst else "nomatch")
            why = (f"the {stage} list stayed empty" if not lst else
                   f"{s}/{sd}: {'sub-division ' + sd if stage == 'sub-division' else 'survey'} not in TNREGINET's {stage} list ({len(lst)} options: {', '.join(lst[:15])}{'…' if len(lst) > 15 else ''})")
            survey_question(job, f"Survey screen: {why}. Pick the right one from TNREGINET's list (or type it), then start the TP again.", shot,
                            options=lst, field="TNREGINET survey option")
            raise Stop(f"Survey {s}/{sd}: {why}. A robot question with the screenshot is on the case; the draft is saved.")
        P.wait(2.5); wait_loader(page)
        # Land Type (mandatory on TNREGINET): from Drafting's Land classification, else the only option, else ask
        lopts = wait_options(P, page, "^Land Type", 15)
        if lopts:
            want = g(d, "Land classification")
            rx = LAND_CLASS.get(nz(want), re.escape(want)) if want else ""
            hit = next((o for o in lopts if rx and re.search(rx, o, re.I)), None) or (lopts[0] if len(lopts) == 1 else None)
            if hit:
                page.evaluate(SELECT_AFTER_JS, ["^Land Type", "^" + re.escape(hit) + "$"])
            else:
                shot = survey_shot(page, job, i, "landtype")
                survey_question(job, f"Survey screen: Land Type is mandatory - which one is this land (patta: punjai / nanjai …)? TNREGINET offers: {', '.join(lopts[:15])}",
                                shot, options=lopts, field="Land classification")
                raise Stop("Land Type: not in Drafting. A robot question with TNREGINET's choices is on the case; the draft is saved.")
        P.wait(2)
        st = g(d, "Street name")
        if st and i == 0:
            opts = page.evaluate(OPTIONS_AFTER_JS, "^Street Name$") or []
            hit = next((o for o in opts if nz(st)[:6] and nz(st)[:6] in nz(o)), None)
            if hit:
                page.evaluate(SELECT_AFTER_JS, ["^Street Name$", "^" + re.escape(hit) + "$"])
        if i == 0:
            for o in old:
                for v in ([o, strip_part(o)] if PART_WORD.search(o) else [o]):
                    page.evaluate(FILL_AFTER_JS, ["^Old Survey No\\.?$", v, ""])
                    page.evaluate(CLICK_AFTER_JS, ["^Old Survey No\\.?$", "^Add Old Survey Number$"])
                    P.wait(1.5); P.popup_ok()
                    if nz(v) in nz(page.evaluate(TABLE_TEXT_JS, "Old Survey No")):
                        if v != o:
                            note(f"old survey '{o}' was not accepted with 'Part'; added as {v} (the remarks keep 'Part')")
                        break
            have = page.evaluate(TABLE_TEXT_JS, "Old Survey No")
            miss = [o for o in old if nz(o) not in nz(have) and nz(strip_part(o)) not in nz(have)]
            if miss:
                note("old survey no. not added: " + ", ".join(miss))
        if "Agri" in ptype and g(d, "Total survey extent"):
            raw = g(d, "Total survey extent")
            raw = raw if unit_key(raw) or re.search(r"\d\s*[-–]\s*\d", raw) else (f"{num(raw)} {cu}" if cu else raw)
            tv = area_in(raw, u) if u else ""
            if not tv:
                tv = num(raw)
                note(f"total survey extent '{raw}': unit not clear - typed {tv} as it is; check it is in {u or 'the chosen unit'}")
            elif unit_key(raw) != unit_key(u):
                note(f"total survey extent {raw} typed as {tv} {u} (same unit as the mortgaged extent)")
            fill_extent(page, "^Total Survey Extent", tv, u, note)
        src = ext or (g(d, "Extent") if i == 0 else "")
        src = src if unit_key(src) or not cu else f"{num(src)} {cu}"
        e = (area_in(src, u) if u else "") or num(src)
        if e and u and unit_key(src) and unit_key(src) != unit_key(u):
            note(f"mortgaged extent {src} typed as {e} {u}")
        if not e:
            note(f"mortgaged extent for {s}/{sd} not in Drafting - add it by hand")
            continue
        e_typed = fill_extent(page, "^Mortgaged Survey Extent", e, u, note)
        P.wait(1)
        before = survey_shot(page, job, i, "before")
        page.evaluate(CLICK_AFTER_JS, ["^Market Value of Mortgaged", "^Add$"])
        P.wait(2.5); m = P.popup_ok()
        tbl = page.evaluate(TABLE_TEXT_JS, "Mortgaged Survey Extent")
        after = survey_shot(page, job, i, "after")
        sv_ok = f"{s}/{sd}".strip("/") in tbl or (not sd and s in tbl)
        ex_ok = bool(e) and any(x and (x in tbl) for x in [e, e.split(".")[0], *(e_typed[1:] if len(e_typed) > 1 else [])])
        if not sv_ok or not ex_ok or (m and re.search(r"invalid|not|exceed|error|greater|must", m, re.I)):
            why = ("TNREGINET said: " + m[:140]) if m else (f"survey {s}/{sd} not in the table" if not sv_ok else f"extent {e} {u} not in the table")
            if survey_ok(job):
                note(f"survey screen: {why} - you answered 'Looks right - go on' earlier, so the robot went on (screenshot kept on the case)")
                continue
            survey_question(job, f"Survey screen: {why}. We typed survey {s}/{sd}, extent {e} {u}. Table now: {tbl[:160] or '(empty)'}", after or before)
            raise Stop(f"Survey screen did not match ({why}). Screenshots and a question are on the case; the draft is saved - answer the robot question, then start again.")
    pl = g(d, "Plot No")
    if pl:
        page.evaluate(FILL_AFTER_JS, ["^Plot Number$", pl, ""])
        page.evaluate(CLICK_AFTER_JS, ["^Plot Number$", "^Add$"])
        P.wait(1.5); P.popup_ok()
    if pre:
        n0 = num(pre.split("Sq")[0][-12:]) if "Sq" in pre else ""
        if n0 and num(g(d, "Extent")) and n0 != num(g(d, "Extent")):
            note(f"the linked deed's survey row says '{pre[-60:]}', the MODT extent is {g(d, 'Extent')} (MODT used)")


def side_boxes(page):
    return page.evaluate("()=>/What is in the North/i.test(document.body.innerText)")


# MODT boundary screen: labels are cut short ("What is in the North(Stre .."); after each side's Street dropdown come the English then the Tamil box
SIDE_BOXES_JS = "([side,vals])=>{" + VIS_JS + r"""const heads=[...document.querySelectorAll('label,span,div,p,td')].filter(e=>vis(e)&&new RegExp('^What is in the\\s*'+side,'i').test(own(e)||(e.innerText||'').trim())&&(e.innerText||'').length<80);
  if(!heads.length)return 'NOTEXT';heads.sort((a,b)=>(a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING)?-1:1);const h=heads[0];
  const ins=[...document.querySelectorAll('input[type=text],input:not([type]),textarea')].filter(e=>vis(e)&&!e.readOnly&&(h.compareDocumentPosition(e)&Node.DOCUMENT_POSITION_FOLLOWING));
  const out=[];vals.forEach((v,i)=>{const e=ins[i];if(!e||v==null)return;e.focus();e.value=v;fire(e);out.push(e.id||e.name||'box'+i)});return out.join('|')}"""


def fill_side(page, s, en, ta):
    """English (and Tamil) box of one boundary side: the full label first (Deed of Receipt screens), else the box order under the cut-short label."""
    r1 = page.evaluate(FILL_AFTER_JS, [f"What is in the {s}\\? \\(In English\\)", en, ""]) if en is not None else "skip"
    r2 = page.evaluate(FILL_AFTER_JS, [f"What is in the {s}\\? \\(In Tamil\\)", ta, ""]) if ta is not None else "skip"
    if r1 in ("NOTEXT", "NOBOX") or r2 in ("NOTEXT", "NOBOX"):
        return page.evaluate(SIDE_BOXES_JS, [s, [en, ta]])
    return f"{r1}|{r2}"


def no_side(d, s):
    """A boundary box holding only a space = no boundary on that side (triangular land)."""
    v = d.get(f"Boundary {s}")
    return v not in (None, "") and not str(v).strip()


def is_triangle(d):
    sides = ("North", "South", "East", "West")
    return sum(no_side(d, s) for s in sides) == 1 and sum(bool(g(d, f"Boundary {s}")) for s in sides) == 3


def tri_remarks(en, ta):
    """Triangular land: 'triangular shaped land measuring 5.5 Cents' / 'முக்கோண வடிவமுள்ள 5.5 சென்ட் நிலம்'."""
    if en and not re.search(r"triangular", en, re.I):
        en = (re.sub(r"an extent of ", "triangular shaped land measuring ", en, count=1, flags=re.I) if re.search(r"an extent of ", en, re.I)
              else re.sub(r"(\d+(?:\.\d+)?\s*(?:cents?|acres?|sq\.?\s*f(?:ee)?t|sq\.?\s*m|hectares?))", r"triangular shaped land measuring \1", en, count=1, flags=re.I))
    if ta and "முக்கோண" not in ta:
        ta = re.sub(r"(\d+(?:\.\d+)?\s*(?:சென்ட்|செண்ட்|ஏக்கர்|சதுர\s*அடி|சதுர\s*மீட்டர்|ஹெக்டேர்))(\s*நிலம்)?", r"முக்கோண வடிவமுள்ள \1 நிலம்", ta, count=1)
    return en, ta


def boundary_tab(P, job, page, d, note):
    lens = [num(g(d, f"{s} length")) for s in ("North", "South", "East", "West")]
    bt = g(d, "Boundary type")
    if all(lens):
        calc = "Regular" if lens[0] == lens[1] and lens[2] == lens[3] else "Irregular"
        if bt and bt != calc:
            note(f"boundary type in Drafting '{bt}', measurements say {calc} - {calc} used")
        bt = calc
    if bt:
        page.evaluate(RADIO_JS, "^" + bt + "( Boundary)?$")
        P.wait(1.5)
    missing_ta = {}
    for s in ("North", "South", "East", "West"):
        if no_side(d, s):
            # no boundary on this side (triangular land): a single space in both boxes - TNREGINET then accepts the page
            fill_side(page, s, " ", " ")
            note(f"{s}: no boundary on this side - a space typed")
            continue
        en, ta = g(d, f"Boundary {s}"), g(d, f"Boundary {s} Tamil")
        has_ta = bool(ta and TAMIL_RE.search(ta))
        if en or has_ta:
            r = fill_side(page, s, en or None, ta if has_ta else None)
            if r == "NOTEXT":
                note(f"{s} boundary box not found on TNREGINET")
        if not en:
            note(f"{s} boundary empty in Drafting")
        if has_ta:
            pass
        elif en:
            missing_ta[s] = en
    if missing_ta:
        tr = english_pass(P, job, "Boundaries in Tamil", kind="tamil", texts=missing_ta)
        for s, v in tr.items():
            fill_side(page, s, None, v)
        left = [s for s in missing_ta if s not in tr]
        if left:
            note("Tamil boundary not filled: " + ", ".join(left))


def linear_tab(P, page, d, note):
    vals = {s: num(g(d, f"{s} length")) for s in ("North", "South", "East", "West")}
    if not any(vals.values()):
        note("no linear measurements in Drafting - left as copied")
        return
    page.evaluate("()=>{const b=[...document.querySelectorAll('button,input[type=button]')].find(x=>x.offsetParent&&/^Reset$/i.test((x.value||x.innerText||'').trim()));if(b)b.click()}")
    P.wait(1.5); P.popup_ok()
    for s, v in vals.items():
        if v:
            page.evaluate(FILL_AFTER_JS, [f"^{s} Measurement$", v, ""])
    P.wait(1.5)
    txt = page.evaluate("()=>document.body.innerText")
    m1, m2 = re.search(r"Mortgaged Extent Area\s*:\s*([\d.]+)", txt), re.search(r"Computed Area\s*:\s*([\d.]+)", txt)
    if m1 and m2:
        a, b = float(m1.group(1)), float(m2.group(1))
        if a and abs(a - b) > max(25, a * 0.05):
            note(f"linear measurements give {b:g}, the mortgaged extent is {a:g} (entered as the documents say)")


def remarks_text(d):
    """English and Tamil remarks in the fixed pattern; Drafting's text wins when filled."""
    en, ta = g(d, "Property remarks"), g(d, "Property remarks Tamil")
    sv = surveys_of(d)
    sn = ", ".join(f"{a}/{b}".strip("/") for a, b, _ in sv)
    old = g(d, "Old survey numbers")
    unit = g(d, "Unit") or "sq.ft"
    bld = g(d, "Building type")
    if not en:
        en = (f"{g(d, 'Village')} Village, New Survey No. {sn}" + (f" (Old Survey No. {old})" if old else "") + f", an extent of {g(d, 'Extent')}"
              + ("" if re.search(r"sq|cent|acre|hect", g(d, "Extent"), re.I) else f" {unit}") + (f", Plot No. {g(d, 'Plot No')}" if g(d, "Plot No") else "")
              + (f", together with the {bld} and all the land therein." if bld and not re.search(r"^no", bld, re.I) else ", and all the land therein."))
    if is_triangle(d):
        en, ta = tri_remarks(en, ta)
    return en, ta


def remarks_tab(P, job, page, d, note):
    en, ta = remarks_text(d)
    page.evaluate(FILL_AFTER_JS, ["^Remarks$", en, "textarea"])
    if not (ta and TAMIL_RE.search(ta)):
        tr = english_pass(P, job, "Remarks in Tamil", kind="tamil", texts={"remarks": en})
        ta = tr.get("remarks", "")
    if ta:
        page.evaluate(FILL_AFTER_JS, ["^Remarks \\(in Tamil\\)$", ta, "textarea"])
    else:
        note("Tamil remarks not filled")


INSERT_HTML_JS = r"""(html)=>{
  if(window.CKEDITOR&&CKEDITOR.instances){const k=Object.keys(CKEDITOR.instances).find(k=>{const e=CKEDITOR.instances[k];return e&&e.container&&e.container.$&&e.container.$.getClientRects().length});
    if(k){const e=CKEDITOR.instances[k];e.setData(e.getData()+html);e.updateElement&&e.updateElement();return 'ckeditor '+k}}
  if(window.tinymce&&tinymce.activeEditor){tinymce.activeEditor.setContent(tinymce.activeEditor.getContent()+html);tinymce.activeEditor.save&&tinymce.activeEditor.save();return 'tinymce'}
  const ce=[...document.querySelectorAll('[contenteditable=true]')].find(e=>e.getClientRects().length);if(ce){ce.insertAdjacentHTML('beforeend',html);ce.dispatchEvent(new Event('input',{bubbles:true}));return 'editable'}
  const ta=[...document.querySelectorAll('textarea')].filter(e=>e.getClientRects().length).pop();
  if(ta){const d=document.createElement('div');d.innerHTML=html.replace(/<\/(p|tr|div)>/g,'\n').replace(/<\/t[dh]>/g,' | ');ta.value+=d.innerText;['input','change','blur'].forEach(t=>ta.dispatchEvent(new Event(t,{bubbles:true})));return 'textarea '+ta.id}
  return ''}"""

SET_HTML_JS = r"""(html)=>{
  if(window.CKEDITOR&&CKEDITOR.instances){const k=Object.keys(CKEDITOR.instances).find(k=>{const e=CKEDITOR.instances[k];return e&&e.container&&e.container.$&&e.container.$.getClientRects().length});
    if(k){const e=CKEDITOR.instances[k];e.setData(html);e.updateElement&&e.updateElement();return 'ckeditor '+k}}
  if(window.tinymce&&tinymce.activeEditor){tinymce.activeEditor.setContent(html);tinymce.activeEditor.save&&tinymce.activeEditor.save();return 'tinymce'}
  const ce=[...document.querySelectorAll('[contenteditable=true]')].find(e=>e.getClientRects().length);if(ce){ce.innerHTML=html;ce.dispatchEvent(new Event('input',{bubbles:true}));return 'editable'}
  const ta=[...document.querySelectorAll('textarea')].filter(e=>e.getClientRects().length).pop();
  if(ta){const d=document.createElement('div');d.innerHTML=html.replace(/<\/p>/g,'\n');ta.value=d.innerText.trim();['input','change','blur'].forEach(t=>ta.dispatchEvent(new Event(t,{bubbles:true})));return 'textarea '+ta.id}
  return ''}"""


def legal_html(text):
    """Company legal conditions as the Word copy: one paragraph per block, justified, a blank line between, no numbers."""
    import html as _h
    paras = [re.sub(r"\s+", " ", re.sub(r"^\s*(?:[-–•]|\d+[.)])\s*", "", x)).strip() for x in re.split(r"\n\s*\n", (text or "").strip())]
    paras = [x for x in paras if x]
    return "<p>&nbsp;</p>".join(f'<p style="text-align:justify">{_h.escape(x)}</p>' for x in paras)


def company_legal(case):
    try:
        lists = kv_get("config", "lists") or {}
        co = (((lists.get("profiles") or {}).get(case.get("bank") or "") or {}).get("company") or {})
        return legal_html(co.get("modt_legal") or "")
    except Exception as e:
        log("company legal conditions:", e)
        return ""


EDITOR_TEXT_JS = r"""()=>{if(window.CKEDITOR&&CKEDITOR.instances){const k=Object.keys(CKEDITOR.instances).find(k=>{const e=CKEDITOR.instances[k];return e&&e.container&&e.container.$&&e.container.$.getClientRects().length});
  if(k){const d=document.createElement('div');d.innerHTML=CKEDITOR.instances[k].getData();return d.innerText}}
  if(window.tinymce&&tinymce.activeEditor)return tinymce.activeEditor.getContent({format:'text'});
  const ce=[...document.querySelectorAll('[contenteditable=true]')].find(e=>e.getClientRects().length);if(ce)return ce.innerText;
  const ta=[...document.querySelectorAll('textarea')].filter(e=>e.getClientRects().length).pop();return ta?ta.value:''}"""

# Document Valuation page: every line that names a fee and an amount
FEES_JS = r"""()=>{const out={};const add=(k,v)=>{v=String(v).replace(/[^\d.]/g,'');if(v&&!(k in out))out[k]=Number(v)};
  const re=/(stamp\s*duty|registration\s*fee|computer\s*fee|processing\s*fee|copy(ing)?\s*fee|user\s*charge|scanning\s*fee|cd\s*fee|other\s*fee|deficit|total)/i;
  document.querySelectorAll('tr').forEach(r=>{if(!r.getClientRects().length)return;const c=[...r.cells].map(x=>x.innerText.trim());
    for(let i=0;i<c.length;i++){const m=c[i].match(re);if(m){const v=c.slice(i+1).find(x=>/\d/.test(x));if(v)add(c[i].replace(/\s+/g,' ').slice(0,40),v)}}});
  if(!Object.keys(out).length){document.body.innerText.split('\n').forEach(l=>{const m=l.match(/^(.*?(stamp\s*duty|registration\s*fee|computer\s*fee|fee|total)[^\d₹]*)[₹\s:]*([\d,]+(\.\d+)?)/i);if(m)add(m[1].trim().slice(0,40),m[3])})}
  return out}"""


def lod_html(case, d, comments):
    """List of documents deposited, with its table (from the Word draft), corrections applied and checked."""
    txt = g(d, "List of documents deposited")
    read = g(case.get("tpAuto") or {}, "List of documents deposited")
    # the Word table is used unless the office changed the list in Drafting after Claude read it
    html = case.get("lodHtml") or "" if (not read or nz(read) == nz(txt)) else ""
    if not html and txt:
        rows = [l for l in txt.split("\n") if l.strip()]
        if sum(1 for l in rows if "|" in l) >= 2:
            cells = lambda l: "".join(f"<td style='border:1px solid #000;padding:2px 4px'>{x.strip()}</td>" for x in l.strip(" |").split("|"))
            html = "<table style='border-collapse:collapse' border='1'>" + "".join(f"<tr>{cells(l)}</tr>" for l in rows if "|" in l) + "</table>"
            pre = [l for l in rows if "|" not in l]
            html = "".join(f"<p>{x}</p>" for x in pre[:2]) + html
        else:
            html = "".join(f"<p>{re.sub(r'[<>&]', ' ', l)}</p>" for l in rows)
    fixed = []
    for line in g(d, "LOD corrections").split("\n"):
        m = re.match(r"\s*(.+?)\s*(?:->|→|=>)\s*(.+?)\s*(?:\((.*)\))?\s*$", line)
        if m and m.group(1) in html:
            html = html.replace(m.group(1), m.group(2))
            fixed.append(f"{m.group(1)} → {m.group(2)}" + (f" ({m.group(3)})" if m.group(3) else ""))
    if fixed:
        comments.append("List of documents corrected: " + "; ".join(fixed))
    plain = re.sub(r"<[^>]+>", " ", html)
    for n in (1, 2, 3):
        no, yr = num(g(d, f"Deed {n} Doc No")), g(d, f"Deed {n} Year")
        if no and not re.search(rf"\b0*{no}\b", plain):
            comments.append(f"List of documents: title deed {n} doc no. {no}/{yr} not found in it - check")
        elif no and yr and not re.search(rf"\b0*{no}\s*(?:/|of|dated|dt\.?)?[^\n|]{{0,40}}{yr}", plain, re.I) and yr not in plain:
            comments.append(f"List of documents: year {yr} of title deed {n} not found - check")
    pt = g(d, "Patta No")
    if pt and re.search(r"patta", plain, re.I) and not re.search(rf"\b{re.escape(pt)}\b", plain):
        comments.append(f"List of documents: patta no. {pt} (revenue document) not in it - check")
    return html


def loan_check(d, case, comments):
    a = {"MODT draft": num(g(d, "Loan amount")), "sanction letter": num(g(d, "Sanction loan amount")), "portal (new case)": num(case.get("loanAmount"))}
    have = {k: v for k, v in a.items() if v}
    if len({float(v) for v in have.values()}) > 1:
        used = next(k for k in a if a[k])
        comments.append("Loan amount differs: " + ", ".join(f"{k} {v}" for k, v in have.items()) + f" - the {used} amount was used")
    return a["MODT draft"] or a["sanction letter"] or a["portal (new case)"]


def modt_plan(case, d, folder, P, comments):
    """MODT uploads: ID of every title holder and both witnesses, one property image, title deed colour copy, EC + patta + others."""
    lists = kv_get("config", "lists") or {}

    def fetch(f, name):
        if not f or not f.get("url"):
            return None
        ext = (re.search(r"\.(pdf|jpe?g|png)$", f.get("name") or "", re.I) or [".pdf"])[0].lower()
        try:
            return download(f["url"], os.path.join(folder, name + ext))
        except Exception as e:
            P.warn.append(f"{name} download failed: {e}")
            return None

    docs = case.get("docs") or {}
    plan = []
    th = docs.get("th_kyc") or []
    names = {x.get("url"): x.get("name") or "" for x in (case.get("thKyc") or [])}
    holders = int(re.sub(r"\D", "", g(d, "Title holders")) or "1")
    used = set()
    for n in range(1, min(holders, 4) + 1):
        nm = g(d, f"Mortgagor {n} name")
        tok = [t for t in re.split(r"[^a-z]+", nm.lower()) if len(t) > 2]
        hit = next((x for x in th if x.get("url") not in used and tok and all(t in names.get(x.get("url"), "").lower() for t in tok)), None) or \
            next((x for x in th if x.get("url") not in used and tok and tok[0] in names.get(x.get("url"), "").lower()), None)
        if not hit and len(th) >= n and th[n - 1].get("url") not in used:
            hit = th[n - 1]
            if names:
                comments.append(f"Title holder {n} ({nm}): no Aadhaar file with this name - file {n} used, check")
        if hit:
            used.add(hit.get("url"))
        f = fetch(hit, f"HOLDER{n}_KYC") if hit else None
        if not f:
            comments.append(f"Title holder {n} ({nm}) Aadhaar not uploaded in the case - not uploaded")
        plan.append(("Identification", nm, f, f"Title holder {n} ID", f"HOLDER{n}"))
    cw = case.get("caseWit") or {}
    for n in (1, 2):
        nm = ((cw.get(str(n)) or cw.get(n) or {}).get("name")) or g(d, f"Witness {n}") or case.get(f"w{n}") or ""
        # the ID must be THIS witness's: a case file is used only when it is the customer's own witness (read in this case)
        # or its file name carries the witness's name; otherwise the KYC Master copy of that witness
        tok = [t for t in re.split(r"[^a-z]+", nm.lower()) if len(t) > 2]
        cfs = (docs.get(f"w{n}_kyc") or [])
        own = cw.get(str(n)) or cw.get(n)
        cf = cfs[0] if cfs and (own or (tok and tok[0] in (cfs[0].get("name") or "").lower())) else None
        f = fetch(cf, f"WITNESS{n}_KYC") if cf else None
        if not f:
            w = next((x for x in (lists.get("wit") or []) if nz(x.get("name")) == nz(nm)), None)
            f = w and fetch((w.get("kyc") or [None])[0], f"WITNESS{n}_KYC")
            if cfs and not cf:
                comments.append(f"Witness {n} is {nm}: the witness KYC uploaded in the case ({cfs[0].get('name')}) is someone else - {nm}'s KYC Master copy used")
            if not f:
                comments.append(f"Witness {n} ({nm}): no Aadhaar file for this witness - not uploaded")
        plan.append(("Identification", nm, f, f"Witness {n} ID", f"WITNESS{n}"))
    ph = (docs.get("photos") or [None])[0]
    plan.append(("Property", "re:^(?!\\s*-+\\s*select)", fetch(ph, "PROPERTY_IMAGE"), "Property image", "PROPERTY_IMAGE"))
    deeds = [fetch(x, f"TITLE_DEED_{i + 1}") for i, x in enumerate(docs.get("deed") or [])]
    deeds = [x for x in deeds if x]
    deed = None
    if deeds:
        try:
            deed = deeds[0] if len(deeds) == 1 and deeds[0].endswith(".pdf") else to_pdf(deeds, os.path.join(folder, "TITLE_DEED.pdf"))
        except Exception as e:
            comments.append(f"title deed files could not be joined ({e}); first file used")
            deed = deeds[0]
    plan.append(("Reference", "re:original", deed, "Title deed (colour copy)", "TITLE_DEED"))
    rest = [fetch(x, f"OTHER_{k}_{i + 1}") for k in ("ec", "revenue", "other") for i, x in enumerate(docs.get(k) or [])]
    rest = [x for x in rest if x]
    if not (docs.get("ec") or []):
        comments.append("EC not in the case (mandatory for MODT)")
    if not (docs.get("revenue") or []):
        comments.append("Patta not in the case (mandatory for MODT)")
    other = None
    if rest:
        try:
            other = to_pdf(rest, os.path.join(folder, "EC_PATTA_OTHERS.pdf"))
        except Exception as e:
            comments.append(f"EC / patta files could not be joined ({e})")
    plan.append(("Reference", "re:other", other, "EC + patta + others", "EC_PATTA_OTHERS"))
    return plan


def pdf_pages(path):
    try:
        ensure_pdf_tools()
        from pypdf import PdfReader
        return len(PdfReader(path).pages)
    except Exception:
        try:
            return len(re.findall(rb"/Type\s*/Page[^s]", open(path, "rb").read()))
        except Exception:
            return 0


def save_fees(job, case_id, tp, fees, comments):
    """Fees to collect: TNREGINET's fees (read before Create) + ₹100 a page above 10 pages of the print (computer fee) + service charge."""
    try:
        c = kv_get("cases", case_id) or {}
        folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files", job.id[:8])
        pdf = os.path.join(folder, tp.replace("/", "_") + ".pdf")
        pages = pdf_pages(pdf) if os.path.exists(pdf) else 0
        extra = max(0, pages - 10) * 100
        lists = kv_get("config", "lists") or {}
        prof = (lists.get("profiles") or {}).get(c.get("bank") or "") or {}
        svc = (c.get("payment") or {}).get("service") or prof.get("service") or (lists.get("pay") or {}).get("service") or 0
        items = {k: v for k, v in (fees or {}).items() if not re.search(r"total", k, re.I)}
        comp_key = next((k for k in items if re.search(r"computer", k, re.I)), None)
        gov = sum(float(v) for v in items.values())
        f = {"items": items, "tnTotal": (fees or {}).get(next((k for k in (fees or {}) if re.search(r"total", k, re.I)), ""), None),
             "pages": pages, "extraPages": max(0, pages - 10), "extraFee": extra, "computerKey": comp_key or "Computer fee",
             "govTotal": gov + extra, "service": float(svc or 0), "total": gov + extra + float(svc or 0), "at": now(), "tp": tp}
        pay = dict(c.get("payment") or {})
        pay.update(regFee=f["govTotal"], service=f["service"], total=f["total"])
        kv_merge("cases", case_id, {"fees": f, "payment": pay})
        if not items:
            comments.append("fees could not be read from TNREGINET's valuation page - enter them in Payment by hand")
        if not pages:
            comments.append("print page count not known - check the computer fee")
        return f
    except Exception as e:
        comments.append(f"fees not worked out ({str(e)[:80]})")
        return None


def modt_second_half(P, job, page, d, case, comments):
    """MODT robot part 2 (screens of 10-11 Nov): loan (checked 3 ways) → title deposit (skip) → possession (skip) →
    legal conditions (tick all, Copy, 2 lines, list of documents WITH its table) → reasons (save) → supporting details →
    preview → read fees → Create Document → uploads → print → fees with page count. Stops before payment."""
    job.step("9 · Mortgage and security details", page)
    main_next(P, page, 6)
    wait_for(P, "()=>/Loan Amount/i.test(document.body.innerText)", 20)
    amt = loan_check(d, case, comments)
    if not amt:
        raise Stop("Loan amount is empty (MODT draft, sanction letter and the case). The draft is saved on TNREGINET.")
    if not label_fill(page, r"Loan Amount", amt):
        comments.append("Loan amount box not found")
    r = label_select(page, r"Purpose of Loan", r"does not have concession")
    if not str(r).startswith("ok"):
        comments.append(f"Purpose of loan not set ({r[:80]})")
    P.wait(1)
    job.step(f"9 · Loan amount {amt}", page)
    modt_next(P, job, page, "Mortgage and security details")
    job.step("10 · Documents of title deposited (no change)", page)
    modt_next(P, job, page, "Documents of title deposited")
    job.step("11 · Possession remarks (no change)", page)
    modt_next(P, job, page, "Possession remarks")
    job.step("12 · Legal conditions", page)
    wait_for(P, "()=>/Legal Condition/i.test(document.body.innerText)", 15)
    n = page.evaluate("()=>{let n=0;[...document.querySelectorAll('input[type=checkbox]')].filter(e=>e.getClientRects().length&&!e.checked).forEach(e=>{e.click();n++});return n}")
    own = company_legal(case)
    if own:
        # the company's own conditions (Settings → company → MODT legal conditions) replace TNREGINET's copied text
        how = page.evaluate(SET_HTML_JS, own)
        if not how:
            comments.append("company legal conditions not put in (editor not found) - paste them by hand")
        P.wait(1)
    else:
        before = page.evaluate(EDITOR_TEXT_JS) or ""
        copied = click_btn_text(P, r"^copy legal condition")
        P.wait(2.5); P.popup_ok()
        after_txt = page.evaluate(EDITOR_TEXT_JS) or ""
        if not copied or len(after_txt) <= len(before):
            comments.append("legal conditions did not copy into the box - check")
    html = lod_html(case, d, comments)
    if html:
        how = page.evaluate(INSERT_HTML_JS, "<p>&nbsp;</p><p>&nbsp;</p>" + html)
        if not how:
            comments.append("List of documents not added (editor not found) - add it by hand")
    else:
        comments.append("List of documents deposited is empty in Drafting - add it by hand")
    job.step(f"12 · Legal conditions ticked ({n}), copied, list of documents added", page)
    modt_next(P, job, page, "Legal conditions")
    job.step("13 · Reasons for availing loan (save)", page)
    modt_next(P, job, page, "Reasons for availing loan")
    job.step("14 · Supporting details · place of execution", page)
    wait_for(P, "()=>/Place of Execution/i.test(document.body.innerText)", 20)
    sro = g(d, "Execution SRO", "Property SRO")
    sro_ta = g(d, "SRO Tamil")
    if not sro_ta and sro:
        sro_ta = english_pass(P, job, "SRO Tamil", kind="tamil", texts={"sro": sro}).get("sro", "")
    if not label_fill(page, r"Place of\s*Execution(?!.*Tam)", sro):
        comments.append("Place of execution (English) box not found")
    if sro_ta and not label_fill(page, r"Place of Execution in Tam", sro_ta):
        comments.append("Place of execution (Tamil) box not found")
    shown = page.evaluate("""()=>{const s=[...document.querySelectorAll('select')].find(e=>{if(!e.offsetParent)return false;let p=e.parentElement,l='';for(let i=0;i<3&&p&&!l;i++){const x=p.querySelector('label');if(x)l=x.innerText;p=p.parentElement}return /Sub Registrar Office/i.test(l)});
        return s?((s.selectedOptions[0]||{}).text||''):''}""")
    if shown and sro and nz(sro)[:6] not in nz(shown):
        comments.append(f"SRO selection on Supporting details shows '{shown}' (Drafting says '{sro}') - not changed, please check")
    modt_next(P, job, page, "Supporting details")
    job.step("15 · Document preview", page)
    P.wait(3)
    if page.evaluate("()=>{const b=document.getElementById('btnMainNext');return !!(b&&b.offsetParent)}"):
        main_next(P, page, 5)
    job.step("16 · Stamp duty and fees", page)
    P.wait(2)
    fees = page.evaluate(FEES_JS) or {}
    job.upd(fees=fees)
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
    cid = job.d.get("case", "")
    kv_merge("cases", cid, {"tpNo": tp, "tpStop": None})
    job.upd(tpNo=tp)
    job.d["tpNo"] = tp
    job.step(f"17 · TP created: {tp}", page)
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files", job.id[:8])
    os.makedirs(folder, exist_ok=True)
    done, err = [], ""
    try:
        plan = modt_plan(case, d, folder, P, comments)
        done = upload_docs(P, job, page, {}, case, comments, plan=plan)
        err = save_print(P, job, page, cid, tp)
    except Stop as e:
        err = str(e)
    if err:
        comments.append(err)
    f = save_fees(job, cid, tp, fees, comments)
    miss = tp_ready(job, cid, tp, done, err)
    if miss:
        comments.append("Stays at TP Creation, missing: " + ", ".join(miss))
    note = f"MODT TP created: {tp}. Uploaded: {', '.join(done) or 'none'}" + ("" if err else ". Print saved in the case")
    if f:
        note += f". To collect: ₹{f['total']:,.0f} (TNREGINET ₹{f['govTotal']:,.0f} incl. {f['extraPages']} extra page(s) × ₹100, service ₹{f['service']:,.0f})"
    note += ". Do biometric + payment yourself. "
    if comments:
        note += "CHECK: " + " · ".join(comments) + ". "
    if P.warn:
        note += "Warnings: " + "; ".join(P.warn[:8])
    try:
        c = kv_get("cases", cid) or {}
        if comments:
            kv_merge("cases", cid, {"tpRemarks": comments, "log": (c.get("log") or [])[-79:] + [{"at": now(), "by": "TP robot", "text": f"{tp}: please check - " + " · ".join(comments)[:900]}]})
    except Exception:
        pass
    tm = job.timing()
    job.upd(status="done", tpNo=tp, message=note.strip(), comments=comments, timing=tm)
    try:
        kv_merge("cases", cid, {"tpTiming": tm})
    except Exception:
        pass
    return tp


def tp_ready(job, case_id, tp, done, print_err):
    """After TP + uploads + print: the case goes to Biometric pending only when all three are complete; otherwise it stays
    at TP Creation and the case shows what is missing."""
    missing = []
    need = getattr(job, "upPlan", 5) or 5
    if len(done) < need:
        missing.append(f"{need - len(done)} document(s) not uploaded")
    elif not getattr(job, "upSaved", False):
        missing.append("uploads not saved on TNREGINET")
    if print_err:
        missing.append("TP print not saved")
    try:
        c = kv_get("cases", case_id) or {}
        ch = {"tpChecks": {"tp": tp, "uploaded": done, "saved": bool(getattr(job, "upSaved", False)), "print": not print_err, "missing": missing, "at": now()}}
        if not missing and (c.get("stage") or "") in ("", "Draft Pending", "TP Creation", "TP On Hold"):
            ch.update(stage="Photo/Biometric", stageAt=now(), updatedAt=now(),
                      stageLog=(c.get("stageLog") or [])[-39:] + [{"s": "Photo/Biometric", "at": now(), "by": "TP robot"}],
                      log=(c.get("log") or [])[-79:] + [{"at": now(), "by": "TP robot", "text": f"{tp}: all documents uploaded and TP print saved → Biometric pending"}])
        kv_merge("cases", case_id, ch)
    except Exception as e:
        log("tp_ready failed", e)
    return missing


def finish_dor(P, job, page, t, case, comments):
    tp = loan_to_create(P, job, page, t, case, comments)
    done = upload_docs(P, job, page, t, case, comments)
    err = save_print(P, job, page, job.d.get("case", ""), tp)
    if err:
        comments.append(err)
    miss = tp_ready(job, job.d.get("case", ""), tp, done, err)
    if miss:
        comments.append("Stays at TP Creation, missing: " + ", ".join(miss))
    note = f"TP created: {tp}. Uploaded: {', '.join(done) or 'none'}" + ("" if err else ". Print saved in the case") + ". Do biometric + payment yourself. "
    if comments:
        note += "CHECK: " + " · ".join(comments) + ". "
    if P.warn:
        note += "Warnings: " + "; ".join(P.warn[:8])
    job.step("Finished - stopped before payment", page)
    tm = job.timing()
    job.upd(status="done", tpNo=tp, message=note.strip(), comments=comments, timing=tm)
    try:
        kv_merge("cases", job.d.get("case", ""), {"tpTiming": tm})
    except Exception:
        pass
    return tp


BIO_DONE_RE = re.compile(r"biometric\s*(completed|complete|done|captured|success)|submitted|checker|verif|registered|pending\s*(for\s*)?payment|payment\s*pending|paid|e-?stamp", re.I)
BIO_PENDING_RE = re.compile(r"biometric\s*pending|pending\s*(for\s*)?biometric|awaiting\s*biometric", re.I)


def run_bio(page, job, data):
    """Daily biometric check: My Documents → the row of each TP → its status. All done → the case goes to Payment."""
    P = Portal(page, job)
    res = []
    page.evaluate("""()=>{const a=[...document.querySelectorAll('a,[title]')].find(e=>/^My Documents?$/i.test((e.title||e.textContent||'').trim()));if(a)a.click()}""")
    wait_loader(page); P.wait(3)
    job.step("My Documents opened", page)
    for cid in data.get("cases") or []:
        c = kv_get("cases", cid) or {}
        tp = c.get("tpNo") or ""
        if not tp.startswith("TP/") or c.get("stage") != "Photo/Biometric":
            continue
        no = tp.split("/")[1]
        info = None
        for _ in range(3):
            info = page.evaluate(r"""(no)=>{const tr=[...document.querySelectorAll('tr')].find(r=>r.offsetParent&&r.innerText.includes(no));if(!tr)return null;
                const tb=tr.closest('table'),hr=tb?[...tb.querySelectorAll('th')].map(x=>x.innerText.trim()):[];
                return {head:hr,cells:[...tr.children].map(x=>x.innerText.trim().replace(/\s+/g,' ')),row:tr.innerText.replace(/\s+/g,' ').trim()}}""", no)
            if info:
                break
            P.wait(3)
        if not info:
            kv_merge("cases", cid, {"bioCheck": {"at": now(), "found": False, "text": f"{tp} not in My Documents of {job.d.get('loginUser', '')}"}})
            res.append(f"{c.get('applicant', cid)}: not found")
            continue
        head, cells = info.get("head") or [], info.get("cells") or []
        st = next((cells[i] for i, h in enumerate(head) if i < len(cells) and re.search(r"status", h, re.I)), "") or info.get("row", "")
        done = bool(BIO_DONE_RE.search(st)) and not BIO_PENDING_RE.search(st)
        ch = {"bioCheck": {"at": now(), "found": True, "status": st[:200], "row": info.get("row", "")[:400], "head": head[:15], "done": done, "by": "TP robot · daily check"}}
        if done:
            b = dict(c.get("bio") or {})
            for k in ("rep", "w1", "w2"):
                b.setdefault(k, {"at": now(), "by": "TNREGINET (daily check)"})
            ch.update(bio=b, stage="Payment", stageAt=now(), updatedAt=now(),
                      stageLog=(c.get("stageLog") or [])[-39:] + [{"s": "Payment", "at": now(), "by": "TP robot · daily check"}],
                      log=(c.get("log") or [])[-79:] + [{"at": now(), "by": "TP robot", "text": f"{tp}: TNREGINET shows '{st[:80]}' → biometric complete → Payment"}])
        kv_merge("cases", cid, ch)
        res.append(f"{c.get('applicant', cid)}: {st[:60] or '?'}{' → Payment' if done else ''}")
    job.step("Biometric check finished", page)
    job.upd(status="done", message="Biometric check: " + ("; ".join(res) or "no TP to check"))


def schedule_bio(force=False):
    """07:00 India time, once a day: one check job per login that has TPs waiting for biometric."""
    ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    day = ist.strftime("%Y%m%d")
    if not force and (not BIO_AUTO or ist.hour < 7 or BIO.get("day") == day):
        return
    BIO["day"] = day
    rows = kv_list("cases", stage="Photo/Biometric")
    by = {}
    for r in rows:
        d = r["data"] or {}
        if str(d.get("tpNo") or "").startswith("TP/") and d.get("tpLogin") and not d.get("mergedInto"):
            by.setdefault(d["tpLogin"], []).append(r["id"])
    if not by:
        return
    lr = HTTP.get(f"{SB}/rest/v1/tn_logins", headers=H, params={"select": "id,username"}, timeout=20)
    lr.raise_for_status()
    ids = {x["username"]: x["id"] for x in lr.json()}
    for user, cids in by.items():
        if user not in ids:
            continue
        kv_insert_new("tp_jobs", f"bio-{day}-{re.sub(r'[^A-Za-z0-9]', '', user)[:24]}",
                      {"kind": "bio", "login": ids[user], "loginUser": user, "cases": cids, "status": "queued", "at": now(), "by": "Daily 7 am biometric check"})
    log("daily biometric check queued for", len(by), "login(s)")


BIO = {"day": ""}
BIO_AUTO = False  # daily automatic check is on hold (time to be decided); "Check now" on the dashboard still works


def handle(pw, jid, data):
    job = Job(jid, data)
    job.upd(status="starting", pickedAt=now(), message="")
    if data.get("kind") == "bio":
        case = {}
    else:
        case = kv_get("cases", data.get("case", ""))
        if not case:
            raise Stop("Case not found")
    if data.get("kind") == "dor" and part_survey(case.get("tp") or {}) and not case.get("partOk"):
        ps = part_survey(case.get("tp") or {})
        msg = (f"Survey number is '{ps}' (Part). TNREGINET will not make a TP with a Part survey number. "
               "Revenue document (patta / TSLR) is needed from the bank; enter the new survey number in Drafting and start again.")
        job.last = "Survey number is Part"
        record_stop(job, data.get("case", ""), msg)
        raise Stop(msg)
    if data.get("kind") in ("dor", "modt"):
        bad = aadhaar_problems(case, data.get("kind"))
        if bad:
            msg = ("Aadhaar number not right - TP not started (a wrong digit fails the biometric): " + "; ".join(bad) +
                   ". Correct it in Drafting / KYC Master and start again.")
            job.last = "Aadhaar check"
            record_stop(job, data.get("case", ""), msg)
            raise Stop(msg)
    if data.get("kind") == "modt" and not case.get("modtOk"):
        bad = modt_problems(case)
        if bad:
            msg = "MODT not started: " + "; ".join(bad) + ". Correct it in Drafting and start again."
            job.last = "MODT check"
            record_stop(job, data.get("case", ""), msg)
            raise Stop(msg)
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
        if data.get("kind") == "bio":
            run_bio(page, job, data)
            return
        if data.get("kind") == "modt":
            if str(case.get("tpNo") or "").startswith("TP/"):
                raise Stop(f"This case already has {case['tpNo']}. A new TP is not created.")
            if case.get("createPressedAt"):
                raise Stop("Create Document was already pressed for this case earlier but no TP number was saved. Check My Documents on TNREGINET first "
                           "(if a TP is there, use Enter TP No.; if not, press 'Allow a new TP' on the case).")
            try:
                run_modt(page, job, case)
            except Exception as e:
                job.step("Stopped here", page)
                keep_draft(page, job, getattr(job, "modt_stage", "start"))
                record_stop(job, data.get("case", ""), e)
                raise
            return
        if data.get("kind", "dor") != "dor":
            raise Stop("Only Deed of Receipt and MODT are automated so far")
        if str(case.get("tpNo") or "").startswith("TP/"):
            raise Stop(f"This case already has {case['tpNo']}. Use 'Upload documents only' - a new TP is not created.")
        if case.get("createPressedAt"):
            raise Stop("Create Document was already pressed for this case earlier but no TP number was saved. Check My Documents on TNREGINET first "
                       "(if a TP is there, use Enter TP No.; if not, press 'Allow a new TP' on the case).")
        try:
            tp = run_dor(page, job, case)
        except Exception as e:
            job.step("Stopped here", page)
            record_stop(job, data.get("case", ""), e)
            raise
        kv_merge("cases", data["case"], {"tpStop": None})
        if tp and tp.startswith("TP/"):
            kv_merge("cases", data["case"], {"tpNo": tp, "tpDraft": None, "tpDraftStage": None})
        # keep the browser open a while so a follow-up step can continue (future); then close
    finally:
        try:
            job.d["shot"] = None
            ctx.close(); browser.close()
        except Exception:
            pass


ACTIVE = set()          # (batch, login) chains running now
ACTIVE_LOCK = threading.Lock()
MAX_CHAINS = 3          # logins running side by side (Admin's Bulk Run)


def next_in_chain(batch, login):
    rows = [r for r in kv_list("tp_jobs", batch=batch, status="queued") if not r["id"].startswith("_") and (r["data"] or {}).get("batch") == batch
            and (r["data"] or {}).get("login") == login and (r["data"] or {}).get("status") == "queued"]
    rows.sort(key=lambda r: (r["data"].get("seq", 0), r["data"].get("at", "")))
    return rows[0] if rows else None


REC_JS = r"""() => {
  const vis = e => !!(e && (e.offsetParent || e.getClientRects().length));
  const lab = e => { let t = '';
    if (e.id) { const l = document.querySelector('label[for="' + CSS.escape(e.id) + '"]'); if (l) t = l.innerText; }
    if (!t) { const l = e.closest('label'); if (l) t = l.innerText; }
    if (!t) { const td = e.closest('td'); if (td && td.previousElementSibling) t = td.previousElementSibling.innerText; }
    if (!t) { const g = e.closest('.form-group,.col-md-3,.col-md-4,.col-md-6,.col-sm-6,div'); if (g) { const l = g.querySelector('label,span.control-label,.lbl'); if (l) t = l.innerText; } }
    return (t || e.placeholder || e.title || '').replace(/\s+/g, ' ').trim().slice(0, 80); };
  const F = [...document.querySelectorAll('input,select,textarea')].filter(e => vis(e) && e.type !== 'hidden').slice(0, 250).map(e => ({
    tag: e.tagName.toLowerCase(), id: e.id || '', name: e.name || '', type: e.type || '', label: lab(e),
    value: e.type === 'password' ? '' : (e.tagName === 'SELECT' ? (e.options[e.selectedIndex] || {}).text || '' : String(e.value || '')).slice(0, 80),
    checked: (e.type === 'checkbox' || e.type === 'radio') ? e.checked : undefined,
    options: e.tagName === 'SELECT' ? [...e.options].slice(0, 40).map(o => o.text.trim().slice(0, 60)) : undefined }));
  const B = [...document.querySelectorAll('button,a.btn,input[type=button],input[type=submit],[role=tab],.nav-tabs a')].filter(vis).slice(0, 120)
    .map(e => ({ id: e.id || '', text: (e.value || e.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 50), onclick: (e.getAttribute('onclick') || '').slice(0, 80) }));
  const H = [...document.querySelectorAll('h1,h2,h3,h4,h5,legend,.panel-title,.card-header,.nav-tabs .active,.nav-tabs li.active a')].filter(vis).map(e => e.innerText.replace(/\s+/g, ' ').trim().slice(0, 80)).slice(0, 30);
  const T = [...document.querySelectorAll('table')].filter(vis).slice(0, 8).map(t => ({ id: t.id || '', rows: [...t.rows].slice(0, 6).map(r => [...r.cells].map(c => c.innerText.replace(/\s+/g, ' ').trim().slice(0, 40))) }));
  const ev = window.__rec || []; window.__rec = [];
  return { url: location.href, title: document.title, heads: H, fields: F, buttons: B, tables: T, events: ev,
           sig: H.join('|') + '#' + F.map(f => f.id || f.name).join(',') + '#' + B.length };
}"""
REC_INIT = r"""(() => { window.__rec = window.__rec || [];
  const d = e => { const t = e.target.closest('button,a,input,select,textarea,li,td,span,label') || e.target; return {
    id: t.id || (t.closest('[id]') || {}).id || '', tag: t.tagName, text: (t.value && t.type !== 'password' ? t.value : t.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 60) }; };
  document.addEventListener('click', e => window.__rec.push({ at: Date.now(), ev: 'click', ...d(e) }), true);
  document.addEventListener('change', e => { const x = d(e); if (e.target.type === 'password') x.text = ''; window.__rec.push({ at: Date.now(), ev: 'change', ...x }); }, true);
})()"""


def run_record(jid, data):
    """Recording mode: a VISIBLE browser on the TP computer. The robot signs in (captcha in the portal), then the
    person does one TNREGINET entry by hand. Every page, field, button and click is saved for building the robot.
    Nothing is clicked by the robot after sign-in; it never submits or pays."""
    key = ("record", data.get("login", ""))
    job = Job(jid, data)
    steps, n = [], 0
    try:
        with sync_playwright() as pw:
            acc = login_of(data.get("login", ""))
            if not acc:
                raise Stop("Login ID not found. Add it in Clients & Staff → TNREGINET login IDs.")
            job.upd(status="starting", pickedAt=now(), message="", step="Opening a visible browser on the TP computer")
            browser = pw.chromium.launch(headless=False, args=["--start-maximized"])
            ctx = browser.new_context(viewport=None, locale="en-IN", timezone_id="Asia/Kolkata")
            ctx.add_init_script(REC_INIT)
            page = ctx.new_page()
            dialogs = []
            page.on("dialog", lambda dl: dialogs.append(dl.message[:200]))  # left open for the person to answer
            do_login(page, job, acc)
            job.upd(status="recording", step="Signed in. On the TP computer, do the whole entry by hand in the open browser. Stop before payment. Press Stop recording in the portal when done.", at=now())
            last_sig, t0, last_save = "", time.time(), 0
            while time.time() - t0 < 3600:
                d = job.fresh()
                if d.get("status") in ("cancel", "stop_rec"):
                    break
                if browser.is_connected() is False or page.is_closed():
                    pg = [x for x in ctx.pages if not x.is_closed()]
                    if not pg:
                        break
                    page = pg[-1]
                if len(ctx.pages) > 1 and ctx.pages[-1] != page and not ctx.pages[-1].is_closed():
                    page = ctx.pages[-1]
                try:
                    snap = page.evaluate(REC_JS)
                except Exception:
                    time.sleep(1.5)
                    continue
                if dialogs:
                    snap["dialogs"] = dialogs[:]
                    dialogs.clear()
                if snap["sig"] != last_sig:
                    last_sig = snap["sig"]
                    n += 1
                    try:
                        shot = page.screenshot(type="jpeg", quality=55, full_page=True)
                        HTTP.post(f"{SB}/storage/v1/object/docs/rec/{jid}/{n:03d}.jpg", headers={**AUTH, "Content-Type": "image/jpeg", "x-upsert": "true"}, data=shot, timeout=60)
                        snap["shot"] = f"rec/{jid}/{n:03d}.jpg"
                    except Exception as e:
                        snap["shotErr"] = str(e)[:100]
                    snap["n"], snap["at"] = n, now()
                    steps.append(snap)
                elif snap.get("events") or snap.get("dialogs"):
                    steps.append({"n": n, "at": now(), "events": snap.get("events", []), "dialogs": snap.get("dialogs", []), "fields": snap["fields"]})
                if steps and time.time() - last_save > 6:
                    HTTP.post(f"{SB}/storage/v1/object/docs/rec/{jid}/steps.json", headers={**AUTH, "Content-Type": "application/json", "x-upsert": "true"},
                              data=json.dumps(steps, ensure_ascii=False).encode(), timeout=60)
                    last_save = time.time()
                    job.upd(recPages=n, recFile=f"rec/{jid}/steps.json", at=now(), step=f"Recording · {n} page(s) saved · {snap.get('heads', [''])[0] if snap.get('heads') else ''}"[:200])
                time.sleep(1.5)
            if steps:
                HTTP.post(f"{SB}/storage/v1/object/docs/rec/{jid}/steps.json", headers={**AUTH, "Content-Type": "application/json", "x-upsert": "true"},
                          data=json.dumps(steps, ensure_ascii=False).encode(), timeout=60)
            job.upd(status="done", recPages=n, recFile=f"rec/{jid}/steps.json", step=f"Recording saved: {n} page(s)", at=now())
            try:
                browser.close()
            except Exception:
                pass
    except Stop as e:
        job.upd(status="cancelled" if "portal" in str(e) else "error", message=str(e), at=now())
    except Exception as e:
        traceback.print_exc()
        job.upd(status="error", message=f"Recording problem: {e}"[:300], at=now())
    finally:
        with ACTIVE_LOCK:
            ACTIVE.discard(key)


def run_chain(batch, login, first_id, first_data):
    """Bulk Run: one browser, one login (one captcha) for a whole list of cases, one after another.
    A failed case is recorded and the next one starts; nothing waits for a person except the captcha."""
    key = (batch, login)
    try:
        with sync_playwright() as pw:
            acc = login_of(login)
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="en-IN", timezone_id="Asia/Kolkata",
                                      user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
            page = ctx.new_page()
            page.on("dialog", lambda d: (DLG.append(d.message[:150]), d.accept()))
            jid, data, first = first_id, first_data, True
            try:
                while jid:
                    job = Job(jid, data)
                    try:
                        job.upd(status="starting", pickedAt=now(), message="")
                        if not acc:
                            raise Stop("Login ID not found. Add it in Clients & Staff → TNREGINET login IDs.")
                        case = kv_get("cases", data.get("case", ""))
                        if not case:
                            raise Stop("Case not found")
                        if str(case.get("tpNo") or "").startswith("TP/"):
                            raise Stop(f"This case already has {case['tpNo']} - skipped (no new TP).")
                        if first or not logged_in(page):
                            if not first:
                                page.goto("https://tnreginet.gov.in/portal/", wait_until="domcontentloaded", timeout=90000)
                            do_login(page, job, acc)
                            first = False
                        else:
                            page.goto("https://tnreginet.gov.in/portal/", wait_until="domcontentloaded", timeout=90000)
                            page.wait_for_timeout(2500)
                            to_english(page)
                        if case.get("createPressedAt"):
                            raise Stop("Create Document was already pressed for this case earlier but no TP number was saved. Check My Documents on TNREGINET first.")
                        tp = run_dor(page, job, case)
                        if tp and tp.startswith("TP/"):
                            kv_merge("cases", data["case"], {"tpNo": tp, "tpStop": None, "tpDraft": None, "tpDraftStage": None})
                    except Stop as e:
                        try:
                            job.step("Stopped here", page)
                        except Exception:
                            pass
                        record_stop(job, data.get("case", ""), e)
                        job.upd(status="cancelled" if "portal" in str(e) else "error", message=str(e), at=now())
                    except Exception as e:
                        traceback.print_exc()
                        try:
                            job.step("Stopped here", page)
                        except Exception:
                            pass
                        job.upd(status="error", message=f"Unexpected problem: {e}"[:300], at=now())
                        try:  # a broken page: start the next case from a clean page
                            page.close()
                        except Exception:
                            pass
                        page = ctx.new_page()
                        page.on("dialog", lambda d: (DLG.append(d.message[:150]), d.accept()))
                    nxt = next_in_chain(batch, login)
                    if not nxt:
                        break
                    jid, data = nxt["id"], nxt["data"]
                    kv_merge("tp_jobs", jid, {"status": "starting", "pickedAt": now()})
            finally:
                try:
                    ctx.close(); browser.close()
                except Exception:
                    pass
    except Exception as e:
        log("chain error", e)
    finally:
        with ACTIVE_LOCK:
            ACTIVE.discard(key)


SRC = "https://raw.githubusercontent.com/vgrlegal/portal/main/worker/tp_worker.py"
try:
    import hashlib
    CODE_VER = hashlib.sha1(open(os.path.abspath(__file__), "rb").read()).hexdigest()[:10]
except Exception:
    CODE_VER = "?"


def self_update():
    """Pick up new versions pushed to GitHub (systemd restarts us)."""
    try:
        r = HTTP.get(SRC, timeout=20)
        me = os.path.abspath(__file__)
        if r.ok and "def main()" in r.text and r.text != open(me, encoding="utf-8").read():
            open(me, "w", encoding="utf-8", newline="\n").write(r.text)
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


BAK = {"at": 0}
BACKUP_DIR = os.path.expanduser("~/VGR-Backup")
BACKUP_FILE = os.path.join(BACKUP_DIR, "VGR-portal-backup.json")


def daily_backup():
    """Once a day (after 11 pm, or at the first chance if the Mac was off): save settings + cases to ONE file,
    replaced each time. Never replaces a good file with one whose staff and company lists are empty."""
    import datetime
    t = datetime.datetime.now()
    today = t.strftime("%Y-%m-%d")
    old = None
    try:
        with open(BACKUP_FILE, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        pass
    last = (old or {}).get("date", "")
    yday = (t - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    if last == today or (t.hour < 23 and last >= yday):
        return
    cfg = kv_get("config", "lists") or {}
    if not (cfg.get("staff") or cfg.get("banks")) and old and ((old.get("settings") or {}).get("banks")):
        log("backup skipped: the portal settings look empty; keeping the last good backup from", last)
        return
    cases = [dict(r["data"] or {}, _id=r["id"]) for r in kv_list("cases")]
    out = {"app": "VGR Legal portal", "date": today, "made": now(), "settings": cfg, "cases": cases,
           "note": "Restore: portal > Clients & Staff > Backup > Restore settings from a backup. TNREGINET passwords are not in this file."}
    os.makedirs(BACKUP_DIR, exist_ok=True)
    tmp = BACKUP_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, BACKUP_FILE)
    log("daily backup saved:", BACKUP_FILE, len(cases), "cases")


def beat():
    for k, v in list(BUSY.items()):
        if time.time() - v.get("t", 0) > 3 * 3600:
            BUSY.pop(k, None)
    busy = [{x: v.get(x, "") for x in ("job", "case", "kind", "login", "status", "step", "since")} for v in list(BUSY.values())]
    kv_set("tp_jobs", "_worker", {"at": now(), "host": HOST, "probe": PROBE["res"], "busy": busy, "ver": CODE_VER})


def beat_loop():
    """Heartbeat on its own thread every 30 s, so the portal shows Ready / Busy even while a TP or a captcha wait is running."""
    while True:
        try:
            if PROBE.get("ok"):
                beat()
        except Exception as e:
            log("heartbeat failed", e)
        time.sleep(30)


def main():
    threading.Thread(target=beat_loop, daemon=True).start()
    log("TP worker started", "key type:", "secret" if KEY.startswith("sb_secret_") else "jwt" if KEY.startswith("eyJ") else ("EMPTY" if not KEY else "unknown"))
    with sync_playwright() as pw:
        last_beat, last_upd = 0, time.time()
        while True:
            if time.time() - last_upd > 300 and not ACTIVE:
                last_upd = time.time()
                self_update()
            try:
                if time.time() - BAK["at"] > 600:
                    BAK["at"] = time.time()
                    try:
                        daily_backup()
                    except Exception as e:
                        log("backup failed", e)
                if time.time() - PROBE["at"] > (1800 if PROBE["ok"] else 300):
                    probe()
                if not PROBE["ok"]:
                    # this computer cannot open TNREGINET: stay out of the way (another computer may take the jobs)
                    kv_set("tp_jobs", "_blocked_" + HOST, {"at": now(), "probe": PROBE["res"]})
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
                    beat()
                    last_beat = time.time()
                try:
                    schedule_bio()
                except Exception as e:
                    log("biometric schedule failed", e)
                for row in kv_list("tp_jobs", status="queued"):
                    if row["id"].startswith("_") or (row["data"] or {}).get("status") != "queued":
                        continue
                    jid, data = row["id"], row["data"]
                    if data.get("kind") == "record":
                        key = ("record", data.get("login", ""))
                        with ACTIVE_LOCK:
                            if key in ACTIVE:
                                continue
                            ACTIVE.add(key)
                        if not claim_job(jid):
                            with ACTIVE_LOCK:
                                ACTIVE.discard(key)
                            continue
                        threading.Thread(target=run_record, args=(jid, data), daemon=True).start()
                        continue
                    if data.get("batch"):
                        key = (data["batch"], data.get("login", ""))
                        with ACTIVE_LOCK:
                            if key in ACTIVE or len(ACTIVE) >= MAX_CHAINS or any(k[1] == key[1] for k in ACTIVE):
                                continue  # that chain is already running (it takes its own next case), or too many logins at once
                            ACTIVE.add(key)
                        if not claim_job(jid):
                            with ACTIVE_LOCK:
                                ACTIVE.discard(key)
                            continue
                        threading.Thread(target=run_chain, args=(data["batch"], data.get("login", ""), jid, data), daemon=True).start()
                        continue
                    with ACTIVE_LOCK:
                        if any(k[1] == data.get("login", "") for k in ACTIVE):
                            continue  # this login is busy in a Bulk Run - one TNREGINET session per login
                    if not claim_job(jid):
                        continue  # another TP computer took it
                    try:
                        handle(pw, jid, data)
                    except Stop as e:
                        BUSY.pop(jid, None)
                        kv_merge("tp_jobs", jid, {"status": "cancelled" if "portal" in str(e) else "error", "message": str(e), "at": now()})
                    except Exception as e:
                        traceback.print_exc()
                        kv_merge("tp_jobs", jid, {"status": "error", "message": f"Unexpected problem: {e}"[:300], "at": now()})
                    BUSY.pop(jid, None)
                    last_beat = 0
            except Exception as e:
                log("loop error", e)
                time.sleep(10)
            time.sleep(3)


if __name__ == "__main__":
    main()
