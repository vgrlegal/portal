-- A partner (Raja) may add company representatives, witnesses and TNREGINET login IDs (Receipt only, not the MODT pool).
-- People: only the 'wit' and 'profiles' parts of the settings change; a backup of the settings is kept for the day first.
create or replace function public.partner_save_people(wit jsonb, profiles jsonb) returns void language plpgsql security definer set search_path = public as $$
declare cur jsonb;
begin
  if partner_org() is null and my_role() <> 'md' then raise exception 'only the Admin or a partner can save people'; end if;
  if jsonb_typeof(wit) <> 'array' or jsonb_typeof(profiles) <> 'object' then raise exception 'bad data'; end if;
  select data into cur from kv where collection='config' and id='lists' for update;
  if cur is null then raise exception 'settings not found'; end if;
  if jsonb_array_length(coalesce(cur->'wit','[]'::jsonb)) > 0 and jsonb_array_length(wit) = 0 then raise exception 'not saved: this would empty the witness list'; end if;
  if (select count(*) from jsonb_object_keys(coalesce(cur->'profiles','{}'::jsonb))) > 0 and (select count(*) from jsonb_object_keys(profiles)) = 0 then raise exception 'not saved: this would empty the client profiles'; end if;
  if not exists (select 1 from kv where collection='config' and id='lists_prev_'||to_char(now() at time zone 'Asia/Kolkata','YYYY-MM-DD')) then
    insert into kv(collection,id,data) values('config','lists_prev_'||to_char(now() at time zone 'Asia/Kolkata','YYYY-MM-DD'), cur || jsonb_build_object('savedAt', now()));
  end if;
  update kv set data = data || jsonb_build_object('wit', wit, 'profiles', profiles), updated_at = now() where collection='config' and id='lists';
end $$;
grant execute on function public.partner_save_people(jsonb, jsonb) to authenticated;

alter table public.tn_logins add column if not exists created_by text default lower(auth.jwt()->>'email');
grant select (created_by) on public.tn_logins to authenticated;
alter policy tnl_read on public.tn_logins using (my_role() = 'md' or (my_role() = 'staff' and (staff_can_tp() or partner_org() is not null)));
drop policy if exists tnl_partner_ins on public.tn_logins;
create policy tnl_partner_ins on public.tn_logins for insert to authenticated
  with check (partner_org() is not null and client <> 'MODT · all companies');
drop policy if exists tnl_partner_del on public.tn_logins;
create policy tnl_partner_del on public.tn_logins for delete to authenticated
  using (partner_org() is not null and created_by = lower(auth.jwt()->>'email'));
select 'partner people + login IDs ready' as result;
