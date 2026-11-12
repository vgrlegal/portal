-- VGR Legal (12 Nov 2026): client (bank) logins can file and follow all three services -
-- Deed of Receipt, MODT and Legal Report. A client may be limited to some services with "services" on its
-- entry in config.lists.clients (e.g. ["MODT"]); without it, all three are allowed.
-- Run once in the Supabase SQL editor. Replaces client_owns / client_view / client_new_case / client_add_doc.

create or replace function public.client_services() returns text[] language sql stable security definer set search_path = public as $$
  select coalesce((select array_agg(x) from jsonb_array_elements_text(
            case when jsonb_typeof(client_me()->'services') = 'array' and jsonb_array_length(client_me()->'services') > 0
                 then client_me()->'services' else '["Deed of Receipt","MODT","Legal Report"]'::jsonb end) x
          where x in ('Deed of Receipt','MODT','Legal Report')), array['Deed of Receipt'])
$$;

-- which cases a client may see: its company (and branch, if the login is for one branch), any of its services
create or replace function public.client_owns(d jsonb) returns boolean language sql stable security definer set search_path = public as $$
  select coalesce((select (d->>'bank') = (m->>'company') and (coalesce(m->>'branch','') = '' or lower(d->>'branch') = lower(m->>'branch'))
                          and (d->>'type') = any(client_services())
                   from (select client_me() m) x), false)
$$;

-- what a client sees of a case: no internal comments, logs, staff or settings; only the documents of its own kind
create or replace function public.client_view(id text, d jsonb) returns jsonb language sql stable as $$
  select jsonb_build_object(
    'doc', id, 'id', d->>'id', 'applicant', d->>'applicant', 'loanNo', d->>'loanNo', 'phone', d->>'phone',
    'bank', d->>'bank', 'branch', d->>'branch', 'type', d->>'type', 'stage', d->>'stage', 'stageAt', d->>'stageAt',
    'requestedAt', d->>'requestedAt', 'createdAt', d->>'createdAt', 'tpNo', d->>'tpNo', 'docNo', d->>'docNo',
    'refuseReason', d->>'refuseReason', 'source', d->>'source',
    'docs', coalesce((select jsonb_object_agg(k, v) from jsonb_each(coalesce(d->'docs','{}'::jsonb)) e(k, v)
                      where k in ('modt','modt2','noc','revenue','tpcopy','regcopy',
                                  'mdraft','deed','th_kyc','ec','photos','w1_kyc','w2_kyc','sanction','legal','other','report')), '{}'::jsonb),
    'comments', coalesce((select jsonb_agg(c) from jsonb_array_elements(coalesce(d->'comments','[]'::jsonb)) c
                          where c->>'internal' = 'false'), '[]'::jsonb),
    'queries', coalesce(d->'queries', '[]'::jsonb))
$$;

