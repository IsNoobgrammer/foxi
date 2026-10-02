"""Foxi — use your controller as mouse + keyboard. Window host: pywebview (Edge WebView2) shows ui/index.html,
which talks to the engine through Api. Build with build.bat."""
import base64, copy, ctypes, ctypes.wintypes, glob, os, subprocess, sys, threading
import webview
import diag
import engine as E

FROZEN = getattr(sys, 'frozen', False)
BASE = E.APP_DIR  # portable: Foxi.exe + data/ next to it
RES = getattr(sys, '_MEIPASS', BASE)  # bundled files (ui, HidHide installer, cursors), unpacked by the exe
CFG = os.path.join(E.DATA_DIR, 'foxi.json')
VERSION = '1.0.0'


def migrate():
    """Older builds kept foxi.json beside the exe; move it into data/ once."""
    os.makedirs(E.DATA_DIR, exist_ok=True)
    old = os.path.join(BASE, 'foxi.json')
    if os.path.exists(old) and not os.path.exists(CFG):
        os.replace(old, CFG)


def norm(v):
    return max(-1.0, min(1.0, v / 32767))


class Api:
    """Every public method is callable from the page as window.pywebview.api.<name>()."""

    def __init__(self, eng):
        self._eng = eng
        self._diag = diag.Sampler(eng.get_state, E.ST)

    def state(self):
        e, p = self._eng, self._eng.pad
        return {'connected': e.connected, 'mode': e.mode, 'speed': e.speed() if e.cfg else None,
                'held': [n for n, b in E.BUTTONS.items() if e.connected and e.held & b],
                'lx': norm(p.sLX), 'ly': norm(p.sLY), 'rx': norm(p.sRX), 'ry': norm(p.sRY),
                'lt': p.bLT / 255, 'rt': p.bRT / 255, 'log': list(e.log), 'error': e.error,
                'counts': dict(e.counts)}

    def config(self):
        try:
            return E.load_config(CFG)[0]
        except Exception:  # broken file: show defaults; overwritten only when the user saves
            return copy.deepcopy(E.DEFAULT_CONFIG)

    def defaults(self):
        return copy.deepcopy(E.DEFAULT_CONFIG)

    def save(self, raw):
        try:
            E.validate(raw)
        except (ValueError, KeyError, TypeError) as ex:
            return {'error': str(ex)}
        E.save_config(CFG, raw)  # engine hot-reloads on mtime change
        return {'ok': True}

    def check_action(self, action):
        try:
            E.check_action(action)
            return {'ok': True}
        except ValueError as ex:
            return {'error': str(ex)}

    def toggle(self):
        self._eng.cmds.put('toggle')

    def record_start(self):
        self._eng.rec_peak = 0
        self._eng.paused = True

    def record_poll(self):
        """Done once something was pressed and everything is released again."""
        e = self._eng
        if e.rec_peak and not e.held:
            e.paused = False
            return {'done': True, 'combo': E.combo_name(e.rec_peak)}
        return {'done': False, 'held': E.combo_name(e.held | e.rec_peak)}

    def record_cancel(self):
        self._eng.paused = False

    def hidhide_status(self):
        return {'installed': E.hidhide_installed()}

    def hidhide_install(self):
        found = glob.glob(os.path.join(RES, 'vendor', 'hidhide', 'HidHide_*.exe'))
        if not found:
            return {'error': 'Bundled HidHide installer not found.'}
        subprocess.run([found[0]])  # shows its own UI; this call blocks until it closes
        return {'installed': E.hidhide_installed()}

    def hidhide_setup(self):
        ids = E.hidhide_setup()
        E.cloak(self._eng.mode == 'on')
        return {'ids': ids}

    def cursor_previews(self):
        """data: URLs of each pack's arrow/hand/ibeam so the page can preview them with CSS cursor:url()."""
        out = {}
        for pack in E.CURSOR_PACKS:
            d = os.path.join(RES, 'vendor', 'cursors', pack)
            out[pack] = {}
            for f in ('arrow', 'hand', 'ibeam'):
                p = os.path.join(d, f + '.cur')
                if os.path.exists(p):
                    with open(p, 'rb') as fh:
                        out[pack][f] = 'data:image/x-icon;base64,' + base64.b64encode(fh.read()).decode()
        return out

    def info(self):
        return {'version': VERSION, 'config': CFG, 'log': E.LOG_PATH, 'folder': BASE, 'keys': list(E.VK)}

    def diag_start(self, test):
        return {'started': self._diag.start(test)}

    def diag_status(self):
        return self._diag.status()

    def diag_stop(self):
        self._diag.stop()

    def counts_reset(self):
        self._eng.counts = {}

    def rumble(self, which):
        l, r = {'left': (50000, 0), 'right': (0, 50000), 'both': (50000, 50000)}[which]
        def go():
            self._eng.xi.XInputSetState(0, ctypes.byref(E.VIB(l, r))); E.time.sleep(0.7)
            self._eng.xi.XInputSetState(0, ctypes.byref(E.VIB(0, 0)))
        threading.Thread(target=go, daemon=True).start()

    def perf(self):
        e = self._eng
        return {'step_us': round(e.step_us, 1), 'loop_ms': round(e.loop_ms, 3)}

    def open_folder(self):
        os.startfile(BASE)

    def menu_state(self):
        e = self._eng
        if not e.cfg:
            return {'open': False, 'index': 0, 'items': [], 'crumbs': []}
        return {'open': e.menu_open, 'index': e.menu_index, 'items': e.menu_items(), 'crumbs': e.menu_crumbs(),
                'tiles': e.menu_tiles(), 'cols': E.MENU_COLS}

    def menu_pick(self, i):
        self._eng.menu_pick(int(i))

    def menu_close(self):
        self._eng.close_menu()


