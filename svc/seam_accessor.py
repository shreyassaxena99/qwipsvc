import random
import os
from seam import Seam
import logging
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

DEFAULT_DEVICE_ID = "6254ee56-0d9d-4928-a754-c676d1eb40c1"


def _get_seam_api_client() -> Seam:
    return Seam(api_key=os.getenv("SEAM_API_KEY"))


def _get_access_code() -> str:
    return str(random.randint(100000, 999999))


def _is_code_still_active(seam_client: Seam, access_code_id: str) -> bool:
    try:
        access_code = seam_client.access_codes.get(access_code_id=access_code_id)
        return True
    except:
        return False


def get_access_code(
    access_code_id: str, seam_client: Seam = _get_seam_api_client()
) -> str:
    """
    Retrieves the access code for the specified access code ID.

    Args:
        access_code_id (str): The ID of the access code to retrieve.
        seam_client (Seam): The Seam API client. Defaults to a new client created with the SEAM_API_KEY environment variable.

    Returns:
        str: The access code.
    """
    access_code = seam_client.access_codes.get(access_code_id=access_code_id)
    return access_code.code


def set_access_code(
    starts_at_dt: datetime,
    device_id: str = DEFAULT_DEVICE_ID,
    seam_client: Seam = _get_seam_api_client(),
) -> str:
    """
    Sets a time-bound access code on the specified device. It will be set to start now and end in 3 hours.

    This is because seam access codes require a start and end time, and we want to ensure the code is valid for a reasonable period.
    Whenever client ends up finishing their booking before the 3 hours are up, we will delete the access code to invalidate it early.

    Currently we continuously poll the status of the access code until it is set. Let us see if this is an issue in production.

    Args:
        starts_at_dt (datetime): The start time for the access code.
        device_id (str): The ID of the device to set the access code on. Defaults to DEFAULT_DEVICE_ID.
        seam_client (Seam): The Seam API client. Defaults to a new client created with the SEAM_API_KEY environment variable.

    Returns:
        str: The ID of the created access code.
    """

    device = seam_client.devices.get(device_id=device_id)

    ends_at_dt = starts_at_dt + timedelta(minutes=180)  # Access code valid for 3 hours

    starts_at = starts_at_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
    ends_at = ends_at_dt.isoformat(timespec="seconds").replace("+00:00", "Z")

    if (
        not device.can_program_online_access_codes
        and not device.can_program_offline_access_codes
    ):
        raise RuntimeError("Device does not support programming access codes.")

    access_code = seam_client.access_codes.create(
        device_id=device_id,
        name=f"temporary access code from {starts_at}",
        starts_at=starts_at,
        ends_at=ends_at,
        code=_get_access_code(),
    )

    while (
        seam_client.access_codes.get(access_code_id=access_code.access_code_id).status
        != "set"
    ):
        logger.debug("Waiting for access code to be set...")
        time.sleep(1)  # wait for 1 second before checking again

    logger.info(f"Access code is set: {access_code.code}")

    return access_code.access_code_id


def delete_access_code(
    access_code_id: str, seam_client: Seam = _get_seam_api_client()
) -> None:
    """
    Deletes the specified access code.

    Currently we continously poll the status of the access code until it is deleted. Let us see if this is an issue in production.

    Args:
        access_code_id (str): The ID of the access code to delete.
        seam_client (Seam): The Seam API client. Defaults to a new client created with the SEAM_API_KEY environment variable.
    """
    seam_client.access_codes.delete(access_code_id=access_code_id)

    while _is_code_still_active(seam_client, access_code_id):
        logger.debug("Waiting for access code to be deleted...")
        time.sleep(1)  # wait for 1 second before checking again

    logger.info("Access code deleted.")
