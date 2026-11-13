"""Regression tests for the TP robot's pure helpers (no browser). Run: python3 tests/test_worker.py"""
import json, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "worker", "tp_worker.py"), encoding="utf-8").read()
ns = {"re": re}
exec("def g(t,*k):\n    for x in k:\n        v=t.get(x)\n        if v not in (None,''): return str(v).strip()\n    return ''\n", ns)
for start, end in (("SV_KW = ", "def run_dor("), ("def survey_list(", "def ask_captcha("), ("def surveys_of(", "\ndef ", )):
    a = src.index(start)
    exec(src[a:src.index(end, a + len(start))], ns)
fails = 0
def check(name, got, want):
    global fails
    if got != want:
        fails += 1
        print("FAIL", name, "got", repr(got), "want", repr(want))
for text, exp in json.load(open(os.path.join(ROOT, "tests", "survey_cases.json"), encoding="utf-8")):
    r = ns["parse_survey"](text)
    for k, v in exp.items():
        check(f"parse_survey({text[:40]!r}).{k}", r[k], v)
# hold rule (MD 11 Nov 2026): Part + new number = no hold
check("part_survey new inside line", ns["part_survey"]({"Survey number": "Old Sy.No.622/3 Part, New Survey No. 1025/12"}), "")
check("part_survey only part", ns["part_survey"]({"Survey number": "R.S.No.117/1 Part"}), "R.S.No.117/1 Part")
check("part_survey new in own field", ns["part_survey"]({"Survey number": "22(Part)", "New survey no": "22/4"}), "")
# Deed of Receipt: Part + new number -> the robot replaces the copied survey row (T.T.Pandian, 0058)
d = ns["dor_survey_change"]({"Survey number": "Plot No.57 (Western Portion), Old T.S.No.334/2 Part, T.S.No.57, as per TSLR Old S.No.334, 57 part, New T.S.No.57/1, Block No.20, Ward No-2", "Extent": "1200 Sq.ft"})
check("dor_survey_change new", (d or {}).get("New survey no"), "57")
check("dor_survey_change sub", (d or {}).get("Subdivision"), "1")
check("dor_survey_change ward/block/land", ((d or {}).get("Ward"), (d or {}).get("Block"), (d or {}).get("Land type")), ("2", "20", "Urban"))
check("dor_survey_change none for plain", ns["dor_survey_change"]({"Survey number": "S.F.No.123/4A"}), None)
check("dor_survey_change none for part only", ns["dor_survey_change"]({"Survey number": "R.S.No.117/1 Part"}), None)
# EC search reads every written form, new number first
check("survey_list T.S", ns["survey_list"]({"Survey number": "Old T.S.No.334/2 Part, New T.S.No.57/1"})[0], ("57", "1"))
check("survey_list Sy", ns["survey_list"]({"Survey number": "Sy.No.622/3"}), [("622", "3")])
# survey number cleaned by the robot too (Rajeswari 0088)
check("surveys_of junk survey", ns["surveys_of"]({"New survey no": "20 / 2B2 with linked 20/2C1A", "Subdivision": "2B2", "Extent": "5 Cent"})[0][:2], ("20", "2B2"))
check("surveys_of 140/2A2", ns["surveys_of"]({"New survey no": "140/2A2"})[0][:2], ("140", "2A2"))
# MODT upload: no title deed -> the bank letter goes in the title deed slot (12 Nov)
check("modt_plan bank letter fallback", 'docs.get("deed") or docs.get("letter")' in src, True)
# 12 Nov 2026: company representative optional - no rep -> skip the rep step, mortgagor presents
a = src.index("def presenter_name("); exec(src[a:src.index("\n\n\n", a)], ns)
check("presenter rep", ns["presenter_name"]({"Rep name": "R KUMAR", "Mortgagor": "A"}), "R KUMAR")
check("presenter no rep -> mortgagor", ns["presenter_name"]({"Rep name": "", "Mortgagor": "A"}), "A")
check("presenter chosen mortgagor", ns["presenter_name"]({"Rep name": "R", "Mortgagor": "A", "Presenter": "Mortgagor"}), "A")
rw = src[src.index("def rep_witness_presenter("):src.index("def add_representative(")]
check("rep step skipped when no rep", "if g(t, \"Rep name\")" in rw and "raise Stop" not in rw, True)

