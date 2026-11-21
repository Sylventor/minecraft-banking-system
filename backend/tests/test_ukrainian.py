import pytest

from app.banking_schemas import UserSettings
from app.localization import CODE_MESSAGES, MESSAGES, UK_MESSAGES, error_message


def test_ukrainian_covers_all_existing_error_translations():
    for message in {*MESSAGES.values(), *CODE_MESSAGES.values()}:
        assert message in UK_MESSAGES
        assert UK_MESSAGES[message]


@pytest.mark.parametrize("language", ["ru", "en", "uk-UA"])
def test_existing_profile_settings_accept_all_three_languages(language):
    assert UserSettings(language=language).language == language


def test_ukrainian_errors_never_fall_back_to_russian_or_english():
    assert error_message("core_permission_denied", "authorization failed", "uk-UA") == "У вас немає прав на цю дію"
    assert error_message("core_already_exists", "idempotency payload mismatch", "uk-UA") == (
        "Цей ключ уже пов’язано з іншим запитом. Спочатку перевірте попередній результат"
    )
    assert error_message("unknown", "private implementation detail", "uk-UA") == (
        "Дію відхилено. Перевірте дані та права доступу"
    )
