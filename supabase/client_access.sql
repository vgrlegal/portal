-- VGR Legal: Client (bank) logins. A client sees ONLY its own company's Deed of Receipt cases (or one branch),
-- never internal comments, logs, staff or settings. Everything goes through these functions; clients have no direct table access.

create or replace function public.my_role() returns text language sql stable security definer set search_path = public as $$
  select case
    when exists (select 1 from md_emails where lower(email) = lower(auth.jwt()->>'email')) then 'md'
    when exists (select 1 from kv, jsonb_array_elements(coalesce(kv.data->'staff','[]'::jsonb)) s
                 where kv.collection = 'config' and kv.id = 'lists' and jsonb_typeof(s) = 'object'
                   and lower(s->>'email') = lower(auth.jwt()->>'email') and coalesce(s->>'email','') <> '') then 'staff'
    when exists (select 1 from kv, jsonb_array_elements(coalesce(kv.data->'clients','[]'::jsonb)) s
                 where kv.collection = 'config' and kv.id = 'lists' and jsonb_typeof(s) = 'object'
                   and lower(s->>'email') = lower(auth.jwt()->>'email') and coalesce(s->>'email','') <> '') then 'client'
    else null end
$$;

create or replace function public.client_me() returns jsonb language sql stable security definer set search_path = public as $$
  select s from kv, jsonb_array_elements(coalesce(kv.data->'clients','[]'::jsonb)) s
  where kv.collection = 'config' and kv.id = 'lists' and jsonb_typeof(s) = 'object'
    and lower(s->>'email') = lower(auth.jwt()->>'email') and coalesce(s->>'email','') <> '' limit 1
$$;

create or replace function public.client_key() returns text language sql stable as $$ select md5(lower(auth.jwt()->>'email')) $$;

-- which cases a client may see
create or replace function public.client_owns(d jsonb) returns boolean language sql stable security definer set search_path = public as $$
  select coalesce((select (d->>'bank') = (m->>'company') and (coalesce(m->>'branch','') = '' or lower(d->>'branch') = lower(m->>'branch'))
                          and (d->>'type') = 'Deed of Receipt'
                   from (select client_me() m) x), false)
$$;

create or replace function public.client_view(id text, d jsonb) returns jsonb language sql stable as $$
  select jsonb_build_object(
    'doc', id, 'id', d->>'id', 'applicant', d->>'applicant', 'loanNo', d->>'loanNo', 'phone', d->>'phone',
    'bank', d->>'bank', 'branch', d->>'branch', 'type', d->>'type', 'stage', d->>'stage', 'stageAt', d->>'stageAt',
    'requestedAt', d->>'requestedAt', 'createdAt', d->>'createdAt', 'tpNo', d->>'tpNo', 'docNo', d->>'docNo',
    'refuseReason', d->>'refuseReason', 'source', d->>'source',
    'docs', coalesce((select jsonb_object_agg(k, v) from jsonb_each(coalesce(d->'docs','{}'::jsonb)) e(k, v)
                      where k in ('modt','modt2','noc','revenue','tpcopy','regcopy')), '{}'::jsonb),
    'comments', coalesce((select jsonb_agg(c) from jsonb_array_elements(coalesce(d->'comments','[]'::jsonb)) c
                          where c->>'internal' = 'false'), '[]'::jsonb),
    'queries', coalesce(d->'queries', '[]'::jsonb))
$$;

create or replace function public.client_cases() returns jsonb language plpgsql stable security definer set search_path = public as $$
begin
  if my_role() <> 'client' then raise exception 'not a client login'; end if;
  return coalesce((select jsonb_agg(client_view(id, data) order by data->>'requestedAt' desc)
                   from kv where collection = 'cases' and client_owns(data)), '[]'::jsonb);
end $$;

