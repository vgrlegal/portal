"""Regression tests for the TP robot's pure helpers (no browser). Run: python3 tests/test_worker.py"""
import json, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "worker", "tp_worker.py"), encoding="utf-8").read()
ns = {"re": re}
exec("def g(t,*k):\n    for x in k:\n        v=t.get(x)\n        if v not in (None,''): return str(v).strip()\n    return ''\n", ns)
for start, end in (("SV_KW = ", "def run_dor("), ("def survey_list(", "def ask_captcha(")):
    exec(src[src.index(start):src.index(end)], ns)
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
print("worker tests:", "OK" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
