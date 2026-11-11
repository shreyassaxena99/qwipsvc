-- Apply before deploying the customer cancellation endpoint.
begin;

alter table public.pod_reservations
  add column if not exists customer_cancel_requested_at timestamptz,
  add column if not exists stripe_refund_id text,
  add column if not exists stripe_refund_status text;

create or replace function public.request_customer_cancel_pod_reservation(
  p_reservation_id uuid, p_customer_email text
) returns text language plpgsql security definer set search_path = public as $$
declare booking public.pod_reservations;
begin
  select * into booking from public.pod_reservations
    where id = p_reservation_id for update;
  if not found or lower(booking.customer_email) is distinct from p_customer_email then
    return 'not_found';
  end if;
  if booking.customer_cancel_requested_at is not null then
    return 'already_requested';
  end if;
  if booking.status <> 'paid' or booking.start_at <= now() + interval '1 hour'
    or booking.stripe_payment_intent_id is null then
    return 'ineligible';
  end if;

  update public.pod_reservations
    set status = 'cancelled', customer_cancel_requested_at = now()
    where id = p_reservation_id;
  update public.reservation_delivery_jobs
    set status = 'cancelled', leased_until = null, lease_token = null
    where reservation_id = p_reservation_id and status in ('pending', 'processing');
  return 'cancelled';
end $$;

revoke execute on function public.request_customer_cancel_pod_reservation(uuid,text)
  from public, anon, authenticated;
grant execute on function public.request_customer_cancel_pod_reservation(uuid,text)
  to service_role;

commit;
