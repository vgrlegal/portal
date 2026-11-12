// Regression tests for portal helpers (pure functions pulled out of index.html). Run: node tests/test_portal.js
const fs=require("fs"),path=require("path"),ROOT=path.join(__dirname,"..");
const html=fs.readFileSync(path.join(ROOT,"index.html"),"utf8");
const grab=(start,end)=>{const a=html.indexOf(start);if(a<0)throw new Error("not found: "+start);const b=html.indexOf(end,a+start.length);return html.slice(a,b)};
let src="const isRD=t=>/receipt/i.test(t||'');const esc=x=>String(x);const cfg={wit:[],banks:[],profiles:{}};\n";
src+=grab("const PART_RE=","\n")+"\n"+grab("/* 12 Nov 2026 - same reader","const PART_REASON=")+"\n";
const extra=(globalThis.__EXTRA__||[]);
for(const [a,b] of [["function lodBoxOf(","\nconst OFF_TA="],["const OFF_TA=","\nasync function saveReview("]])src+=grab(a,b)+"\n";
src+=grab("const DOR_REQ=","\n")+"\n"+grab("const digits=","\n")+"\n"+grab("function dorDoneCheck(","function showDoneProblems(")+"\n";
src+=grab("const NOT_DOR_RE=","async function bankReads(){")+"\n";
src+=grab("const DOCS={","\nconst docsFor=")+"\n"+grab("const docsFor=","\n")+"\n"+grab("function missingReq(","\n}\n")+"\n}\n";
src="const flowOf=t=>/legal/i.test(t||'')?'legal':'modt';\n"+src;
src+=grab("function modtUploads(","\n  return out}")+"\n  return out}\n";
src+=grab("function surveyClean(","\nasync function readModtCase(")+"\n";
src+="const nowISO=()=>'2026-11-12T00:00:00Z',who=()=>'Test';\n"+grab("function settleQs(","\nfunction ")+"\n";
src+=grab("async function doubtQs(","\n/* files are kept")+"\n";
const MSTATES=(html.match(/MSTATES=\[[^\]]*\]/)||["MSTATES=[]"])[0];
const T={};new Function("T",`const ${MSTATES};${src};T.parseSurvey=parseSurvey;T.partSurvey=partSurvey;T.modtCoAddr=modtCoAddr;T.lodBoxOf=lodBoxOf;T.dorDoneCheck=dorDoneCheck;T.custFromSubject=custFromSubject;T.NOT_DOR_RE=NOT_DOR_RE;T.dupPlan=dupPlan;T.missingReq=missingReq;T.modtUploads=modtUploads;T.surveyClean=surveyClean;T.settleQs=settleQs;T.doubtQs=doubtQs;`)(T);
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
check("dorDoneCheck no rep/witness -> only the witness",T.dorDoneCheck({type:"Deed of Receipt"},{...full,"Rep name":"","Witness 1 name":""}).length,1);
// 12 Nov: company representative is optional (auto-selected from the saved rep) - never blocks Draft done
check("dorDoneCheck no rep at all is fine",T.dorDoneCheck({type:"Deed of Receipt"},{...full,"Rep name":"","Rep Aadhaar":""}).length,0);
check("dorDoneCheck rep Aadhaar wrong length still caught",T.dorDoneCheck({type:"Deed of Receipt"},{...full,"Rep Aadhaar":"12345"}).length,1);
check("DOR_REQ has no Rep",/const DOR_REQ=\[[^\]]*Rep /.test(html),false);
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
check("MODT read starts without the draft (title deed or bank letter)",/if\(!\(D\.deed\|\|\[\]\)\.length&&!\(D\.letter\|\|\[\]\)\.length\)\{toast\("Upload the title deed or the bank letter first/.test(html),true);
// MODT: title deed OR bank letter is enough (12 Nov)
check("MODT with bank letter only",T.missingReq("MODT",{letter:[{}],th_kyc:[{}],w1_kyc:[{}],w2_kyc:[{}]}),[]);
check("MODT with title deed only",T.missingReq("MODT",{deed:[{}],th_kyc:[{}],w1_kyc:[{}],w2_kyc:[{}]}),[]);
check("MODT with neither",T.missingReq("MODT",{th_kyc:[{}],w1_kyc:[{}],w2_kyc:[{}]}).length,1);
// MODT uploads: EC and property image optional, patta still needed (12 Nov)
{const U=T.modtUploads({docs:{th_kyc:[{name:"a"}],w1_kyc:[{name:"x"}],w2_kyc:[{name:"y"}],deed:[{name:"d"}],revenue:[{name:"p"}]},caseWit:{1:{name:"x"},2:{name:"y"}}},{"Title holders":"1"});
 const need=U.filter(x=>(x.need&&!x.v)||(x.miss||[]).length).map(x=>x.l);check("MODT uploads: no EC, no photo -> nothing missing",need,[]);
 const U2=T.modtUploads({docs:{th_kyc:[{name:"a"}],deed:[{name:"d"}]},caseWit:{1:{name:"x"},2:{name:"y"}}},{"Title holders":"1"});
 check("MODT uploads: patta still needed",U2.some(x=>(x.miss||[]).includes("patta")),true);}
// survey number cleaned after reading (Rajeswari 0088, 12 Nov)
{const d=T.surveyClean({"New survey no":"20 / 2B2 with linked 20/2C1A","Subdivision":"2B2","Linked survey numbers":"20/2C1A=1690"});
 check("surveyClean number only",[d["New survey no"],d.Subdivision,d["Linked survey numbers"]],["20","2B2","20/2C1A=1690"]);
 const e=T.surveyClean({"New survey no":"140/2A2"});check("surveyClean 140/2A2",[e["New survey no"],e.Subdivision],["140","2A2"]);
 check("surveyClean plain kept",T.surveyClean({"New survey no":"42"})["New survey no"],"42");}
check("file names: no (optional) in saved names",/DOC_NAME\[d\.k\]\|\|String\(d\.l\)\.replace/.test(html)&&/mdraft:"MODT draft \(Word copy\)"/.test(html),true);
// robot questions settle on Save (12 Nov): changed value or typed box -> answered; untouched same value stays open
{const c={robotQs:[{id:1,field:"Mortgagor 1 district",read:"Tiruvallur"},{id:2,field:"Deed 2 SRO",read:"Kundrathur"},{id:3,field:"Mortgagor 1 relative name",read:"Krishnan"}]};
 const r=T.settleQs(c,{"Mortgagor 1 district":"Chennai","Deed 2 SRO":"Kundrathur","Mortgagor 1 relative name":"Krishnan"},"draft",new Set(["rv_Deed_2_SRO"]));
 check("settleQs changed + typed settle, untouched stays",r.qs.map(q=>!!q.answer),[true,true,false]);}
if(globalThis.__MORE__)globalThis.__MORE__(T,check,html);
(async()=>{const c={draft:{"Mortgagor 1 district":"Chennai"},robotQs:[{id:1,field:"Mortgagor 1 district",read:"Tiruvallur",answer:"Chennai",store:"draft"}]};
 const out=await T.doubtQs(c,[{field:"Mortgagor 1 district",read:"Tiruvallur",options:[]},{field:"Deed 2 SRO",read:"Kundrathur",options:[]}],"draft","MODT");
 check("doubtQs: answered not asked again",out.filter(q=>!q.answer).map(q=>q.field),["Deed 2 SRO"]);
 console.log("portal tests:",fails?fails+" FAILED":"OK");process.exit(fails?1:0)})();