MENU_TITLE, MENU_W, MENU_ROW, MENU_CHROME = 'Foxi menu', 460, 46, 92  # logical px: width, row, header+footer


_u = ctypes.windll.user32  # declare 64-bit handle args, or HWND_TOPMOST (-1) gets truncated and the call fails
_u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            ctypes.c_uint]
_u.MonitorFromPoint.restype = ctypes.c_void_p
_u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]


class MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint), ('rcMonitor', ctypes.wintypes.RECT), ('rcWork', ctypes.wintypes.RECT),
                ('dwFlags', ctypes.c_uint)]


OFFSCREEN = -32000  # "closed" menu parks here: still visible to WinForms, so WebView2 keeps rendering it
MENU_HWND = [0]     # found once by title at startup, then the title is cleared (nothing lists 'Foxi menu')


def own_window(title, pid=None):
    """This process's top-level window with exactly `title`. Never FindWindow: it matches titles
    case-insensitively across ALL apps, so 'Foxi' also hits a terminal tab named 'foxi'."""
    u, pid, found = ctypes.windll.user32, pid or os.getpid(), []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(h, _):
        owner = ctypes.wintypes.DWORD()
        u.GetWindowThreadProcessId(ctypes.c_void_p(h), ctypes.byref(owner))
        if owner.value == pid:
            buf = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(ctypes.c_void_p(h), buf, 256)
            if buf.value == title:
                found.append(h)
                return False
        return True
    u.EnumWindows(visit, 0)
    return found[0] if found else None


