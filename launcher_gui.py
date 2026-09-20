"""Small dark launcher UI for managing game accounts.

The game automation hooks are intentionally not started by this module yet.
This first screen only manages account assignments and window preferences.
"""

from dataclasses import asdict, dataclass
import json
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from game_automation import GameWorker, LoginPlan

BOSS_TYPES = ("Trộm chó", "Ngáo ộp", "Đại sơn tặc")
WINDOW_SIZES = ("320x180", "480x270", "640x360", "800x450", "860x484")


@dataclass
class Account:
    username: str
    password: str
    boss: str
    window_size: str
    status: str = "Chưa đăng nhập"


class AccountStore:
    def __init__(self, path: str | Path = "accounts.json"):
        self.path = Path(path)
        self.accounts: list[Account] = []

    def add(self, username: str, password: str, boss: str, window_size: str) -> Account:
        username, password = username.strip(), password.strip()
        if not username or not password:
            raise ValueError("Vui lòng nhập tài khoản và mật khẩu")
        if boss not in BOSS_TYPES:
            raise ValueError("Loại boss không hợp lệ")
        if window_size not in WINDOW_SIZES:
            raise ValueError("Kích thước cửa sổ không hợp lệ")
        account = Account(username, password, boss, window_size)
        self.accounts.append(account)
        return account

    def remove(self, index: int) -> None:
        del self.accounts[index]

    def save(self) -> None:
        self.path.write_text(
            json.dumps([asdict(item) for item in self.accounts], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.accounts = [Account(**item) for item in data]


class LauncherApp(tk.Tk):
    BG = "#111820"
    PANEL = "#18232d"
    BORDER = "#2a3946"
    TEXT = "#d9e2e9"
    MUTED = "#8ea0ad"
    ACCENT = "#d6a84f"

    def __init__(self, store: AccountStore | None = None):
        super().__init__()
        self.store = store or AccountStore()
        self.workers = {}
        self.store.load()
        self.title("Thien Menh Lac Hong - Multi Account")
        self.geometry("720x520")
        self.minsize(620, 420)
        self.configure(bg=self.BG)
        self._build_style()
        self._build_ui()
        self._refresh_table()

    def _build_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("App.TFrame", background=self.BG)
        style.configure("Panel.TLabelframe", background=self.PANEL, foreground=self.TEXT)
        style.configure("Panel.TLabelframe.Label", background=self.PANEL, foreground=self.ACCENT)
        style.configure("TLabel", background=self.BG, foreground=self.TEXT)
        style.configure("Muted.TLabel", background=self.BG, foreground=self.MUTED)
        style.configure("TButton", padding=(8, 4))
        style.configure("Accent.TButton", foreground="#17120a")
        style.configure("Treeview", background="#10171e", fieldbackground="#10171e", foreground=self.TEXT, rowheight=27)
        style.configure("Treeview.Heading", background=self.PANEL, foreground=self.ACCENT)

    def _build_ui(self):
        root = ttk.Frame(self, style="App.TFrame", padding=10)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="QUẢN LÝ TÀI KHOẢN", font=("Segoe UI", 12, "bold"), foreground=self.ACCENT).pack(anchor="w")
        ttk.Label(root, text="Mỗi tài khoản được gán một boss và một kích thước cửa sổ", style="Muted.TLabel").pack(anchor="w", pady=(2, 8))

        form = ttk.LabelFrame(root, text="Thêm tài khoản", style="Panel.TLabelframe", padding=8)
        form.pack(fill="x", pady=(0, 8))
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.boss = tk.StringVar(value=BOSS_TYPES[0])
        self.window_size = tk.StringVar(value=WINDOW_SIZES[0])
        fields = [("Tài khoản", self.username), ("Mật khẩu", self.password)]
        for col, (label, var) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=0, column=col * 2, sticky="w", padx=(0, 5))
            ttk.Entry(form, textvariable=var, show="•" if col else "").grid(row=0, column=col * 2 + 1, sticky="ew", padx=(0, 10))
        ttk.Label(form, text="Boss").grid(row=1, column=0, sticky="w", pady=(7, 0))
        ttk.Combobox(form, textvariable=self.boss, values=BOSS_TYPES, state="readonly").grid(row=1, column=1, sticky="ew", pady=(7, 0), padx=(0, 10))
        ttk.Label(form, text="Kích thước").grid(row=1, column=2, sticky="w", pady=(7, 0))
        ttk.Combobox(form, textvariable=self.window_size, values=WINDOW_SIZES, state="readonly").grid(row=1, column=3, sticky="ew", pady=(7, 0), padx=(0, 10))
        ttk.Button(form, text="＋ Thêm", style="Accent.TButton", command=self._add_account).grid(row=1, column=4, pady=(7, 0))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        table_frame = ttk.Frame(root, style="App.TFrame")
        table_frame.pack(fill="both", expand=True)
        columns = ("account", "boss", "size", "status")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended")
        for key, text, width in (("account", "Tài khoản", 170), ("boss", "Boss", 150), ("size", "Cửa sổ", 90), ("status", "Trạng thái", 145)):
            self.table.heading(key, text=text)
            self.table.column(key, width=width, anchor="w")
        self.table.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        scrollbar.pack(side="right", fill="y")
        self.table.configure(yscrollcommand=scrollbar.set)

        actions = ttk.Frame(root, style="App.TFrame")
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Đăng nhập đã chọn", command=self._login_selected).pack(side="left")
        ttk.Button(actions, text="Xếp cửa sổ", command=lambda: self._log("Đã chuẩn bị xếp cửa sổ theo kích thước đã chọn")).pack(side="left", padx=6)
        ttk.Button(actions, text="Xóa", command=self._remove_selected).pack(side="left")
        log_frame = ttk.LabelFrame(root, text="Log debug", style="Panel.TLabelframe", padding=5)
        log_frame.pack(fill="x", pady=(8, 0))
        self.log_text = tk.Text(log_frame, height=5, bg="#0c1116", fg="#aebdca", insertbackground="#ffffff", relief="flat", state="disabled")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")
        self._log("Sẵn sàng")

    def _refresh_table(self):
        self.table.delete(*self.table.get_children())
        for item in self.store.accounts:
            self.table.insert("", "end", values=(item.username, item.boss, item.window_size, item.status))

    def _add_account(self):
        try:
            self.store.add(self.username.get(), self.password.get(), self.boss.get(), self.window_size.get())
            self.store.save()
        except (ValueError, OSError) as exc:
            messagebox.showerror("Không thể thêm tài khoản", str(exc), parent=self)
            return
        self.username.set("")
        self.password.set("")
        self._refresh_table()
        self._log("Đã thêm tài khoản")

    def _remove_selected(self):
        selected = list(self.table.selection())
        for item_id in reversed(selected):
            self.store.remove(self.table.index(item_id))
        if selected:
            self.store.save()
            self._refresh_table()
            self._log(f"Đã xóa {len(selected)} tài khoản")

    def _login_selected(self):
        selected = self.table.selection()
        if not selected:
            self._log("Hãy chọn ít nhất một tài khoản")
            return
        for item_id in selected:
            index = self.table.index(item_id)
            account = self.store.accounts[index]
            if index in self.workers:
                continue
            account.status = "Đang mở game"
            worker = GameWorker(
                LoginPlan(account.username, account.password),
                on_status=lambda status, index=index: self.after(0, self._worker_status, index, status),
            )
            self.workers[index] = worker
            worker.start()
        self.store.save()
        self._refresh_table()
        self._log(f"Đã khởi chạy {len(selected)} worker game độc lập")

    def _worker_status(self, index, status):
        if index < len(self.store.accounts):
            self.store.accounts[index].status = status
            self.store.save()
            self._refresh_table()
        self._log(status)

    def _log(self, text: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {text}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


if __name__ == "__main__":
    LauncherApp().mainloop()
