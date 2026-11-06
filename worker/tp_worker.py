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
from playwright.sync_api import sync_playwright

ENV = "/opt/vgr/worker.env"
for line in open(ENV) if os.path.exists(ENV) else []:
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.strip().split("=", 1)
        os.environ.setdefault(k, v)
SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
URL = "https://tnreginet.gov.in/portal/"
CAPTCHA_WAIT = 600  # seconds


def now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def log(*a):
    print(now(), *a, flush=True)


# ---------------- Supabase ----------------
def kv_get(col, id_):
    r = requests.get(f"{SB}/rest/v1/kv", headers=H, params={"collection": f"eq.{col}", "id": f"eq.{id_}", "select": "id,data"}, timeout=20)
    r.raise_for_status()
    rows = r.json()
    return rows[0]["data"] if rows else None


def kv_list(col):
    r = requests.get(f"{SB}/rest/v1/kv", headers=H, params={"collection": f"eq.{col}", "select": "id,data"}, timeout=20)
    r.raise_for_status()
    return r.json()


def kv_merge(col, id_, patch):
    r = requests.post(f"{SB}/rest/v1/rpc/kv_merge", headers=H, json={"p_collection": col, "p_id": id_, "p_patch": patch}, timeout=20)
    r.raise_for_status()


def kv_set(col, id_, data):
    h = dict(H, Prefer="resolution=merge-duplicates")
    r = requests.post(f"{SB}/rest/v1/kv", headers=h, json={"collection": col, "id": id_, "data": data, "updated_at": now()}, timeout=20)
    r.raise_for_status()


def login_of(id_):
    r = requests.get(f"{SB}/rest/v1/tn_logins", headers=H, params={"id": f"eq.{id_}", "select": "username,password,client"}, timeout=20)
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


def download(url, dest):
    """Portal stores files as 'sb:<path>' in the private 'docs' bucket."""
    if not url.startswith("sb:"):
        return None
    r = requests.get(f"{SB}/storage/v1/object/docs/{url[3:]}", headers={"apikey": KEY, "Authorization": f"Bearer {KEY}"}, timeout=60)
    r.raise_for_status()
    open(dest, "wb").write(r.content)
    return dest


# ---------------- job state ----------------
class Job:
    def __init__(self, jid, data):
        self.id, self.d = jid, data

    def upd(self, **kv):
        self.d.update(kv)
        kv_merge("tp_jobs", self.id, kv)

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
        self.d = kv_get("tp_jobs", self.id) or self.d
        return self.d

    def cancelled(self):
        return self.fresh().get("status") == "cancel"


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
        txt = self.p.evaluate("""()=>[...document.querySelectorAll('.modal.show,.modal.in,.swal2-popup,.jconfirm-box,.bootbox')]
            .filter(e=>e.offsetParent).map(e=>e.innerText.trim()).join(' | ')""")
        if txt:
            for sel in ["button:has-text('OK')", "button:has-text('Ok')", "input[value='OK']", ".swal2-confirm"]:
                b = self.p.locator(sel)
                if b.count():
                    try:
                        b.first.click(timeout=2000)
                        break
                    except Exception:
                        pass
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
        try:
            loc = self.p.locator(f"[id='{fid}']:visible").first
            loc.click()
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
    try:
        return bool(page.evaluate("()=>/log ?out|sign ?out/i.test(document.body.innerText)"))
    except Exception:
        return False


def captcha_png(page):
    img = page.locator("#captcha_login").first
    img.scroll_into_view_if_needed()
    return "data:image/png;base64," + base64.b64encode(img.screenshot()).decode()


def do_login(page, job, acc):
    job.upd(status="starting", step="Opening TNREGINET", at=now())
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)
    for attempt in range(5):
        if not page.locator("#username").first.is_visible():
            page.evaluate("()=>{const b=[...document.querySelectorAll('a,button')].find(e=>e.offsetParent&&/^\\s*sign in\\s*$/i.test(e.textContent)); if(b) b.click()}")
            page.wait_for_timeout(1500)
        page.fill("#username", acc["username"])
        page.fill("#password", acc["password"])
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
        page.fill("#txt_Captcha_login", d["captcha"].strip())
        page.evaluate("""()=>{const u=document.getElementById('username'); let f=u; for(let i=0;i<8&&f;i++){f=f.parentElement; if(f&&f.querySelector('#txt_Captcha_login')&&[...f.querySelectorAll('button,input[type=submit],input[type=button]')].some(b=>/sign in|login/i.test(b.value||b.textContent))) break;}
            const b=[...(f||document).querySelectorAll('button,input[type=submit],input[type=button]')].find(b=>/sign in|login/i.test(b.value||b.textContent)); if(b) b.click();}""")
        page.wait_for_timeout(5000)
        if logged_in(page):
            job.upd(status="filling", captchaImg="", message="", step="Signed in as " + acc["username"], at=now())
            return
        msg = page.evaluate("()=>[...document.querySelectorAll('.modal.show,.modal.in,.alert,.error,.swal2-popup,.jconfirm-box,.bootbox')].filter(e=>e.offsetParent).map(e=>e.innerText.trim()).join(' | ').slice(0,200)")
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
    P.sel("cmb_State", "^" + (state or "Tamil Nadu"), 2)
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


