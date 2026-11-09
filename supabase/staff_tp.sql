-- Staff ticked "TP Generate" in Clients & Staff may start single TP jobs:
-- they can see login IDs (company + username only; passwords stay write-only) and create tp_jobs.
create or replace function public.staff_can_tp() returns boolean language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from kv, jsonb_array_elements(coalesce(kv.data->'staff', '[]'::jsonb)) s
    where kv.collection = 'config' and kv.id = 'lists' and jsonb_typeof(s) = 'object'
      and lower(s->>'email') = lower(auth.jwt()->>'email') and (s->>'tp') = 'true')
$$;
grant execute on function public.staff_can_tp() to authenticated;
alter policy tnl_read on public.tn_logins using (my_role() = 'md' or (my_role() = 'staff' and staff_can_tp()));
drop policy if exists kv_insert_tp_staff on public.kv;
create policy kv_insert_tp_staff on public.kv for insert to authenticated
  with check (my_role() = 'staff' and collection = 'tp_jobs' and staff_can_tp());
select 'staff TP Generate ready' as result;
