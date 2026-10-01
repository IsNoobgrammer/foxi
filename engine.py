"""Foxi engine: XInput pad (EvoFox One S, 2.4GHz mode) -> mouse + keyboard, with chord shortcuts.
No GUI here; foxi.py runs Engine.run() in a thread and reads its public attributes.

Self-check: python engine.py --test
"""
import collections, copy, ctypes, json, math, os, queue, subprocess, sys, time
from ctypes import Structure, c_long, c_ushort, c_ubyte, c_short, c_uint, byref

BUTTONS = {'UP': 0x1, 'DOWN': 0x2, 'LEFT': 0x4, 'RIGHT': 0x8, 'START': 0x10, 'BACK': 0x20,
           'LS': 0x40, 'RS': 0x80, 'LB': 0x100, 'RB': 0x200, 'HOME': 0x400,
           'A': 0x1000, 'B': 0x2000, 'X': 0x4000, 'Y': 0x8000,
           'LT': 0x10000, 'RT': 0x20000}  # LT/RT: synthetic bits, analog > trigger_threshold

VK = {'ctrl': 0x11, 'shift': 0x10, 'alt': 0x12, 'win': 0x5B, 'menu': 0x5D, 'tab': 0x09, 'enter': 0x0D,
      'esc': 0x1B, 'space': 0x20, 'backspace': 0x08, 'delete': 0x2E, 'insert': 0x2D,
      'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27,
      'home': 0x24, 'end': 0x23, 'pageup': 0x21, 'pagedown': 0x22, 'printscreen': 0x2C,
      '.': 0xBE, ',': 0xBC, '-': 0xBD, '=': 0xBB, ';': 0xBA, '/': 0xBF, '`': 0xC0,
      'volume_up': 0xAF, 'volume_down': 0xAE, 'mute': 0xAD,
      'play_pause': 0xB3, 'next': 0xB0, 'prev': 0xB1, 'stop': 0xB2,
      **{f'f{i}': 0x6F + i for i in range(1, 25)},
      **{c: ord(c.upper()) for c in 'abcdefghijklmnopqrstuvwxyz0123456789'}}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E, 0x5B, 0x5D}
MOUSE = {'left': (0x2, 0x4), 'right': (0x8, 0x10), 'middle': (0x20, 0x40)}  # (down, up) flags
ACTIONS = ('keys', 'repeat', 'mouse', 'run', 'text', 'switcher', 'toggle', 'dpi', 'menu')
MENU_ACTIONS = ('keys', 'text', 'run')  # what a quick-menu item may do
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: no console flash from the windowed exe

DEFAULT_CONFIG = {
    'mouse_stick': 'left', 'scroll_stick': 'right',  # mouse_stick 'both': both sticks together = scroll
    'cursor_pack': 'macos',  # cursors while ON: macos / posy / windows (GAME always restores yours)
    'mouse_speed': 700, 'mouse_curve': 2.2, 'deadzone': 0.10,
    'scroll_speed': 6, 'scroll_curve': 2.0,  # notches/s at full tilt; curve like the cursor's
    'mouse_boost': 2.5, 'mouse_boost_ramp_ms': 600,  # full tilt held: speed ramps 1x -> boost over ramp
    'dpi_levels': [400, 700, 1200],  # dpi action cycles these cursor speeds (px/s at full tilt)
    'trigger_threshold': 60, 'repeat_delay_ms': 400, 'repeat_rate_ms': 40, 'switcher_timeout_ms': 1000,
    'map': {
        'LT': 'mouse:left', 'RT': 'mouse:right',
        'UP': 'repeat:up', 'DOWN': 'repeat:down', 'LEFT': 'repeat:left', 'RIGHT': 'repeat:right',
        'X': 'keys:enter', 'LB': 'keys:win+h', 'RB': 'repeat:ctrl+backspace', 'Y': 'switcher',
        'LS+RS': 'toggle', 'BACK': 'dpi', 'START': 'menu', 'A': 'keys:win+shift+s',
    },
    # START opens this list near the cursor: D-pad moves, X picks, B closes. keys: presses, text: types.
    # text: items only type, never press Enter: you check the text, then press Enter (X) yourself
    'quick_menu': [
        {'label': 'Copy', 'action': 'keys:ctrl+c'},
        {'label': 'Paste', 'action': 'keys:ctrl+v'},
        {'label': 'Select all', 'action': 'keys:ctrl+a'},
        {'label': 'Paste image', 'action': 'keys:alt+v'},
        {'label': 'Resume a session', 'action': 'text:/resume'},
        {'label': 'Compact the context', 'action': 'text:/compact'},
        {'label': 'Claude, skip permissions', 'action': 'text:claude --dangerously-skip-permissions'},
    ],
}

