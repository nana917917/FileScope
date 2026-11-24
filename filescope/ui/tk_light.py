"""Compact light palette and local document glyphs for Tk."""

import tkinter as tk
from tkinter import ttk

THEMES = {"light": "ライト", "dark": "ダーク", "classic": "クラシック"}


def apply_light(root):
    apply_theme(root, "light")


def apply_theme(root, theme, *, size=10, compact=False):
    root.filescope_light = True
    style = ttk.Style(root)
    style.theme_use("clam")
    if theme == "dark":
        bg, surface, ink, muted, border, select, select_ink = (
            "#242932", "#171c24", "#eaf0f8", "#b3bfce", "#485364", "#265880", "#ffffff")
    elif theme == "classic":
        bg, surface, ink, muted, border, select, select_ink = (
            "#ececec", "#ffffff", "#222222", "#535353", "#aaaaaa", "#d9e2ee", "#222222")
    else:
        bg, surface, ink, muted, border, select, select_ink = (
            "#f5f7fa", "#ffffff", "#172941", "#52647a", "#c9d5e3", "#d5eaff", "#122e4b")
    root.configure(background=bg)
    for klass in ("Text", "Listbox", "Menu"):
        root.option_add(f"*{klass}.background", surface)
        root.option_add(f"*{klass}.foreground", ink)
        root.option_add(f"*{klass}.selectBackground", select)
        root.option_add(f"*{klass}.selectForeground", select_ink)
    style.configure(".", background=bg, foreground=ink, font=("Segoe UI", size),
                    bordercolor=border, fieldbackground=surface, insertcolor=ink,
                    lightcolor=bg, darkcolor=bg, selectbackground=select, selectforeground=select_ink)
    style.configure("TButton", background=surface, padding=(8, 3))
    style.configure("Treeview", background=surface, fieldbackground=surface,
                    rowheight=round((size + (4 if compact else 9)) * float(root.tk.call("tk", "scaling"))))
    style.configure("Treeview.Heading", background=bg, padding=(5, 4))
    style.configure("Muted.TLabel", foreground=muted)
    style.configure("Error.TLabel", foreground="#ffaaaa" if theme == "dark" else "#a13535")
    style.map("Treeview", background=[("selected", select)], foreground=[("selected", select_ink)])
    def recolor(widget):
        if isinstance(widget, (tk.Text, tk.Listbox, tk.Menu)):
            widget.configure(background=surface, foreground=ink)
            if isinstance(widget, tk.Text):
                widget.configure(insertbackground=ink)
        elif isinstance(widget, tk.Canvas):
            widget.configure(background=surface)
        elif isinstance(widget, tk.Toplevel):
            widget.configure(background=bg)
        for child in widget.winfo_children():
            recolor(child)
    recolor(root)


def document_icons(master):
    colors = {"Excel": "#16764d", "CSV": "#207d84", "PDF": "#a62939",
              "Word": "#2457a6", "PPT": "#a54c22", "Text": "#5e6573", "?": "#606870"}
    images = {}
    for kind, color in colors.items():
        image = tk.PhotoImage(master=master, width=14, height=16)
        image.put(color, to=(1, 1, 12, 15))
        image.put("#ffffff", to=(3, 6, 10, 7))
        image.put("#ffffff", to=(3, 9, 9, 10))
        images[kind] = image
    return images
