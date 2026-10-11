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
import socket
import ctypes

# Cap ALL network calls at 15 seconds. Spotipy's OAuth token-refresh has no
# built-in timeout, so without this the poll thread could hang indefinitely.
socket.setdefaulttimeout(15)

# Try to import required packages; if missing, auto-install them via pip.
try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    import requests
    import keyboard  # global hotkey listener for game mode (works without window focus)
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'spotipy', 'requests', 'keyboard'])
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    import requests
    import keyboard

# ── Credentials ───────────────────────────────────────────────────────────────
# Load credentials from config.py if it exists; otherwise fall back to
# environment variables. If neither is set the setup dialog will run first.
try:
    import config as _cfg
    CLIENT_ID     = _cfg.CLIENT_ID
    CLIENT_SECRET = _cfg.CLIENT_SECRET
except (ImportError, AttributeError):
    CLIENT_ID     = os.environ.get('SPOTIFY_CLIENT_ID',     '')
    CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET', '')

REDIRECT_URI  = 'http://127.0.0.1:8888/callback'   # must match Spotify dashboard setting
SCOPE         = ('user-read-currently-playing '     # read currently playing track
                 'user-read-playback-state '         # read play/pause state
                 'user-modify-playback-state')       # allow play/pause from this app
POLL_INTERVAL = 2   # seconds between Spotify API calls

# Placeholder keybind for toggling game mode — change to whatever's convenient.
# Uses the `keyboard` lib's global-hotkey syntax: https://github.com/boppreh/keyboard
GAME_MODE_HOTKEY = 'ctrl+alt+g'

# Win32 constants used to make the overlay click-through in game mode.
# (Game mode is a no-op on non-Windows platforms.)
GWL_EXSTYLE        = -20
WS_EX_LAYERED      = 0x00080000
WS_EX_TRANSPARENT  = 0x00000020


# ── LRC Parser ────────────────────────────────────────────────────────────────
# Matches LRC timestamp lines like: [01:23.456] Some lyric text
_LRC_RE = re.compile(r'\[(\d{1,3}):(\d{2})\.(\d{1,3})\](.*)')

def parse_lrc(lrc_text: str) -> list[tuple[int, str]]:
    """Parse an LRC-format lyrics string into a list of (milliseconds, text) tuples.

    LRC format uses timestamps like [mm:ss.ms] before each lyric line.
    Returns the list sorted by time so _line_index can scan it in order.
    """
    lines = []
    for raw in lrc_text.splitlines():
        m = _LRC_RE.match(raw.strip())
        if m:
            mins, secs, frac, text = m.groups()
            # Left-pad the fractional part to 3 digits so it always represents milliseconds
            frac_ms = int(frac.ljust(3, '0')[:3])
            ms = (int(mins) * 60 + int(secs)) * 1000 + frac_ms
            if text.strip():  # skip blank lyric lines (instrumental gaps)
                lines.append((ms, text.strip()))
    return sorted(lines, key=lambda x: x[0])