user32 = ctypes.windll.user32


def parse_combo(s):
    """'UP+X' -> bitmask."""
    mask = 0
    for name in s.upper().replace(' ', '').split('+'):
        if name not in BUTTONS:
            raise ValueError(f'unknown button {name!r} in {s!r}; valid: {", ".join(BUTTONS)}')
        mask |= BUTTONS[name]
    return mask


def combo_name(mask):
    return '+'.join(n for n, b in BUTTONS.items() if mask & b)


def parse_keys(spec):
    names = [k.strip().lower() for k in spec.split('+')]
    bad = [n for n in names if n not in VK]
    if bad:
        raise ValueError(f'unknown key {bad[0]!r} in {spec!r}')
    return [VK[n] for n in names]


def check_action(a):
    kind, _, arg = a.partition(':')
    if kind not in ACTIONS:
        raise ValueError(f'unknown action {a!r}; use one of {", ".join(ACTIONS)}')
    if kind in ('keys', 'repeat'):
        parse_keys(arg)
    elif kind == 'mouse' and arg not in MOUSE:
        raise ValueError(f'unknown mouse button in {a!r}; use left, right or middle')
    elif kind == 'run' and not arg.strip():
        raise ValueError('run: needs a command')
    elif kind == 'text' and not arg:
        raise ValueError('text: needs something to type')


def load_config(path):
    """Returns (raw json dict, compiled map {mask: action}). Missing keys fall back to defaults."""
    with open(path, encoding='utf-8-sig') as f:  # -sig: tolerate a BOM from Notepad/PowerShell
        raw = {**copy.deepcopy(DEFAULT_CONFIG), **json.load(f)}
    return raw, validate(raw)


def validate(raw):
    """Raises ValueError with a readable message; returns the compiled map {mask: action}."""
    if raw['cursor_pack'] not in CURSOR_PACKS:
        raise ValueError(f"cursor_pack must be one of {', '.join(CURSOR_PACKS)}")
    if raw['mouse_stick'] not in ('left', 'right', 'both', 'none'):
        raise ValueError('mouse_stick must be left, right, both or none')
    for i, it in enumerate(raw['quick_menu']):
        if not isinstance(it, dict) or not str(it.get('label', '')).strip():
            raise ValueError(f'quick menu item {i + 1} needs a label')
        if it.get('action', '').partition(':')[0] not in MENU_ACTIONS:
            raise ValueError(f"quick menu item {i + 1}: use keys:, text: or run:")
        check_action(it['action'])
    compiled = {}
    for k, a in raw['map'].items():
        check_action(a)  # validate now, not mid-game
        compiled[parse_combo(k)] = a
    return compiled


def save_config(path, raw):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(raw, f, indent=2)
    os.replace(tmp, path)  # atomic: engine never reads a half-written file


def pick(bit, held, mapping):
    """Just-pressed button `bit`: longest mapped combo that includes it and is fully held.
    So LT held (dragging) + UP still fires UP, and UP then X fires UP, then UP+X if mapped."""
    cands = [c for c in mapping if c & bit and c & ~held == 0]
    return max(cands, key=lambda c: bin(c).count('1'), default=None)


def stick(x, y, deadzone, curve):
    """Raw stick -> screen-direction vector, magnitude 0..1 with radial deadzone + response curve."""
    m = min(math.hypot(x, y) / 32767, 1.0)
    if m <= deadzone:
        return 0.0, 0.0
    s = ((m - deadzone) / (1 - deadzone)) ** curve / m
    return x / 32767 * s, -y / 32767 * s  # stick up = screen up