create or replace function public.client_new_case(p jsonb) returns text language plpgsql security definer set search_path = public as $$
declare m jsonb := client_me(); doc text := gen_random_uuid()::text; day text := to_char(now() at time zone 'Asia/Kolkata', 'YYYYMMDD');
        n int; cid text; t text := to_char(now() at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'); who text;
begin
  if my_role() <> 'client' or m is null then raise exception 'not a client login'; end if;
  if coalesce(trim(p->>'applicant'),'') = '' then raise exception 'customer name is needed'; end if;
  who := coalesce(m->>'name', auth.jwt()->>'email') || ' (bank)';
  select coalesce(max((substring(data->>'id' from '-(\d+)$'))::int), 0) + 1 into n from kv where collection = 'cases' and data->>'id' like '%-' || day || '-%';
  cid := 'OO-' || day || '-' || lpad(n::text, 3, '0');
  insert into kv(collection, id, data) values ('cases', doc, jsonb_build_object(
    'id', cid, 'type', 'Deed of Receipt', 'applicant', trim(p->>'applicant'), 'loanNo', coalesce(p->>'loanNo',''),
    'phone', coalesce(p->>'phone',''), 'bank', m->>'company',
    'branch', case when coalesce(m->>'branch','') <> '' then m->>'branch' else coalesce(p->>'branch','') end,
    'state', 'Tamil Nadu', 'stage', 'Draft Pending', 'stageAt', t, 'createdAt', t, 'requestedAt', t, 'updatedAt', t,
    'stageLog', jsonb_build_array(jsonb_build_object('s','Case Created','at',t,'by',who), jsonb_build_object('s','Draft Pending','at',t,'by',who)),
    'log', jsonb_build_array(jsonb_build_object('at',t,'by',who,'text','Case filed by the bank')),
    'comments', jsonb_build_array(jsonb_build_object('at',t,'by',who,'text','Case filed by the bank','internal','false','from','client')),
    'docs', '{}'::jsonb, 'tp', '{}'::jsonb, 'source', 'client', 'clientEmail', lower(auth.jwt()->>'email')));
  return doc;
end $$;

create or replace function public.client_add_doc(doc text, k text, f jsonb) returns boolean language plpgsql security definer set search_path = public as $$
declare d jsonb; who text := coalesce(client_me()->>'name', auth.jwt()->>'email') || ' (bank)'; t text := to_char(now() at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"');
        lbl text := case k when 'modt' then 'MODT' when 'modt2' then 'Supplementary MODT' when 'noc' then 'NOC' when 'revenue' then 'Revenue document' else k end;
begin
  if my_role() <> 'client' then raise exception 'not a client login'; end if;
  if k not in ('modt','modt2','noc','revenue') then raise exception 'not allowed'; end if;
  if (f->>'url') not like 'sb:client/' || client_key() || '/%' then raise exception 'bad file'; end if;
  select data into d from kv where collection = 'cases' and id = doc for update;
  if d is null or not client_owns(d) then raise exception 'case not found'; end if;
  d := jsonb_set(d, array['docs'], coalesce(d->'docs','{}'::jsonb) || jsonb_build_object(k, coalesce(d->'docs'->k,'[]'::jsonb) ||
        jsonb_build_array(jsonb_build_object('name', left(f->>'name',150), 'url', f->>'url', 'at', t, 'by', who))));
  d := jsonb_set(d, array['comments'], coalesce(d->'comments','[]'::jsonb) || jsonb_build_array(jsonb_build_object('at',t,'by',who,
        'text','Uploaded ' || lbl || ': ' || left(f->>'name',150),'internal','false','from','client')));
  if k in ('modt','modt2') then d := d || jsonb_build_object('needsRead', k); end if;
  d := d || jsonb_build_object('updatedAt', t);
  update kv set data = d, updated_at = now() where collection = 'cases' and id = doc;
  return true;
end $$;

create or replace function public.client_comment(doc text, msg text, qid text default null) returns boolean language plpgsql security definer set search_path = public as $$
declare d jsonb; who text := coalesce(client_me()->>'name', auth.jwt()->>'email') || ' (bank)'; t text := to_char(now() at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"');
begin
  if my_role() <> 'client' then raise exception 'not a client login'; end if;
  if coalesce(trim(msg),'') = '' then return false; end if;
  select data into d from kv where collection = 'cases' and id = doc for update;
  if d is null or not client_owns(d) then raise exception 'case not found'; end if;
  d := jsonb_set(d, array['comments'], coalesce(d->'comments','[]'::jsonb) || jsonb_build_array(jsonb_build_object('at',t,'by',who,
        'text', left(msg, 2000),'internal','false','from','client')));
  if qid is not null then
    d := jsonb_set(d, array['queries'], (select coalesce(jsonb_agg(case when q->>'id' = qid then q || jsonb_build_object('status','answered','answer',left(msg,2000),'answeredAt',t,'answeredBy',who) else q end), '[]'::jsonb)
                                         from jsonb_array_elements(coalesce(d->'queries','[]'::jsonb)) q));
  end if;
  update kv set data = d || jsonb_build_object('updatedAt', t), updated_at = now() where collection = 'cases' and id = doc;
  return true;
end $$;

revoke all on function public.client_cases(), public.client_new_case(jsonb), public.client_add_doc(text,text,jsonb), public.client_comment(text,text,text) from anon;
grant execute on function public.client_cases(), public.client_new_case(jsonb), public.client_add_doc(text,text,jsonb), public.client_comment(text,text,text), public.client_me() to authenticated;

-- files: clients may only upload / read inside their own folder client/<key>/ (case files are opened through the server)
alter policy docs_read on storage.objects using (bucket_id = 'docs' and (my_role() in ('md','staff') or (my_role() = 'client' and name like 'client/' || client_key() || '/%')));
alter policy docs_insert on storage.objects with check (bucket_id = 'docs' and (my_role() in ('md','staff') or (my_role() = 'client' and name like 'client/' || client_key() || '/%')));

select 'client logins ready' as result;
