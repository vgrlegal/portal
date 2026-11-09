-- Login status (Invited / Active, last login) for the Admin, and daily time on the portal for everyone.
create or replace function public.login_status() returns table(email text, invited timestamptz, last_login timestamptz) language sql stable security definer set search_path = public, auth as $$
  select lower(u.email), coalesce(u.invited_at, u.created_at), u.last_sign_in_at from auth.users u
  where my_role() = 'md' or (partner_org() is not null and exists (select 1 from kv, jsonb_array_elements(coalesce(kv.data->'staff','[]'::jsonb)) s
    where kv.collection='config' and kv.id='lists' and jsonb_typeof(s)='object' and lower(s->>'email')=lower(u.email) and s->>'team'=partner_org()))
$$;
grant execute on function public.login_status() to authenticated;

-- one row per person per day (India time); a ping every minute while the portal is open adds a minute
create or replace function public.ping_presence() returns void language plpgsql security definer set search_path = public as $$
declare em text := lower(auth.jwt()->>'email'); d text := to_char(now() at time zone 'Asia/Kolkata','YYYY-MM-DD'); k text; cur jsonb; org text; nm text;
begin
  if em is null or my_role() is null or my_role() = 'client' then return; end if;
  k := em || '|' || d;
  select s->>'name', coalesce(s->>'team', s->>'partner') into nm, org from kv, jsonb_array_elements(coalesce(kv.data->'staff','[]'::jsonb)) s
    where kv.collection='config' and kv.id='lists' and jsonb_typeof(s)='object' and lower(s->>'email')=em limit 1;
  select data into cur from kv where collection='presence' and id=k;
  if cur is null then
    insert into kv(collection,id,data) values('presence',k,jsonb_build_object('email',em,'name',coalesce(nm,em),'org',coalesce(org,''),'date',d,'first',now(),'last',now(),'minutes',1,'sessions',1));
  else
    update kv set data = data || jsonb_build_object('last',now(),'name',coalesce(nm,em),'org',coalesce(org,''),
      'minutes',(data->>'minutes')::int + case when now() - (data->>'last')::timestamptz < interval '3 minutes' then 1 else 0 end,
      'sessions',(data->>'sessions')::int + case when now() - (data->>'last')::timestamptz >= interval '3 minutes' then 1 else 0 end), updated_at = now()
    where collection='presence' and id=k;
  end if;
end $$;
grant execute on function public.ping_presence() to authenticated;

-- staff read their own day rows; a partner also reads his team's
alter policy kv_read on public.kv using (my_role() = 'md' or (my_role() = 'staff' and (collection in ('config', 'tp_jobs')
  or (collection = 'cases' and data->>'type' = any (staff_tabs()) and (cardinality(staff_banks()) = 0 or data->>'bank' = any (staff_banks())))
  or (collection = 'usage' and data->>'org' = partner_org())
  or (collection = 'presence' and (lower(data->>'email') = lower(auth.jwt()->>'email') or (partner_org() is not null and data->>'org' = partner_org()))))));