def route(L, R, cfg):
    """Which raw stick drives the cursor and which scrolls -> (cursor_xy | None, scroll_xy | None).
    mouse_stick 'both': either stick moves the cursor; both pushed together scroll (their average)."""
    ms = cfg['mouse_stick']
    if ms == 'both':
        live = lambda s: math.hypot(*s) > cfg['deadzone'] * 32767
        if live(L) and live(R):
            return None, ((L[0] + R[0]) // 2, (L[1] + R[1]) // 2)
        return (L if live(L) else R), None
    sticks = {'left': L, 'right': R}
    return sticks.get(ms), sticks.get(cfg['scroll_stick'])


def boost(edge_time, cfg):
    """Speed multiplier: 1x on normal tilt, ramps to mouse_boost while the stick is held at the edge."""
    ramp = max(cfg['mouse_boost_ramp_ms'], 1) / 1000
    return 1 + (cfg['mouse_boost'] - 1) * min(1.0, edge_time / ramp)


class POINT(Structure):
    _fields_ = [('x', c_long), ('y', c_long)]


def move_cursor(dx, dy):
    """Absolute SetCursorPos, not relative mouse_event: skips Windows pointer acceleration
    ('Enhance pointer precision') so Foxi's curve is the only one. Desktop apps see normal mouse moves."""
    pt = POINT()
    user32.GetCursorPos(byref(pt))
    user32.SetCursorPos(pt.x + dx, pt.y + dy)


RES_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))  # bundled files
CURSOR_PACKS = ('macos', 'posy', 'windows')
OCR = {'arrow.cur': 32512, 'ibeam.cur': 32513, 'cross.cur': 32515, 'sizenwse.cur': 32642, 'sizenesw.cur': 32643,
       'sizewe.cur': 32644, 'sizens.cur': 32645, 'sizeall.cur': 32646, 'no.cur': 32648, 'hand.cur': 32649}
user32.LoadCursorFromFileW.restype = ctypes.c_void_p  # 64-bit handle; default int would truncate it
user32.SetSystemCursor.argtypes = (ctypes.c_void_p, ctypes.c_uint)


def set_cursor_pack(pack):
    """Swap the system cursors ('windows' = the user's own scheme). SetSystemCursor only lasts until
    SPI_SETCURSORS or a reboot, so a crash can never leave the user's cursor settings changed."""
    user32.SystemParametersInfoW(0x57, 0, None, 0)  # SPI_SETCURSORS: reload the user's scheme
    if pack == 'windows':
        return
    for f, ocr in OCR.items():
        p = os.path.join(RES_DIR, 'vendor', 'cursors', pack, f)
        h = user32.LoadCursorFromFileW(p) if os.path.exists(p) else None
        if h:
            user32.SetSystemCursor(h, ocr)  # takes ownership of h


def key(vk, up):
    user32.keybd_event(vk, 0, (2 if up else 0) | (1 if vk in EXTENDED else 0), 0)


def tap(spec):
    codes = parse_keys(spec)
    for c in codes:
        key(c, False)
    for c in reversed(codes):
        key(c, True)


class KEYBDINPUT(Structure):
    _fields_ = [('wVk', c_ushort), ('wScan', c_ushort), ('dwFlags', c_uint), ('time', c_uint),
                ('dwExtraInfo', ctypes.c_size_t)]


class INPUT(Structure):  # type + union; MOUSEINPUT is the biggest member, hence the padding
    _fields_ = [('type', c_uint), ('ki', KEYBDINPUT), ('_pad', c_ubyte * 8)]


def type_text(s):
    """Types any text into the focused window (KEYEVENTF_UNICODE), independent of keyboard layout."""
    units = s.encode('utf-16-le')
    seq = (INPUT * (len(units)))()
    for i in range(0, len(units), 2):
        cu = units[i] | units[i + 1] << 8
        for j, up in ((i, 0), (i + 1, 2)):  # down, then up (KEYEVENTF_KEYUP)
            seq[j].type = 1  # INPUT_KEYBOARD
            seq[j].ki = KEYBDINPUT(0, cu, 0x4 | up, 0, 0)
    user32.SendInput(len(seq), seq, ctypes.sizeof(INPUT))


def start(action):
    kind, _, arg = action.partition(':')
    if kind in ('keys', 'repeat'):
        tap(arg)
    elif kind == 'text':
        type_text(arg)
    elif kind == 'mouse':
        user32.mouse_event(MOUSE[arg][0], 0, 0, 0, 0)
    elif kind == 'run':
        subprocess.Popen(arg, shell=True, creationflags=NO_WINDOW)


def stop(action):
    kind, _, arg = action.partition(':')
    if kind == 'mouse':
        user32.mouse_event(MOUSE[arg][1], 0, 0, 0, 0)


