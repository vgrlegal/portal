// Regression tests for portal helpers (pure functions pulled out of index.html). Run: node tests/test_portal.js
const fs=require("fs"),path=require("path"),ROOT=path.join(__dirname,"..");
const html=fs.readFileSync(path.join(ROOT,"index.html"),"utf8");
const grab=(start,end)=>{const a=html.indexOf(start);if(a<0)throw new Error("not found: "+start);const b=html.indexOf(end,a+start.length);return html.slice(a,b)};
let src="var window=globalThis;const isRD=t=>/receipt/i.test(t||'');const esc=x=>String(x);const cfg={wit:[],banks:[],profiles:{}};var profOf=b=>(cfg.profiles||{})[b]||null;\n";
src+=grab("const PART_RE=","\n")+"\n"+grab("/* 12 Nov 2026 - same reader","const PART_REASON=")+"\n";
const extra=(globalThis.__EXTRA__||[]);
for(const [a,b] of [["function lodBoxOf(","\nconst OFF_TA="],["const OFF_TA=","\nasync function saveReview("]])src+=grab(a,b)+"\n";
src+=grab("const DOR_REQ=","\n")+"\n"+grab("const digits=","\n")+"\n"+grab("function dorDoneCheck(","function showDoneProblems(")+"\n";
src+=grab("const NOT_DOR_RE=","async function bankReads(){")+"\n";
src+=grab("const DOCS={","\nconst docsFor=")+"\n"+grab("const docsFor=","\n")+"\n"+grab("const MODT_DOC_ROWS=","function missingReq(")+"\n"+grab("function missingReq(","\n}\n")+"\n}\n";
src="const flowOf=t=>/legal/i.test(t||'')?'legal':'modt';\n"+src;
src+=grab("function modtUploads(","\n  return out}")+"\n  return out}\n";
src+=grab("const DOC_NAME=","async function addDocs(")+"\n";
src+=grab("const AREA_SQM=","function modtFill(")+"\n";
src+=grab("/* 12 Nov 2026: Claude on / off per work","const USD_INR=")+"\n";
src+=grab("function surveyClean(","\nasync function readModtCase(")+"\n";
src+="const nowISO=()=>'2026-11-12T00:00:00Z',who=()=>'Test';\n"+grab("function settleQs(","\nfunction ")+"\n";
src+=grab("async function doubtQs(","\n/* files are kept")+"\n";
const MSTATES=(html.match(/MSTATES=\[[^\]]*\]/)||["MSTATES=[]"])[0];
const T={};new Function("T",`const ${MSTATES};${src};T.parseSurvey=parseSurvey;T.partSurvey=partSurvey;T.modtCoAddr=modtCoAddr;T.lodBoxOf=lodBoxOf;T.dorDoneCheck=dorDoneCheck;T.custFromSubject=custFromSubject;T.NOT_DOR_RE=NOT_DOR_RE;T.dupPlan=dupPlan;T.missingReq=missingReq;T.modtUploads=modtUploads;T.surveyClean=surveyClean;T.settleQs=settleQs;T.doubtQs=doubtQs;T.svRowsOf=svRowsOf;T.svFix=svFix;T.legalSecurity=legalSecurity;T.secText=secText;T.deedProblems=deedProblems;T.applySecurity=applySecurity;T.legalBlock=legalBlock;T.lodParse=lodParse;T.lodSplit=lodSplit;T.lodRowsFromText=lodRowsFromText;T.lodBuild=lodBuild;T.lodSync=lodSync;T.lodTextOf=lodTextOf;T.docNames=docNames;T.remarksStreet=remarksStreet;T.extNum=extNum;T.extFull=extFull;T.cfg=cfg;T.modtRule=modtRule;T.claudeOn=claudeOn;T.claudeAreaOf=claudeAreaOf;`)(T);
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
 // 18 Nov 2026 (the MD: same question again and again): Drafting checks, mail split and Aadhaar doubts are asked once
 {const Q={};new Function("Q",grab("const qNorm=","/* doubts become questions")+";Q.addQs=addQs;Q.qKey=qKey;Q.qNorm=qNorm")(Q);
  const L=[{id:1,field:"Boundary North",q:"Boundary North is not in the MODT draft",answer:"Road"},{id:2,key:"extent|1 Acre|0.5 Acre",q:"Mortgaged extent…",src:"Extent check"}];
  check("addQs: answered field not asked again",Q.addQs(L,[{id:3,field:"Boundary North",q:"Boundary North is not in the MODT draft"}]).length,2);
  check("addQs: waiting check with same values not added twice",Q.addQs(L,[{id:4,key:"extent|1 Acre|0.5 Acre",q:"x"}]).length,2);
  check("addQs: new values are a new question",Q.addQs(L,[{id:5,key:"extent|2 Acre|0.5 Acre",q:"x"}]).length,3);
  check("addQs: same question twice in one go added once",Q.addQs([],[{id:6,field:"Land type"},{id:7,field:"Land type"}]).length,1);
  check("addQs: no field, same words = same question",Q.addQs([{q:"Split  it?",src:"Mail"}],[{q:"split it?",src:"Mail"}]).length,1);
  check("Land type asked only when Drafting has none",/!String\(d\["Land type"\]\|\|""\)\.trim\(\)\)ch\.robotQs=addQs\(/.test(html),true);
  check("Aadhaar doubt answers remembered per card",/A\[k\]!==undefined\)c\[q\.key\]=/.test(html)&&/aadAnsPut\(add\)/.test(html),true);
  check("last answer carries the TP Desk on from the saved draft",/resumeAfterAnswers\(\{\.\.\.c,\.\.\.ch\}\)/.test(html)&&/tpDraftResume:true/.test(html),true);}
 // 18 Nov 2026: Stop Claude, waiting cases, login alert
 {const Z={};new Function("Z","var cfg={claudeOff:{}};const isRD=t=>/receipt/i.test(t||''),flowOf=t=>/legal/i.test(t||'')?'legal':'modt';"+grab("const claudeOn=","\nfunction claudeAreaOf(")+";Z.set=o=>cfg.claudeOff=o;Z.claudeOn=claudeOn;Z.claudeWait=claudeWait;Z.claudeOffNow=claudeOffNow")(Z);
  check("claudeOn: on by default",[Z.claudeOn("modt"),Z.claudeOn("dor"),Z.claudeOn("any")],[true,true,true]);
  Z.set({all:true});check("Stop Claude: everything off",[Z.claudeOn("modt"),Z.claudeOn("dor"),Z.claudeOn("any")],[false,false,false]);
  check("Stop Claude: waiting case is shown as waiting (not failed)",Z.claudeWait({type:"MODT",needsRead:true}),true);
  check("Stop Claude: a failed-read case is not 'waiting'",Z.claudeWait({type:"MODT",needsRead:true,readFailed:true}),false);
  check("Stop Claude: an error while stopped is not counted as a failure",Z.claudeOffNow({type:"Deed of Receipt"},new Error("x")),true);
  Z.set({modt:true});check("MODT off, Receipt on",[Z.claudeOn("modt"),Z.claudeOn("dor"),Z.claudeOn("any")],[false,true,true]);
  Z.set({});check("Claude on: a real error still counts",Z.claudeOffNow({type:"Deed of Receipt"},new Error("No JSON in reply")),false);
  check("Claude on: 'stopped' reply from the server does not count",Z.claudeOffNow({type:"Deed of Receipt"},new Error("Claude stopped by the Admin - all Claude reading is paused")),true);}
 {const L={};new Function("L",grab("const devName=","\nconst istTime=")+";L.devName=devName")(L);
  check("devName Windows Chrome",L.devName("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),"Windows computer · Chrome");
  check("devName iPhone Safari",L.devName("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"),"iPhone · Safari");
  check("devName Android Chrome",L.devName("Mozilla/5.0 (Linux; Android 14; SM-A146B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36"),"Android phone · Chrome");
  check("devName Mac Edge",L.devName("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 Edg/126.0"),"Mac · Edge");
  check("login alert: device tick every 30 s, new page = fresh",/sb\.rpc\("device_seen",\{p_dev:dev,p_ua:navigator\.userAgent,p_fresh:fresh\}\)/.test(html)&&/setInterval\(tick,30000\)/.test(html),true);
  check("login alert: not me signs out other devices + their sessions",/devices_sign_out",\{p_email:a\.email,p_keep:window\.__dev/.test(html)&&/signOut\(\{scope:"others"\}\)/.test(html),true);
  check("login alert: Admin-only devices card",/devCard:!isMD/.test(html),true);}
 console.log("portal tests:",fails?fails+" FAILED":"OK");process.exit(fails?1:0)})();
// 12 Nov 2026 (Rajeswari 0088): survey rows - survey and sub-division apart, each linked survey its own row, old numbers per row
{const d={"New survey no":"20 / 2B2 with linked 20/2C1A","Subdivision":"2B2","Linked survey numbers":"20/2C1A","Old survey numbers":"20/2, 20/2C1"};T.surveyClean(d);
 check("survey rows built",T.svRowsOf(d),[{s:"20",sd:"2B2",old:"20/2"},{s:"20",sd:"2C1A",old:"20/2C1"}]);
 check("survey rows -> old fields",[d["New survey no"],d.Subdivision,d["Linked survey numbers"],d["Old survey numbers"]],["20","2B2","20/2C1A","20/2, 20/2C1"]);
 const e={...d,"Survey rows":JSON.stringify([{s:"20",sd:"2B2",old:""},{s:"20",sd:"2C1A",old:"20/2, 20/2C1"}])};T.svFix(e);
 check("edited rows win",[e["Old survey numbers"],JSON.parse(e["Survey rows"])[1].old],["20/2, 20/2C1","20/2, 20/2C1"]);
 check("old number unclear -> row 1",T.svRowsOf({"New survey no":"73","Subdivision":"10","Linked survey numbers":"74/2=300","Old survey numbers":"99/1"})[0].old,"99/1");
 check("single survey one row",T.svRowsOf({"New survey no":"73","Subdivision":"10","Old survey numbers":"73/2, 73/2A"}),[{s:"73",sd:"10",old:"73/2, 73/2A"}]);}
// the legal report decides the linked deeds (marked "- MOD"); the earlier MODT and the Deed of Receipt are not linked
{const lr="III. Documents perused for scrutiny:- 1. 26.05.1962 Sale Deed Executed by Mr.Dhamotharan in favour of Mr.Govindharaj (Doc.No.1340/1962) S.No.20/2 5. 14.06.2013 Release Deed Executed by Mr.Ulaganathan to in favour of Mr.Krishnan (Doc.No. 9088 /2013) 8. 17.03.2022 Release Deed Executed by Mrs.Sivagami, Mr.Vinothkumar & Mr.Karthik to in favour of Mr.Balaji (Doc.No. 5429 /2022) - MOD S.No.20/2 , New S.No.20/2C1 10. 07.11.2025 MODT Executed by Mr.Balaji to in favour of Truhome Finance Ltd (Doc.No. 20504 /2025 ) DOCUMENTS ORIGINAL TO BE COLLECTED POST DISBURSEMENT:- 1. 17.03.2022 Release Deed (Doc.No. 5429 /2022) - MOD Original 2. 07.11.2025 MODT (Doc.No. 20504 /2025) Original 3. Registered Discharge Receipt for MODT Doc.No.20504/2025.";
 const L=T.legalSecurity(lr);check("legal report MOD deed",L.map(x=>x.no+"/"+x.yr),["5429/2022"]);check("security text",T.secText(L),"5429/2022 Release Deed");
 const d={"Deed 1 SRO":"Kunrathur","Deed 1 Doc No":"5429","Deed 1 Year":"2022","Deed 2 Doc No":"20504","Deed 2 Year":"2025","Deed 3 SRO":"Kunrathur","Deed 3 Doc No":"21195","Deed 3 Year":"2026","Documents for security (legal report)":"5429/2022 Release Deed"};
 check("wrong deeds flagged",T.deedProblems(d).length,2);
 T.applySecurity(d,L,false);check("only the title deed kept",[1,2,3].map(n=>d[`Deed ${n} Doc No`]+"/"+d[`Deed ${n} Year`]+"/"+d[`Deed ${n} SRO`]),["5429/2022/Kunrathur","//","//"]);
 check("no problems after",T.deedProblems(d),[]);
 check("top-up keeps the earlier MODT",T.deedProblems({...d,"Deed 2 Doc No":"20504","Deed 2 Year":"2025","Top-up loan":"Yes"}),[]);
 const sec2=T.legalSecurity("V. Documents for Security: 1. Sale deed dated 2.1.2019 Doc No.221/2019 2. Settlement deed Document No. 45 of 2020 VI. Certificate");check("Documents for security heading",sec2.map(x=>x.no+"/"+x.yr),["221/2019","45/2020"]);
 check("no legal report -> no rule",T.deedProblems({"Deed 1 Doc No":"1"}),[]);
 check("legal block has the rules",/Documents for security/.test(T.legalBlock("x"))&&T.legalBlock("")==="",true);}

check("login IDs: Edit and Delete buttons, password write-only",/data-tne=/.test(html)&&/function tnEdit\(/.test(html)&&/Leave empty to keep the saved password/.test(html)&&!/id="tne_pass"[^>]*value=/.test(html),true);
check("survey rows widget: Edit, Delete, + Add survey row",/data-sv="e\$\{i\}">Edit</.test(html)&&/>Delete<\/button><\/div>`\)/.test(html)&&/\+ Add survey row/.test(html),true);

// 12 Nov 2026 (Rajeswari 0088): the office's LOD correction is what the robot pastes, in the Word table's layout
{const W='<table><tr><td>Sl.No</td><td>Date</td><td>  </td><td>Original/Photocopy</td></tr><tr><td></td><td>17.03.2022</td><td>Release Deed (Doc.No.5429/2022) &amp; - MOD</td><td>Original</td></tr><tr><td></td><td>07.11.2025</td><td>MODT (Doc.No.20504/2025)</td><td>Original</td></tr></table>';
 const P=T.lodParse(W);check("LOD header kept",P.head,["Sl.No","Date","","Original/Photocopy"]);check("LOD rows",P.rows.length,2);
 check("typed line -> columns",T.lodSplit("Deed of Receipt dated 10-11-2026, Doc.No.21195/2026, Online Copy",P),["","10.11.2026","Deed of Receipt, Doc.No.21195/2026","Online Copy"]);
 const txt=T.lodTextOf([...P.rows,T.lodSplit("Deed of Receipt dated 10-11-2026, Doc.No.21195/2026, Online Copy",P)].map((r,i)=>{const c=[...r];c[0]=String(i+1);return c}));
 const c={lodHtml:W,tpAuto:{"List of documents deposited":"1 | 17.03.2022 | Release Deed | Original"}};
 const ch=T.lodSync(c,{"List of documents deposited":txt});
 check("corrected LOD rebuilt with the Word header",[ch&&/Sl\.No/.test(ch.lodHtml),ch&&/Online Copy/.test(ch.lodHtml),ch&&/>3</.test(ch.lodHtml),ch&&/&amp;/.test(ch.lodHtml),ch&&ch.lodFrom===txt,ch&&ch.lodWord===W],[true,true,true,true,true,true]);
 check("unchanged LOD -> Word table untouched",T.lodSync(c,{"List of documents deposited":"1 | 17.03.2022 | Release Deed | Original"}),null);
 check("saved again -> nothing new",T.lodSync({...c,...ch},{"List of documents deposited":txt}),null);
 check("pipe row with empty No. keeps its columns",T.lodRowsFromText(" | 17.03.2022 | Release Deed | Original",P)[0],["","17.03.2022","Release Deed","Original"]);
 check("robot uses the rebuilt table",/case\.get\("lodFrom"\)/.test(require("fs").readFileSync(require("path").join(ROOT,"worker","tp_worker.py"),"utf8")),true);}

// 12 Nov 2026: file names - Aadhaar files after the person on the Aadhaar, others case name + (title holder); slots unchanged
{const c={type:"MODT",applicant:"Rajeswari",thKyc:[{url:"u1",name:"Balaji"}],caseWit:{1:{name:"MADHANKUMAR"}},draft:{"Title holders":"1","Mortgagor 1 name":"Balaji"},
  docs:{th_kyc:[{url:"u1",name:"Rajeswari - Title Holder KYC.pdf"}],w1_kyc:[{url:"w",name:"MADHANKUMAR - Witness KYC.pdf"}],deed:[{url:"d",name:"Rajeswari - Title Deed 1.pdf"}],ec:[{url:"e",name:"Rajeswari - EC.pdf"}],photos:[{url:"p",name:"Rajeswari - Property Photo 1.jpeg"}],tpcopy:[{url:"t",name:"TP.pdf"}]}};
 const D=T.docNames(c);check("file names",[D.th_kyc[0].name,D.w1_kyc[0].name,D.deed[0].name,D.ec[0].name,D.photos[0].name,D.tpcopy[0].name,D.deed[0].url],
  ["Balaji - Title Holder 1 KYC.pdf","MADHANKUMAR - Witness 1 KYC.pdf","Rajeswari - Title Deed 1 (Balaji).pdf","Rajeswari - EC (Balaji).pdf","Rajeswari - Property Photo 1 (Balaji).jpeg","TP.pdf","d"]);
 check("names again -> no change",T.docNames({...c,docs:D}),null);
 check("holder = applicant -> no brackets",T.docNames({...c,applicant:"Balaji"}).ec[0].name,"Balaji - EC.pdf");
 check("Deed of Receipt files untouched",T.docNames({...c,type:"Deed of Receipt"}),null);}

// 12 Nov 2026: street of the schedule property before the village, English and Tamil; no street, no change
{const d={"Street name":"Kuppusamy Nayakkar Street",Village:"Porur","Boundary South":"Kuppusamy Nayakkar Street","Boundary South Tamil":"குப்புசாமி நாயக்கர் தெரு","Property remarks":"Porur Village, New Survey No. 20/2B2, an extent of 2499 Sq.Ft.","Property remarks Tamil":"போரூர் கிராமம், புதிய சர்வே எண் 20/2B2"};
 T.remarksStreet(d);check("street in remarks",[d["Property remarks"],d["Property remarks Tamil"],d["Street name Tamil"]],["Kuppusamy Nayakkar Street, Porur Village, New Survey No. 20/2B2, an extent of 2499 Sq.Ft.","குப்புசாமி நாயக்கர் தெரு, போரூர் கிராமம், புதிய சர்வே எண் 20/2B2","குப்புசாமி நாயக்கர் தெரு"]);
 const e={...d};T.remarksStreet(e);check("street not added twice",e["Property remarks"],d["Property remarks"]);
 check("Naicker spelling counts as the same street",T.remarksStreet({"Street name":"Kuppusamy Naicker St",Village:"Porur","Property remarks":d["Property remarks"]})["Property remarks"],d["Property remarks"]);
 const n={Village:"Porur","Property remarks":"Porur Village, x"};check("no street, no change",T.remarksStreet({...n})["Property remarks"],"Porur Village, x");}

// 12 Nov 2026: Extent holds only the number; the unit is Drafting's Unit
check("extent '2499 Sq.Ft.' -> 2499",T.extNum({Extent:"2499 Sq.Ft.",Unit:"Sq.ft"}).Extent,"2499");
check("extent unit taken when Unit empty",(({Extent,Unit})=>[Extent,Unit])(T.extNum({Extent:"5.5 Cents"})),["5.5","Cent"]);
check("extent number stays",T.extNum({Extent:"2499",Unit:"Sq.ft"}).Extent,"2499");
check("extent unclear unit kept for the check",T.extNum({Extent:"2499 kuzhi",Unit:"Sq.ft"}).Extent,"2499 kuzhi");
check("extFull adds the unit back for checks",T.extFull({Extent:"2499",Unit:"Sq.ft"}),"2499 Sq.ft");
check("surveyClean stores the number",T.surveyClean({"New survey no":"20",Extent:"2499 Sq.Ft.",Unit:"Sq.ft"}).Extent,"2499");
check("login IDs: Edit finds MODT logins too",/tnEdit\(\[\.\.\.pool,\.\.\.all\]\.find/.test(html),true);
check("login IDs: added date, newest first, Duplicate mark",/created_at/.test(html)&&/function tnDups\(/.test(html)&&/Duplicate\$\{/.test(html),true);

// 12 Nov 2026: MODT documents per bank (Mandatory / Optional); a bank not set keeps the office rule
{const docs={th_kyc:[{name:"a"}],deed:[{name:"d"}],w1_kyc:[{name:"w"}],w2_kyc:[{name:"w"}]};
 check("office rule: New case as before",T.missingReq("MODT",docs,"Bank A"),[]);
 check("office rule: Draft done still wants patta",T.modtUploads({bank:"Bank A",docs},{}).some(x=>(x.miss||[]).includes("patta")),true);
 T.cfg.profiles["Bank B"]={modtDocs:{revenue:"o",ec:"m",photos:"m"}};
 check("bank set: New case asks for EC and property image, not patta",T.missingReq("MODT",docs,"Bank B"),["EC","Property image"]);
 const up=T.modtUploads({bank:"Bank B",docs},{});check("bank set: Draft done follows it",[up.find(x=>/Property image/.test(x.l)).need,up.find(x=>/EC \+ patta/.test(x.l)).miss],[1,["EC"]]);
 check("Deed of Receipt not touched by the MODT rule",T.missingReq("Deed of Receipt",{},"Bank B").length>0,true);
 delete T.cfg.profiles["Bank B"];}
check("Sheet name box removed from Drafting",/"Sheet name"/.test(html),false);
check("New captcha button next to every captcha box",(html.match(/\$\{capNewBtn\(/g)||[]).length,4);

check("Watch robot: Admin-only button, live screen refresh",/isMD&&!ended&&j\.status!=="cancel"\)h\+=`<div style="margin-top:8px"><button type="button" class="btn" id="rvWatch">/.test(html)&&/function liveWatch\(/.test(html),true);

// 12 Nov 2026: Claude on / off per work (only the Admin switches; nothing stops by itself)
check("Claude on by default",[T.claudeOn("modt"),T.claudeOn("dor"),T.claudeOn("any")],[true,true,true]);
T.cfg.claudeOff={modt:true};
check("MODT off, Receipt on",[T.claudeOn("modt"),T.claudeOn("dor"),T.claudeOn("any")],[false,true,true]);
check("area from the kind when no case",[T.claudeAreaOf({kind:"modt_case"}),T.claudeAreaOf({kind:"modt"}),T.claudeAreaOf({kind:"classify"}),T.claudeAreaOf({})],["modt","dor","dor","any"]);
T.cfg.claudeOff={modt:true,dor:true};check("both off blocks everything",T.claudeOn("any"),false);T.cfg.claudeOff={};
check("Claude plan tab: Admin only, switch checked in the reading wrapper and the mail reads",/if\(t==="__claude"\)return isMD;/.test(html)&&/if\(!claudeOn\(a\)\)return Promise\.resolve/.test(html)&&(html.match(/claudeOn\(areaOfCase\(x\)/g)||[]).length===2,true);
check("Claude plan: old days show about how many cases were made (~)",/const MADE=\{\};cases\.forEach/.test(html)&&/cpN\(d,v,"modt"\)/.test(html)&&/cpN\(d,v,"dor"\)/.test(html),true);
check("TP jobs carry the organisation (partner laptops take only their own)",(html.match(/status:"queued",org:myOrg\(\),/g)||[]).length,5);// 18 Nov 2026: +1 = TP Desk carries on after the last question is answered
{const f=new Function("myOrg",html.match(/const tpOnline=[^\n]*/)[0].replace("const tpOnline=","return ")).call(null,()=>"");const n=Date.parse("2026-11-13T00:00:00Z");
 check("Dashboard counts TP computers that are on (VGR view hides partner laptops)",f([{at:"2026-11-12T23:59:00Z",org:"vgr"},{at:"2026-11-12T23:59:30Z",org:"oo"},{at:"2026-11-12T23:00:00Z",org:"vgr"},{at:"2026-11-12T23:58:00Z"}],n).length,2)}
{/* 13 Nov 2026: the word "robot" never shows on screen - "TP Desk" (code names like robotQs stay) */
 const vis=html.replace(/<meta name="robots"[^>]*>/,"").replace(/\b(?:TP )?\[Rr\]obot\\b/,"").replace(/add\("robot",/,"");
 check("no visible 'robot' word in the portal",(vis.match(/\b[Rr]obot\b|\bROBOT\b/g)||[]).length,0);
 check("old saved text shows TP Desk",eval(html.match(/const esc=(s=>[^\n]*?\}\[c\]\)\))/)[1])("TP robot · daily check / Robot question answered / the robot stopped"),"TP Desk · daily check / TP Desk question answered / the TP Desk stopped");}
{/* 13 Nov 2026: Claude plan switch went back ON - settings must be saved and read whole */
 const staffObj=x=>x,DEFAULT_TYPES=["MODT"];const g=k=>eval(html.match(new RegExp("const "+k+"=[^\\n]*"))[0].replace("const "+k+"=","(").replace(/;\s*$/,"")+")");
 const cfgFrom=g("cfgFrom"),cfgDoc=g("cfgDoc");
 const d=cfgDoc({banks:[1],staff:[],types:["MODT"],claudeOff:{modt:true},cleanAt:"2026-11-13",showMoney:true,claudePay:[{amt:5}],x:undefined});
 check("save keeps the Claude switches, clean-up date, Show money",[d.claudeOff,d.cleanAt,d.showMoney],[{modt:true},"2026-11-13",true]);
 check("payments never go into settings (staff can read settings)",("claudePay" in d)||("x" in d),false);
 check("read keeps the Claude switches",cfgFrom({claudeOff:{dor:true}}).claudeOff,{dor:true});
 check("Claude payments kept Admin-only (usage/_claude_pay)",/db\.doc\("usage\/_claude_pay"\)\.set/.test(html)&&!/cfg\.claudePay/.test(html),true);}
{/* 13 Nov 2026: fee rules - same cases as the TP Desk (tests/fee_cases.json) */
 const FC=JSON.parse(fs.readFileSync(path.join(ROOT,"tests","fee_cases.json"),"utf8"));
 const a=html.indexOf("const FEE_RULE="),b=html.indexOf("function feesHtml(c){");
 for(const t of FC){const fc=new Function("profOf","isRD",html.slice(a,b)+";return feeCalc;")(()=>t.prof||null,x=>/receipt/i.test(x||""));check("fees: "+t.why,fc(t.c).total,t.total)}
 check("Show money switch removed from Tools",/id="showMoney"/.test(html),false);
 check("payment message: Tamil + English, total only",(()=>{const m=html.match(/function payMsg[\s\S]*?\n(?=function openPayment)/)[0];return /மொத்தம்/.test(m)&&/Total: /.test(m)&&!/Service charge|Registration fee/.test(m)})(),true);
 check("fees shown to Admin and partner only",/const FEES_OK=\(\)=>!!\(isMD\|\|isPartner\(\)\)/.test(html),true);}
{/* 13 Nov 2026: KYC Master for the partner - his org's companies only */
 check("KYC Master tab shown to the partner",/\(isMD\|\|isAdmin\|\|isPartner\(\)(\|\|canSplit\(\))?\?`<button role="tab" data-t="__ppl"/.test(html)&&/if\(t==="__ppl"&&isPartner\(\)\)return true;/.test(html),true);
 const pb=new Function("myOrg","cfg","cases","teamCase",html.match(/function partnerBanks\(\)\{[\s\S]*?\n  return cfg\.banks\.filter\(b=>S\.has\(b\)\)\}/)[0]+";return partnerBanks()");
 const C=[{bank:"Muthoot",org:"oo"},{bank:"Chola",org:""},{bank:"ICICI",org:"oo"}];
 check("partner sees only his org's companies",pb(()=>"oo",{banks:["Chola","ICICI","Muthoot","Jana"],profiles:{Jana:{org:"oo"},Chola:{}}},C,c=>c.org==="oo"),["ICICI","Muthoot","Jana"]);
 check("partner: witnesses hidden in KYC Master, reps saved with his org",/wc\.hidden=pplMine\(\)/.test(html)&&/if\(pplMine\(\)&&!p\.org\)p\.org=myOrg\(\)/.test(html),true);
 check("a representative can be removed (last one too)",/p\.reps\.length>1\|\|r\.name\|\|\(r\.kyc\|\|\[\]\)\.length\?/.test(html),true);}
check("TP print check mismatch shown on the case",/TP print check · not seen/.test(html),true);
check("Dashboard shows the daily test result (Admin)",/id="dashTests"/.test(html)&&/actions\/workflows\/daily-tests\.yml\/runs/.test(html),true);
check("Clean-up never touches cases or documents",/async function cleanPlan\(/.test(html)&&!/from\("kv"\)\.delete\(\)\.eq\("collection","cases"\)/.test(html),true);

{/* 16 Nov 2026: KYC Master - split one combined Aadhaar PDF into one KYC PDF per person */
 const sg=new Function(html.match(/function splitGroup\(roles\)\{[\s\S]*?\n  return\{n:P\.length,at\}\}/)[0]+";return splitGroup")();
 check("split: front,back,sign x2",sg(["front","back","sign","front","back","sign"]),{n:2,at:[0,0,0,1,1,1]});
 check("split: 3 e-Aadhaar pages then 3 signatures",sg(["both","both","both","sign","sign","sign"]),{n:3,at:[0,1,2,0,1,2]});
 check("split: signatures at the end shared in order",sg(["front","back","front","back","sign","sign"]),{n:2,at:[0,0,1,1,0,1]});
 check("split: signature before each Aadhaar",sg(["sign","front","back","sign","front","back"]),{n:2,at:[0,0,0,1,1,1]});
 check("split: skip pages stay out",sg(["both","skip","both"]),{n:2,at:[0,-1,1]});
 check("split: one person",sg(["front","back","sign"]),{n:1,at:[0,0,0]});
 const cs=(md,staff,list)=>new Function("isMD","myStaff","cfg",html.match(/function canSplit\(\)\{[^\n]*\}/)[0]+";return canSplit()")(md,staff,{kycSplit:list});
 check("split: Admin always",cs(true,null,[]),true);
 check("split: staff not ticked - no",cs(false,{email:"a@x.in"},[]),false);
 check("split: staff ticked by the Admin - yes",cs(false,{email:"A@x.in"},["a@x.in"]),true);
 check("split: KYC Master tab shown to ticked staff",/isPartner\(\)\|\|canSplit\(\)\?`<button role="tab" data-t="__ppl"/.test(html)&&/if\(t==="__ppl"&&canSplit\(\)\)return true;/.test(html),true);
 check("split: access list only the Admin edits",/\$\("sp_accD"\)\.hidden=!isMD/.test(html),true);
 check("split: normal KYC maker still reads 4 pages",/async function readPdf\(buf,maxP=4\)/.test(html)&&/window\.kycHeadless=async\(files\)=>\{if\(!cv\)cv=\(await waitCv\(\)\)\[0\];const its=\[\];\n  for\(const f of files\)\{const cs=await readFile\(f\)/.test(html),true);}
{/* 16 Nov 2026: MODT case - more than one title holder (+ Add title holder) */
 const ti=new Function("isRD","flowOf",html.match(/function thInit\(c\)\{[\s\S]*?\n  return\[[^\n]*\}/)[0]+";return thInit")(t=>/receipt/i.test(t||""),t=>/legal/i.test(t||"")?"legal":"modt");
 check("title holders: new case starts with one",ti(null),[""]);
 check("title holders: from the case",ti({thCount:3,thNames:["A","B"]}),["A","B",""]);
 check("title holders: from uploaded Aadhaars and Drafting names",ti({docs:{th_kyc:[{},{}]},draft:{"Mortgagor 2 name":"RAVI"}}),["","RAVI"]);
 check("title holders: never more than 4",ti({thCount:9}).length,4);
 const mf=new Function("profOf","cleanAddrPair","branchCoAddr",html.match(/function modtFill\(c,d\)\{[\s\S]*?\n  return d\}/)[0]+";return modtFill")(()=>null,()=>{},()=>null);
 const d1=mf({thCount:2,thNames:["kumar","devi"],docs:{}},{});
 check("Drafting gets the holder count from the case",d1["Title holders"],"2");
 check("Drafting gets the holder names",[d1["Mortgagor 1 name"],d1["Mortgagor 2 name"]],["KUMAR","DEVI"]);
 check("Drafting: names already read are kept",mf({thCount:2,thNames:["kumar","devi"]},{"Mortgagor 1 name":"K. KUMAR","Title holders":"3"})["Mortgagor 1 name"]+"|"+mf({thCount:2},{"Title holders":"3"})["Title holders"],"K. KUMAR|3");
 check("one title holder case unchanged",mf({docs:{th_kyc:[{}]}},{})["Title holders"],"1");
 check("case saves the title holders (MODT only)",/if\(isModtT\(b\.type\)\)\{b\.thCount=draftTh\.length;b\.thNames=/.test(html),true);}
check("MODT type gets the title holder rows",new Function(html.match(/const isRD=[^\n]*/)[0]+";"+html.match(/const flowOf=[^\n]*/)[0]+";"+html.match(/const isModtT=[^\n]*/)[0]+";return [isModtT('MODT'),isModtT('Deed of Receipt'),isModtT('Legal Report')]")(),[true,false,false]);
{/* 16 Nov 2026 (VGR-20261116-0155): answered doubts never come back; removing a title holder; remarks follow the survey number */
 const grabFn=(start,end)=>{const a=html.indexOf(start),b=html.indexOf(end,a);return html.slice(a,b)};
 const RS=new Function(grabFn("const svTok=","\n  return t}")+"\n  return t};return remarksSurvey")();
 const rem="Suleeswaranpatti Village, New Survey No. 152, an extent of 1350 Sq.ft., Plot No. 71, Door No. 152.";
 check("remarks: sub division added to the survey number only",RS(rem,[{s:"152",sd:"6"}],[{s:"152",sd:"6"}]),"Suleeswaranpatti Village, New Survey No. 152/6, an extent of 1350 Sq.ft., Plot No. 71, Door No. 152.");
 check("remarks: survey number changed",RS("Village, New Survey No. 152/6, extent",[{s:"152",sd:"6"}],[{s:"153",sd:"2"}]),"Village, New Survey No. 153/2, extent");
 check("remarks: Tamil follows",RS("சூலேஸ்வரன்பட்டி கிராமம், புதிய சர்வே எண் 152, 1350 சதுர அடி",[{s:"152",sd:"6"}],[{s:"152",sd:"6"}]),"சூலேஸ்வரன்பட்டி கிராமம், புதிய சர்வே எண் 152/6, 1350 சதுர அடி");
 check("remarks: already right - unchanged",RS("New Survey No. 152/6 and 153",[{s:"152",sd:"6"}],[{s:"152",sd:"6"}]),"New Survey No. 152/6 and 153");
 check("remarks: second survey in a list",RS("S.No. 150/1, 152, extent",[{s:"152",sd:""}],[{s:"152",sd:"4"}]),"S.No. 150/1, 152/4, extent");
 const DH=new Function(grabFn("function dropHolder(","\n/* which Drafting")+";return dropHolder")();
 const r=DH({"Title holders":"2","Mortgagor 1 name":"M Selvaraj","Mortgagor 1 Aadhaar":"1","Mortgagor 2 name":"Mahalakshmi","Mortgagor 2 Aadhaar":"2","Presenter":"Title holder 2"},1,[{field:"Mortgagor 1 name Tamil",q:"x"},{field:"Mortgagor 2 address",q:"Check Mortgagor 2 address"},{field:"Title holders",answer:"1"}]);
 check("remove holder 1: holder 2 moves up",[r.d["Title holders"],r.d["Mortgagor 1 name"],r.d["Mortgagor 1 Aadhaar"],r.d["Mortgagor 2 name"],r.d.Presenter],["1","Mahalakshmi","2",undefined,"Title holder 1"]);
 check("remove holder 1: doubts move with the holders",r.qs.map(q=>q.field),["Mortgagor 1 address","Title holders"]);
 check("remove holder: never below one",DH({"Title holders":"1","Mortgagor 1 name":"A"},1,[]).d["Mortgagor 1 name"],"A");
 const HF=new Function(grabFn("function holderOfFile(","\n/* 16 Nov 2026: MODT title holders")+";return holderOfFile")();
 check("which holder a deleted Aadhaar belongs to",HF({draft:{"Title holders":"2","Mortgagor 1 name":"M Selvaraj","Mortgagor 2 name":"Mahalakshmi"},thKyc:[{url:"u1",name:"M SELVARAJ"}]},{url:"u1",name:"x.pdf"}),1);
 check("answered doubt never asked again",/list=\(list\|\|\[\]\)\.filter\(q=>!done\.some\(x=>x\.field===q\.field\)\);/.test(html),true);
 const mf=new Function("profOf","cleanAddrPair","branchCoAddr",html.match(/function modtFill\(c,d\)\{[\s\S]*?\n  return d\}/)[0]+";return modtFill")(()=>null,()=>{},()=>null);
 check("two Aadhaars but answered one holder: stays one",mf({docs:{th_kyc:[{},{}]}},{"Title holders":"1"})["Title holders"],"1");
 check("Drafting save keeps the case's holder count in step",/ch\.thCount=n;ch\.thNames=/.test(html),true);}
{const dc=new Function(html.match(/function deedNoClean\(d\)\{[\s\S]*?return d\}/)[0]+";return deedNoClean")();
 const d=dc({"Deed 1 Doc No":"992","Deed 1 Year":"1989","Deed 2 Doc No":"1086/1992","Deed 2 Year":"1992","Deed 3 Doc No":"55 of 2001"});
 check("deed doc no: year taken out of the number (VGR 0155)",[d["Deed 1 Doc No"],d["Deed 2 Doc No"],d["Deed 3 Doc No"],d["Deed 3 Year"]],["992","1086","55","2001"]);}
{/* 16 Nov 2026: Lendingkart branch addresses (VGR-20261116-0155 Nagercoil) */
 const P={"Lendingkart Finance Limited":{company:{addr_en:"Office 3 Connekt, 401/B, 4th Floor, Silver Utopia, Cardinal Gracious Road, Andheri (East), Mumbai Suburban",office:"Registered & Corporate Office",state:"Maharashtra",pincode:"400099"},branches:{Nagercoil:"Kalluveetil Teenu Centre 82, Third Floor Part A, Court Road, Nagercoil 629001",Tiruppur:"RKG Building No.9 Universal Theatre Road, Tirupur - 641601"}}};
 const src=html.match(/const brNorm=[\s\S]*?\nfunction modtFill\(c,d\)\{[\s\S]*?\n  return d\}/)[0];
 const T2=new Function("profOf","cleanAddrPair",src+";return{branchCoAddr,modtFill}")(b=>P[b]||null,()=>{});
 const d=T2.modtFill({bank:"Lendingkart Finance Limited",branch:"Nagercoil"},{"Company address":"Mumbai only, wrong"});
 check("branch address: Nagercoil case",d["Company address"],"Nagercoil Branch, Kalluveetil Teenu Centre 82, Third Floor Part A, Court Road, Nagercoil 629001, having its Registered & Corporate Office at Office 3 Connekt, 401/B, 4th Floor, Silver Utopia, Cardinal Gracious Road, Andheri (East), Mumbai Suburban");
 check("branch address: office, state, pincode",[d["Company office"],d["Company state"],d["Company pincode"],d["Company address Tamil"]],["Registered & Corporate Office","Maharashtra","400099",""]);
 check("branch address: Tirupur spelling matches Tiruppur",(T2.branchCoAddr({bank:"Lendingkart Finance Limited",branch:"Tirupur branch"})||{}).en.slice(0,16),"Tiruppur Branch,");
 check("branch address: unknown branch leaves the MODT address",T2.modtFill({bank:"Lendingkart Finance Limited",branch:"Salem"},{"Company address":"From MODT"})["Company address"],"From MODT");
 check("branch address: already right is kept (edits stay)",T2.modtFill({bank:"Lendingkart Finance Limited",branch:"Nagercoil"},{"Company address":"Nagercoil Branch, Kalluveetil Teenu Centre 82 (edited)"})["Company address"],"Nagercoil Branch, Kalluveetil Teenu Centre 82 (edited)");
 check("MODT witness shows details",/data-wdet="\$\{n\}">\$\{witDetails\(cur\)\}/.test(html),true);}
