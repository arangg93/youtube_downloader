# -*- coding: utf-8 -*-
import sys, subprocess, importlib, os, re, threading, queue, json, webbrowser, shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

IS_FROZEN = getattr(sys, "frozen", False)

def install_import(pkg, name=None):
    try:
        return importlib.import_module(name or pkg)
    except ImportError:
        if IS_FROZEN: raise
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
        return importlib.import_module(name or pkg)

yt_dlp = install_import("yt-dlp", "yt_dlp")
try:
    pyperclip = install_import("pyperclip")
except: pyperclip = None

from yt_dlp import YoutubeDL

YT_RE = re.compile(r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/')

def cfg_path():
    d = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), "ArangYTDownloader")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "config.json")

def load_dir():
    try:
        if os.path.exists(p := cfg_path()):
            with open(p, "r", encoding="utf-8") as f:
                if (d := json.load(f).get("last_dir", "")) and os.path.isdir(d):
                    return d
    except: pass
    return d if os.path.isdir(d := os.path.join(os.path.expanduser("~"), "Downloads")) else ""

def save_dir(d):
    try:
        with open(cfg_path(), "w", encoding="utf-8") as f:
            json.dump({"last_dir": d}, f)
    except: pass

def find_ffmpeg():
    for d in [os.path.dirname(getattr(sys, "executable", sys.argv[0])), os.getcwd()]:
        for n in ("ffmpeg.exe", "ffmpeg"):
            if os.path.exists(os.path.join(d, n)):
                return d
    return None

def setup_ffmpeg():
    if d := find_ffmpeg():
        if d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        return d
    return None

def has_ffmpeg():
    return shutil.which("ffmpeg.exe" if os.name == "nt" else "ffmpeg") is not None

def prompt_ffmpeg():
    if messagebox.askyesno("ffmpeg 필요", 
        "ffmpeg가 필요합니다.\n\n다운로드 페이지를 여시겠습니까?\n"
        "(Windows용 ffmpeg.exe를 프로그램과 같은 폴더에 두세요)"):
        try: webbrowser.open("https://www.gyan.dev/ffmpeg/builds/")
        except: pass

def find_cookies():
    for d in [os.path.dirname(getattr(sys, "executable", sys.argv[0])), os.getcwd()]:
        if os.path.isfile(p := os.path.join(d, "cookies.txt")) and os.path.getsize(p) > 0:
            return p
    return None

def norm_url(u):
    if "?" not in u: return u
    base, qs = u.split("?", 1)
    keep = [p for p in qs.split("&") if p.split("=")[0].lower() not in 
            ("list", "index", "start_radio", "pp", "si")]
    return base + ("?" + "&".join(keep) if keep else "")

def clean_name(n):
    return re.sub(r"\s+", " ", re.sub(r"[\\/:*?\"<>|]+", " ", n)).strip()[:180]

FMT = {"high": ("bv*[height<=1080]", 1080), "medium": ("bv*[height<=720]", 720), 
       "low": ("bv*[height<=480]", 480)}
RES_LBL = {"high": "(상) ", "medium": "(중) ", "low": "(하) "}

def unique(d, base, ext):
    base = clean_name(base)
    if not os.path.exists(p := os.path.join(d, f"{base}.{ext}")): return p
    i = 1
    while os.path.exists(p := os.path.join(d, f"{base} ({i:02d}).{ext}")): i += 1
    return p

