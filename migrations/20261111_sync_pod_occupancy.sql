-- Keep pods.in_use true during a paid reservation while preserving walk-up occupancy.
-- Apply before deploying the backend change to update_pod_status.
begin;

do $$
begin
  if not exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'pods' and column_name = 'walkup_in_use'
  ) then
    alter table public.pods add column walkup_in_use boolean not null default false;
    update public.pods set walkup_in_use = coalesce(in_use, false);
  end if;
end $$;

create or replace function public.reconcile_pod_in_use()
returns integer language plpgsql security definer set search_path = public as $$
declare changed_count integer;
begin
  with desired as (
    select p.id,
           (p.walkup_in_use or exists (
             select 1 from public.pod_reservations r
             where r.pod_id = p.id::text and r.status = 'paid'
               and r.start_at <= now() and r.end_at > now()
           )) as occupied
    from public.pods p
  )
  update public.pods p set in_use = d.occupied
    from desired d
    where p.id = d.id and p.in_use is distinct from d.occupied;
  get diagnostics changed_count = row_count;
  return changed_count;
end $$;

create or replace function public.set_walkup_pod_in_use(p_pod_id text, p_in_use boolean)
returns boolean language plpgsql security definer set search_path = public as $$
begin
  update public.pods p
    set walkup_in_use = p_in_use,
        in_use = p_in_use or exists (
          select 1 from public.pod_reservations r
          where r.pod_id = p.id::text and r.status = 'paid'
            and r.start_at <= now() and r.end_at > now()
        )
    where p.id::text = p_pod_id;
  return found;
end $$;

revoke execute on function public.reconcile_pod_in_use() from public, anon, authenticated;
revoke execute on function public.set_walkup_pod_in_use(text,boolean) from public, anon, authenticated;
grant execute on function public.reconcile_pod_in_use() to service_role;
grant execute on function public.set_walkup_pod_in_use(text,boolean) to service_role;

create extension if not exists pg_cron with schema extensions;
select cron.schedule('qwip-pod-occupancy', '* * * * *',
                     'select public.reconcile_pod_in_use()');
select public.reconcile_pod_in_use();

commit;
