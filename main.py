from __future__ import annotations

import csv
import json
import os
import queue
import re
import threading
import time
import tkinter as tk
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from autodl_api import AutoDLClient, AutoDLWebClient, ApiResult
from browser_login import login_and_capture_token
from security import protect, unprotect


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
LOG_DIR = BASE_DIR / "logs"
LOG_PATH = LOG_DIR / "events.csv"


@dataclass
class Config:
    encrypted_token: str = ""
    interval_seconds: int = 30
    mode: str = "notify"  # notify | power_on | create
    armed: bool = False
    instance_uuid: str = ""
    gpu_spec_uuid: str = "4090D"
    gpu_display_name: str = "RTX 4090D"
    image_uuid: str = ""
    gpu_amount: int = 1
    cuda_v_from: int = 118
    regions: str = ""
    instance_name: str = "AutoDL Watch"
    start_command: str = ""
    max_created_instances: int = 1
    retry_cooldown_seconds: int = 120
    window_x: int = 30
    window_y: int = 125

    @classmethod
    def load(cls):
        if not CONFIG_PATH.exists():
            cfg = cls()
            cfg.save()
            return cfg
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            values = {k: v for k, v in raw.items() if k in cls.__annotations__}
            return cls(**values)
        except Exception:
            return cls()

    def save(self):
        CONFIG_PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    def token(self):
        try:
            return unprotect(self.encrypted_token)
        except Exception:
            return ""


