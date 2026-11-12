// Regression tests for portal helpers (pure functions pulled out of index.html). Run: node tests/test_portal.js
const fs=require("fs"),path=require("path"),ROOT=path.join(__dirname,"..");
const html=fs.readFileSync(path.join(ROOT,"index.html"),"utf8");
const grab=(start,end)=>{const a=html.indexOf(start);if(a<0)throw new Error("not found: "+start);const b=html.indexOf(end,a+start.length);return html.slice(a,b)};
let src="const isRD=t=>/receipt/i.test(t||'');const esc=x=>String(x);const cfg={wit:[],banks:[],profiles:{}};var profOf=b=>(cfg.profiles||{})[b]||null;\n";
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
src+=grab("function surveyClean(","\nasync function readModtCase(")+"\n";
src+="const nowISO=()=>'2026-11-12T00:00:00Z',who=()=>'Test';\n"+grab("function settleQs(","\nfunction ")+"\n";
src+=grab("async function doubtQs(","\n/* files are kept")+"\n";
const MSTATES=(html.match(/MSTATES=\[[^\]]*\]/)||["MSTATES=[]"])[0];
const T={};new Function("T",`const ${MSTATES};${src};T.parseSurvey=parseSurvey;T.partSurvey=partSurvey;T.modtCoAddr=modtCoAddr;T.lodBoxOf=lodBoxOf;T.dorDoneCheck=dorDoneCheck;T.custFromSubject=custFromSubject;T.NOT_DOR_RE=NOT_DOR_RE;T.dupPlan=dupPlan;T.missingReq=missingReq;T.modtUploads=modtUploads;T.surveyClean=surveyClean;T.settleQs=settleQs;T.doubtQs=doubtQs;T.svRowsOf=svRowsOf;T.svFix=svFix;T.legalSecurity=legalSecurity;T.secText=secText;T.deedProblems=deedProblems;T.applySecurity=applySecurity;T.legalBlock=legalBlock;T.lodParse=lodParse;T.lodSplit=lodSplit;T.lodRowsFromText=lodRowsFromText;T.lodBuild=lodBuild;T.lodSync=lodSync;T.lodTextOf=lodTextOf;T.docNames=docNames;T.remarksStreet=remarksStreet;T.extNum=extNum;T.extFull=extFull;T.cfg=cfg;T.modtRule=modtRule;`)(T);
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