# ── Setup Dialog ──────────────────────────────────────────────────────────────
def run_setup():
    """Show a first-time setup dialog that collects Spotify API credentials.

    On save, writes a config.py file next to main.py and restarts the process
    so the new credentials are picked up immediately.
    """
    root = tk.Tk()
    root.title('Spotify Lyrics — First-time Setup')
    root.geometry('520x320')
    root.resizable(False, False)
    root.configure(bg='#0d0d0d')
    root.attributes('-topmost', True)  # keep dialog above other windows

    # Center the dialog on screen
    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f'520x320+{(sw-520)//2}+{(sh-320)//2}')

    # Header labels
    tk.Label(root, text='Spotify Lyrics Overlay', bg='#0d0d0d', fg='#1DB954',
             font=('Segoe UI', 16, 'bold')).pack(pady=(24, 4))
    tk.Label(root, text='Enter your Spotify Developer credentials below.',
             bg='#0d0d0d', fg='#aaaaaa', font=('Segoe UI', 10)).pack()
    tk.Label(root, text='developer.spotify.com/dashboard  →  Create app  →  copy IDs',
             bg='#0d0d0d', fg='#555555', font=('Segoe UI', 9)).pack(pady=(2, 18))

    frm = tk.Frame(root, bg='#0d0d0d')
    frm.pack(padx=40, fill='x')

    def _row(label, row, show=''):
        """Helper to create a label + entry pair on the given grid row.

        `show` is passed to Entry so the Client Secret field can mask input with bullets.
        Returns the StringVar bound to the entry so the caller can read the value.
        """
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
    secret_var = _row('Client Secret', 1, show='•')  # masked so secret isn't visible

    # Error label — hidden until validation fails
    err_lbl = tk.Label(root, text='', bg='#0d0d0d', fg='#ff6b6b',
                       font=('Segoe UI', 9))
    err_lbl.pack()

    def save_and_start():
        """Validate inputs, write config.py, then restart the process."""
        cid = id_var.get().strip()
        sec = secret_var.get().strip()
        if not cid or not sec:
            err_lbl.config(text='Both fields are required.')
            return
        # Write credentials to config.py alongside this script
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.py')
        with open(cfg_path, 'w') as f:
            f.write(f'CLIENT_ID     = "{cid}"\n')
            f.write(f'CLIENT_SECRET = "{sec}"\n')
        root.destroy()
        # Replace the current process with a fresh one so config.py is imported cleanly
        os.execv(sys.executable, [sys.executable] + sys.argv)

    tk.Button(root, text='Save & Launch', command=save_and_start,
              bg='#1DB954', fg='white', activebackground='#17a347',
              font=('Segoe UI', 11, 'bold'), padx=24, pady=10,
              bd=0, cursor='hand2', relief='flat').pack(pady=16)

    root.mainloop()
    sys.exit(0)  # if the user closes the dialog without saving, exit cleanly