class AutoDLWatch:
    TOKENS = {
        "ink": "#171717",
        "muted": "#475569",
        "surface": "#FFFFFF",
        "canvas": "#F3F4F6",
        "border": "#E5E7EB",
        "accent": "#A16207",
        "focus": "#171717",
    }
    COLORS = {
        "idle": ("#64748b", "#f8fafc", "#e2e8f0"),
        "watch": ("#2563eb", "#eff6ff", "#bfdbfe"),
        "found": ("#16a34a", "#f0fdf4", "#bbf7d0"),
        "warn": ("#d97706", "#fffbeb", "#fde68a"),
        "error": ("#dc2626", "#fef2f2", "#fecaca"),
    }

    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = Config.load()
        self.queue = queue.Queue()
        self.running = False
        self.drag_origin = None
        self.created_count = 0
        self.last_action_at = 0.0
        self.last_action_key = ""

        self.configure_styles()

        root.title("AutoDL Watch")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.geometry(f"420x108+{self.cfg.window_x}+{self.cfg.window_y}")
        root.configure(bg=self.TOKENS["border"])

        self.card = tk.Frame(root, bg="#ffffff", highlightthickness=1, highlightbackground=self.TOKENS["border"])
        self.card.pack(fill="both", expand=True, padx=3, pady=3)
        self.accent = tk.Frame(self.card, bg="#64748b")
        self.accent.place(x=0, y=0, width=5, height=102)
        self.brand = tk.Label(self.card, text="AUTODL WATCH", font=("Segoe UI", 8, "bold"), bg="#ffffff", fg=self.TOKENS["muted"], anchor="w")
        self.brand.place(x=20, y=10, width=132, height=18)
        self.dot = tk.Label(self.card, text="●", font=("Segoe UI", 12, "bold"), bg="#ffffff", fg="#64748b")
        self.dot.place(x=19, y=39, width=20, height=24)
        self.title = tk.Label(self.card, text="需要配置", font=("Microsoft YaHei UI", 11, "bold"), bg="#ffffff", fg=self.TOKENS["ink"], anchor="w")
        self.title.place(x=47, y=35, width=280, height=28)
        self.detail = tk.Label(self.card, text="右键打开设置，登录 AutoDL 网页", font=("Microsoft YaHei UI", 9), bg="#ffffff", fg=self.TOKENS["muted"], anchor="w")
        self.detail.place(x=48, y=67, width=315, height=20)
        self.mode_badge = tk.Label(self.card, text="只通知", font=("Microsoft YaHei UI", 8, "bold"), bg="#F3F4F6", fg="#334155", padx=8, pady=3)
        self.mode_badge.place(x=292, y=9, height=24)
        self.more = tk.Label(self.card, text="•••", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#64748b", cursor="hand2")
        self.more.place(x=371, y=35, width=40, height=40)

        for widget in (self.card, self.accent, self.brand, self.dot, self.title, self.detail):
            widget.bind("<ButtonPress-1>", self.start_drag)
            widget.bind("<B1-Motion>", self.do_drag)
            widget.bind("<ButtonRelease-1>", self.end_drag)
            widget.bind("<Button-3>", self.show_menu)
        self.more.bind("<Button-1>", self.show_menu)
        root.bind("<Double-Button-1>", lambda _e: self.run_now())

        self.menu = tk.Menu(root, tearoff=False)
        self.menu.add_command(label="立即检查", command=self.run_now)
        self.menu.add_command(label="设置…", command=self.open_settings)
        self.menu.add_command(label="打开日志目录", command=lambda: os.startfile(LOG_DIR))
        self.menu.add_separator()
        self.menu.add_command(label="暂停自动操作", command=self.disarm)
        self.menu.add_command(label="退出", command=root.destroy)

        LOG_DIR.mkdir(exist_ok=True)
        self.update_mode_badge()
        root.after(250, self.poll_queue)
        root.after(600, self.schedule)

    def configure_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=self.TOKENS["surface"])
        style.configure("TLabel", background=self.TOKENS["surface"], foreground=self.TOKENS["ink"], font=("Microsoft YaHei UI", 9))
        style.configure("TButton", font=("Microsoft YaHei UI", 9, "bold"), padding=(14, 8), relief="flat")
        style.map("TButton", background=[("active", "#E5E7EB"), ("pressed", "#D1D5DB")])
        style.configure("Primary.TButton", background=self.TOKENS["ink"], foreground="#FFFFFF", padding=(16, 9))
        style.map("Primary.TButton", background=[("active", "#303030"), ("pressed", "#000000")])
        style.configure("TEntry", padding=(8, 7), fieldbackground="#FFFFFF", bordercolor=self.TOKENS["border"], lightcolor=self.TOKENS["border"], darkcolor=self.TOKENS["border"])
        style.configure("TCombobox", padding=(8, 7), fieldbackground="#FFFFFF")
        style.configure("TRadiobutton", background="#FFFFFF", font=("Microsoft YaHei UI", 9))
        style.configure("TCheckbutton", background="#FFFFFF", font=("Microsoft YaHei UI", 9))
        style.configure("Section.TLabel", font=("Microsoft YaHei UI", 10, "bold"), foreground=self.TOKENS["ink"])

    def start_drag(self, event):
        self.drag_origin = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def do_drag(self, event):
        if self.drag_origin:
            self.root.geometry(f"+{event.x_root-self.drag_origin[0]}+{event.y_root-self.drag_origin[1]}")

    def end_drag(self, _event):
        self.drag_origin = None
        self.cfg.window_x, self.cfg.window_y = self.root.winfo_x(), self.root.winfo_y()
        self.cfg.save()

    def show_menu(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    def update_mode_badge(self):
        labels = {"notify": "只通知", "power_on": "自动开机", "create": "自动创建"}
        text = labels.get(self.cfg.mode, "只通知")
        if self.cfg.mode != "notify" and not self.cfg.armed:
            text += "·暂停"
        self.mode_badge.configure(text=text)

    def disarm(self):
        self.cfg.armed = False
        self.cfg.save()
        self.update_mode_badge()
        self.set_status("idle", "自动操作已暂停", "仍会继续检查状态")

    def schedule(self):
        self.run_now()
        self.root.after(max(10, self.cfg.interval_seconds) * 1000, self.schedule)

    def run_now(self):
        if self.running:
            return
        self.running = True
        self.set_status("watch", "正在检查 AutoDL", "读取实例与 GPU 状态…")
        threading.Thread(target=self.worker, daemon=True).start()

    def worker(self):
        token = self.cfg.token()
        if not token:
            self.queue.put(("idle", "需要配置", "右键打开设置，登录 AutoDL 网页"))
            return
        client = AutoDLWebClient(token)
        if self.cfg.mode == "create":
            self.check_create(client)
        else:
            self.check_instance(client)

    def check_instance(self, client: AutoDLClient):
        result = client.list_instances()
        if not result.ok:
            self.finish_api_error(result)
            return
        instances = (result.data or {}).get("list", []) or []
        target = next((item for item in instances if item.get("uuid") == self.cfg.instance_uuid), None)
        if not self.cfg.instance_uuid:
            self.queue.put(("warn", "请选择实例", f"已读取 {len(instances)} 个实例，请在设置中选择"))
            return
        if not target:
            self.queue.put(("error", "未找到目标实例", self.cfg.instance_uuid))
            return
        status = target.get("status", "unknown")
        name = target.get("name") or self.cfg.instance_uuid
        if status == "running":
            self.queue.put(("found", "实例正在运行", f"{name} · {datetime.now():%H:%M:%S}"))
            return
        if self.cfg.mode == "power_on" and self.cfg.armed and self.can_act("power:" + self.cfg.instance_uuid):
            powered = client.power_on(self.cfg.instance_uuid, self.cfg.start_command)
            if powered.ok:
                self.mark_action("power:" + self.cfg.instance_uuid)
                self.log("power_on", "success", self.cfg.instance_uuid)
                self.queue.put(("found", "已提交开机", f"{name} · AutoDL 已接受请求"))
            else:
                self.log("power_on", powered.code, powered.message)
                self.queue.put(("warn", "暂时无法开机", self.clean_message(powered)))
            return
        action = "等待可用 GPU" if self.cfg.mode == "power_on" else "实例当前未运行"
        self.queue.put(("watch", action, f"{name} · 状态 {status}"))

    def check_create(self, client: AutoDLClient):
        if isinstance(client, AutoDLWebClient):
            self.queue.put(("warn", "普通实例克隆尚未启用", "请先完成网页登录，以便验证克隆参数"))
            return
        if self.created_count >= self.cfg.max_created_instances:
            self.queue.put(("idle", "已达到创建上限", f"本次运行已创建 {self.created_count} 个实例"))
            return
        if not self.cfg.armed:
            self.queue.put(("watch", "监测配置已就绪", f"{self.cfg.gpu_display_name} · 自动创建未启用"))
            return
        if not self.cfg.image_uuid or not self.cfg.gpu_spec_uuid:
            self.queue.put(("warn", "创建参数不完整", "请填写 GPU 规格 ID 与镜像 UUID"))
            return
        action_key = f"create:{self.cfg.gpu_spec_uuid}:{self.cfg.image_uuid}"
        if not self.can_act(action_key):
            left = int(self.cfg.retry_cooldown_seconds - (time.time() - self.last_action_at))
            self.queue.put(("watch", "等待下次调度", f"冷却中，约 {max(0, left)} 秒后重试"))
            return
        result = client.create_instance(
            self.cfg.gpu_spec_uuid,
            self.cfg.image_uuid,
            self.cfg.gpu_amount,
            self.cfg.cuda_v_from,
            self.cfg.instance_name,
            [x.strip() for x in self.cfg.regions.split(",") if x.strip()],
            self.cfg.start_command,
        )
        self.mark_action(action_key)
        if result.ok:
            self.created_count += 1
            self.cfg.armed = False
            self.cfg.save()
            self.log("create", "success", str(result.data))
            self.queue.put(("found", "已创建并提交开机", f"实例 {result.data} · 自动操作已暂停"))
        else:
            self.log("create", result.code, result.message)
            self.queue.put(("watch", "目标 GPU 暂不可调度", self.clean_message(result)))

    def can_act(self, key):
        return key != self.last_action_key or time.time() - self.last_action_at >= self.cfg.retry_cooldown_seconds

    def mark_action(self, key):
        self.last_action_key = key
        self.last_action_at = time.time()

    def finish_api_error(self, result):
        self.queue.put(("error", "AutoDL 接口异常", self.clean_message(result)))

    @staticmethod
    def clean_message(result: ApiResult):
        text = result.message or result.code or "未知错误"
        return text.replace("\n", " ")[:72]

    def log(self, action, status, detail):
        new = not LOG_PATH.exists()
        with LOG_PATH.open("a", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            if new:
                writer.writerow(["time", "action", "status", "detail"])
            writer.writerow([datetime.now().astimezone().isoformat(timespec="seconds"), action, status, detail])

    def poll_queue(self):
        try:
            while True:
                state, title, detail = self.queue.get_nowait()
                self.running = False
                self.set_status(state, title, detail)
                self.update_mode_badge()
                if state == "found":
                    self.root.bell()
        except queue.Empty:
            pass
        self.root.after(250, self.poll_queue)

    def set_status(self, state, title, detail):
        color, bg, border = self.COLORS[state]
        self.root.configure(bg=self.TOKENS["border"])
        self.card.configure(bg="#FFFFFF", highlightbackground=self.TOKENS["border"])
        self.accent.configure(bg=color)
        for item in (self.brand, self.dot, self.title, self.detail, self.more):
            item.configure(bg="#FFFFFF")
        self.dot.configure(fg=color)
        self.title.configure(text=title)
        self.detail.configure(text=detail[:54])

    def open_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("AutoDL Watch 设置")
        dialog.geometry("660x700")
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        footer = ttk.Frame(dialog, padding=(22, 10))
        footer.pack(side="bottom", fill="x")
        content = tk.Canvas(dialog, highlightthickness=0)
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=content.yview)
        scrollbar.pack(side="right", fill="y")
        content.pack(side="left", fill="both", expand=True)
        content.configure(yscrollcommand=scrollbar.set)
        body = ttk.Frame(content, padding=22)
        body_window = content.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _e: content.configure(scrollregion=content.bbox("all")))
        content.bind("<Configure>", lambda e: content.itemconfigure(body_window, width=e.width))
        content.bind("<MouseWheel>", lambda e: content.yview_scroll(int(-e.delta / 120), "units"))
        ttk.Label(body, text="AutoDL 自动监测", font=("Microsoft YaHei UI", 15, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(body, text="普通实例使用网页会话；会话 Token 通过 Windows DPAPI 加密保存。", foreground="#64748b").grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 16))

        values = {
            "token": tk.StringVar(value=self.cfg.token()),
            "interval_seconds": tk.StringVar(value=str(self.cfg.interval_seconds)),
            "instance_uuid": tk.StringVar(value=self.cfg.instance_uuid),
            "gpu_spec_uuid": tk.StringVar(value=self.cfg.gpu_spec_uuid),
            "gpu_display_name": tk.StringVar(value=self.cfg.gpu_display_name),
            "image_uuid": tk.StringVar(value=self.cfg.image_uuid),
            "gpu_amount": tk.StringVar(value=str(self.cfg.gpu_amount)),
            "cuda_v_from": tk.StringVar(value=str(self.cfg.cuda_v_from)),
            "regions": tk.StringVar(value=self.cfg.regions),
            "instance_name": tk.StringVar(value=self.cfg.instance_name),
            "retry_cooldown_seconds": tk.StringVar(value=str(self.cfg.retry_cooldown_seconds)),
            "max_created_instances": tk.StringVar(value=str(self.cfg.max_created_instances)),
        }
        mode = tk.StringVar(value=self.cfg.mode)
        armed = tk.BooleanVar(value=self.cfg.armed)

        rows = [
            ("网页会话 Token", "token", "建议使用下方网页登录"),
            ("检查间隔（秒）", "interval_seconds", ""),
            ("目标实例", "instance_uuid", "可读取后选择"),
            ("GPU 规格 ID", "gpu_spec_uuid", "例如 4090D / 5090-p"),
            ("GPU 显示名称", "gpu_display_name", "仅用于界面显示"),
            ("创建使用的镜像", "image_uuid", "可读取后选择"),
            ("GPU 数量", "gpu_amount", "1–4"),
            ("最低 CUDA 编码", "cuda_v_from", "例如 118"),
            ("地区代码", "regions", "逗号分隔；留空由平台调度"),
            ("实例名称", "instance_name", ""),
            ("操作冷却（秒）", "retry_cooldown_seconds", ""),
            ("单次运行创建上限", "max_created_instances", ""),
        ]
        entries = {}
        for index, (label, key, hint) in enumerate(rows, start=2):
            ttk.Label(body, text=label).grid(row=index, column=0, sticky="w", pady=5)
            if key in ("instance_uuid", "image_uuid"):
                entry = ttk.Combobox(body, textvariable=values[key], width=32)
            else:
                entry = ttk.Entry(body, textvariable=values[key], width=34, show="•" if key == "token" else "")
            entry.grid(row=index, column=1, sticky="ew", padx=(12, 8), pady=5)
            ttk.Label(body, text=hint, foreground="#94a3b8").grid(row=index, column=2, sticky="w")
            entries[key] = entry

        instance_box = entries["instance_uuid"]
        image_box = entries["image_uuid"]
        instance_map = {}
        image_map = {}

        def cuda_code(text):
            match = re.search(r"cuda\s*([0-9]+)(?:\.([0-9]+))?", text, re.IGNORECASE)
            if not match:
                return None
            major = int(match.group(1))
            minor = int(match.group(2) or 0)
            return major * 10 + minor

        def instance_selected(_event=None):
            item = instance_map.get(instance_box.get())
            if not item:
                return
            values["instance_uuid"].set(item.get("uuid", ""))
            spec = item.get("gpu_spec_uuid") or item.get("gpu_spec_config_id") or item.get("gpu_name") or ""
            if spec:
                values["gpu_spec_uuid"].set(str(spec))
            display = (
                item.get("snapshot_gpu_alias_name")
                or item.get("user_define_gpu_name")
                or item.get("gpu_alias_name")
                or item.get("gpu_name")
                or spec
            )
            if display:
                values["gpu_display_name"].set(str(display))
            highest_cuda = item.get("highest_cuda_version")
            if highest_cuda:
                try:
                    number = float(highest_cuda)
                    values["cuda_v_from"].set(str(round(number * 10)))
                except (TypeError, ValueError):
                    pass

        def image_selected(_event=None):
            item = image_map.get(image_box.get())
            if not item:
                return
            values["image_uuid"].set(item.get("image_uuid", ""))
            code = cuda_code(" ".join(str(v) for v in item.values()))
            if code:
                values["cuda_v_from"].set(str(code))

        instance_box.bind("<<ComboboxSelected>>", instance_selected)
        image_box.bind("<<ComboboxSelected>>", image_selected)

        load_state = tk.StringVar(value="")
        resource_results = queue.Queue()

        def login_web():
            login_button.configure(state="disabled")
            load_state.set("请在 Edge 窗口中登录 AutoDL…")

            def task():
                try:
                    resource_results.put(("login", login_and_capture_token()))
                except Exception as exc:
                    resource_results.put(("login_error", str(exc)))

            threading.Thread(target=task, daemon=True).start()

        def apply_resources(instances_result, images_result):
            load_button.configure(state="normal")
            if not instances_result.ok:
                load_state.set("读取失败：" + self.clean_message(instances_result))
                return
            instances = (instances_result.data or {}).get("list", []) or []
            instance_labels = []
            instance_map.clear()
            for item in instances:
                gpu = (
                    item.get("snapshot_gpu_alias_name")
                    or item.get("user_define_gpu_name")
                    or item.get("gpu_spec_config_id")
                    or "未知GPU"
                )
                label = f"{item.get('name') or '未命名'}  ·  {gpu}  ·  {item.get('status')}  [{item.get('uuid')}]"
                instance_labels.append(label)
                instance_map[label] = item
            instance_box.configure(values=instance_labels)

            image_labels = []
            image_map.clear()
            if images_result.ok:
                images = (images_result.data or {}).get("list", []) or []
                for item in images:
                    label = f"{item.get('name') or item.get('image_name') or '未命名镜像'}  [{item.get('image_uuid')}]"
                    image_labels.append(label)
                    image_map[label] = item
                image_box.configure(values=image_labels)
                load_state.set(f"已读取 {len(instances)} 个实例、{len(images)} 个私有镜像")
            else:
                load_state.set(f"已读取 {len(instances)} 个实例；镜像读取失败")

        def load_resources():
            token = values["token"].get().strip()
            if not token:
                messagebox.showwarning("缺少登录会话", "请先点击“登录 AutoDL 网页”。", parent=dialog)
                return
            load_button.configure(state="disabled")
            load_state.set("正在读取普通实例…")

            def task():
                client = AutoDLWebClient(token)
                instances_result = client.list_instances()
                images_result = ApiResult(True, {"list": []})
                resource_results.put((instances_result, images_result))

            threading.Thread(target=task, daemon=True).start()

        def poll_resource_results():
            try:
                result = resource_results.get_nowait()
                if result[0] == "login":
                    values["token"].set(result[1])
                    self.cfg.encrypted_token = protect(result[1])
                    self.cfg.save()
                    login_button.configure(state="normal")
                    load_state.set("登录成功，会话已加密保存；正在读取实例…")
                    load_resources()
                elif result[0] == "login_error":
                    login_button.configure(state="normal")
                    load_state.set("登录失败：" + result[1][:60])
                else:
                    instances_result, images_result = result
                    apply_resources(instances_result, images_result)
            except queue.Empty:
                pass
            try:
                if dialog.winfo_exists():
                    dialog.after(120, poll_resource_results)
            except tk.TclError:
                pass

        resource_bar = ttk.Frame(body)
        resource_bar.grid(row=14, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        login_button = ttk.Button(resource_bar, text="登录 AutoDL 网页", command=login_web)
        login_button.pack(side="left")
        load_button = ttk.Button(resource_bar, text="读取普通实例", command=load_resources)
        load_button.pack(side="left", padx=(8, 0))
        ttk.Label(resource_bar, textvariable=load_state, foreground="#64748b").pack(side="left", padx=12)
        dialog.after(120, poll_resource_results)

        mode_row = 15
        ttk.Label(body, text="运行模式").grid(row=mode_row, column=0, sticky="nw", pady=(12, 4))
        modes = ttk.Frame(body)
        modes.grid(row=mode_row, column=1, columnspan=2, sticky="w", pady=(8, 4))
        ttk.Radiobutton(modes, text="只通知", value="notify", variable=mode).pack(side="left")
        ttk.Radiobutton(modes, text="自动开机", value="power_on", variable=mode).pack(side="left", padx=12)
        ttk.Radiobutton(modes, text="自动克隆（待验证）", value="create", variable=mode).pack(side="left")
        armed_box = ttk.Checkbutton(body, text="启用自动计费操作（成功后自动暂停）", variable=armed)
        armed_box.grid(row=16, column=1, columnspan=2, sticky="w", pady=6)
        ttk.Label(body, text="自动开机或创建会产生费用。程序不会自动关机或释放实例。", foreground="#b45309").grid(row=17, column=0, columnspan=3, sticky="w", pady=(8, 4))

        clone_only = (
            "gpu_spec_uuid", "gpu_display_name", "image_uuid", "gpu_amount",
            "cuda_v_from", "regions", "instance_name", "max_created_instances",
        )

        def update_mode_fields(*_args):
            selected = mode.get()
            for key in clone_only:
                entries[key].configure(state="normal" if selected == "create" else "disabled")
            entries["instance_uuid"].configure(state="disabled" if selected == "create" else "normal")
            armed_box.configure(state="disabled" if selected == "notify" else "normal")
            if selected == "notify":
                armed.set(False)

        mode.trace_add("write", update_mode_fields)
        update_mode_fields()

        def save():
            try:
                chosen_mode = mode.get()
                chosen_armed = armed.get() and chosen_mode != "notify"
                if chosen_armed:
                    ok = messagebox.askyesno(
                        "确认自动计费操作",
                        "启用后，程序会在条件满足时自动开机或创建实例，并可能立即开始计费。\n\n是否确认启用？",
                        parent=dialog,
                    )
                    if not ok:
                        return
                token = values["token"].get().strip()
                self.cfg.encrypted_token = protect(token) if token else ""
                self.cfg.interval_seconds = max(10, int(values["interval_seconds"].get()))
                self.cfg.mode = chosen_mode
                self.cfg.armed = chosen_armed
                self.cfg.instance_uuid = values["instance_uuid"].get().strip()
                self.cfg.gpu_spec_uuid = values["gpu_spec_uuid"].get().strip()
                self.cfg.gpu_display_name = values["gpu_display_name"].get().strip()
                self.cfg.image_uuid = values["image_uuid"].get().strip()
                self.cfg.gpu_amount = min(4, max(1, int(values["gpu_amount"].get())))
                self.cfg.cuda_v_from = int(values["cuda_v_from"].get())
                self.cfg.regions = values["regions"].get().strip()
                self.cfg.instance_name = values["instance_name"].get().strip()
                self.cfg.retry_cooldown_seconds = max(30, int(values["retry_cooldown_seconds"].get()))
                self.cfg.max_created_instances = max(1, int(values["max_created_instances"].get()))
                self.cfg.save()
                dialog.destroy()
                self.update_mode_badge()
                self.run_now()
            except ValueError:
                messagebox.showerror("设置错误", "数值字段包含无效内容。", parent=dialog)
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc), parent=dialog)

        ttk.Button(footer, text="保存并检查", command=save, style="Primary.TButton").pack(side="right")
        ttk.Button(footer, text="取消", command=dialog.destroy).pack(side="right", padx=8)


if __name__ == "__main__":
    root = tk.Tk()
    AutoDLWatch(root)
    root.mainloop()

