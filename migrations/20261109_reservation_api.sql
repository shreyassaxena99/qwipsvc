-- Apply to the Supabase database before enabling the reservation API.
-- Existing paid overlaps must be resolved before adding the exclusion constraint.
create extension if not exists btree_gist;

-- This service now owns reminder delivery; avoid queuing a second reminder
-- through the old database trigger.
drop trigger if exists queue_reservation_whatsapp_reminder on public.pod_reservations;

alter table public.pod_reservations
  add column if not exists hold_expires_at timestamptz;

create table if not exists public.reservation_delivery_jobs (
  id uuid primary key default gen_random_uuid(),
  reservation_id uuid not null references public.pod_reservations(id),
  kind text not null check (kind in ('email', 'booking_whatsapp', 'end_reminder')),
  due_at timestamptz not null default now(),
  status text not null default 'pending' check (status in ('pending', 'processing', 'sent', 'failed', 'cancelled')),
  attempts integer not null default 0,
  leased_until timestamptz,
  lease_token uuid,
  last_error text,
  sent_at timestamptz,
  unique (reservation_id, kind)
);

alter table public.pod_reservations enable row level security;
alter table public.reservation_delivery_jobs enable row level security;
revoke all on public.pod_reservations from anon, authenticated;
revoke all on public.reservation_delivery_jobs from anon, authenticated;

-- Existing pending rows are abandoned checkouts; release them before enforcing holds.
update public.pod_reservations
set status = 'cancelled'
where status = 'pending' and hold_expires_at is null;

alter table public.pod_reservations
  add constraint pod_reservations_no_overlap
  exclude using gist (
    pod_id with =,
    tstzrange(start_at, end_at, '[)') with &&
  ) where (status in ('pending', 'paid'));

create or replace function public.create_pod_reservation(
  p_pod_id text, p_pod_label text, p_start_at timestamptz,
  p_end_at timestamptz, p_duration_minutes integer, p_amount_pence integer,
  p_customer_name text, p_customer_email text, p_customer_phone text,
  p_hold_expires_at timestamptz
) returns public.pod_reservations
language plpgsql security definer set search_path = public as $$
declare result public.pod_reservations;
begin
  update public.pod_reservations set status = 'cancelled'
    where pod_id = p_pod_id and status = 'pending' and hold_expires_at <= now();
  insert into public.pod_reservations
    (pod_id, pod_label, start_at, end_at, duration_minutes, amount_pence,
     customer_name, customer_email, customer_phone, hold_expires_at)
  values
    (p_pod_id, p_pod_label, p_start_at, p_end_at, p_duration_minutes, p_amount_pence,
     p_customer_name, p_customer_email, p_customer_phone, p_hold_expires_at)
  returning * into result;
  return result;
end $$;

create or replace function public.attach_reservation_payment(
  p_reservation_id uuid, p_payment_intent_id text
) returns boolean language plpgsql security definer set search_path = public as $$
begin
  update public.pod_reservations set stripe_payment_intent_id = p_payment_intent_id
  where id = p_reservation_id and status = 'pending'
    and hold_expires_at > now() and stripe_payment_intent_id is null;
  return found;
end $$;

create or replace function public.confirm_paid_reservation(
  p_reservation_id uuid, p_payment_intent_id text, p_access_code text
) returns text language plpgsql security definer set search_path = public as $$
declare booking public.pod_reservations;
begin
  select * into booking from public.pod_reservations
    where id = p_reservation_id for update;
  if not found or booking.stripe_payment_intent_id is distinct from p_payment_intent_id then
    return 'mismatch';
  end if;
  if booking.status = 'paid' then return 'already_paid'; end if;
  if booking.status <> 'pending' or booking.hold_expires_at <= now() then
    return 'expired';
  end if;
  update public.pod_reservations set status = 'paid', access_code = p_access_code
    where id = p_reservation_id;
  insert into public.reservation_delivery_jobs(reservation_id, kind)
    values (p_reservation_id, 'email') on conflict do nothing;
  if booking.customer_phone is not null then
    insert into public.reservation_delivery_jobs(reservation_id, kind, due_at)
      values (p_reservation_id, 'end_reminder', booking.end_at - interval '5 minutes')
      on conflict do nothing;
  end if;
  return 'paid';
