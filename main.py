#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spotify Lyrics Overlay — Client
Connects to a proxy server. No Spotify credentials needed locally.

Setup (first run):
  Enter the server URL your administrator gave you, e.g.
  https://spotify-lyrics.example.com
  A browser will open once to log in with your Spotify account.
"""

import tkinter as tk
import threading
import time
import re
import os
import sys
import uuid
import webbrowser

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'requests'])
    import requests

# ── Config ────────────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
_CFG = os.path.join(_DIR, 'config.py')

try:
    import config as _cfg
    SERVER_URL  = _cfg.SERVER_URL.rstrip('/')
    CLIENT_UUID = _cfg.CLIENT_UUID
except (ImportError, AttributeError):
    SERVER_URL  = ''
    CLIENT_UUID = ''

POLL_INTERVAL = 0.5


# ── LRC Parser ────────────────────────────────────────────────────────────────
_LRC_RE = re.compile(r'\[(\d{1,3}):(\d{2})\.(\d{1,3})\](.*)')

def parse_lrc(lrc_text: str) -> list[tuple[int, str]]:
    lines = []
    for raw in lrc_text.splitlines():
        m = _LRC_RE.match(raw.strip())
        if m:
            mins, secs, frac, text = m.groups()
            frac_ms = int(frac.ljust(3, '0')[:3])
            ms = (int(mins) * 60 + int(secs)) * 1000 + frac_ms
            if text.strip():
                lines.append((ms, text.strip()))
    return sorted(lines, key=lambda x: x[0])


# ── Setup Dialog ──────────────────────────────────────────────────────────────
def run_setup():
    root = tk.Tk()
    root.title('Spotify Lyrics — Setup')
    root.geometry('480x240')
    root.resizable(False, False)
    root.configure(bg='#0d0d0d')
    root.attributes('-topmost', True)

    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f'480x240+{(sw-480)//2}+{(sh-240)//2}')

    tk.Label(root, text='Spotify Lyrics Overlay', bg='#0d0d0d', fg='#1DB954',
             font=('Segoe UI', 16, 'bold')).pack(pady=(22, 4))
    tk.Label(root, text='Enter the server URL provided by your administrator.',
             bg='#0d0d0d', fg='#aaaaaa', font=('Segoe UI', 10)).pack()
    tk.Label(root, text='e.g.  https://spotify-lyrics.example.com',
             bg='#0d0d0d', fg='#555555', font=('Segoe UI', 9)).pack(pady=(2, 18))

    frm = tk.Frame(root, bg='#0d0d0d')
    frm.pack(padx=40, fill='x')
    tk.Label(frm, text='Server URL', bg='#0d0d0d', fg='#cccccc',
             font=('Segoe UI', 10), width=12, anchor='e').grid(
                 row=0, column=0, sticky='e', pady=6)
    url_var = tk.StringVar()
    tk.Entry(frm, textvariable=url_var, width=34, bg='#1e1e1e', fg='white',
             insertbackground='white', relief='flat', bd=4,
             font=('Consolas', 10)).grid(row=0, column=1, padx=(10, 0), pady=6)

    err_lbl = tk.Label(root, text='', bg='#0d0d0d', fg='#ff6b6b',
                       font=('Segoe UI', 9))
    err_lbl.pack()

    def save_and_start():
        url = url_var.get().strip().rstrip('/')
        if not url:
            err_lbl.config(text='Server URL is required.')
            return
        with open(_CFG, 'w') as f:
            f.write(f'SERVER_URL  = "{url}"\n')
            f.write(f'CLIENT_UUID = "{uuid.uuid4()}"\n')
        root.destroy()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    tk.Button(root, text='Save & Launch', command=save_and_start,
              bg='#1DB954', fg='white', activebackground='#17a347',
              font=('Segoe UI', 11, 'bold'), padx=24, pady=10,
              bd=0, cursor='hand2', relief='flat').pack(pady=14)

    root.mainloop()
    sys.exit(0)


# ── Main Overlay ──────────────────────────────────────────────────────────────
class LyricsOverlay:
    BG     = '#0d0d0d'
    BAR_BG = '#161616'
    CUR_FG = '#ffffff'
    NXT_FG = '#3a3a3a'
    FONT   = ('Segoe UI', 15)

    def __init__(self):
        self.root = tk.Tk()
        self.root.title('Lyrics')
        self.root.configure(bg=self.BG)
        self.root.attributes('-topmost', True)
        self.root.overrideredirect(True)
        self.root.attributes('-alpha', 0.92)

        self.current_track_id: str | None = None
        self.lyrics: list[tuple[int, str]] = []
        self._is_playing  = False
        self._collapsed   = False
        self._prev_height = 130
        self._drag_ox = self._drag_oy = 0
        self._rsz: tuple | None = None

        self._build_ui()
        self._authenticate()
        self._start_poll()
        self._place_window()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = self.root

        # Title bar
        bar = tk.Frame(root, bg=self.BAR_BG, height=26)
        bar.pack(fill='x', side='top')
        bar.pack_propagate(False)

        self.track_lbl = tk.Label(
            bar, text='♫  Spotify Lyrics',
            bg=self.BAR_BG, fg='#505050',
            font=('Segoe UI', 8), anchor='w')
        self.track_lbl.pack(side='left', padx=10, fill='y')

        # Opacity slider
        self.alpha_var = tk.DoubleVar(value=0.92)
        tk.Scale(bar, from_=0.15, to=1.0, resolution=0.05,
                 orient='horizontal', variable=self.alpha_var,
                 bg=self.BAR_BG, fg='#505050', troughcolor='#2a2a2a',
                 highlightthickness=0, bd=0, sliderrelief='flat',
                 length=70, showvalue=False, cursor='arrow',
                 command=lambda v: root.attributes('-alpha', float(v))
                 ).pack(side='right', padx=4)
        tk.Label(bar, text='opacity', bg=self.BAR_BG, fg='#404040',
                 font=('Segoe UI', 7)).pack(side='right')

        # Close
        tk.Button(bar, text='✕', bg=self.BAR_BG, fg='#555555',
                  font=('Segoe UI', 10), bd=0, padx=8, pady=0,
                  activebackground='#c0392b', activeforeground='white',
                  cursor='arrow', command=root.quit, relief='flat').pack(side='right')

        # Drag + double-click collapse on the whole bar
        for w in (bar, self.track_lbl):
            w.bind('<Button-1>',        self._drag_start)
            w.bind('<B1-Motion>',       self._drag_move)
            w.bind('<Double-Button-1>', self._toggle_collapse)

        # Lyrics frame — clicking toggles play/pause
        self.lf = tk.Frame(root, bg=self.BG, cursor='hand2')
        self.lf.pack(fill='both', expand=True, padx=14, pady=(8, 6))

        self.cur_lbl = tk.Label(
            self.lf, text='Connecting…',
            bg=self.BG, fg=self.CUR_FG,
            font=self.FONT,
            anchor='center', justify='center', wraplength=660)
        self.cur_lbl.pack(fill='x', expand=True)

        self.nxt_lbl = tk.Label(
            self.lf, text='',
            bg=self.BG, fg=self.NXT_FG,
            font=self.FONT,
            anchor='center', justify='center', wraplength=660)
        self.nxt_lbl.pack(fill='x', expand=True)

        for w in (self.lf, self.cur_lbl, self.nxt_lbl):
            w.bind('<Button-1>', self._click_lyrics)

        # Resize grip
        self.grip = tk.Label(root, text='⠿', bg=self.BG, fg='#2a2a2a',
                              cursor='size_nw_se', font=('', 11))
        self.grip.place(relx=1.0, rely=1.0, anchor='se', x=-3, y=-2)
        self.grip.bind('<Button-1>',  self._rsz_start)
        self.grip.bind('<B1-Motion>', self._rsz_move)

        root.bind('<Configure>', self._on_resize)

    def _on_resize(self, _e):
        wrap = max(100, self.root.winfo_width() - 28)
        self.cur_lbl.config(wraplength=wrap)
        self.nxt_lbl.config(wraplength=wrap)

    # ── Drag ─────────────────────────────────────────────────────────────────
    def _drag_start(self, e):
        self._drag_ox = e.x_root - self.root.winfo_x()
        self._drag_oy = e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f'+{e.x_root - self._drag_ox}+{e.y_root - self._drag_oy}')

    # ── Collapse / expand on double-click ────────────────────────────────────
    def _toggle_collapse(self, _e):
        w = self.root.winfo_width()
        if self._collapsed:
            self.lf.pack(fill='both', expand=True, padx=14, pady=(8, 6))
            self.grip.place(relx=1.0, rely=1.0, anchor='se', x=-3, y=-2)
            self.root.geometry(f'{w}x{self._prev_height}')
            self._collapsed = False
        else:
            self._prev_height = self.root.winfo_height()
            self.lf.pack_forget()
            self.grip.place_forget()
            self.root.geometry(f'{w}x26')
            self._collapsed = True

    # ── Resize ────────────────────────────────────────────────────────────────
    def _rsz_start(self, e):
        self._rsz = (e.x_root, e.y_root,
                     self.root.winfo_width(), self.root.winfo_height())

    def _rsz_move(self, e):
        if not self._rsz:
            return
        sx, sy, sw, sh = self._rsz
        self.root.geometry(f'{max(300, sw + e.x_root - sx)}x{max(90, sh + e.y_root - sy)}')

    # ── Click lyrics = play / pause ───────────────────────────────────────────
    def _click_lyrics(self, _e):
        action = 'pause' if self._is_playing else 'play'
        self._is_playing = not self._is_playing
        threading.Thread(
            target=lambda: requests.post(
                f'{SERVER_URL}/api/{action}',
                params={'client_id': CLIENT_UUID},
                timeout=5),
            daemon=True).start()

    # ── Auth ──────────────────────────────────────────────────────────────────
    def _authenticate(self):
        try:
            r = requests.get(
                f'{SERVER_URL}/api/status',
                params={'client_id': CLIENT_UUID},
                timeout=5)
            if r.json().get('authenticated'):
                return
        except Exception:
            pass
        webbrowser.open(f'{SERVER_URL}/login?client_id={CLIENT_UUID}')
        self._set_display('Waiting for Spotify login…', 'Check your browser')

    # ── Polling thread ────────────────────────────────────────────────────────
    def _poll(self):
        while True:
            try:
                r    = requests.get(f'{SERVER_URL}/api/playback',
                                    params={'client_id': CLIENT_UUID}, timeout=5)
                data = r.json()

                if data.get('error') == 'not_authenticated':
                    self.root.after(0, self._set_display,
                                    'Waiting for login…', 'Check your browser')
                    time.sleep(2)
                    continue

                item = data.get('item')
                if item:
                    tid    = item['id']
                    pos    = data.get('progress_ms', 0)
                    name   = item['name']
                    artist = item['artists'][0]['name']
                    self._is_playing = data.get('is_playing', False)

                    if tid != self.current_track_id:
                        self.current_track_id = tid
                        self.lyrics = []
                        self.root.after(0, self.track_lbl.config,
                                        {'text': f'♫  {name}  —  {artist}'})
                        self.lyrics = self._fetch_lyrics(
                            name, artist, item['duration_ms'])

                    if not self._is_playing:
                        self.root.after(0, self._set_display, '⏸  Paused', '')
                    elif self.lyrics:
                        i   = self._line_index(self.lyrics, pos)
                        cur = self.lyrics[i][1]
                        nxt = self.lyrics[i + 1][1] if i + 1 < len(self.lyrics) else ''
                        self.root.after(0, self._set_display, cur, nxt)
                    else:
                        self.root.after(0, self._set_display,
                                        f'♫  {name}',
                                        f'by {artist}   (lyrics not found)')
                else:
                    self.root.after(0, self._set_display, '♫  Nothing playing', '')

            except Exception as exc:
                print(f'[poll] {exc}')
                self.root.after(0, self._set_display, '⚠  Server error', str(exc)[:60])
                time.sleep(4)
                continue

            time.sleep(POLL_INTERVAL)

    def _fetch_lyrics(self, title: str, artist: str, duration_ms: int):
        try:
            r = requests.get(
                'https://lrclib.net/api/get',
                params={'track_name': title, 'artist_name': artist,
                        'duration': duration_ms // 1000},
                timeout=7)
            if r.status_code == 200:
                data = r.json()
                if data.get('syncedLyrics'):
                    return parse_lrc(data['syncedLyrics'])
                if data.get('plainLyrics'):
                    raw  = [l for l in data['plainLyrics'].splitlines() if l.strip()]
                    step = max(2000, duration_ms // max(len(raw), 1))
                    return [(i * step, l) for i, l in enumerate(raw)]
        except Exception as exc:
            print(f'[lyrics] {exc}')
        return []

    @staticmethod
    def _line_index(lyrics: list, pos_ms: int) -> int:
        idx = 0
        for i, (t, _) in enumerate(lyrics):
            if t <= pos_ms:
                idx = i
            else:
                break
        return idx

    def _start_poll(self):
        threading.Thread(target=self._poll, daemon=True).start()

    def _set_display(self, current: str, nxt: str):
        self.cur_lbl.config(text=current)
        self.nxt_lbl.config(text=nxt)

    def _place_window(self):
        W, H = 700, 130
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f'{W}x{H}+{(sw - W) // 2}+{sh - H - 80}')

    def run(self):
        self.root.mainloop()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    if not SERVER_URL or not CLIENT_UUID:
        run_setup()
    else:
        LyricsOverlay().run()
