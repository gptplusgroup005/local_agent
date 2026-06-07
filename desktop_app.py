from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from collections import Counter
from tkinter import messagebox, ttk
from typing import Any

from talos_core import (
    APP_MIN_HEIGHT,
    APP_MIN_WIDTH,
    CYBER,
    DEFAULT_CONFIG,
    DETAIL_PANE_MIN_HEIGHT,
    LANGUAGES,
    MEMORY_PATH,
    QUEUE_SPLIT_INITIAL_RATIO,
    QUEUE_SPLITTER_HEIGHT,
    QUEUE_PANE_MIN_HEIGHT,
    ROOT,
    TASKS_PATH,
    TASK_STATUSES,
    WEBVIEW_MIN_HEIGHT,
    WEBVIEW_MIN_WIDTH,
    ConversationMemory,
    LocalComputerActionEngine,
    TaskStore,
    check_ollama,
    language_code,
    language_label,
    load_config,
    now,
    preview_text,
    process_prompt,
    queue_split_initial_sash_y,
    save_config,
)

class HoloPanel(tk.Canvas):
    def __init__(
        self,
        parent: tk.Widget,
        *,
        bg_color: str,
        line_color: str,
        glow_color: str,
        min_height: int = 140,
        min_width: int = 240,
        inset: int = 10,
    ) -> None:
        super().__init__(
            parent,
            bg=CYBER["bg"],
            bd=0,
            highlightthickness=0,
            width=min_width,
            height=min_height,
        )
        self.bg_color = bg_color
        self.line_color = line_color
        self.glow_color = glow_color
        self.inset = inset
        self.inner = tk.Frame(self, bg=bg_color)
        self.inner_window = self.create_window(inset, inset, anchor="nw", window=self.inner)
        self.bind("<Configure>", self.draw)

    def draw(self, _event: tk.Event | None = None) -> None:
        self.delete("surface")
        width = max(self.winfo_width(), 2)
        height = max(self.winfo_height(), 2)
        inset = self.inset
        x1, y1 = inset, inset
        x2, y2 = width - inset, height - inset
        cut = 18

        self.coords(self.inner_window, inset + 3, inset + 3)
        self.itemconfigure(self.inner_window, width=max(1, width - (inset + 3) * 2), height=max(1, height - (inset + 3) * 2))

        self.create_rectangle(0, 0, width, height, fill=CYBER["bg"], outline="", tags="surface")
        self.create_polygon(
            x1 + 8,
            y1 + 12,
            x2 + 6,
            y1 + 12,
            x2 + 6,
            y2 + 7,
            x1 + 8,
            y2 + 7,
            fill=CYBER["bg"],
            outline="",
            tags="surface",
        )
        for expand, color in ((8, CYBER["glow_soft"]), (4, CYBER["line_soft"])):
            self.create_polygon(
                x1 - expand + cut,
                y1 - expand,
                x2 + expand,
                y1 - expand,
                x2 + expand,
                y2 + expand - cut,
                x2 + expand - cut,
                y2 + expand,
                x1 - expand,
                y2 + expand,
                x1 - expand,
                y1 - expand + cut,
                outline=color,
                fill="",
                width=1,
                tags="surface",
            )
        self.create_polygon(
            x1 + cut,
            y1,
            x2,
            y1,
            x2,
            y2 - cut,
            x2 - cut,
            y2,
            x1,
            y2,
            x1,
            y1 + cut,
            fill=self.bg_color,
            outline=self.line_color,
            width=1,
            tags="surface",
        )
        self.create_line(x1 + cut + 2, y1 + 2, x2 - 6, y1 + 2, fill=self.glow_color, width=2, tags="surface")
        self.create_line(x1 + 2, y1 + cut + 2, x1 + 2, y2 - 6, fill=CYBER["line_soft"], width=1, tags="surface")
        self.create_line(x1 + 20, y2 - 3, x2 - cut - 2, y2 - 3, fill=CYBER["bg_2"], width=3, tags="surface")
        self.create_line(x2 - 2, y1 + 20, x2 - 2, y2 - cut - 2, fill=CYBER["bg_2"], width=3, tags="surface")
        self.create_line(x1 + 10, y1 + 16, x1 + cut, y1 + 2, fill=CYBER["text"], width=1, tags="surface")
        self.create_line(x2 - cut, y2 - 2, x2 - 2, y2 - cut, fill=self.glow_color, width=1, tags="surface")
        self.tag_lower("surface", self.inner_window)

class AutoScrollbar(ttk.Scrollbar):
    def __init__(self, parent: tk.Widget, **options: Any) -> None:
        super().__init__(parent, **options)
        self.visible = True

    def set(self, first: str, last: str) -> None:
        if float(first) <= 0.0 and float(last) >= 1.0:
            if self.visible:
                self.grid_remove()
                self.visible = False
        elif not self.visible:
            self.grid()
            self.visible = True
        super().set(first, last)

