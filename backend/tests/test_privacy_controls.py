import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.authentication import RegisterInput
from app.privacy import without_secrets
from app.privacy_worker import verify_backup


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_registration_requires_explicit_age_attestation(value):
    with pytest.raises(ValidationError):
        RegisterInput(
            login="Example",
            password="long test passphrase",
            password_confirmation="long test passphrase",
            age_confirmed=value,
        )


def test_missing_age_is_rejected_and_true_is_accepted():
    payload = dict(login="Example", password="long test passphrase", password_confirmation="long test passphrase")
    with pytest.raises(ValidationError):
        RegisterInput(**payload)
    assert RegisterInput(**payload, age_confirmed=True).age_confirmed


def test_export_recursively_excludes_credentials_but_preserves_amounts():
    value = without_secrets(
        {
            "amount": "123456789.01",
            "payload": [
                {
                    "cookie": "secret",
                    "password_hash": "secret",
                    "session_hash": "secret",
                    "lease_token": "secret",
                    "operation_id": "public",
                }
            ],
        }
    )
    assert value == {"amount": "123456789.01", "payload": [{"operation_id": "public"}]}


def test_cleanup_requires_recent_real_restore_and_matching_dump(tmp_path):
    dump = tmp_path / "horizon-privacy-test.dump"
    dump.write_bytes(b"test dump")
    now = datetime.now(UTC)
    record = {
        "backup_file": dump.name,
        "sha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
        "verified_at": now.isoformat(),
        "restore_checked": True,
        "reconciliation_zero": True,
    }
    manifest = tmp_path / "verified.json"
    manifest.write_text(json.dumps(record))
    verify_backup(manifest, tmp_path, now)
    with pytest.raises(ValueError):
        verify_backup(manifest, tmp_path, now + timedelta(days=2))
    dump.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        verify_backup(manifest, tmp_path, now)
