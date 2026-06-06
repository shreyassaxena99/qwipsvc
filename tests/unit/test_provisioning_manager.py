from unittest.mock import MagicMock, patch

import pytest

from svc.custom_types import ProvisionStatus
from svc.models import SessionDeprovisioningJobMetadata, SessionProvisioningJobMetadata

_SESSION_ID = "sess_test"
_POD_ID = "pod_1"
_ACCESS_CODE_ID = "encrypted_code_id"


def _prov_meta(use_static: bool = True) -> SessionProvisioningJobMetadata:
    return SessionProvisioningJobMetadata(
        jwt_token="test_jwt",
        session_id=_SESSION_ID,
        use_static_codes=use_static,
    )


def _deprov_meta(use_static: bool = True) -> SessionDeprovisioningJobMetadata:
    return SessionDeprovisioningJobMetadata(
        access_code_id=_ACCESS_CODE_ID,
        pod_id=_POD_ID,
        use_static_codes=use_static,
    )


def _session_row(access_code_id=None):
    return {
        "pod_id": _POD_ID,
        "user_email": "user@test.com",
        "start_time": "2025-01-01T10:00:00+00:00",
        "access_code_id": access_code_id,
    }


@patch("svc.provisioning_manager.send_access_email")
@patch("svc.provisioning_manager.set_start_time_for_session")
@patch("svc.provisioning_manager.get_pod_by_id")
@patch("svc.provisioning_manager.increment_provisioning_attempts")
@patch("svc.provisioning_manager.set_provisioning_status_by_session_id")
@patch("svc.provisioning_manager.set_access_code_id_for_session")
@patch("svc.provisioning_manager.StaticCodeManager")
@patch("svc.provisioning_manager.get_session")
@patch("svc.provisioning_manager.create_supabase_client")
def test_provision_static_code_marks_ready(
    mock_create_client,
    mock_get_session,
    mock_static_cls,
    mock_set_code,
    mock_set_status,
    mock_increment,
    mock_get_pod,
    mock_set_start,
    mock_send_email,
):
    from svc.provisioning_manager import provision_access_code_job

    mock_create_client.return_value = MagicMock()
    mock_get_session.return_value = _session_row(access_code_id=None)
    mock_get_pod.return_value = {"name": "Test Pod", "address": "1 St"}
    mock_mgr = MagicMock()
    mock_mgr.random_encrypted_access_code_id.return_value = "encrypted_xyz"
    mock_mgr.decrypt_code.return_value = "12345"
    mock_static_cls.return_value = mock_mgr

    provision_access_code_job(_prov_meta(use_static=True))

    mock_set_code.assert_called_once()
    mock_set_status.assert_called_with(
        mock_create_client.return_value, _SESSION_ID, ProvisionStatus.READY
    )
    mock_send_email.assert_called_once()


@patch("svc.provisioning_manager.set_provisioning_status_by_session_id")
@patch("svc.provisioning_manager.get_session")
@patch("svc.provisioning_manager.create_supabase_client")
def test_provision_idempotent_when_code_already_exists(
    mock_create_client,
    mock_get_session,
    mock_set_status,
):
    from svc.provisioning_manager import provision_access_code_job

    mock_create_client.return_value = MagicMock()
    mock_get_session.return_value = _session_row(access_code_id="already_set")

    provision_access_code_job(_prov_meta())

    mock_set_status.assert_called_once_with(
        mock_create_client.return_value, _SESSION_ID, ProvisionStatus.READY
    )


@patch("svc.provisioning_manager.set_provisioning_status_by_session_id")
@patch("svc.provisioning_manager.increment_provisioning_attempts")
@patch("svc.provisioning_manager.get_session")
@patch("svc.provisioning_manager.create_supabase_client")
def test_provision_failure_marks_failed(
    mock_create_client,
    mock_get_session,
    mock_increment,
    mock_set_status,
):
    from svc.provisioning_manager import provision_access_code_job

    mock_create_client.return_value = MagicMock()
    mock_get_session.side_effect = RuntimeError("DB error")

    with pytest.raises(RuntimeError):
        provision_access_code_job(_prov_meta())

    mock_set_status.assert_called_with(
        mock_create_client.return_value, _SESSION_ID, ProvisionStatus.FAILED
    )


@patch("svc.provisioning_manager.update_pod_status")
@patch("svc.provisioning_manager.create_supabase_client")
def test_deprovision_static_marks_pod_available(mock_create_client, mock_update_pod):
    from svc.provisioning_manager import deprovision_access_code_job

    mock_create_client.return_value = MagicMock()

    deprovision_access_code_job(_deprov_meta(use_static=True))

    mock_update_pod.assert_called_once_with(
        mock_create_client.return_value, _POD_ID, False
    )


@patch("svc.provisioning_manager.update_pod_status")
@patch("svc.provisioning_manager.delete_access_code")
@patch("svc.provisioning_manager.create_supabase_client")
def test_deprovision_seam_deletes_code_and_marks_available(
    mock_create_client, mock_delete, mock_update_pod
):
    from svc.provisioning_manager import deprovision_access_code_job

    mock_create_client.return_value = MagicMock()

    deprovision_access_code_job(_deprov_meta(use_static=False))

    mock_delete.assert_called_once_with(_ACCESS_CODE_ID)
    mock_update_pod.assert_called_once_with(
        mock_create_client.return_value, _POD_ID, False
    )