# --- HidHide: hides the pad from every app except Foxi, so Windows' own controller navigation
#     (terminal tabs, voice-typing dismiss, taskbar) stops reacting while Foxi is ON.
HIDHIDE_CLI = r'C:\Program Files\Nefarius Software Solutions\HidHide\x64\HidHideCLI.exe'


def hidhide_installed():
    return os.path.exists(HIDHIDE_CLI)


def hidhide(*args):
    return subprocess.run([HIDHIDE_CLI, *args], capture_output=True, text=True, creationflags=NO_WINDOW)


def pad_instance_ids():
    """Connected XInput pads: the USB (XnaComposite) node plus its HID child (&IG_ = XInput interface)."""
    ps = ("Get-PnpDevice -PresentOnly | ? { $_.Class -eq 'XnaComposite' -or $_.InstanceId -match '^HID\\\\.*&IG_' }"
          " | % InstanceId")
    out = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True,
                         creationflags=NO_WINDOW).stdout
    return [l.strip() for l in out.splitlines() if l.strip()]


def hidhide_setup():
    """One-time: allow this exe, add every connected XInput pad to the hidden list. Returns the pad ids."""
    hidhide('--app-reg', sys.executable)  # frozen: Foxi.exe; dev: python.exe
    ids = pad_instance_ids()
    for i in ids:
        hidhide('--dev-hide', i)
    return ids


def hidhide_sync():
    """Every start: allow whichever path Foxi runs from (portable), and hide pads not yet listed
    (a replug can give the HID child a new instance id). Idempotent."""
    if not hidhide_installed():
        return
    if sys.executable.lower() not in hidhide('--app-list').stdout.lower():
        hidhide('--app-reg', sys.executable)
    listed = hidhide('--dev-list').stdout.lower()
    if any(i.lower() not in listed for i in pad_instance_ids()):
        hidhide_setup()


def cloak(on):
    if hidhide_installed():
        hidhide('--cloak-on' if on else '--cloak-off')


APP_DIR = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, 'data')  # portable: profile + log live next to Foxi.exe
LOG_PATH = os.path.join(DATA_DIR, 'foxi.log')


def log_crash():
    import traceback
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(f'--- {time.ctime()}\n{traceback.format_exc()}\n')


class GP(Structure):
    _fields_ = [('wButtons', c_ushort), ('bLT', c_ubyte), ('bRT', c_ubyte),
                ('sLX', c_short), ('sLY', c_short), ('sRX', c_short), ('sRY', c_short)]


class ST(Structure):
    _fields_ = [('dwPacket', c_uint), ('pad', GP)]


class VIB(Structure):
    _fields_ = [('l', c_ushort), ('r', c_ushort)]


