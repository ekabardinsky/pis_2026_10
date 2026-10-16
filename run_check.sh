#!/usr/bin/env bash
# Полный цикл: ./run_check.sh [параметры check_labs.py, например --force].
# CODEX_BIN=/path/to/codex ./run_check.sh — если Codex CLI отсутствует в PATH.
# PYTHON_BIN=/path/to/python3 ./run_check.sh — другой интерпретатор Python.
# render_summary.py также обновляет все индивидуальные Markdown-отчёты.

set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
PYTHON_BIN="${PYTHON_BIN:-python3}"
failed_steps=()

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    printf 'Не найден Python: %s\n' "$PYTHON_BIN" >&2
    exit 1
fi

run_step() {
    local label="$1"
    shift
    printf '\n%s\n' "$label"
    if "$PYTHON_BIN" -B "$@"; then
        printf 'Этап завершён.\n'
    else
        failed_steps+=("$label")
        printf 'Этап завершился с ошибкой; продолжаем сборку доступных результатов.\n' >&2
    fi
}

run_step '1/3. Синхронизация репозиториев' "$SCRIPT_DIR/scripts/sync_repos.py"
run_step '2/3. Проверка лабораторных и обновление JSON' "$SCRIPT_DIR/scripts/check_labs.py" "$@"
run_step '3/3. Markdown-отчёты и сводная таблица' "$SCRIPT_DIR/scripts/render_summary.py"

printf '\nПосмотрите результаты перед публикацией:\n'
printf '  %s/results.md\n  %s/results/\n  %s/aggregated.json\n' "$SCRIPT_DIR" "$SCRIPT_DIR" "$SCRIPT_DIR"
printf 'Commit и push выполняются вручную.\n'

if (( ${#failed_steps[@]} > 0 )); then
    printf '\nЭтапы с ошибками:\n' >&2
    printf '  %s\n' "${failed_steps[@]}" >&2
    printf 'Часть данных может относиться к предыдущему запуску.\n' >&2
    exit 1
fi

printf '\nВсе этапы завершены успешно.\n'
