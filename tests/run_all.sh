#!/bin/bash
# Run before every push: syntax of the portal + robot, and every regression test (old fixes must keep passing).
cd "$(dirname "$0")/.." || exit 1
fail=0
python3 - <<'PY' || fail=1
import re,subprocess,tempfile,os
s=open('index.html',encoding='utf-8').read()
bl=re.findall(r'<script(?![^>]*src)[^>]*>(.*?)</script>',s,re.S)
f=tempfile.NamedTemporaryFile('w',suffix='.js',delete=False);f.write("\n;\n".join(bl));f.close()
r=subprocess.run(['node','--check',f.name],capture_output=True,text=True);os.unlink(f.name)
print("portal syntax:", "OK" if r.returncode==0 else r.stderr[:400]);raise SystemExit(r.returncode)
PY
python3 -c "import ast;ast.parse(open('worker/tp_worker.py',encoding='utf-8').read());print('robot syntax: OK')" || fail=1
node tests/test_portal.js || fail=1
python3 tests/test_worker.py || fail=1
python3 -c "import playwright" 2>/dev/null && { python3 tests/test_screens.py || fail=1; }
[ -f "$MODT_HARNESS" ] && { n=$(python3 "$MODT_HARNESS" 2>&1 | grep -vc PASS); echo "robot screen tests: $n not passing"; [ "$n" = "0" ] || fail=1; }
[ $fail = 0 ] && echo "ALL TESTS PASS" || echo "SOME TESTS FAILED"
exit $fail
