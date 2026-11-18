-- 18 Nov 2026: Login alert (the MD).
-- Each browser keeps its own device id. While the portal is open it tells the database every 30 seconds that the device
-- is in use (device_seen). When a login STARTS on one device while the same login is in use on another device (seen in
-- the last 5 minutes), one alert row is made (collection login_alerts) for that person and the Admin.
-- "This was not me - sign out other devices" marks the other devices signed out (collection devices, revoked = true);
-- each of them signs itself out on its next tick (within 30 seconds). The person's own button also ends the other
-- sessions' refresh tokens in Supabase Auth (signOut scope "others" in the portal).
-- Only these functions read or change the two collections; the kv read policy does not open them.
-- Run once in Supabase → SQL Editor.

create or replace function public.device_seen(p_dev text, p_ua text default '', p_fresh boolean default false) returns jsonb
language plpgsql security definer set search_path = public as $$
declare em text := lower(auth.jwt()->>'email'); k text; cur jsonb; others jsonb; nm text; aid text;
begin
  if em is null or my_role() is null or coalesce(p_dev, '') = '' then return jsonb_build_object('ok', false); end if;
  p_dev := left(p_dev, 64); p_ua := left(coalesce(p_ua, ''), 300); k := em || '|' || p_dev;
  select data into cur from kv where collection = 'devices' and id = k;
  if cur is not null and coalesce((cur->>'revoked')::boolean, false) then
    return jsonb_build_object('revoked', true, 'by', cur->>'revokedBy', 'at', cur->>'revokedAt');
  end if;
  select s->>'name' into nm from kv, jsonb_array_elements(coalesce(kv.data->'staff', '[]'::jsonb)) s
    where kv.collection = 'config' and kv.id = 'lists' and jsonb_typeof(s) = 'object' and lower(s->>'email') = em limit 1;
  -- a login starts: a new device, a page just opened, or a device back after 5 minutes away
  if p_fresh or cur is null or now() - (cur->>'last')::timestamptz > interval '5 minutes' then
    select coalesce(jsonb_agg(jsonb_build_object('dev', data->>'dev', 'ua', data->>'ua', 'last', data->>'last')), '[]'::jsonb) into others
      from kv where collection = 'devices' and data->>'email' = em and id <> k
        and not coalesce((data->>'revoked')::boolean, false) and now() - (data->>'last')::timestamptz < interval '5 minutes';
    -- one alert per device pair: not again while an open alert for this device is less than 30 minutes old
    if jsonb_array_length(others) > 0 and not exists (select 1 from kv where collection = 'login_alerts' and data->>'email' = em
         and data->>'dev' = p_dev and data->>'status' = 'open' and now() - (data->>'at')::timestamptz < interval '30 minutes') then
      aid := gen_random_uuid()::text;
      insert into kv(collection, id, data) values ('login_alerts', aid, jsonb_build_object('email', em, 'name', coalesce(nm, em),
        'dev', p_dev, 'ua', p_ua, 'others', others, 'at', now(), 'status', 'open', 'log', '[]'::jsonb));
    end if;
  end if;
  if cur is null then
    insert into kv(collection, id, data) values ('devices', k, jsonb_build_object('email', em, 'name', coalesce(nm, em), 'dev', p_dev,
      'ua', p_ua, 'first', now(), 'last', now(), 'revoked', false));
  else
    update kv set data = data || jsonb_build_object('last', now(), 'ua', p_ua, 'name', coalesce(nm, em)), updated_at = now()
      where collection = 'devices' and id = k;
  end if;
  return jsonb_build_object('ok', true, 'alert', aid);
end $$;
grant execute on function public.device_seen(text, text, boolean) to authenticated;

-- devices of the last 30 days: the Admin sees everyone's, others only their own
create or replace function public.devices_list() returns setof jsonb language sql stable security definer set search_path = public as $$
  select data || jsonb_build_object('id', id, 'active', now() - (data->>'last')::timestamptz < interval '5 minutes') from kv
  where collection = 'devices' and (my_role() = 'md' or data->>'email' = lower(auth.jwt()->>'email'))
    and now() - (data->>'last')::timestamptz < interval '30 days'
  order by data->>'last' desc limit 500
$$;
grant execute on function public.devices_list() to authenticated;

-- alerts: open ones always, closed ones for 60 days (the log). Admin: everyone's; others: their own
create or replace function public.login_alerts_list() returns setof jsonb language sql stable security definer set search_path = public as $$
  select data || jsonb_build_object('id', id) from kv
  where collection = 'login_alerts' and (my_role() = 'md' or data->>'email' = lower(auth.jwt()->>'email'))
    and (data->>'status' = 'open' or now() - (data->>'at')::timestamptz < interval '60 days')
  order by data->>'at' desc limit 300
$$;
grant execute on function public.login_alerts_list() to authenticated;

-- sign out devices: one device (p_dev), or every device of p_email except p_keep. Admin: anyone; others: only their own.
-- p_alert (optional) closes that alert with the action in its log.
create or replace function public.devices_sign_out(p_email text, p_keep text default '', p_dev text default '', p_alert text default '') returns int
language plpgsql security definer set search_path = public as $$
declare me text := lower(auth.jwt()->>'email'); n int := 0; act text;
begin
  p_email := lower(coalesce(p_email, ''));
  if me is null or p_email = '' or not (my_role() = 'md' or p_email = me) then raise exception 'Not allowed'; end if;
  update kv set data = data || jsonb_build_object('revoked', true, 'revokedBy', me, 'revokedAt', now()), updated_at = now()
    where collection = 'devices' and data->>'email' = p_email and not coalesce((data->>'revoked')::boolean, false)
      and (case when coalesce(p_dev, '') <> '' then data->>'dev' = p_dev else data->>'dev' <> coalesce(p_keep, '') end);
  get diagnostics n = row_count;
  act := case when coalesce(p_dev, '') <> '' then 'device signed out' else 'other devices signed out' end;
  if coalesce(p_alert, '') <> '' then
    update kv set data = data || jsonb_build_object('status', 'done', 'action', act, 'by', me, 'doneAt', now(), 'signedOut', n,
      'log', coalesce(data->'log', '[]'::jsonb) || jsonb_build_array(jsonb_build_object('at', now(), 'by', me, 'action', act, 'n', n))), updated_at = now()
      where collection = 'login_alerts' and id = p_alert and (my_role() = 'md' or data->>'email' = me);
  end if;
  return n;
end $$;
grant execute on function public.devices_sign_out(text, text, text, text) to authenticated;

-- "It was me" (or the Admin's OK): the alert is closed and kept in the log
create or replace function public.login_alert_close(p_alert text, p_action text default 'It was me') returns void
language plpgsql security definer set search_path = public as $$
declare me text := lower(auth.jwt()->>'email');
begin
  update kv set data = data || jsonb_build_object('status', 'done', 'action', left(coalesce(p_action, ''), 80), 'by', me, 'doneAt', now(),
    'log', coalesce(data->'log', '[]'::jsonb) || jsonb_build_array(jsonb_build_object('at', now(), 'by', me, 'action', left(coalesce(p_action, ''), 80)))), updated_at = now()
    where collection = 'login_alerts' and id = p_alert and (my_role() = 'md' or data->>'email' = me);
end $$;
grant execute on function public.login_alert_close(text, text) to authenticated;

select 'login alert ready' as result;
