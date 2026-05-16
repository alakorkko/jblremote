#!/usr/bin/env python3
"""JBL MA-Series AVR — interactive TUI remote control."""

import curses
import socket
import sys
import threading
import time

PORT = 50000
HEARTBEAT_INTERVAL = 25  # seconds between automatic heartbeats

# ── Protocol command IDs ──────────────────────────────────────────────────────
CMD_POWER     = 0x00
CMD_DIM       = 0x01
CMD_INPUT     = 0x05
CMD_VOLUME    = 0x06
CMD_MUTE      = 0x07
CMD_SURROUND  = 0x08
CMD_TREBLE    = 0x0B
CMD_BASS      = 0x0C
CMD_DIALOG    = 0x0E
CMD_DOLBY     = 0x0F
CMD_INIT      = 0x50
CMD_HEARTBEAT = 0x51
REQUEST       = 0xF0  # data1 value meaning "query current state"

# ── Lookup tables ─────────────────────────────────────────────────────────────
SURROUND_MODES = [
    (0x01, "Dolby Surround"),
    (0x02, "DTS Neural:X"),
    (0x03, "Stereo 2.0"),
    (0x04, "Stereo 2.1"),
    (0x05, "All Stereo"),
    (0x06, "Native"),
    (0x07, "Dolby ProLogic II"),
]
SURR_IDS   = [m[0] for m in SURROUND_MODES]
SURR_NAMES = {m[0]: m[1] for m in SURROUND_MODES}

INPUTS = [
    (0x01, "TV (ARC)"),
    (0x02, "HDMI 1"),
    (0x03, "HDMI 2"),
    (0x04, "HDMI 3"),
    (0x05, "HDMI 4"),
    (0x06, "HDMI 5"),
    (0x07, "HDMI 6"),
    (0x08, "Coax"),
    (0x09, "Optical"),
    (0x0A, "Analog 1"),
    (0x0B, "Analog 2"),
    (0x0C, "Phono"),
    (0x0D, "Bluetooth"),
    (0x0E, "Network"),
]
INPUT_IDS   = [i[0] for i in INPUTS]
INPUT_NAMES = {i[0]: i[1] for i in INPUTS}

MODELS = {0x01: "MA510", 0x02: "MA710", 0x03: "MA7100HP", 0x04: "MA9100HP"}

DOLBY_MODES = [(0x01, "Music"), (0x02, "Movie"), (0x03, "Night"), (0x00, "Off")]
DOLBY_IDS   = [d[0] for d in DOLBY_MODES]
DOLBY_NAMES = {d[0]: d[1] for d in DOLBY_MODES}

DISPLAY_DIM = [
    (0x00, "Full"),
    (0x01, "50%"),
    (0x02, "25%"),
    (0x03, "Off"),
]
DIM_IDS   = [d[0] for d in DISPLAY_DIM]
DIM_NAMES = {d[0]: d[1] for d in DISPLAY_DIM}

MIN_COLS = 72
MIN_ROWS = 24


# ── Protocol helpers ──────────────────────────────────────────────────────────

def build_cmd(cmd_id: int, data1: int | None = None) -> bytes:
    if data1 is None:
        return bytes([0x23, cmd_id, 0x00, 0x0D])
    return bytes([0x23, cmd_id, 0x01, data1, 0x0D])


def parse_response(data: bytes) -> dict | None:
    """Parse an AVR response packet."""
    if len(data) < 5 or data[0] != 0x02 or data[1] != 0x23:
        return None
    cmd_id   = data[2]
    rsp_code = data[3]
    data_len = data[4]
    payload  = list(data[5:5 + data_len])
    return {"cmd": cmd_id, "rsp": rsp_code, "payload": payload}


def raw_to_db(raw: int) -> int:
    """Convert EQ byte from device to signed dB (-12..+12)."""
    if raw >= 0xF4:   # 0xF4..0xFF → -12..-1
        return raw - 0x100
    return raw        # 0x00..0x0C →  0..+12


def db_to_raw(db: int) -> int:
    """Convert signed dB to EQ byte for device."""
    return 0x100 + db if db < 0 else db


# ── Remote control class ──────────────────────────────────────────────────────