class LocalAgentDesktop(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Talos")
        self.overrideredirect(True)
        self.geometry("1120x720")
        self.minsize(APP_MIN_WIDTH, APP_MIN_HEIGHT)
        self.configure(bg=CYBER["bg"])
        self.is_maximized = False
        self.is_minimized = False
        self.normal_geometry = "1120x720"
        self.drag_start_x = 0
        self.drag_start_y = 0

        self.config_data = load_config()
        self.store = TaskStore(TASKS_PATH)
        self.memory = ConversationMemory(MEMORY_PATH)
        self.events: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.selected_task_ids: set[int] = set()

        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.configure_style()
        self.build_ui()
        self.refresh_all()

        self.worker = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker.start()
        self.after(1000, self.tick)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Map>", self.restore_window_chrome)

    def configure_style(self) -> None:
        self.style.configure("TFrame", background=CYBER["bg"])
        self.style.configure("Rail.TFrame", background=CYBER["rail"])
        self.style.configure("Panel.TFrame", background=CYBER["panel"], relief="flat")
        self.style.configure("Card.TFrame", background=CYBER["panel_2"], relief="flat")
        self.style.configure("TLabel", background=CYBER["bg"], foreground=CYBER["text"], font=("Segoe UI", 10))
        self.style.configure("Muted.TLabel", background=CYBER["panel"], foreground=CYBER["muted"], font=("Cascadia Mono", 9))
        self.style.configure("Panel.TLabel", background=CYBER["panel"], foreground=CYBER["text"], font=("Cascadia Mono", 10, "bold"))
        self.style.configure("Hero.TLabel", background=CYBER["bg"], foreground=CYBER["cyan"], font=("Cascadia Mono", 23, "bold"))
        self.style.configure(
            "TButton",
            padding=(12, 8),
            background=CYBER["panel_2"],
            foreground=CYBER["text"],
            bordercolor=CYBER["line"],
            lightcolor=CYBER["line_soft"],
            darkcolor=CYBER["rail"],
            focuscolor=CYBER["cyan"],
            relief="flat",
            borderwidth=1,
            font=("Cascadia Mono", 9),
        )
        self.style.configure(
            "Accent.TButton",
            padding=(14, 9),
            background=CYBER["cyan"],
            foreground=CYBER["bg"],
            bordercolor=CYBER["glow"],
            lightcolor=CYBER["text"],
            darkcolor=CYBER["deep_blue"],
            focuscolor=CYBER["glow"],
            relief="flat",
            borderwidth=1,
            font=("Cascadia Mono", 9, "bold"),
        )
        self.style.map(
            "TButton",
            background=[("pressed", CYBER["glow_soft"]), ("active", CYBER["deep_blue"])],
            bordercolor=[("active", CYBER["glow"])],
            foreground=[("active", CYBER["text"])],
        )
        self.style.map(
            "Accent.TButton",
            background=[("pressed", CYBER["green"]), ("active", CYBER["blue"])],
            foreground=[("active", CYBER["text"])],
        )
        self.style.configure("TNotebook", background=CYBER["bg"], bordercolor=CYBER["line"], tabmargins=(0, 0, 0, 0))
        self.style.configure(
            "TNotebook.Tab",
            padding=(18, 10),
            background=CYBER["field"],
            foreground=CYBER["muted"],
            bordercolor=CYBER["line_soft"],
            font=("Cascadia Mono", 9, "bold"),
        )
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", CYBER["panel_2"]), ("active", CYBER["glow_soft"])],
            foreground=[("selected", CYBER["cyan"]), ("active", CYBER["text"])],
            bordercolor=[("selected", CYBER["glow"])],
        )
        self.style.configure(
            "Treeview",
            rowheight=31,
            background=CYBER["field"],
            fieldbackground=CYBER["field"],
            foreground=CYBER["text"],
            bordercolor=CYBER["line"],
            lightcolor=CYBER["line_soft"],
            darkcolor=CYBER["rail"],
            borderwidth=0,
            font=("Cascadia Mono", 9),
        )
        self.style.configure(
            "Treeview.Heading",
            padding=(8, 9),
            background=CYBER["panel_2"],
            foreground=CYBER["cyan"],
            bordercolor=CYBER["line"],
            font=("Cascadia Mono", 9, "bold"),
        )
        self.style.map("Treeview", background=[("selected", CYBER["deep_blue"])], foreground=[("selected", CYBER["text"])])
        self.style.configure(
            "TEntry",
            fieldbackground=CYBER["field"],
            background=CYBER["field"],
            foreground=CYBER["text"],
            insertcolor=CYBER["cyan"],
            bordercolor=CYBER["line_soft"],
            lightcolor=CYBER["line_soft"],
            darkcolor=CYBER["rail"],
            padding=8,
        )
        self.style.map("TEntry", bordercolor=[("focus", CYBER["glow"])])
        self.style.configure(
            "TCombobox",
            fieldbackground=CYBER["field"],
            background=CYBER["panel_2"],
            foreground=CYBER["text"],
            arrowcolor=CYBER["cyan"],
            bordercolor=CYBER["line_soft"],
            padding=6,
        )
        self.style.map("TCombobox", fieldbackground=[("readonly", CYBER["field"])], bordercolor=[("focus", CYBER["glow"])])
        self.style.configure(
            "TCheckbutton",
            background=CYBER["panel"],
            foreground=CYBER["text"],
            focuscolor=CYBER["glow"],
            font=("Cascadia Mono", 9),
        )
        self.style.map("TCheckbutton", foreground=[("active", CYBER["cyan"])], background=[("active", CYBER["panel"])])

    def build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        outer = tk.Frame(self, bg=CYBER["glow"], padx=1, pady=1)
        outer.grid(row=0, column=0, rowspan=2, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        titlebar = tk.Frame(outer, bg=CYBER["rail"], height=42)
        titlebar.grid(row=0, column=0, sticky="ew")
        titlebar.grid_propagate(False)
        titlebar.columnconfigure(1, weight=1)
        titlebar.bind("<ButtonPress-1>", self.start_window_drag)
        titlebar.bind("<B1-Motion>", self.drag_window)
        titlebar.bind("<Double-Button-1>", lambda _event: self.toggle_maximize())

        tk.Label(
            titlebar,
            text="LA",
            bg=CYBER["blue"],
            fg=CYBER["text"],
            font=("Cascadia Mono", 10, "bold"),
            width=4,
        ).grid(row=0, column=0, sticky="ns", padx=(10, 8), pady=7)
        title_label = tk.Label(
            titlebar,
            text="TALOS :: ONLINE",
            bg=CYBER["rail"],
            fg=CYBER["cyan"],
            font=("Cascadia Mono", 10, "bold"),
        )
        title_label.grid(row=0, column=1, sticky="w")
        title_label.bind("<ButtonPress-1>", self.start_window_drag)
        title_label.bind("<B1-Motion>", self.drag_window)
        title_label.bind("<Double-Button-1>", lambda _event: self.toggle_maximize())

        window_buttons = tk.Frame(titlebar, bg=CYBER["rail"])
        window_buttons.grid(row=0, column=2, sticky="e")
        self.make_window_button(window_buttons, "-", self.minimize_window).pack(side="left")
        self.make_window_button(window_buttons, "[]", self.toggle_maximize).pack(side="left")
        self.make_window_button(window_buttons, "X", self.on_close, danger=True).pack(side="left")

        app_frame = ttk.Frame(outer, style="TFrame")
        app_frame.grid(row=1, column=0, sticky="nsew")
        app_frame.columnconfigure(1, weight=1)
        app_frame.rowconfigure(0, weight=1)

        rail = ttk.Frame(app_frame, width=92, style="Rail.TFrame")
        rail.grid(row=0, column=0, sticky="ns")
        rail.grid_propagate(False)

        rail_glow = tk.Canvas(rail, width=54, height=54, bg=CYBER["rail"], highlightthickness=0)
        rail_glow.pack(pady=(18, 18))
        rail_glow.create_oval(5, 5, 49, 49, outline=CYBER["glow_soft"], width=4)
        rail_glow.create_oval(10, 10, 44, 44, outline=CYBER["cyan"], width=2)
        rail_glow.create_text(27, 27, text="LA", fill=CYBER["text"], font=("Cascadia Mono", 14, "bold"))
        for label, command in [
            ("Dash", self.show_dashboard),
            ("Queue", self.show_queue),
            ("Logs", self.show_logs),
            ("Set", self.show_settings),
        ]:
            ttk.Button(rail, text=label, command=command).pack(fill="x", padx=10, pady=5)

        self.content = ttk.Frame(app_frame, padding=18, style="TFrame")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(1, weight=1)

        header = ttk.Frame(self.content, style="TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Talos", style="Hero.TLabel").grid(row=0, column=0, sticky="w")
        self.mode_label = ttk.Label(header, text="", foreground=CYBER["muted"])
        self.mode_label.grid(row=1, column=0, sticky="w")
        self.header_reactor = tk.Canvas(header, width=118, height=54, bg=CYBER["bg"], highlightthickness=0)
        self.header_reactor.grid(row=0, column=1, rowspan=2, padx=(10, 12), sticky="e")
        self.header_reactor.bind("<Configure>", self.draw_header_reactor)
        ttk.Button(header, text="Refresh", command=self.refresh_all).grid(row=0, column=2, rowspan=2, padx=(0, 0))

        notebook_outer, notebook_inner = self.make_glow_frame(self.content, bg=CYBER["panel"], glow=CYBER["glow_soft"])
        notebook_outer.grid(row=1, column=0, sticky="nsew")
        notebook_inner.columnconfigure(0, weight=1)
        notebook_inner.rowconfigure(0, weight=1)
        self.notebook = ttk.Notebook(notebook_inner)
        self.notebook.grid(row=0, column=0, sticky="nsew")

        self.dashboard_tab = ttk.Frame(self.notebook, padding=14, style="Panel.TFrame")
        self.queue_tab = ttk.Frame(self.notebook, padding=14, style="Panel.TFrame")
        self.logs_tab = ttk.Frame(self.notebook, padding=14, style="Panel.TFrame")
        self.settings_tab = ttk.Frame(self.notebook, padding=14, style="Panel.TFrame")
        self.notebook.add(self.dashboard_tab, text="Dashboard")
        self.notebook.add(self.queue_tab, text="Queue")
        self.notebook.add(self.logs_tab, text="Logs")
        self.notebook.add(self.settings_tab, text="Settings")

        self.build_dashboard()
        self.build_queue()
        self.build_logs()
        self.build_settings()

    def make_glow_frame(
        self,
        parent: tk.Widget,
        *,
        bg: str | None = None,
        glow: str | None = None,
        line: str | None = None,
        depth: int = 2,
    ) -> tuple[tk.Widget, tk.Frame]:
        glow_color = glow or CYBER["glow"]
        line_color = line or CYBER["line"]
        body_color = bg or CYBER["field"]
        min_height = 150 + depth * 12
        panel = HoloPanel(
            parent,
            bg_color=body_color,
            line_color=line_color,
            glow_color=glow_color,
            min_height=min_height,
            inset=8 + depth,
        )
        return panel, panel.inner

    def draw_header_reactor(self, _event: tk.Event | None = None) -> None:
        self.header_reactor.delete("all")
        width = self.header_reactor.winfo_width()
        height = self.header_reactor.winfo_height()
        cy = height // 2
        self.header_reactor.create_line(0, cy, width, cy, fill=CYBER["line_soft"], width=1)
        for radius, color, line_width in ((21, CYBER["glow_soft"], 5), (17, CYBER["cyan"], 2), (9, CYBER["green"], 2)):
            self.header_reactor.create_oval(
                width - 55 - radius,
                cy - radius,
                width - 55 + radius,
                cy + radius,
                outline=color,
                width=line_width,
            )
        self.header_reactor.create_arc(width - 82, cy - 27, width - 28, cy + 27, start=25, extent=125, outline=CYBER["amber"], width=2, style="arc")
        self.header_reactor.create_line(8, cy, width - 82, cy, fill=CYBER["cyan"], width=2)
        self.header_reactor.create_line(18, cy - 9, width - 95, cy - 9, fill=CYBER["line_soft"], width=1)

    def make_window_button(self, parent: tk.Widget, text: str, command: Any, danger: bool = False) -> tk.Button:
        bg = CYBER["rail"]
        active = CYBER["fail"] if danger else CYBER["deep_blue"]
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=CYBER["text"],
            activebackground=active,
            activeforeground=CYBER["text"],
            bd=0,
            highlightthickness=0,
            width=5,
            height=2,
            font=("Cascadia Mono", 10, "bold"),
        )

    def make_text_box(self, parent: tk.Widget, **options: Any) -> tk.Text:
        defaults = {
            "bg": CYBER["field"],
            "fg": CYBER["text"],
            "insertbackground": CYBER["cyan"],
            "selectbackground": CYBER["deep_blue"],
            "highlightthickness": 1,
            "highlightbackground": CYBER["line"],
            "highlightcolor": CYBER["glow"],
            "relief": "flat",
            "bd": 0,
            "padx": 14,
            "pady": 12,
            "wrap": "word",
            "font": ("Cascadia Mono", 10),
        }
        defaults.update(options)
        return tk.Text(parent, **defaults)

    def start_window_drag(self, event: tk.Event) -> None:
        if self.is_maximized:
            return
        self.drag_start_x = event.x_root - self.winfo_x()
        self.drag_start_y = event.y_root - self.winfo_y()

    def drag_window(self, event: tk.Event) -> None:
        if self.is_maximized:
            return
        x = event.x_root - self.drag_start_x
        y = event.y_root - self.drag_start_y
        self.geometry(f"+{x}+{y}")

    def minimize_window(self) -> None:
        self.is_minimized = True
        self.overrideredirect(False)
        self.update_idletasks()
        self.iconify()

    def restore_window_chrome(self, _event: tk.Event | None = None) -> None:
        if not self.is_minimized or self.state() == "iconic":
            return
        self.is_minimized = False
        self.after(10, lambda: self.overrideredirect(True))

    def toggle_maximize(self) -> None:
        if self.is_maximized:
            self.geometry(self.normal_geometry)
            self.is_maximized = False
            return
        self.normal_geometry = self.geometry()
        self.geometry(
            f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0"
        )
        self.is_maximized = True

    def build_dashboard(self) -> None:
        self.dashboard_tab.columnconfigure(0, weight=1)
        self.dashboard_tab.rowconfigure(3, weight=1)
        self.scanline = tk.Canvas(self.dashboard_tab, height=22, bg=CYBER["panel"], highlightthickness=0)
        self.scanline.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.scanline.bind("<Configure>", self.draw_scanline)

        self.stats_label = ttk.Label(self.dashboard_tab, text="", style="Panel.TLabel", font=("Cascadia Mono", 12, "bold"), foreground=CYBER["green"])
        self.stats_label.grid(row=1, column=0, sticky="w", pady=(0, 12))
        ttk.Label(self.dashboard_tab, text="COMMAND_DECK", style="Panel.TLabel", font=("Cascadia Mono", 13, "bold"), foreground=CYBER["cyan"]).grid(row=2, column=0, sticky="w")

        prompt_outer, prompt_inner = self.make_glow_frame(self.dashboard_tab, bg=CYBER["field"], glow=CYBER["line_soft"], line=CYBER["glow"], depth=1)
        prompt_outer.grid(row=3, column=0, sticky="nsew", pady=8)
        prompt_inner.columnconfigure(0, weight=1)
        prompt_inner.rowconfigure(0, weight=1)
        self.prompt_text = self.make_text_box(prompt_inner, height=7)
        self.prompt_text.grid(row=0, column=0, sticky="nsew")
        actions = ttk.Frame(self.dashboard_tab, style="Panel.TFrame")
        actions.grid(row=4, column=0, sticky="ew")
        ttk.Button(actions, text="Queue Task", style="Accent.TButton", command=self.queue_prompt).pack(side="right")
        ttk.Label(actions, text="Use: open notepad | open C:\\path | run python --version", style="Muted.TLabel").pack(side="left")

    def draw_scanline(self, _event: tk.Event | None = None) -> None:
        self.scanline.delete("all")
        width = self.scanline.winfo_width()
        self.scanline.create_rectangle(0, 0, width, 22, fill=CYBER["panel"], outline="")
        for x in range(0, width, 42):
            self.scanline.create_line(x, 8, min(x + 24, width), 8, fill=CYBER["glow"], width=2)
            self.scanline.create_line(x + 6, 15, min(x + 14, width), 15, fill=CYBER["amber"], width=1)
        self.scanline.create_line(0, 20, width, 20, fill=CYBER["line"], width=1)

    def build_queue(self) -> None:
        self.queue_tab.columnconfigure(0, weight=1)
        self.queue_tab.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self.queue_tab, style="Panel.TFrame")
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.select_all_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(toolbar, text="Select all", variable=self.select_all_var, command=self.toggle_select_all).pack(side="left")
        self.selection_label = ttk.Label(toolbar, text="0 selected", style="Muted.TLabel")
        self.selection_label.pack(side="left", padx=12)
        ttk.Button(toolbar, text="Clear Selected", command=self.clear_selected).pack(side="left")
        ttk.Button(toolbar, text="Refresh", command=self.refresh_all).pack(side="right")
        ttk.Button(toolbar, text="Clear Done", command=self.clear_done).pack(side="right", padx=(0, 12))

        deck_outer, deck_inner = self.make_glow_frame(self.queue_tab, bg=CYBER["panel"], glow=CYBER["glow_soft"], line=CYBER["line"], depth=2)
        deck_outer.grid(row=1, column=0, columnspan=2, sticky="nsew")
        deck_inner.columnconfigure(0, weight=1)
        deck_inner.rowconfigure(0, weight=1)
        deck_inner.grid_propagate(False)

        self.queue_split = tk.PanedWindow(
            deck_inner,
            orient=tk.VERTICAL,
            bg=CYBER["line"],
            bd=0,
            borderwidth=0,
            sashwidth=QUEUE_SPLITTER_HEIGHT,
            sashrelief="flat",
            opaqueresize=True,
            showhandle=False,
        )
        self.queue_split.grid(row=0, column=0, sticky="nsew")
        self.queue_split.bind("<Configure>", self.position_queue_split)

        self.queue_area = tk.Frame(self.queue_split, bg=CYBER["panel"])
        self.queue_area.grid_propagate(False)
        self.queue_area.columnconfigure(0, weight=1)
        self.queue_area.rowconfigure(0, weight=1)

        queue_inner = tk.Frame(self.queue_area, bg=CYBER["field"], highlightthickness=1, highlightbackground=CYBER["line_soft"])
        queue_inner.grid(row=0, column=0, sticky="nsew")
        queue_inner.grid_propagate(False)
        queue_inner.columnconfigure(0, weight=1)
        queue_inner.rowconfigure(0, weight=1)
        self.task_tree = ttk.Treeview(queue_inner, columns=("select", "status", "created", "prompt"), show="headings", height=1)
        tree_y_scroll = AutoScrollbar(queue_inner, orient="vertical", command=self.task_tree.yview)
        tree_x_scroll = AutoScrollbar(queue_inner, orient="horizontal", command=self.task_tree.xview)
        self.task_tree.configure(yscrollcommand=tree_y_scroll.set, xscrollcommand=tree_x_scroll.set)
        self.task_tree.heading("select", text="")
        self.task_tree.heading("status", text="Status")
        self.task_tree.heading("created", text="Created")
        self.task_tree.heading("prompt", text="Prompt")
        self.task_tree.column("select", width=48, stretch=False, anchor="center")
        self.task_tree.column("status", width=90, stretch=False)
        self.task_tree.column("created", width=150, stretch=False)
        self.task_tree.column("prompt", width=760, minwidth=360, stretch=False)
        self.task_tree.grid(row=0, column=0, sticky="nsew")
        tree_y_scroll.grid(row=0, column=1, sticky="ns")
        tree_x_scroll.grid(row=1, column=0, sticky="ew")
        self.task_tree.tag_configure("queued", foreground=CYBER["muted"])
        self.task_tree.tag_configure("running", foreground=CYBER["cyan"])
        self.task_tree.tag_configure("done", foreground=CYBER["green"])
        self.task_tree.tag_configure("failed", foreground=CYBER["fail"])
        self.task_tree.bind("<<TreeviewSelect>>", self.show_selected_task)
        self.task_tree.bind("<Button-1>", self.on_task_tree_click)

        self.detail_area = tk.Frame(self.queue_split, bg=CYBER["panel"])
        self.detail_area.grid_propagate(False)
        self.detail_area.columnconfigure(0, weight=1)
        self.detail_area.rowconfigure(1, weight=1)
        ttk.Label(self.detail_area, text="TASK_DETAIL", style="Panel.TLabel", foreground=CYBER["cyan"]).grid(row=0, column=0, sticky="w", pady=(0, 6))
        detail_inner = tk.Frame(self.detail_area, bg=CYBER["field"], highlightthickness=1, highlightbackground=CYBER["line_soft"])
        detail_inner.grid(row=1, column=0, sticky="nsew")
        detail_inner.grid_propagate(False)
        detail_inner.columnconfigure(0, weight=1)
        detail_inner.rowconfigure(0, weight=1)
        self.task_detail = self.make_text_box(detail_inner, height=1)
        detail_y_scroll = AutoScrollbar(detail_inner, orient="vertical", command=self.task_detail.yview)
        self.task_detail.configure(yscrollcommand=detail_y_scroll.set)
        self.task_detail.grid(row=0, column=0, sticky="nsew")
        detail_y_scroll.grid(row=0, column=1, sticky="ns")

        self.queue_split.add(self.queue_area, minsize=QUEUE_PANE_MIN_HEIGHT, stretch="always")
        self.queue_split.add(self.detail_area, minsize=DETAIL_PANE_MIN_HEIGHT, stretch="always")
        self.queue_split_positioned = False
        self.queue_split.after_idle(self.position_queue_split)

    def position_queue_split(self, _event: tk.Event | None = None) -> None:
        if not hasattr(self, "queue_split"):
            return
        if self.queue_split_positioned:
            return
        height = self.queue_split.winfo_height()
        if height <= 1:
            self.queue_split.after(50, self.position_queue_split)
            return
        try:
            self.queue_split.sash_place(0, 0, queue_split_initial_sash_y(height))
        except tk.TclError:
            self.queue_split.after(50, self.position_queue_split)
            return
        self.queue_split_positioned = True

    def build_logs(self) -> None:
        self.logs_tab.columnconfigure(0, weight=1)
        self.logs_tab.rowconfigure(0, weight=1)
        log_outer, log_inner = self.make_glow_frame(self.logs_tab, bg=CYBER["field"], glow=CYBER["glow_soft"], depth=2)
        log_outer.grid(row=0, column=0, sticky="nsew")
        log_inner.columnconfigure(0, weight=1)
        log_inner.rowconfigure(0, weight=1)
        self.log_text = self.make_text_box(log_inner)
        self.log_text.grid(row=0, column=0, sticky="nsew")

    def build_settings(self) -> None:
        self.settings_tab.columnconfigure(1, weight=1)
        self.model_var = tk.StringVar(value=self.config_data.get("model", ""))
        self.url_var = tk.StringVar(value=self.config_data.get("ollama_url", ""))
        self.ctx_var = tk.StringVar(value=str(self.config_data.get("num_ctx", 4096)))
        self.temp_var = tk.StringVar(value=str(self.config_data.get("temperature", 0.4)))
        self.model_enabled_var = tk.BooleanVar(value=bool(self.config_data.get("model_enabled", False)))
        self.shell_var = tk.BooleanVar(value=bool(self.config_data.get("allow_shell", False)))
        self.language_var = tk.StringVar(value=language_code(self.config_data))
        self.model_status_var = tk.StringVar(value="Model status has not been checked.")

        rows = [
            ("Model", self.model_var),
            ("Ollama URL", self.url_var),
            ("Context", self.ctx_var),
            ("Temperature", self.temp_var),
        ]
        for row, (label, var) in enumerate(rows):
            ttk.Label(self.settings_tab, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=6)
            ttk.Entry(self.settings_tab, textvariable=var).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Label(self.settings_tab, text="Language", style="Panel.TLabel").grid(row=4, column=0, sticky="w", pady=6)
        language_select = ttk.Combobox(
            self.settings_tab,
            textvariable=self.language_var,
            values=list(LANGUAGES.keys()),
            state="readonly",
            width=20,
        )
        language_select.grid(row=4, column=1, sticky="w", pady=6)
        ttk.Label(
            self.settings_tab,
            text="auto: detect command language, fallback English | vi | en | fr | ja | zh",
            style="Muted.TLabel",
        ).grid(row=5, column=1, sticky="w", pady=(0, 6))
        ttk.Checkbutton(self.settings_tab, text="Enable model calls", variable=self.model_enabled_var).grid(row=6, column=1, sticky="w", pady=6)
        ttk.Checkbutton(self.settings_tab, text="Allow shell commands from allowlist", variable=self.shell_var).grid(row=7, column=1, sticky="w", pady=6)
        ttk.Label(self.settings_tab, textvariable=self.model_status_var, style="Muted.TLabel", wraplength=720).grid(row=8, column=1, sticky="ew", pady=8)
        action_row = ttk.Frame(self.settings_tab, style="Panel.TFrame")
        action_row.grid(row=9, column=1, sticky="e", pady=12)
        ttk.Button(action_row, text="Test AI Model", command=self.test_model_status).pack(side="left", padx=(0, 8))
        ttk.Button(action_row, text="Save Settings", style="Accent.TButton", command=self.save_settings).pack(side="left")

    def queue_prompt(self) -> None:
        prompt = self.prompt_text.get("1.0", "end").strip()
        if not prompt:
            return
        task_id = self.store.create(prompt)
        self.prompt_text.delete("1.0", "end")
        self.events.put(f"{now()} queued task #{task_id}")
        self.refresh_all()

    def worker_loop(self) -> None:
        while not self.stop_event.is_set():
            task = self.store.claim()
            if not task:
                self.stop_event.wait(1.0)
                continue
            self.events.put(f"{now()} running task #{task['id']}")
            try:
                config = load_config()
                result = process_prompt(task["prompt"], config, self.memory)
                self.store.update(task["id"], status="done", result=result, error="")
                self.events.put(f"{now()} completed task #{task['id']}")
            except Exception as exc:
                self.store.update(task["id"], status="failed", error=str(exc))
                self.events.put(f"{now()} failed task #{task['id']}: {exc}")

    def refresh_all(self) -> None:
        self.config_data = load_config()
        mode = "Prototype mode" if not self.config_data.get("model_enabled", False) else self.config_data.get("model", "")
        shell = "shell allowlist" if self.config_data.get("allow_shell", False) else "shell locked"
        self.mode_label.configure(text=f"{mode} | {language_label(self.config_data)} | {shell} | {ROOT}")

        tasks = sorted(self.store.read(), key=lambda item: item["id"], reverse=True)
        current_ids = {task["id"] for task in tasks}
        self.selected_task_ids.intersection_update(current_ids)
        counts = Counter(task.get("status", "") for task in tasks)
        self.stats_label.configure(
            text="    ".join(f"{status.title()} {counts[status]}" for status in TASK_STATUSES)
        )

        selected = self.task_tree.selection()
        for item in self.task_tree.get_children():
            self.task_tree.delete(item)
        for task in tasks:
            check = "[x]" if task["id"] in self.selected_task_ids else "[ ]"
            status = str(task.get("status", ""))
            self.task_tree.insert(
                "",
                "end",
                iid=str(task["id"]),
                values=(
                    check,
                    status,
                    task.get("created_at", ""),
                    preview_text(str(task.get("prompt", ""))),
                ),
                tags=(status,),
            )
        if selected:
            for item in selected:
                if self.task_tree.exists(item):
                    self.task_tree.selection_set(item)
                    break
        self.update_selection_state(len(tasks))

    def show_selected_task(self, _event: object | None = None) -> None:
        selected = self.task_tree.selection()
        if not selected:
            return
        task = next((item for item in self.store.read() if str(item["id"]) == selected[0]), None)
        if not task:
            return
        text = (
            f"Task #{task['id']} [{task['status']}]\n"
            f"Created: {task['created_at']}\nUpdated: {task['updated_at']}\n\n"
            f"Prompt:\n{task['prompt']}\n\n"
            f"Result:\n{task.get('result', '')}\n\n"
            f"Error:\n{task.get('error', '')}"
        )
        self.task_detail.delete("1.0", "end")
        self.task_detail.insert("1.0", text)

    def on_task_tree_click(self, event: tk.Event) -> str | None:
        region = self.task_tree.identify("region", event.x, event.y)
        if region != "cell":
            return None
        row_id = self.task_tree.identify_row(event.y)
        column = self.task_tree.identify_column(event.x)
        if not row_id:
            return None
        if column == "#1":
            self.toggle_task_selection(int(row_id))
            return "break"
        return None

    def toggle_task_selection(self, task_id: int) -> None:
        if task_id in self.selected_task_ids:
            self.selected_task_ids.remove(task_id)
        else:
            self.selected_task_ids.add(task_id)
        self.refresh_all()

    def toggle_select_all(self) -> None:
        tasks = self.store.read()
        if self.select_all_var.get():
            self.selected_task_ids = {task["id"] for task in tasks}
        else:
            self.selected_task_ids.clear()
        self.refresh_all()

    def update_selection_state(self, total: int | None = None) -> None:
        if total is None:
            total = len(self.store.read())
        selected = len(self.selected_task_ids)
        self.selection_label.configure(text=f"{selected} selected")
        self.select_all_var.set(total > 0 and selected == total)

    def clear_selected(self) -> None:
        if not self.selected_task_ids:
            return
        count = len(self.selected_task_ids)
        if not messagebox.askyesno("Clear selected tasks", f"Clear {count} selected task(s)?"):
            return
        self.store.clear_ids(set(self.selected_task_ids))
        self.selected_task_ids.clear()
        self.task_detail.delete("1.0", "end")
        self.events.put(f"{now()} cleared {count} selected task(s)")
        self.refresh_all()

    def show_dashboard(self) -> None:
        self.notebook.select(self.dashboard_tab)

    def show_queue(self) -> None:
        self.notebook.select(self.queue_tab)

    def show_logs(self) -> None:
        self.notebook.select(self.logs_tab)

    def show_settings(self) -> None:
        self.notebook.select(self.settings_tab)

    def save_settings(self) -> None:
        config = load_config()
        try:
            num_ctx = int(self.ctx_var.get())
            temperature = float(self.temp_var.get())
        except ValueError:
            messagebox.showerror("Invalid settings", "Context must be an integer and temperature must be a number.")
            return

        config["model"] = self.model_var.get().strip()
        config["ollama_url"] = self.url_var.get().strip()
        config["num_ctx"] = num_ctx
        config["temperature"] = temperature
        config["model_enabled"] = bool(self.model_enabled_var.get())
        config["language"] = self.language_var.get()
        config["allow_shell"] = bool(self.shell_var.get())
        save_config(config)
        self.events.put(f"{now()} saved settings")
        self.refresh_all()

    def test_model_status(self) -> None:
        self.save_settings()
        self.model_status_var.set("Checking Ollama...")

        def run_check() -> None:
            ok, message = check_ollama(load_config())
            prefix = "Ready" if ok else "Not ready"
            self.events.put(f"{now()} model check: {prefix}")
            self.after(0, lambda: self.model_status_var.set(message))

        threading.Thread(target=run_check, daemon=True).start()

    def clear_done(self) -> None:
        self.store.clear_done()
        self.events.put(f"{now()} cleared completed tasks")
        self.refresh_all()

    def tick(self) -> None:
        updated = False
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", event + "\n")
            self.log_text.see("end")
            updated = True
        if updated:
            self.refresh_all()
        self.after(1000, self.tick)

    def on_close(self) -> None:
        self.stop_event.set()
        self.destroy()

