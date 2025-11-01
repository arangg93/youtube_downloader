# -*- coding: utf-8 -*-
r"""
아랑 유튜브 다운로더 (403 회피 + cookies.txt 자동 적용 포함)
- 축소 UI(420x640)
- 단일 영상만(playlist 파라미터 제거)
- ffmpeg 자동 탐색(같은 폴더/실행폴더/현재폴더)
- 403 완화: IPv4 강제, 청크 축소, 동시 조각 1개, UA/헤더 지정, player_client 스위칭
- cookies.txt 자동 인식(같은 폴더 또는 작업폴더)
- H.264+AAC 선호, 불가 시 자동 폴백(필요 시 재인코딩) → mp4 보장
- 중복 파일 자동 넘버링 / 완료 후 실제 파일 검증(보강: 생성된 파일을 우선 신뢰)
- 폴더 열기 / 마지막 저장 폴더 기억 (APPDATA\ArangYTDownloader\config.json)
"""

import sys, subprocess, importlib
IS_FROZEN = getattr(sys, "frozen", False)

# ------------------------ 의존성 ------------------------
def install_and_import(pkg, import_name=None):
    try:
        return importlib.import_module(import_name or pkg)
    except ImportError:
        if IS_FROZEN:
            raise
        print(f"[설치 중] {pkg} 라이브러리가 없어 설치합니다...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
        return importlib.import_module(import_name or pkg)

yt_dlp = install_and_import("yt-dlp", "yt_dlp")
try:
    pyperclip = install_and_import("pyperclip")
except Exception:
    pyperclip = None

# ------------------------ 표준 라이브러리 ------------------------
import os, re, threading, queue, traceback, json, webbrowser, shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from yt_dlp import YoutubeDL

YOUTUBE_REGEX = re.compile(r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/')

# ---------- 설정(최근 폴더) ----------
def get_config_path():
    base = os.getenv("APPDATA") or os.path.expanduser("~")
    cfg_dir = os.path.join(base, "ArangYTDownloader")
    os.makedirs(cfg_dir, exist_ok=True)
    return os.path.join(cfg_dir, "config.json")

def load_last_dir():
    try:
        path = get_config_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            d = data.get("last_dir", "")
            if d and os.path.isdir(d):
                return d
    except Exception:
        pass
    dfl = os.path.join(os.path.expanduser("~"), "Downloads")
    return dfl if os.path.isdir(dfl) else ""

def save_last_dir(d):
    try:
        path = get_config_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"last_dir": d}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# ---------- ffmpeg ----------
def find_ffmpeg_dir():
    candidates = []
    exe_dir = os.path.dirname(getattr(sys, "executable", sys.argv[0]))
    if exe_dir:
        candidates.append(exe_dir)
    candidates.append(os.getcwd())
    for d in candidates:
        for n in ("ffmpeg.exe", "ffmpeg"):
            if os.path.exists(os.path.join(d, n)):
                return d
    return None

def ensure_ffmpeg_on_path():
    ffdir = find_ffmpeg_dir()
    if ffdir and ffdir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = ffdir + os.pathsep + os.environ.get("PATH", "")
    return ffdir

def _ffmpeg_in_path() -> bool:
    return shutil.which("ffmpeg.exe" if os.name == "nt" else "ffmpeg") is not None

def prompt_ffmpeg_download():
    msg = (
        "ffmpeg가 필요합니다.\n\n"
        "1) '다운로드 페이지 열기'를 누르면 브라우저가 열립니다.\n"
        "2) Windows용 ffmpeg를 내려받아 압축 해제 후,\n"
        "   ffmpeg.exe를 이 프로그램과 같은 폴더(또는 PATH)에 두세요.\n"
        "3) 프로그램을 다시 실행하세요."
    )
    if messagebox.askyesno("ffmpeg 필요", msg + "\n\n다운로드 페이지를 여시겠습니까?"):
        try:
            webbrowser.open("https://www.gyan.dev/ffmpeg/builds/")
        except Exception:
            pass

# ---------- cookies.txt 자동 탐지 ----------
def find_cookie_file():
    """exe 폴더/현재폴더에서 cookies.txt를 찾아 경로 반환(없으면 None)"""
    candidates = []
    exe_dir = os.path.dirname(getattr(sys, "executable", sys.argv[0]))
    if exe_dir:
        candidates.append(os.path.join(exe_dir, "cookies.txt"))
    candidates.append(os.path.join(os.getcwd(), "cookies.txt"))
    for p in candidates:
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            return p
    return None

# ---------- 유틸 ----------
def normalize_youtube_url(u: str) -> str:
    """항상 단일 영상만 받도록 playlist 관련 파라미터 제거"""
    if "?" not in u:
        return u
    base, qs = u.split("?", 1)
    keep = []
    for part in qs.split("&"):
        k = part.split("=")[0].lower()
        if k in ("list", "index", "start_radio", "pp", "si"):
            continue
        keep.append(part)
    return base + ("?" + "&".join(keep) if keep else "")

def sanitize_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:180]

