#!/usr/bin/env python3
"""
run_macos_ci_local.py — локальное воспроизведение .github/workflows/macos.yml
                        в изолированной Lima macOS 15 VM (Sequoia, aarch64).

Все параметры сборки и тест-команды читаются напрямую из macos.yml —
никакого дублирования, скрипт всегда синхронизирован с CI.

Создаёт Lima VM с macOS 15, монтирует репозиторий как /userver (writable),
устанавливает Homebrew и зависимости внутри VM, собирает и тестирует.
Хост не затронут.

Требования:
    limactl >= 2.1.0  (brew install lima)

Использование:
    ./scripts/ci/run_macos_ci_local.py [ОПЦИИ]

    При первом запуске автоматически создаётся venv в scripts/ci/.venv
    и устанавливаются зависимости из scripts/ci/requirements.txt.

Опции:
    --vm-name NAME    имя Lima VM (default: userver-ci)
    --build-dir DIR   путь внутри VM для сборки (default: /tmp/userver-build)
    --jobs N          параллельность cmake --build (default: все ядра хоста)
    --cpus N          vCPU для VM (default: все ядра хоста)
    --memory GB       RAM для VM в GiB (default: 8)
    --disk GB         диск VM в GiB (default: 60)
    --skip-install    пропустить brew install (VM уже настроена)
    --skip-build      пропустить cmake configure + build
    --skip-tests      пропустить запуск тестов
    --test SUITE      запустить только один suite (имя из "Run tests (*)")
    --destroy         удалить VM после завершения
    --recreate        пересоздать VM с нуля
    --help            эта справка
"""

# ── Venv bootstrap ─────────────────────────────────────────────────────────────
# Выполняется до любых импортов сторонних библиотек.
# Создаёт venv, устанавливает зависимости и перезапускает себя из него.
# Цикл предотвращается через env-переменную _USERVER_CI_VENV.

import sys
import os
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_VENV_DIR = _SCRIPT_DIR / ".venv"
_REQUIREMENTS = _SCRIPT_DIR / "requirements.txt"
_VENV_PYTHON = _VENV_DIR / "bin" / "python3"

def _bootstrap_venv() -> None:
    """Создаёт venv и перезапускает скрипт из него если нужно."""
    # Уже перезапущены из venv — не зацикливаться
    if os.environ.get("_USERVER_CI_VENV") == str(_VENV_DIR):
        return

    import subprocess

    if not _VENV_PYTHON.exists():
        print(f"[CI] Создание venv: {_VENV_DIR}")
        subprocess.run([sys.executable, "-m", "venv", str(_VENV_DIR)], check=True)

    print(f"[CI] Установка зависимостей из {_REQUIREMENTS.name}...")
    subprocess.run(
        [str(_VENV_PYTHON), "-m", "pip", "install", "-q", "-r", str(_REQUIREMENTS)],
        check=True,
    )

    # Перезапускаем себя из venv; env-переменная ломает возможный цикл
    env = {**os.environ, "_USERVER_CI_VENV": str(_VENV_DIR)}
    os.execve(str(_VENV_PYTHON), [str(_VENV_PYTHON), __file__] + sys.argv[1:], env)

_bootstrap_venv()

# ── Основные импорты (теперь pyyaml доступен) ─────────────────────────────────

import argparse
import re
import subprocess
import textwrap
import time
from typing import NamedTuple

import yaml  # из venv

# ── Цвета ─────────────────────────────────────────────────────────────────────

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text

def log_info(msg: str)    -> None: print(_c("34", "[CI]  ") + msg)
def log_ok(msg: str)      -> None: print(_c("32", "[OK]  ") + msg)
def log_warn(msg: str)    -> None: print(_c("33", "[WARN]") + msg, file=sys.stderr)
def log_error(msg: str)   -> None: print(_c("31", "[ERR] ") + msg, file=sys.stderr)
def log_section(msg: str) -> None:
    bar = "═" * 52
    print(f"\n{_c('34', bar)}\n{_c('34', '  ' + msg)}\n{_c('34', bar)}")

# ── Запуск команд на хосте ────────────────────────────────────────────────────