def run_legacy_tk_app() -> None:
    app = LocalAgentDesktop()
    app.mainloop()

def run_desktop_shell() -> None:
    sys.modules.setdefault("desktop_app", sys.modules[__name__])

    try:
        import webview
    except ImportError:
        messagebox.showerror(
            "Talos",
            "Desktop WebView runtime is missing.\n\nRun:\npython -m pip install pywebview",
        )
        return

    from http.server import ThreadingHTTPServer

    from web_app import LocalAgentWebHandler, STOP_EVENT, find_port, worker_loop

    host = "127.0.0.1"
    port = find_port(host, 8787)
    threading.Thread(target=worker_loop, daemon=True).start()
    server = ThreadingHTTPServer((host, port), LocalAgentWebHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    window_ref: dict[str, Any] = {"window": None, "maximized": False}

    class WindowApi:
        def minimize(self) -> None:
            window = window_ref["window"]
            if window is not None:
                window.minimize()

        def toggle_maximize(self) -> bool:
            window = window_ref["window"]
            if window is None:
                return False
            state = str(getattr(window, "state", ""))
            if window_ref["maximized"] or "maximized" in state.lower():
                window.restore()
                window_ref["maximized"] = False
            else:
                window.maximize()
                window_ref["maximized"] = True
            return window_ref["maximized"]

        def get_window_state(self) -> dict[str, Any]:
            window = window_ref["window"]
            state = str(getattr(window, "state", "")) if window is not None else ""
            maximized = window_ref["maximized"] or "maximized" in state.lower()
            window_ref["maximized"] = maximized
            return {"maximized": maximized, "state": state}

        def snap_to(self, x: int, y: int, width: int, height: int) -> dict[str, Any]:
            window = window_ref["window"]
            if window is None:
                return {"maximized": False}
            window.restore()
            window_ref["maximized"] = False
            window.move(int(x), int(y))
            window.resize(max(WEBVIEW_MIN_WIDTH, int(width)), max(WEBVIEW_MIN_HEIGHT, int(height)))
            return {"maximized": False}

        def close(self) -> None:
            window = window_ref["window"]
            if window is not None:
                window.destroy()

    def on_closed() -> None:
        STOP_EVENT.set()
        server.shutdown()
        server.server_close()

    window = webview.create_window(
        "Talos",
        f"http://{host}:{port}",
        width=1440,
        height=900,
        min_size=(WEBVIEW_MIN_WIDTH, WEBVIEW_MIN_HEIGHT),
        background_color=CYBER["bg"],
        frameless=True,
        easy_drag=False,
        js_api=WindowApi(),
    )
    window_ref["window"] = window
    window.events.closed += on_closed
    webview.start(debug="--debug-webview" in sys.argv)

if __name__ == "__main__":
    try:
        if "--legacy-tk" in sys.argv:
            run_legacy_tk_app()
        else:
            run_desktop_shell()
    except Exception as exc:
        messagebox.showerror("Talos", str(exc))
