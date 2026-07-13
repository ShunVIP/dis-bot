import json
import os
import queue
import re
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk


def resolve_root() -> Path:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend([exe_dir, exe_dir.parent])
    cwd = Path.cwd().resolve()
    candidates.extend([cwd, cwd.parent])
    candidates.append(Path(__file__).resolve().parent.parent)

    for candidate in candidates:
        if (candidate / "scripts").exists() and (candidate / "main_file.py").exists():
            return candidate

    return Path(__file__).resolve().parent.parent


ROOT = resolve_root()
SETTINGS_PATH = ROOT / ".control_center.local.json"
README_PATH = ROOT / "README.md"
PS = "powershell.exe"
DEFAULTS = {
    "tailscale_ip": "",
    "vps_host": "206.245.134.221",
    "vps_user": "root",
    "ssh_key": str(Path(os.environ.get("USERPROFILE", "")) / ".ssh" / "disbot_vps_ed25519"),
    "admin_url": "http://100.90.24.117:8080/",
    "local_web_url": "http://127.0.0.1:3000/",
}


class StatusCard(ttk.Frame):
    def __init__(self, master, title: str):
        super().__init__(master, padding=10)
        self.columnconfigure(0, weight=1)
        ttk.Label(self, text=title, font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.value = ttk.Label(self, text="Неизвестно", font=("Segoe UI", 11))
        self.value.grid(row=1, column=0, sticky="w", pady=(6, 0))

    def set(self, text: str):
        self.value.configure(text=text)


class HintLabel(ttk.Label):
    def __init__(self, master, text: str):
        super().__init__(master, text=text, justify="left", foreground="#5b6472", wraplength=1020)


class BotControlApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ViPik Bot Control")
        self.root.geometry("1180x780")
        self.root.minsize(1080, 700)

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.settings = self._load_settings()
        self.tailscale_ip_var = tk.StringVar(value=self.settings.get("tailscale_ip") or self._detect_tailscale_ip())
        self.vps_host_var = tk.StringVar(value=self.settings.get("vps_host", DEFAULTS["vps_host"]))
        self.vps_user_var = tk.StringVar(value=self.settings.get("vps_user", DEFAULTS["vps_user"]))
        self.ssh_key_var = tk.StringVar(value=self.settings.get("ssh_key", DEFAULTS["ssh_key"]))
        self.admin_url_var = tk.StringVar(value=self.settings.get("admin_url", DEFAULTS["admin_url"]))
        self.local_web_url_var = tk.StringVar(value=self.settings.get("local_web_url", DEFAULTS["local_web_url"]))
        self.status_var = tk.StringVar(value="Готово.")

        self._build_ui()
        self._start_log_pump()
        self.refresh_statuses()
        self._log(f"GUI запущен. ROOT={ROOT}")

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        try:
            ttk.Style().theme_use("vista")
        except Exception:
            pass

        header = ttk.Frame(self.root, padding=(16, 14))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="ViPik Bot Control", font=("Segoe UI", 22, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status_var, foreground="#136f2d").grid(row=0, column=1, sticky="e")

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        self.main_tab = ttk.Frame(notebook, padding=14)
        self.docs_tab = ttk.Frame(notebook, padding=14)
        self.log_tab = ttk.Frame(notebook, padding=14)
        notebook.add(self.main_tab, text="Обзор")
        notebook.add(self.docs_tab, text="Документация")
        notebook.add(self.log_tab, text="Лог")

        self._build_main_tab()
        self._build_docs_tab()
        self._build_log_tab()

    def _build_main_tab(self):
        self.main_tab.columnconfigure((0, 1, 2, 3), weight=1)

        self.models_card = StatusCard(self.main_tab, "Локальные Markov")
        self.models_card.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

        self.vps_card = StatusCard(self.main_tab, "VPS")
        self.vps_card.grid(row=0, column=1, sticky="nsew", padx=6, pady=6)

        self.bot_card = StatusCard(self.main_tab, "Бот")
        self.bot_card.grid(row=0, column=2, sticky="nsew", padx=6, pady=6)

        self.commands_card = StatusCard(self.main_tab, "Команды")
        self.commands_card.grid(row=0, column=3, sticky="nsew", padx=6, pady=6)

        self.db_card = StatusCard(self.main_tab, "База сегодня")
        self.db_card.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)

        self.markov_card = StatusCard(self.main_tab, "Markov сегодня")
        self.markov_card.grid(row=1, column=1, sticky="nsew", padx=6, pady=6)

        self.tailnet_card = StatusCard(self.main_tab, "Tailscale")
        self.tailnet_card.grid(row=1, column=2, sticky="nsew", padx=6, pady=6)

        self.toxicity_ml_card = StatusCard(self.main_tab, "ML токсичности")
        self.toxicity_ml_card.grid(row=1, column=3, sticky="nsew", padx=6, pady=6)

        main_actions = ttk.LabelFrame(self.main_tab, text="Главные действия", padding=12)
        main_actions.grid(row=2, column=0, columnspan=4, sticky="ew", padx=6, pady=6)
        for col in range(3):
            main_actions.columnconfigure(col, weight=1)

        ttk.Button(main_actions, text="Скачать DB и обучить Markov", command=self.sync_and_train_markov).grid(
            row=0, column=0, sticky="ew", padx=4, pady=4
        )
        ttk.Button(main_actions, text="Отправить Markov на VPS", command=self.sync_training).grid(
            row=0, column=1, sticky="ew", padx=4, pady=4
        )
        ttk.Button(main_actions, text="Обновить статусы", command=self.refresh_statuses).grid(
            row=0, column=2, sticky="ew", padx=4, pady=4
        )
        HintLabel(
            main_actions,
            "Сначала забери свежую messages.db и обучи Markov на ПК. После проверки отправь лёгкие артефакты на VPS. "
            "ML токсичности работает отдельно в теневом режиме и не влияет на санкции.",
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=4, pady=(8, 0))

        extra = ttk.LabelFrame(self.main_tab, text="Дополнительно", padding=12)
        extra.grid(row=3, column=0, columnspan=4, sticky="ew", padx=6, pady=6)
        for col in range(4):
            extra.columnconfigure(col, weight=1)

        ttk.Button(extra, text="Git pull", command=self.git_pull).grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(extra, text="Git status", command=self.git_status).grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(extra, text="Commit и push", command=self.git_commit_push).grid(row=0, column=2, sticky="ew", padx=4, pady=4)
        ttk.Button(extra, text="Открыть веб-панель", command=self.open_admin_panel).grid(row=0, column=3, sticky="ew", padx=4, pady=4)
        ttk.Button(extra, text="Установить зависимости", command=self.install_dependencies).grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(extra, text="Запустить бота локально", command=self.run_bot_locally).grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(extra, text="Отправить лёгкие модели на VPS", command=self.sync_training).grid(row=1, column=2, sticky="ew", padx=4, pady=4)
        ttk.Button(extra, text="Поставить нового VPS", command=self.install_new_vps).grid(row=1, column=3, sticky="ew", padx=4, pady=4)
        ttk.Button(extra, text="Запустить сайт/app", command=self.run_web_app_locally).grid(row=2, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(extra, text="Открыть сайт/app", command=self.open_local_web_app).grid(row=2, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(extra, text="Настройки подключения", command=self.open_settings_window).grid(row=2, column=2, sticky="ew", padx=4, pady=4)
        ttk.Button(extra, text="Daily sync-задача", command=self.install_daily_sync_task).grid(row=2, column=3, sticky="ew", padx=4, pady=4)
        ttk.Button(extra, text="Очистить лог", command=self.clear_log).grid(row=3, column=0, sticky="ew", padx=4, pady=4)
        HintLabel(
            extra,
            "Этот блок нужен не каждый день. Настройки подключения трогай только если меняются Tailscale IP, VPS host или SSH-ключ.",
        ).grid(row=4, column=0, columnspan=4, sticky="w", padx=4, pady=(8, 0))

    def _build_docs_tab(self):
        self.docs_tab.columnconfigure(0, weight=1)

        intro = ttk.LabelFrame(self.docs_tab, text="Как пользоваться", padding=12)
        intro.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        ttk.Label(
            intro,
            text=(
                "1. Нажми «Скачать DB и обучить Markov».\n"
                "2. Проверь статус локальных моделей.\n"
                "3. Отправь Markov-артефакты на VPS.\n"
                "4. При ошибке открой вкладку «Лог»."
            ),
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        status_help = ttk.LabelFrame(self.docs_tab, text="Что означают статусы", padding=12)
        status_help.grid(row=1, column=0, sticky="ew", padx=6, pady=6)
        ttk.Label(
            status_help,
            text=(
                "Локальные Markov: число готовых JSON-моделей на ПК.\n"
                "VPS: systemd-сервис бота активен.\n"
                "Бот: в логах есть успешный старт и вход в Discord.\n"
                "Команды: slash-команды синхронизированы и в последних логах нет явной аварии.\n"
                "База сегодня: messages.db на VPS сегодня добирала новые сообщения.\n"
                "Markov сегодня: ежедневный safe daily Markov сегодня завершился.\n"
                "ML токсичности: наличие теневой классификационной модели на VPS."
            ),
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        docs_actions = ttk.LabelFrame(self.docs_tab, text="Документация", padding=12)
        docs_actions.grid(row=2, column=0, sticky="ew", padx=6, pady=6)
        ttk.Button(docs_actions, text="Открыть README", command=self.open_readme).grid(row=0, column=0, sticky="ew")

        extra_help = ttk.LabelFrame(self.docs_tab, text="Что в разделе «Дополнительно»", padding=12)
        extra_help.grid(row=3, column=0, sticky="ew", padx=6, pady=6)
        ttk.Label(
            extra_help,
            text=(
                "Git pull / Git status / Commit и push — обслуживание репозитория.\n"
                "Открыть веб-панель — открыть приватную админку на VPS через Tailscale.\n"
                "Установить зависимости / Запустить бота локально — если нужно запускать проект на ПК.\n"
                "Отправить лёгкие модели на VPS — синхронизировать Markov и manifest.\n"
                "Поставить нового VPS — аварийное восстановление на новом сервере.\n"
                "Настройки подключения — Tailscale IP, VPS host/user и SSH key.\n"
                "Daily sync-задача — чтобы Windows каждый день сама скачивала свежую messages.db."
            ),
            justify="left",
        ).grid(row=0, column=0, sticky="w")

    def _build_log_tab(self):
        self.log_tab.columnconfigure(0, weight=1)
        self.log_tab.rowconfigure(0, weight=1)

        self.log_text = tk.Text(self.log_tab, wrap="word", font=("Consolas", 10))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.configure(state="disabled")

        scroll = ttk.Scrollbar(self.log_tab, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

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
            "vps_host": self.vps_host_var.get().strip(),
            "vps_user": self.vps_user_var.get().strip(),
            "ssh_key": self.ssh_key_var.get().strip(),
            "admin_url": self.admin_url_var.get().strip(),
            "local_web_url": self.local_web_url_var.get().strip(),
        }
        SETTINGS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.status_var.set("Настройки сохранены.")
        self._log("Локальные настройки сохранены.")

    def open_settings_window(self):
        win = tk.Toplevel(self.root)
        win.title("Настройки подключения")
        win.geometry("760x360")
        win.transient(self.root)
        win.grab_set()
        win.columnconfigure(1, weight=1)
        win.columnconfigure(3, weight=1)

        ttk.Label(win, text="Tailscale IP ПК").grid(row=0, column=0, sticky="w", padx=10, pady=8)
        ttk.Entry(win, textvariable=self.tailscale_ip_var).grid(row=0, column=1, sticky="ew", padx=10, pady=8)
        ttk.Button(win, text="Определить IP", command=self.detect_ip_to_field).grid(row=0, column=2, columnspan=2, sticky="ew", padx=10, pady=8)

        ttk.Label(win, text="VPS host").grid(row=1, column=0, sticky="w", padx=10, pady=8)
        ttk.Entry(win, textvariable=self.vps_host_var).grid(row=1, column=1, sticky="ew", padx=10, pady=8)

        ttk.Label(win, text="VPS user").grid(row=2, column=2, sticky="w", padx=10, pady=8)
        ttk.Entry(win, textvariable=self.vps_user_var).grid(row=2, column=3, sticky="ew", padx=10, pady=8)

        ttk.Label(win, text="SSH key").grid(row=3, column=0, sticky="w", padx=10, pady=8)
        ttk.Entry(win, textvariable=self.ssh_key_var).grid(row=3, column=1, columnspan=3, sticky="ew", padx=10, pady=8)

        ttk.Label(win, text="URL веб-панели").grid(row=4, column=0, sticky="w", padx=10, pady=8)
        ttk.Entry(win, textvariable=self.admin_url_var).grid(row=4, column=1, columnspan=3, sticky="ew", padx=10, pady=8)

        ttk.Label(win, text="URL локального сайта/app").grid(row=5, column=0, sticky="w", padx=10, pady=8)
        ttk.Entry(win, textvariable=self.local_web_url_var).grid(row=5, column=1, columnspan=3, sticky="ew", padx=10, pady=8)

        HintLabel(
            win,
            "Когда это нужно: обычно сюда заходят один раз при первой настройке. "
            "Потом трогать это нужно только если изменился Tailscale IP, адрес VPS или SSH-ключ.",
        ).grid(row=6, column=0, columnspan=4, sticky="w", padx=10, pady=(6, 0))

        ttk.Button(win, text="Сохранить", command=lambda: [self.save_settings(), win.destroy()]).grid(
            row=7, column=0, columnspan=4, sticky="ew", padx=10, pady=(12, 8)
        )

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

    def _ssh_args(self, remote_command: str) -> list[str]:
        return [
            "ssh",
            "-i",
            self.ssh_key_var.get().strip(),
            f"{self.vps_user_var.get().strip()}@{self.vps_host_var.get().strip()}",
            remote_command,
        ]

    def _ssh_capture(self, remote_command: str, timeout: float = 30) -> subprocess.CompletedProcess:
        return subprocess.run(
            self._ssh_args(remote_command),
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout,
        )

    def _format_iso_short(self, value: str | None) -> str:
        if not value:
            return "нет данных"
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).strftime("%d.%m %H:%M UTC")
        except Exception:
            return value[:16]

    def refresh_statuses(self):
        local_models = len(list((ROOT / "models").glob("*_мем.json"))) + len(list((ROOT / "models").glob("*_разум.json")))
        self.models_card.set(f"{local_models} моделей")
        self.vps_card.set("Проверка...")
        self.bot_card.set("Проверка...")
        self.commands_card.set("Проверка...")
        self.db_card.set("Проверка...")
        self.markov_card.set("Проверка...")
        self.tailnet_card.set(self.tailscale_ip_var.get().strip() or "IP не задан")
        self.toxicity_ml_card.set("Проверка...")

        def worker():
            utc_today = datetime.now(timezone.utc).date().isoformat()
            try:
                service_proc = self._ssh_capture("systemctl is-active vipik-discord-bot", timeout=20)
                service_value = service_proc.stdout.strip() or service_proc.stderr.strip() or "unknown"

                log_proc = self._ssh_capture("journalctl -u vipik-discord-bot --no-pager -n 200", timeout=25)
                logs = (log_proc.stdout or "") + "\n" + (log_proc.stderr or "")

                today_log_proc = self._ssh_capture("journalctl -u vipik-discord-bot --since today --no-pager", timeout=25)
                today_logs = (today_log_proc.stdout or "") + "\n" + (today_log_proc.stderr or "")

                db_script = (
                    "python3 - <<'PY'\n"
                    "import json, sqlite3\n"
                    "from pathlib import Path\n"
                    "p=Path('/opt/dis-bot/datebase/messages.db')\n"
                    "result={'exists': p.exists(), 'max_created_at': None, 'max_last_collected': None}\n"
                    "if p.exists():\n"
                    "    conn=sqlite3.connect(p)\n"
                    "    cur=conn.cursor()\n"
                    "    cur.execute('select max(created_at) from user_messages')\n"
                    "    result['max_created_at']=cur.fetchone()[0]\n"
                    "    cur.execute('select max(last_collected) from collect_checkpoints')\n"
                    "    result['max_last_collected']=cur.fetchone()[0]\n"
                    "    conn.close()\n"
                    "print(json.dumps(result, ensure_ascii=False))\n"
                    "PY"
                )
                db_proc = self._ssh_capture(db_script, timeout=30)
                db_info = json.loads((db_proc.stdout or "{}").strip() or "{}")

                ml_script = (
                    "python3 - <<'PY'\n"
                    "import json\n"
                    "from pathlib import Path\n"
                    "root=Path('/opt/dis-bot/models')\n"
                    "result={'markov': len(list(root.glob('*_мем.json'))) + len(list(root.glob('*_разум.json'))), 'toxicity': (root/'toxicity_nb.json').exists()}\n"
                    "print(json.dumps(result, ensure_ascii=False))\n"
                    "PY"
                )
                ml_proc = self._ssh_capture(ml_script, timeout=30)
                ml_info = json.loads((ml_proc.stdout or "{}").strip() or "{}")

                bot_ready = "Бот готов к работе" in logs and service_value == "active"
                slash_match = re.search(r"Slash-команд синхронизировано:\s*(\d+)", logs)
                has_recent_error = any(marker in logs for marker in ["Traceback", "Unknown interaction", "discord.app_commands.errors"])
                commands_text = "OK"
                if slash_match:
                    commands_text = f"Sync {slash_match.group(1)}"
                if has_recent_error:
                    commands_text = f"{commands_text}, есть ошибки"

                last_collected = db_info.get("max_last_collected")
                db_today = bool(last_collected and str(last_collected).startswith(utc_today))

                markov_today = "Safe daily Markov готово" in today_logs

                self.root.after(0, lambda: self.vps_card.set(service_value))
                self.root.after(0, lambda: self.bot_card.set("Готов" if bot_ready else "Нет сигнала"))
                self.root.after(0, lambda: self.commands_card.set(commands_text))
                self.root.after(
                    0,
                    lambda: self.db_card.set(
                        f"Сегодня ({self._format_iso_short(last_collected)})" if db_today else self._format_iso_short(last_collected)
                    ),
                )
                self.root.after(0, lambda: self.markov_card.set("Сегодня" if markov_today else "Нет сигнала сегодня"))
                self.root.after(0, lambda: self.toxicity_ml_card.set("Shadow готов" if ml_info.get("toxicity") else "Rules only"))
            except Exception as exc:
                self.root.after(0, lambda: self.vps_card.set("Недоступен"))
                self.root.after(0, lambda: self.bot_card.set("Неизвестно"))
                self.root.after(0, lambda: self.commands_card.set("Неизвестно"))
                self.root.after(0, lambda: self.db_card.set("Неизвестно"))
                self.root.after(0, lambda: self.markov_card.set("Неизвестно"))
                self.root.after(0, lambda: self.toxicity_ml_card.set("Неизвестно"))
                self._log(f"[exception] Обновление статусов: {exc}")
            finally:
                self.root.after(0, lambda: self.status_var.set("Статусы обновлены."))
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

    def run_web_app_locally(self):
        command = (
            f"Set-Location -LiteralPath '{ROOT}'; "
            "if (-not (Test-Path '.venv\\Scripts\\python.exe')) { "
            "Write-Host 'Сначала установи зависимости.' -ForegroundColor Yellow; return }; "
            "& '.\\.venv\\Scripts\\python.exe' run_web_app.py"
        )
        self._open_powershell_window("Локальный сайт/app", command)

    def open_local_web_app(self):
        url = self.local_web_url_var.get().strip() or DEFAULTS["local_web_url"]
        webbrowser.open(url)
        self._log(f"Открыт локальный сайт/app: {url}")

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
            for title, args in [
                ("git add .", ["git", "add", "."]),
                (f"git commit -m {commit_message.strip()}", ["git", "commit", "-m", commit_message.strip()]),
                ("git push origin main", ["git", "push", "origin", "main"]),
            ]:
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
        self._run_background_command(
            "Установка ежедневной sync-задачи",
            self._powershell_args("install_local_message_sync_task.ps1", "-DailyAt", (daily_at or "07:30").strip()),
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

    def sync_messages(self):
        self._run_background_command("Синхронизация messages.db", self._powershell_args("sync_messages_from_vps.ps1"))

    def _python_executable(self) -> str:
        venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
        return str(venv_python) if venv_python.exists() else "python"

    def _ask_training_scope(self) -> list[str] | None:
        all_users = messagebox.askyesno(
            "Обучение Markov",
            "Обучать Markov для всех пользователей?\n\nДа = для всех\nНет = для одного пользователя",
        )
        if all_users:
            return ["--all"]

        user_id = simpledialog.askstring("Обучение Markov", "Discord user id пользователя:", parent=self.root)
        if not user_id or not user_id.strip():
            self._log("Обучение Markov отменено: не указан user id.")
            return None
        return ["--user-id", user_id.strip()]

    def _run_training_process(self, title: str, extra_args: list[str]):
        args = [self._python_executable(), str(ROOT / "scripts" / "train_local.py"), *extra_args]

        def worker():
            self._log(f"[start] {title}")
            try:
                proc = subprocess.Popen(
                    args,
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    text = line.rstrip()
                    if text:
                        self._log(f"[train] {text}")
                proc.wait()
                if proc.returncode == 0:
                    self.status_var.set(f"Готово: {title}")
                    self._log(f"[ok] {title}")
                else:
                    self.status_var.set(f"Ошибка: {title}")
                    self._log(f"[fail] {title} (код {proc.returncode})")
            except Exception as exc:
                self.status_var.set(f"Ошибка: {title}")
                self._log(f"[exception] {title}: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def sync_and_train_markov(self):
        scope_args = self._ask_training_scope()
        if not scope_args:
            return

        def worker():
            self._log("[start] Синхронизация messages.db перед Markov-обучением")
            try:
                proc = subprocess.run(
                    self._powershell_args("sync_messages_from_vps.ps1"),
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
                    self.status_var.set("Ошибка: sync messages.db")
                    self._log(f"[fail] sync messages.db (код {proc.returncode})")
                    return
                self._log("[ok] sync messages.db")
                self.root.after(0, lambda: self._run_training_process("Обучение Markov", scope_args))
            except Exception as exc:
                self.status_var.set("Ошибка: sync messages.db")
                self._log(f"[exception] sync messages.db: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def sync_training(self):
        args = self._powershell_args("sync_training_to_vps.ps1")
        self._run_background_command("Отправка Markov-моделей и manifest на VPS", args)

    def git_pull(self):
        self._run_background_command("Git pull", ["git", "pull", "origin", "main"])

    def show_vps_status(self):
        self._run_background_command(
            "Статус VPS",
            [
                "ssh",
                "-i",
                self.ssh_key_var.get().strip(),
                f"{self.vps_user_var.get().strip()}@{self.vps_host_var.get().strip()}",
                "systemctl status vipik-discord-bot --no-pager -n 40",
            ],
        )

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