# 12 Nov 2026 (Rajeswari 0088): survey rows, street close match, deeds only from the legal report's security list
ns["json"] = json
for start, end in (("PART_WORD = re.compile", "def linked_check("), ("def sv_norm(", "# land area"), ("def sec_nos(", "def modt_problems(")):
    a = src.index(start)
    exec(src[a:src.index(end, a + len(start))], ns)
d88 = {"New survey no": "20", "Subdivision": "2B2", "Linked survey numbers": "20/2C1A", "Old survey numbers": "20/2, 20/2C1", "Extent": "2499 Sq.Ft."}
check("survey_rows from old fields", ns["survey_rows"](d88), [("20", "2B2", ["20/2"], ""), ("20", "2C1A", ["20/2C1"], "")])
check("survey_rows from Drafting rows", ns["survey_rows"]({**d88, "Survey rows": json.dumps([{"s": "20", "sd": "2B2", "old": ""}, {"s": "20", "sd": "2C1A", "old": "20/2, 20/2C1"}])}),
      [("20", "2B2", [], ""), ("20", "2C1A", ["20/2", "20/2C1"], "")])
check("survey_rows single", ns["survey_rows"]({"New survey no": "73", "Subdivision": "10", "Old survey numbers": "73/2, 73/2A", "Extent": "555"}), [("73", "10", ["73/2", "73/2A"], "")])
check("street Nayakkar = NAICKER", ns["street_pick"]("Kuppusamy Nayakkar Street", ["- Select -", "MAIN ROAD", "KUPPUSAMY NAICKER STREET", "KUPPAN STREET"]), "KUPPUSAMY NAICKER STREET")
check("street Naickar spelling", ns["street_pick"]("Kuppusamy Naickar St", ["KUPPUSAMY NAYAKAR STREET"]), "KUPPUSAMY NAYAKAR STREET")
check("street different name not picked", ns["street_pick"]("Kuppusamy Nayakkar Street", ["RAMASAMY NAICKER STREET", "MAIN ROAD"]), "")
dd = {"Documents for security (legal report)": "5429/2022 Release Deed", "Deed 1 Doc No": "5429", "Deed 1 Year": "2022", "Deed 2 Doc No": "20504", "Deed 2 Year": "2025", "Deed 3 Doc No": "21195", "Deed 3 Year": "2026"}
check("deed_problems flags the MODT and the Deed of Receipt", len(ns["deed_problems"](dd)), 2)
check("deed_problems ok with only the title deed", ns["deed_problems"]({**dd, "Deed 2 Doc No": "", "Deed 3 Doc No": ""}), [])
check("deed_problems top-up keeps the earlier MODT", ns["deed_problems"]({**dd, "Deed 3 Doc No": "", "Top-up loan": "Yes"}), [])
check("deed_problems no legal report", ns["deed_problems"]({"Deed 1 Doc No": "1"}), [])
check("survey select ignores CMDA list", "any(re.search(r\"\\d\", o) for o in opts)" in src and "selOk" in src, True)

# 12 Nov 2026: the schedule street goes before the village in both remarks (robot side, when Drafting's remarks lack it)
a = src.index("def street_in("); exec(src[a:src.index("\n\n\nINSERT_HTML_JS", a)], ns)
dd = {"Street name": "Kuppusamy Nayakkar Street", "Village": "Porur", "Boundary South": "Kuppusamy Nayakkar Street", "Boundary South Tamil": "குப்புசாமி நாயக்கர் தெரு"}
check("remarks_street", ns["remarks_street"](dd, "Porur Village, New Survey No. 20/2B2", "போரூர் கிராமம், புதிய சர்வே எண் 20/2B2"),
      ("Kuppusamy Nayakkar Street, Porur Village, New Survey No. 20/2B2", "குப்புசாமி நாயக்கர் தெரு, போரூர் கிராமம், புதிய சர்வே எண் 20/2B2"))
