VERSION = "1.0.0"

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import time
import json
import random
import os
import sys
import tempfile

try:
    import pyautogui
    import cv2
    import numpy as np
    from PIL import ImageGrab, Image, ImageTk
    import keyboard
    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = False
except ImportError as e:
    print(f"필수 라이브러리 없음: {e}")

# ────────── 설정 ──────────
DEFAULT_START_KEY = "f6"
DEFAULT_RECORD_KEY = "f3"
DEFAULT_CONFIDENCE = 0.8
SCAN_INTERVAL = 0.1  # 초


# ────────── 영역 선택 오버레이 ──────────
class RegionSelector:
    """드래그로 화면 영역을 선택하는 오버레이.
    mode="region" : 좌표 튜플 반환
    mode="capture": 해당 영역을 캡처해 저장 경로 반환
    """
    def __init__(self, callback, mode="region", save_dir=None):
        self.callback = callback
        self.mode = mode
        self.save_dir = save_dir or tempfile.gettempdir()
        self.start_x = self.start_y = 0
        self.rect = None

        # 먼저 화면 전체 스크린샷 찍기 (pyautogui가 더 안정적)
        self._screenshot = pyautogui.screenshot()
        sw, sh = self._screenshot.size

        self.root = tk.Toplevel()
        self.root.overrideredirect(True)
        self.root.geometry(f"{sw}x{sh}+0+0")
        self.root.attributes("-topmost", True)
        self.root.lift()
        self.root.focus_force()

        self.canvas = tk.Canvas(self.root, cursor="cross", highlightthickness=0,
                                 width=sw, height=sh)
        self.canvas.pack(fill="both", expand=True)

        # 스크린샷을 배경으로 표시 (반투명 효과)
        self._bg_img = ImageTk.PhotoImage(self._screenshot.convert("RGBA")
                                           .point(lambda p: int(p * 0.5)))
        self.canvas.create_image(0, 0, anchor="nw", image=self._bg_img)

        hint = "드래그하여 캡처 영역 선택" if mode == "capture" else "드래그하여 감지 영역 선택"
        self.canvas.create_text(sw // 2, 20, text=f"{hint}  (ESC: 취소)",
                                 fill="white", font=("Arial", 14, "bold"))

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.canvas.bind("<Escape>", lambda e: self.root.destroy())
        self.canvas.focus_set()
        self.root.grab_set()

    def _on_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        if self.rect:
            self.canvas.delete(self.rect)

    def _on_drag(self, event):
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, event.x, event.y,
            outline="red", width=2, fill="red", stipple="gray25"
        )

    def _on_release(self, event):
        x1 = min(self.start_x, event.x)
        y1 = min(self.start_y, event.y)
        x2 = max(self.start_x, event.x)
        y2 = max(self.start_y, event.y)
        self.root.destroy()

        if x2 - x1 < 5 or y2 - y1 < 5:
            return

        if self.mode == "capture":
            self._do_capture(x1, y1, x2, y2)
        else:
            self.callback((x1, y1, x2, y2))

    def _do_capture(self, x1, y1, x2, y2):
        img = self._screenshot.crop((x1, y1, x2, y2))
        path = os.path.join(self.save_dir, f"capture_{int(time.time()*1000)}.png")
        img.save(path)
        self.callback(path)


