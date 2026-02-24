"""
BGF 리포트 생성기 - GUI 런처

더블클릭으로 실행하면 리포트 종류를 선택하고
생성 버튼을 누르면 HTML 리포트가 생성되어 브라우저에서 자동으로 열린다.
"""

import sys
import os
import threading
import webbrowser
from pathlib import Path
from datetime import datetime

# ── 경로 설정 ──
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SRC_ROOT))
os.chdir(str(SRC_ROOT))

import tkinter as tk
from tkinter import filedialog, scrolledtext

DB_PATH = str(PROJECT_ROOT / "data" / "bgf_sales.db")

# ── 색상 테마 ──
COLORS = {
    "bg": "#0f0f1a",
    "bg2": "#16213e",
    "bg3": "#1e3054",
    "fg": "#e0e0e0",
    "fg2": "#999999",
    "accent": "#00d2ff",
    "green": "#69f0ae",
    "red": "#ff6b6b",
    "yellow": "#ffd600",
}


class ReportLauncher:
    """리포트 생성 GUI"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("BGF 리포트 생성기")
        self.root.configure(bg=COLORS["bg"])
        self.root.geometry("520x680")
        self.root.resizable(False, False)

        # 윈도우 아이콘 (있으면)
        icon_path = SCRIPT_DIR / "bgf_report.ico"
        if icon_path.exists():
            try:
                self.root.iconbitmap(str(icon_path))
            except Exception:
                pass

        self._running = False
        self._build_ui()

    def _build_ui(self):
        root = self.root
        pad = {"padx": 16, "pady": 4}

        # ── 헤더 ──
        header = tk.Frame(root, bg=COLORS["bg"])
        header.pack(fill="x", padx=16, pady=(16, 8))
        tk.Label(
            header, text="BGF 리포트 생성기", font=("맑은 고딕", 16, "bold"),
            fg="#fff", bg=COLORS["bg"],
        ).pack(side="left")
        tk.Label(
            header, text="v1.0", font=("맑은 고딕", 9),
            fg=COLORS["fg2"], bg=COLORS["bg"],
        ).pack(side="left", padx=(8, 0), pady=(6, 0))

        # ── 리포트 선택 ──
        self._section_label(root, "리포트 선택")

        sel_frame = tk.Frame(root, bg=COLORS["bg2"], bd=0, highlightthickness=1,
                             highlightbackground=COLORS["bg3"])
        sel_frame.pack(fill="x", padx=16, pady=(0, 8))

        # 일일 발주
        self.var_daily = tk.BooleanVar(value=True)
        self._checkbox(sel_frame, "일일 발주 리포트", self.var_daily)

        # 주간 트렌드
        self.var_weekly = tk.BooleanVar(value=True)
        self._checkbox(sel_frame, "주간 트렌드 리포트", self.var_weekly)

        # 카테고리 분석
        cat_row = tk.Frame(sel_frame, bg=COLORS["bg2"])
        cat_row.pack(fill="x", padx=12, pady=3)
        self.var_category = tk.BooleanVar(value=False)
        tk.Checkbutton(
            cat_row, text="카테고리 분석", variable=self.var_category,
            fg=COLORS["fg"], bg=COLORS["bg2"], selectcolor=COLORS["bg"],
            activebackground=COLORS["bg2"], activeforeground=COLORS["fg"],
            font=("맑은 고딕", 10),
        ).pack(side="left")

        # 카테고리 드롭다운
        self._load_categories()
        self.cat_var = tk.StringVar(value=self.cat_options[0] if self.cat_options else "")
        self.cat_menu = tk.OptionMenu(cat_row, self.cat_var, *self.cat_options)
        self.cat_menu.config(
            bg=COLORS["bg"], fg=COLORS["fg"], activebackground=COLORS["bg3"],
            highlightthickness=0, font=("맑은 고딕", 9), width=20,
        )
        self.cat_menu["menu"].config(
            bg=COLORS["bg2"], fg=COLORS["fg"], activebackground=COLORS["accent"],
        )
        self.cat_menu.pack(side="right", padx=(8, 0))

        # Baseline 저장
        self.var_baseline = tk.BooleanVar(value=False)
        self._checkbox(sel_frame, "안전재고 Baseline 저장", self.var_baseline)

        # 영향도 분석
        impact_row = tk.Frame(sel_frame, bg=COLORS["bg2"])
        impact_row.pack(fill="x", padx=12, pady=(3, 8))
        self.var_impact = tk.BooleanVar(value=False)
        tk.Checkbutton(
            impact_row, text="영향도 분석", variable=self.var_impact,
            fg=COLORS["fg"], bg=COLORS["bg2"], selectcolor=COLORS["bg"],
            activebackground=COLORS["bg2"], activeforeground=COLORS["fg"],
            font=("맑은 고딕", 10),
        ).pack(side="left")

        self.baseline_path_var = tk.StringVar(value="")
        tk.Entry(
            impact_row, textvariable=self.baseline_path_var, width=22,
            bg=COLORS["bg"], fg=COLORS["fg"], insertbackground=COLORS["fg"],
            font=("맑은 고딕", 9), relief="flat", highlightthickness=1,
            highlightbackground=COLORS["bg3"],
        ).pack(side="left", padx=(8, 4))
        tk.Button(
            impact_row, text="찾기", command=self._browse_baseline,
            bg=COLORS["bg3"], fg=COLORS["fg"], relief="flat",
            activebackground=COLORS["accent"], font=("맑은 고딕", 9), padx=8,
        ).pack(side="left")

        # ── 옵션 ──
        self._section_label(root, "옵션")

        opt_frame = tk.Frame(root, bg=COLORS["bg2"], bd=0, highlightthickness=1,
                             highlightbackground=COLORS["bg3"])
        opt_frame.pack(fill="x", padx=16, pady=(0, 8))

        # 최대 상품수
        max_row = tk.Frame(opt_frame, bg=COLORS["bg2"])
        max_row.pack(fill="x", padx=12, pady=6)
        tk.Label(
            max_row, text="최대 상품수:", fg=COLORS["fg"], bg=COLORS["bg2"],
            font=("맑은 고딕", 10),
        ).pack(side="left")
        self.max_items_var = tk.StringVar(value="0")
        tk.Entry(
            max_row, textvariable=self.max_items_var, width=8,
            bg=COLORS["bg"], fg=COLORS["fg"], insertbackground=COLORS["fg"],
            font=("맑은 고딕", 10), relief="flat", highlightthickness=1,
            highlightbackground=COLORS["bg3"], justify="center",
        ).pack(side="left", padx=(8, 4))
        tk.Label(
            max_row, text="(0 = 전체)", fg=COLORS["fg2"], bg=COLORS["bg2"],
            font=("맑은 고딕", 9),
        ).pack(side="left", padx=(4, 0))

        # 브라우저 자동 열기
        self.var_open = tk.BooleanVar(value=True)
        self._checkbox(opt_frame, "생성 후 브라우저에서 열기", self.var_open, pady=(3, 8))

        # ── 생성 버튼 ──
        self.btn_generate = tk.Button(
            root, text="리포트 생성", command=self._on_generate,
            bg=COLORS["accent"], fg="#000", font=("맑은 고딕", 13, "bold"),
            relief="flat", activebackground="#33dfff", cursor="hand2",
            padx=20, pady=8,
        )
        self.btn_generate.pack(pady=(8, 12))

        # ── 로그 ──
        self._section_label(root, "진행 상황")

        self.log_area = scrolledtext.ScrolledText(
            root, height=10, bg=COLORS["bg2"], fg=COLORS["green"],
            insertbackground=COLORS["fg"], font=("Consolas", 9),
            relief="flat", highlightthickness=1, highlightbackground=COLORS["bg3"],
            state="disabled",
        )
        self.log_area.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    def _section_label(self, parent, text):
        tk.Label(
            parent, text=text, fg=COLORS["accent"], bg=COLORS["bg"],
            font=("맑은 고딕", 10, "bold"), anchor="w",
        ).pack(fill="x", padx=18, pady=(8, 2))

    def _checkbox(self, parent, text, var, pady=3):
        tk.Checkbutton(
            parent, text=text, variable=var,
            fg=COLORS["fg"], bg=COLORS["bg2"], selectcolor=COLORS["bg"],
            activebackground=COLORS["bg2"], activeforeground=COLORS["fg"],
            font=("맑은 고딕", 10),
        ).pack(anchor="w", padx=12, pady=pady)

    def _load_categories(self):
        """카테고리 목록 로드"""
        try:
            from prediction.categories.default import CATEGORY_NAMES
            self.cat_options = [
                f"{name} ({code})"
                for code, name in sorted(CATEGORY_NAMES.items())
            ]
            self._cat_map = {
                f"{name} ({code})": code
                for code, name in CATEGORY_NAMES.items()
            }
        except Exception:
            self.cat_options = ["049 - 맥주"]
            self._cat_map = {"049 - 맥주": "049"}

    def _browse_baseline(self):
        path = filedialog.askopenfilename(
            title="Baseline JSON 선택",
            initialdir=str(PROJECT_ROOT / "data" / "reports" / "impact"),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.baseline_path_var.set(path)

    def _log(self, msg: str):
        """로그 출력 (thread-safe)"""
        def _append():
            self.log_area.config(state="normal")
            self.log_area.insert("end", msg + "\n")
            self.log_area.see("end")
            self.log_area.config(state="disabled")
        self.root.after(0, _append)

    def _set_running(self, running: bool):
        def _update():
            self._running = running
            self.btn_generate.config(
                text="생성 중..." if running else "리포트 생성",
                state="disabled" if running else "normal",
                bg=COLORS["bg3"] if running else COLORS["accent"],
            )
        self.root.after(0, _update)

    def _on_generate(self):
        if self._running:
            return

        # 선택 확인
        any_selected = any([
            self.var_daily.get(), self.var_weekly.get(),
            self.var_category.get(), self.var_baseline.get(),
            self.var_impact.get(),
        ])
        if not any_selected:
            self._log("[오류] 리포트를 하나 이상 선택하세요.")
            return

        # 영향도 분석 시 baseline 경로 확인
        if self.var_impact.get() and not self.baseline_path_var.get():
            self._log("[오류] 영향도 분석에는 Baseline JSON 경로가 필요합니다.")
            return

        self._set_running(True)
        self.log_area.config(state="normal")
        self.log_area.delete("1.0", "end")
        self.log_area.config(state="disabled")

        thread = threading.Thread(target=self._generate_reports, daemon=True)
        thread.start()

    def _generate_reports(self):
        """백그라운드 스레드에서 리포트 생성"""
        generated_files = []
        start_time = datetime.now()

        try:
            max_items = int(self.max_items_var.get() or 0)
        except ValueError:
            max_items = 0

        # 예측 결과 (daily, baseline, impact에 필요)
        predictions = None
        need_predictions = (
            self.var_daily.get() or self.var_baseline.get() or self.var_impact.get()
        )

        if need_predictions:
            try:
                self._log("[예측] 발주 예측 실행 중...")
                from prediction.improved_predictor import ImprovedPredictor
                predictor = ImprovedPredictor()
                predictions = predictor.get_order_candidates(min_order_qty=0)
                if max_items > 0:
                    predictions = predictions[:max_items]
                self._log(f"[예측] 완료: {len(predictions)}개 상품")
            except Exception as e:
                self._log(f"[오류] 예측 실패: {e}")
                self._set_running(False)
                return

        # ── 일일 발주 ──
        if self.var_daily.get():
            try:
                self._log("[일일] 일일 발주 리포트 생성 중...")
                from report import DailyOrderReport
                report = DailyOrderReport(DB_PATH)
                path = report.generate(predictions)
                generated_files.append(path)
                self._log(f"[일일] 완료: {path.name}")
            except Exception as e:
                self._log(f"[오류] 일일 발주 실패: {e}")

        # ── 주간 트렌드 ──
        if self.var_weekly.get():
            try:
                self._log("[주간] 주간 트렌드 리포트 생성 중...")
                from report import WeeklyTrendReportHTML
                report = WeeklyTrendReportHTML(DB_PATH)
                path = report.generate()
                generated_files.append(path)
                self._log(f"[주간] 완료: {path.name}")
            except Exception as e:
                self._log(f"[오류] 주간 트렌드 실패: {e}")

        # ── 카테고리 분석 ──
        if self.var_category.get():
            try:
                selected = self.cat_var.get()
                mid_cd = self._cat_map.get(selected, "049")
                self._log(f"[카테고리] {selected} 분석 중...")
                from report import CategoryDetailReport
                report = CategoryDetailReport(DB_PATH)
                path = report.generate(mid_cd)
                generated_files.append(path)
                self._log(f"[카테고리] 완료: {path.name}")
            except Exception as e:
                self._log(f"[오류] 카테고리 분석 실패: {e}")

        # ── Baseline 저장 ──
        if self.var_baseline.get():
            try:
                self._log("[Baseline] 안전재고 baseline 저장 중...")
                from report import SafetyImpactReport
                report = SafetyImpactReport(DB_PATH)
                path = report.save_baseline(predictions)
                self._log(f"[Baseline] 저장 완료: {path.name}")
                self._log(f"  경로: {path}")
            except Exception as e:
                self._log(f"[오류] Baseline 저장 실패: {e}")

        # ── 영향도 분석 ──
        if self.var_impact.get():
            try:
                baseline_path = self.baseline_path_var.get()
                self._log(f"[영향도] 영향도 분석 중...")
                from report import SafetyImpactReport
                report = SafetyImpactReport(DB_PATH)
                path = report.generate(predictions, baseline_path)
                generated_files.append(path)
                self._log(f"[영향도] 완료: {path.name}")
            except Exception as e:
                self._log(f"[오류] 영향도 분석 실패: {e}")

        # ── 결과 ──
        elapsed = (datetime.now() - start_time).total_seconds()
        self._log(f"\n{'─'*40}")
        self._log(f"완료: {len(generated_files)}건 생성 ({elapsed:.1f}초)")

        # 브라우저 자동 열기
        if self.var_open.get() and generated_files:
            for fpath in generated_files:
                try:
                    webbrowser.open(fpath.as_uri())
                    self._log(f"  열기: {fpath.name}")
                except Exception:
                    webbrowser.open(str(fpath))

        self._set_running(False)

    def run(self):
        # 화면 중앙 배치
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (w // 2)
        y = (self.root.winfo_screenheight() // 2) - (h // 2)
        self.root.geometry(f"+{x}+{y}")
        self.root.mainloop()


if __name__ == "__main__":
    app = ReportLauncher()
    app.run()