FORMAT_PRESETS = {
    "high":   ("bv*[height<=1080]", 1080),
    "medium": ("bv*[height<=720]",   720),
    "low":    ("bv*[height<=480]",   480),
}
RES_LABEL = {"high": "(해상도 상) ", "medium": "(해상도 중) ", "low": "(해상도 하) "}

def unique_path(outdir: str, base: str, ext: str) -> str:
    base = sanitize_filename(base)
    p = os.path.join(outdir, f"{base}.{ext}")
    if not os.path.exists(p):
        return p
    i = 1
    while True:
        p2 = os.path.join(outdir, f"{base} ({i:02d}).{ext}")
        if not os.path.exists(p2):
            return p2
        i += 1

def find_neighbor_output(outdir: str, base: str):
    base = sanitize_filename(base)
    cand_exts = (".mp4", ".mkv", ".webm", ".m4a", ".mov", ".mp3")
    candidates = []
    try:
        for fn in os.listdir(outdir):
            full = os.path.join(outdir, fn)
            if not os.path.isfile(full):
                continue
            name, ext = os.path.splitext(fn)
            if ext.lower() not in cand_exts:
                continue
            if name == base or re.fullmatch(rf"{re.escape(base)} \(\d+\)", name):
                candidates.append(full)
        if candidates:
            candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            return candidates[0]
    except Exception:
        pass
    return None