create or replace function public.client_new_case(p jsonb) returns text language plpgsql security definer set search_path = public as $$
declare m jsonb := client_me(); doc text := gen_random_uuid()::text; day text := to_char(now() at time zone 'Asia/Kolkata', 'YYYYMMDD');
        n int; cid text; t text := to_char(now() at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'); who text;
        typ text := coalesce(nullif(trim(p->>'type'),''), 'Deed of Receipt');
begin
  if my_role() <> 'client' or m is null then raise exception 'not a client login'; end if;
  if coalesce(trim(p->>'applicant'),'') = '' then raise exception 'customer name is needed'; end if;
  if not (typ = any(client_services())) then raise exception 'this service is not open for your login'; end if;
  who := coalesce(m->>'name', auth.jwt()->>'email') || ' (bank)';
  select coalesce(max((substring(data->>'id' from '^[A-Z]+-\d{8}-(\d+)$'))::int), 0) + 1 into n from kv where collection = 'cases';
  cid := 'OO-' || day || '-' || lpad(n::text, 4, '0');
  insert into kv(collection, id, data) values ('cases', doc, jsonb_build_object(
    'id', cid, 'type', typ, 'applicant', trim(p->>'applicant'), 'loanNo', coalesce(p->>'loanNo',''),
    'loanAmount', coalesce(p->>'loanAmount',''),
    'phone', coalesce(p->>'phone',''), 'bank', m->>'company',
    'branch', case when coalesce(m->>'branch','') <> '' then m->>'branch' else coalesce(p->>'branch','') end,
    'state', 'Tamil Nadu', 'stage', 'Draft Pending', 'stageAt', t, 'createdAt', t, 'requestedAt', t, 'updatedAt', t,
    'stageLog', jsonb_build_array(jsonb_build_object('s','Case Created','at',t,'by',who), jsonb_build_object('s','Draft Pending','at',t,'by',who)),
    'log', jsonb_build_array(jsonb_build_object('at',t,'by',who,'text', typ || ' case filed by the bank')),
    'comments', jsonb_build_array(jsonb_build_object('at',t,'by',who,'text', typ || ' case filed by the bank','internal','false','from','client')),
    'docs', '{}'::jsonb, 'tp', '{}'::jsonb, 'draft', '{}'::jsonb, 'source', 'client', 'clientEmail', lower(auth.jwt()->>'email')));
  return doc;
end $$;

create or replace function public.client_add_doc(doc text, k text, f jsonb) returns boolean language plpgsql security definer set search_path = public as $$
declare d jsonb; who text := coalesce(client_me()->>'name', auth.jwt()->>'email') || ' (bank)'; t text := to_char(now() at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"');
        lbl text := case k when 'modt' then 'MODT' when 'modt2' then 'Supplementary MODT' when 'noc' then 'NOC' when 'revenue' then 'Revenue document'
                           when 'mdraft' then 'MODT draft (Word copy)' when 'deed' then 'Title deed' when 'th_kyc' then 'Title holder Aadhaar'
                           when 'ec' then 'EC' when 'photos' then 'Property photo' when 'w1_kyc' then 'Witness 1 Aadhaar' when 'w2_kyc' then 'Witness 2 Aadhaar'
                           when 'sanction' then 'Sanction letter' when 'legal' then 'Legal report' when 'other' then 'Other document' else k end;
        typ text;
begin
  if my_role() <> 'client' then raise exception 'not a client login'; end if;
  if (f->>'url') not like 'sb:client/' || client_key() || '/%' then raise exception 'bad file'; end if;
  select data into d from kv where collection = 'cases' and id = doc for update;
  if d is null or not client_owns(d) then raise exception 'case not found'; end if;
  typ := d->>'type';
  if not ((typ = 'Deed of Receipt' and k in ('modt','modt2','noc','revenue'))
       or (typ = 'MODT' and k in ('mdraft','deed','th_kyc','revenue','ec','photos','w1_kyc','w2_kyc','sanction','legal','other'))
       or (typ = 'Legal Report' and k in ('deed','ec','revenue','other'))) then raise exception 'not allowed'; end if;
  d := jsonb_set(d, array['docs'], coalesce(d->'docs','{}'::jsonb) || jsonb_build_object(k, coalesce(d->'docs'->k,'[]'::jsonb) ||
        jsonb_build_array(jsonb_build_object('name', left(f->>'name',150), 'url', f->>'url', 'at', t, 'by', who))));
  d := jsonb_set(d, array['comments'], coalesce(d->'comments','[]'::jsonb) || jsonb_build_array(jsonb_build_object('at',t,'by',who,
        'text','Uploaded ' || lbl || ': ' || left(f->>'name',150),'internal','false','from','client')));
  -- Deed of Receipt: the MODT copy is read by Claude automatically (MODT cases are read from Drafting by the office)
  if typ = 'Deed of Receipt' and k in ('modt','modt2') then d := d || jsonb_build_object('needsRead', k); end if;
  d := d || jsonb_build_object('updatedAt', t);
  update kv set data = d, updated_at = now() where collection = 'cases' and id = doc;
  return true;
end $$;

revoke all on function public.client_services() from anon;
grant execute on function public.client_services() to authenticated;

select 'client services ready: ' || array_to_string(array['Deed of Receipt','MODT','Legal Report'], ', ') as result;
