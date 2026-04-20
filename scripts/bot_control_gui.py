import json
import os
import queue
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk


def resolve_root() -> Path:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent)
    candidates.append(Path.cwd().resolve())
    candidates.append(Path(__file__).resolve().parent.parent)

    for candidate in candidates:
        if (candidate / "scripts").exists():
            return candidate

    return Path(__file__).resolve().parent.parent


ROOT = resolve_root()
SETTINGS_PATH = ROOT / ".control_center.local.json"
README_PATH = ROOT / "README.md"
ENV_PATH = ROOT / "KGTD.env"
PS = "powershell.exe"
DEFAULTS = {
    "tailscale_ip": "",
    "bridge_token": "secretkeyvipik",
    "vps_host": "206.245.134.221",
    "vps_user": "root",
    "ssh_key": str(Path(os.environ.get("USERPROFILE", "")) / ".ssh" / "disbot_vps_ed25519"),
    "admin_url": "http://100.90.24.117:8080/",
}
SCENARIOS_TEXT = (
    "Обычная работа:\n"
    "1. Обновить Git.\n"
    "2. Скачать свежую messages.db.\n"
    "3. Открыть обучение моделей и выбрать нужный режим.\n"
    "4. Если нужен GPT с ПК для VPS: запустить bridge и связать VPS.\n\n"
    "Если нужен только прод-контроль:\n"
    "1. Проверить статус VPS.\n"
    "2. Открыть веб-панель.\n"
    "3. При необходимости включить или выключить удалённую тяжёлую модель.\n\n"
    "Если нужен новый VPS:\n"
    "1. Запустить установку нового VPS.\n"
    "2. Заполнить KGTD.env на сервере.\n"
    "3. Отправить лёгкие модели и базы на VPS.\n\n"
    "Если что-то сломалось:\n"
    "1. Сначала посмотри лог в этой программе.\n"
    "2. Проверь Git статус и статус VPS.\n"
    "3. Только потом лезь в ручные скрипты."
)