# ---------- 앱 ----------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("아랑의 Youtube 다운로더 v1.2.3")
        self.geometry("420x640")
        self.minsize(360, 520)
        self.resizable(True, True)

        self.msg_q = queue.Queue()
        self.current_dir = ""
        self.last_finished_path = None   # ★ 진행 훅이 보고한 실제 생성 파일 경로

        # URL + 시작 버튼
        frm_url = ttk.Frame(self); frm_url.pack(fill="x", padx=10, pady=(10,6))
        ttk.Label(frm_url, text="YouTube URL").pack(side="top", anchor="w")
        row = ttk.Frame(frm_url); row.pack(fill="x")
        self.ent_url = ttk.Entry(row); self.ent_url.pack(side="left", fill="x", expand=True)
        self.btn_start = ttk.Button(row, text="다운로드 시작", command=self.on_start)
        self.btn_start.pack(side="left", padx=6)

        # 저장 폴더
        frm_dir = ttk.Frame(self); frm_dir.pack(fill="x", padx=10, pady=6)
        ttk.Label(frm_dir, text="저장 폴더").pack(side="top", anchor="w")
        row = ttk.Frame(frm_dir); row.pack(fill="x")
        self.lbl_dir = ttk.Label(row, text="(미선택)", width=26, relief="sunken", anchor="w")
        self.lbl_dir.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="찾아보기", command=self.choose_dir).pack(side="left", padx=(6,0))
        ttk.Button(row, text="폴더 열기", command=self.open_dir).pack(side="left", padx=(6,0))

        # 파일 이름
        frm_name = ttk.Frame(self); frm_name.pack(fill="x", padx=10, pady=6)
        ttk.Label(frm_name, text="(선택) 원하는 파일이름(확장자 제외)").pack(side="top", anchor="w")
        self.ent_name = ttk.Entry(frm_name); self.ent_name.pack(fill="x")

        # 형식 / 해상도
        grp_fmt = ttk.LabelFrame(self, text="형식"); grp_fmt.pack(fill="x", padx=10, pady=(8,4))
        self.mode = tk.StringVar(value="video")
        ttk.Radiobutton(grp_fmt, text="영상", value="video", variable=self.mode,
                        command=self.toggle_res_opts).pack(side="left", padx=8, pady=4)
        ttk.Radiobutton(grp_fmt, text="음성(m4a)", value="audio", variable=self.mode,
                        command=self.toggle_res_opts).pack(side="left", padx=8, pady=4)

        grp_res = ttk.LabelFrame(self, text="해상도"); grp_res.pack(fill="x", padx=10, pady=(4,8))
        self.res_preset = tk.StringVar(value="high")
        self.rb_high = ttk.Radiobutton(grp_res, text="상(≤1080p)", value="high", variable=self.res_preset)
        self.rb_med  = ttk.Radiobutton(grp_res, text="중(≤720p)",  value="medium", variable=self.res_preset)
        self.rb_low  = ttk.Radiobutton(grp_res, text="하(≤480p)",  value="low", variable=self.res_preset)
        for w in (self.rb_high, self.rb_med, self.rb_low):
            w.pack(side="left", padx=8, pady=4)

        # 진행률/로그
        frm_prog = ttk.Frame(self); frm_prog.pack(fill="x", padx=10, pady=(4,0))
        self.pbar = ttk.Progressbar(frm_prog, mode="determinate", maximum=100, value=0)
        self.pbar.pack(fill="x")
        frm_log = ttk.Frame(self); frm_log.pack(fill="both", expand=True, padx=10, pady=6)
        self.txt_log = tk.Text(frm_log, height=12, wrap="word", state="disabled")
        self.txt_log.pack(side="left", fill="both", expand=True)
        sbar = ttk.Scrollbar(frm_log, command=self.txt_log.yview); sbar.pack(side="right", fill="y")
        self.txt_log.configure(yscrollcommand=sbar.set)

        # 하단
        frm_btn = ttk.Frame(self); frm_btn.pack(fill="x", padx=10, pady=8)
        ttk.Button(frm_btn, text="끝내기", command=self.destroy).pack(side="right")

        # 초기화
        last_dir = load_last_dir()
        if last_dir:
            self.set_current_dir(last_dir)

        self.log(
            "웹에서 유튜브 파일을 다운로드 받으면, 이상한 팝업과 백그라운드 광고도 뜨고 컴퓨터나 노트북에 악영향을 끼쳐서 그냥 직접 만들었습니다.",".",
            "-------------[사용법]-------------",
            "1. 유튜브 URL 창에 주소 붙여 넣기",
            "2. 저장 폴더 확인 및 선택, 한번 설정하면 자동완성",
            "3. 영상 또는 음악 파일 선택 → 해상도 필요 시 선택",
            "4. '다운로드 시작' 클릭.", 
            "5. 로그에 기록되며 다운로드 됨.",".",
            "윈도우 기본 미디어 장치에서도 재생 가능하도록 제작함.",".",
            "제작 : [상우] 독서하는 아랑t (개인적으로 사용할 것)"
        )
        self.load_clipboard()
        self.toggle_res_opts()

        self.after(100, self.process_messages)

    # ---------- 디렉터리 ----------
    def set_current_dir(self, d: str):
        d = os.path.abspath(os.path.normpath(d))
        self.current_dir = d
        self.lbl_dir.config(text=d)

    # ---------- 유틸 ----------
    def log(self, *lines):
        text = "\n".join(str(x) for x in lines)
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", text + ("" if text.endswith("\n") else "\n"))
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def set_progress(self, percent: float):
        self.pbar["value"] = max(0, min(100, percent))
        self.update_idletasks()

    def load_clipboard(self):
        if not pyperclip:
            return
        try:
            clip = pyperclip.paste().strip()
            if clip and YOUTUBE_REGEX.search(clip):
                self.ent_url.delete(0, "end"); self.ent_url.insert(0, clip)
        except Exception:
            pass

    def toggle_res_opts(self):
        state = "!disabled" if self.mode.get() == "video" else "disabled"
        for rb in (self.rb_high, self.rb_med, self.rb_low):
            rb.state([state])

    def choose_dir(self):
        d = filedialog.askdirectory(title="저장할 폴더 선택")
        if d:
            self.set_current_dir(d)
            save_last_dir(d)

    def open_dir(self):
        d = self.current_dir or self.lbl_dir.cget("text")
        if d and os.path.isdir(d):
            try:
                if sys.platform.startswith("win"):
                    os.startfile(d)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", d])
                else:
                    subprocess.Popen(["xdg-open", d])
            except Exception as e:
                self.log(f"[폴더 열기 실패] {e}")
        else:
            messagebox.showinfo("안내", "열 수 있는 저장 폴더가 없습니다. 먼저 폴더를 선택하세요.")

    # ---------- 실행 ----------
    def on_start(self):
        url = self.ent_url.get().strip()
        if not url or not YOUTUBE_REGEX.search(url):
            messagebox.showerror("오류", "유효한 YouTube URL을 입력하세요.")
            return
        outdir = self.current_dir or self.lbl_dir.cget("text")
        if outdir in ("", "(미선택)") or not os.path.isdir(outdir):
            messagebox.showerror("오류", "저장할 폴더를 선택하세요.")
            return
        filename = sanitize_filename(self.ent_name.get().strip())

        url = normalize_youtube_url(url)

        ffdir = ensure_ffmpeg_on_path()
        if not ffdir and not _ffmpeg_in_path():
            prompt_ffmpeg_download()
            messagebox.showerror("ffmpeg 필요", "ffmpeg.exe를 같은 폴더에 두거나 PATH에 등록하세요.")
            return

        # ★ 이전 기록 초기화
        self.last_finished_path = None

        ck = find_cookie_file()
        if ck:
            self.msg_q.put(("log", f"[쿠키] {ck} 사용"))

        self.set_progress(0)
        self.log("다운로드를 시작합니다...")
        threading.Thread(
            target=self.download_worker,
            args=(url, outdir, self.mode.get(), filename, self.res_preset.get()),
            daemon=True
        ).start()

    def process_messages(self):
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "progress":
                    self.set_progress(payload)
                elif kind == "done":
                    if payload.get("ok"):
                        final = payload.get("path")
                        if final and os.path.exists(final) and os.path.getsize(final) > 0:
                            self.log(f"[완료] {final}")
                            messagebox.showinfo("완료", f"다운로드가 완료되었습니다.\n{final}")
                        else:
                            self.log("[오류] 완료 표시였으나 파일이 확인되지 않습니다.")
                            messagebox.showerror("실패", "파일이 확인되지 않습니다.")
                    else:
                        self.log(payload.get("msg", "다운로드 실패"))
                        messagebox.showerror("실패", payload.get("msg", "다운로드 실패"))
                self.msg_q.task_done()
        except queue.Empty:
            pass
        self.after(100, self.process_messages)

    # yt-dlp 진행 콜백
    def ydl_progress_hook(self, d):
        try:
            if d.get('status') == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes') or 0
                percent = (downloaded / total * 100) if total else 0
                speed = d.get('speed'); eta = d.get('eta')
                txt = []
                if total: txt.append(f"{percent:.1f}%")
                if speed: txt.append(f"{speed/1024/1024:.2f} MB/s")
                if eta:   txt.append(f"ETA {int(eta)}s")
                if txt:   self.msg_q.put(("log", "[진행] " + " | ".join(txt)))
                self.msg_q.put(("progress", percent))
            elif d.get('status') == 'finished':
                # ★ yt-dlp가 알려주는 실제 생성 파일 경로를 기억
                self.last_finished_path = d.get('filename') or (d.get('info_dict') or {}).get('_filename')
                self.msg_q.put(("progress", 100.0))
                self.msg_q.put(("log", "[처리 중] 후처리 진행..."))
        except Exception:
            pass

    # ---- yt-dlp 옵션(403 완화 + cookies.txt) ----
    def build_ydl_opts(self, outpath, mode, ffdir, fmt_str, recode_to_mp4=False):
        UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

        ydl_opts = {
            "outtmpl": outpath,
            "noprogress": True,
            "progress_hooks": [self.ydl_progress_hook],
            "noplaylist": True,
            "windowsfilenames": True,

            # 네트워크/재시도(403 완화)
            "retries": 20,
            "fragment_retries": 20,
            "source_address": "0.0.0.0",        # IPv4 강제
            "concurrent_fragment_downloads": 1, # 동시 조각 1개
            "http_chunk_size": 2 * 1024 * 1024, # 2MB
            "retry_sleep_functions": {
                "http": ["exponential", 1, 2, 10],
                "fragment": ["exponential", 1, 2, 10],
            },

            # 포맷
            "format": fmt_str,
            "format_sort": ["res", "br", "vcodec:avc1", "acodec:mp4a", "ext:mp4:m4a"],
            "format_sort_force": True,

            # YouTube extractor 튜닝
            "extractor_retries": 5,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "web"],
                    "po_token": ["1"],
                }
            },

            # 브라우저 유사 헤더
            "http_headers": {
                "User-Agent": UA,
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        }

        if ffdir:
            ydl_opts["ffmpeg_location"] = ffdir

        ck = find_cookie_file()
        if ck:
            ydl_opts["cookiefile"] = ck

        if mode == "audio":
            ydl_opts.update({
                "postprocessors": [
                    {"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "0"}
                ],
                "format": "bestaudio/best",
            })
        else:
            if recode_to_mp4:
                ydl_opts["recodevideo"] = "mp4"
                ydl_opts["postprocessor_args"] = {
                    "FFmpegVideoConvertor": ["-c:v", "libx264", "-pix_fmt", "yuv420p",
                                             "-c:a", "aac", "-movflags", "+faststart"]
                }
            else:
                ydl_opts["merge_output_format"] = "mp4"
                ydl_opts["postprocessor_args"] = ["-c:v", "copy", "-c:a", "aac"]

        return ydl_opts

    def _try(self, idx, total, msg):
        self.msg_q.put(("log", f"[자동 호환성 검사] {idx}/{total} - {msg}"))

    def download_worker(self, url, outdir, mode, filename, res_preset):
        try:
            ffdir = ensure_ffmpeg_on_path()

            # 메타 추출(playlist 방지)
            with YoutubeDL({"quiet": True, "noplaylist": True}) as info_ydl:
                info = info_ydl.extract_info(url, download=False)
            title = info.get('title') or info.get('id') or 'video'
            vurl  = info.get('webpage_url') or url

            # 파일명(최종 확장자 기준) + 중복 넘버링
            res_prefix = RES_LABEL.get(res_preset, "")
            ext = "m4a" if mode == "audio" else "mp4"
            base = f"{res_prefix}{sanitize_filename(filename or title)}"
            final_path = unique_path(outdir, base, ext)
            self.msg_q.put(("log", f"[저장 경로] {final_path}"))

            cap_fmt, _ = FORMAT_PRESETS.get(res_preset, FORMAT_PRESETS["high"])

            # 폴백 단계
            attempts = []
            if mode == "video":
                attempts.append( (f"{cap_fmt}[vcodec^=avc1]+ba[acodec^=mp4a]", False, "H.264+AAC(가능하면) 우선") )
                attempts.append( (f"{cap_fmt}+ba/best", True, "코덱 무관 수집 → mp4 변환") )
                attempts.append( ("bv*[height<=720]+ba/best", True, "720p 이하 폴백 → mp4 변환") )
                attempts.append( ("bestvideo*+bestaudio/best", True, "최후 폴백 → mp4 변환") )
            else:
                attempts.append( ("bestaudio/best", False, "최적 오디오(m4a 추출)") )

            total = len(attempts)
            last_err = None
            for i, (fmt, recode, desc) in enumerate(attempts, start=1):
                try:
                    self._try(i, total, desc)
                    ydl_opts = self.build_ydl_opts(final_path, mode, ffdir, fmt, recode_to_mp4=recode)
                    with YoutubeDL(ydl_opts) as ydl:
                        ydl.download([vurl])

                    # ---- 결과 검증(보강: 생성된 파일을 우선 인정) ----
                    candidates = [final_path]
                    if self.last_finished_path:
                        candidates.append(self.last_finished_path)
                    neighbor = find_neighbor_output(outdir, os.path.splitext(os.path.basename(final_path))[0])
                    if neighbor:
                        candidates.append(neighbor)

                    picked = next((p for p in candidates
                                   if p and os.path.exists(p) and os.path.getsize(p) > 0), None)

                    if picked:
                        self.msg_q.put(("done", {"ok": True, "path": picked}))
                        return

                    # 여기까지 못 찾았으면 다음 단계 시도
                    raise Exception("다운로드/후처리 후 파일이 확인되지 않았습니다.")

                except Exception as e:
                    last_err = e
                    self.msg_q.put(("log", " - 불가, 다음 단계로 진행"))

            raise last_err if last_err else Exception("다운로드 가능한 형식을 찾지 못했습니다.")

        except Exception as e:
            self.msg_q.put(("log", f"[에러] {e}\n" + traceback.format_exc(limit=2)))
            if not IS_FROZEN:
                self.msg_q.put(("log", "[안내] yt-dlp를 최신으로 업데이트해 보세요:  pip install -U yt-dlp"))
            else:
                self.msg_q.put(("log", "[안내] yt-dlp 업데이트가 필요할 수 있습니다. 최신 yt-dlp로 EXE를 재빌드하세요."))
            self.msg_q.put(("done", {"ok": False, "msg": str(e)}))

def main():
    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()
