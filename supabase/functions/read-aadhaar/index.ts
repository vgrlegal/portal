// VGR Legal portal: read an Aadhaar card or a registered MODT copy with Claude and return the fields as JSON.
// Needs the secret ANTHROPIC_API_KEY. Only signed-in MD / staff can call it.
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";

const cors = {
  "Access-Control-Allow-Origin": "https://portal.vgrlegal.in",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const json = (b: unknown, s = 200) => new Response(JSON.stringify(b), { status: s, headers: { ...cors, "Content-Type": "application/json" } });

const PROMPTS: Record<string, string> = {
  classify: `Each image is one page (usually the FIRST PAGE) of a file sent by a bank for a Tamil Nadu "Deed of Receipt" (MODT discharge) case; a text label before each image gives the file number, file name and sometimes the page. Give one entry per IMAGE, numbered 1, 2, 3... in the order of the images, and decide what that page is (a file may hold several documents joined together). Return ONLY JSON:
{"files":[{"n":1,"type":"modt | supp_modt | noc | patta | ec | letter | aadhaar | other","customer":"borrower / mortgagor name if printed","loan_no":"loan / LAN number if printed","doc_no":"registered document number/year if it is a registered MODT"}],"phone":"customer mobile if printed anywhere","lender":"the bank / NBFC / housing finance company that gave the loan (NOT the customer), its full legal name as printed on the letterhead or NOC, e.g. Truhome Finance Limited"}
modt = the registered Memorandum of Deposit of Title Deeds (has SRO registration endorsement, document number). supp_modt = a supplementary / further MODT. noc = no-objection / closure / release / discharge letter from the lender. patta = patta, chitta, TSLR or other revenue record. ec = encumbrance certificate. letter = covering letter / request / authorisation from the bank. aadhaar = ID card. Judge by the content, not the file name.`,
  modt_case: `You are preparing a Tamil Nadu TNREGINET "Deposit of Title Deeds" (MODT) entry for a law office. You get, each labelled: the bank's MODT DRAFT (Word text), the TITLE DEED pages (often Tamil), the title holders' AADHAAR/KYC pages, PATTA / revenue pages, and when given EC pages, a PROPERTY PHOTO, the SANCTION LETTER and an FMB sketch. Read them and return ONLY one JSON object with exactly these keys (use "" when unknown):
{"Deed 1 SRO":"SRO of the 1st title deed, English","Deed 1 Doc No":"","Deed 1 Year":"4 digits","Deed 2 SRO":"","Deed 2 Doc No":"","Deed 2 Year":"","Deed 3 SRO":"","Deed 3 Doc No":"","Deed 3 Year":"",
"Title holders":"number of title holders / mortgagors, 1-4",
"Mortgagor 1 salutation":"", "Mortgagor 1 name":"", "Mortgagor 1 name Tamil":"", "Mortgagor 1 relation":"", "Mortgagor 1 relative name":"", "Mortgagor 1 relative name Tamil":"", "Mortgagor 1 date of birth":"", "Mortgagor 1 Aadhaar":"", "Mortgagor 1 district":"", "Mortgagor 1 address":"", "Mortgagor 1 address Tamil":"", "Mortgagor 1 pincode":"", "Mortgagor 1 phone":"", "Mortgagor 2 salutation":"", "Mortgagor 2 name":"", "Mortgagor 2 name Tamil":"", "Mortgagor 2 relation":"", "Mortgagor 2 relative name":"", "Mortgagor 2 relative name Tamil":"", "Mortgagor 2 date of birth":"", "Mortgagor 2 Aadhaar":"", "Mortgagor 2 district":"", "Mortgagor 2 address":"", "Mortgagor 2 address Tamil":"", "Mortgagor 2 pincode":"", "Mortgagor 2 phone":"", "Mortgagor 3 salutation":"", "Mortgagor 3 name":"", "Mortgagor 3 name Tamil":"", "Mortgagor 3 relation":"", "Mortgagor 3 relative name":"", "Mortgagor 3 relative name Tamil":"", "Mortgagor 3 date of birth":"", "Mortgagor 3 Aadhaar":"", "Mortgagor 3 district":"", "Mortgagor 3 address":"", "Mortgagor 3 address Tamil":"", "Mortgagor 3 pincode":"", "Mortgagor 3 phone":"", "Mortgagor 4 salutation":"", "Mortgagor 4 name":"", "Mortgagor 4 name Tamil":"", "Mortgagor 4 relation":"", "Mortgagor 4 relative name":"", "Mortgagor 4 relative name Tamil":"", "Mortgagor 4 date of birth":"", "Mortgagor 4 Aadhaar":"", "Mortgagor 4 district":"", "Mortgagor 4 address":"", "Mortgagor 4 address Tamil":"", "Mortgagor 4 pincode":"", "Mortgagor 4 phone":"",
"Govt Body Name":"mortgagee company full legal name","Company state":"state of the company's head office address","Company district":"district of that address, e.g. Chennai","Company address":"head office address (and branch if the draft gives it), English","Company address Tamil":"same address in Tamil script","Company pincode":"",
"Property type":"exactly one of: Plot with Building, Plot, Flats, Agricultural Land, Agriculture Land with Building","Building type":"from the PROPERTY PHOTO: RCC house, Tiled house, Sheet roof house, or No building","Property SRO":"SRO of the property, English","Village":"revenue village, English","Patta No":"patta number from the PATTA page","Land type":"one of: Urban, Natham, Rural (read from the patta)","Ward":"ward, only for Urban (from the patta / MODT)","Block":"block, only for Urban","Street name":"the street of the property exactly as in the LEGAL REPORT's schedule property; else only if the EC (after the village) or the MODT draft names one","Street name Tamil":"that street in Tamil script (e.g. from the title deed or the Tamil boundary)","Unit":"Sq.ft, Sq.m, Cent, Acre or Hectare as in the MODT draft (if unclear, as the EC entry of the title deed)","New survey no":"the NEW survey number without subdivision, e.g. 73","Subdivision":"e.g. 10","Linked survey numbers":"ONLY when the property lies over MORE THAN ONE new survey number: the other new survey numbers as survey/sub=extent, comma separated, e.g. 74/2=300; else empty","Old survey numbers":"comma separated, e.g. 73/2, 73/2A","Extent":"mortgaged extent, number with unit exactly as in the MODT draft","Total survey extent":"ONLY for agricultural land: total extent of the survey number EXACTLY as printed in the uploaded PATTA / revenue page (the current patta, e.g. '0 - 2.23' hectare-ares) - NOT from an old patta copy inside the title deed; keep the patta's own figure and unit, do not convert; else empty","Plot No":"plot number if the MODT draft or title deed gives one",
"Boundary type":"Regular or Irregular","Boundary North":"English: what is on the NORTH side, seen from INSIDE our property","Boundary South":"","Boundary East":"","Boundary West":"","Boundary North Tamil":"Tamil script, same thing, spelling from the title deed","Boundary South Tamil":"","Boundary East Tamil":"","Boundary West Tamil":"",
"North length":"number only, in the Unit's linear measure (feet for Sq.ft, metre for Sq.m)","South length":"","East length":"","West length":"",
"Property remarks":"English, exactly this pattern: [<Street name>, ]<Village> Village, New Survey No. <survey/sub> (Old Survey No. <old>), an extent of <extent>[, Plot No. <n>], together with the <RCC house / tiled house> and all the land therein. (no building: end with ', and all the land therein.')","Property remarks Tamil":"the same in Tamil, pattern: [<தெரு>, ]<கிராமம்> கிராமம், புதிய சர்வே எண் <..> (பழைய சர்வே எண் <..>), <..> சதுர அடி[, மனை எண் <..>], ஆர்.சி.சி. வீடும் அதிலுள்ள சகலமும். (tiled house: ஓட்டு வீடும் அதிலுள்ள சகலமும்; never write கூரை வீடு; no building: அதிலுள்ள சகலமும்)",
"Loan amount":"digits only, from the MODT draft","Sanction loan amount":"digits only, from the SANCTION LETTER if given","List of documents deposited":"the 'list of documents deposited' / schedule of title documents from the MODT draft, one row per line, columns separated by |, as in the draft","LOD corrections":"one line per WRONG document number or year in that list: wrong -> right (why). Check every document number and year in the list against the title deed scans, the EC entries and the deed numbers above; a different day/date or a small name spelling difference is NOT an error. Empty if all correct.",
"Execution SRO":"SRO where the MODT is registered (normally the property SRO), English","SRO Tamil":"that SRO name in Tamil",
"Doubts":"short list of things you were unsure about, for the office to check"}
Rules: LEGAL REPORT FIRST - when the text includes a LEGAL REPORT, Deed 1-3 are ONLY the title deed(s) under its "Documents for security" (or marked "- MOD"): never an earlier sale deed, never the earlier MODT unless it is a top-up loan, never a Deed of Receipt / discharge receipt; and the property (survey numbers, old survey numbers, extent, street, boundaries, measurements) is the ONE schedule in that legal report - where the MODT draft differs, follow the legal report and say so in Doubts. New survey no is the number only and Subdivision its sub-division; each other new survey number of the same property goes to Linked survey numbers. names, relation, date of birth (DD-MM-YYYY), Aadhaar number, district, address and pincode of each holder come from that holder's AADHAAR exactly as printed (English and Tamil); check them against the title deed. Mr./Mrs./Ms. as in the ID. Relation is S/o, W/o, D/o or C/o. Holder order as in the MODT draft. Loan amount, company address, extent, boundaries, linear measurements and list of documents come from the MODT DRAFT; compare with the title deed and mention differences in Doubts. Tamil boundary text from the title deed where available. BOUNDARIES ARE ALWAYS GIVEN AS SEEN FROM INSIDE OUR PROPERTY: Tamil deeds describe from outside — "ராமசாமி நிலத்திற்கும் தெற்கு" means our property is south of Ramasamy's land, so Ramasamy's land is our NORTH: Boundary North "Ramasamy land", Boundary North Tamil "ராமசாமி நிலம்" (drop "நிலத்திற்கும் தெற்கு"). In English drafts "North BY X" keeps the direction (X is North); "South OF X" is from outside, so X goes to the OPPOSITE side (North). Property type: Agricultural Land when the deed says agricultural land, or the extent is in cents above about 5 cents, or in acres/hectares; then "Agriculture Land with Building" if the PROPERTY PHOTO shows a building. Otherwise Plot with Building if the photo (or MODT draft / patta) shows a building, else Plot. ALL FOUR BOUNDARIES (North, South, East, West) MUST be filled in English AND Tamil: take each from the MODT draft; when the draft leaves one out (or it is blank), take that side from the TITLE DEED (same inside-view rule) and write in Doubts "Boundary <side> taken from the title deed"; compare every side of the draft with the title deed and put each real difference in "doubts" (field "Boundary <side>"). Boundary type Regular only when North length = South length and East length = West length, else Irregular. Land type from the PATTA: Ward and Block shown → Urban; natham patta → Natham; agricultural / punjai / nanjai → Rural — if unsure, say so in Doubts. Linear measurements from the MODT draft or title deed, else from an FMB sketch if one is given. Name in Tamil must be exactly the same name as the English name (if English is "Singumani", Tamil is only "சிங்குமணி", not the father's name too); use the name as in the title deed / MODT draft when the Aadhaar adds the father's name. Aadhaar: 12 digits, no spaces. Addresses (English and Tamil): only the part BEFORE the district (no district, state or pincode, they have their own boxes) and no ":" characters (write "PO" not "PO:"). Never invent numbers; leave "" if not printed.`,
  aadhaar: `You are reading an Indian Aadhaar card (front/back photo, or the e-Aadhaar letter). Return ONLY a JSON object with these keys (use "" when not printed):
{"name":"English name exactly as printed","name_ta":"Tamil name exactly as printed in Tamil script","rel_type":"S/O, W/O, D/O or C/O as printed in the address","rel_name":"relative's name in English","rel_name_ta":"relative's name in Tamil script","dob":"DD-MM-YYYY, or YYYY if only year of birth","gender":"Male or Female","aadhaar":"12-digit Aadhaar number, digits only (NOT the 16-digit VID, NOT the enrolment number)","address_en":"full English address after the relation part, as one line, ending with the pincode","address_ta":"full Tamil address after the relation part, as one line","district":"district from the English address","state":"state from the English address","pincode":"6-digit pincode","mobile":"10-digit mobile if printed"}
Copy text exactly; do not translate or guess. If Tamil is not printed, leave the Tamil keys empty.`,
  modt: `These are pages of a registered Tamil Nadu MODT (Memorandum of Deposit of Title Deeds) copy: usually an SRO registration receipt/endorsement page (Tamil + English) and the deed pages. Return ONLY a JSON object with these keys (use "" when not found):
{"sro":"Sub Registrar Office name in English (e.g. Rasipuram)","sro_ta":"SRO name in Tamil","book":"book number (usually 1)","doc_no":"registered document number","year":"registration year (4 digits)","reg_date":"registration date DD-MM-YYYY",
"mortgagor_sal":"Mr, Mrs or Ms","mortgagor":"mortgagor/borrower full name in English","mortgagor_ta":"mortgagor name in Tamil if printed","age":"age stated in the deed (number only)","rel_type":"S/O, W/O, D/O or C/O","rel_name":"relative's name in English","aadhaar":"mortgagor's 12-digit Aadhaar, digits only","address":"mortgagor address in English as one line","pincode":"6-digit pincode of that address","mobile":"mortgagor mobile if printed",
"co_mortgagors":[{"sal":"Mr, Mrs or Ms","name":"English name","name_ta":"Tamil name exactly as printed","age":"age number","rel_type":"S/O, W/O, D/O or C/O","rel_name":"relative's name in English"}],"bank":"mortgagee bank / finance company full name","bank_branch":"lender branch","earlier_doc_no":"ONLY if this is a supplementary / additional / further-charge MODT that refers to an EARLIER deposit of title deeds for the same loan or property: that earlier document number, else empty","earlier_year":"year of that earlier document","earlier_sro":"SRO of that earlier document in English","earlier_book":"book of that earlier document (usually 1)","earlier_loan_amount":"loan amount secured by that earlier document, digits only, if printed","bank_address":"mortgagee's registered / corporate office address in English exactly as printed","bank_address_ta":"same address in Tamil exactly as printed, if printed","bank_state":"state of that corporate address","bank_district":"district of that corporate address","bank_pincode":"6-digit pincode of that corporate address","branch_address":"lender branch address in English exactly as printed","branch_address_ta":"branch address in Tamil exactly as printed, if printed","loan_amount":"loan amount in rupees, digits only","loan_account":"loan account number if printed",
"property_district":"district of the property","property_sro":"SRO of the property in English","village":"village of the property in English","survey_no":"survey number(s) with subdivision","extent":"extent with unit as printed","north":"north boundary","east":"east boundary","west":"west boundary","south":"south boundary"}
sro, sro_ta, book, doc_no, year and reg_date MUST come from the SRO's REGISTRATION ENDORSEMENT of THIS document: the registration receipt / certificate / endorsement pages (stamped by the Sub-Registrar, e.g. "registered as Document No. 1232 of 2023 in Book I ... Sub-Registrar of ...", Tamil "பதிவு செய்யப்பட்டது" / "ஆவண எண்" / "சார்பதிவாளர்"), which are often the first page and the last pages (pages 7 to 10). NEVER take them from the property schedule, from the property's SRO / registration district, or from earlier title deeds mentioned in the deed; those go only in property_sro. The office in the Tamil endorsement line (e.g. "R/1 எண் இணை சார்பதிவாளர் கன்னியாகுமாரி/புத்தகம்-1/4221/2023" = Joint I Sub-Registrar Kanyakumari, Book 1, Doc 4221, Year 2023) is the one to use: give sro as the Registration Department's English name of THAT office, e.g. "Kanyakumari Joint I"; ignore town aliases on rubber stamps such as "Kanniyakumari @ Nagercoil" (use the office name before the @).
Copy text exactly as printed; do not guess. If there are several mortgagors / borrowers / co-borrowers, give the first one in the mortgagor fields and EVERY other one, in the order printed, in co_mortgagors (empty list if only one). Never skip a mortgagor. mortgagor_ta must be the Tamil spelling exactly as printed in the document (never transliterate it yourself; leave it empty if no Tamil name is printed). The four boundaries are in the property schedule (often near the end, sometimes in Tamil: வடக்கு north, கிழக்கு east, மேற்கு west, தெற்கு south) - give each boundary text as printed (English if English is printed, otherwise the Tamil text). For property_sro and village give the English name as printed.`,
  tamil: `Below is a JSON object of Tamil Nadu place names (Sub-Registrar offices, villages, taluks, districts) in English. For each key give the name in Tamil script exactly as the Tamil Nadu Registration Department writes it (e.g. Rasipuram → இராசிபுரம், Kunrathur → குன்றத்தூர், Oothangarai → ஊத்தங்கரை). Return ONLY a JSON object with the same keys. Tamil script only, no English, no explanation.`,
  english: `Below is a JSON object of text fields taken from a Tamil Nadu TNREGINET registration form. Most values are in Tamil script. For each key, give the English (Latin script) form that a Tamil Nadu document writer would type in the English field, and return ONLY a JSON object with the same keys.
Rules:
- Names: transliterate, do not translate. Tamil initials become English initials (மா. → M., இரா. → R., கே. → K.). Keep the same order and the same parts: if the Tamil has the father's name joined (e.g. ராமசாமி மாரிமுத்து), keep it joined (Ramasamy Marimuthu). Use the usual Tamil Nadu spelling (ராமசாமி → Ramasamy, முருகன் → Murugan, செல்வி → Selvi). Write names in Title Case.
- Addresses and property text: transliterate names of people, streets, villages, towns; translate the common words: தெரு Street, சாலை Road, நகர் Nagar, கிராமம் Village, வட்டம் Taluk, மாவட்டம் District, அஞ்சல் Post, கதவு எண் / எண் Door No., பிளாட் Plot, சர்வே எண் / புல எண் S.F.No., உட்பிரிவு Sub Division, ஏக்கர் Acres, ஹெக்டேர் Hectares, சதுர அடி Sq.ft, நிலம் land, வீடு house, காலி மனை vacant site, பொது வழி / பாதை common pathway, ரோடு Road, வாய்க்கால் channel, புறம்போக்கு poramboke. Keep all numbers exactly.
- Boundaries (keys mentioning north/south/east/west/boundary, or text that starts with வடக்கு/தெற்கு/கிழக்கு/மேற்கு): give ONLY the boundary description, e.g. "வடக்கு - ராமசாமி நிலம்" → "Ramasamy land". Never add the direction word, never add slashes or dashes.
- If a value is already English, return it unchanged. Do not add anything that is not in the Tamil text.`,
};

// Admin invites a staff member: Supabase emails a link and they set their own password.
// Only the Admin (my_role = 'md'), and only for an email already saved in Clients & Staff.
// Invite a staff / client login. The Admin can invite anyone saved in Clients & Staff; a partner (e.g. Opinion &
// Ownership) can invite only their own team. link=true returns a one-time link to send on WhatsApp instead of e-mail.
// deno-lint-ignore no-explicit-any
async function invite(sb: any, role: unknown, email: unknown, link: unknown) {
  const em = String(email ?? "").trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(em)) return json({ error: "Not a valid email" }, 400);
  const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
  const { data: cfg } = await admin.from("kv").select("data").eq("collection", "config").eq("id", "lists").maybeSingle();
  const staff = ((cfg?.data as { staff?: unknown[] })?.staff ?? []) as { email?: string; team?: string }[];
  const clients = ((cfg?.data as { clients?: unknown[] })?.clients ?? []) as { email?: string }[];
  const find = (a: { email?: string }[]) => a.find((x) => typeof x === "object" && String(x.email ?? "").toLowerCase() === em);
  if (role !== "md") {
    const { data: org } = await sb.rpc("partner_org");
    const m = find(staff) as { team?: string } | undefined;
    if (!org || !m || m.team !== org) return json({ error: "You can invite only your own team" }, 403);
  } else if (!find(staff) && !find(clients)) return json({ error: "Save this email in Clients & Staff first" }, 400);
  if (link) {
    let r = await admin.auth.admin.generateLink({ type: "invite", email: em, options: { redirectTo: "https://portal.vgrlegal.in/?invite=1" } });
    if (r.error && /already/i.test(r.error.message)) r = await admin.auth.admin.generateLink({ type: "recovery", email: em, options: { redirectTo: "https://portal.vgrlegal.in/" } });
    if (r.error) return json({ error: r.error.message }, 400);
    return json({ ok: true, link: r.data.properties.action_link });
  }
  const { error } = await admin.auth.admin.inviteUserByEmail(em, { redirectTo: "https://portal.vgrlegal.in/?invite=1" });
  if (error) {
    if (/already/i.test(error.message)) {
      const r = await admin.auth.resetPasswordForEmail(em, { redirectTo: "https://portal.vgrlegal.in/" });
      if (r.error) return json({ error: r.error.message }, 400);
      return json({ ok: true, sent: "reset" });
    }
    return json({ error: error.message }, 400);
  }
  return json({ ok: true, sent: "invite" });
}

