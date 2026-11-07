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
  aadhaar: `You are reading an Indian Aadhaar card (front/back photo, or the e-Aadhaar letter). Return ONLY a JSON object with these keys (use "" when not printed):
{"name":"English name exactly as printed","name_ta":"Tamil name exactly as printed in Tamil script","rel_type":"S/O, W/O, D/O or C/O as printed in the address","rel_name":"relative's name in English","rel_name_ta":"relative's name in Tamil script","dob":"DD-MM-YYYY, or YYYY if only year of birth","gender":"Male or Female","aadhaar":"12-digit Aadhaar number, digits only (NOT the 16-digit VID, NOT the enrolment number)","address_en":"full English address after the relation part, as one line, ending with the pincode","address_ta":"full Tamil address after the relation part, as one line","district":"district from the English address","state":"state from the English address","pincode":"6-digit pincode","mobile":"10-digit mobile if printed"}
Copy text exactly; do not translate or guess. If Tamil is not printed, leave the Tamil keys empty.`,
  modt: `These are pages of a registered Tamil Nadu MODT (Memorandum of Deposit of Title Deeds) copy: usually an SRO registration receipt/endorsement page (Tamil + English) and the deed pages. Return ONLY a JSON object with these keys (use "" when not found):
{"sro":"Sub Registrar Office name in English (e.g. Rasipuram)","sro_ta":"SRO name in Tamil","book":"book number (usually 1)","doc_no":"registered document number","year":"registration year (4 digits)","reg_date":"registration date DD-MM-YYYY",
"mortgagor_sal":"Mr, Mrs or Ms","mortgagor":"mortgagor/borrower full name in English","mortgagor_ta":"mortgagor name in Tamil if printed","age":"age stated in the deed (number only)","rel_type":"S/O, W/O, D/O or C/O","rel_name":"relative's name in English","aadhaar":"mortgagor's 12-digit Aadhaar, digits only","address":"mortgagor address in English as one line","pincode":"6-digit pincode of that address","mobile":"mortgagor mobile if printed",
"co_mortgagors":[{"sal":"Mr, Mrs or Ms","name":"English name","name_ta":"Tamil name exactly as printed","age":"age number","rel_type":"S/O, W/O, D/O or C/O","rel_name":"relative's name in English"}],"bank":"mortgagee bank / finance company full name","bank_branch":"lender branch","loan_amount":"loan amount in rupees, digits only","loan_account":"loan account number if printed",
"property_district":"district of the property","property_sro":"SRO of the property in English","village":"village of the property in English","survey_no":"survey number(s) with subdivision","extent":"extent with unit as printed","north":"north boundary","east":"east boundary","west":"west boundary","south":"south boundary"}
Copy text exactly as printed; do not guess. If there are several mortgagors / borrowers / co-borrowers, give the first one in the mortgagor fields and EVERY other one, in the order printed, in co_mortgagors (empty list if only one). Never skip a mortgagor. mortgagor_ta must be the Tamil spelling exactly as printed in the document (never transliterate it yourself; leave it empty if no Tamil name is printed). The four boundaries are in the property schedule (often near the end, sometimes in Tamil: வடக்கு north, கிழக்கு east, மேற்கு west, தெற்கு south) - give each boundary text as printed (English if English is printed, otherwise the Tamil text). For property_sro and village give the English name as printed.`,
  english: `Below is a JSON object of text fields taken from a Tamil Nadu TNREGINET registration form. Most values are in Tamil script. For each key, give the English (Latin script) form that a Tamil Nadu document writer would type in the English field, and return ONLY a JSON object with the same keys.
Rules:
- Names: transliterate, do not translate. Tamil initials become English initials (மா. → M., இரா. → R., கே. → K.). Keep the same order and the same parts: if the Tamil has the father's name joined (e.g. ராமசாமி மாரிமுத்து), keep it joined (Ramasamy Marimuthu). Use the usual Tamil Nadu spelling (ராமசாமி → Ramasamy, முருகன் → Murugan, செல்வி → Selvi). Write names in Title Case.
- Addresses and property text: transliterate names of people, streets, villages, towns; translate the common words: தெரு Street, சாலை Road, நகர் Nagar, கிராமம் Village, வட்டம் Taluk, மாவட்டம் District, அஞ்சல் Post, கதவு எண் / எண் Door No., பிளாட் Plot, சர்வே எண் / புல எண் S.F.No., உட்பிரிவு Sub Division, ஏக்கர் Acres, ஹெக்டேர் Hectares, சதுர அடி Sq.ft, நிலம் land, வீடு house, காலி மனை vacant site, பொது வழி / பாதை common pathway, ரோடு Road, வாய்க்கால் channel, புறம்போக்கு poramboke. Keep all numbers exactly.
- Boundaries (keys mentioning north/south/east/west/boundary, or text that starts with வடக்கு/தெற்கு/கிழக்கு/மேற்கு): give ONLY the boundary description, e.g. "வடக்கு - ராமசாமி நிலம்" → "Ramasamy land". Never add the direction word, never add slashes or dashes.
- If a value is already English, return it unchanged. Do not add anything that is not in the Tamil text.`,
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  try {
    const auth = req.headers.get("Authorization") ?? "";
    const sb = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_ANON_KEY")!, { global: { headers: { Authorization: auth } } });
    const { data: role } = await sb.rpc("my_role");
    if (!role) return json({ error: "Not allowed" }, 403);
    const key = Deno.env.get("ANTHROPIC_API_KEY");
    if (!key) return json({ error: "ANTHROPIC_API_KEY is not set" }, 500);
    const { images, kind = "aadhaar", hints = [], texts } = await req.json();
    let PROMPT = PROMPTS[kind];
    if (!PROMPT) return json({ error: "Unknown kind" }, 400);
    if (Array.isArray(hints) && hints.length) {
      const h = hints.slice(0, 40).map((x: unknown) => String(x).slice(0, 200)).join("\n");
      if (kind === "english") PROMPT += `\n\nEnglish spellings already read from the MODT for this case. When the same name or place appears, use exactly these spellings:\n${h}`;
      else PROMPT += `\n\nOur staff corrected these readings before (field: "what was read" → "correct value"). They show the spellings and formats this office uses (SRO names, villages, company names). When you see the same text, use the corrected form; do not copy these values into other fields:\n${h}`;
    }
    let content: unknown[] = [];
    if (kind === "english") {
      if (!texts || typeof texts !== "object" || !Object.keys(texts).length) return json({ error: "Send texts" }, 400);
      const t: Record<string, string> = {};
      for (const [k, v] of Object.entries(texts).slice(0, 40)) t[String(k).slice(0, 60)] = String(v).slice(0, 1500);
      content.push({ type: "text", text: PROMPT + "\n\nFields:\n" + JSON.stringify(t, null, 1) });
    } else {
      if (!Array.isArray(images) || !images.length || images.length > 10) return json({ error: "Send 1-10 images" }, 400);
      content = images.map((d: string) => {
        const m = /^data:(image\/(?:jpeg|png|webp));base64,(.+)$/.exec(d);
        if (!m) throw new Error("Bad image");
        return { type: "image", source: { type: "base64", media_type: m[1], data: m[2] } };
      });
      content.push({ type: "text", text: PROMPT });
    }
    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: { "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json" },
      body: JSON.stringify({ model: Deno.env.get("AADHAAR_MODEL") ?? "claude-sonnet-5-5", max_tokens: 2000, messages: [{ role: "user", content }] }),
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
