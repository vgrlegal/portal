"""Design v2 (13 Nov 2026, the MD): professional fonts + only Dashboard, MODT, Deed of Receipt, Legal Report on top;
everything else in a side menu (≡). Applied to a copy (test.html) first; after the MD's OK the same patch goes to index.html.
Usage: python3 tools/design_v2.py index.html test.html"""
import sys

src, out = sys.argv[1], sys.argv[2]
s = open(src, encoding="utf-8").read()


def rep(a, b):
    global s
    assert s.count(a) == 1, (s.count(a), a[:80])
    s = s.replace(a, b)


# 1. fonts: Merriweather for headings (law-office, readable), Inter for everything else
rep('family=Source+Serif+4:opsz,wght@8..60,700&family=IBM+Plex+Sans:wght@400;500;600&', 'family=Merriweather:wght@700&family=Inter:wght@400;500;600&')
rep('--display:"Source Serif 4",Georgia,serif; --body:"IBM Plex Sans","Noto Sans Tamil",system-ui,sans-serif;',
    '--display:"Merriweather",Georgia,serif; --body:"Inter","Noto Sans Tamil",system-ui,sans-serif;')
# 2. side menu
rep('<nav class="tabs" id="tabs" role="tablist"></nav>',
    '<button type="button" id="menuBtn" class="menubtn" aria-label="Open menu" aria-expanded="false" aria-controls="sideNav">'
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg>'
    '<span id="menuLbl">Menu</span></button><nav class="tabs" id="tabs" role="tablist"></nav>')
rep('</header>', '</header>\n<div id="sideScrim" class="side-scrim" hidden></div>'
    '<aside id="sideNav" class="side-nav" hidden aria-label="Menu"><div class="side-head"><b>Menu</b>'
    '<button type="button" id="sideClose" class="btn" aria-label="Close menu">Close</button></div><div id="sideList" class="side-list"></div></aside>')
rep('.tabs .n{', '''.menubtn{display:flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--surface);color:var(--ink);border-radius:8px;padding:0 12px;height:38px;margin:6px 8px 6px 0;font:500 14px var(--body);cursor:pointer;flex:none}
.menubtn.on{border-color:var(--accent);color:var(--accent)}
.side-scrim{position:fixed;inset:0;background:rgba(15,20,30,.35);z-index:60}
.side-nav{position:fixed;top:0;left:0;bottom:0;width:280px;max-width:86vw;background:var(--surface);border-right:1px solid var(--line);z-index:61;display:flex;flex-direction:column;padding:max(12px,env(safe-area-inset-top,0px)) 12px 12px;box-shadow:0 10px 30px rgba(0,0,0,.15)}
.side-nav[hidden],.side-scrim[hidden]{display:none}
.side-head{display:flex;align-items:center;justify-content:space-between;padding:4px 4px 12px;border-bottom:1px solid var(--line);margin-bottom:8px;font:700 16px var(--display)}
.side-list{display:flex;flex-direction:column;gap:2px;overflow-y:auto}
.side-list button{display:flex;align-items:center;justify-content:space-between;text-align:left;border:0;background:none;font:500 15px var(--body);color:var(--ink);padding:12px 12px;border-radius:8px;cursor:pointer;min-height:44px}
.side-list button:hover{background:var(--panel)}
.side-list button[aria-selected="true"]{background:var(--accent-soft);color:var(--accent)}
.tabs .n{''')
# 3. keep the 4 main tabs on top, move the rest into the menu (same buttons, same tab keys)
rep('''    (isMD?`<button role="tab" data-t="__claude" aria-selected="${tab==="__claude"}">Claude plan</button>`:"");
}''', '''    (isMD?`<button role="tab" data-t="__claude" aria-selected="${tab==="__claude"}">Claude plan</button>`:"");
  /* design v2: only Dashboard, MODT, Deed of Receipt and Legal Report stay on top; the rest go to the side menu */
  const TOP=new Set(["dash","MODT","Deed of Receipt","Legal Report"]),list=$("sideList");if(!list)return;list.innerHTML="";let cur="";
  [...$("tabs").querySelectorAll("button")].forEach(b=>{if(!TOP.has(b.dataset.t)){if(b.getAttribute("aria-selected")==="true")cur=b.textContent;list.appendChild(b)}});
  $("menuLbl").textContent=cur||"Menu";$("menuBtn").classList.toggle("on",!!cur);
}
function sideOpen(on){$("sideNav").hidden=!on;$("sideScrim").hidden=!on;$("menuBtn").setAttribute("aria-expanded",on?"true":"false");if(on){const f=$("sideList").querySelector("button");if(f)f.focus()}}
$("menuBtn").onclick=()=>sideOpen(true);$("sideClose").onclick=()=>sideOpen(false);$("sideScrim").onclick=()=>sideOpen(false);
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&!$("sideNav").hidden)sideOpen(false)});
$("sideList").onclick=e=>{const b=e.target.closest("button");if(!b)return;sideOpen(false);tab=b.dataset.t;page=0;lsSet("vgr.tab",tab);render()};''')
open(out, "w", encoding="utf-8").write(s)
print("written", out)