# ── Main Overlay ──────────────────────────────────────────────────────────────
class LyricsOverlay:
    """Frameless, always-on-top lyrics overlay window.

    Architecture
    ────────────
    • Poll thread (_poll): calls the Spotify API every POLL_INTERVAL seconds.
      Updates _last_pos_ms / _last_pos_time when a song is playing so the UI
      thread can interpolate the exact position between polls.

    • UI tick (_tick): runs every 250 ms on the main (tkinter) thread.
      Interpolates the current playback position from the last known position
      plus elapsed wall-clock time, then looks up and displays the right lyric.
      This gives smooth lyric updates without hammering the Spotify API.
    """

    # Colour constants used throughout the UI
    BG     = '#0d0d0d'   # main window background (near-black)
    BAR_BG = '#161616'   # title bar background (slightly lighter)
    CUR_FG = '#ffffff'   # current lyric line colour (white)
    NXT_FG = '#3a3a3a'   # next lyric line colour (dim grey)
    FONT   = ('Segoe UI', 15)

    def __init__(self):
        self.root = tk.Tk()
        self.root.title('Lyrics')
        self.root.configure(bg=self.BG)
        self.root.attributes('-topmost', True)      # always above other windows
        self.root.overrideredirect(True)            # remove OS title bar / frame
        self.root.attributes('-alpha', 0.92)        # slight transparency

        # Track state
        self.current_track_id: str | None = None    # Spotify track ID of the current song
        self.lyrics: list[tuple[int, str]] = []     # parsed (ms, text) pairs for current song
        self._is_playing    = False                 # mirrors Spotify's is_playing flag
        self._game_mode     = False                 # when True, overlay is fully click-through
        self._collapsed     = False                 # whether the window is title-bar-only
        self._prev_height   = 130                   # height to restore when expanding
        self._drag_ox = self._drag_oy = 0           # drag offset from window origin

        # Resize state: (start_x, start_y, start_width, start_height) or None
        self._rsz: tuple | None = None

        # Interpolation state: the Spotify-reported position and the wall-clock
        # timestamp at which that position was captured.
        self._last_pos_ms   = 0
        self._last_pos_time = 0.0

        # While set, _tick() shows this static message instead of computing from lyrics.
        # Set to None when live lyrics should be displayed.
        self._override_display: tuple | None = ('Connecting to Spotify…', '')

        self._build_ui()
        self._init_spotify()
        self._init_game_mode_hotkey()
        self._start_poll()
        self._place_window()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        """Build all tkinter widgets: title bar, lyrics labels, and resize grip."""
        root = self.root

        # ── Title bar ─────────────────────────────────────────────────────────
        bar = tk.Frame(root, bg=self.BAR_BG, height=26)
        bar.pack(fill='x', side='top')
        bar.pack_propagate(False)  # prevent children from shrinking the bar

        # Song name label (left side of title bar)
        self.track_lbl = tk.Label(
            bar, text='♫  Spotify Lyrics',
            bg=self.BAR_BG, fg='#505050',
            font=('Segoe UI', 8), anchor='w')
        self.track_lbl.pack(side='left', padx=10, fill='y')

        # Game-mode indicator — hidden unless game mode is active, since the
        # window becomes click-through and this is the only feedback the user gets.
        self.game_mode_lbl = tk.Label(
            bar, text='🎮 GAME MODE', bg=self.BAR_BG, fg='#1DB954',
            font=('Segoe UI', 7, 'bold'))
        # not packed here — _toggle_game_mode() packs/forgets it on demand

        # Close button — packed first (right) so it sits at the far right edge
        close_btn = tk.Button(bar, text='✕', bg=self.BAR_BG, fg='#555555',
                              font=('Segoe UI', 10), bd=0, padx=10, pady=0,
                              activebackground='#c0392b', activeforeground='white',
                              cursor='arrow', command=root.quit, relief='flat')
        close_btn.pack(side='right')
        # Hover effect: turn red on mouse-enter, revert on mouse-leave
        close_btn.bind('<Enter>', lambda e: close_btn.config(bg='#c0392b', fg='white'))
        close_btn.bind('<Leave>', lambda e: close_btn.config(bg=self.BAR_BG, fg='#555555'))

        # Opacity slider — sits just to the left of the close button
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
                 command=lambda v: root.attributes('-alpha', float(v))  # live update
                 ).pack(side='right', padx=(4, 0))

        # Bind drag and double-click-to-collapse to the bar and song label
        for w in (bar, self.track_lbl):
            w.bind('<Button-1>',        self._drag_start)
            w.bind('<B1-Motion>',       self._drag_move)
            w.bind('<Double-Button-1>', self._toggle_collapse)

        # ── Lyrics frame ──────────────────────────────────────────────────────
        # Clicking anywhere in the lyrics area toggles Spotify play/pause
        self.lf = tk.Frame(root, bg=self.BG, cursor='hand2')
        self.lf.pack(fill='both', expand=True, padx=14, pady=(8, 6))

        # Current lyric line (bright white)
        self.cur_lbl = tk.Label(
            self.lf, text='Connecting to Spotify…',
            bg=self.BG, fg=self.CUR_FG,
            font=self.FONT,
            anchor='center', justify='center', wraplength=660)
        self.cur_lbl.pack(fill='x', expand=True)

        # Upcoming lyric line (dim grey — a preview of what comes next)
        self.nxt_lbl = tk.Label(
            self.lf, text='',
            bg=self.BG, fg=self.NXT_FG,
            font=self.FONT,
            anchor='center', justify='center', wraplength=660)
        self.nxt_lbl.pack(fill='x', expand=True)

        # Clicking any part of the lyrics area triggers play/pause
        for w in (self.lf, self.cur_lbl, self.nxt_lbl):
            w.bind('<Button-1>', self._click_lyrics)

        # ── Resize grip ───────────────────────────────────────────────────────
        # A small Braille-pattern character in the bottom-right corner acts as
        # a visual drag handle for resizing the window.
        self.grip = tk.Label(root, text='⠿', bg=self.BG, fg='#2a2a2a',
                              cursor='size_nw_se', font=('', 11))
        self.grip.place(relx=1.0, rely=1.0, anchor='se', x=-3, y=-2)
        self.grip.bind('<Button-1>',  self._rsz_start)
        self.grip.bind('<B1-Motion>', self._rsz_move)

        # Update wraplength whenever the window is resized
        root.bind('<Configure>', self._on_resize)

    def _on_resize(self, _e):
        """Keep lyric label wraplength in sync with the current window width."""
        wrap = max(100, self.root.winfo_width() - 28)
        self.cur_lbl.config(wraplength=wrap)
        self.nxt_lbl.config(wraplength=wrap)

    # ── Drag ─────────────────────────────────────────────────────────────────
    def _drag_start(self, e):
        """Record the offset between the mouse pointer and the window's top-left
        corner at the moment the drag begins."""
        self._drag_ox = e.x_root - self.root.winfo_x()
        self._drag_oy = e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        """Move the window to follow the mouse, preserving the initial offset."""
        self.root.geometry(f'+{e.x_root - self._drag_ox}+{e.y_root - self._drag_oy}')

    # ── Collapse / expand ─────────────────────────────────────────────────────
    def _toggle_collapse(self, _e):
        """Double-click the title bar to collapse the window to just the bar,
        or expand it back to its previous height."""
        w = self.root.winfo_width()
        if self._collapsed:
            # Expand: restore lyrics frame and grip, reset to saved height
            self.lf.pack(fill='both', expand=True, padx=14, pady=(8, 6))
            self.grip.place(relx=1.0, rely=1.0, anchor='se', x=-3, y=-2)
            self.root.geometry(f'{w}x{self._prev_height}')
            self._collapsed = False
        else:
            # Collapse: save current height, hide lyrics and grip, shrink to bar
            self._prev_height = self.root.winfo_height()
            self.lf.pack_forget()
            self.grip.place_forget()
            self.root.geometry(f'{w}x26')
            self._collapsed = True

    # ── Resize ────────────────────────────────────────────────────────────────
    def _rsz_start(self, e):
        """Capture the anchor point and original dimensions when a resize drag starts."""
        self._rsz = (e.x_root, e.y_root,
                     self.root.winfo_width(), self.root.winfo_height())

    def _rsz_move(self, e):
        """Resize the window as the grip is dragged, enforcing minimum dimensions."""
        if not self._rsz:
            return
        sx, sy, sw, sh = self._rsz
        # Clamp to a minimum of 300×90 so the window stays usable
        self.root.geometry(f'{max(300, sw + e.x_root - sx)}x{max(90, sh + e.y_root - sy)}')

    # ── Click lyrics = play / pause ───────────────────────────────────────────
    def _click_lyrics(self, _e):
        """Toggle Spotify playback when the user clicks anywhere on the lyrics area.

        The Spotify call is dispatched on a daemon thread so it never blocks the UI.
        """
        if not self.sp:
            return
        if self._is_playing:
            threading.Thread(target=self.sp.pause_playback, daemon=True).start()
        else:
            threading.Thread(target=self.sp.start_playback, daemon=True).start()
        # Optimistically flip the local flag so the UI feels instant
        self._is_playing = not self._is_playing

    # ── Game mode ─────────────────────────────────────────────────────────────
    def _init_game_mode_hotkey(self):
        """Register the global hotkey that toggles game mode.

        Uses the `keyboard` lib rather than a tkinter key binding because a
        tkinter binding only fires while the overlay has focus — the whole
        point of game mode is toggling it while a game window is focused.
        The callback hops onto the tkinter thread via `after()` since tkinter
        isn't thread-safe and `keyboard` invokes callbacks on its own thread.

        Note: if the game runs elevated (as admin), Windows won't deliver
        global hooks from this non-elevated process — run this script as
        admin too in that case.
        """
        try:
            keyboard.add_hotkey(GAME_MODE_HOTKEY, lambda: self.root.after(0, self._toggle_game_mode))
        except Exception as exc:
            print(f'[game-mode] hotkey registration failed: {exc}')

    def _toggle_game_mode(self):
        """Flip game mode on/off.

        Game mode makes the overlay fully click-through — every mouse event
        (drag, resize, click-to-pause, the opacity slider, the close button)
        passes straight through it to whatever is underneath, so it can sit
        on top of a game without ever stealing a click. The only way out is
        the hotkey, since the window can no longer be interacted with.
        """
        self._game_mode = not self._game_mode
        self._set_clickthrough(self._game_mode)
        if self._game_mode:
            self.game_mode_lbl.pack(side='left', padx=(6, 0))
        else:
            self.game_mode_lbl.pack_forget()
        print(f'[game-mode] {"ON" if self._game_mode else "OFF"} ({GAME_MODE_HOTKEY})')

    def _set_clickthrough(self, enabled: bool):
        """Add/remove the WS_EX_TRANSPARENT extended style on the window.

        This is a Windows-only trick; it's a no-op elsewhere. WS_EX_LAYERED
        is required alongside it (and is also what tkinter's `-alpha` already
        relies on), so it's kept set either way rather than restored exactly.
        """
        if not sys.platform.startswith('win'):
            return
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if enabled:
                style |= (WS_EX_LAYERED | WS_EX_TRANSPARENT)
            else:
                style = (style | WS_EX_LAYERED) & ~WS_EX_TRANSPARENT
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception as exc:
            print(f'[game-mode] click-through toggle failed: {exc}')

    # ── Spotify ───────────────────────────────────────────────────────────────
    def _init_spotify(self):
        """Create the authenticated Spotipy client.

        On first run this opens a browser for OAuth consent and caches the
        resulting token in .spotify_cache for future runs.
        """
        try:
            self.sp = spotipy.Spotify(
                auth_manager=SpotifyOAuth(
                    client_id=CLIENT_ID,
                    client_secret=CLIENT_SECRET,
                    redirect_uri=REDIRECT_URI,
                    scope=SCOPE,
                    open_browser=True,
                    cache_path=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            '.spotify_cache')
                ),
                requests_timeout=10,  # hard cap on individual HTTP requests
            )
        except Exception as exc:
            self.sp = None
            self._set_display(f'Auth error: {exc}', '')

    def _fetch_lyrics(self, title: str, artist: str, duration_ms: int):
        """Fetch lyrics for a track from lrclib.net.

        Prefers time-synced LRC lyrics; falls back to plain-text lyrics with
        evenly-spaced synthetic timestamps if synced lyrics are unavailable.
        Returns a list of (milliseconds, text) tuples, or [] on failure.
        """
        try:
            r = requests.get(
                'https://lrclib.net/api/get',
                params={'track_name': title, 'artist_name': artist,
                        'duration': duration_ms // 1000},
                timeout=7)
            if r.status_code == 200:
                data = r.json()
                if data.get('syncedLyrics'):
                    # Best case: proper LRC with per-line timestamps
                    return parse_lrc(data['syncedLyrics'])
                if data.get('plainLyrics'):
                    # Fallback: spread plain lines evenly across the track duration
                    raw  = [l for l in data['plainLyrics'].splitlines() if l.strip()]
                    step = max(2000, duration_ms // max(len(raw), 1))
                    return [(i * step, l) for i, l in enumerate(raw)]
        except Exception as exc:
            print(f'[lyrics] {exc}')
        return []

    @staticmethod
    def _line_index(lyrics: list, pos_ms: int) -> int:
        """Return the index of the lyric line that should be showing at pos_ms.

        Scans through the sorted list and returns the last line whose timestamp
        is <= pos_ms (i.e. the most recently started line).
        """
        idx = 0
        for i, (t, _) in enumerate(lyrics):
            if t <= pos_ms:
                idx = i
            else:
                break
        return idx

    # ── Polling thread ────────────────────────────────────────────────────────
    def _poll(self):
        """Background thread: polls Spotify every POLL_INTERVAL seconds.

        When the track changes, fetches new lyrics.
        When playing, records the Spotify-reported position + wall-clock time so
        _tick() can interpolate between polls for smooth lyric changes.
        Handles rate limiting (HTTP 429) by honouring the Retry-After header.
        """
        while True:
            try:
                if not self.sp:
                    self._override_display = ('⚠  Not connected', 'Restart to retry')
                    time.sleep(5)
                    continue

                pb = self.sp.current_playback()
                fetch_time = time.time()   # wall-clock time of this Spotify response
                if pb and pb.get('item'):
                    track  = pb['item']
                    tid    = track['id']
                    pos    = pb.get('progress_ms', 0)       # playback position in ms
                    name   = track['name']
                    artists = track.get('artists') or []
                    # Podcasts don't have 'artists', fall back to show name
                    artist  = artists[0]['name'] if artists else track.get('show', {}).get('name', '')
                    self._is_playing = pb.get('is_playing', False)

                    if tid != self.current_track_id:
                        # New track detected — reset lyrics and fetch new ones
                        self.current_track_id = tid
                        self.lyrics = []
                        # Update title bar on the UI thread
                        self.root.after(0, self.track_lbl.config,
                                        {'text': f'♫  {name}  —  {artist}'})
                        # Show song name while lyrics load
                        self._override_display = (f'♫  {name}', f'by {artist}')
                        self.lyrics = self._fetch_lyrics(
                            name, artist, track['duration_ms'])

                    if not self._is_playing:
                        self._override_display = ('⏸  Paused', '')
                    elif self.lyrics:
                        # Hand off to _tick() for smooth interpolated updates.
                        # fetch_time is the wall-clock anchor for pos, so elapsed
                        # time (including _fetch_lyrics latency) is accounted for.
                        self._last_pos_ms   = pos
                        self._last_pos_time = fetch_time
                        self._override_display = None  # let _tick() drive the display
                    else:
                        # Playing but no lyrics found
                        self._override_display = (f'♫  {name}',
                                                  f'by {artist}   (lyrics not found)')
                else:
                    self._override_display = ('♫  Nothing playing', '')

            except spotipy.SpotifyException as exc:
                if exc.http_status == 429:
                    # Rate limited — honour Retry-After (cap at 5 min to avoid
                    # indefinite hangs if the header value is extreme).
                    import re as _re
                    m = _re.search(r'[Rr]etry.{0,10}?(\d+)', str(exc))
                    wait = int(m.group(1)) if m else 60
                    wait = min(wait, 300)
                    print(f'[poll] rate-limited — waiting {wait}s')
                    self._override_display = ('⏳  Rate limited by Spotify',
                                              f'Retrying in {wait}s — slow down polling')
                    time.sleep(wait)
                else:
                    print(f'[poll] {exc}')
                    self._override_display = ('⚠  Error', str(exc)[:70])
                    time.sleep(4)
                continue
            except Exception as exc:
                print(f'[poll] {exc}')
                self._override_display = ('⚠  Error', str(exc)[:70])
                time.sleep(4)
                continue

            time.sleep(POLL_INTERVAL)

    def _tick(self):
        """Runs every 250 ms on the UI thread. Interpolates playback position
        so lyric changes are smooth regardless of the 2-second poll interval."""
        if self._override_display is not None:
            # Show a static status message (paused, error, loading, etc.)
            self._set_display(*self._override_display)
        elif self.lyrics and self._last_pos_time and self._is_playing:
            # Estimate current position: last known position + time elapsed since poll
            elapsed_ms = (time.time() - self._last_pos_time) * 1000
            pos = int(self._last_pos_ms + elapsed_ms)
            i   = self._line_index(self.lyrics, pos)
            cur = self.lyrics[i][1]
            nxt = self.lyrics[i + 1][1] if i + 1 < len(self.lyrics) else ''
            self._set_display(cur, nxt)
        # Schedule the next tick in 250 ms
        self.root.after(250, self._tick)

    def _start_poll(self):
        """Launch the background poll thread and start the 250 ms UI tick loop."""
        threading.Thread(target=self._poll, daemon=True).start()
        self.root.after(250, self._tick)

    def _set_display(self, current: str, nxt: str):
        """Update both lyric labels. Called only from the UI thread."""
        self.cur_lbl.config(text=current)
        self.nxt_lbl.config(text=nxt)

    def _place_window(self):
        """Position the window at the bottom-centre of the screen on startup."""
        W, H = 700, 130
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        # 80 px gap from the bottom edge keeps the overlay above the taskbar
        self.root.geometry(f'{W}x{H}+{(sw - W) // 2}+{sh - H - 80}')

    def run(self):
        """Start the tkinter event loop (blocks until the window is closed)."""
        self.root.mainloop()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    if not CLIENT_ID or not CLIENT_SECRET:
        # No credentials found — show the first-time setup dialog
        run_setup()
    else:
        LyricsOverlay().run()
