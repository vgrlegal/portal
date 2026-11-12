"""Robot screen tests: the TNREGINET survey screen mocks in tests/screens/ (needs playwright). Run: python3 tests/test_screens.py"""
import os, sys, types
os.environ.setdefault("SUPABASE_URL", "http://x"); os.environ.setdefault("SUPABASE_SERVICE_KEY", "x")
sys.argv = ["x"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "worker"))
import tp_worker as W
from playwright.sync_api import sync_playwright

fails = 0
def check(name, got, want):
    global fails
    if got != want:
        fails += 1
        print("FAIL", name, "got", repr(got), "want", repr(want))

asked = []
W.survey_shot = lambda *a, **k: ""
W.survey_question = lambda job, q, *a, **k: asked.append(q)
W.survey_ok = lambda job: False
job = types.SimpleNamespace(id="t", d={}, step=lambda *a, **k: None, cancelled=lambda: False)
D88 = {"Land type": "Rural", "Street name": "Kuppusamy Nayakkar Street", "Unit": "Sq.ft", "Extent": "2499 Sq.Ft.",
       "New survey no": "20", "Subdivision": "2B2", "Linked survey numbers": "20/2C1A", "Old survey numbers": "20/2, 20/2C1"}
URL = "file://" + os.path.join(ROOT, "tests", "screens", "survey_linked.html")
with sync_playwright() as pw:
    b = pw.chromium.launch()
    for layout in ("combined", "sep"):
        p = b.new_page()
        p.goto(URL + ("#sep" if layout == "sep" else ""))
        P = W.Portal(p, job)
        notes = []
        try:
            W.survey_tab(P, job, p, dict(D88), "Plot with Building", notes.append)
            err = ""
        except W.Stop as e:
            err = str(e)
        check(f"{layout}: no stop", err, "")
        check(f"{layout}: linked rows added one by one", p.evaluate("()=>rowsAdded"),
              [["20/2B2", "KUPPUSAMY NAICKER STREET", "20/2"], ["20/2C1A", "KUPPUSAMY NAICKER STREET", "20/2C1"]])
        check(f"{layout}: one common extent after the rows", p.evaluate("()=>window.extAdded"), "2499")
        check(f"{layout}: Linked Survey chosen", p.evaluate("()=>document.querySelectorAll('input[name=st]')[1].checked"), True)
        check(f"{layout}: CMDA list never touched", p.evaluate("()=>appr.selectedIndex"), 0)
        check(f"{layout}: copied row deleted (slow OK pop-up) before the survey rows", p.evaluate("()=>!document.getElementById('copied')"), True)
        if layout == "sep":
            check("sep: separate boxes used after Rural + Linked", p.evaluate("()=>sep.style.display"), "")
        p.close()
    # single survey still works: survey + sub-division, extent and Add
    p = b.new_page(); p.goto(URL + "#sep"); P = W.Portal(p, job)
    d1 = {"Land type": "Rural", "Unit": "Sq.ft", "Extent": "555 Sq.ft", "New survey no": "20", "Subdivision": "2C2", "Old survey numbers": "20/2"}
    try:
        W.survey_tab(P, job, p, d1, "Plot", lambda x: None); err = ""
    except W.Stop as e:
        err = str(e)
    check("single: Single Survey chosen", p.evaluate("()=>document.querySelectorAll('input[name=st]')[0].checked"), True)
    check("single: extent added", p.evaluate("()=>window.extAdded"), "555")
    p.close()
    # the deleted row comes back after Linked Survey: deleted again, then the rows go in
    p = b.new_page(); p.goto(URL + "#sep-back"); P = W.Portal(p, job); notes = []
    try:
        W.survey_tab(P, job, p, dict(D88), "Plot with Building", notes.append); err = ""
    except W.Stop as e:
        err = str(e)
    check("row came back: deleted again and rows added", (err, len(p.evaluate("()=>rowsAdded")), any("came back" in n for n in notes)), ("", 2, True))
    p.close()
    # row that never deletes: 4 tries, then stop and ask
    p = b.new_page(); p.goto(URL + "#sep"); P = W.Portal(p, job); asked.clear()
    p.evaluate("()=>{window.askDel=()=>{}}")
    try:
        W.survey_tab(P, job, p, dict(D88), "Plot", lambda x: None); err = ""
    except W.Stop as e:
        err = str(e)
    check("undeletable row stops with a question", ("could not be deleted" in err, bool(asked)), (True, True))
    p.close()
    # wrong sub-division: a clear stop + question, never a guess
    p = b.new_page(); p.goto(URL + "#sep"); P = W.Portal(p, job); asked.clear()
    try:
        W.survey_tab(P, job, p, {**D88, "Subdivision": "9Z9"}, "Plot", lambda x: None); err = ""
    except W.Stop as e:
        err = str(e)
    check("bad sub-division stops with a question", ("9Z9" in err, bool(asked)), (True, True))
    b.close()
print("robot screen tests (survey):", "OK" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