check("remarks_street no street", ns["remarks_street"]({"Village": "Porur"}, "Porur Village, x", "y"), ("Porur Village, x", "y"))

# 12 Nov 2026: MODT documents per bank; waits look every 2 s and stop as soon as the step is done
a = src.index("MODT_DEF = {"); exec(src[a:src.index("def modt_problems(", a)], ns)
L = {"profiles": {"Bank B": {"modtDocs": {"revenue": "o", "ec": "m"}}}}
r, set_ = ns["modt_rule"]({"bank": "Bank A"}, L)
check("office rule when not set", (r["revenue"], r["ec"], set_), ("m", "o", False))
r, set_ = ns["modt_rule"]({"bank": "Bank B"}, L)
check("bank rule", (r["revenue"], r["ec"], set_), ("o", "m", True))
check("missing mandatory", ns["modt_doc_missing"]({"docs": {"deed": [1], "th_kyc": [1]}}, r), ["EC"])
a = src.index("def until("); exec(src[a:src.index("\n\n\ndef radio_click(", a)], ns)
import time as _t
ns["time"] = _t
class _P:
    waited = 0
    def wait(self, s): self.waited += s
P_ = _P(); n = {"i": 0}
def _fn():
    n["i"] += 1
    return n["i"] >= 2
check("until stops as soon as done", (ns["until"](P_, _fn, 10), P_.waited), (True, 2))
P2 = _P()
check("until gives up after the longest wait", ns["until"](P2, lambda: False, 0.3, 0.1), False)
check("watch: live screen only while watched, nothing clicked", 'getattr(self.job, "watching", False)' in src and '"select": "st:data->>status,w:data->>watch"' in src, True)
# 13 Nov 2026: the survey question is not asked again and again
a = src.index("def survey_qs_keep("); exec(src[a:src.index("\n\n\ndef survey_question(", a)], ns)
Q = [{"kind": "survey", "q": "Survey screen: X", "job": "j1"}, {"kind": "survey", "q": "Old", "job": "j1", "answer": "Fixed by hand"}, {"kind": "doubt", "q": "D"}]
check("same question waiting -> not added", ns["survey_qs_keep"](Q, "Survey  screen: x", "j2"), None)
check("new run replaces old unanswered survey qs, keeps answered + others", [x["q"] for x in ns["survey_qs_keep"](Q, "Other", "j2")], ["Old", "D"])
check("same run keeps its own questions", len(ns["survey_qs_keep"](Q, "Other", "j1")), 3)
check("live / latest screen is the whole page, not only the top", src.count("full_page=False"), 0)
# 13 Nov 2026: staff Windows laptops - a VGR computer takes every TP, a partner laptop only its own
a = src.index("def job_for_me("); 
for org, data, want in (("vgr", {}, True), ("vgr", {"org": "oo"}, True), ("oo", {}, False), ("oo", {"org": ""}, False), ("oo", {"org": "oo"}, True)):
    ns["ORG"] = org; exec(src[a:src.index("\n\n\n", a)], ns)
    check(f"job_for_me {org} {data}", ns["job_for_me"](data), want)
check("startup never closes another computer's job", '((row["data"] or {}).get("host") or HOST) == HOST' in src, True)
check("backup only on the main computer", 'if ROLE == "main" and time.time() - BAK["at"] > 600' in src, True)
check("one heartbeat row per computer", 'kv_set("tp_jobs", "_w_" +' in src, True)
win = os.path.join(ROOT, "worker", "windows")
nsi = open(os.path.join(win, "installer.nsi"), encoding="utf-8").read()
check("installer: two brands, brand.txt, both exes built", ("!ifdef OO" in nsi, "brand.txt" in nsi, os.path.exists(os.path.join(win, "VGR-Legal-TP-Desk-Setup.exe")), os.path.exists(os.path.join(win, "Opinion-and-Ownership-TP-Desk-Setup.exe"))), (True, True, True, True))
for f in ("setup.ps1", "status.ps1", "uninstall.ps1"):
    t = open(os.path.join(win, f), encoding="utf-8").read()
    check(f"{f}: brand-aware, no fixed VGR path/task", ('brand.ps1' in t, 'LOCALAPPDATA "VGRLegal' in t, '-TaskName "VGR Legal TP Robot"' in t), (True, False, False))