def find_file(d, base):
    base = clean_name(base)
    try:
        files = [(os.path.join(d, f), os.path.getmtime(os.path.join(d, f))) 
                 for f in os.listdir(d)
                 if os.path.isfile(fp := os.path.join(d, f)) and
                 os.path.splitext(f)[1].lower() in (".mp4",".mkv",".webm",".m4a",".mov",".mp3") and
                 (os.path.splitext(f)[0] == base or re.fullmatch(rf"{re.escape(base)} \(\d+\)", os.path.splitext(f)[0]))]
        return max(files, key=lambda x: x[1])[0] if files else None
    except: return None

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("아랑의 Youtube 다운로더 v1.3")
        self.geometry("420x640")
        self.minsize(360, 520)
        
        self.msg_q = queue.Queue()
        self.cur_dir = ""
        self.last_path = None
        
        frm = ttk.Frame(self); frm.pack(fill="x", padx=10, pady=(10,6))
        ttk.Label(frm, text="YouTube URL").pack(anchor="w")
        r = ttk.Frame(frm); r.pack(fill="x")
        self.url = ttk.Entry(r); self.url.pack(side="left", fill="x", expand=True)
        self.btn = ttk.Button(r, text="다운로드", command=self.start)
        self.btn.pack(side="left", padx=6)
        
        frm = ttk.Frame(self); frm.pack(fill="x", padx=10, pady=6)
        ttk.Label(frm, text="저장 폴더").pack(anchor="w")
        r = ttk.Frame(frm); r.pack(fill="x")
        self.dir_lbl = ttk.Label(r, text="(미선택)", width=26, relief="sunken", anchor="w")
        self.dir_lbl.pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="찾기", command=self.pick_dir).pack(side="left", padx=(6,0))
        ttk.Button(r, text="열기", command=self.open_dir).pack(side="left", padx=(6,0))
        
        frm = ttk.Frame(self); frm.pack(fill="x", padx=10, pady=6)
        ttk.Label(frm, text="파일이름(선택)").pack(anchor="w")
        self.name = ttk.Entry(frm); self.name.pack(fill="x")
        
        g = ttk.LabelFrame(self, text="형식"); g.pack(fill="x", padx=10, pady=(8,4))
        self.mode = tk.StringVar(value="video")
        ttk.Radiobutton(g, text="영상", value="video", variable=self.mode, 
                        command=self.toggle).pack(side="left", padx=8, pady=4)
        ttk.Radiobutton(g, text="음성", value="audio", variable=self.mode,
                        command=self.toggle).pack(side="left", padx=8, pady=4)
        
        g = ttk.LabelFrame(self, text="해상도"); g.pack(fill="x", padx=10, pady=(4,8))
        self.res = tk.StringVar(value="high")
        self.rb = [ttk.Radiobutton(g, text=t, value=v, variable=self.res) 
                   for t, v in [("상≤1080p", "high"), ("중≤720p", "medium"), ("하≤480p", "low")]]
        for w in self.rb: w.pack(side="left", padx=8, pady=4)
        
        frm = ttk.Frame(self); frm.pack(fill="x", padx=10, pady=(4,0))
        self.prog = ttk.Progressbar(frm, mode="determinate", maximum=100)
        self.prog.pack(fill="x")
        
        frm = ttk.Frame(self); frm.pack(fill="both", expand=True, padx=10, pady=6)
        self.log = tk.Text(frm, height=12, wrap="word", state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        s = ttk.Scrollbar(frm, command=self.log.yview); s.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=s.set)
        
        ttk.Button(self, text="끝내기", command=self.destroy).pack(side="right", padx=10, pady=8)
        
        if d := load_dir():
            self.set_dir(d)
        
        self.write(
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
        self.load_clip()
        self.toggle()
        self.after(100, self.process)
    
    def set_dir(self, d):
        self.cur_dir = os.path.abspath(d)
        self.dir_lbl.config(text=self.cur_dir)
    
    def write(self, *lines):
        self.log.configure(state="normal")
        self.log.insert("end", "\n".join(str(x) for x in lines) + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
    
    def set_prog(self, v):
        self.prog["value"] = max(0, min(100, v))
        self.update_idletasks()
    
    def load_clip(self):
        if not pyperclip: return
        try:
            if (c := pyperclip.paste().strip()) and YT_RE.search(c):
                self.url.delete(0, "end")
                self.url.insert(0, c)
        except: pass
    
    def toggle(self):
        st = "!disabled" if self.mode.get() == "video" else "disabled"
        for w in self.rb: w.state([st])
    
    def pick_dir(self):
        if d := filedialog.askdirectory(title="저장 폴더"):
            self.set_dir(d)
            save_dir(d)
    
    def open_dir(self):
        d = self.cur_dir or self.dir_lbl.cget("text")
        if d and os.path.isdir(d):
            try:
                if sys.platform.startswith("win"): os.startfile(d)
                elif sys.platform == "darwin": subprocess.Popen(["open", d])
                else: subprocess.Popen(["xdg-open", d])
            except Exception as e: self.write(f"[오류] {e}")
        else: messagebox.showinfo("안내", "폴더를 먼저 선택하세요")
    
    def start(self):
        u = self.url.get().strip()
        if not u or not YT_RE.search(u):
            return messagebox.showerror("오류", "올바른 YouTube URL을 입력하세요")
        d = self.cur_dir or self.dir_lbl.cget("text")
        if d in ("", "(미선택)") or not os.path.isdir(d):
            return messagebox.showerror("오류", "저장 폴더를 선택하세요")
        
        if not setup_ffmpeg() and not has_ffmpeg():
            prompt_ffmpeg()
            return messagebox.showerror("ffmpeg 필요", "ffmpeg.exe가 필요합니다")
        
        self.last_path = None
        if c := find_cookies():
            self.msg_q.put(("log", f"[쿠키] {c}"))
        
        self.set_prog(0)
        self.write("다운로드 시작...")
        threading.Thread(target=self.work, 
                        args=(norm_url(u), d, self.mode.get(), 
                              clean_name(self.name.get().strip()), self.res.get()),
                        daemon=True).start()
    
    def process(self):
        try:
            while True:
                k, v = self.msg_q.get_nowait()
                if k == "log": self.write(v)
                elif k == "prog": self.set_prog(v)
                elif k == "done":
                    if v.get("ok"):
                        if (p := v.get("path")) and os.path.exists(p) and os.path.getsize(p) > 0:
                            self.write(f"[완료] {p}")
                            messagebox.showinfo("완료", f"다운로드 완료\n{p}")
                        else:
                            self.write("[오류] 파일 확인 실패")
                            messagebox.showerror("실패", "파일 확인 실패")
                    else:
                        self.write(v.get("msg", "실패"))
                        messagebox.showerror("실패", v.get("msg", "실패"))
                self.msg_q.task_done()
        except queue.Empty: pass
        self.after(100, self.process)
    
    def hook(self, d):
        try:
            if d.get('status') == 'downloading':
                tot = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                dl = d.get('downloaded_bytes') or 0
                pct = (dl / tot * 100) if tot else 0
                self.msg_q.put(("prog", pct))
            elif d.get('status') == 'finished':
                self.last_path = d.get('filename') or (d.get('info_dict') or {}).get('_filename')
                self.msg_q.put(("prog", 100))
        except: pass
    
    def opts(self, out, mode, ff, fmt, recode=False):
        o = {
            "outtmpl": out, "noprogress": True, "progress_hooks": [self.hook],
            "noplaylist": True, "windowsfilenames": True, "retries": 20,
            "fragment_retries": 20, "source_address": "0.0.0.0",
            "concurrent_fragment_downloads": 1, "http_chunk_size": 2097152,
            "format": fmt, "format_sort": ["res","br","vcodec:avc1","acodec:mp4a","ext:mp4:m4a"],
            "format_sort_force": True, "extractor_retries": 5,
            "extractor_args": {"youtube": {"player_client": ["android","web"], "po_token": ["1"]}},
            "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        }
        if ff: o["ffmpeg_location"] = ff
        if c := find_cookies(): o["cookiefile"] = c
        
        if mode == "audio":
            o.update({"postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
                     "format": "bestaudio/best"})
        else:
            if recode:
                o["recodevideo"] = "mp4"
                o["postprocessor_args"] = {"FFmpegVideoConvertor": 
                    ["-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-movflags","+faststart"]}
            else:
                o["merge_output_format"] = "mp4"
                o["postprocessor_args"] = ["-c:v","copy","-c:a","aac"]
        return o
    
    def work(self, url, d, mode, name, res):
        try:
            ff = setup_ffmpeg()
            with YoutubeDL({"quiet": True, "noplaylist": True}) as y:
                info = y.extract_info(url, download=False)
            title = info.get('title') or info.get('id') or 'video'
            
            ext = "m4a" if mode == "audio" else "mp4"
            base = f"{RES_LBL.get(res, '')}{clean_name(name or title)}"
            out = unique(d, base, ext)
            self.msg_q.put(("log", f"[저장] {out}"))
            
            cap, _ = FMT.get(res, FMT["high"])
            
            attempts = []
            if mode == "video":
                attempts = [(f"{cap}[vcodec^=avc1]+ba[acodec^=mp4a]", False, "H.264+AAC"),
                           (f"{cap}+ba/best", True, "변환"),
                           ("bv*[height<=720]+ba/best", True, "720p"),
                           ("bestvideo*+bestaudio/best", True, "최후")]
            else:
                attempts = [("bestaudio/best", False, "오디오")]
            
            err = None
            for i, (fmt, rec, desc) in enumerate(attempts, 1):
                try:
                    self.msg_q.put(("log", f"[{i}/{len(attempts)}] {desc}"))
                    with YoutubeDL(self.opts(out, mode, ff, fmt, rec)) as y:
                        y.download([info.get('webpage_url') or url])
                    
                    cand = [out]
                    if self.last_path: cand.append(self.last_path)
                    if n := find_file(d, os.path.splitext(os.path.basename(out))[0]):
                        cand.append(n)
                    
                    if p := next((x for x in cand if x and os.path.exists(x) and os.path.getsize(x) > 0), None):
                        return self.msg_q.put(("done", {"ok": True, "path": p}))
                    
                    raise Exception("파일 없음")
                except Exception as e:
                    err = e
            
            raise err or Exception("실패")
        except Exception as e:
            self.msg_q.put(("log", f"[에러] {e}"))
            if not IS_FROZEN:
                self.msg_q.put(("log", "pip install -U yt-dlp 시도"))
            self.msg_q.put(("done", {"ok": False, "msg": str(e)}))

if __name__ == "__main__":
    App().mainloop()