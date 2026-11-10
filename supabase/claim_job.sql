-- Two (or more) TP computers: a queued job is taken by exactly one of them.
create or replace function public.claim_job(p_id text, p_host text) returns boolean language plpgsql security definer set search_path = public as $$
begin
  update kv set data = data || jsonb_build_object('status','starting','pickedAt',to_char(now() at time zone 'utc','YYYY-MM-DD"T"HH24:MI:SS"Z"'),'host',p_host), updated_at = now()
   where collection = 'tp_jobs' and id = p_id and data->>'status' = 'queued';
  return found;
end $$;
revoke execute on function public.claim_job(text, text) from public, anon, authenticated;
grant execute on function public.claim_job(text, text) to service_role;
select 'claim_job ready' as result;
