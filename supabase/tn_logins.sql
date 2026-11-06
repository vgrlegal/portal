-- TNREGINET login IDs per company. Passwords can be written by the MD but never read back by the portal;
-- only the TP worker (service key) reads them.
create table if not exists public.tn_logins (
  id uuid primary key default gen_random_uuid(),
  client text not null,
  username text not null,
  password text not null,
  created_at timestamptz not null default now()
);
alter table public.tn_logins enable row level security;
drop policy if exists tnl_read on public.tn_logins;
drop policy if exists tnl_write on public.tn_logins;
create policy tnl_read on public.tn_logins for select to authenticated using (my_role() = 'md');
create policy tnl_write on public.tn_logins for all to authenticated using (my_role() = 'md') with check (my_role() = 'md');
revoke all on public.tn_logins from anon, authenticated;
grant select (id, client, username, created_at) on public.tn_logins to authenticated;
grant insert (client, username, password), update (client, username, password), delete on public.tn_logins to authenticated;
select 'TNREGINET login table ready' as result;
