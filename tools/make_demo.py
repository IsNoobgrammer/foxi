"""Records assets/demo.gif: a short tour of the Foxi window (runs foxi.py, clicks through pages, captures
the client area). Run from the project root: .venv\\Scripts\\python tools\\make_demo.py"""
import ctypes, ctypes.wintypes, os, subprocess, sys, time
from PIL import Image, ImageGrab

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ctypes.windll.user32.SetProcessDPIAware()
u = ctypes.windll.user32


def main():
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, 'foxi.py')], cwd=ROOT)
    h = 0
    sys.path.insert(0, ROOT)
    from foxi import own_window  # by process, never by title: FindWindow would also match a 'foxi' terminal
    for _ in range(80):
        h = own_window('Foxi', p.pid)
        if h:
            break
        time.sleep(0.25)
    time.sleep(5)
    u.ShowWindow(h, 9); u.SetForegroundWindow(h)
    u.SetWindowPos(h, 0, 0, 0, 0, 0, 0x0001 | 0x0004)  # top-left corner so the window is fully on screen
    time.sleep(0.6)
    dpi = u.GetDpiForWindow(h) / 96
    rc = ctypes.wintypes.RECT(); u.GetClientRect(h, ctypes.byref(rc))
    pt = ctypes.wintypes.POINT(0, 0); u.ClientToScreen(h, ctypes.byref(pt))
    box = (pt.x, pt.y, pt.x + rc.right, pt.y + rc.bottom)
    old = ctypes.wintypes.POINT(); u.GetCursorPos(ctypes.byref(old))
    frames = []

    def click(x, y):  # client-area logical px
        u.SetCursorPos(int(pt.x + x * dpi), int(pt.y + y * dpi))
        u.mouse_event(2, 0, 0, 0, 0); u.mouse_event(4, 0, 0, 0, 0)

    def snap(hold=1.8):
        time.sleep(0.9)
        im = ImageGrab.grab(box, all_screens=True).convert('RGB')
        im = im.resize((960, int(960 * im.height / im.width)), Image.LANCZOS)
        frames.append((im, hold))

    nav = lambda i: 20 + 30 + 40 + 44 * i
    snap(2.4)                              # controller page
    click(571, 202); snap(2.4)             # click LB on the pad -> editor drawer
    u.keybd_event(0x1B, 0, 0, 0); u.keybd_event(0x1B, 0, 2, 0); time.sleep(0.4)
    for i in (1, 2, 3, 4, 5):              # shortcuts, combos, cursor & scroll, diagnostics, system
        click(90, nav(i)); snap()
    click(90, nav(0)); time.sleep(0.3)
    u.SetCursorPos(old.x, old.y)
    u.PostMessageW(h, 0x10, 0, 0)
    try:
        p.wait(8)
    except subprocess.TimeoutExpired:
        p.kill()

    pal = [f.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for f, _ in frames]
    out = os.path.join(ROOT, 'assets', 'demo.gif')
    pal[0].save(out, save_all=True, append_images=pal[1:], duration=[int(d * 1000) for _, d in frames], loop=0,
                optimize=True)
    print(f'wrote {out} ({len(frames)} frames, {os.path.getsize(out) // 1024} KB)')


if __name__ == '__main__':
    main()
