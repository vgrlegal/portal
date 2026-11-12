// Regression tests for portal helpers (pure functions pulled out of index.html). Run: node tests/test_portal.js
const fs=require("fs"),path=require("path"),ROOT=path.join(__dirname,"..");
const html=fs.readFileSync(path.join(ROOT,"index.html"),"utf8");
const grab=(start,end)=>{const a=html.indexOf(start);if(a<0)throw new Error("not found: "+start);const b=html.indexOf(end,a+start.length);return html.slice(a,b)};
let src="const isRD=t=>/receipt/i.test(t||'');const esc=x=>String(x);\n";
src+=grab("const PART_RE=","\n")+"\n"+grab("/* 12 Nov 2026 - same reader","const PART_REASON=")+"\n";
const extra=(globalThis.__EXTRA__||[]);
for(const [a,b] of [["function lodBoxOf(","\nconst OFF_TA="],["const OFF_TA=","\nasync function saveReview("]])src+=grab(a,b)+"\n";
src+=grab("const DOR_REQ=","\n")+"\n"+grab("const digits=","\n")+"\n"+grab("function dorDoneCheck(","function showDoneProblems(")+"\n";
src+=grab("const NOT_DOR_RE=","async function bankReads(){")+"\n";
const MSTATES=(html.match(/MSTATES=\[[^\]]*\]/)||["MSTATES=[]"])[0];
const T={};new Function("T",`const ${MSTATES};${src};T.parseSurvey=parseSurvey;T.partSurvey=partSurvey;T.modtCoAddr=modtCoAddr;T.lodBoxOf=lodBoxOf;T.dorDoneCheck=dorDoneCheck;T.custFromSubject=custFromSubject;T.NOT_DOR_RE=NOT_DOR_RE;T.dupPlan=dupPlan;`)(T);
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
// Deed of Receipt Draft done (12 Nov): missing representative / witnesses must stop it (Kalaiyarasi 0087)
const full={"SRO":"Dusi","Doc No":"2111","Year":"2024","Company name":"X","Mortgagor":"A","Rep name":"R","Rep Aadhaar":"123456789012","Witness 1 name":"W1","Witness 1 Aadhaar":"123456789012","Witness 2 name":"W2","Witness 2 Aadhaar":"123456789012","Loan Amount":"100","Survey number":"S.No.12/3"};
check("dorDoneCheck complete",T.dorDoneCheck({type:"Deed of Receipt"},full).length,0);
check("dorDoneCheck no rep/witness",T.dorDoneCheck({type:"Deed of Receipt"},{...full,"Rep name":"","Witness 1 name":""}).length,2);
check("dorDoneCheck part only",T.dorDoneCheck({type:"Deed of Receipt"},{...full,"Survey number":"R.S.No.117/1 Part"}).length,1);
check("dorDoneCheck part+new ok",T.dorDoneCheck({type:"Deed of Receipt"},{...full,"Survey number":"Old Sy.No.622/3 Part, New Survey No. 1025/12"}).length,0);
// mail subject -> customer name, vetting / legal report mails are not cases (12 Nov)
check("custFromSubject Ugro",T.custFromSubject("Request For Online MODT Cancellation || MEENA TIFFEN CENTRE || UGTHTMS0000017545"),"MEENA TIFFEN CENTRE");
check("custFromSubject Muthoot",T.custFromSubject("Req: MFL MODT CANCELLATION REQUEST // MR Babu M // MFLSECUTAML000005488668"),"Babu M");
check("custFromSubject plain name kept",T.custFromSubject("SARANYADEVI S"),"SARANYADEVI S");
check("NOT_DOR vetting",T.NOT_DOR_RE.test("Vetting report KANAKADHARA NILAYAM/ CAS ID 5232392"),true);
check("NOT_DOR legal report",T.NOT_DOR_RE.test("Fwd: Revised Legal Report for M/s.Dinesh Poultry Farms"),true);
check("NOT_DOR receipt request is a case",T.NOT_DOR_RE.test("Request For Online MODT Cancellation || X"),false);
// duplicates by loan number (MD's rule)
const mk=(id,o)=>({id,type:"Deed of Receipt",loanNo:"25610000189",stage:"Draft Pending",createdAt:"2026-11-0"+id.slice(-1)+"T00:00:00Z",...o});
let p=T.dupPlan(mk("A1",{tp:{Mortgagor:"Mrithinjayan C","Doc No":"3112",Year:"2025"},stage:"TP Creation"}),mk("B2",{tpAuto:{Mortgagor:"C. MRITHINJAYAN","Doc No":"3112",Year:"2025"}}));
check("dupPlan same MODT twice",[p&&p.how,p&&p.keep.id],["same","A1"]);
p=T.dupPlan(mk("A1",{tp:{Mortgagor:"BABU","Doc No":"100",Year:"2024"}}),mk("B2",{tp:{Mortgagor:"Babu","Doc No":"215",Year:"2025"}}));
check("dupPlan supplementary",[p&&p.how,p&&p.keep.id],["supp","A1"]);
p=T.dupPlan(mk("A1",{tp:{Mortgagor:"Ravi","Doc No":"1",Year:"2024"}}),mk("B2",{tp:{Mortgagor:"Selvi","Doc No":"2",Year:"2024"}}));
check("dupPlan different owner warns",!!(p&&p.warn),true);
check("dupPlan different loan",T.dupPlan(mk("A1",{}),{...mk("B2",{}),loanNo:"999999999"}),null);
// MODT draft (Word copy) is optional (12 Nov): office New case and client New case
check("MODT draft not required (office)",/\{k:"mdraft"[^}]*req:1/.test(html),false);
check("MODT draft not required (client)",/"MODT":\[\["mdraft",[^\]]*,1\]/.test(html),false);
check("MODT read starts without the draft",/if\(!\(D\.deed\|\|\[\]\)\.length\)\{toast\("Upload the title deed first/.test(html),true);
if(globalThis.__MORE__)globalThis.__MORE__(T,check,html);
console.log("portal tests:",fails?fails+" FAILED":"OK");process.exit(fails?1:0);
