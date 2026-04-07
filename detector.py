VERSION = "1.0.0"

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import time
import json
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

        # 먼저 화면 전체 스크린샷 찍기
        self._screenshot = ImageGrab.grab()
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
            outline="red", width=2, fill="#ff000033"
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


# ────────── 메인 앱 ──────────
class CheDetect:
    def __init__(self, root):
        self.root = root
        self.root.title(f"cheDetect v{VERSION}")
        self.root.resizable(False, False)

        self.records = []   # {"name", "image_path", "yes_to", "no_to", "confidence"}
        self.region = None  # (x1, y1, x2, y2)
        self.running = False
        self.macro_thread = None
        self.start_key = DEFAULT_START_KEY

        self._build_ui()
        self._register_hotkeys()

    def _build_ui(self):
        # ── 감지 영역 ──
        frame_region = tk.LabelFrame(self.root, text="감지 영역")
        frame_region.pack(padx=10, pady=(10, 5), fill="x")

        self.region_var = tk.StringVar(value="설정 안됨")
        tk.Label(frame_region, textvariable=self.region_var, width=30).pack(side="left", padx=5, pady=3)
        tk.Button(frame_region, text="영역 선택", command=self._select_region).pack(side="left", padx=5)
        tk.Button(frame_region, text="전체 화면", command=self._set_fullscreen).pack(side="left", padx=5)

        # ── 레코드 테이블 ──
        frame_table = tk.LabelFrame(self.root, text="레코드")
        frame_table.pack(padx=10, pady=5, fill="both")

        columns = ("#", "이름", "이미지", "YES→", "NO→", "정확도")
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
        frame_rec_btns = tk.Frame(self.root)
        frame_rec_btns.pack(padx=10, pady=3)

        tk.Button(frame_rec_btns, text="+ 추가", width=8, command=self._add_record).pack(side="left", padx=2)
        tk.Button(frame_rec_btns, text="✎ 편집", width=8, command=self._edit_record).pack(side="left", padx=2)
        tk.Button(frame_rec_btns, text="삭제", width=8, command=self._delete_record).pack(side="left", padx=2)
        tk.Button(frame_rec_btns, text="▲ 위로", width=8, command=self._move_up).pack(side="left", padx=2)
        tk.Button(frame_rec_btns, text="▼ 아래로", width=8, command=self._move_down).pack(side="left", padx=2)

        # ── 단축키 설정 ──
        frame_keys = tk.LabelFrame(self.root, text="단축키 설정")
        frame_keys.pack(padx=10, pady=5, fill="x")

        tk.Label(frame_keys, text="시작/종료:").grid(row=0, column=0, padx=5, pady=3)
        self.start_key_var = tk.StringVar(value=self.start_key.upper())
        tk.Entry(frame_keys, textvariable=self.start_key_var, width=8).grid(row=0, column=1, padx=5)
        tk.Button(frame_keys, text="적용", command=self._apply_key).grid(row=0, column=2, padx=5)

        # ── 상태 ──
        self.status_var = tk.StringVar(value="⏹ 대기 중")
        tk.Label(self.root, textvariable=self.status_var, font=("Arial", 11, "bold")).pack(pady=3)

        # ── 저장/불러오기/시작 ──
        frame_btns = tk.Frame(self.root)
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
        RegionSelector(self._on_region_selected)

    def _on_region_selected(self, region):
        self.region = region
        self.region_var.set(f"({region[0]}, {region[1]}) ~ ({region[2]}, {region[3]})")
        self.root.deiconify()

    def _set_fullscreen(self):
        w, h = pyautogui.size()
        self.region = (0, 0, w, h)
        self.region_var.set(f"전체 화면 ({w}x{h})")

    # ── 레코드 관리 ──
    def _refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.records):
            img_name = os.path.basename(r["image_path"]) if r["image_path"] else "없음"
            yes_label = str(r["yes_to"] + 1) if r["yes_to"] is not None else "종료"
            no_label = str(r["no_to"] + 1) if r["no_to"] is not None else "종료"
            self.tree.insert("", "end", values=(
                i + 1, r["name"], img_name, yes_label, no_label,
                f"{int(r['confidence'] * 100)}%"
            ))

    def _add_record(self):
        RecordDialog(self.root, self.records, None, self._refresh_table)

    def _edit_record(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("선택 없음", "편집할 레코드를 선택하세요.")
            return
        idx = self.tree.index(selected[0])
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
        except Exception as e:
            print(f"단축키 등록 실패: {e}")

    def _apply_key(self):
        self.start_key = self.start_key_var.get().lower()
        self._register_hotkeys()
        messagebox.showinfo("단축키", f"적용됨: {self.start_key.upper()}")

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
            self.start_key_var.set(self.start_key.upper())
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
            found = self._find_image(record)

            if found:
                # 클릭
                cx = (found[0] + found[2]) // 2
                cy = (found[1] + found[3]) // 2
                pyautogui.click(cx, cy)
                self.root.after(0, lambda r=record, i=current_idx:
                                self.status_var.set(f"▶ [{i+1}] {r['name']} - YES 클릭"))
                next_idx = record["yes_to"]
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

    def _stop_from_thread(self):
        self.running = False
        self.root.after(0, lambda: self.status_var.set("⏹ 완료"))
        self.root.after(0, lambda: self.toggle_btn.config(text="▶ 시작", bg="#4CAF50"))

    def on_close(self):
        self.running = False
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

        self.win = tk.Toplevel(parent)
        self.win.title("레코드 추가" if edit_idx is None else "레코드 편집")
        self.win.grab_set()
        self.win.resizable(True, True)

        if edit_idx is not None:
            r = records[edit_idx]
        else:
            r = {"name": "", "image_path": "", "yes_to": 0, "no_to": None, "confidence": DEFAULT_CONFIDENCE}

        pad = {"padx": 8, "pady": 4}

        # ── 좌우 분할 ──
        frame_left = tk.Frame(self.win)
        frame_left.pack(side="left", fill="y", padx=(0, 0))

        frame_right = tk.Frame(self.win, bg="#2b2b2b", width=300)
        frame_right.pack(side="left", fill="both", expand=True)
        frame_right.pack_propagate(False)

        # ── 왼쪽: 폼 ──
        # 이름
        tk.Label(frame_left, text="이름:").grid(row=0, column=0, sticky="e", **pad)
        self.name_var = tk.StringVar(value=r["name"])
        tk.Entry(frame_left, textvariable=self.name_var, width=22).grid(row=0, column=1, columnspan=3, **pad)

        # 이미지 경로
        tk.Label(frame_left, text="이미지:").grid(row=1, column=0, sticky="e", **pad)
        self.img_var = tk.StringVar(value=r["image_path"])
        self.img_var.trace_add("write", lambda *_: self._update_preview())
        tk.Entry(frame_left, textvariable=self.img_var, width=16).grid(row=1, column=1, **pad)
        tk.Button(frame_left, text="파일", width=5, command=self._browse_image).grid(row=1, column=2, **pad)
        tk.Button(frame_left, text="캡처", width=5, command=self._capture_image).grid(row=1, column=3, **pad)

        # YES → 이동
        tk.Label(frame_left, text="YES → 레코드:").grid(row=2, column=0, sticky="e", **pad)
        yes_options = [str(i + 1) for i in range(self.count)] + ["종료"]
        self.yes_var = tk.StringVar()
        self.yes_var.set(str(r["yes_to"] + 1) if r["yes_to"] is not None and r["yes_to"] < self.count else "종료")
        ttk.Combobox(frame_left, textvariable=self.yes_var, values=yes_options, width=8,
                     state="readonly").grid(row=2, column=1, sticky="w", **pad)

        # NO → 이동
        tk.Label(frame_left, text="NO → 레코드:").grid(row=3, column=0, sticky="e", **pad)
        no_options = [str(i + 1) for i in range(self.count)] + ["종료"]
        self.no_var = tk.StringVar()
        self.no_var.set(str(r["no_to"] + 1) if r["no_to"] is not None and r["no_to"] < self.count else "종료")
        ttk.Combobox(frame_left, textvariable=self.no_var, values=no_options, width=8,
                     state="readonly").grid(row=3, column=1, sticky="w", **pad)

        # 정확도
        tk.Label(frame_left, text="정확도 (%):").grid(row=4, column=0, sticky="e", **pad)
        self.conf_var = tk.StringVar(value=str(int(r["confidence"] * 100)))
        tk.Entry(frame_left, textvariable=self.conf_var, width=8).grid(row=4, column=1, sticky="w", **pad)

        # 버튼
        frame_btn = tk.Frame(frame_left)
        frame_btn.grid(row=5, column=0, columnspan=4, pady=8)
        tk.Button(frame_btn, text="확인", width=10, command=self._apply).pack(side="left", padx=5)
        tk.Button(frame_btn, text="취소", width=10, command=self.win.destroy).pack(side="left", padx=5)

        # ── 오른쪽: 미리보기 ──
        tk.Label(frame_right, text="미리보기", bg="#2b2b2b", fg="white",
                 font=("Arial", 10)).pack(pady=(6, 2))
        self.preview_label = tk.Label(frame_right, text="이미지 없음", bg="#2b2b2b",
                                       fg="#888888", font=("Arial", 10))
        self.preview_label.pack(fill="both", expand=True, padx=4, pady=(0, 8))
        self._preview_img = None

        if r["image_path"] and os.path.exists(r["image_path"]):
            self.win.after(100, self._update_preview)

    def _browse_image(self):
        path = filedialog.askopenfilename(
            filetypes=[("이미지 파일", "*.png *.jpg *.bmp"), ("모든 파일", "*.*")],
            title="이미지 선택"
        )
        if path:
            self.img_var.set(path)

    def _capture_image(self):
        self.win.withdraw()
        time.sleep(0.2)
        RegionSelector(self._on_captured, mode="capture")

    def _on_captured(self, path):
        self.win.deiconify()
        self.img_var.set(path)

    def _update_preview(self):
        path = self.img_var.get().strip()
        if not path or not os.path.exists(path):
            self.preview_label.config(image="", text="이미지 없음")
            return
        try:
            self.win.update_idletasks()
            pw = self.preview_label.winfo_width() or 300
            ph = self.preview_label.winfo_height() or 250
            if pw < 10:
                pw, ph = 300, 250

            img = Image.open(path)
            img.thumbnail((pw, ph), Image.LANCZOS)
            self._preview_img = ImageTk.PhotoImage(img)
            self.preview_label.config(image=self._preview_img, text="")
        except Exception:
            self.preview_label.config(image="", text="이미지 로드 실패")

    def _apply(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("오류", "이름을 입력하세요.", parent=self.win)
            return

        img_path = self.img_var.get().strip()
        if not img_path or not os.path.exists(img_path):
            messagebox.showerror("오류", "유효한 이미지 파일을 선택하세요.", parent=self.win)
            return

        try:
            conf = int(self.conf_var.get()) / 100
            if not (0 < conf <= 1):
                raise ValueError
        except ValueError:
            messagebox.showerror("오류", "정확도는 1~100 숫자로 입력하세요.", parent=self.win)
            return

        yes_val = self.yes_var.get()
        no_val = self.no_var.get()
        yes_to = int(yes_val) - 1 if yes_val != "종료" else None
        no_to = int(no_val) - 1 if no_val != "종료" else None

        record = {
            "name": name,
            "image_path": img_path,
            "yes_to": yes_to,
            "no_to": no_to,
            "confidence": conf
        }

        if self.edit_idx is None:
            self.records.append(record)
        else:
            self.records[self.edit_idx] = record

        self.refresh_callback()
        self.win.destroy()


# ────────── 실행 ──────────
if __name__ == "__main__":
    root = tk.Tk()
    app = CheDetect(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