class JBLRemote:
    def __init__(self, host: str):
        self.host = host
        self.sock: socket.socket | None = None
        self._lock = threading.Lock()
        self.connected = False
        self.state: dict = {
            "power": None, "volume": None, "mute": None,
            "input": None, "surround": None,
            "treble": None, "bass": None,
            "dialog": None, "dolby": None,
            "dim": None, "model": None,
        }

    def connect(self) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((self.host, PORT))
            with self._lock:
                self.sock = sock
                self.connected = True
            # Query model via init command
            r = self._do(build_cmd(CMD_INIT, REQUEST))
            if r and r["cmd"] == CMD_INIT and r["payload"]:
                self.state["model"] = MODELS.get(r["payload"][0], "Unknown")
            return True
        except Exception:
            self.connected = False
            return False

    def disconnect(self):
        with self._lock:
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None
            self.connected = False

    def _do(self, cmd: bytes) -> dict | None:
        """Send cmd and read response. Caller must hold _lock or be single-threaded at init."""
        try:
            if self.sock:
                self.sock.send(cmd)
                self.sock.settimeout(3)
                data = self.sock.recv(1024)
                return parse_response(data) if data else None
        except Exception:
            self.connected = False
        return None

    def send(self, cmd_id: int, data1: int | None = None) -> dict | None:
        with self._lock:
            if not self.connected:
                return None
            return self._do(build_cmd(cmd_id, data1))

    def refresh_all(self):
        """Query all controllable state from the device."""
        queries = [
            (CMD_POWER,   "power"),
            (CMD_VOLUME,  "volume"),
            (CMD_MUTE,    "mute"),
            (CMD_INPUT,   "input"),
            (CMD_SURROUND,"surround"),
            (CMD_TREBLE,  "treble"),
            (CMD_BASS,    "bass"),
            (CMD_DIALOG,  "dialog"),
            (CMD_DOLBY,   "dolby"),
            (CMD_DIM,     "dim"),
        ]
        for cmd_id, key in queries:
            r = self.send(cmd_id, REQUEST)
            if r and r["rsp"] == 0x00 and r["payload"]:
                self.state[key] = r["payload"][0]
            time.sleep(0.05)

    # ── Internal cycle helper ─────────────────────────────────────────────────

    @staticmethod
    def _cycle(id_list: list, current, forward: bool) -> int:
        idx = id_list.index(current) if current in id_list else -1
        return id_list[(idx + (1 if forward else -1)) % len(id_list)]

    # ── Commands — each returns (success: bool, message: str) ─────────────────

    def volume_up(self) -> tuple[bool, str]:
        new = min(99, (self.state["volume"] or 0) + 1)
        r = self.send(CMD_VOLUME, new)
        if r and r["rsp"] == 0x00:
            self.state["volume"] = new
            return True, f"Volume → {new}"
        return False, "Volume up failed"

    def volume_down(self) -> tuple[bool, str]:
        new = max(0, (self.state["volume"] or 0) - 1)
        r = self.send(CMD_VOLUME, new)
        if r and r["rsp"] == 0x00:
            self.state["volume"] = new
            return True, f"Volume → {new}"
        return False, "Volume down failed"

    def toggle_mute(self) -> tuple[bool, str]:
        new = 0x00 if self.state.get("mute") == 0x01 else 0x01
        r = self.send(CMD_MUTE, new)
        if r and r["rsp"] == 0x00:
            self.state["mute"] = new
            return True, f"Mute → {'On' if new else 'Off'}"
        return False, "Mute toggle failed"

    def toggle_power(self) -> tuple[bool, str]:
        new = 0x00 if self.state.get("power") == 0x01 else 0x01
        r = self.send(CMD_POWER, new)
        if r and r["rsp"] == 0x00:
            self.state["power"] = new
            return True, f"Power → {'On' if new else 'Standby'}"
        return False, "Power toggle failed"

    def cycle_input(self, forward: bool = True) -> tuple[bool, str]:
        new_id = self._cycle(INPUT_IDS, self.state.get("input"), forward)
        r = self.send(CMD_INPUT, new_id)
        if r and r["rsp"] == 0x00:
            self.state["input"] = new_id
            return True, f"Input → {INPUT_NAMES[new_id]}"
        return False, "Input change failed"

    def cycle_surround(self, forward: bool = True) -> tuple[bool, str]:
        new_id = self._cycle(SURR_IDS, self.state.get("surround"), forward)
        r = self.send(CMD_SURROUND, new_id)
        if r and r["rsp"] == 0x00:
            self.state["surround"] = new_id
            return True, f"Surround → {SURR_NAMES[new_id]}"
        return False, "Surround change failed"

    def treble_adj(self, up: bool) -> tuple[bool, str]:
        db = raw_to_db(self.state.get("treble") or 0)
        new_db = max(-12, min(12, db + (1 if up else -1)))
        r = self.send(CMD_TREBLE, db_to_raw(new_db))
        if r and r["rsp"] == 0x00:
            self.state["treble"] = db_to_raw(new_db)
            return True, f"Treble → {new_db:+d} dB"
        return False, "Treble adjust failed"

    def bass_adj(self, up: bool) -> tuple[bool, str]:
        db = raw_to_db(self.state.get("bass") or 0)
        new_db = max(-12, min(12, db + (1 if up else -1)))
        r = self.send(CMD_BASS, db_to_raw(new_db))
        if r and r["rsp"] == 0x00:
            self.state["bass"] = db_to_raw(new_db)
            return True, f"Bass → {new_db:+d} dB"
        return False, "Bass adjust failed"

    def toggle_dialog(self) -> tuple[bool, str]:
        new = 0x00 if self.state.get("dialog") == 0x01 else 0x01
        r = self.send(CMD_DIALOG, new)
        if r and r["rsp"] == 0x00:
            self.state["dialog"] = new
            return True, f"Dialog Enhanced → {'On' if new else 'Off'}"
        return False, "Dialog toggle failed"

    def cycle_dolby(self) -> tuple[bool, str]:
        new_id = self._cycle(DOLBY_IDS, self.state.get("dolby"), True)
        r = self.send(CMD_DOLBY, new_id)
        if r and r["rsp"] == 0x00:
            self.state["dolby"] = new_id
            return True, f"Dolby → {DOLBY_NAMES[new_id]}"
        rsp_info = f"0x{r['rsp']:02x}" if r else "no response"
        return False, f"Dolby '{DOLBY_NAMES[new_id]}' failed (RspCode {rsp_info})"

    def cycle_dim(self) -> tuple[bool, str]:
        new_id = self._cycle(DIM_IDS, self.state.get("dim"), True)
        r = self.send(CMD_DIM, new_id)
        if r and r["rsp"] == 0x00:
            self.state["dim"] = new_id
            return True, f"Display → {DIM_NAMES[new_id]}"
        return False, "Display dim failed"

    def heartbeat(self) -> bool:
        r = self.send(CMD_HEARTBEAT)
        return r is not None and r["rsp"] == 0x00