# ────────── 박스 영역 선택 오버레이 ──────────
class BoxRegionSelector:
    """고정 비율 박스를 이동/리사이즈하여 감지 영역을 설정하는 오버레이.
    - 박스 내부 드래그 : 이동
    - 테두리/모서리 핸들 드래그 : 비율 고정 리사이즈
    - 마우스 스크롤 : 비율 고정 크기 조절
    - Enter / 더블클릭 : 확정  /  ESC : 취소
    """
    RATIO_W = 1476
    RATIO_H = 777
    HANDLE_R = 7    # 핸들 히트 반경(px)
    MIN_W    = 200

    def __init__(self, callback):
        self.callback = callback
        self.ratio = self.RATIO_W / self.RATIO_H

        self._screenshot = pyautogui.screenshot()
        sw, sh = self._screenshot.size
        self._sw, self._sh = sw, sh

        # 초기 박스 크기 (화면에 맞게 축소)
        bw = min(self.RATIO_W, sw - 80)
        bh = round(bw / self.ratio)
        if bh > sh - 80:
            bh = sh - 80
            bw = round(bh * self.ratio)
        bx = (sw - bw) // 2
        by = (sh - bh) // 2
        self.box = [bx, by, bx + bw, by + bh]

        self._drag_mode      = None
        self._drag_start_xy  = None
        self._drag_start_box = None

        self.root = tk.Toplevel()
        self.root.overrideredirect(True)
        self.root.geometry(f"{sw}x{sh}+0+0")
        self.root.attributes("-topmost", True)
        self.root.lift()
        self.root.focus_force()

        self.canvas = tk.Canvas(self.root, highlightthickness=0, width=sw, height=sh)
        self.canvas.pack(fill="both", expand=True)

        # 어두운 배경 (정적)
        bg = self._screenshot.convert("RGBA").point(lambda p: int(p * 0.45))
        self._bg_img = ImageTk.PhotoImage(bg)
        self.canvas.create_image(0, 0, anchor="nw", image=self._bg_img)

        self.canvas.bind("<ButtonPress-1>",   self._on_press)
        self.canvas.bind("<B1-Motion>",       self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_confirm)
        self.canvas.bind("<Motion>",          self._on_hover)
        self.canvas.bind("<MouseWheel>",      self._on_scroll)   # Windows
        self.canvas.bind("<Button-4>",        self._on_scroll)   # Linux scroll up
        self.canvas.bind("<Button-5>",        self._on_scroll)   # Linux scroll down
        self.root.bind("<Return>", self._on_confirm)
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.canvas.focus_set()
        self.root.grab_set()

        self._draw()

    # ── 핸들 좌표 ──
    def _handles(self):
        x1, y1, x2, y2 = [int(v) for v in self.box]
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        return {
            "TL": (x1, y1), "TC": (cx, y1), "TR": (x2, y1),
            "ML": (x1, cy),                  "MR": (x2, cy),
            "BL": (x1, y2), "BC": (cx, y2), "BR": (x2, y2),
        }

    def _hit(self, x, y):
        for name, (hx, hy) in self._handles().items():
            if abs(x - hx) <= self.HANDLE_R + 2 and abs(y - hy) <= self.HANDLE_R + 2:
                return name
        x1, y1, x2, y2 = self.box
        if x1 < x < x2 and y1 < y < y2:
            return "move"
        return None

    def _cursor_for(self, hit):
        return {
            "TL": "size_nw_se", "BR": "size_nw_se",
            "TR": "size_ne_sw", "BL": "size_ne_sw",
            "TC": "size_ns",    "BC": "size_ns",
            "ML": "size_we",    "MR": "size_we",
            "move": "fleur",
        }.get(hit, "arrow")

    # ── 이벤트 ──
    def _on_hover(self, event):
        hit = self._hit(event.x, event.y)
        self.canvas.config(cursor=self._cursor_for(hit) if hit else "arrow")

    def _on_press(self, event):
        hit = self._hit(event.x, event.y)
        if hit:
            self._drag_mode      = hit
            self._drag_start_xy  = (event.x, event.y)
            self._drag_start_box = list(self.box)

    def _on_drag(self, event):
        if not self._drag_mode:
            return
        dx = event.x - self._drag_start_xy[0]
        dy = event.y - self._drag_start_xy[1]
        x1, y1, x2, y2 = self._drag_start_box

        if self._drag_mode == "move":
            w, h = x2 - x1, y2 - y1
            nx = max(0, min(self._sw - w, x1 + dx))
            ny = max(0, min(self._sh - h, y1 + dy))
            self.box = [nx, ny, nx + w, ny + h]
        else:
            self._resize(self._drag_mode, dx, dy, x1, y1, x2, y2)
        self._draw()

    def _resize(self, handle, dx, dy, x1, y1, x2, y2):
        MIN_H = round(self.MIN_W / self.ratio)
        w0, h0 = x2 - x1, y2 - y1

        # 새 크기 결정
        if handle in ("BR", "MR", "TR"):
            nw = max(self.MIN_W, w0 + dx)
        elif handle in ("BL", "ML", "TL"):
            nw = max(self.MIN_W, w0 - dx)
        elif handle == "BC":
            nh = max(MIN_H, h0 + dy); nw = round(nh * self.ratio)
        elif handle == "TC":
            nh = max(MIN_H, h0 - dy); nw = round(nh * self.ratio)
        else:
            nw = w0
        nh = round(nw / self.ratio)

        # 앵커 적용
        if   handle == "TL": self.box = [x2-nw, y2-nh, x2,       y2      ]
        elif handle == "TR": self.box = [x1,    y2-nh, x1+nw,    y2      ]
        elif handle == "BL": self.box = [x2-nw, y1,    x2,       y1+nh   ]
        elif handle == "BR": self.box = [x1,    y1,    x1+nw,    y1+nh   ]
        elif handle == "ML":
            cy = (y1+y2)//2; self.box = [x2-nw, cy-nh//2, x2,    cy+nh//2]
        elif handle == "MR":
            cy = (y1+y2)//2; self.box = [x1,    cy-nh//2, x1+nw, cy+nh//2]
        elif handle == "TC":
            cx = (x1+x2)//2; self.box = [cx-nw//2, y2-nh, cx+nw//2, y2  ]
        elif handle == "BC":
            cx = (x1+x2)//2; self.box = [cx-nw//2, y1,    cx+nw//2, y1+nh]

    def _on_scroll(self, event):
        delta = getattr(event, "delta", 0)
        if delta == 0:
            delta = 120 if event.num == 4 else -120
        factor = 1.05 if delta > 0 else 0.95
        x1, y1, x2, y2 = self.box
        cx, cy = (x1+x2)/2, (y1+y2)/2
        nw = max(self.MIN_W, (x2-x1) * factor)
        nh = round(nw / self.ratio)
        self.box = [cx-nw/2, cy-nh/2, cx+nw/2, cy+nh/2]
        self._draw()

    def _on_release(self, event):
        self._drag_mode = None

    def _on_confirm(self, event=None):
        region = tuple(int(v) for v in self.box)
        self.root.destroy()
        self.callback(region)

    # ── 그리기 ──
    def _draw(self):
        self.canvas.delete("dyn")
        x1, y1, x2, y2 = [int(v) for v in self.box]

        # 박스 안: 원본 이미지 표시
        crop = self._screenshot.crop((
            max(0, x1), max(0, y1),
            min(self._sw, x2), min(self._sh, y2)
        ))
        self._crop_tk = ImageTk.PhotoImage(crop)
        self.canvas.create_image(x1, y1, anchor="nw", image=self._crop_tk, tags="dyn")

        # 박스 테두리
        self.canvas.create_rectangle(x1, y1, x2, y2,
                                      outline="#00FF88", width=2, tags="dyn")

        # 핸들 (8방향)
        r = self.HANDLE_R
        for hx, hy in self._handles().values():
            self.canvas.create_rectangle(hx-r, hy-r, hx+r, hy+r,
                                          fill="#00FF88", outline="white",
                                          width=1, tags="dyn")

        # 크기 표시
        w, h = x2-x1, y2-y1
        self.canvas.create_text((x1+x2)//2, y1+18,
                                 text=f"{w} × {h}",
                                 fill="white", font=("Arial", 11, "bold"),
                                 tags="dyn")

        # 하단 안내
        self.canvas.create_text(
            self._sw//2, self._sh - 22,
            text="박스 내부 드래그: 이동  │  핸들 드래그 / 스크롤: 크기 조절  │  Enter / 더블클릭: 확인  │  ESC: 취소",
            fill="white", font=("Arial", 11), tags="dyn"
        )


