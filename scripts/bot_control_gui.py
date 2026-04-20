import json
import os
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / ".control_center.local.json"
README_PATH = ROOT / "README.md"
PS = "powershell.exe"
VPS_ADMIN_URL = "http://100.90.24.117:8080/"


class BotControlApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ViPik Bot Control")
        self.root.geometry("1100x760")
        self.root.minsize(980, 680)

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.bridge_process: subprocess.Popen | None = None
        self._bridge_reader_threads: list[threading.Thread] = []

        self.settings = self._load_settings()

        self.tailscale_ip_var = tk.StringVar(value=self.settings.get("tailscale_ip", self._detect_tailscale_ip()))
        self.bridge_token_var = tk.StringVar(value=self.settings.get("bridge_token", "secretkeyvipik"))
        self.status_var = tk.StringVar(value="Готово.")

        self._build_ui()
        self._start_log_pump()
        self._log("GUI запущен. Можно работать в одном окне.")

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        header = ttk.Frame(self.root, padding=12)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        ttk.Label(header, text="ViPik Bot Control", font=("Segoe UI", 20, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Одно окно для bridge, VPS и частых действий по проекту",
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(header, textvariable=self.status_var, foreground="#1f6f43").grid(row=0, column=1, rowspan=2, sticky="e")

        settings = ttk.LabelFrame(self.root, text="Настройки", padding=12)
        settings.grid(row=1, column=0, sticky="ew", padx=12)
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)

        ttk.Label(settings, text="Tailscale IP ПК").grid(row=0, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.tailscale_ip_var).grid(row=0, column=1, sticky="ew", padx=(8, 16))

        ttk.Label(settings, text="Токен bridge").grid(row=0, column=2, sticky="w")
        ttk.Entry(settings, textvariable=self.bridge_token_var, show="*").grid(row=0, column=3, sticky="ew", padx=(8, 0))

        ttk.Button(settings, text="Сохранить настройки", command=self.save_settings).grid(row=0, column=4, padx=(12, 0))
        ttk.Button(settings, text="Определить IP", command=self.detect_ip_to_field).grid(row=0, column=5, padx=(8, 0))

        actions = ttk.Frame(self.root, padding=(12, 10))
        actions.grid(row=2, column=0, sticky="nsew")
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        actions.rowconfigure(1, weight=1)

        bridge_box = ttk.LabelFrame(actions, text="Bridge и VPS", padding=12)
        bridge_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        bridge_box.columnconfigure(0, weight=1)

        ttk.Button(bridge_box, text="Запустить bridge", command=self.start_bridge).grid(row=0, column=0, sticky="ew", pady=4)
        ttk.Button(bridge_box, text="Остановить bridge", command=self.stop_bridge).grid(row=1, column=0, sticky="ew", pady=4)
        ttk.Button(bridge_box, text="Связать VPS с ПК", command=self.link_vps).grid(row=2, column=0, sticky="ew", pady=4)
        ttk.Button(bridge_box, text="Отключить тяжёлые модели на VPS", command=self.disable_remote_models).grid(row=3, column=0, sticky="ew", pady=4)
        ttk.Button(bridge_box, text="Запустить комплект", command=self.start_full_kit).grid(row=4, column=0, sticky="ew", pady=4)
        ttk.Button(bridge_box, text="Статус VPS", command=self.show_vps_status).grid(row=5, column=0, sticky="ew", pady=4)
        ttk.Button(bridge_box, text="Открыть веб-панель", command=self.open_admin_panel).grid(row=6, column=0, sticky="ew", pady=4)

        project_box = ttk.LabelFrame(actions, text="Проект", padding=12)
        project_box.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        project_box.columnconfigure(0, weight=1)

        ttk.Button(project_box, text="Git pull", command=self.git_pull).grid(row=0, column=0, sticky="ew", pady=4)
        ttk.Button(project_box, text="Скачать messages.db с VPS", command=self.sync_messages).grid(row=1, column=0, sticky="ew", pady=4)
        ttk.Button(project_box, text="Отправить лёгкие модели и базы на VPS", command=self.sync_training).grid(row=2, column=0, sticky="ew", pady=4)
        ttk.Button(project_box, text="Открыть обучение моделей", command=self.open_training_menu).grid(row=3, column=0, sticky="ew", pady=4)
        ttk.Button(project_box, text="Открыть README", command=self.open_readme).grid(row=4, column=0, sticky="ew", pady=4)
        ttk.Button(project_box, text="Очистить лог", command=self.clear_log).grid(row=5, column=0, sticky="ew", pady=4)

        log_box = ttk.LabelFrame(actions, text="Лог", padding=12)
        log_box.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_box, wrap="word", font=("Consolas", 10))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.configure(state="disabled")

        scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

    def _load_settings(self) -> dict:
        if not SETTINGS_PATH.exists():
            return {}
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_settings(self):
        payload = {
            "tailscale_ip": self.tailscale_ip_var.get().strip(),
            "bridge_token": self.bridge_token_var.get().strip(),
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

    def _stream_process_output(self, pipe, prefix: str):
        try:
            for line in iter(pipe.readline, ""):
                text = line.rstrip()
                if text:
                    self._log(f"{prefix}{text}")
        finally:
            pipe.close()

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
        self._bridge_reader_threads = [reader]
        self.status_var.set("Bridge запускается...")
        self._log(f"Bridge запущен в фоне через GUI, PID {self.bridge_process.pid}.")

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
        )

    def disable_remote_models(self):
        self._run_background_command(
            "Отключение тяжёлых моделей на VPS",
            self._powershell_args("disable_remote_models.ps1"),
        )

    def start_full_kit(self):
        self.start_bridge()

        def delayed_link():
            self.root.after(4000, self.link_vps)

        delayed_link()
        self._log("Запущен комплект: сначала bridge, затем привязка VPS.")

    def sync_messages(self):
        self._run_background_command(
            "Синхронизация messages.db",
            self._powershell_args("sync_messages_from_vps.ps1"),
        )

    def sync_training(self):
        self._run_background_command(
            "Отправка лёгких моделей и баз на VPS",
            self._powershell_args("sync_training_to_vps.ps1"),
        )

    def git_pull(self):
        self._run_background_command("Git pull", ["git", "pull", "origin", "main"])

    def show_vps_status(self):
        args = [
            "ssh",
            "-i",
            str(Path(os.environ["USERPROFILE"]) / ".ssh" / "disbot_vps_ed25519"),
            "root@206.245.134.221",
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
        webbrowser.open(VPS_ADMIN_URL)
        self._log(f"Открыта веб-панель: {VPS_ADMIN_URL}")


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