# ── TUI ───────────────────────────────────────────────────────────────────────

# Color pair indices
C_HDR  = 1   # header bar
C_LBL  = 2   # label text
C_VAL  = 3   # normal value
C_WARN = 4   # warning: muted, standby, etc.
C_OK   = 5   # success / connected
C_ERR  = 6   # error / disconnected
C_KEY  = 7   # keyboard shortcut highlight
C_SEP  = 8   # separators / borders


def vol_bar(vol: int, width: int = 16) -> str:
    filled = round(vol / 99 * width)
    return "█" * filled + "░" * (width - filled)


def safe(win, y: int, x: int, text: str, attr: int = 0):
    """addstr that silently clamps to terminal bounds."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    text = text[:w - x]
    if not text:
        return
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def draw(stdscr, remote: JBLRemote, status: str, status_ok: bool):
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    if h < MIN_ROWS or w < MIN_COLS:
        safe(stdscr, 0, 0,
             f"Terminal too small ({w}×{h}). Need at least {MIN_COLS}×{MIN_ROWS}.",
             curses.color_pair(C_ERR) | curses.A_BOLD)
        stdscr.refresh()
        return

    s      = remote.state
    model  = s.get("model") or "JBL AVR"
    is_on  = remote.connected

    # ── Header ───────────────────────────────────────────────────────────────
    conn_icon = "●" if is_on else "○"
    conn_text = f"{remote.host}  {conn_icon}  "
    hdr_attr  = curses.color_pair(C_HDR) | curses.A_BOLD
    safe(stdscr, 0, 0, " " * w, hdr_attr)
    safe(stdscr, 0, 0, f"  {model} Remote Control", hdr_attr)
    safe(stdscr, 0, max(0, w - len(conn_text)), conn_text,
         curses.color_pair(C_HDR) | (curses.A_BOLD if is_on else 0))

    sep_attr = curses.color_pair(C_SEP)
    safe(stdscr, 1, 0, "─" * w, sep_attr)

    # Layout: left half = state panel, right half = controls
    lw = w // 2  # width of left panel (divider at column lw)
    rc = lw + 2  # right-panel content start column
    rw = w - rc - 1

    # ── Vertical divider ──────────────────────────────────────────────────────
    for r in range(2, h - 2):
        safe(stdscr, r, lw, "│", sep_attr)

    # ── Left panel helpers ────────────────────────────────────────────────────
    def put(row: int, label: str, value: str, val_attr: int):
        if row >= h - 2:
            return
        safe(stdscr, row, 2,  f"{label:<13}", curses.color_pair(C_LBL))
        safe(stdscr, row, 15, value[:lw - 16], val_attr)

    row = 2

    # Power
    pwr = s.get("power")
    if   pwr == 0x01: put(row, "Power",    "On",      curses.color_pair(C_OK)  | curses.A_BOLD)
    elif pwr == 0x00: put(row, "Power",    "Standby", curses.color_pair(C_WARN))
    else:             put(row, "Power",    "?",       curses.color_pair(C_SEP))
    row += 1

    # Volume + bar
    vol = s.get("volume")
    if vol is not None:
        put(row, "Volume", f"{vol_bar(vol)}  {vol:3d}", curses.color_pair(C_VAL))
    else:
        put(row, "Volume", "?", curses.color_pair(C_SEP))
    row += 1

    # Mute
    mute = s.get("mute")
    if   mute == 0x01: put(row, "Mute", "On  ◀ MUTED", curses.color_pair(C_WARN) | curses.A_BOLD)
    elif mute == 0x00: put(row, "Mute", "Off",          curses.color_pair(C_VAL))
    else:              put(row, "Mute", "?",             curses.color_pair(C_SEP))
    row += 2  # blank line

    # Input
    inp = s.get("input")
    put(row, "Input",
        INPUT_NAMES.get(inp, "?") if inp is not None else "?",
        curses.color_pair(C_VAL) if inp is not None else curses.color_pair(C_SEP))
    row += 1

    # Surround
    surr = s.get("surround")
    put(row, "Surround",
        SURR_NAMES.get(surr, "?") if surr is not None else "?",
        curses.color_pair(C_VAL) if surr is not None else curses.color_pair(C_SEP))
    row += 2  # blank line

    # Treble
    treble = s.get("treble")
    put(row, "Treble",
        f"{raw_to_db(treble):+d} dB" if treble is not None else "?",
        curses.color_pair(C_VAL) if treble is not None else curses.color_pair(C_SEP))
    row += 1

    # Bass
    bass = s.get("bass")
    put(row, "Bass",
        f"{raw_to_db(bass):+d} dB" if bass is not None else "?",
        curses.color_pair(C_VAL) if bass is not None else curses.color_pair(C_SEP))
    row += 2  # blank line

    # Dialog Enhanced
    dlg = s.get("dialog")
    if   dlg == 0x01: put(row, "Dialog Enh.", "On",  curses.color_pair(C_OK))
    elif dlg == 0x00: put(row, "Dialog Enh.", "Off", curses.color_pair(C_VAL))
    else:             put(row, "Dialog Enh.", "?",   curses.color_pair(C_SEP))
    row += 1

    # Dolby mode
    dolby = s.get("dolby")
    put(row, "Dolby Mode",
        DOLBY_NAMES.get(dolby, "?") if dolby is not None else "?",
        curses.color_pair(C_VAL) if dolby is not None else curses.color_pair(C_SEP))
    row += 1

    # Display dim
    dim = s.get("dim")
    put(row, "Display",
        DIM_NAMES.get(dim, "?") if dim is not None else "?",
        curses.color_pair(C_VAL) if dim is not None else curses.color_pair(C_SEP))

    # ── Right panel: keyboard controls ───────────────────────────────────────
    controls = [
        ("↑ / ↓",  "Volume up / down"),
        ("m",       "Toggle mute"),
        ("p",       "Toggle power"),
        ("i / I",   "Next / prev input"),
        ("s / S",   "Next / prev surround"),
        ("d",       "Toggle dialog enhanced"),
        ("t / T",   "Treble +/-"),
        ("b / B",   "Bass +/-"),
        ("o",       "Cycle Dolby mode"),
        ("D",       "Cycle display brightness"),
        ("",        ""),
        ("r",       "Refresh all state"),
        ("h",       "Send heartbeat"),
        ("q / Esc", "Quit"),
    ]

    crow = 2
    safe(stdscr, crow, rc, "CONTROLS",
         curses.color_pair(C_LBL) | curses.A_BOLD | curses.A_UNDERLINE)
    crow += 1

    for ktext, desc in controls:
        if crow >= h - 2:
            break
        if not ktext:
            crow += 1
            continue
        safe(stdscr, crow, rc,      f"{ktext:<9}", curses.color_pair(C_KEY) | curses.A_BOLD)
        safe(stdscr, crow, rc + 10, desc[:rw - 10], curses.color_pair(C_LBL))
        crow += 1

    # ── Status bar ────────────────────────────────────────────────────────────
    safe(stdscr, h - 2, 0, "─" * w, sep_attr)
    st_attr = curses.color_pair(C_OK) if status_ok else curses.color_pair(C_ERR)
    safe(stdscr, h - 1, 0, f"  › {status}", st_attr)

    stdscr.refresh()


# ── Main TUI loop ─────────────────────────────────────────────────────────────

def main_tui(stdscr, host: str):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(300)

    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(C_HDR,  curses.COLOR_BLACK,  curses.COLOR_CYAN)
        curses.init_pair(C_LBL,  curses.COLOR_CYAN,   -1)
        curses.init_pair(C_VAL,  curses.COLOR_WHITE,  -1)
        curses.init_pair(C_WARN, curses.COLOR_RED,    -1)
        curses.init_pair(C_OK,   curses.COLOR_GREEN,  -1)
        curses.init_pair(C_ERR,  curses.COLOR_RED,    -1)
        curses.init_pair(C_KEY,  curses.COLOR_YELLOW, -1)
        curses.init_pair(C_SEP,  curses.COLOR_WHITE,  -1)

    remote    = JBLRemote(host)
    status    = "Connecting…"
    status_ok = True
    draw(stdscr, remote, status, status_ok)

    if not remote.connect():
        status    = f"Connection failed: {host}:{PORT}"
        status_ok = False
    else:
        status = "Fetching state…"
        draw(stdscr, remote, status, status_ok)
        remote.refresh_all()
        status = "Ready"

    draw(stdscr, remote, status, status_ok)

    # Background heartbeat thread
    stop_evt = threading.Event()

    def _heartbeat_loop():
        while not stop_evt.wait(HEARTBEAT_INTERVAL):
            if remote.connected:
                remote.heartbeat()

    threading.Thread(target=_heartbeat_loop, daemon=True).start()

    # ── Key → action mapping ──────────────────────────────────────────────────
    def handle(key: int) -> tuple[bool, str] | None:
        if key in (curses.KEY_UP, ord('+')):
            return remote.volume_up()
        if key in (curses.KEY_DOWN, ord('-')):
            return remote.volume_down()
        if key == ord('m'):
            return remote.toggle_mute()
        if key == ord('p'):
            return remote.toggle_power()
        if key == ord('i'):
            return remote.cycle_input(True)
        if key == ord('I'):
            return remote.cycle_input(False)
        if key == ord('s'):
            return remote.cycle_surround(True)
        if key == ord('S'):
            return remote.cycle_surround(False)
        if key == ord('t'):
            return remote.treble_adj(True)
        if key == ord('T'):
            return remote.treble_adj(False)
        if key == ord('b'):
            return remote.bass_adj(True)
        if key == ord('B'):
            return remote.bass_adj(False)
        if key == ord('d'):
            return remote.toggle_dialog()
        if key == ord('o'):
            return remote.cycle_dolby()
        if key == ord('D'):
            return remote.cycle_dim()
        return None

    # ── Main event loop ───────────────────────────────────────────────────────
    while True:
        try:
            key = stdscr.getch()
        except Exception:
            key = -1

        if key == curses.KEY_RESIZE:
            draw(stdscr, remote, status, status_ok)
            continue

        if key == -1:
            # Idle redraw (picks up heartbeat / background state changes)
            draw(stdscr, remote, status, status_ok)
            continue

        # Quit
        if key in (ord('q'), ord('Q'), 27):
            break

        # Refresh
        if key == ord('r'):
            status = "Refreshing…"
            draw(stdscr, remote, status, True)
            remote.refresh_all()
            status    = "State refreshed"
            status_ok = True
            draw(stdscr, remote, status, status_ok)
            continue

        # Manual heartbeat
        if key == ord('h'):
            ok        = remote.heartbeat()
            status    = "Heartbeat OK" if ok else "Heartbeat failed"
            status_ok = ok
            draw(stdscr, remote, status, status_ok)
            continue

        result = handle(key)
        if result is not None:
            if not remote.connected:
                status = "Reconnecting…"
                draw(stdscr, remote, status, False)
                if remote.connect():
                    remote.refresh_all()
                    status    = "Reconnected — press again"
                    status_ok = True
                else:
                    status    = "Reconnection failed"
                    status_ok = False
                draw(stdscr, remote, status, status_ok)
                continue

            status_ok, status = result
            draw(stdscr, remote, status, status_ok)

    stop_evt.set()
    remote.disconnect()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <receiver_ip>", file=sys.stderr)
        sys.exit(1)
    curses.wrapper(main_tui, sys.argv[1])


if __name__ == "__main__":
    main()
