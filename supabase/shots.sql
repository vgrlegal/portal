-- robot screenshots live in their own rows (collection tp_shots) so the job rows that are polled stay small
alter policy kv_read on public.kv using (my_role() = 'md' or (my_role() = 'staff' and (collection in ('config', 'tp_jobs', 'tp_shots')
  or (collection = 'cases' and data->>'type' = any (staff_tabs()) and (cardinality(staff_banks()) = 0 or data->>'bank' = any (staff_banks())))
  or (collection = 'usage' and data->>'org' = partner_org())
  or (collection = 'presence' and (lower(data->>'email') = lower(auth.jwt()->>'email') or (partner_org() is not null and data->>'org' = partner_org()))))));