# ────────── 메인 앱 ──────────
class CheDetect:
    def __init__(self, root):
        self.root = root
        self.root.title(f"cheDetect v{VERSION}")
        self.root.resizable(False, False)

        self.records = []   # {"type": "image"|"click", ...}
        self.region = None  # (x1, y1, x2, y2)
        self.running = False
        self.macro_thread = None
        self.start_key = DEFAULT_START_KEY
        self.record_key = DEFAULT_RECORD_KEY
        self._live_preview_running = False
        self._live_preview_thread = None
        self._region_preview_img = None

        self._build_ui()
        self._register_hotkeys()

    def _build_ui(self):
        # ── 좌우 분할 ──
        frame_main = tk.Frame(self.root)
        frame_main.pack(fill="both", expand=True)

        frame_left = tk.Frame(frame_main)
        frame_left.pack(side="left", fill="y", padx=(0, 0))

        frame_right = tk.LabelFrame(frame_main, text="영역 미리보기", bg="#2b2b2b", width=600)
        frame_right.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=8)
        frame_right.pack_propagate(False)

        self.region_preview = tk.Label(frame_right, text="영역 미설정", bg="#2b2b2b",
                                        fg="#888888", font=("Arial", 10))
        self.region_preview.pack(fill="both", expand=True, padx=4, pady=4)

        # ── 감지 영역 ──
        frame_region = tk.LabelFrame(frame_left, text="감지 영역")
        frame_region.pack(padx=10, pady=(10, 5), fill="x")

        self.region_var = tk.StringVar(value="설정 안됨")
        tk.Label(frame_region, textvariable=self.region_var, width=28).pack(side="left", padx=5, pady=3)
        tk.Button(frame_region, text="영역 선택", command=self._select_region).pack(side="left", padx=5)
        tk.Button(frame_region, text="전체 화면", command=self._set_fullscreen).pack(side="left", padx=5)

        # ── 레코드 테이블 ──
        frame_table = tk.LabelFrame(frame_left, text="레코드")
        frame_table.pack(padx=10, pady=5, fill="both")

        columns = ("#", "이름", "내용", "YES→", "NO→", "정확도")
        self.tree = ttk.Treeview(frame_table, columns=columns, show="headings",
                                  height=8, selectmode="browse")
        widths = [30, 80, 160, 60, 60, 60]
        for col, w in zip(columns, widths):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, anchor="center")

        scrollbar = ttk.Scrollbar(frame_table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both")
        scrollbar.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self._on_double_click)

        # ── 레코드 버튼 ──
        frame_rec_btns = tk.Frame(frame_left)
        frame_rec_btns.pack(padx=10, pady=3)

        tk.Button(frame_rec_btns, text="+ 추가", width=8, command=self._add_record).pack(side="left", padx=2)
        tk.Button(frame_rec_btns, text="✎ 편집", width=8, command=self._edit_record).pack(side="left", padx=2)
        tk.Button(frame_rec_btns, text="삭제", width=8, command=self._delete_record).pack(side="left", padx=2)
        tk.Button(frame_rec_btns, text="▲ 위로", width=8, command=self._move_up).pack(side="left", padx=2)
        tk.Button(frame_rec_btns, text="▼ 아래로", width=8, command=self._move_down).pack(side="left", padx=2)

        # ── 단축키 설정 ──
        frame_keys = tk.LabelFrame(frame_left, text="단축키 설정")
        frame_keys.pack(padx=10, pady=5, fill="x")

        tk.Label(frame_keys, text="시작/종료:").grid(row=0, column=0, padx=5, pady=3)
        self.start_key_var = tk.StringVar(value=self.start_key.upper())
        tk.Entry(frame_keys, textvariable=self.start_key_var, width=8).grid(row=0, column=1, padx=5)

        tk.Label(frame_keys, text="클릭 기록:").grid(row=0, column=2, padx=5, pady=3)
        self.record_key_var = tk.StringVar(value=self.record_key.upper())
        tk.Entry(frame_keys, textvariable=self.record_key_var, width=8).grid(row=0, column=3, padx=5)

        tk.Button(frame_keys, text="적용", command=self._apply_key).grid(row=0, column=4, padx=5)

        # ── 상태 ──
        self.status_var = tk.StringVar(value="⏹ 대기 중")
        tk.Label(frame_left, textvariable=self.status_var, font=("Arial", 11, "bold")).pack(pady=3)

        # ── 저장/불러오기/시작 ──
        frame_btns = tk.Frame(frame_left)
        frame_btns.pack(padx=10, pady=(0, 10))

        tk.Button(frame_btns, text="💾 저장", width=10, command=self._save).pack(side="left", padx=3)
        tk.Button(frame_btns, text="📂 불러오기", width=10, command=self._load).pack(side="left", padx=3)
        self.toggle_btn = tk.Button(frame_btns, text="▶ 시작", width=10,
                                     bg="#4CAF50", fg="white", command=self._toggle)
        self.toggle_btn.pack(side="left", padx=3)

    # ── 영역 선택 ──
    def _select_region(self):
        self.root.iconify()
        time.sleep(0.3)
        BoxRegionSelector(self._on_region_selected)

    def _on_region_selected(self, region):
        self.region = region
        self.region_var.set(f"({region[0]}, {region[1]}) ~ ({region[2]}, {region[3]})")
        self.root.deiconify()
        self._start_live_preview()

    def _set_fullscreen(self):
        w, h = pyautogui.size()
        self.region = (0, 0, w, h)
        self.region_var.set(f"전체 화면 ({w}x{h})")
        self._start_live_preview()

    def _start_live_preview(self):
        self._live_preview_running = False
        if self._live_preview_thread and self._live_preview_thread.is_alive():
            self._live_preview_thread.join(timeout=0.5)
        self._live_preview_running = True
        self._live_preview_thread = threading.Thread(
            target=self._live_preview_loop, daemon=True)
        self._live_preview_thread.start()

    def _live_preview_loop(self):
        while self._live_preview_running and self.region:
            try:
                img = ImageGrab.grab(bbox=self.region)
                self.root.after(0, self._apply_preview_frame, img)
            except Exception:
                pass
            time.sleep(0.1)  # 10fps

    def _apply_preview_frame(self, img):
        if not self._live_preview_running:
            return
        try:
            pw = self.region_preview.winfo_width() or 580
            ph = self.region_preview.winfo_height() or 400
            if pw < 10:
                pw, ph = 580, 400
            img.thumbnail((pw, ph), Image.LANCZOS)
            self._region_preview_img = ImageTk.PhotoImage(img)
            self.region_preview.config(image=self._region_preview_img, text="")
        except Exception:
            pass

    # ── 레코드 관리 ──
    def _refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.records):
            rtype = r.get("type", "image")
            if rtype == "click":
                content = f"영역내 x:{r.get('click_x',0)}  y:{r.get('click_y',0)}"
                conf_label = "-"
            elif rtype == "color":
                rgb = r.get("color_rgb", [0, 0, 0])
                content = f"색상 RGB({rgb[0]},{rgb[1]},{rgb[2]})"
                conf_label = f"±{r.get('color_tolerance', 20)}"
            else:
                content = os.path.basename(r.get("image_path", "")) or "없음"
                conf_label = f"{int(r.get('confidence', DEFAULT_CONFIDENCE) * 100)}%"
            yes_label = str(r["yes_to"] + 1) if r["yes_to"] is not None else "종료"
            no_label = str(r["no_to"] + 1) if r["no_to"] is not None else "종료"
            self.tree.insert("", "end", values=(i + 1, r["name"], content, yes_label, no_label, conf_label))

    def _add_record(self):
        RecordDialog(self.root, self.records, None, self._refresh_table)

    def _add_click_record(self):
        if not self.region:
            self.root.after(0, lambda: messagebox.showwarning(
                "경고", "감지 영역을 먼저 설정하세요.\n(클릭 좌표는 영역 내 상대 좌표로 저장됩니다.)"))
            return
        ax, ay = pyautogui.position()
        rx, ry = ax - self.region[0], ay - self.region[1]
        count = len(self.records)
        record = {
            "type": "click",
            "name": "클릭",
            "click_x": rx,
            "click_y": ry,
            "yes_to": 0 if count > 0 else None,
            "no_to": None,
        }
        self.records.append(record)
        self.root.after(0, self._refresh_table)

    def _edit_record(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("선택 없음", "편집할 레코드를 선택하세요.")
            return
        idx = self.tree.index(selected[0])
        if self.records[idx].get("type") == "click":
            ClickRecordDialog(self.root, self.records, idx, self._refresh_table)
        else:
            RecordDialog(self.root, self.records, idx, self._refresh_table)

    def _delete_record(self):
        selected = self.tree.selection()
        if not selected:
            return
        idx = self.tree.index(selected[0])
        if messagebox.askyesno("삭제", f"레코드 {idx + 1}을 삭제할까요?"):
            self.records.pop(idx)
            self._refresh_table()

    def _move_up(self):
        selected = self.tree.selection()
        if not selected:
            return
        idx = self.tree.index(selected[0])
        if idx > 0:
            self.records[idx], self.records[idx - 1] = self.records[idx - 1], self.records[idx]
            self._refresh_table()
            self.tree.selection_set(self.tree.get_children()[idx - 1])

    def _move_down(self):
        selected = self.tree.selection()
        if not selected:
            return
        idx = self.tree.index(selected[0])
        if idx < len(self.records) - 1:
            self.records[idx], self.records[idx + 1] = self.records[idx + 1], self.records[idx]
            self._refresh_table()
            self.tree.selection_set(self.tree.get_children()[idx + 1])

    def _on_double_click(self, event):
        self._edit_record()

    # ── 단축키 ──
    def _register_hotkeys(self):
        try:
            keyboard.unhook_all()
            keyboard.add_hotkey(self.start_key, self._toggle)
            keyboard.add_hotkey(self.record_key, self._add_click_record)
        except Exception as e:
            print(f"단축키 등록 실패: {e}")

    def _apply_key(self):
        self.start_key = self.start_key_var.get().lower()
        self.record_key = self.record_key_var.get().lower()
        self._register_hotkeys()
        messagebox.showinfo("단축키", f"시작/종료: {self.start_key.upper()}  클릭기록: {self.record_key.upper()}")

    # ── 저장 / 불러오기 ──
    def _save(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON 파일", "*.json")],
            title="저장"
        )
        if path:
            data = {
                "start_key": self.start_key,
                "record_key": self.record_key,
                "region": self.region,
                "records": self.records
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            messagebox.showinfo("저장", "저장되었습니다.")

    def _load(self):
        path = filedialog.askopenfilename(
            filetypes=[("JSON 파일", "*.json")],
            title="불러오기"
        )
        if path:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.records = data.get("records", [])
            self.region = data.get("region")
            self.start_key = data.get("start_key", DEFAULT_START_KEY)
            self.record_key = data.get("record_key", DEFAULT_RECORD_KEY)
            self.start_key_var.set(self.start_key.upper())
            self.record_key_var.set(self.record_key.upper())
            if self.region:
                r = self.region
                self.region_var.set(f"({r[0]}, {r[1]}) ~ ({r[2]}, {r[3]})")
            self._register_hotkeys()
            self._refresh_table()
            messagebox.showinfo("불러오기", "불러왔습니다.")

    # ── 토글 ──
    def _toggle(self):
        if self.running:
            self._stop()
        else:
            self._start()

    def _start(self):
        if not self.records:
            messagebox.showwarning("경고", "레코드가 없습니다.")
            return
        if not self.region:
            messagebox.showwarning("경고", "감지 영역을 설정하세요.")
            return
        self.running = True
        self.status_var.set("▶ 실행 중...")
        self.toggle_btn.config(text="⏹ 종료", bg="#f44336")
        self.macro_thread = threading.Thread(target=self._run_macro, daemon=True)
        self.macro_thread.start()

    def _stop(self):
        self.running = False
        self.status_var.set("⏹ 대기 중")
        self.toggle_btn.config(text="▶ 시작", bg="#4CAF50")

    # ── 매크로 실행 ──
    def _run_macro(self):
        current_idx = 0
        while self.running:
            if current_idx >= len(self.records) or current_idx < 0:
                self._stop_from_thread()
                break

            record = self.records[current_idx]
            rtype = record.get("type", "image")

            if rtype == "click":
                rx, ry = record["click_x"], record["click_y"]
                ax = rx + (self.region[0] if self.region else 0)
                ay = ry + (self.region[1] if self.region else 0)
                pyautogui.click(ax, ay)
                self.root.after(0, lambda r=record, i=current_idx, ax=ax, ay=ay:
                                self.status_var.set(f"▶ [{i+1}] {r['name']} - 클릭 ({ax},{ay})"))
                next_idx = record["yes_to"]
            elif rtype == "color":
                pos = self._find_color(record)
                wait_type = record.get("wait_type", "none")
                if pos:
                    pyautogui.click(pos[0], pos[1])
                    self.root.after(0, lambda r=record, i=current_idx:
                                    self.status_var.set(f"▶ [{i+1}] {r['name']} - 색상 YES 클릭"))
                    next_idx = record["yes_to"]
                    if wait_type == "single":
                        time.sleep(record.get("wait_single", 0))
                    elif wait_type == "random":
                        time.sleep(random.uniform(record.get("wait_min", 0), record.get("wait_max", 0)))
                else:
                    self.root.after(0, lambda r=record, i=current_idx:
                                    self.status_var.set(f"▶ [{i+1}] {r['name']} - 색상 NO"))
                    next_idx = record["no_to"]
            else:
                wait_type = record.get("wait_type", "none")
                img_path = record.get("image_path", "")
                # 이미지 없이 대기만 하는 경우 (단일/랜덤이고 이미지 미설정)
                if wait_type in ("single", "random") and (not img_path or not os.path.exists(img_path)):
                    self.root.after(0, lambda r=record, i=current_idx:
                                    self.status_var.set(f"▶ [{i+1}] {r['name']} - 대기"))
                    if wait_type == "single":
                        time.sleep(record.get("wait_single", 0))
                    else:
                        time.sleep(random.uniform(record.get("wait_min", 0), record.get("wait_max", 0)))
                    next_idx = record["yes_to"]
                else:
                    found = self._find_image(record)
                    if found:
                        cx = (found[0] + found[2]) // 2
                        cy = (found[1] + found[3]) // 2
                        pyautogui.click(cx, cy)
                        self.root.after(0, lambda r=record, i=current_idx:
                                        self.status_var.set(f"▶ [{i+1}] {r['name']} - YES 클릭"))
                        next_idx = record["yes_to"]
                        if wait_type == "single":
                            time.sleep(record.get("wait_single", 0))
                        elif wait_type == "random":
                            time.sleep(random.uniform(record.get("wait_min", 0), record.get("wait_max", 0)))
                    else:
                        self.root.after(0, lambda r=record, i=current_idx:
                                        self.status_var.set(f"▶ [{i+1}] {r['name']} - NO"))
                        next_idx = record["no_to"]

            if next_idx is None:
                self._stop_from_thread()
                break

            current_idx = next_idx
            time.sleep(SCAN_INTERVAL)

    def _find_image(self, record):
        """이미지를 화면에서 찾아 (x1,y1,x2,y2) 반환, 없으면 None"""
        if not record["image_path"] or not os.path.exists(record["image_path"]):
            return None
        try:
            x1, y1, x2, y2 = self.region
            screenshot = ImageGrab.grab(bbox=(x1, y1, x2, y2))
            screen = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

            template = cv2.imread(record["image_path"])
            if template is None:
                return None

            result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val >= record["confidence"]:
                th, tw = template.shape[:2]
                mx, my = max_loc
                # 영역 좌표로 변환
                return (x1 + mx, y1 + my, x1 + mx + tw, y1 + my + th)
        except Exception as e:
            print(f"이미지 감지 오류: {e}")
        return None

    def _find_color(self, record):
        """색상을 color_region에서 찾아 클릭 좌표 반환, 없으면 None"""
        region = record.get("color_region")
        if not region:
            return None
        try:
            x1, y1, x2, y2 = region
            screenshot = ImageGrab.grab(bbox=(x1, y1, x2, y2))
            img = np.array(screenshot)
            r, g, b = record["color_rgb"]
            tol = record.get("color_tolerance", 20)
            mask = (
                (np.abs(img[:, :, 0].astype(int) - r) <= tol) &
                (np.abs(img[:, :, 1].astype(int) - g) <= tol) &
                (np.abs(img[:, :, 2].astype(int) - b) <= tol)
            )
            positions = np.argwhere(mask)
            if len(positions) > 0:
                cy_arr, cx_arr = positions[:, 0], positions[:, 1]
                cx_mean = int(cx_arr.mean()) + x1
                cy_mean = int(cy_arr.mean()) + y1
                return (cx_mean, cy_mean)
        except Exception as e:
            print(f"색상 감지 오류: {e}")
        return None

    def _stop_from_thread(self):
        self.running = False
        self.root.after(0, lambda: self.status_var.set("⏹ 완료"))
        self.root.after(0, lambda: self.toggle_btn.config(text="▶ 시작", bg="#4CAF50"))

    def on_close(self):
        self.running = False
        self._live_preview_running = False
        try:
            keyboard.unhook_all()
        except:
            pass
        self.root.destroy()


# ────────── 레코드 편집 다이얼로그 ──────────
class RecordDialog:
    def __init__(self, parent, records, edit_idx, refresh_callback):
        self.records = records
        self.edit_idx = edit_idx
        self.refresh_callback = refresh_callback
        self.count = len(records)
        self._preview_img = None

        self.win = tk.Toplevel(parent)
        self.win.title("레코드 추가" if edit_idx is None else "레코드 편집")
        self.win.grab_set()
        self.win.resizable(True, True)

        if edit_idx is not None:
            r = records[edit_idx]
        else:
            r = {"type": "image", "name": "", "image_path": "", "yes_to": 0,
                 "no_to": None, "confidence": DEFAULT_CONFIDENCE,
                 "wait_type": "none", "wait_single": 1.0, "wait_min": 0.0, "wait_max": 1.0}

        pad = {"padx": 8, "pady": 4}

        # ── 좌우 분할 ──
        frame_left = tk.Frame(self.win)
        frame_left.pack(side="left", fill="y", padx=(0, 0))

        frame_right = tk.Frame(self.win, bg="#2b2b2b", width=300)
        frame_right.pack(side="left", fill="both", expand=True)
        frame_right.pack_propagate(False)

        # ── 왼쪽: 폼 ──
        # Row 0: 이름
        tk.Label(frame_left, text="이름:").grid(row=0, column=0, sticky="e", **pad)
        self.name_var = tk.StringVar(value=r["name"])
        tk.Entry(frame_left, textvariable=self.name_var, width=22).grid(row=0, column=1, columnspan=3, **pad)

        # Row 1: 대기 라디오버튼
        tk.Label(frame_left, text="대기:").grid(row=1, column=0, sticky="e", **pad)
        wait_radio_frame = tk.Frame(frame_left)
        wait_radio_frame.grid(row=1, column=1, columnspan=3, sticky="w", padx=8, pady=4)
        self.wait_type_var = tk.StringVar(value=r.get("wait_type", "none"))
        tk.Radiobutton(wait_radio_frame, text="없음", variable=self.wait_type_var, value="none",
                       command=self._on_wait_type_change).pack(side="left")
        tk.Radiobutton(wait_radio_frame, text="단일", variable=self.wait_type_var, value="single",
                       command=self._on_wait_type_change).pack(side="left", padx=(8, 0))
        tk.Radiobutton(wait_radio_frame, text="랜덤", variable=self.wait_type_var, value="random",
                       command=self._on_wait_type_change).pack(side="left", padx=(8, 0))

        # Row 2: 대기 상세 설정 (동적 표시)
        self.wait_detail_frame = tk.Frame(frame_left)
        self.wait_detail_frame.grid(row=2, column=0, columnspan=4, sticky="w", padx=8)

        # 단일 대기 설정 서브프레임
        self.single_frame = tk.Frame(self.wait_detail_frame)
        tk.Label(self.single_frame, text="대기(초) 설정 :").pack(side="left")
        self.wait_single_var = tk.StringVar(value=str(r.get("wait_single", 1.0)))
        tk.Entry(self.single_frame, textvariable=self.wait_single_var, width=8).pack(side="left", padx=4)
        tk.Label(self.single_frame, text="(초)").pack(side="left")

        # 랜덤 대기 설정 서브프레임
        self.random_frame = tk.Frame(self.wait_detail_frame)
        tk.Label(self.random_frame, text="대기(초) 설정 :").pack(side="left")
        self.wait_min_var = tk.StringVar(value=str(r.get("wait_min", 0.0)))
        tk.Entry(self.random_frame, textvariable=self.wait_min_var, width=7).pack(side="left", padx=4)
        tk.Label(self.random_frame, text="(0.00초)  ~").pack(side="left")
        self.wait_max_var = tk.StringVar(value=str(r.get("wait_max", 1.0)))
        tk.Entry(self.random_frame, textvariable=self.wait_max_var, width=7).pack(side="left", padx=4)
        tk.Label(self.random_frame, text="(0.00초)").pack(side="left")

        # Row 3: 이미지/색상 감지 컨테이너
        self._detect_frame = tk.Frame(frame_left)
        self._detect_frame.grid(row=3, column=0, columnspan=4, sticky="w")

        # 이미지 모드 서브프레임
        self._img_row = tk.Frame(self._detect_frame)
        tk.Label(self._img_row, text="이미지:", width=9, anchor="e").pack(side="left", padx=(8, 2))
        self.img_var = tk.StringVar(value=r.get("image_path", ""))
        tk.Entry(self._img_row, textvariable=self.img_var, width=16).pack(side="left")
        tk.Button(self._img_row, text="파일", width=5, command=self._browse_image).pack(side="left", padx=4)
        tk.Button(self._img_row, text="캡처", width=5, command=self._capture_color).pack(side="left")

        # 색상 모드 서브프레임
        self._color_row = tk.Frame(self._detect_frame)
        tk.Label(self._color_row, text="색상:", width=9, anchor="e").pack(side="left", padx=(8, 2))
        self._color_swatch = tk.Label(self._color_row, text="   ", bg="#cccccc", relief="sunken", width=3)
        self._color_swatch.pack(side="left")
        self._color_info_label = tk.Label(self._color_row, text="")
        self._color_info_label.pack(side="left", padx=6)
        tk.Button(self._color_row, text="재선택", width=6, command=self._capture_color).pack(side="left", padx=4)
        tk.Button(self._color_row, text="초기화", width=5, command=self._clear_color).pack(side="left")

        # 색상 데이터 초기화
        self.color_data = None
        if r.get("type") == "color":
            self.color_data = {
                "region": r.get("color_region"),
                "rgb": r.get("color_rgb"),
                "tolerance": r.get("color_tolerance", 20),
            }

        # Row 4: YES → 이동
        tk.Label(frame_left, text="YES → 레코드:").grid(row=4, column=0, sticky="e", **pad)
        self._yes_options_all = [str(i + 1) for i in range(self.count)] + ["종료"]
        self._yes_options_no_exit = [str(i + 1) for i in range(self.count)] or ["종료"]
        self.yes_var = tk.StringVar()
        self.yes_var.set(str(r["yes_to"] + 1) if r["yes_to"] is not None and r["yes_to"] < self.count else "종료")
        self.yes_combo = ttk.Combobox(frame_left, textvariable=self.yes_var, width=8, state="readonly")
        self.yes_combo.grid(row=4, column=1, sticky="w", **pad)

        # Row 5: NO → 이동
        tk.Label(frame_left, text="NO → 레코드:").grid(row=5, column=0, sticky="e", **pad)
        no_options = [str(i + 1) for i in range(self.count)] + ["종료"]
        self.no_var = tk.StringVar()
        self.no_var.set(str(r["no_to"] + 1) if r["no_to"] is not None and r["no_to"] < self.count else "종료")
        ttk.Combobox(frame_left, textvariable=self.no_var, values=no_options, width=8,
                     state="readonly").grid(row=5, column=1, sticky="w", **pad)

        # Row 6: 정확도
        tk.Label(frame_left, text="정확도 (%):").grid(row=6, column=0, sticky="e", **pad)
        self.conf_var = tk.StringVar(value=str(int(r.get("confidence", DEFAULT_CONFIDENCE) * 100)))
        tk.Entry(frame_left, textvariable=self.conf_var, width=8).grid(row=6, column=1, sticky="w", **pad)

        # Row 7: 버튼
        frame_btn = tk.Frame(frame_left)
        frame_btn.grid(row=7, column=0, columnspan=4, pady=8)
        tk.Button(frame_btn, text="확인", width=10, command=self._apply).pack(side="left", padx=5)
        tk.Button(frame_btn, text="취소", width=10, command=self.win.destroy).pack(side="left", padx=5)

        # ── 오른쪽: 미리보기 ──
        tk.Label(frame_right, text="미리보기", bg="#2b2b2b", fg="white",
                 font=("Arial", 10)).pack(pady=(6, 2))
        self.preview_label = tk.Label(frame_right, text="이미지 없음", bg="#2b2b2b",
                                       fg="#888888", font=("Arial", 10))
        self.preview_label.pack(fill="both", expand=True, padx=4, pady=(0, 8))

        # 초기 대기 UI 상태 및 이미지/색상 모드 반영
        self._on_wait_type_change()
        self._update_detect_mode()

        img_path = r.get("image_path", "")
        if img_path and os.path.exists(img_path):
            self.win.after(100, self._update_preview)

    def _on_wait_type_change(self):
        wtype = self.wait_type_var.get()
        self.single_frame.pack_forget()
        self.random_frame.pack_forget()
        if wtype == "single":
            self.single_frame.pack(fill="x", pady=2)
            self.yes_combo["values"] = self._yes_options_no_exit
            if self.yes_var.get() == "종료" and self._yes_options_no_exit[0] != "종료":
                self.yes_var.set(self._yes_options_no_exit[0])
            self._detect_frame.grid_remove()
        elif wtype == "random":
            self.random_frame.pack(fill="x", pady=2)
            self.yes_combo["values"] = self._yes_options_no_exit
            if self.yes_var.get() == "종료" and self._yes_options_no_exit[0] != "종료":
                self.yes_var.set(self._yes_options_no_exit[0])
            self._detect_frame.grid_remove()
        else:
            self.yes_combo["values"] = self._yes_options_all
            self._detect_frame.grid()

    def _browse_image(self):
        path = filedialog.askopenfilename(
            filetypes=[("이미지 파일", "*.png *.jpg *.bmp"), ("모든 파일", "*.*")],
            title="이미지 선택"
        )
        if path:
            self.color_data = None          # 색상 모드 해제
            self.img_var.set(path)
            self._update_detect_mode()
            self._update_preview()

    def _capture_color(self):
        self.win.grab_release()
        self.win.withdraw()
        self.win.update()
        self.win.after(400, lambda: RegionSelector(self._on_color_region_selected, mode="region"))

    def _on_color_region_selected(self, region):
        x1, y1, x2, y2 = region
        screenshot = ImageGrab.grab(bbox=(x1, y1, x2, y2))
        self.win.deiconify()
        self.win.grab_set()
        self.win.update()
        ColorPickerDialog(self.win, region, screenshot,
                          lambda rgb, tol: self._on_color_picked(region, rgb, tol))

    def _on_color_picked(self, region, rgb, tolerance):
        self.color_data = {"region": list(region), "rgb": list(rgb), "tolerance": tolerance}
        self.img_var.set("")
        self._update_detect_mode()
        self.preview_label.config(image="", text="이미지 없음")

    def _clear_color(self):
        self.color_data = None
        self._update_detect_mode()

    def _update_detect_mode(self):
        if self.color_data and self.color_data.get("rgb"):
            r, g, b = self.color_data["rgb"]
            hex_color = f"#{r:02x}{g:02x}{b:02x}"
            self._color_swatch.config(bg=hex_color)
            tol = self.color_data.get("tolerance", 20)
            self._color_info_label.config(text=f"RGB({r},{g},{b})  허용±{tol}")
            self._img_row.pack_forget()
            self._color_row.pack(fill="x", pady=2)
        else:
            self._color_row.pack_forget()
            self._img_row.pack(fill="x", pady=2)

    def _update_preview(self):
        if not hasattr(self, "preview_label"):
            return
        path = self.img_var.get().strip()
        if not path or not os.path.exists(path):
            self.preview_label.config(image="", text="이미지 없음")
            return
        try:
            pw = self.preview_label.winfo_width()
            ph = self.preview_label.winfo_height()
            if pw < 10 or ph < 10:
                pw, ph = 280, 220

            img = Image.open(path)
            img.thumbnail((pw, ph), Image.LANCZOS)
            self._preview_img = ImageTk.PhotoImage(img)
            self.preview_label.config(image=self._preview_img, text="")
        except Exception as e:
            print(f"[preview error] {e}")
            self.preview_label.config(image="", text="이미지 로드 실패")

    def _apply(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("오류", "이름을 입력하세요.", parent=self.win)
            return

        wait_type = self.wait_type_var.get()
        wait_single = 0.0
        wait_min = 0.0
        wait_max = 0.0

        if wait_type == "single":
            try:
                wait_single = float(self.wait_single_var.get())
                if wait_single < 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("오류", "대기(초)는 0 이상의 숫자로 입력하세요.", parent=self.win)
                return
        elif wait_type == "random":
            try:
                wait_min = float(self.wait_min_var.get())
                wait_max = float(self.wait_max_var.get())
                if wait_min < 0 or wait_max < 0 or wait_min > wait_max:
                    raise ValueError
            except ValueError:
                messagebox.showerror("오류", "대기 범위를 올바르게 입력하세요.\n(최솟값 ≤ 최댓값, 0 이상)", parent=self.win)
                return

        yes_val = self.yes_var.get()
        no_val = self.no_var.get()
        yes_to = int(yes_val) - 1 if yes_val != "종료" else None
        no_to = int(no_val) - 1 if no_val != "종료" else None

        if wait_type in ("single", "random") and yes_to is None:
            messagebox.showerror("오류", "대기 설정 시 YES → 레코드를 지정해야 합니다.", parent=self.win)
            return

        common = {
            "name": name,
            "yes_to": yes_to,
            "no_to": no_to,
            "wait_type": wait_type,
            "wait_single": wait_single,
            "wait_min": wait_min,
            "wait_max": wait_max,
        }

        if self.color_data and self.color_data.get("rgb"):
            # 색상 감지 레코드
            record = {
                "type": "color",
                "color_region": self.color_data["region"],
                "color_rgb": self.color_data["rgb"],
                "color_tolerance": self.color_data.get("tolerance", 20),
                **common,
            }
        else:
            # 이미지 템플릿 매칭 레코드
            img_path = self.img_var.get().strip()
            if wait_type == "none":
                if not img_path or not os.path.exists(img_path):
                    messagebox.showerror("오류", "유효한 이미지 파일을 선택하거나 색상을 캡처하세요.", parent=self.win)
                    return
            elif img_path and not os.path.exists(img_path):
                messagebox.showerror("오류", "이미지 경로가 올바르지 않습니다.", parent=self.win)
                return

            try:
                conf = int(self.conf_var.get()) / 100
                if not (0 < conf <= 1):
                    raise ValueError
            except ValueError:
                messagebox.showerror("오류", "정확도는 1~100 숫자로 입력하세요.", parent=self.win)
                return

            record = {
                "type": "image",
                "image_path": img_path,
                "confidence": conf,
                **common,
            }

        if self.edit_idx is None:
            self.records.append(record)
        else:
            self.records[self.edit_idx] = record

        self.refresh_callback()
        self.win.destroy()


# ────────── 색상 선택 다이얼로그 ──────────
class ColorPickerDialog:
    def __init__(self, parent, region_coords, screenshot, callback):
        self.region_coords = region_coords
        self.screenshot = screenshot
        self.callback = callback
        self._selected_rgb = None

        self.win = tk.Toplevel(parent)
        self.win.title("색상 선택")
        self.win.grab_set()
        self.win.resizable(False, False)

        # 표시용 이미지 스케일 (최대 400x300)
        display = screenshot.copy()
        display.thumbnail((400, 300), Image.LANCZOS)
        self._scale_x = screenshot.width / display.width
        self._scale_y = screenshot.height / display.height
        self._display = display
        self._tk_img = ImageTk.PhotoImage(display)

        tk.Label(self.win, text="색상을 선택할 픽셀을 클릭하세요",
                 font=("Arial", 10)).pack(pady=(8, 4))

        self.canvas = tk.Canvas(self.win, width=display.width, height=display.height,
                                cursor="crosshair")
        self.canvas.pack(padx=8)
        self.canvas.create_image(0, 0, anchor="nw", image=self._tk_img)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Button-1>", self._on_click)

        # 색상 미리보기
        info_frame = tk.Frame(self.win)
        info_frame.pack(fill="x", padx=8, pady=4)
        tk.Label(info_frame, text="선택 색상:").pack(side="left")
        self._swatch = tk.Label(info_frame, text="   ", bg="#cccccc", relief="sunken", width=4)
        self._swatch.pack(side="left", padx=4)
        self._rgb_label = tk.Label(info_frame, text="클릭하여 선택")
        self._rgb_label.pack(side="left")

        # HEX 직접 입력
        hex_frame = tk.Frame(self.win)
        hex_frame.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(hex_frame, text="HEX 직접 입력:").pack(side="left")
        self._hex_var = tk.StringVar()
        self._hex_entry = tk.Entry(hex_frame, textvariable=self._hex_var, width=10)
        self._hex_entry.pack(side="left", padx=4)
        tk.Label(hex_frame, text="(예: #FF0000)").pack(side="left")
        tk.Button(hex_frame, text="적용", width=5, command=self._apply_hex).pack(side="left", padx=4)

        # 허용 오차
        tol_frame = tk.Frame(self.win)
        tol_frame.pack(fill="x", padx=8, pady=4)
        tk.Label(tol_frame, text="허용 오차 (0~255):").pack(side="left")
        self._tol_var = tk.StringVar(value="20")
        tk.Entry(tol_frame, textvariable=self._tol_var, width=6).pack(side="left", padx=4)

        # 버튼
        btn_frame = tk.Frame(self.win)
        btn_frame.pack(pady=8)
        self._ok_btn = tk.Button(btn_frame, text="확인", width=10, command=self._apply,
                                  state="disabled")
        self._ok_btn.pack(side="left", padx=5)
        tk.Button(btn_frame, text="취소", width=10, command=self.win.destroy).pack(side="left", padx=5)

    def _pixel_at(self, event):
        px = max(0, min(int(event.x * self._scale_x), self.screenshot.width - 1))
        py = max(0, min(int(event.y * self._scale_y), self.screenshot.height - 1))
        return self.screenshot.getpixel((px, py))[:3]

    def _set_color(self, r, g, b):
        self._selected_rgb = (r, g, b)
        hex_str = f"#{r:02x}{g:02x}{b:02x}"
        self._swatch.config(bg=hex_str)
        self._rgb_label.config(text=f"RGB({r}, {g}, {b})  ✓ 선택됨")
        self._hex_var.set(hex_str.upper())
        self._ok_btn.config(state="normal")

    def _on_motion(self, event):
        r, g, b = self._pixel_at(event)
        self._swatch.config(bg=f"#{r:02x}{g:02x}{b:02x}")
        self._rgb_label.config(text=f"RGB({r}, {g}, {b})")

    def _on_click(self, event):
        r, g, b = self._pixel_at(event)
        self._set_color(r, g, b)

    def _apply_hex(self):
        raw = self._hex_var.get().strip()
        if not raw.startswith("#"):
            raw = "#" + raw
        try:
            raw = raw.lstrip("#")
            if len(raw) != 6:
                raise ValueError
            r = int(raw[0:2], 16)
            g = int(raw[2:4], 16)
            b = int(raw[4:6], 16)
            self._set_color(r, g, b)
        except ValueError:
            messagebox.showerror("오류", "올바른 HEX 코드를 입력하세요.\n예: #FF0000", parent=self.win)

    def _apply(self):
        if self._selected_rgb is None:
            return
        try:
            tol = max(0, min(255, int(self._tol_var.get())))
        except ValueError:
            tol = 20
        self.callback(self._selected_rgb, tol)
        self.win.destroy()


# ────────── 클릭 레코드 편집 다이얼로그 ──────────
class ClickRecordDialog:
    def __init__(self, parent, records, edit_idx, refresh_callback):
        self.records = records
        self.edit_idx = edit_idx
        self.refresh_callback = refresh_callback
        self.count = len(records)

        r = records[edit_idx]

        self.win = tk.Toplevel(parent)
        self.win.title("클릭레코드 편집")
        self.win.grab_set()
        self.win.resizable(False, False)

        pad = {"padx": 10, "pady": 5}

        # 이름
        tk.Label(self.win, text="이름:").grid(row=0, column=0, sticky="e", **pad)
        self.name_var = tk.StringVar(value=r.get("name", "클릭"))
        tk.Entry(self.win, textvariable=self.name_var, width=20).grid(row=0, column=1, sticky="w", **pad)

        # X
        tk.Label(self.win, text="X:").grid(row=1, column=0, sticky="e", **pad)
        self.x_var = tk.StringVar(value=str(r.get("click_x", 0)))
        tk.Entry(self.win, textvariable=self.x_var, width=20).grid(row=1, column=1, sticky="w", **pad)

        # Y
        tk.Label(self.win, text="Y:").grid(row=2, column=0, sticky="e", **pad)
        self.y_var = tk.StringVar(value=str(r.get("click_y", 0)))
        tk.Entry(self.win, textvariable=self.y_var, width=20).grid(row=2, column=1, sticky="w", **pad)

        # YES → 레코드
        tk.Label(self.win, text="YES → 레코드:").grid(row=3, column=0, sticky="e", **pad)
        yes_options = [str(i + 1) for i in range(self.count)] + ["종료"]
        self.yes_var = tk.StringVar()
        self.yes_var.set(str(r["yes_to"] + 1) if r["yes_to"] is not None and r["yes_to"] < self.count else "종료")
        ttk.Combobox(self.win, textvariable=self.yes_var, values=yes_options, width=8,
                     state="readonly").grid(row=3, column=1, sticky="w", **pad)

        # 버튼
        frame_btn = tk.Frame(self.win)
        frame_btn.grid(row=4, column=0, columnspan=2, pady=10)
        tk.Button(frame_btn, text="확인", width=10, command=self._apply).pack(side="left", padx=5)
        tk.Button(frame_btn, text="취소", width=10, command=self.win.destroy).pack(side="left", padx=5)

    def _apply(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("오류", "이름을 입력하세요.", parent=self.win)
            return

        try:
            x = int(self.x_var.get())
            y = int(self.y_var.get())
        except ValueError:
            messagebox.showerror("오류", "X, Y는 정수로 입력하세요.", parent=self.win)
            return

        yes_val = self.yes_var.get()
        yes_to = int(yes_val) - 1 if yes_val != "종료" else None

        self.records[self.edit_idx] = {
            "type": "click",
            "name": name,
            "click_x": x,
            "click_y": y,
            "yes_to": yes_to,
            "no_to": self.records[self.edit_idx].get("no_to"),
        }

        self.refresh_callback()
        self.win.destroy()


# ────────── 실행 ──────────
if __name__ == "__main__":
    root = tk.Tk()
    app = CheDetect(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
