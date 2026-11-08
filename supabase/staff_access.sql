-- Staff see and change only the work types the Admin ticked for them (cases.data.type in their tabs).
create or replace function public.staff_tabs() returns text[] language sql stable security definer set search_path = public as $$
  select coalesce(array_agg(t), '{}')
  from kv, jsonb_array_elements(coalesce(kv.data->'staff', '[]'::jsonb)) s, jsonb_array_elements_text(coalesce(s->'tabs', '[]'::jsonb)) t
  where kv.collection = 'config' and kv.id = 'lists' and jsonb_typeof(s) = 'object' and lower(s->>'email') = lower(auth.jwt()->>'email')
$$;
alter policy kv_read on public.kv using (my_role() = 'md' or (my_role() = 'staff' and (collection in ('config', 'tp_jobs') or (collection = 'cases' and data->>'type' = any (staff_tabs())))));
alter policy kv_update on public.kv using (my_role() = 'md' or (my_role() = 'staff' and (collection = 'tp_jobs' or (collection = 'cases' and data->>'type' = any (staff_tabs())))));
select 'staff access by work type ready' as result;
