# run_macos_ci_local.py

Локальное воспроизведение GitHub Actions macOS CI (`macos.yml`) в изолированной Lima VM с macOS 15 Sequoia (aarch64).

Скрипт читает все параметры сборки и тест-команды напрямую из `.github/workflows/macos.yml` — никакого дублирования, всегда синхронизировано с CI.

## Требования

- **macOS** — скрипт работает **только на Mac**. Lima использует Apple Virtualization Framework (VZ), который доступен исключительно на macOS.
- **Lima** ≥ 2.1.0 — установите через Homebrew:

  ```bash
  brew install lima
  ```

- **~70 GB свободного места** — IPSW-образ macOS 15 (~13 GB, скачивается один раз) + диск VM (по умолчанию 60 GB).

## Быстрый старт

```bash
./scripts/ci/run_macos_ci_local.py
```

При первом запуске:

1. Автоматически создаётся Python venv в `scripts/ci/.venv` и устанавливаются зависимости из `scripts/ci/requirements.txt`.
2. Скачивается IPSW-образ macOS 15 (~13 GB) — **только один раз**, потом кэшируется.
3. Создаётся Lima VM с macOS 15.
4. **Откроется окно macOS Installer** — это нормально, не закрывайте и не трогайте его. Установка пройдёт автоматически. После завершения установки GUI-окно будет просто висеть.
5. Внутри VM устанавливается Homebrew и зависимости из workflow.
6. Запускается cmake configure, сборка и тесты — точно те же шаги, что и в CI.

## Опции

| Опция | Описание | По умолчанию |
|---|---|---|
| `--vm-name NAME` | Имя Lima VM | `userver-ci` |
| `--build-dir DIR` | Путь внутри VM для сборки | `/tmp/userver-build` |
| `--jobs N` | Параллельность `cmake --build` | Все ядра хоста |
| `--cpus N` | Количество vCPU для VM при сборке | Все ядра хоста |
| `--test-cpus N` | Количество vCPU для VM при тестах (переконфигурация перед тестами) | Как `--cpus` |
| `--memory GB` | RAM для VM в GiB | `8` |
| `--disk GB` | Размер диска VM в GiB | `60` |
| `--skip-install` | Пропустить `brew install` (VM уже настроена) | — |
| `--skip-build` | Пропустить cmake configure + build | — |
| `--skip-tests` | Пропустить запуск тестов | — |
| `--test SUITE` | Запустить только один test suite (имя из «Run tests (*)») | — |
| `--fail-fast` | Остановиться после первого провалившегося теста | — |
| `--dump-logs DIR` | Скопировать логи тестов из VM в DIR на хосте | `test-results` |
| `--destroy` | Удалить VM после завершения | — |
| `--recreate` | Пересоздать VM с нуля (удалить старую и создать заново) | — |

## Типичные сценарии

### Полный прогон с нуля

```bash
./scripts/ci/run_macos_ci_local.py
```

### Пересоздать VM (если что-то сломалось)

```bash
./scripts/ci/run_macos_ci_local.py --recreate
```

### Только тесты (VM и зависимости уже готовы, сборка уже была)

```bash
./scripts/ci/run_macos_ci_local.py --skip-install --skip-build
```

### Запустить только один test suite

```bash
# Узнать доступные suite можно из вывода скрипта (строка "Test suites: ...")
./scripts/ci/run_macos_ci_local.py --skip-install --skip-build --test universal
```

### Уменьшить ресурсы VM

```bash
./scripts/ci/run_macos_ci_local.py --cpus 4 --memory 6 --disk 40
```

### Быстрая сборка, ограниченные ресурсы для тестов

Собрать на всех ядрах хоста, но тестировать с ограниченным количеством vCPU (например, 2) — чтобы воспроизвести условия resource-constrained среды:

```bash
./scripts/ci/run_macos_ci_local.py --test-cpus 2
```

Скрипт автоматически:
1. Создаёт/запускает VM с `--cpus` (по умолчанию все ядра хоста) для быстрой сборки
2. Перед тестами переконфигурирует VM на `--test-cpus` vCPU (редактирует `lima.yaml` + перезапуск)
3. После тестов восстанавливает исходное количество vCPU для следующей быстрой сборки

Можно комбинировать с `--skip-build` — тогда VM сразу запустится с `--test-cpus`:

```bash
./scripts/ci/run_macos_ci_local.py --skip-install --skip-build --test-cpus 2
```

### Удалить VM после прогона (освободить диск)

```bash
./scripts/ci/run_macos_ci_local.py --destroy
```

## Как это работает

1. Скрипт парсит `.github/workflows/macos.yml` и извлекает `CMAKE_FLAGS`, install-шаги, build-шаги и test-шаги.
2. Создаёт Lima VM с macOS 15 (если не существует) и монтирует репозиторий как `/tmp/userver` (writable).
3. Адаптирует `run:` скрипты из workflow для VM: подставляет абсолютные пути, добавляет `brew shellenv`, настраивает ccache, CPM, locale.
4. Выполняет шаги внутри VM через `limactl shell`.
5. После тестов копирует логи (ctest, gtest xml, yasuite) из VM на хост.

## Важные замечания

- **GUI-окно при первом запуске** — macOS Installer требует дисплей. При первом создании VM откроется окно установки. Не трогайте его — установка пройдёт автоматически. При последующих запусках окно не появится.
- **Virtiofs-кэш** — если файлы в репозитории изменились после последнего старта VM, скрипт автоматически перезапустит VM для сброса кэша virtiofs (иначе Lima может отдавать устаревшие данные файлов).
- **Quarantine-атрибуты** — в Lima VM скачанные бинарники (например, ClickHouse) могут получить `com.apple.quarantine`, и Gatekeeper убьёт их сигналом SIGKILL. Скрипт автоматически снимает quarantine-атрибуты.
- **Ошибки VZErrorDomain** — если `limactl create` падает с ошибкой Apple VZ, удалите остатки и пересоздайте:

  ```bash
  limactl delete --force userver-ci
  rm -rf ~/.lima/userver-ci
  ./scripts/ci/run_macos_ci_local.py --recreate
  ```

## Управление VM

```bash
# Зайти в VM
limactl shell userver-ci

# Остановить VM
limactl stop userver-ci

# Удалить VM
limactl delete --force userver-ci

# Список всех VM
limactl list

# Логи загрузки VM
tail -f ~/.lima/userver-ci/serialv.log
```