# 13 Nov 2026: the rename to "TP Desk" put a space in a PowerShell function name ("Missing function body") - names stay one word
for f in ("brand.ps1", "setup.ps1", "status.ps1", "uninstall.ps1"):
    t = open(os.path.join(win, f), encoding="utf-8").read()
    bad = [l for l in t.splitlines() if re.match(r"\s*function\s", l) and not re.match(r"\s*function\s+[A-Za-z][\w-]*\s*[({]", l)]
    bad += [l for l in t.splitlines() if re.search(r"(^|\|\s*)Get-TP Desk|Get-Robot\b", l)]
    check(f"{f}: PowerShell function names are one word", bad, [])
# real PowerShell parse of the Windows scripts when PowerShell is on this machine (e.g. /opt/pwsh/pwsh)
import shutil, subprocess
ps = shutil.which("pwsh") or ("/opt/pwsh/pwsh" if os.path.exists("/opt/pwsh/pwsh") else "")
if ps:
    for f in ("brand.ps1", "setup.ps1", "status.ps1", "uninstall.ps1"):
        fp = os.path.join(win, f).replace("'", "''")
        out = subprocess.run([ps, "-NoProfile", "-c", f"$e=$null;[System.Management.Automation.Language.Parser]::ParseFile('{fp}',[ref]$null,[ref]$e)|Out-Null;($e|%{{$_.Message}}) -join ' | '"], capture_output=True, text=True, timeout=120).stdout.strip()
        check(f"{f}: PowerShell parses it", out, "")
# 13 Nov 2026: the MD's fee rules - the same cases are checked in the portal (tests/fee_cases.json)
import math; ns["math"] = math
a = src.index("FEE_RULE = {"); exec(src[a:src.index("def save_fees(", a)], ns)
for t in json.load(open(os.path.join(ROOT, "tests", "fee_cases.json"), encoding="utf-8")):
    check("fees: " + t["why"], ns["fee_calc"](t["c"], t.get("prof") or {})["total"], t["total"])
# 13 Nov 2026: TP print check - the case details must be on TNREGINET's print
a = src.index("def tp_print_items("); exec(src[a:src.index("def save_print(", a)], ns)
C = {"type": "Deed of Receipt", "loanNo": "FED/CHE/0012345", "tp": {"Doc No": "17280", "Year": "2023", "Survey number": "320/1A1, 320/1E1", "Extent": "2387.00"}}
items = ns["tp_print_items"](C, "TP/325099999/2026")
check("print items (Deed of Receipt)", [l for l, _ in items], ["TP number", "Loan number", "MODT document number", "Survey number", "Survey number", "Extent"])
good = ("Temporary Document Number TP/325099999/2026 Loan Account FED/CHE/0012345 linked R/Thiruporur/BOOK 1/17280/2023 " * 3) + " S.No. 320/1A1 , 320/1E1 extent 2387 sq.ft"
check("print check: all found", ns["tp_print_check"](good, items)[1], [])
check("print check: a wrong doc number is reported", ns["tp_print_check"](good.replace("17280", "17208"), items)[1], ["MODT document number 17280/2023"])
check("print check: unreadable print flags nothing", ns["tp_print_check"]("x" * 50, items), ([], []))
M = {"type": "MODT", "draft": {"Loan amount": "10,10,000", "New survey no": "20/2B2", "Extent": "2499"}}
check("print items (MODT): loan amount digits", ("Loan amount", "1010000") in ns["tp_print_items"](M, "TP/1/2026"), True)
print("worker tests:", "OK" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