class Engine:
    """Public, read-only for the GUI: mode, connected, pad, held, error, log.
    GUI -> engine: cmds.put('toggle'); set .paused=True to capture combos without firing them."""

    def __init__(self, cfg_path):
        self.cfg_path = cfg_path
        if not os.path.exists(cfg_path):
            save_config(cfg_path, DEFAULT_CONFIG)
        self.cfg, self.map, self.mtime, self.error = None, {}, 0, None
        self.mode, self.paused, self.running = 'on', False, True
        self.connected, self.pad, self.held = False, GP(), 0
        self.log = collections.deque(maxlen=8)
        self.cmds = queue.SimpleQueue()
        self.active = {}  # combo -> [action, next_repeat_time]
        self.prev, self.acc, self.switch_until = 0, [0.0] * 4, 0  # acc: sub-pixel mx, my, wheel, hwheel
        self.edge_time, self.dpi = 0.0, None  # dpi: speed picked with the dpi action; None = mouse_speed
        self.pack = None  # cursor pack currently applied
        self.rec_peak = 0  # buttons seen while paused (combo recording)
        self.counts, self.cnt_prev = {}, 0  # diagnostics: presses per button since reset
        self.switch_combo = 0  # buttons of the switcher mapping, to know when it's released
        self.menu_open, self.menu_index, self.menu_combo = False, 0, 0  # quick menu (START by default)
        self.on_menu = lambda opened: None  # GUI shows/hides the menu window (set by foxi.py)
        self.step_us, self.loop_ms = 0.0, 1.0  # diagnostics: processing time per tick, loop period (EWMA)
        self.xi = ctypes.WinDLL('xinput1_4')
        self.get_state = self.xi[100]  # XInputGetStateEx (undocumented ordinal): like GetState but reports HOME

    def buzz(self, n):
        for _ in range(n):
            self.xi.XInputSetState(0, byref(VIB(40000, 40000))); time.sleep(0.12)
            self.xi.XInputSetState(0, byref(VIB(0, 0))); time.sleep(0.12)

    def end_switcher(self):
        if self.switch_until:
            key(VK['alt'], True); self.switch_until = 0

    def release_all(self):
        for a, _ in self.active.values():
            stop(a)
        self.active.clear(); self.end_switcher()

    def apply_cursors(self):
        want = self.cfg['cursor_pack'] if self.mode == 'on' else 'windows'
        if want != self.pack:
            set_cursor_pack(want)
            self.pack = want

    def set_mode(self, m):
        self.mode = m
        for c in [c for c in self.active if self.active[c][0] != 'toggle']:
            stop(self.active.pop(c)[0])
        self.end_switcher()
        cloak(m == 'on')
        self.apply_cursors()
        self.log.append('mode ON (mouse + keyboard)' if m == 'on' else 'mode GAME (normal controller)')
        self.buzz(2 if m == 'on' else 3)

    def open_menu(self, combo):
        for c in [c for c in self.active if c != combo]:
            stop(self.active.pop(c)[0])  # nothing keeps repeating behind the menu
        self.end_switcher()
        self.menu_open, self.menu_index, self.menu_combo = True, 0, combo
        self.on_menu(True)

    def close_menu(self):
        if self.menu_open:
            self.menu_open = False
            self.on_menu(False)

    def menu_pick(self, i):
        """Hide the menu, then act. The menu window never takes focus, so input lands where you were."""
        items = self.cfg['quick_menu']
        self.close_menu()
        if 0 <= i < len(items):
            start(items[i]['action'])
            self.log.append(f"menu -> {items[i]['label']}")

    def menu_step(self, held):
        """While the menu is open the D-pad/X/B drive it instead of their mappings; sticks keep working."""
        new, n = held & ~self.prev, max(len(self.cfg['quick_menu']), 1)
        if new & BUTTONS['UP']:
            self.menu_index = (self.menu_index - 1) % n
        if new & BUTTONS['DOWN']:
            self.menu_index = (self.menu_index + 1) % n
        if new & (BUTTONS['X'] | BUTTONS['A']):
            self.menu_pick(self.menu_index)
        elif new & (BUTTONS['B'] | self.menu_combo):
            self.close_menu()

    def reload(self):
        m = os.path.getmtime(self.cfg_path)
        if m == self.mtime:
            return
        self.mtime = m
        try:
            self.cfg, self.map = load_config(self.cfg_path)
            self.error, self.dpi = None, None  # settings saved from the GUI win over a dpi-picked speed
            self.apply_cursors()
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            self.error = f'foxi.json: {e} (keeping previous settings)'

    def step(self, now, dt):
        s = ST()
        if self.get_state(0, byref(s)) != 0:  # asleep / dongle unplugged
            self.connected = False
            self.release_all(); self.prev = self.held = 0
            return 0.5
        self.connected, p, cfg = True, s.pad, self.cfg
        t = cfg['trigger_threshold']
        held = p.wButtons | (BUTTONS['LT'] if p.bLT > t else 0) | (BUTTONS['RT'] if p.bRT > t else 0)
        self.pad, self.held = p, held
        rise, self.cnt_prev = held & ~self.cnt_prev, held
        if rise:
            for n, b in BUTTONS.items():
                if rise & b:
                    self.counts[n] = self.counts.get(n, 0) + 1
        if self.paused:  # GUI is recording a combo: collect every button seen, at pad rate (short taps count)
            self.release_all(); self.prev = held
            self.rec_peak |= held
            return 0.001

        for combo in [c for c in self.active if c & ~held]:  # any button of the combo let go
            stop(self.active.pop(combo)[0])

        if self.menu_open and self.mode != 'on':
            self.close_menu()
        menu_tick = self.menu_open  # the press that picks/closes is used up by the menu: X must not also Enter
        if menu_tick:
            self.menu_step(held)
        for bit in BUTTONS.values() if not menu_tick else ():
            if not held & bit & ~self.prev:
                continue
            combo = pick(bit, held, self.map)
            if combo is None or combo in self.active:
                continue
            action = self.map[combo]
            if action == 'toggle':
                self.set_mode('game' if self.mode == 'on' else 'on')
            elif self.mode != 'on':
                continue
            elif action == 'dpi':
                levels = cfg['dpi_levels']
                i = next((i for i, v in enumerate(levels) if v > self.speed()), 0)  # next level up, wrap to lowest
                self.dpi = levels[i]
                self.log.append(f'cursor speed {self.dpi} px/s')
                self.buzz(i + 1)  # 1 buzz = slowest level
            elif action == 'menu':
                self.open_menu(combo)
            elif action == 'switcher':
                if not self.switch_until:
                    key(VK['alt'], False)  # hold Alt: first Tab opens the switcher
                tap('tab')
                self.switch_until = now + cfg['switcher_timeout_ms'] / 1000
                self.switch_combo = combo
            else:
                start(action)
                self.log.append(f'{combo_name(combo)} -> {action}')
            rep = action.startswith('repeat:')
            self.active[combo] = [action, now + cfg['repeat_delay_ms'] / 1000 if rep else math.inf]

        if self.switch_until and now > self.switch_until and not held & self.switch_combo:
            self.end_switcher()  # no press for switcher_timeout_ms -> let go of Alt, window picked

        for a in self.active.values():
            if now >= a[1]:
                tap(a[0].partition(':')[2])
                a[1] = now + cfg['repeat_rate_ms'] / 1000

        if self.mode == 'on':
            acc = self.acc
            cur, scr = route((p.sLX, p.sLY), (p.sRX, p.sRY), cfg)
            if cur:
                x, y = cur
                vx, vy = stick(x, y, cfg['deadzone'], cfg['mouse_curve'])
                self.edge_time = self.edge_time + dt if math.hypot(x, y) > 0.92 * 32767 else 0.0
                speed = self.speed() * boost(self.edge_time, cfg)
                acc[0] += vx * speed * dt; acc[1] += vy * speed * dt
                ix, iy = int(acc[0]), int(acc[1])
                if ix or iy:
                    acc[0] -= ix; acc[1] -= iy
                    move_cursor(ix, iy)
            if not scr:
                acc[2] = acc[3] = 0.0
            else:
                vx, vy = stick(*scr, cfg['deadzone'], cfg['scroll_curve'])
                if vx == vy == 0:
                    acc[2] = acc[3] = 0.0  # stick released: drop leftovers so the next nudge doesn't scroll instantly
                acc[2] += -vy * cfg['scroll_speed'] * dt; acc[3] += vx * cfg['scroll_speed'] * dt  # in notches
                for i, flag in ((2, 0x800), (3, 0x1000)):  # WHEEL, HWHEEL
                    if abs(acc[i]) >= 1:  # whole notches only: many apps treat ANY wheel event as a full notch
                        n = int(acc[i]); acc[i] -= n
                        user32.mouse_event(flag, 0, 0, n * 120, 0)
        self.prev = held
        return 0.001  # pad reports at ~1000 Hz; match it for a smooth cursor

    def speed(self):
        return self.dpi or self.cfg['mouse_speed']

    def run(self):
        ctypes.windll.winmm.timeBeginPeriod(1)  # 1ms sleeps -> smooth cursor
        self.reload()
        if self.cfg is None:  # broken config on first load: fall back to defaults in memory
            self.cfg, self.map = copy.deepcopy(DEFAULT_CONFIG), {parse_combo(k): v for k, v in DEFAULT_CONFIG['map'].items()}
        hidhide_sync()
        cloak(True)
        last = next_reload = time.perf_counter()
        try:
            while self.running:
                while not self.cmds.empty():
                    if self.cmds.get() == 'toggle':
                        self.set_mode('game' if self.mode == 'on' else 'on')
                now = time.perf_counter()
                if now >= next_reload:  # stat foxi.json twice a second, not every 1ms tick
                    self.reload(); next_reload = now + 0.5
                wait = self.step(now, min(now - last, 0.05))  # clamp: no cursor jump after a sleep/buzz
                self.step_us += ((time.perf_counter() - now) * 1e6 - self.step_us) * 0.01
                self.loop_ms += ((now - last) * 1000 - self.loop_ms) * 0.01
                last = now
                time.sleep(wait)
        except Exception:  # windowed exe has no console: surface it in the GUI and foxi.log
            log_crash()
            self.error = f'Engine stopped: {sys.exc_info()[1]!r} (details in foxi.log)'
        finally:
            self.release_all()  # never leave a mouse button or Alt stuck down
            self.close_menu()
            cloak(False)        # hand the controller back to Windows
            set_cursor_pack('windows')