def run_dor(page, job, case):
    t = case.get("tp") or {}
    P = Portal(page, job)
    # 1. create & link previous document
    job.step("1 · Create document & link the MODT")
    page.goto(URL + "web?requestType=ApplicationRH&actionVal=presencelessReg", wait_until="domcontentloaded", timeout=60000)
    P.wait(3)
    if not page.locator("#deedType").count():
        raise Stop("The Create Document page did not open")
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
    P.wait(1); P.click("createDocProceed", 6)
    job.step("2 · Mortgagee (company)", page)
    P.sel("partyType", "Company")
    address(P, g(t, "Company state") or "Tamil Nadu", g(t, "Company district"), addr=g(t, "Company address"),
            addr_ta=g(t, "Company address (Tamil)"), pin=g(t, "Company pincode"))
    add_and_next(P, "Company")
    job.step("3 · Mortgagor (borrower)", page)
    P.sel("partyType", "Individual")
    person(P, t, "Mortgagor", designation=False)
    P.sel("professionId", "Others")
    P.set("professionOther", g(t, "Mortgagor profession") or ("Housewife" if g(t, "Mortgagor relation").lower().startswith("w") else "Others"))
    address(P, "Tamil Nadu", g(t, "Mortgagor district"), g(t, "Mortgagor taluk"), g(t, "Mortgagor village"),
            g(t, "Mortgagor address"), "", g(t, "Mortgagor pincode"), g(t, "Mortgagor mobile"))
    add_and_next(P, "Mortgagor")
    job.step("4 · Company representative", page)
    person(P, t, "Rep")
    address(P, g(t, "Rep state") or "Tamil Nadu", g(t, "Rep district"), addr=g(t, "Rep address"),
            addr_ta=g(t, "Rep address (Tamil)"), pin=g(t, "Rep pincode"), mobile=g(t, "Rep mobile"))
    add_and_next(P, "Representative")
    for n in (1, 2):
        if not g(t, f"Witness {n} name"):
            continue
        job.step(f"{4 + n} · Witness {n}", page)
        person(P, t, f"Witness {n}", designation=False)
        address(P, "Tamil Nadu", g(t, f"Witness {n} district"), addr=g(t, f"Witness {n} address"), pin=g(t, f"Witness {n} pincode"))
        msg = P.click("addExecutantDetailsInGrid", 3)
        if msg and not re.search(r"success|added", msg, re.I):
            raise Stop(f"Witness {n}: TNREGINET said '{msg[:160]}'")
    P.click("btnChildNext", 4)
    # Next screens (presenter, property, loan, uploads): record what TNREGINET asks for, then stop for the MD.
    fields = page.evaluate("""()=>[...document.querySelectorAll('input,select,textarea')].filter(e=>e.offsetParent&&e.type!=='hidden')
        .map(e=>{const l=e.labels&&e.labels[0]?e.labels[0].innerText.trim():(e.closest('div,td')||{}).innerText||'';return (e.id||e.name)+' | '+l.slice(0,60)}).slice(0,80)""")
    tp = page.evaluate("()=>(document.body.innerText.match(/TP\\/\\d+\\/\\d{4}/)||[''])[0]")
    job.step("Stopped before Presenter / Property / Uploads", page)
    note = "Filled up to the witnesses. " + ("Warnings: " + "; ".join(P.warn[:8]) if P.warn else "")
    job.upd(status="review", tpNo=tp, message=note.strip(), pageFields=fields)
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
    page.on("dialog", lambda d: d.accept())
    try:
        do_login(page, job, acc)
        if data.get("kind", "dor") != "dor":
            raise Stop("Only Deed of Receipt is automated so far")
        tp = run_dor(page, job, case)
        if tp:
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
        r = requests.get(SRC, timeout=20)
        me = os.path.abspath(__file__)
        if r.ok and "def main()" in r.text and r.text != open(me).read():
            open(me, "w").write(r.text)
            log("updated from GitHub, restarting")
            sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        log("update check failed", e)


def main():
    log("TP worker started")
    with sync_playwright() as pw:
        last_beat, last_upd = 0, time.time()
        while True:
            if time.time() - last_upd > 300:
                last_upd = time.time()
                self_update()
            try:
                if time.time() - last_beat > 30:
                    kv_set("tp_jobs", "_worker", {"at": now(), "host": os.uname().nodename})
                    last_beat = time.time()
                for row in kv_list("tp_jobs"):
                    if row["id"] == "_worker" or (row["data"] or {}).get("status") != "queued":
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