// A bank (client) login opens a file of its own case: the server checks the case belongs to that bank and
// the file is one the bank may see, then hands back a short-lived link.
// deno-lint-ignore no-explicit-any
async function clientFile(sb: any, role: unknown, doc: unknown, url: unknown) {
  if (role !== "client") return json({ error: "Not allowed" }, 403);
  const { data: list, error } = await sb.rpc("client_cases");
  if (error) return json({ error: error.message }, 400);
  const c = (list ?? []).find((x: { doc: string }) => x.doc === String(doc));
  if (!c) return json({ error: "Case not found" }, 404);
  const ok = Object.values(c.docs ?? {}).some((arr) => Array.isArray(arr) && arr.some((f: { url?: string }) => f.url === url));
  if (!ok || !String(url).startsWith("sb:")) return json({ error: "File not found" }, 404);
  const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
  const { data, error: e2 } = await admin.storage.from("docs").createSignedUrl(String(url).slice(3), 600);
  if (e2) return json({ error: e2.message }, 400);
  return json({ url: data.signedUrl });
}

// Cases arriving by e-mail (receipt@ mailbox): a Google Apps Script in that mailbox posts each new mail here.
// Protected by MAIL_SECRET. Creates a Deed of Receipt case in Draft Pending with the attachments; the Admin's
// portal then reads the MODT with Claude (needsRead) and fills the customer, company and loan.
async function mailIn(body: Record<string, unknown>) {
  const secret = Deno.env.get("MAIL_SECRET");
  if (!secret || body.secret !== secret) return json({ error: "Not allowed" }, 403);
  const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
  if (body.action === "queries") return await mailQueries(admin);
  if (body.action === "queryDone") return await mailQueryDone(admin, String(body.doc ?? ""), String(body.how ?? "sent"));
  const mailId = String(body.mailId ?? "").slice(0, 200);
  if (!mailId) return json({ error: "mailId needed" }, 400);
  const { data: dup } = await admin.from("kv").select("id").eq("collection", "cases").filter("data->mailIds", "cs", JSON.stringify([mailId])).limit(1);
  if (dup && dup.length) return json({ ok: true, duplicate: true, doc: dup[0].id });
  const { data: dup1 } = await admin.from("kv").select("id").eq("collection", "cases").eq("data->>mailId", mailId).limit(1);
  if (dup1 && dup1.length) return json({ ok: true, duplicate: true, doc: dup1[0].id });
  const threadId = String(body.threadId ?? "").slice(0, 200);
  // "FW:", "Req:", "RE:", "Fwd:" and similar short prefixes are removed, so the same request sent twice is one case
  const subjKey = String(body.subject ?? "").toLowerCase().replace(/^\s*(([a-z]{1,5})\s*:\s*|\[external\]-?\s*)+/g, "").replace(/\s+/g, " ").trim().slice(0, 120);
  // loan account numbers in the subject (long codes with many digits), e.g. MFLSECUTAML000005488668
  const loanKeys = [...new Set((String(body.subject ?? "").toUpperCase().match(/[A-Z0-9]{8,}/g) ?? []).filter((x) => (x.match(/\d/g) ?? []).length >= 6))].slice(0, 5);
  const labels = (Array.isArray(body.labels) ? body.labels : []).map((x: unknown) => String(x).slice(0, 60)).filter((x: string) => !/^(vgr-portal|opinion-portal)$/i.test(x)).slice(0, 10);
  const files = Array.isArray(body.files) ? body.files.slice(0, 15) : [];
  const t = new Date().toISOString().replace(/\.\d+Z$/, "Z");
  const day = t.slice(0, 10).replace(/-/g, "");
  // fingerprint of every file (free): the same PDF sent again is recognised before any reading
  const items: { name: string; mime: string; bytes: Uint8Array; hash: string }[] = [];
  for (const f of files as { name?: string; mime?: string; b64?: string }[]) {
    const name = String(f.name ?? "file").replace(/[\\/#?%*:|"<>]/g, "_").slice(0, 120);
    if (!f.b64 || !/\.(pdf|jpe?g|png|docx?)$/i.test(name)) continue;
    const bytes = Uint8Array.from(atob(f.b64), (c) => c.charCodeAt(0));
    const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
    if (items.some((x) => x.hash === hash)) continue;
    items.push({ name, mime: f.mime || "application/octet-stream", bytes, hash });
  }
  // the same mail conversation (thread) is ONE case; so is the same subject, the same file or the same loan number
  let same: { id: string; data: Record<string, unknown> } | null = null, why = "";
  if (threadId) { const { data: r } = await admin.from("kv").select("id,data").eq("collection", "cases").eq("data->>mailThread", threadId).limit(1); if (r && r.length) { same = r[0] as never; why = "same mail conversation"; } }
  if (!same && subjKey.length > 8) { const { data: r } = await admin.from("kv").select("id,data").eq("collection", "cases").eq("data->>mailSubjectKey", subjKey).eq("data->>source", "mail").limit(5); const h = (r ?? []).find((x) => !(x.data as Record<string, unknown>)?.notCase); if (h) { same = h as never; why = "same subject"; } }
  for (const it of items) { if (same) break; const { data: r } = await admin.from("kv").select("id,data").eq("collection", "cases").filter("data->fileHashes", "cs", JSON.stringify([it.hash])).limit(1); if (r && r.length) { same = r[0] as never; why = "same file (" + it.name + ")"; } }
  for (const lk of loanKeys) { if (same) break; const { data: r } = await admin.from("kv").select("id,data").eq("collection", "cases").filter("data->loanKeys", "cs", JSON.stringify([lk])).limit(5); const h = (r ?? []).find((x) => !(x.data as Record<string, unknown>)?.notCase); if (h) { same = h as never; why = "same loan number " + lk; } }
  // removed in the portal as "not a case": a later mail of the same conversation (or the same files) is not made a case again
  if (same && (same.data as Record<string, unknown>)?.notCase) {
    const d = same.data as Record<string, unknown>; const t2 = new Date().toISOString().replace(/\.\d+Z$/, "Z");
    await admin.from("kv").update({ data: { ...d, mailIds: [...((d.mailIds as string[]) ?? []), mailId].slice(-50), log: [...((d.log as unknown[]) ?? []), { at: t2, by: "Mail", text: `Another mail ignored (${why}): this was removed as not a case` }].slice(-80) }, updated_at: new Date().toISOString() }).eq("collection", "cases").eq("id", same.id);
    return json({ ok: true, ignored: "removed as not a case", doc: same.id });
  }
  const known = new Set(((same?.data as Record<string, unknown>)?.fileHashes as string[]) ?? []);
  const docs: Record<string, unknown[]> = {};
  const hashes: string[] = [];
  let n = 0, skipped = 0;
  for (const { name, mime, bytes, hash } of items) {
    if (known.has(hash)) { skipped++; continue; }
    const f = { mime };
    const path = `mail/${t.slice(0, 7)}/${crypto.randomUUID()}/${name}`;
    const { error } = await admin.storage.from("docs").upload(path, bytes, { contentType: f.mime, upsert: false });
    if (error) continue;
    hashes.push(hash);
    const k = /supp/i.test(name) ? "modt2" : /noc|no.?objection|closure|release|foreclos/i.test(name) ? "noc" : /letter|sro/i.test(name) ? "letter" : (docs.modt ? "letter" : "modt");
    (docs[k] = docs[k] ?? []).push({ at: t, id: path, url: "sb:" + path, name, from: "mail", hash });
    n++;
  }
  if (same) {
    // deno-lint-ignore no-explicit-any
    const d = same.data as Record<string, any>; const t2 = new Date().toISOString().replace(/\.\d+Z$/, "Z");
    const nd = { ...(d.docs ?? {}) } as Record<string, unknown[]>;
    for (const [k, arr] of Object.entries(docs)) {
      if (k === "modt" && (nd.modt ?? []).length) nd.letter = [...(nd.letter ?? []), ...arr];
      else nd[k] = [...(nd[k] ?? []), ...arr];
    }
    const who2 = "Mail · " + String(body.from ?? "").slice(0, 200);
    const upd = { ...d, docs: nd, fileHashes: [...((d.fileHashes as string[]) ?? []), ...hashes].slice(-200), loanKeys: [...new Set([...((d.loanKeys as string[]) ?? []), ...loanKeys])].slice(0, 10), mailIds: [...((d.mailIds as string[]) ?? [d.mailId]), mailId].slice(-50), mailThread: d.mailThread || threadId, updatedAt: t2, mailNew: true,
      comments: [...((d.comments as unknown[]) ?? []), { at: t2, by: who2, text: `New mail for this case (${why}; ${n} new file(s)${skipped ? `, ${skipped} already here, not added again` : ""}): ${String(body.subject ?? "")}\n${String(body.text ?? "").slice(0, 1500)}`, internal: "true", from: "mail" }].slice(-200),
      log: [...((d.log as unknown[]) ?? []), { at: t2, by: who2, text: `Mail reply added (${n} file(s))` }].slice(-80) };
    if (!d.needsRead && docs.modt && !(d.docs as Record<string, unknown[]>)?.modt?.length) (upd as Record<string, unknown>).needsRead = "modt";
    // a case that had no MODT gets its new files sorted again (the bank may have replied with the MODT)
    if (n && d.noModt) Object.assign(upd, { classified: false, noModt: null, needsRead: null });
    const { error: ue } = await admin.from("kv").update({ data: upd, updated_at: new Date().toISOString() }).eq("collection", "cases").eq("id", same.id);
    if (ue) return json({ error: ue.message }, 500);
    return json({ ok: true, doc: same.id, added: n, skipped, joined: why });
  }
  if (!n) return json({ ok: true, skipped: "no attachments and no earlier case" });
  // case number OO-YYYYMMDD-NNNN: today's date + one running serial for every case (OO and VGR), never reset
  const { data: all } = await admin.from("kv").select("data->>id").eq("collection", "cases");
  const top = Math.max(0, ...((all ?? []) as Record<string, string>[]).map((r) => Number((String(Object.values(r)[0] ?? "").match(/^[A-Z]+-\d{8}-(\d+)$/) || [])[1] || 0)));
  const cid = `OO-${day}-${String(top + 1).padStart(4, "0")}`;
  const from = String(body.from ?? "").slice(0, 200), subject = String(body.subject ?? "").slice(0, 200);
  // forwarded mail: the bank's original sender is inside the text ("From: Name <x@bank.com>")
  const om = String(body.text ?? "").match(/From:\s*[^\n<]*<?\s*([\w.+-]+@[\w.-]+\.[a-z]{2,})/i);
  const origFrom = om ? om[1].toLowerCase() : "";
  const who = "Mail · " + from;
  const data = {
    id: cid, type: "Deed of Receipt", applicant: subject.slice(0, 80) || "(from mail)", applicantFromMail: true, loanNo: "", phone: "", bank: "", branch: "",
    state: "Tamil Nadu", stage: "Draft Pending", stageAt: t, createdAt: t, requestedAt: t, updatedAt: t,
    stageLog: [{ s: "Case Created", at: t, by: who }, { s: "Draft Pending", at: t, by: who }],
    log: [{ at: t, by: who, text: `Case created from e-mail (${n} attachment(s))` }],
    comments: [{ at: t, by: who, text: `E-mail from ${from}: ${subject}\n${String(body.text ?? "").slice(0, 1500)}`, internal: "true", from: "mail" }],
    docs, tp: {}, source: "mail", fileHashes: hashes, loanKeys, mailId, mailIds: [mailId], mailThread: threadId, mailSubjectKey: subjKey, mailLabels: labels, mailOrigFrom: origFrom, mailFrom: from, mailSubject: subject, needsRead: docs.modt ? "modt" : null,
  };
  const doc = crypto.randomUUID();
  const { error } = await admin.from("kv").insert({ collection: "cases", id: doc, data });
  if (error) return json({ error: error.message }, 500);
  return json({ ok: true, doc, id: cid, files: n });
}

// Query mails: new mail cases (from QUERY_FROM) that have no MODT; the mail script replies in the same conversation, then confirms.
const QUERY_FROM = "2026-11-10";
// deno-lint-ignore no-explicit-any
async function mailQueries(admin: any) {
  const { data } = await admin.from("kv").select("id,data").eq("collection", "cases").eq("data->>source", "mail").eq("data->>noModt", "true").gte("data->>createdAt", QUERY_FROM).limit(50);
  const out = (data ?? []).filter((r: { data: Record<string, unknown> }) => r.data.classified && !r.data.querySentAt && r.data.mailThread && r.data.stage === "Draft Pending")
    .map((r: { id: string; data: Record<string, unknown> }) => ({ doc: r.id, id: r.data.id, thread: r.data.mailThread, subject: r.data.mailSubject }));
  return json({ ok: true, queries: out });
}
// deno-lint-ignore no-explicit-any
async function mailQueryDone(admin: any, doc: string, how: string) {
  const { data: r } = await admin.from("kv").select("data").eq("collection", "cases").eq("id", doc).maybeSingle();
  if (!r) return json({ error: "no such case" }, 404);
  const t = new Date().toISOString().replace(/\.\d+Z$/, "Z"), d = r.data as Record<string, unknown[]>;
  const text = how === "draft" ? "Query mail asking for the MODT copy is ready as a Gmail draft in this conversation (check and send it)." : "Query mail sent to the bank asking for the registered MODT copy.";
  await admin.from("kv").update({ data: { ...d, querySentAt: t, queryHow: how, comments: [...(d.comments ?? []), { at: t, by: "Mail (auto)", text, internal: "true", from: "mail" }].slice(-200) }, updated_at: new Date().toISOString() }).eq("collection", "cases").eq("id", doc);
  return json({ ok: true });
}

// reply hit the length limit (cut off) - counted in the cost report
const out_cut = (u: any, kind: string) => Number(u?.output_tokens ?? 0) >= (kind === "modt_case" ? 16000 : kind === "modt" ? 6000 : 3000) - 5;
// Usage log (who ran Claude reading, how much): kv collection "usage", one row per reading, for the reports.
// deno-lint-ignore no-explicit-any
async function logUsage(sb: any, kind: string, images: number, usage: any, model = "", ref = "") {
  const { data: u } = await sb.auth.getUser();
  const email = String(u?.user?.email ?? "").toLowerCase();
  const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
  const { data: cfg } = await admin.from("kv").select("data").eq("collection", "config").eq("id", "lists").maybeSingle();
  const st = (((cfg?.data as { staff?: unknown[] })?.staff ?? []) as Record<string, string>[]).find((x) => typeof x === "object" && String(x.email ?? "").toLowerCase() === email);
  const inT = Number(usage?.input_tokens ?? 0), outT = Number(usage?.output_tokens ?? 0);
  const cheap = /haiku/i.test(model);
  const usd = cheap ? (inT * 1 + outT * 5) / 1e6 : (inT * 3 + outT * 15) / 1e6; // approximate list price per million tokens
  await admin.from("kv").insert({ collection: "usage", id: crypto.randomUUID(), data: {
    at: new Date().toISOString(), email, name: st?.name ?? "", org: st?.partner || st?.team || "vgr", kind, images, model, inTok: inT, outTok: outT, ref, cut: out_cut(usage, kind), usd: Math.round(usd * 10000) / 10000 } });
}

// company logos: Admin only. From the company's website (server side, no browser CORS limits) or an uploaded picture,
// saved in the public "logos" bucket as logos/<slug> so the bank login can show it without signing anything.
const logoSlug = (b: string) => String(b || "").split(" - ")[0].toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
// deno-lint-ignore no-explicit-any
async function logo(role: string, body: any) {
  if (role !== "md") return json({ error: "Only the Admin can change logos" }, 403);
  const slug = logoSlug(body.bank);
  if (!slug) return json({ error: "No company" }, 400);
  const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
  const { data: b } = await admin.storage.getBucket("logos");
  if (!b) await admin.storage.createBucket("logos", { public: true });
  let bytes: Uint8Array | null = null, type = "", from = "";
  const UA = { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept": "text/html,image/*,*/*" };
  const grab = async (u: string) => {
    try {
      const r = await fetch(u, { headers: UA, redirect: "follow" });
      const t = (r.headers.get("content-type") || "").toLowerCase();
      const buf = new Uint8Array(await r.arrayBuffer());
      const svg = t.includes("svg") || /^\s*<(\?xml|svg)/.test(new TextDecoder().decode(buf.slice(0, 200)));
      if (r.ok && (t.startsWith("image/") || svg) && buf.length > 300) return { buf, t: svg ? "image/svg+xml" : t.split(";")[0] };
    } catch (_) { /* next */ }
    return null;
  };
  if (typeof body.dataUrl === "string" && body.dataUrl.startsWith("data:image/")) {
    const m = body.dataUrl.match(/^data:(image\/[\w+.-]+);base64,(.+)$/);
    if (!m) return json({ error: "Bad picture" }, 400);
    bytes = Uint8Array.from(atob(m[2]), (c) => c.charCodeAt(0)); type = m[1]; from = "uploaded";
  } else {
    const site = String(body.site || "").replace(/\/$/, "");
    const cands: string[] = [];
    if (body.url) cands.push(String(body.url));
    if (site) {
      try {
        const r = await fetch(site, { headers: UA, redirect: "follow" });
        const base = r.url || site, html = (await r.text()).slice(0, 400000);
        const abs = (x: string) => { try { return new URL(x.replace(/&amp;/g, "&"), base).href; } catch (_) { return ""; } };
        const imgs = [...html.matchAll(/<img\b[^>]*>/gi)].map((m) => m[0]).filter((t) => /logo/i.test(t));
        for (const t of imgs.slice(0, 6)) {
          const src = (t.match(/\s(?:data-src|src)\s*=\s*["']([^"']+)["']/i) || [])[1];
          if (src && !src.startsWith("data:")) cands.push(abs(src));
        }
        const links = [...html.matchAll(/<link\b[^>]*>/gi)].map((m) => m[0]);
        for (const re of [/apple-touch-icon/i, /rel=["'][^"']*icon/i]) {
          const l = links.find((x) => re.test(x));
          const h = l && (l.match(/href\s*=\s*["']([^"']+)["']/i) || [])[1];
          if (h) cands.push(abs(h));
        }
        const og = (html.match(/<meta[^>]+property=["']og:image["'][^>]*content=["']([^"']+)["']/i) || [])[1];
        if (og) cands.push(abs(og));
      } catch (_) { /* site not reachable */ }
      cands.push(`https://t3.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=${encodeURIComponent(site)}&size=256`);
    }
    for (const u of cands.filter(Boolean)) {
      const g = await grab(u);
      if (g) { bytes = g.buf; type = g.t; from = u; break; }
    }
  }
  if (!bytes) return json({ ok: false, error: "No logo found" });
  const { error } = await admin.storage.from("logos").upload(slug, bytes, { contentType: type, upsert: true, cacheControl: "300" });
  if (error) return json({ error: error.message }, 500);
  return json({ ok: true, slug, from, type, size: bytes.length, url: `${Deno.env.get("SUPABASE_URL")}/storage/v1/object/public/logos/${slug}` });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  try {
    if (new URL(req.url).searchParams.get("mail") === "1") return await mailIn(await req.json());
    const auth = req.headers.get("Authorization") ?? "";
    const sb = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_ANON_KEY")!, { global: { headers: { Authorization: auth } } });
    const { data: role } = await sb.rpc("my_role");
    if (!role) return json({ error: "Not allowed" }, 403);
    const body = await req.json();
    if (body?.kind === "invite") return await invite(sb, role, body.email, body.link);
    if (body?.kind === "client_file") return await clientFile(sb, role, body.doc, body.url);
    if (body?.kind === "logo") return await logo(role, body);
    if (role === "client") return json({ error: "Not allowed" }, 403);
    const key = Deno.env.get("ANTHROPIC_API_KEY");
    if (!key) return json({ error: "ANTHROPIC_API_KEY is not set" }, 500);
    const { images, kind = "aadhaar", hints = [], texts } = body;
    let PROMPT = PROMPTS[kind];
    if (!PROMPT) return json({ error: "Unknown kind" }, 400);
    if (kind === "modt_case") PROMPT += `\n\nKEEP THE REPLY SHORT: leave out every key whose value would be "" (empty), and do not repeat the same text in two keys. Start the reply with { and end with } – no words before or after.`;
    if (["modt", "modt_case", "aadhaar"].includes(kind)) PROMPT += `\n\nALSO add the key "doubts": a list (empty [] if none) of every value you are NOT fully sure of: unclear or faded print, a spelling that differs between documents or between the English and Tamil, a digit you could not read clearly, two possible answers. Each item: {"field":"the JSON key above it belongs to (for a co-mortgagor write co_mortgagors.N.key, N from 0)","read":"the value you gave","options":["the other likely value(s)"],"why":"very short reason","img":number of the image (or [Page N]) it is printed on, 1 = first,"box":[left,top,right,bottom] as fractions 0 to 1 of that image's width and height, tightly around the exact words}. Only real doubts; do not list values you are sure of.`;
    if (Array.isArray(hints) && hints.length) {
      const h = hints.slice(0, 40).map((x: unknown) => String(x).slice(0, 200)).join("\n");
      if (kind === "english") PROMPT += `\n\nEnglish spellings already read from the MODT for this case. When the same name or place appears, use exactly these spellings:\n${h}`;
      else PROMPT += `\n\nOur staff corrected these readings before (field: "what was read" → "correct value"). They show the spellings and formats this office uses (SRO names, villages, company names). When you see the same text, use the corrected form; do not copy these values into other fields:\n${h}`;
    }
    let content: unknown[] = [];
    if (kind === "english" || kind === "tamil") {
      if (!texts || typeof texts !== "object" || !Object.keys(texts).length) return json({ error: "Send texts" }, 400);
      const t: Record<string, string> = {};
      for (const [k, v] of Object.entries(texts).slice(0, 40)) t[String(k).slice(0, 60)] = String(v).slice(0, 1500);
      content.push({ type: "text", text: PROMPT + "\n\nFields:\n" + JSON.stringify(t, null, 1) });
    } else if (kind === "modt" && typeof body.pdfText === "string" && body.pdfText.length > 600 && (!Array.isArray(images) || !images.length)) {
      // the MODT PDF already has its text inside: send the text, not page photos (much cheaper)
      content.push({ type: "text", text: "MODT DOCUMENT TEXT (taken from the PDF):\n" + body.pdfText.slice(0, 60000) });
      content.push({ type: "text", text: PROMPT });
    } else {
      const max = kind === "modt_case" || kind === "classify" ? 20 : kind === "modt" ? 14 : 10;
      if (!Array.isArray(images) || !images.length || images.length > max) return json({ error: `Send 1-${max} images` }, 400);
      const labels: string[] = Array.isArray(body.labels) ? body.labels : [];
      if (kind === "modt_case" && body.draft) content.push({ type: "text", text: "MODT DRAFT (Word text):\n" + String(body.draft).slice(0, 30000) });
      images.forEach((d: string, i: number) => {
        const m = /^data:(image\/(?:jpeg|png|webp));base64,(.+)$/.exec(d);
        if (!m) throw new Error("Bad image");
        if (labels[i] && labels[i] !== labels[i - 1]) content.push({ type: "text", text: String(labels[i]).slice(0, 80) + ":" });
        content.push({ type: "image", source: { type: "base64", media_type: m[1], data: m[2] } });
      });
      content.push({ type: "text", text: PROMPT });
    }
    // sorting files and translation use the cheaper model; ID cards (Aadhaar) and MODT reading keep the stronger one for accuracy
    const model = ["classify", "english", "tamil"].includes(kind) ? (Deno.env.get("CHEAP_MODEL") ?? "claude-haiku-5-5") : (Deno.env.get("AADHAAR_MODEL") ?? "claude-sonnet-5-5");
    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: { "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json" },
      body: JSON.stringify({ model, max_tokens: kind === "modt_case" ? 16000 : kind === "modt" ? 6000 : 3000, messages: [{ role: "user", content }] }),
    });
    const out = await r.json();
    if (!r.ok) return json({ error: out?.error?.message ?? "Claude error" }, 502);
    try { await logUsage(sb, kind, Array.isArray(images) ? images.length : 0, out?.usage, model, String(body?.ref ?? "").slice(0, 160)); } catch (_) { /* usage log never blocks reading */ }
    const text = (out.content ?? []).map((c: { text?: string }) => c.text ?? "").join("");
    const m = text.match(/\{[\s\S]*\}/);
    if (m) { try { return json({ fields: JSON.parse(m[0]) }); } catch (_) { /* cut short: mend below */ } }
    // the reply was cut off (too long): keep every complete "key": value pair and close the object
    const start = text.indexOf("{");
    if (start >= 0) {
      let body = text.slice(start);
      for (let cut = body.length; cut > 1; cut = body.lastIndexOf(",", cut - 1)) {
        if (cut < 0) break;
        try { const f = JSON.parse(body.slice(0, cut) + "}"); f.Doubts = (f.Doubts ? f.Doubts + " | " : "") + "Claude's reply was cut short – some fields may be empty, fill them by hand."; return json({ fields: f, cut: true }); } catch (_) { /* try a shorter piece */ }
      }
    }
    return json({ error: out?.stop_reason === "max_tokens" ? "Claude's reply was too long and got cut off" : "No JSON in reply" }, 502);
  } catch (e) {
    return json({ error: String((e as Error).message ?? e) }, 500);
  }
});
