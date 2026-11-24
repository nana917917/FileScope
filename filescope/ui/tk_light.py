"""Compact light palette and local document glyphs for Tk."""

import tkinter as tk
from tkinter import font, ttk

THEMES = {"light": "ライト", "dark": "ダーク", "classic": "クラシック"}


def apply_light(root):
    apply_theme(root, "light")


def apply_theme(root, theme, *, size=10, compact=False):
    root.filescope_light = True
    available = {name.casefold(): name for name in font.families(root)}
    family = next((available[name] for name in ("meiryo ui", "meiryo", "yu gothic ui", "segoe ui")
                   if name in available), font.nametofont("TkDefaultFont", root=root).actual("family"))
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        font.nametofont(name, root=root).configure(family=family, size=size, weight="normal")
    heading = "FileScopeHeading"
    if heading not in font.names(root):
        root._filescope_heading_font = font.Font(root=root, name=heading)
    font.nametofont(heading, root=root).configure(family=family, size=size, weight="bold")
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
        root.option_add(f"*{klass}.font", "TkTextFont" if klass == "Text" else "TkDefaultFont")
        root.option_add(f"*{klass}.background", surface)
        root.option_add(f"*{klass}.foreground", ink)
        root.option_add(f"*{klass}.selectBackground", select)
        root.option_add(f"*{klass}.selectForeground", select_ink)
    style.configure(".", background=bg, foreground=ink, font="TkDefaultFont",
                    bordercolor=border, fieldbackground=surface, insertcolor=ink,
                    lightcolor=bg, darkcolor=bg, selectbackground=select, selectforeground=select_ink)
    style.configure("TButton", background=surface, padding=(8, 3))
    style.configure("Treeview", background=surface, fieldbackground=surface,
                    rowheight=round((size + (4 if compact else 9)) * float(root.tk.call("tk", "scaling"))))
    style.configure("Treeview.Heading", background=bg, padding=(5, 4))
    style.configure("Section.TLabel", font=heading)
    style.configure("Muted.TLabel", foreground=muted)
    style.configure("Error.TLabel", foreground="#ffaaaa" if theme == "dark" else "#a13535")
    style.map("Treeview", background=[("selected", select)], foreground=[("selected", select_ink)])
    def recolor(widget):
        if isinstance(widget, (tk.Text, tk.Listbox, tk.Menu)):
            widget.configure(background=surface, foreground=ink)
            if isinstance(widget, tk.Text):
                widget.configure(insertbackground=ink)
                if "location" in widget.tag_names():
                    widget.tag_configure("location", foreground="#8dd7b6" if theme == "dark" else "#27664d")
                if "note" in widget.tag_names():
                    widget.tag_configure("note", foreground=muted)
        elif isinstance(widget, tk.Canvas):
            widget.configure(background=surface)
        elif isinstance(widget, tk.Toplevel):
            widget.configure(background=bg)
        for child in widget.winfo_children():
            recolor(child)
    recolor(root)


def document_icons(master):
    glyphs = {
        "Excel": ("#16764d", ("10001", "01010", "00100", "01010", "10001")),
        "Word": ("#2457a6", ("10001", "10001", "10101", "10101", "01010")),
        "PPT": ("#a54c22", ("11110", "10001", "11110", "10000", "10000")),
        "PDF": ("#a62939", ("01100", "01010", "10110", "10001", "01110")),
        "CSV": ("#207d84", ("11111", "10101", "11111", "10101", "11111")),
        "Text": ("#5e6573", ("11111", "00000", "11111", "00000", "11100")),
        "?": ("#606870", ("01110", "00010", "00100", "00000", "00100")),
    }
    images = {}
    for kind, (color, glyph) in glyphs.items():
        image = tk.PhotoImage(master=master, width=16, height=16)
        image.put(color, to=(1, 1, 13, 15))
        if kind == "PDF":
            for y in range(1, 4):
                for x in range(9 + y, 13):
                    image.transparency_set(x, y, True)
        for y, line in enumerate(glyph, start=5):
            for x, dot in enumerate(line, start=4):
                if dot == "1":
                    image.put("#ffffff", (x, y))
        images[kind] = image
    return images