def run_host(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    log_info("$ " + " ".join(str(c) for c in cmd))
    # stdout/stderr не перехватываем — вывод идёт прямо в терминал в реальном времени
    return subprocess.run(cmd, check=check)

def run_host_ok(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

# ── Запуск команд внутри Lima VM ──────────────────────────────────────────────

def vm_run(vm_name: str, script: str, *, check: bool = True) -> subprocess.CompletedProcess:
    preview = script.strip().splitlines()[0][:100]
    log_info(f"[{vm_name}]$ {preview}{'...' if len(script) > 100 else ''}")
    # stdout/stderr не перехватываем — вывод стримится прямо в терминал.
    # stdin=DEVNULL: brew спрашивает "Do you want to proceed?" только если
    # $stdin.tty? && $stdout.tty? (см. /opt/homebrew/Library/Homebrew/ask.rb).
    # Перенаправляем stdin в /dev/null — $stdin.tty? вернёт false, вопрос не задаётся.
    return subprocess.run(
        ["limactl", "shell", "--tty=false", vm_name, "--", "bash", "-c", script],
        check=check,
        stdin=subprocess.DEVNULL,
    )

def vm_run_ok(vm_name: str, script: str) -> bool:
    try:
        subprocess.run(
            ["limactl", "shell", vm_name, "--", "bash", "-c", script],
            check=True, capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False

# ── Парсинг macos.yml ─────────────────────────────────────────────────────────

class WorkflowStep(NamedTuple):
    name: str   # значение поля "name:"
    run: str    # значение поля "run:" (многострочный shell-скрипт)


class MacosWorkflow(NamedTuple):
    cmake_flags: str          # содержимое env.CMAKE_FLAGS
    install_steps: list[WorkflowStep]   # шаги до компиляции
    build_dir: str            # директория сборки из cmake-команды ("build_debug")
    test_steps: list[WorkflowStep]      # шаги "Run tests (*)"


def _strip_yaml_comments(script: str) -> str:
    """Убирает inline-комментарии из shell-скрипта (# ...) для чистоты."""
    # Оставляем строки как есть — bash сам проигнорирует комментарии
    return script


def parse_macos_workflow(workflow_path: Path) -> MacosWorkflow:
    """
    Читает macos.yml и извлекает:
    - CMAKE_FLAGS из env секции job
    - run: блоки всех шагов с name: (кроме uses:)
    - Разделяет на install-шаги и test-шаги ("Run tests (*)")
    """
    raw = workflow_path.read_text()
    doc = yaml.safe_load(raw)

    job = doc["jobs"]["macos"]

    # CMAKE_FLAGS: YAML >- скалярный блок, pyyaml сворачивает в одну строку
    cmake_flags: str = job["env"]["CMAKE_FLAGS"]

    steps_raw: list[dict] = job["steps"]

    install_steps: list[WorkflowStep] = []
    test_steps:    list[WorkflowStep] = []
    build_dir = "build_debug"

    for step in steps_raw:
        name: str = step.get("name", "")
        run:  str = step.get("run", "")

        if not run:
            continue  # uses: шаги пропускаем

        # Извлекаем build_dir из cmake-команды
        m = re.search(r"cmake\s+-S[./\s]+\s+-B[./]*(\S+)", run)
        if m:
            build_dir = m.group(1).lstrip("./")

        if name.startswith("Run tests"):
            # "Run tests (universal)" -> suite = "universal"
            m = re.match(r"Run tests \((.+)\)", name)
            suite_name = m.group(1) if m else name
            test_steps.append(WorkflowStep(suite_name, run))
        else:
            install_steps.append(WorkflowStep(name, run))

    return MacosWorkflow(
        cmake_flags=cmake_flags,
        install_steps=install_steps,
        build_dir=build_dir,
        test_steps=test_steps,
    )

# ── Lima VM ───────────────────────────────────────────────────────────────────

def make_lima_config(repo_root: Path, cpus: int, memory_gb: int, disk_gb: int) -> str:
    # Lima на macOS guest монтирует через virtiofs-симлинк.
    # Корень / — read-only (SSV), поэтому Lima кладёт шары в
    # /Volumes/My Shared Files/<id> и создаёт симлинк на mountPoint.
    # Симлинк создаётся через fake-cloud-init ДО user-provision скриптов,
    # поэтому mountPoint должен быть в уже существующей директории.
    # /tmp всегда существует и writable — используем его.
    mount_point = "/tmp/userver"

    return textwrap.dedent(f"""\
        # userver CI — macOS 15 (Sequoia, aarch64)
        # Соответствует GitHub Actions macos-latest / macos-15
        minimumLimaVersion: 2.1.0

        base:
        - template:_images/macos-15
        - template:_default/mounts

        cpus: {cpus}
        memory: "{memory_gb}GiB"
        disk: "{disk_gb}GiB"

        # macOS installer требует дисплей — без него установка падает с VZErrorDomain.
        video:
          display: "default"

        mounts:
        - location: "{repo_root}"
          mountPoint: "{mount_point}"
          writable: true

        provision:
        - mode: data
          path: /usr/local/bin/lima-sudo-askpass.sh
          permissions: 755
          content: |
            #!/bin/sh
            set -eu
            cat "$HOME/password"

        - mode: user
          script: |
            #!/bin/bash
            set -eux -o pipefail
            [ -e /opt/homebrew ] && exit 0
            curl -o homebrew-install.sh -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh
            SUDO_ASKPASS=/usr/local/bin/lima-sudo-askpass.sh NONINTERACTIVE=1 /bin/bash homebrew-install.sh
            rm -f homebrew-install.sh
            echo 'eval "$(/opt/homebrew/bin/brew shellenv zsh)"' >> ~/.zprofile
            echo 'eval "$(/opt/homebrew/bin/brew shellenv bash)"' >> ~/.bash_profile

        message: |
          macOS 15 CI VM готова. Репозиторий смонтирован в {mount_point}.
    """)


def vm_list() -> list[tuple[str, str]]:
    """Возвращает список (name, status) всех Lima VM."""
    r = subprocess.run(
        ["limactl", "list", "--format", "{{.Name}}\t{{.Status}}"],
        capture_output=True, text=True,
    )
    result = []
    for line in r.stdout.splitlines():
        parts = line.strip().split("\t")
        if len(parts) == 2:
            result.append((parts[0], parts[1]))
    return result


def vm_exists(vm_name: str) -> bool:
    return any(name == vm_name for name, _ in vm_list())


def warn_stale_vms(vm_name: str) -> None:
    """Предупреждает о VM которые занимают место и не нужны."""
    all_vms = vm_list()
    if not all_vms:
        return

    other_vms = [(n, s) for n, s in all_vms if n != vm_name]
    if not other_vms:
        return

    log_warn("Найдены другие Lima VM которые занимают место на диске:")
    for name, status in other_vms:
        log_warn(f"  {name}  ({status})")
    log_warn("Удалить ненужные VM:")
    for name, _ in other_vms:
        log_warn(f"  limactl delete --force {name}")


def vm_is_running(vm_name: str) -> bool:
    r = subprocess.run(
        ["limactl", "list", "--format", "{{.Name}} {{.Status}}"],
        capture_output=True, text=True,
    )
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == vm_name:
            return parts[1] == "Running"
    return False


VM_REPO_PATH = "/tmp/userver"

_LIMA_DIR = Path.home() / ".lima"


def _delete_vm(vm_name: str) -> None:
    """Удаляет VM и все остаточные файлы которые Lima оставляет после сбоя."""
    log_info(f"Удаление VM {vm_name}...")
    run_host(["limactl", "delete", "--force", vm_name], check=False)

    # Lima иногда оставляет директорию после сбойного создания.
    # Остатки диска вызывают ошибку VZErrorDomain/DFU при следующем create.
    vm_dir = _LIMA_DIR / vm_name
    if vm_dir.exists():
        log_info(f"Удаление остаточных файлов: {vm_dir}")
        import shutil
        shutil.rmtree(vm_dir)
        log_ok(f"Директория {vm_dir} удалена")


def _create_vm(vm_name: str, repo_root: Path, args: argparse.Namespace) -> None:
    """Создаёт Lima VM. При ошибке даёт понятную инструкцию."""
    log_info("Создание VM (первый раз: скачивание macOS 15 IPSW ~13GB, только один раз)...")
    log_warn(
        "Откроется окно macOS Installer — это нормально при первом создании.\n"
        "  После установки GUI больше не понадобится."
    )

    config = make_lima_config(repo_root, args.cpus, args.memory, args.disk)
    config_path = Path(f"/tmp/lima-{vm_name}.yaml")
    config_path.write_text(config)

    try:
        run_host(["limactl", "create", f"--name={vm_name}", str(config_path)])
    except subprocess.CalledProcessError as e:
        log_error("limactl create завершился с ошибкой.")
        log_error(
            "Если в логе есть 'VZErrorDomain' / 'DFU mode' — это баг Apple VZ\n"
            "  при повторной установке. Очистите остатки и попробуйте снова:\n"
            f"\n"
            f"    limactl delete --force {vm_name}\n"
            f"    rm -rf ~/.lima/{vm_name}\n"
            f"    {sys.argv[0]} --recreate\n"
        )
        sys.exit(e.returncode)
    finally:
        config_path.unlink(missing_ok=True)

    log_ok("VM создана")


def _vm_start_time(vm_name: str) -> float:
    """
    Возвращает unix-время последнего старта VM.
    Используем mtime pid-файла Lima — он обновляется при каждом старте.
    Если файл не найден — возвращаем 0 (считаем VM устаревшей).
    """
    # Lima хранит pid hostagent в ~/.lima/<name>/ha.pid
    pid_file = _LIMA_DIR / vm_name / "ha.pid"
    if pid_file.exists():
        return pid_file.stat().st_mtime
    return 0.0


def _repo_newest_mtime(repo_root: Path) -> tuple[float, Path]:
    """Возвращает (mtime, path) самого свежего файла в репозитории."""
    newest_mtime = 0.0
    newest_path = repo_root
    # Обходим только tracked файлы через git — быстро и не цепляет build/venv
    r = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        capture_output=True,
    )
    for rel in r.stdout.split(b"\x00"):
        if not rel:
            continue
        p = repo_root / rel.decode(errors="replace")
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if mt > newest_mtime:
            newest_mtime = mt
            newest_path = p
    return newest_mtime, newest_path


def _restart_vm(vm_name: str) -> None:
    log_info(f"Перезапуск VM {vm_name} для сброса virtiofs кэша...")
    run_host(["limactl", "stop", vm_name])
    run_host(["limactl", "start", vm_name])
    log_ok("VM перезапущена, virtiofs кэш сброшен")


def ensure_vm(vm_name: str, repo_root: Path, args: argparse.Namespace) -> None:
    log_section(f"Lima VM: {vm_name} (macOS 15 Sequoia)")

    warn_stale_vms(vm_name)

    if args.recreate and vm_exists(vm_name):
        _delete_vm(vm_name)

    if not vm_exists(vm_name):
        _create_vm(vm_name, repo_root, args)
    else:
        log_ok(f"VM {vm_name} уже существует")

    if not vm_is_running(vm_name):
        log_info(f"Запуск {vm_name}...")
        log_warn(
            f"Чтобы следить за прогрессом загрузки в другом терминале:\n"
            f"  tail -f ~/.lima/{vm_name}/serialv.log"
        )
        run_host(["limactl", "start", vm_name])
    else:
        log_ok(f"VM {vm_name} уже запущена")
        # Проверяем virtiofs кэш: если репозиторий менялся после последнего
        # старта VM — перезапускаем её. Lima virtiofs может отдавать старые
        # данные файлов изменённых на хосте пока VM была запущена.
        vm_start = _vm_start_time(vm_name)
        repo_mtime, repo_newest = _repo_newest_mtime(repo_root)
        if repo_mtime > vm_start:
            import datetime
            log_warn(
                f"Файлы репозитория новее последнего старта VM:\n"
                f"  новый файл : {repo_newest.relative_to(repo_root)}\n"
                f"  изменён   : {datetime.datetime.fromtimestamp(repo_mtime):%Y-%m-%d %H:%M:%S}\n"
                f"  VM запущена: {datetime.datetime.fromtimestamp(vm_start):%Y-%m-%d %H:%M:%S}\n"
                f"  Перезапускаю VM для сброса virtiofs кэша..."
            )
            _restart_vm(vm_name)
        else:
            log_ok("virtiofs кэш актуален (VM запущена после последних изменений)")

    if not vm_run_ok(vm_name, f"test -d {VM_REPO_PATH}/.git"):
        log_error(f"Репозиторий недоступен в VM как {VM_REPO_PATH}")
        log_error("Попробуйте --recreate чтобы пересоздать VM с правильным конфигом")
        sys.exit(1)
    log_ok(f"Репозиторий {VM_REPO_PATH} доступен")


# ── Адаптация run: скриптов для VM ────────────────────────────────────────────

# В macos.yml пути относительные (cd build_debug/..., cmake -S./ -B./build_debug).
# Внутри VM репозиторий в /userver, а build_dir — в /tmp/userver-build.
# Подменяем пути и добавляем brew shellenv.

_BREW_INIT = 'eval "$(/opt/homebrew/bin/brew shellenv bash)"'
_CCACHE_ENV = (
    'export CCACHE_DIR="$HOME/Library/Caches/ccache-userver-ci"\n'
    'export CCACHE_NOHASHDIR=true'
)
# На GitHub runner CPM_SOURCE_CACHE=/Users/runner/Library/Caches/CPM (macos.yml env:).
# Без CPM_SOURCE_CACHE CPM использует FetchContent, который при повторном cmake
# configure может запустить update-step (git checkout) без patch-step, оставляя
# исходники без патчей. С CPM_SOURCE_CACHE CPM кэширует пропатченные исходники.
_CPM_ENV = 'export CPM_SOURCE_CACHE="$HOME/Library/Caches/CPM"'
# На GitHub runner brew не спрашивает подтверждений (нет TTY).
# В Lima shell TTY может быть — brew начинает задавать вопросы.
# Основная защита: stdin=subprocess.DEVNULL в vm_run() — $stdin.tty? == false
# => ask.rb::confirm? возвращает false без вопроса (см. ask.rb строка 13).
# NONINTERACTIVE=1 — дополнительно отключает интерактивные запросы brew.
_BREW_NONINTERACTIVE = 'export HOMEBREW_NO_INSTALL_UPGRADE=1 NONINTERACTIVE=1'

# ── Переменные окружения которых нет в Lima VM, но есть на GitHub runner ──────

# PostgreSQL на macOS использует fork() и проверяет что процесс однопоточный.
# Без валидной локали locale-подсистема загружает многопоточные библиотеки,
# и PostgreSQL падает: "FATAL: postmaster became multithreaded during startup".
# GitHub runner имеет LANG/LC_ALL в дефолтном профиле; Lima VM — нет.
# LC_ALL=C — минимальная валидная локаль, всегда доступна на macOS.
_LOCALE_ENV = 'export LC_ALL=C LANG=C'

# На macOS (особенно в Lima VM) бинарники установленные/скачанные через brew
# или testsuite могут иметь com.apple.quarantine атрибут. Gatekeeper убивает
# такие бинарники сигналом SIGKILL ("Killed: 9"). На GitHub runner quarantine
# не применяется. Снимаем quarantine-атрибуты с:
# - brew-installed clickhouse бинарника (используется testsuite's service-clickhouse)
# - всех файлов в testsuite env директориях (.yasuite-*)
_QUARANTINE_FIX = (
    'xattr -c "$(which clickhouse 2>/dev/null)" 2>/dev/null || true\n'
    'find /private/tmp/.yasuite-* -exec xattr -c {} + 2>/dev/null || true'
)


def adapt_script(script: str, wf_build_dir: str, vm_build_dir: str, vm_repo_path: str) -> str:
    """
    Адаптирует run: блок из macos.yml для выполнения внутри VM:
    - Добавляет brew shellenv, ccache env, locale (LC_ALL=C) в начало
    - Снимает quarantine-атрибуты со скачанных бинарников (ClickHouse)
    - Заменяет относительные пути репозитория на vm_repo_path
    - Заменяет build_dir из workflow на vm_build_dir
    """
    s = script

    # build_debug -> vm_build_dir (делаем до замены -B./ чтобы не было -B.//tmp/...)
    s = s.replace(f"-B./{wf_build_dir}", f"-B{vm_build_dir}")
    s = s.replace(f"-B {wf_build_dir}", f"-B{vm_build_dir}")
    s = s.replace(wf_build_dir, vm_build_dir)

    # Относительные пути к репозиторию -> абсолютные внутри VM
    s = s.replace("-S./", f"-S {vm_repo_path} ")
    s = s.replace("-S ./ ", f"-S {vm_repo_path} ")
    s = s.replace("scripts/docs/en/deps/macos.md",
                  f"{vm_repo_path}/scripts/docs/en/deps/macos.md")

    # Убираем SDKROOT — не нужен, Lima сама настраивает среду
    s = re.sub(r"export SDKROOT=.*\n?", "", s)

    header = (
        f"set -euxo pipefail\n"
        f"{_BREW_INIT}\n"
        f"{_BREW_NONINTERACTIVE}\n"
        f"{_CCACHE_ENV}\n"
        f"{_CPM_ENV}\n"
        f"{_LOCALE_ENV}\n"
        f"{_QUARANTINE_FIX}\n"
    )
    return header + s


# ── Выполнение шагов ──────────────────────────────────────────────────────────

_SKIP_STEP_NAMES = {
    "Restore cached directories",
    "Save cached directories",
    "Show cache stats",
}

# Шаги cmake/compile идентифицируются по имени
_BUILD_STEP_KEYWORDS = ("cmake", "compile", "reconfigure")


def _is_build_step(name: str) -> bool:
    return any(kw in name.lower() for kw in _BUILD_STEP_KEYWORDS)


def _run_steps(
    vm_name: str,
    steps: list[WorkflowStep],
    wf: "MacosWorkflow",
    vm_build_dir: str,
    vm_repo_path: str,
) -> None:
    for step in steps:
        if step.name in _SKIP_STEP_NAMES:
            log_info(f"Пропуск: {step.name} (GitHub Actions cache, не нужен локально)")
            continue

        log_section(f"Шаг: {step.name}")
        script = adapt_script(step.run, wf.build_dir, vm_build_dir, vm_repo_path)

        # Для cmake-шагов подставляем CMAKE_FLAGS явно (в VM env не прокидывается)
        if _is_build_step(step.name):
            cmake_flags_export = f'export CMAKE_FLAGS="{wf.cmake_flags}"\n'
            script = script.replace(
                f"{_BREW_INIT}\n",
                f"{_BREW_INIT}\n{cmake_flags_export}",
            )

        vm_run(vm_name, script)


def run_install_steps(
    vm_name: str,
    wf: "MacosWorkflow",
    vm_build_dir: str,
    vm_repo_path: str,
) -> None:
    """Запускает только brew-install шаги (не cmake/compile)."""
    steps = [s for s in wf.install_steps if not _is_build_step(s.name)]
    _run_steps(vm_name, steps, wf, vm_build_dir, vm_repo_path)


def run_build_steps(
    vm_name: str,
    wf: "MacosWorkflow",
    vm_build_dir: str,
    vm_repo_path: str,
) -> None:
    """Запускает только cmake configure + compile шаги."""
    steps = [s for s in wf.install_steps if _is_build_step(s.name)]
    _run_steps(vm_name, steps, wf, vm_build_dir, vm_repo_path)


def run_test_step(
    vm_name: str,
    step: WorkflowStep,
    wf_build_dir: str,
    vm_build_dir: str,
    vm_repo_path: str,
) -> bool:
    log_section(f"Тесты: {step.name}")
    script = adapt_script(step.run, wf_build_dir, vm_build_dir, vm_repo_path)
    result = subprocess.run(
        ["limactl", "shell", vm_name, "--", "bash", "-c", script],
    )
    return result.returncode == 0


def run_all_tests(
    vm_name: str,
    wf: MacosWorkflow,
    vm_build_dir: str,
    vm_repo_path: str,
    fail_fast: bool = False,
) -> bool:
    log_section("Запуск всех тестов")
    failed: list[str] = []

    for step in wf.test_steps:
        if run_test_step(vm_name, step, wf.build_dir, vm_build_dir, vm_repo_path):
            log_ok(f"PASS  {step.name}")
        else:
            log_warn(f"FAIL  {step.name}")
            failed.append(step.name)
            if fail_fast:
                log_error(f"--fail-fast: остановка после первого провала ({step.name})")
                return False

    if failed:
        log_error("Провалившиеся: " + ", ".join(failed))
        return False

    log_ok("Все тесты прошли!")
    return True


# ── main ──────────────────────────────────────────────────────────────────────

def parse_args(test_suites: list[str]) -> argparse.Namespace:
    host_cpus = os.cpu_count() or 4
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--vm-name",   default="userver-ci")
    p.add_argument("--build-dir", default="/tmp/userver-build",
                   help="путь внутри VM (default: /tmp/userver-build)")
    p.add_argument("--jobs",   type=int, default=host_cpus,
                   help=f"параллельность cmake --build (default: {host_cpus})")
    p.add_argument("--cpus",   type=int, default=host_cpus,
                   help=f"vCPU для VM (default: {host_cpus} — все ядра)")
    p.add_argument("--memory", type=int, default=8,
                   help="RAM для VM в GiB (default: 8)")
    p.add_argument("--disk",   type=int, default=60,
                   help="диск VM в GiB (default: 60)")
    p.add_argument("--skip-install", action="store_true")
    p.add_argument("--skip-build",   action="store_true")
    p.add_argument("--skip-tests",   action="store_true")
    p.add_argument("--test", metavar="SUITE", choices=test_suites,
                   help="запустить только один suite: " + " | ".join(test_suites))
    p.add_argument("--fail-fast", action="store_true",
                   help="остановиться после первого провалившегося теста (по умолчанию: запустить все)")
    p.add_argument("--dump-logs", metavar="DIR", default="test-results",
                   help="скопировать логи тестов из VM в DIR на хосте (default: test-results)")
    p.add_argument("--destroy",  action="store_true", help="удалить VM после завершения")
    p.add_argument("--recreate", action="store_true", help="пересоздать VM с нуля")
    return p.parse_args()


def _limactl_copy_logs(vm_name: str, remote_src: str, local_dest: Path) -> None:
    """Копирует только *.log файлы из remote_src в local_dest через rsync --include."""
    local_dest.mkdir(parents=True, exist_ok=True)
    run_host(
        [
            "limactl", "copy", "--tty=false", "--backend=rsync", "-r",
            f"{vm_name}:{remote_src}/", str(local_dest),
        ],
        check=False,
    )


def dump_logs(vm_name: str, vm_build_dir: str, dest: Path) -> None:
    """
    Копирует логи тестов из VM на хост:
      <dest>/ctest/<suite>/  — *.log из <suite>/Testing/Temporary/ (LastTest, LastTestsFailed, service.log)
      <dest>/xml/            — gtest *.xml из <vm_build_dir>/test-results/
      <dest>/yasuite/<svc>/  — только logs/ из /tmp/.yasuite-*/services/<svc>/logs/
    """
    dest.mkdir(parents=True, exist_ok=True)
    log_section(f"Дамп логов -> {dest}")

    # ── CTest: все *.log из Testing/Temporary/ на всех уровнях ──────────────
    # Структура:
    #   <suite>/Testing/Temporary/LastTest.log          (сводный лог suite)
    #   <suite>/<test>/Testing/Temporary/service.log    (лог сервиса для каждого теста)
    ctest_dest = dest / "ctest"
    r = subprocess.run(
        ["limactl", "shell", "--tty=false", vm_name, "--",
         "bash", "-c", f"find {vm_build_dir} -name 'Testing' -type d 2>/dev/null"],
        capture_output=True, text=True,
    )
    for testing_dir in r.stdout.splitlines():
        testing_dir = testing_dir.strip()
        if not testing_dir:
            continue
        # /tmp/userver-build/samples/postgres_auth/Testing
        # relative: samples/postgres_auth  ->  suite=samples, sub=postgres_auth
        rel = testing_dir.replace(vm_build_dir, "").strip("/")  # e.g. "samples/postgres_auth/Testing"
        parts = rel.split("/")
        suite = parts[0]
        sub = "/".join(parts[1:-1]) if len(parts) > 2 else ""  # "postgres_auth" or ""
        local_tmp = ctest_dest / suite / sub if sub else ctest_dest / suite
        tmp_dir = testing_dir + "/Temporary"
        _limactl_copy_logs(vm_name, tmp_dir, local_tmp)

    log_ok(f"  ctest/")

    # ── GTest XML результаты ──────────────────────────────────────────────────
    xml_dest = dest / "xml"
    xml_dest.mkdir(exist_ok=True)
    run_host(
        ["limactl", "copy", "--tty=false", "-r",
         f"{vm_name}:{vm_build_dir}/test-results/", str(xml_dest)],
        check=False,
    )
    log_ok("  xml/")

    # ── Testsuite: только logs/ из каждого сервиса ───────────────────────────
    yasuite_dest = dest / "yasuite"
    r = subprocess.run(
        ["limactl", "shell", "--tty=false", vm_name, "--",
         "bash", "-c", "echo /tmp/.yasuite-*"],
        capture_output=True, text=True,
    )
    yasuite_dirs = [d for d in r.stdout.split() if ".yasuite" in d]

    for yasuite_dir in yasuite_dirs:
        # копируем только services/<svc>/logs/, пропускаем data/
        r2 = subprocess.run(
            ["limactl", "shell", "--tty=false", vm_name, "--",
             "bash", "-c", f"find {yasuite_dir}/services -maxdepth 2 -name logs -type d 2>/dev/null"],
            capture_output=True, text=True,
        )
        for logs_dir in r2.stdout.splitlines():
            logs_dir = logs_dir.strip()
            if not logs_dir:
                continue
            # .../services/postgresql/logs -> svc=postgresql
            svc = logs_dir.split("/services/")[1].split("/")[0]
            _limactl_copy_logs(vm_name, logs_dir, yasuite_dest / svc)
            log_ok(f"  yasuite/{svc}/")

    log_ok(f"Логи скопированы в {dest}")
    log_info(f"CTest логи  : {ctest_dest}")
    log_info(f"GTest XML   : {xml_dest}")
    log_info(f"Testsuite   : {yasuite_dest}")


def main() -> None:
    if not run_host_ok(["limactl", "--version"]):
        log_error("limactl не найден. Установите: brew install lima")
        sys.exit(1)

    repo_root = _SCRIPT_DIR.parent.parent
    workflow_path = repo_root / ".github" / "workflows" / "macos.yml"

    if not workflow_path.exists():
        log_error(f"Workflow не найден: {workflow_path}")
        sys.exit(1)

    log_info(f"Читаю {workflow_path.relative_to(repo_root)}...")
    wf = parse_macos_workflow(workflow_path)

    test_suites = [s.name for s in wf.test_steps]
    args = parse_args(test_suites)

    log_section("userver macOS CI — Lima macOS 15 VM")
    log_info(f"Workflow     : {workflow_path.relative_to(repo_root)}")
    log_info(f"CMAKE_FLAGS  : {wf.cmake_flags}")
    log_info(f"Test suites  : {', '.join(test_suites)}")
    log_info(f"Репозиторий  : {repo_root}")
    log_info(f"VM name      : {args.vm_name}")
    log_info(f"Build dir    : {args.build_dir} (внутри VM)")
    log_info(f"VM resources : {args.cpus} vCPU, {args.memory}GiB RAM, {args.disk}GiB disk")
    log_info(f"Jobs         : {args.jobs}")

    ensure_vm(args.vm_name, repo_root, args)

    if not args.skip_install:
        run_install_steps(args.vm_name, wf, args.build_dir, VM_REPO_PATH)
    else:
        log_info("--skip-install: пропуск brew install шагов")

    if not args.skip_build:
        run_build_steps(args.vm_name, wf, args.build_dir, VM_REPO_PATH)
    else:
        log_info("--skip-build: пропуск cmake/compile шагов")

    test_failed = False
    if not args.skip_tests:
        if args.test:
            step = next(s for s in wf.test_steps if s.name == args.test)
            ok = run_test_step(args.vm_name, step, wf.build_dir, args.build_dir, VM_REPO_PATH)
        else:
            ok = run_all_tests(args.vm_name, wf, args.build_dir, VM_REPO_PATH, fail_fast=args.fail_fast)
        if not ok:
            test_failed = True
    else:
        log_info("--skip-tests: пропуск тестов")

    if not args.skip_tests and args.dump_logs:
        dump_logs(args.vm_name, args.build_dir, Path(args.dump_logs))

    if test_failed:
        sys.exit(1)

    log_section("Готово!")
    log_info(f"VM           : limactl shell {args.vm_name}")
    log_info(f"Остановить   : limactl stop {args.vm_name}")
    log_info(f"Удалить      : limactl delete --force {args.vm_name}")

    if args.destroy:
        log_info("--destroy: удаление VM...")
        run_host(["limactl", "delete", "--force", args.vm_name])
        log_ok("VM удалена")


if __name__ == "__main__":
    main()