end $$;

create or replace function public.claim_reservation_delivery_jobs(p_limit integer)
returns setof public.reservation_delivery_jobs
language plpgsql security definer set search_path = public as $$
begin
  return query
  with claimed as (
    select id from public.reservation_delivery_jobs
    where kind = 'email' and due_at <= now() and attempts < 10
      and (status = 'pending' or (status = 'processing' and leased_until < now()))
    order by due_at for update skip locked limit least(greatest(p_limit, 1), 50)
  )
  update public.reservation_delivery_jobs j
    set status = 'processing', leased_until = now() + interval '2 minutes',
        lease_token = gen_random_uuid(), attempts = j.attempts + 1
  from claimed where j.id = claimed.id returning j.*;
end $$;

create or replace function public.complete_reservation_email(p_job_id uuid, p_lease_token uuid)
returns boolean language plpgsql security definer set search_path = public as $$
declare job public.reservation_delivery_jobs;
declare booking public.pod_reservations;
begin
  select * into job from public.reservation_delivery_jobs
    where id = p_job_id and kind = 'email';
  if not found then return false; end if;
  select * into booking from public.pod_reservations
    where id = job.reservation_id for update;
  select * into job from public.reservation_delivery_jobs
    where id = p_job_id for update;
  if booking.status <> 'paid' or job.status <> 'processing'
    or job.lease_token is distinct from p_lease_token then return false; end if;
  update public.pod_reservations set code_emailed_at = now() where id = booking.id;
  update public.reservation_delivery_jobs
    set status = 'sent', sent_at = now(), leased_until = null
    where id = p_job_id;
  if booking.customer_phone is not null then
    insert into public.reservation_delivery_jobs(reservation_id, kind)
      values (booking.id, 'booking_whatsapp') on conflict do nothing;
  end if;
  return true;
end $$;

create or replace function public.claim_reservation_whatsapp_jobs(p_limit integer)
returns setof public.reservation_delivery_jobs
language plpgsql security definer set search_path = public as $$
begin
  update public.reservation_delivery_jobs
    set status = 'failed', leased_until = null
    where kind = 'end_reminder' and status = 'processing'
      and leased_until < now() and attempts >= 3;
  update public.reservation_delivery_jobs j
    set status = 'failed', leased_until = null
    from public.pod_reservations b
    where b.id = j.reservation_id and j.kind = 'end_reminder'
      and j.status = 'pending' and b.end_at <= now();
  return query
  with claimed as (
    select j.id from public.reservation_delivery_jobs j
    join public.pod_reservations b on b.id = j.reservation_id
    where j.kind in ('booking_whatsapp', 'end_reminder')
      and b.status = 'paid' and j.due_at <= now()
      and (j.status = 'pending' or (j.status = 'processing' and j.leased_until < now()))
      and j.attempts < case when j.kind = 'end_reminder' then 3 else 10 end
      and (j.kind <> 'end_reminder' or b.end_at > now())
      and (j.kind <> 'booking_whatsapp' or b.code_emailed_at is not null)
    order by j.due_at for update of j skip locked
    limit least(greatest(p_limit, 1), 50)
  )
  update public.reservation_delivery_jobs j
    set status = 'processing', leased_until = now() + interval '2 minutes',
        lease_token = gen_random_uuid(), attempts = j.attempts + 1
  from claimed where j.id = claimed.id returning j.*;
end $$;