class StatusCard(ttk.Frame):
    def __init__(self, master, title: str):
        super().__init__(master, padding=10)
        self.columnconfigure(0, weight=1)
        ttk.Label(self, text=title, font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.value = ttk.Label(self, text="Неизвестно", font=("Segoe UI", 11))
        self.value.grid(row=1, column=0, sticky="w", pady=(6, 0))

    def set(self, text: str):
        self.value.configure(text=text)


class BotControlApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ViPik Bot Control")
        self.root.geometry("1180x820")
        self.root.minsize(1080, 720)

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.bridge_process: subprocess.Popen | None = None

        self.settings = self._load_settings()

        self.tailscale_ip_var = tk.StringVar(value=self.settings.get("tailscale_ip") or self._detect_tailscale_ip())
        self.bridge_token_var = tk.StringVar(value=self.settings.get("bridge_token", DEFAULTS["bridge_token"]))
        self.vps_host_var = tk.StringVar(value=self.settings.get("vps_host", DEFAULTS["vps_host"]))
        self.vps_user_var = tk.StringVar(value=self.settings.get("vps_user", DEFAULTS["vps_user"]))
        self.ssh_key_var = tk.StringVar(value=self.settings.get("ssh_key", DEFAULTS["ssh_key"]))
        self.admin_url_var = tk.StringVar(value=self.settings.get("admin_url", DEFAULTS["admin_url"]))
        self.status_var = tk.StringVar(value="Готово.")

        self._build_ui()
        self._start_log_pump()
        self.refresh_statuses()
        self._log("GUI запущен. Теперь это основной однооконный интерфейс.")

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass

        header = ttk.Frame(self.root, padding=(16, 14))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="ViPik Bot Control", font=("Segoe UI", 22, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Одно окно для bridge, VPS, обучения и обслуживания проекта",
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(header, textvariable=self.status_var, foreground="#136f2d").grid(row=0, column=1, rowspan=2, sticky="e")

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        self.dashboard_tab = ttk.Frame(notebook, padding=14)
        self.control_tab = ttk.Frame(notebook, padding=14)
        self.settings_tab = ttk.Frame(notebook, padding=14)
        self.env_tab = ttk.Frame(notebook, padding=14)
        self.log_tab = ttk.Frame(notebook, padding=14)
        self.help_tab = ttk.Frame(notebook, padding=14)

        notebook.add(self.dashboard_tab, text="Обзор")
        notebook.add(self.control_tab, text="Управление")
        notebook.add(self.settings_tab, text="Настройки")
        notebook.add(self.env_tab, text="KGTD.env")
        notebook.add(self.log_tab, text="Лог")
        notebook.add(self.help_tab, text="Сценарии")

        self._build_dashboard_tab()
        self._build_control_tab()
        self._build_settings_tab()
        self._build_env_tab()
        self._build_log_tab()
        self._build_help_tab()

    def _build_dashboard_tab(self):
        self.dashboard_tab.columnconfigure((0, 1, 2), weight=1)
        for idx in range(3):
            self.dashboard_tab.rowconfigure(idx, weight=0)

        self.bridge_card = StatusCard(self.dashboard_tab, "Bridge")
        self.bridge_card.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

        self.vps_card = StatusCard(self.dashboard_tab, "VPS")
        self.vps_card.grid(row=0, column=1, sticky="nsew", padx=6, pady=6)

        self.tailnet_card = StatusCard(self.dashboard_tab, "Tailscale")
        self.tailnet_card.grid(row=0, column=2, sticky="nsew", padx=6, pady=6)

        quick = ttk.LabelFrame(self.dashboard_tab, text="Быстрые действия", padding=12)
        quick.grid(row=1, column=0, columnspan=3, sticky="ew", padx=6, pady=6)
        for col in range(4):
            quick.columnconfigure(col, weight=1)

        ttk.Button(quick, text="Запустить bridge", command=self.start_bridge).grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(quick, text="Связать VPS с ПК", command=self.link_vps).grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(quick, text="Запустить комплект", command=self.start_full_kit).grid(row=0, column=2, sticky="ew", padx=4, pady=4)
        ttk.Button(quick, text="Обновить статусы", command=self.refresh_statuses).grid(row=0, column=3, sticky="ew", padx=4, pady=4)
        ttk.Button(quick, text="Git pull", command=self.git_pull).grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(quick, text="Скачать messages.db", command=self.sync_messages).grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(quick, text="Открыть обучение", command=self.open_training_menu).grid(row=1, column=2, sticky="ew", padx=4, pady=4)
        ttk.Button(quick, text="Статус VPS", command=self.show_vps_status).grid(row=1, column=3, sticky="ew", padx=4, pady=4)

        help_box = ttk.LabelFrame(self.dashboard_tab, text="Как пользоваться", padding=12)
        help_box.grid(row=2, column=0, columnspan=3, sticky="nsew", padx=6, pady=6)
        help_text = (
            "Обычная схема:\n"
            "1. Нажми «Запустить bridge».\n"
            "2. Нажми «Связать VPS с ПК».\n"
            "3. Пользуйся /пародия -> нейро в Discord.\n"
            "4. Когда больше не нужно — «Отключить тяжёлые модели на VPS» и «Остановить bridge».\n\n"
            "Если нужно обучение:\n"
            "1. Скачай свежую messages.db.\n"
            "2. Открой обучение моделей.\n"
            "3. Выбери только GPT или нужный режим."
        )
        ttk.Label(help_box, text=help_text, justify="left").grid(row=0, column=0, sticky="w")

    def _build_control_tab(self):
        self.control_tab.columnconfigure(0, weight=1)
        self.control_tab.columnconfigure(1, weight=1)

        bridge_box = ttk.LabelFrame(self.control_tab, text="Bridge и VPS", padding=12)
        bridge_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        bridge_box.columnconfigure(0, weight=1)

        for idx, (text, command) in enumerate([
            ("Запустить bridge", self.start_bridge),
            ("Остановить bridge", self.stop_bridge),
            ("Связать VPS с ПК", self.link_vps),
            ("Отключить тяжёлые модели на VPS", self.disable_remote_models),
            ("Запустить комплект", self.start_full_kit),
            ("Статус VPS", self.show_vps_status),
            ("Открыть веб-панель", self.open_admin_panel),
            ("Обновить статусы", self.refresh_statuses),
        ]):
            ttk.Button(bridge_box, text=text, command=command).grid(row=idx, column=0, sticky="ew", pady=4)

        project_box = ttk.LabelFrame(self.control_tab, text="Проект", padding=12)
        project_box.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        project_box.columnconfigure(0, weight=1)

        for idx, (text, command) in enumerate([
            ("Установить локальные зависимости", self.install_dependencies),
            ("Запустить бота локально", self.run_bot_locally),
            ("Git pull", self.git_pull),
            ("Показать Git status", self.git_status),
            ("Commit и push", self.git_commit_push),
            ("Скачать messages.db с VPS", self.sync_messages),
            ("Поставить ежедневную sync-задачу", self.install_daily_sync_task),
            ("Отправить лёгкие модели и базы на VPS", self.sync_training),
            ("Открыть обучение моделей", self.open_training_menu),
            ("Поставить бота на новый VPS", self.install_new_vps),
            ("Открыть README", self.open_readme),
            ("Очистить лог", self.clear_log),
        ]):
            ttk.Button(project_box, text=text, command=command).grid(row=idx, column=0, sticky="ew", pady=4)

    def _build_settings_tab(self):
        self.settings_tab.columnconfigure(1, weight=1)
        self.settings_tab.columnconfigure(3, weight=1)

        ttk.Label(self.settings_tab, text="Tailscale IP ПК").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(self.settings_tab, textvariable=self.tailscale_ip_var).grid(row=0, column=1, sticky="ew", padx=(8, 16), pady=6)
        ttk.Button(self.settings_tab, text="Определить IP", command=self.detect_ip_to_field).grid(row=0, column=2, columnspan=2, sticky="ew", pady=6)

        ttk.Label(self.settings_tab, text="Токен bridge").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(self.settings_tab, textvariable=self.bridge_token_var, show="*").grid(row=1, column=1, sticky="ew", padx=(8, 16), pady=6)

        ttk.Label(self.settings_tab, text="VPS host").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(self.settings_tab, textvariable=self.vps_host_var).grid(row=2, column=1, sticky="ew", padx=(8, 16), pady=6)

        ttk.Label(self.settings_tab, text="VPS user").grid(row=2, column=2, sticky="w", pady=6)
        ttk.Entry(self.settings_tab, textvariable=self.vps_user_var).grid(row=2, column=3, sticky="ew", pady=6)

        ttk.Label(self.settings_tab, text="SSH key").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Entry(self.settings_tab, textvariable=self.ssh_key_var).grid(row=3, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=6)

        ttk.Label(self.settings_tab, text="URL веб-панели").grid(row=4, column=0, sticky="w", pady=6)
        ttk.Entry(self.settings_tab, textvariable=self.admin_url_var).grid(row=4, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=6)

        ttk.Button(self.settings_tab, text="Сохранить настройки", command=self.save_settings).grid(row=5, column=0, columnspan=4, sticky="ew", pady=(14, 8))

        note = (
            "Эти настройки сохраняются локально в .control_center.local.json.\n"
            "Секреты бота в KGTD.env здесь не редактируются автоматически, чтобы не сломать прод."
        )
        ttk.Label(self.settings_tab, text=note, justify="left").grid(row=6, column=0, columnspan=4, sticky="w", pady=(8, 0))

    def _build_log_tab(self):
        self.log_tab.columnconfigure(0, weight=1)
        self.log_tab.rowconfigure(0, weight=1)

        self.log_text = tk.Text(self.log_tab, wrap="word", font=("Consolas", 10))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.configure(state="disabled")

        scroll = ttk.Scrollbar(self.log_tab, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

    def _build_help_tab(self):
        self.help_tab.columnconfigure(0, weight=1)
        self.help_tab.rowconfigure(1, weight=1)

        ttk.Label(
            self.help_tab,
            text="Что делать по шагам",
            font=("Segoe UI", 13, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        help_text = tk.Text(self.help_tab, wrap="word", font=("Segoe UI", 11), height=22)
        help_text.grid(row=1, column=0, sticky="nsew")
        help_text.insert("1.0", SCENARIOS_TEXT)
        help_text.configure(state="disabled")

    def _build_env_tab(self):
        self.env_tab.columnconfigure(0, weight=1)
        self.env_tab.rowconfigure(1, weight=1)

        top = ttk.Frame(self.env_tab)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.columnconfigure(0, weight=1)

        ttk.Label(
            top,
            text="Локальный KGTD.env. Это не меняет продовый env на VPS автоматически.",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(top, text="Загрузить KGTD.env", command=self.load_env_file).grid(row=0, column=1, padx=4)
        ttk.Button(top, text="Сохранить KGTD.env", command=self.save_env_file).grid(row=0, column=2, padx=4)

        env_wrap = ttk.Frame(self.env_tab)
        env_wrap.grid(row=1, column=0, sticky="nsew")
        env_wrap.columnconfigure(0, weight=1)
        env_wrap.rowconfigure(0, weight=1)

        self.env_text = tk.Text(env_wrap, wrap="none", font=("Consolas", 10))
        self.env_text.grid(row=0, column=0, sticky="nsew")

        env_scroll_y = ttk.Scrollbar(env_wrap, orient="vertical", command=self.env_text.yview)
        env_scroll_y.grid(row=0, column=1, sticky="ns")
        self.env_text.configure(yscrollcommand=env_scroll_y.set)

        env_scroll_x = ttk.Scrollbar(env_wrap, orient="horizontal", command=self.env_text.xview)
        env_scroll_x.grid(row=1, column=0, sticky="ew")
        self.env_text.configure(xscrollcommand=env_scroll_x.set)

        self.load_env_file()

    def _load_settings(self) -> dict:
        data = dict(DEFAULTS)
        if SETTINGS_PATH.exists():
            try:
                data.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
            except Exception:
                pass
        return data

    def save_settings(self):
        payload = {
            "tailscale_ip": self.tailscale_ip_var.get().strip(),
            "bridge_token": self.bridge_token_var.get().strip(),
            "vps_host": self.vps_host_var.get().strip(),
            "vps_user": self.vps_user_var.get().strip(),
            "ssh_key": self.ssh_key_var.get().strip(),
            "admin_url": self.admin_url_var.get().strip(),
        }
        SETTINGS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.status_var.set("Настройки сохранены.")
        self._log("Локальные настройки сохранены.")

    def detect_ip_to_field(self):
        ip = self._detect_tailscale_ip()
        if ip:
            self.tailscale_ip_var.set(ip)
            self.status_var.set(f"Tailscale IP: {ip}")
            self._log(f"Определён Tailscale IP: {ip}")
        else:
            messagebox.showwarning("Tailscale", "Не удалось определить Tailscale IP.")

    def _detect_tailscale_ip(self) -> str:
        try:
            result = subprocess.run(
                ["tailscale", "ip", "-4"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=10,
            )
            if result.returncode == 0:
                return (result.stdout.splitlines() or [""])[0].strip()
        except Exception:
            pass
        return ""

    def _start_log_pump(self):
        self.root.after(150, self._pump_logs)

    def _pump_logs(self):
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(150, self._pump_logs)

    def _log(self, message: str):
        self.log_queue.put(message)

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._log("Лог очищен.")

    def load_env_file(self):
        self.env_text.delete("1.0", "end")
        if ENV_PATH.exists():
            self.env_text.insert("1.0", ENV_PATH.read_text(encoding="utf-8"))
            self._log("Загружен локальный KGTD.env.")
        else:
            self.env_text.insert("1.0", "# Локальный KGTD.env не найден\n")
            self._log("Локальный KGTD.env не найден.")

    def save_env_file(self):
        content = self.env_text.get("1.0", "end-1c")
        ENV_PATH.write_text(content + ("\n" if content and not content.endswith("\n") else ""), encoding="utf-8")
        self.status_var.set("KGTD.env сохранён.")
        self._log("Локальный KGTD.env сохранён.")

    def _powershell_args(self, script_name: str, *extra_args: str):
        return [
            PS,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / script_name),
            *extra_args,
        ]

    def _powershell_command(self, command: str):
        return [
            PS,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]

    def _run_background_command(self, title: str, args: list[str], on_success=None):
        def worker():
            self._log(f"[start] {title}")
            try:
                proc = subprocess.run(
                    args,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                )
                if proc.stdout.strip():
                    self._log(proc.stdout.rstrip())
                if proc.stderr.strip():
                    self._log(proc.stderr.rstrip())
                if proc.returncode == 0:
                    self.status_var.set(f"Готово: {title}")
                    self._log(f"[ok] {title}")
                    if on_success:
                        self.root.after(0, on_success)
                else:
                    self.status_var.set(f"Ошибка: {title}")
                    self._log(f"[fail] {title} (код {proc.returncode})")
            except Exception as exc:
                self.status_var.set(f"Ошибка: {title}")
                self._log(f"[exception] {title}: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _run_background_powershell(self, title: str, command: str, on_success=None):
        self._run_background_command(title, self._powershell_command(command), on_success=on_success)

    def _stream_process_output(self, pipe, prefix: str):
        try:
            for line in iter(pipe.readline, ""):
                text = line.rstrip()
                if text:
                    self._log(f"{prefix}{text}")
        finally:
            pipe.close()

    def _check_tcp(self, host: str, port: int, timeout: float = 1.5) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def refresh_statuses(self):
        bridge_up = self._check_tcp("127.0.0.1", 8787)
        self.bridge_card.set("Работает" if bridge_up else "Выключен")

        tail_ip = self.tailscale_ip_var.get().strip()
        self.tailnet_card.set(tail_ip or "IP не задан")

        def worker():
            args = [
                "ssh",
                "-i",
                self.ssh_key_var.get().strip(),
                f"{self.vps_user_var.get().strip()}@{self.vps_host_var.get().strip()}",
                "systemctl is-active vipik-discord-bot",
            ]
            try:
                proc = subprocess.run(
                    args,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=20,
                )
                value = proc.stdout.strip() or proc.stderr.strip() or "unknown"
                self.root.after(0, lambda: self.vps_card.set(value))
            except Exception:
                self.root.after(0, lambda: self.vps_card.set("Недоступен"))

        threading.Thread(target=worker, daemon=True).start()

    def _open_powershell_window(self, title: str, command: str):
        args = [
            PS,
            "-NoLogo",
            "-NoExit",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]
        try:
            subprocess.Popen(args, cwd=ROOT)
            self._log(f"Открыто окно: {title}")
            self.status_var.set(f"Открыто: {title}")
        except Exception as exc:
            self._log(f"[exception] Не удалось открыть окно '{title}': {exc}")
            self.status_var.set(f"Ошибка: {title}")

    def install_dependencies(self):
        command = (
            f"Set-Location -LiteralPath '{ROOT}'; "
            "if (-not (Test-Path '.venv')) { python -m venv .venv }; "
            "& '.\\.venv\\Scripts\\python.exe' -m pip install --upgrade pip; "
            "& '.\\.venv\\Scripts\\python.exe' -m pip install -r requirements.txt"
        )
        self._run_background_powershell("Установка зависимостей", command)

    def run_bot_locally(self):
        command = (
            f"Set-Location -LiteralPath '{ROOT}'; "
            "if (-not (Test-Path '.venv\\Scripts\\python.exe')) { "
            "Write-Host 'Сначала установи зависимости.' -ForegroundColor Yellow; return }; "
            "& '.\\.venv\\Scripts\\python.exe' main_file.py"
        )
        self._open_powershell_window("Локальный бот", command)

    def git_status(self):
        self._run_background_command("Git status", ["git", "status"])

    def git_commit_push(self):
        if not messagebox.askyesno("Git", "Добавить все текущие изменения в git?"):
            return
        commit_message = simpledialog.askstring("Commit", "Сообщение коммита:", parent=self.root)
        if not commit_message or not commit_message.strip():
            self._log("Commit отменён: пустое сообщение.")
            self.status_var.set("Commit отменён.")
            return

        def worker():
            self._log("[start] Commit и push")
            steps = [
                ("git add .", ["git", "add", "."]),
                (f"git commit -m {commit_message}", ["git", "commit", "-m", commit_message.strip()]),
                ("git push origin main", ["git", "push", "origin", "main"]),
            ]
            for title, args in steps:
                proc = subprocess.run(
                    args,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                )
                if proc.stdout.strip():
                    self._log(proc.stdout.rstrip())
                if proc.stderr.strip():
                    self._log(proc.stderr.rstrip())
                if proc.returncode != 0:
                    self.status_var.set("Ошибка: Commit и push")
                    self._log(f"[fail] {title} (код {proc.returncode})")
                    return
            self.status_var.set("Commit и push выполнены.")
            self._log("[ok] Commit и push")

        threading.Thread(target=worker, daemon=True).start()

    def install_daily_sync_task(self):
        daily_at = simpledialog.askstring(
            "Планировщик",
            "Во сколько ставить ежедневную sync-задачу? Формат HH:mm",
            initialvalue="07:30",
            parent=self.root,
        )
        if daily_at is None:
            return
        daily_at = (daily_at or "07:30").strip()
        self._run_background_command(
            "Установка ежедневной sync-задачи",
            self._powershell_args("install_local_message_sync_task.ps1", "-DailyAt", daily_at),
        )

    def install_new_vps(self):
        host = simpledialog.askstring("Новый VPS", "IP или домен нового VPS:", initialvalue=self.vps_host_var.get().strip(), parent=self.root)
        if host is None:
            return
        user = simpledialog.askstring("Новый VPS", "SSH user:", initialvalue=self.vps_user_var.get().strip(), parent=self.root)
        if user is None:
            return
        app_dir = simpledialog.askstring("Новый VPS", "Путь проекта на VPS:", initialvalue="/opt/dis-bot", parent=self.root)
        if app_dir is None:
            return
        self._run_background_command(
            "Установка бота на новый VPS",
            self._powershell_args(
                "install_bot_on_vps.ps1",
                "-VpsHost",
                host.strip() or self.vps_host_var.get().strip(),
                "-VpsUser",
                user.strip() or self.vps_user_var.get().strip(),
                "-RemoteAppDir",
                app_dir.strip() or "/opt/dis-bot",
            ),
        )

    def start_bridge(self):
        token = self.bridge_token_var.get().strip()
        if not token:
            messagebox.showwarning("Bridge", "Нужен токен bridge.")
            return

        self.save_settings()
        self.stop_bridge(silent=True)

        args = self._powershell_args("run_model_bridge.ps1", "-Token", token)
        try:
            self.bridge_process = subprocess.Popen(
                args,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
        except Exception as exc:
            self._log(f"[exception] Не удалось запустить bridge: {exc}")
            self.status_var.set("Ошибка запуска bridge.")
            return

        reader = threading.Thread(
            target=self._stream_process_output,
            args=(self.bridge_process.stdout, "[bridge] "),
            daemon=True,
        )
        reader.start()
        self.status_var.set("Bridge запускается...")
        self._log(f"Bridge запущен, PID {self.bridge_process.pid}.")
        self.root.after(4000, self.refresh_statuses)

    def stop_bridge(self, silent: bool = False):
        if self.bridge_process and self.bridge_process.poll() is None:
            try:
                self.bridge_process.terminate()
            except Exception:
                pass
        self.bridge_process = None
        self._run_background_command(
            "Остановка bridge",
            self._powershell_args("stop_model_bridge.ps1"),
            on_success=self.refresh_statuses,
        )
        if not silent:
            self.status_var.set("Bridge остановлен.")

    def link_vps(self):
        ip = self.tailscale_ip_var.get().strip()
        token = self.bridge_token_var.get().strip()
        if not ip or not token:
            messagebox.showwarning("Bridge", "Нужны Tailscale IP и токен.")
            return
        self.save_settings()
        self._run_background_command(
            "Связка VPS с bridge",
            self._powershell_args("enable_remote_models.ps1", "-TailscaleIp", ip, "-Token", token),
            on_success=self.refresh_statuses,
        )

    def disable_remote_models(self):
        self._run_background_command(
            "Отключение тяжёлых моделей на VPS",
            self._powershell_args("disable_remote_models.ps1"),
            on_success=self.refresh_statuses,
        )

    def start_full_kit(self):
        self.start_bridge()

        def delayed_link():
            self.root.after(4500, self.link_vps)

        delayed_link()
        self._log("Запущен комплект: сначала bridge, затем привязка VPS.")

    def sync_messages(self):
        self._run_background_command(
            "Синхронизация messages.db",
            self._powershell_args("sync_messages_from_vps.ps1"),
        )

    def sync_training(self):
        include_gpt = messagebox.askyesno(
            "Sync training",
            "Включать GPT-модели в отправку на VPS?\nОбычно это не нужно.",
        )
        args = self._powershell_args("sync_training_to_vps.ps1")
        if include_gpt:
            args.append("-IncludeGpt")
        self._run_background_command("Отправка лёгких моделей и баз на VPS", args)

    def git_pull(self):
        self._run_background_command("Git pull", ["git", "pull", "origin", "main"])

    def show_vps_status(self):
        args = [
            "ssh",
            "-i",
            self.ssh_key_var.get().strip(),
            f"{self.vps_user_var.get().strip()}@{self.vps_host_var.get().strip()}",
            "systemctl status vipik-discord-bot --no-pager -n 40",
        ]
        self._run_background_command("Статус VPS", args)

    def open_training_menu(self):
        args = self._powershell_args("train_models_menu.ps1")
        try:
            subprocess.Popen(args, cwd=ROOT)
            self._log("Открыто окно обучения моделей.")
            self.status_var.set("Открыто обучение моделей.")
        except Exception as exc:
            self._log(f"[exception] Не удалось открыть обучение: {exc}")

    def open_readme(self):
        try:
            os.startfile(str(README_PATH))
            self._log("README открыт.")
        except Exception as exc:
            self._log(f"[exception] Не удалось открыть README: {exc}")

    def open_admin_panel(self):
        url = self.admin_url_var.get().strip()
        if not url:
            messagebox.showwarning("Веб-панель", "URL веб-панели не задан.")
            return
        webbrowser.open(url)
        self._log(f"Открыта веб-панель: {url}")


def main():
    root = tk.Tk()
    try:
        root.iconname("ViPik Bot Control")
    except Exception:
        pass
    app = BotControlApp(root)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