def menu_rows(eng):
    """Height of the current level in list rows: a tile row is ~1.6 rows, the 'Categories' divider ~0.6."""
    items, tiles = eng.menu_items(), eng.menu_tiles()
    cats = len(items) - tiles
    tile_rows = -(-tiles // E.MENU_COLS)
    return tile_rows * 1.6 + cats + (0.6 if tiles and cats else 0)


def show_menu(opened, rows):
    """Move the quick menu next to the cursor (kept inside that monitor) WITHOUT activating it, so the app
    you were typing in keeps keyboard focus and receives whatever the menu types. pywebview's show() would
    activate it, and a Win32-shown hidden form never paints, so the window is moved instead of shown/hidden."""
    u = ctypes.windll.user32
    hwnd = MENU_HWND[0]
    if not hwnd:
        return
    if not opened:
        u.SetWindowPos(hwnd, None, OFFSCREEN, OFFSCREEN, 0, 0, 0x0001 | 0x0004 | 0x0010)  # NOSIZE|NOZORDER|NOACTIVATE
        return
    k = (u.GetDpiForWindow(hwnd) or 96) / 96
    w, h = int(MENU_W * k), int((MENU_CHROME + MENU_ROW * max(rows, 1)) * k)
    pt = ctypes.wintypes.POINT()
    u.GetCursorPos(ctypes.byref(pt))
    mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    u.GetMonitorInfoW(u.MonitorFromPoint(pt, 2), ctypes.byref(mi))  # MONITOR_DEFAULTTONEAREST
    wa = mi.rcWork
    x = min(max(pt.x + 18, wa.left), wa.right - w)
    y = min(max(pt.y + 18, wa.top), wa.bottom - h)
    u.SetWindowPos(hwnd, ctypes.c_void_p(-1), x, y, w, h, 0x0010)  # HWND_TOPMOST, SWP_NOACTIVATE


def dark_titlebar(title):
    """Windows 11: dark caption + caption colour matching the page background."""
    hwnd = own_window(title)
    if hwnd:
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)            # immersive dark mode
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(ctypes.c_int(0x00141719)), 4)   # caption #191714 (BGR)


def main():
    ctypes.windll.kernel32.CreateMutexW(None, False, 'Local\\FoxiSingleInstance')
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS: two Foxis = double cursor
        ctypes.windll.user32.MessageBoxW(None, 'Foxi is already running.', 'Foxi', 0x40)
        return
    migrate()
    eng = E.Engine(CFG)
    thread = threading.Thread(target=eng.run, daemon=True)
    thread.start()
    api = Api(eng)
    win = webview.create_window('Foxi', url=os.path.join(RES, 'ui', 'index.html'), js_api=api,
                                width=1180, height=780, min_size=(980, 660), background_color='#191714')
    menu = webview.create_window(MENU_TITLE, url=os.path.join(RES, 'ui', 'menu.html'), js_api=api,
                                 width=MENU_W, height=360, x=OFFSCREEN, y=OFFSCREEN, frameless=True, easy_drag=False,
                                 on_top=True, focus=False, resizable=False, background_color='#1f1b18')
    eng.on_menu = lambda opened: show_menu(opened, menu_rows(eng))

    def prepare_menu():
        """Rounded corners; keep it out of the taskbar, Alt+Tab and Task View; never takes focus.
        WinForms marks forms WS_EX_APPWINDOW, which beats WS_EX_TOOLWINDOW, so drop it, then hide/show
        once so the shell re-reads the styles (shown again at once: WebView2 keeps rendering)."""
        u = ctypes.windll.user32
        hwnd = own_window(MENU_TITLE)
        if not hwnd:
            return
        MENU_HWND[0] = hwnd
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)
        ex = u.GetWindowLongW(hwnd, -20)
        u.SetWindowLongW(hwnd, -20, (ex | 0x80 | 0x08000000) & ~0x40000)  # +TOOLWINDOW +NOACTIVATE -APPWINDOW
        u.ShowWindow(hwnd, 0)  # SW_HIDE
        u.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
        u.SetWindowTextW(hwnd, '')
        show_menu(False, 0)

    def closed():
        eng.running = False
        thread.join(timeout=2)  # engine releases keys/mouse, un-hides the pad, restores cursors
        menu.destroy()          # otherwise the hidden menu window keeps the app alive
    win.events.closed += closed
    win.events.shown += lambda: (dark_titlebar('Foxi'), prepare_menu())
    try:
        webview.start(debug='--debug' in sys.argv, icon=os.path.join(RES, 'assets', 'foxi.ico'))
    except Exception:
        E.log_crash()
        ctypes.windll.user32.MessageBoxW(None, f'Foxi could not open its window.\nDetails: {E.LOG_PATH}', 'Foxi', 0x10)
        closed()


if __name__ == '__main__':
    main()
