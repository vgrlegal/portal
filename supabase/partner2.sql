-- Team members may be limited to some banks (Raja ticks them in My team); usage log readable by the partner for his team.
create or replace function public.staff_banks() returns text[] language sql stable security definer set search_path = public as $$
  select coalesce(array_agg(b), '{}') from kv, jsonb_array_elements(coalesce(kv.data->'staff','[]'::jsonb)) s,
    jsonb_array_elements_text(case when jsonb_typeof(s->'banks')='array' then s->'banks' else '[]'::jsonb end) b
  where kv.collection='config' and kv.id='lists' and jsonb_typeof(s)='object' and lower(s->>'email') = lower(auth.jwt()->>'email')
$$;
alter policy kv_read on public.kv using (my_role() = 'md' or (my_role() = 'staff' and (collection in ('config', 'tp_jobs')
  or (collection = 'cases' and data->>'type' = any (staff_tabs()) and (cardinality(staff_banks()) = 0 or data->>'bank' = any (staff_banks())))
  or (collection = 'usage' and data->>'org' = partner_org()))));
alter policy kv_update on public.kv using (my_role() = 'md' or (my_role() = 'staff' and (collection = 'tp_jobs'
  or (collection = 'cases' and data->>'type' = any (staff_tabs()) and (cardinality(staff_banks()) = 0 or data->>'bank' = any (staff_banks()))))));
create or replace function public.partner_save_team(members jsonb) returns int language plpgsql security definer set search_path = public as $$
declare org text := partner_org(); cur jsonb; keep jsonb; add jsonb; taken text[];
begin
  if org is null then raise exception 'only a partner can manage a team'; end if;
  select data into cur from kv where collection='config' and id='lists' for update;
  select coalesce(jsonb_agg(s),'[]'::jsonb) into keep from jsonb_array_elements(coalesce(cur->'staff','[]'::jsonb)) s
    where jsonb_typeof(s) <> 'object' or coalesce(s->>'team','') <> org;
  select coalesce(array_agg(lower(e)),'{}') into taken from (
    select s->>'email' e from jsonb_array_elements(keep) s where jsonb_typeof(s)='object'
    union select c->>'email' from jsonb_array_elements(coalesce(cur->'clients','[]'::jsonb)) c
    union select email from md_emails) x where coalesce(e,'') <> '';
  select coalesce(jsonb_agg(jsonb_build_object('name', trim(m->>'name'), 'email', lower(trim(m->>'email')), 'phone', coalesce(m->>'phone',''),
      'tabs', jsonb_build_array('Deed of Receipt'), 'tp', coalesce((m->>'tp')::boolean, false), 'team', org,
      'banks', case when jsonb_typeof(m->'banks')='array' then m->'banks' else '[]'::jsonb end)), '[]'::jsonb) into add
    from jsonb_array_elements(members) m
    where trim(coalesce(m->>'name','')) <> '' and lower(trim(coalesce(m->>'email',''))) ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'
      and not (lower(trim(m->>'email')) = any(taken));
  update kv set data = jsonb_set(data, '{staff}', keep || add), updated_at = now() where collection='config' and id='lists';
  return jsonb_array_length(add);
end $$;
grant execute on function public.staff_banks() to authenticated;
select 'team banks + usage ready' as result;
