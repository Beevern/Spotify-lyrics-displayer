#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spotify Lyrics Overlay
======================
Displays the current and next lyric line of your Spotify song
in a frameless, always-on-top, resizable overlay window.

Setup:
  1. pip install -r requirements.txt
  2. Create an app at https://developer.spotify.com/dashboard
     - Check: Web API
     - Set Redirect URI to: http://127.0.0.1:8888/callback
  3. Run this script — a setup dialog will ask for your credentials
     on first launch, then save them to config.py
"""

import tkinter as tk
import threading
import time
import re
import os
import sys

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'spotipy', 'requests'])
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    import requests

# ── Credentials ───────────────────────────────────────────────────────────────
try:
    import config as _cfg
    CLIENT_ID     = _cfg.CLIENT_ID
    CLIENT_SECRET = _cfg.CLIENT_SECRET
except (ImportError, AttributeError):
    CLIENT_ID     = os.environ.get('SPOTIFY_CLIENT_ID',     '')
    CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET', '')

REDIRECT_URI  = 'http://127.0.0.1:8888/callback'
SCOPE         = ('user-read-currently-playing '
                 'user-read-playback-state '
                 'user-modify-playback-state')
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
    root.title('Spotify Lyrics — First-time Setup')
    root.geometry('520x320')
    root.resizable(False, False)
    root.configure(bg='#0d0d0d')
    root.attributes('-topmost', True)

    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f'520x320+{(sw-520)//2}+{(sh-320)//2}')

    tk.Label(root, text='Spotify Lyrics Overlay', bg='#0d0d0d', fg='#1DB954',
             font=('Segoe UI', 16, 'bold')).pack(pady=(24, 4))
    tk.Label(root, text='Enter your Spotify Developer credentials below.',
             bg='#0d0d0d', fg='#aaaaaa', font=('Segoe UI', 10)).pack()
    tk.Label(root, text='developer.spotify.com/dashboard  →  Create app  →  copy IDs',
             bg='#0d0d0d', fg='#555555', font=('Segoe UI', 9)).pack(pady=(2, 18))

    frm = tk.Frame(root, bg='#0d0d0d')
    frm.pack(padx=40, fill='x')

    def _row(label, row, show=''):
        tk.Label(frm, text=label, bg='#0d0d0d', fg='#cccccc',
                 font=('Segoe UI', 10), width=14, anchor='e').grid(
                     row=row, column=0, sticky='e', pady=6)
        var = tk.StringVar()
        tk.Entry(frm, textvariable=var, width=36, bg='#1e1e1e', fg='white',
                 insertbackground='white', relief='flat', bd=4,
                 font=('Consolas', 10), show=show).grid(
                     row=row, column=1, padx=(10, 0), pady=6)
        return var

    id_var     = _row('Client ID',     0)
    secret_var = _row('Client Secret', 1, show='•')

    err_lbl = tk.Label(root, text='', bg='#0d0d0d', fg='#ff6b6b',
                       font=('Segoe UI', 9))
    err_lbl.pack()

    def save_and_start():
        cid = id_var.get().strip()
        sec = secret_var.get().strip()
        if not cid or not sec:
            err_lbl.config(text='Both fields are required.')
            return
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.py')
        with open(cfg_path, 'w') as f:
            f.write(f'CLIENT_ID     = "{cid}"\n')
            f.write(f'CLIENT_SECRET = "{sec}"\n')
        root.destroy()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    tk.Button(root, text='Save & Launch', command=save_and_start,
              bg='#1DB954', fg='white', activebackground='#17a347',
              font=('Segoe UI', 11, 'bold'), padx=24, pady=10,
              bd=0, cursor='hand2', relief='flat').pack(pady=16)

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
        self._init_spotify()
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

        # Close — packed first so it sits at the far right
        close_btn = tk.Button(bar, text='✕', bg=self.BAR_BG, fg='#555555',
                              font=('Segoe UI', 10), bd=0, padx=10, pady=0,
                              activebackground='#c0392b', activeforeground='white',
                              cursor='arrow', command=root.quit, relief='flat')
        close_btn.pack(side='right')
        close_btn.bind('<Enter>', lambda e: close_btn.config(bg='#c0392b', fg='white'))
        close_btn.bind('<Leave>', lambda e: close_btn.config(bg=self.BAR_BG, fg='#555555'))

        # Opacity slider — to the left of close
        tk.Label(bar, text='opacity', bg=self.BAR_BG, fg='#606060',
                 font=('Segoe UI', 7)).pack(side='right', padx=(0, 2))
        self.alpha_var = tk.DoubleVar(value=0.92)
        tk.Scale(bar, from_=0.15, to=1.0, resolution=0.05,
                 orient='horizontal', variable=self.alpha_var,
                 bg=self.BAR_BG, fg='#888888',
                 troughcolor='#ffffff',
                 activebackground='#aaaaaa',
                 highlightthickness=1, highlightcolor='#ffffff',
                 highlightbackground='#ffffff',
                 bd=0, sliderrelief='flat', width=6,
                 length=80, showvalue=False, cursor='arrow',
                 command=lambda v: root.attributes('-alpha', float(v))
                 ).pack(side='right', padx=(4, 0))

        # Drag + double-click to collapse
        for w in (bar, self.track_lbl):
            w.bind('<Button-1>',        self._drag_start)
            w.bind('<B1-Motion>',       self._drag_move)
            w.bind('<Double-Button-1>', self._toggle_collapse)

        # Lyrics frame — click to play/pause
        self.lf = tk.Frame(root, bg=self.BG, cursor='hand2')
        self.lf.pack(fill='both', expand=True, padx=14, pady=(8, 6))

        self.cur_lbl = tk.Label(
            self.lf, text='Waiting for Spotify…',
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

    # ── Collapse / expand ─────────────────────────────────────────────────────
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
        if not self.sp:
            return
        if self._is_playing:
            threading.Thread(target=self.sp.pause_playback, daemon=True).start()
        else:
            threading.Thread(target=self.sp.start_playback, daemon=True).start()
        self._is_playing = not self._is_playing

    # ── Spotify ───────────────────────────────────────────────────────────────
    def _init_spotify(self):
        try:
            self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                redirect_uri=REDIRECT_URI,
                scope=SCOPE,
                open_browser=True,
                cache_path=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        '.spotify_cache')
            ))
        except Exception as exc:
            self.sp = None
            self._set_display(f'Auth error: {exc}', '')

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

    # ── Polling thread ────────────────────────────────────────────────────────
    def _poll(self):
        while True:
            try:
                if self.sp:
                    pb = self.sp.current_playback()
                    if pb and pb.get('item'):
                        track  = pb['item']
                        tid    = track['id']
                        pos    = pb.get('progress_ms', 0)
                        name   = track['name']
                        artist = track['artists'][0]['name']
                        self._is_playing = pb.get('is_playing', False)

                        if tid != self.current_track_id:
                            self.current_track_id = tid
                            self.lyrics = []
                            self.root.after(0, self.track_lbl.config,
                                            {'text': f'♫  {name}  —  {artist}'})
                            self.lyrics = self._fetch_lyrics(
                                name, artist, track['duration_ms'])

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
                self.root.after(0, self._set_display, '⚠  Error', str(exc)[:70])
                time.sleep(4)
                continue

            time.sleep(POLL_INTERVAL)

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
    if not CLIENT_ID or not CLIENT_SECRET:
        run_setup()
    else:
        LyricsOverlay().run()