create or replace function public.ack_reservation_whatsapp_job(
  p_job_id uuid, p_lease_token uuid, p_success boolean, p_error text
) returns text language plpgsql security definer set search_path = public as $$
declare job public.reservation_delivery_jobs;
declare booking_status text;
begin
  select * into job from public.reservation_delivery_jobs where id = p_job_id;
  if not found or job.kind not in ('booking_whatsapp', 'end_reminder')
    or job.lease_token is distinct from p_lease_token then return 'invalid'; end if;
  select status into booking_status from public.pod_reservations
    where id = job.reservation_id for update;
  select * into job from public.reservation_delivery_jobs where id = p_job_id for update;
  if job.lease_token is distinct from p_lease_token then return 'invalid'; end if;
  if job.status = 'sent' then return 'sent'; end if;
  if job.status <> 'processing' then return 'invalid'; end if;
  if booking_status <> 'paid' then
    update public.reservation_delivery_jobs set status = 'cancelled', leased_until = null
      where id = p_job_id;
    return 'cancelled';
  end if;
  if p_success then
    update public.reservation_delivery_jobs
      set status = 'sent', sent_at = now(), leased_until = null, last_error = null
      where id = p_job_id;
    return 'sent';
  end if;
  if job.attempts >= case when job.kind = 'end_reminder' then 3 else 10 end
    or (job.kind = 'end_reminder' and now() >=
      (select end_at from public.pod_reservations where id = job.reservation_id)) then
    update public.reservation_delivery_jobs
      set status = 'failed', leased_until = null, last_error = left(coalesce(p_error, ''), 500)
      where id = p_job_id;
    return 'failed';
  end if;
  update public.reservation_delivery_jobs
    set status = 'pending', leased_until = null,
        due_at = now() + (interval '1 minute' * job.attempts),
        last_error = left(coalesce(p_error, ''), 500)
    where id = p_job_id;
  return 'pending';
end $$;

create or replace function public.cancel_pod_reservation(p_reservation_id uuid)
returns boolean language plpgsql security definer set search_path = public as $$
begin
  update public.pod_reservations set status = 'cancelled'
    where id = p_reservation_id and status <> 'cancelled';
  if not found then return false; end if;
  update public.reservation_delivery_jobs set status = 'cancelled', leased_until = null
    where reservation_id = p_reservation_id and status in ('pending', 'processing');
  return true;
end $$;

create or replace function public.expire_pending_pod_reservations()
returns integer language plpgsql security definer set search_path = public as $$
declare expired_count integer;
begin
  update public.pod_reservations set status = 'cancelled'
    where status = 'pending' and hold_expires_at <= now();
  get diagnostics expired_count = row_count;
  return expired_count;
end $$;

-- Only the service-role backend may invoke these state-changing functions.
revoke execute on function public.create_pod_reservation(text,text,timestamptz,timestamptz,integer,integer,text,text,text,timestamptz) from public, anon, authenticated;
revoke execute on function public.attach_reservation_payment(uuid,text) from public, anon, authenticated;
revoke execute on function public.confirm_paid_reservation(uuid,text,text) from public, anon, authenticated;
revoke execute on function public.claim_reservation_delivery_jobs(integer) from public, anon, authenticated;
revoke execute on function public.complete_reservation_email(uuid,uuid) from public, anon, authenticated;
revoke execute on function public.claim_reservation_whatsapp_jobs(integer) from public, anon, authenticated;
revoke execute on function public.ack_reservation_whatsapp_job(uuid,uuid,boolean,text) from public, anon, authenticated;
revoke execute on function public.cancel_pod_reservation(uuid) from public, anon, authenticated;
revoke execute on function public.expire_pending_pod_reservations() from public, anon, authenticated;
grant execute on function public.create_pod_reservation(text,text,timestamptz,timestamptz,integer,integer,text,text,text,timestamptz) to service_role;
grant execute on function public.attach_reservation_payment(uuid,text) to service_role;
grant execute on function public.confirm_paid_reservation(uuid,text,text) to service_role;
grant execute on function public.claim_reservation_delivery_jobs(integer) to service_role;
grant execute on function public.complete_reservation_email(uuid,uuid) to service_role;
grant execute on function public.claim_reservation_whatsapp_jobs(integer) to service_role;
grant execute on function public.ack_reservation_whatsapp_job(uuid,uuid,boolean,text) to service_role;
grant execute on function public.cancel_pod_reservation(uuid) to service_role;
grant execute on function public.expire_pending_pod_reservations() to service_role;