def test_menu_press_is_consumed():
    """Menu, release, X: only the menu item runs. X's own mapping (Enter) must not fire on the same press."""
    import tempfile
    g = globals()
    saved = {k: g[k] for k in ('start', 'tap', 'key', 'cloak', 'set_cursor_pack', 'hidhide_sync')}
    fired = []
    g.update(start=fired.append, tap=lambda s: fired.append('tap:' + s), key=lambda *a: None,
             cloak=lambda on: None, set_cursor_pack=lambda p: None, hidhide_sync=lambda: None)
    try:
        eng = Engine(os.path.join(tempfile.mkdtemp(), 'foxi.json'))
        eng.reload()
        eng.buzz = lambda n: None
        state = {'b': 0}

        def fake_get(_, ref):
            ref._obj.pad.wButtons = state['b']
            return 0
        eng.get_state = fake_get
        for b, expect_open in ((BUTTONS['START'], True), (0, True), (BUTTONS['X'], False), (0, False)):
            state['b'] = b
            eng.step(time.perf_counter(), 0.001)
            assert eng.menu_open == expect_open, (b, eng.menu_open)
        assert fired == ['keys:ctrl+c'], fired  # Copy only: no 'keys:enter'
        state['b'] = BUTTONS['X']; eng.step(time.perf_counter(), 0.001)  # menu closed: X is Enter again
        assert fired[-1] == 'keys:enter', fired
    finally:
        g.update(saved)


