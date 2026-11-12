// Regression tests for portal helpers (pure functions pulled out of index.html). Run: node tests/test_portal.js
const fs=require("fs"),path=require("path"),ROOT=path.join(__dirname,"..");
const html=fs.readFileSync(path.join(ROOT,"index.html"),"utf8");
const grab=(start,end)=>{const a=html.indexOf(start);if(a<0)throw new Error("not found: "+start);const b=html.indexOf(end,a+start.length);return html.slice(a,b)};
let src="const isRD=t=>/receipt/i.test(t||'');const esc=x=>String(x);\n";
src+=grab("const PART_RE=","\n")+"\n"+grab("/* 12 Nov 2026 - same reader","const PART_REASON=")+"\n";
const extra=(globalThis.__EXTRA__||[]);
for(const [a,b] of [["function lodBoxOf(","\nconst OFF_TA="],["const OFF_TA=","\nasync function saveReview("]])src+=grab(a,b)+"\n";
const MSTATES=(html.match(/MSTATES=\[[^\]]*\]/)||["MSTATES=[]"])[0];
const T={};new Function("T",`const ${MSTATES};${src};T.parseSurvey=parseSurvey;T.partSurvey=partSurvey;T.modtCoAddr=modtCoAddr;T.lodBoxOf=lodBoxOf;`)(T);
let fails=0;const check=(n,g,w)=>{if(JSON.stringify(g)!==JSON.stringify(w)){fails++;console.log("FAIL",n,"got",JSON.stringify(g),"want",JSON.stringify(w))}};
for(const [text,exp] of JSON.parse(fs.readFileSync(path.join(__dirname,"survey_cases.json"),"utf8"))){const r=T.parseSurvey(text);for(const k in exp)check(`parseSurvey(${JSON.stringify(text.slice(0,40))}).${k}`,r[k],exp[k])}
check("partSurvey part+new",T.partSurvey({type:"Deed of Receipt",tp:{"Survey number":"Old Sy.No.622/3 Part, New Survey No. 1025/12"}}),"");
check("partSurvey part only",T.partSurvey({type:"Deed of Receipt",tp:{"Survey number":"R.S.No.117/1 Part"}}),"R.S.No.117/1 Part");
// company address (12 Nov): branch first, office word from the draft, no state / pincode inside
const co=T.modtCoAddr({"Company state":"Maharashtra","Company address":"Brindavan, Plot No. 177, CST Road, Kalina, Santacruz (East), Mumbai (Branch: L&T Finance Ltd, Tharamangalam Branch)"},"Having its Registered Office at Brindavan");
check("modtCoAddr L&T",co["Company address"],"Tharamangalam Branch, having its Registered Office at Brindavan, Plot No. 177, CST Road, Kalina, Santacruz (East), Mumbai");
check("modtCoAddr again same",T.modtCoAddr({...co},"")["Company address"],co["Company address"]);
check("modtCoAddr pin removed",T.modtCoAddr({"Company state":"Maharashtra","Company address":"Office 3, Andheri East, Mumbai, Maharashtra - 400099"},"")["Company address"],"Having its Registered Office at Office 3, Andheri East, Mumbai");
// LOD always boxed
check("lodBoxOf numbered list is a table",/<table/.test(T.lodBoxOf("1. Sale deed 1945/1995\n2. Patta 327")),true);
if(globalThis.__MORE__)globalThis.__MORE__(T,check,html);
console.log("portal tests:",fails?fails+" FAILED":"OK");process.exit(fails?1:0);
