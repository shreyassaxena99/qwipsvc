-- Record a single departure-condition photo without changing payment status.
begin;

alter table public.pod_reservations
  add column if not exists check_out_photo_path text,
  add column if not exists check_out_photo_uploaded_at timestamptz;

create or replace function public.record_reservation_check_out_photo(
  p_reservation_id uuid, p_customer_email text, p_photo_path text
) returns text
language plpgsql security definer set search_path = public as $$
declare booking public.pod_reservations;
begin
  select * into booking from public.pod_reservations
    where id = p_reservation_id for update;
  if not found or lower(booking.customer_email) <> lower(p_customer_email) then
    return 'not_found';
  end if;
  if booking.check_out_photo_uploaded_at is not null then
    return 'already_uploaded';
  end if;
  if booking.status not in ('paid', 'confirmed')
     or now() < booking.start_at
     or now() >= booking.end_at + interval '30 minutes' then
    return 'ineligible';
  end if;
  update public.pod_reservations
    set check_out_photo_path = p_photo_path,
        check_out_photo_uploaded_at = now()
    where id = p_reservation_id;
  return 'saved';
end $$;

revoke all on function public.record_reservation_check_out_photo(uuid, text, text)
  from public, anon, authenticated;
grant execute on function public.record_reservation_check_out_photo(uuid, text, text)
  to service_role;

-- A late check-in cannot follow an already recorded checkout.
create or replace function public.record_reservation_check_in_photo(
  p_reservation_id uuid, p_customer_email text, p_photo_path text
) returns text
language plpgsql security definer set search_path = public as $$
declare booking public.pod_reservations;
begin
  select * into booking from public.pod_reservations
    where id = p_reservation_id for update;
  if not found or lower(booking.customer_email) <> lower(p_customer_email) then
    return 'not_found';
  end if;
  if booking.check_in_photo_uploaded_at is not null then
    return 'already_uploaded';
  end if;
  if booking.status not in ('paid', 'confirmed')
     or booking.check_out_photo_uploaded_at is not null
     or now() < booking.start_at - interval '15 minutes'
     or now() >= booking.end_at then
    return 'ineligible';
  end if;
  update public.pod_reservations
    set check_in_photo_path = p_photo_path,
        check_in_photo_uploaded_at = now()
    where id = p_reservation_id;
  return 'saved';
end $$;

commit;
