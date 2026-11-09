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
  modt_case: `You are preparing a Tamil Nadu TNREGINET "Deposit of Title Deeds" (MODT) entry for a law office. You get, each labelled: the bank's MODT DRAFT (Word text), the TITLE DEED pages (often Tamil), the title holders' AADHAAR/KYC pages, and PATTA / revenue pages. Read them and return ONLY one JSON object with exactly these keys (use "" when unknown):
{"Deed 1 SRO":"SRO of the 1st title deed, English","Deed 1 Doc No":"","Deed 1 Year":"4 digits","Deed 2 SRO":"","Deed 2 Doc No":"","Deed 2 Year":"","Deed 3 SRO":"","Deed 3 Doc No":"","Deed 3 Year":"",
"Title holders":"number of title holders / mortgagors, 1-4",
"Mortgagor 1 salutation":"", "Mortgagor 1 name":"", "Mortgagor 1 name Tamil":"", "Mortgagor 1 relation":"", "Mortgagor 1 relative name":"", "Mortgagor 1 relative name Tamil":"", "Mortgagor 1 date of birth":"", "Mortgagor 1 Aadhaar":"", "Mortgagor 1 district":"", "Mortgagor 1 address":"", "Mortgagor 1 address Tamil":"", "Mortgagor 1 pincode":"", "Mortgagor 1 phone":"", "Mortgagor 2 salutation":"", "Mortgagor 2 name":"", "Mortgagor 2 name Tamil":"", "Mortgagor 2 relation":"", "Mortgagor 2 relative name":"", "Mortgagor 2 relative name Tamil":"", "Mortgagor 2 date of birth":"", "Mortgagor 2 Aadhaar":"", "Mortgagor 2 district":"", "Mortgagor 2 address":"", "Mortgagor 2 address Tamil":"", "Mortgagor 2 pincode":"", "Mortgagor 2 phone":"", "Mortgagor 3 salutation":"", "Mortgagor 3 name":"", "Mortgagor 3 name Tamil":"", "Mortgagor 3 relation":"", "Mortgagor 3 relative name":"", "Mortgagor 3 relative name Tamil":"", "Mortgagor 3 date of birth":"", "Mortgagor 3 Aadhaar":"", "Mortgagor 3 district":"", "Mortgagor 3 address":"", "Mortgagor 3 address Tamil":"", "Mortgagor 3 pincode":"", "Mortgagor 3 phone":"", "Mortgagor 4 salutation":"", "Mortgagor 4 name":"", "Mortgagor 4 name Tamil":"", "Mortgagor 4 relation":"", "Mortgagor 4 relative name":"", "Mortgagor 4 relative name Tamil":"", "Mortgagor 4 date of birth":"", "Mortgagor 4 Aadhaar":"", "Mortgagor 4 district":"", "Mortgagor 4 address":"", "Mortgagor 4 address Tamil":"", "Mortgagor 4 pincode":"", "Mortgagor 4 phone":"",
"Govt Body Name":"mortgagee company full legal name","Company state":"state of the company's head office address","Company address":"head office address (and branch if the draft gives it), English","Company address Tamil":"same address in Tamil script","Company pincode":"","Branch":"lender branch name",
"Property type":"one of: Plot with building, Vacant plot, Agricultural land, Agricultural land with building","Property SRO":"SRO of the property, English","Village":"revenue village, English","Patta No":"from the patta if given","Land type":"one of: Urban, Rural, Natham","Unit":"Sq.ft or Sq.m","New survey no":"new survey number without subdivision, e.g. 125","Subdivision":"e.g. 3","Sheet name":"town survey ward/block/sheet of the PROPERTY from the title deed or patta, never from Aadhaar","Old survey numbers":"comma separated, e.g. 55/4, 10/2","Extent":"extent with unit exactly as in the MODT draft",
"Boundary type":"Regular or Irregular","Boundary North":"English","Boundary South":"","Boundary East":"","Boundary West":"","Boundary North Tamil":"Tamil script","Boundary South Tamil":"","Boundary East Tamil":"","Boundary West Tamil":"",
"North length":"number only, in the Unit's linear measure","South length":"","East length":"","West length":"",
"Property remarks":"English, pattern: Situated in <village> village, <SRO> Sub-Registration District, Survey No. <new/sub> (Old S.No. <old>) as per patta, measuring an extent of <extent><, Plot No. x> – all the land therein.","Property remarks Tamil":"the same sentence in Tamil",
"Loan amount":"digits only, from the MODT draft","List of documents deposited":"the 'list of documents deposited' / schedule of title documents from the MODT draft, one per line, numbered as in the draft",
"Execution SRO":"SRO where the MODT is registered (normally the property SRO), English","SRO Tamil":"that SRO name in Tamil",
"Doubts":"short list of things you were unsure about, for the office to check"}
Rules: names, relation, date of birth (DD-MM-YYYY), Aadhaar number, district, address and pincode of each holder come from that holder's AADHAAR exactly as printed (English and Tamil); check them against the title deed. Mr./Mrs./Ms. as in the ID. Relation is S/o, W/o, D/o or C/o. Holder order as in the MODT draft. Loan amount, company address, extent, boundaries, linear measurements and list of documents come from the MODT DRAFT; compare with the title deed and mention differences in Doubts. Tamil boundary text from the title deed where available. Extent in cents/acres/hectares means agricultural land. Boundary type Regular only when North length = South length and East length = West length, else Irregular. Urban when the deed or patta shows Town Survey / T.S. No / Ward / Block; Natham when it says natham; otherwise Rural — if unsure, say so in Doubts. Never invent numbers; leave "" if not printed.`,
  aadhaar: `You are reading an Indian Aadhaar card (front/back photo, or the e-Aadhaar letter). Return ONLY a JSON object with these keys (use "" when not printed):
{"name":"English name exactly as printed","name_ta":"Tamil name exactly as printed in Tamil script","rel_type":"S/O, W/O, D/O or C/O as printed in the address","rel_name":"relative's name in English","rel_name_ta":"relative's name in Tamil script","dob":"DD-MM-YYYY, or YYYY if only year of birth","gender":"Male or Female","aadhaar":"12-digit Aadhaar number, digits only (NOT the 16-digit VID, NOT the enrolment number)","address_en":"full English address after the relation part, as one line, ending with the pincode","address_ta":"full Tamil address after the relation part, as one line","district":"district from the English address","state":"state from the English address","pincode":"6-digit pincode","mobile":"10-digit mobile if printed"}
Copy text exactly; do not translate or guess. If Tamil is not printed, leave the Tamil keys empty.`,
  modt: `These are pages of a registered Tamil Nadu MODT (Memorandum of Deposit of Title Deeds) copy: usually an SRO registration receipt/endorsement page (Tamil + English) and the deed pages. Return ONLY a JSON object with these keys (use "" when not found):
{"sro":"Sub Registrar Office name in English (e.g. Rasipuram)","sro_ta":"SRO name in Tamil","book":"book number (usually 1)","doc_no":"registered document number","year":"registration year (4 digits)","reg_date":"registration date DD-MM-YYYY",
"mortgagor_sal":"Mr, Mrs or Ms","mortgagor":"mortgagor/borrower full name in English","mortgagor_ta":"mortgagor name in Tamil if printed","age":"age stated in the deed (number only)","rel_type":"S/O, W/O, D/O or C/O","rel_name":"relative's name in English","aadhaar":"mortgagor's 12-digit Aadhaar, digits only","address":"mortgagor address in English as one line","pincode":"6-digit pincode of that address","mobile":"mortgagor mobile if printed",
"co_mortgagors":[{"sal":"Mr, Mrs or Ms","name":"English name","name_ta":"Tamil name exactly as printed","age":"age number","rel_type":"S/O, W/O, D/O or C/O","rel_name":"relative's name in English"}],"bank":"mortgagee bank / finance company full name","bank_branch":"lender branch","earlier_doc_no":"ONLY if this is a supplementary / additional / further-charge MODT that refers to an EARLIER deposit of title deeds for the same loan or property: that earlier document number, else empty","earlier_year":"year of that earlier document","earlier_sro":"SRO of that earlier document in English","earlier_book":"book of that earlier document (usually 1)","earlier_loan_amount":"loan amount secured by that earlier document, digits only, if printed","bank_address":"mortgagee's registered / corporate office address in English exactly as printed","bank_address_ta":"same address in Tamil exactly as printed, if printed","bank_state":"state of that corporate address","bank_district":"district of that corporate address","bank_pincode":"6-digit pincode of that corporate address","branch_address":"lender branch address in English exactly as printed","branch_address_ta":"branch address in Tamil exactly as printed, if printed","loan_amount":"loan amount in rupees, digits only","loan_account":"loan account number if printed",
"property_district":"district of the property","property_sro":"SRO of the property in English","village":"village of the property in English","survey_no":"survey number(s) with subdivision","extent":"extent with unit as printed","north":"north boundary","east":"east boundary","west":"west boundary","south":"south boundary"}
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
async function invite(role: unknown, email: unknown) {
  if (role !== "md") return json({ error: "Only the Admin can invite staff" }, 403);
  const em = String(email ?? "").trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(em)) return json({ error: "Not a valid email" }, 400);
  const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
  const { data: cfg } = await admin.from("kv").select("data").eq("collection", "config").eq("id", "lists").maybeSingle();
  const staff = ((cfg?.data as { staff?: unknown[] })?.staff ?? []) as { email?: string }[];
  const clients = ((cfg?.data as { clients?: unknown[] })?.clients ?? []) as { email?: string }[];
  const listed = (a: { email?: string }[]) => a.some((x) => typeof x === "object" && String(x.email ?? "").toLowerCase() === em);
  if (!listed(staff) && !listed(clients)) return json({ error: "Save this email in Clients & Staff first" }, 400);
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

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  try {
    const auth = req.headers.get("Authorization") ?? "";
    const sb = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_ANON_KEY")!, { global: { headers: { Authorization: auth } } });
    const { data: role } = await sb.rpc("my_role");
    if (!role) return json({ error: "Not allowed" }, 403);
    const body = await req.json();
    if (body?.kind === "invite") return await invite(role, body.email);
    if (body?.kind === "client_file") return await clientFile(sb, role, body.doc, body.url);
    if (role === "client") return json({ error: "Not allowed" }, 403);
    const key = Deno.env.get("ANTHROPIC_API_KEY");
    if (!key) return json({ error: "ANTHROPIC_API_KEY is not set" }, 500);
    const { images, kind = "aadhaar", hints = [], texts } = body;
    let PROMPT = PROMPTS[kind];
    if (!PROMPT) return json({ error: "Unknown kind" }, 400);
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
    } else {
      const max = kind === "modt_case" ? 20 : 10;
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
    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: { "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json" },
      body: JSON.stringify({ model: Deno.env.get("AADHAAR_MODEL") ?? "claude-sonnet-5-5", max_tokens: kind === "modt_case" ? 6000 : 2000, messages: [{ role: "user", content }] }),
    });
    const out = await r.json();
    if (!r.ok) return json({ error: out?.error?.message ?? "Claude error" }, 502);
    const text = (out.content ?? []).map((c: { text?: string }) => c.text ?? "").join("");
    const m = text.match(/\{[\s\S]*\}/);
    if (!m) return json({ error: "No JSON in reply" }, 502);
    return json({ fields: JSON.parse(m[0]) });
  } catch (e) {
    return json({ error: String((e as Error).message ?? e) }, 500);
  }
});
