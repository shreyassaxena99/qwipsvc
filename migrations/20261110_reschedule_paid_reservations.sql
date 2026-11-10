-- Apply before deploying the reschedule API. Existing booking rows remain intact.
begin;

alter table public.pod_reservations
  add column if not exists rescheduled_at timestamptz;

create or replace function public.reschedule_paid_pod_reservation(
  p_reservation_id uuid, p_customer_email text,
  p_start_at timestamptz, p_end_at timestamptz
) returns text language plpgsql security definer set search_path = public as $$
declare booking public.pod_reservations;
begin
  select * into booking from public.pod_reservations
    where id = p_reservation_id for update;
  if not found or lower(booking.customer_email) is distinct from p_customer_email then
    return 'not_found';
  end if;
  if booking.status <> 'paid' or booking.start_at <= now() + interval '1 hour'
    or p_start_at < now() + interval '1 hour'
    or p_end_at - p_start_at <> booking.end_at - booking.start_at then
    return 'ineligible';
  end if;
  if p_start_at = booking.start_at then return 'unchanged'; end if;

  -- The existing exclusion constraint rejects an overlapping paid or held slot.
  update public.pod_reservations
    set start_at = p_start_at, end_at = p_end_at,
        rescheduled_at = now()
    where id = p_reservation_id;

  -- Reuse the original access code and payment. Send the new time by email.
  insert into public.reservation_delivery_jobs(reservation_id, kind, due_at)
    values (p_reservation_id, 'email', now())
    on conflict (reservation_id, kind) do update
      set due_at = now(), status = 'pending', attempts = 0,
          leased_until = null, lease_token = null, sent_at = null, last_error = null;

  -- An old booking WhatsApp may still be queued; the amendment email is the
  -- authoritative notice of the new time.
  update public.reservation_delivery_jobs
    set status = 'cancelled', leased_until = null, lease_token = null
    where reservation_id = p_reservation_id and kind = 'booking_whatsapp'
      and status in ('pending', 'processing');

  if booking.customer_phone is not null then
    insert into public.reservation_delivery_jobs(reservation_id, kind, due_at)
      values (p_reservation_id, 'end_reminder', p_end_at - interval '5 minutes')
      on conflict (reservation_id, kind) do update
        set due_at = excluded.due_at, status = 'pending', attempts = 0,
            leased_until = null, lease_token = null, sent_at = null, last_error = null;
  end if;
  return 'moved';
end $$;

revoke execute on function public.reschedule_paid_pod_reservation(uuid,text,timestamptz,timestamptz)
  from public, anon, authenticated;
grant execute on function public.reschedule_paid_pod_reservation(uuid,text,timestamptz,timestamptz)
  to service_role;

commit;
