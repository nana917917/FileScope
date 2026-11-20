"""Compact light palette and local document glyphs for Tk."""

import tkinter as tk
from tkinter import ttk


def apply_light(root):
    root.configure(background="#f6f8fb")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background="#f6f8fb", foreground="#203148", font=("Segoe UI", 10))
    style.configure("TButton", background="#ffffff", bordercolor="#cbd6e3", padding=(8, 3))
    style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff", rowheight=26)
    style.configure("Treeview.Heading", background="#edf2f7", padding=(5, 4))
    style.map("Treeview", background=[("selected", "#d2e7ff")], foreground=[("selected", "#102b49")])


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
