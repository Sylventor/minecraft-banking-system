import pytest

from app.localization import error_message


@pytest.mark.parametrize(
    "message",
    [
        "Рядом нет свободного банкомата. Подойдите ближе и повторите команду.",
        "Предыдущая операция ещё не завершена",
        "Координатор потерял действующую аренду; операции остановлены",
        "Повторный запрос содержит другие параметры",
    ],
)
def test_core_state_conflicts_preserve_the_specific_russian_reason(message):
    assert error_message("core_already_exists", message) == message


def test_actual_idempotency_mismatch_keeps_its_translation():
    assert error_message("core_already_exists", "idempotency payload mismatch") == (
        "Этот ключ уже связан с другим запросом. Сначала проверьте прежний результат"
    )


def test_unknown_english_core_errors_still_use_safe_localized_fallback():
    assert error_message("core_internal", "private implementation detail") == (
        "Результат уточняется. Повторите то же подтверждение: повторного списания не будет"
    )