def test():
    test_menu_press_is_consumed()
    mp = {parse_combo(k): k for k in ['UP', 'X', 'UP+X', 'LT', 'LB+RB']}
    U, X, LT, LB, RB = (BUTTONS[k] for k in ['UP', 'X', 'LT', 'LB', 'RB'])
    assert mp[pick(U, U, mp)] == 'UP'
    assert mp[pick(X, U | X, mp)] == 'UP+X'          # chord beats single
    assert mp[pick(U, LT | U, mp)] == 'UP'           # arrow works while dragging
    assert mp[pick(RB, LB | RB, mp)] == 'LB+RB'
    assert pick(RB, RB, mp) is None                  # RB alone unmapped
    assert combo_name(U | X) == 'UP+X' and parse_combo(combo_name(LB | RB | LT)) == LB | RB | LT
    assert stick(1000, 1000, 0.1, 2) == (0.0, 0.0)   # inside deadzone
    vx, vy = stick(0, 32767, 0.1, 2)
    assert abs(vx) < 1e-9 and abs(vy + 1) < 1e-9     # full up -> screen up, magnitude 1
    assert 0 < stick(32767 // 2, 0, 0.1, 2)[0] < 0.5  # curve makes half-tilt slow
    for bad in ['keys:ctrl+nope', 'mouse:side', 'jump:x', 'run: ']:
        try: check_action(bad); assert False, bad
        except ValueError: pass
    c = DEFAULT_CONFIG
    validate(c)  # default quick menu items are valid
    for bad in ({**c, 'quick_menu': [{'label': '', 'action': 'text:x'}]},
                {**c, 'quick_menu': [{'label': 'x', 'action': 'menu'}]},
                {**c, 'quick_menu': [{'label': 'x', 'action': 'text:'}]}):
        try: validate(bad); assert False, bad
        except ValueError: pass
    assert ctypes.sizeof(INPUT) == 40  # SendInput rejects a wrong struct size silently
    L, R, Z = (20000, 0), (0, -20000), (0, 0)
    both = {**c, 'mouse_stick': 'both'}
    assert route(L, Z, both) == (L, None) and route(Z, R, both) == (R, None)  # either stick = cursor
    assert route(L, R, both) == (None, (10000, -10000))  # both pushed = scroll by the average
    assert route(L, R, c) == (L, R)  # default: left cursor, right scroll
    for f in OCR:
        assert os.path.exists(os.path.join(RES_DIR, 'vendor', 'cursors', 'macos', f)), f
    assert boost(0, c) == 1 and abs(boost(10, c) - c['mouse_boost']) < 1e-9  # ramps 1x -> boost, then caps
    assert 1 < boost(c['mouse_boost_ramp_ms'] / 2000, c) < c['mouse_boost']
    for good in DEFAULT_CONFIG['map'].values():
        check_action(good)
    try: parse_combo('UP+Q'); assert False
    except ValueError: pass
    print('ok')


if __name__ == '__main__':
    test()
