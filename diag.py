"""Diagnostics: timed tests that sample the pad in a tight loop (the 1 ms engine loop would under-count a
1000 Hz pad) and turn the samples into numbers. A test only runs while the user starts it, so the busy loop
(one core) never runs in the background."""
import math, statistics as st, threading, time
from ctypes import byref

FULL = 32767


def _pct(v):
    return round(v / FULL * 100, 2)


def _verdict(v, good, ok, lower_is_better=True):
    if lower_is_better:
        return 'good' if v <= good else 'ok' if v <= ok else 'poor'
    return 'good' if v >= good else 'ok' if v >= ok else 'poor'


def analyze_poll(rows, secs):
    ts = [r[0] for r in rows]
    gaps = [(b - a) * 1000 for a, b in zip(ts, ts[1:])]
    busy = sorted(g for g in gaps if g < 50)  # ignore pauses where the stick stood still
    if len(busy) < 50:
        return {'error': 'Not enough movement. Keep rolling a stick for the whole test.'}
    med = st.median(busy)
    hz = 1000 / med
    return {'hz': round(hz), 'median_ms': round(med, 3), 'p10_ms': round(busy[len(busy) // 10], 3),
            'p90_ms': round(busy[len(busy) * 9 // 10], 3), 'jitter_ms': round(st.pstdev(busy), 3),
            'reports': len(rows), 'verdict': _verdict(hz, 900, 240, lower_is_better=False)}


def analyze_rest(rows, secs):
    out = {}
    for name, i in (('LX', 4), ('LY', 5), ('RX', 6), ('RY', 7)):
        v = [r[i] for r in rows]
        out[name] = {'offset_pct': _pct(abs(st.mean(v))), 'jitter_sd': round(st.pstdev(v), 1),
                     'range': [min(v), max(v)]}
    worst_off = max(a['offset_pct'] for a in out.values())
    worst_sd = max(a['jitter_sd'] for a in out.values())
    moved = max(max(abs(x) for x in a['range']) for a in out.values())
    if moved > 0.5 * FULL:
        return {'error': 'A stick moved during the test. Put the controller down and don\'t touch it.'}
    return {'axes': out, 'drift_pct': worst_off, 'jitter_sd': worst_sd,
            'lt_rest': max(r[2] for r in rows), 'rt_rest': max(r[3] for r in rows),
            'drift_verdict': _verdict(worst_off, 1.0, 3.0), 'jitter_verdict': _verdict(worst_sd, 40, 200),
            'samples': len(rows)}


def _bins(pts):
    bins = [0.0] * 36
    for x, y in pts:
        a = int((math.degrees(math.atan2(y, x)) % 360) // 10)
        bins[a] = max(bins[a], math.hypot(x, y) / FULL)
    return bins


def analyze_range(rows, secs):
    out = {}
    for side, (ix, iy) in (('left', (4, 5)), ('right', (6, 7))):
        pts = [(r[ix], r[iy]) for r in rows]
        bins = _bins(pts)
        hit = [b for b in bins if b > 0.5]
        res = {'bins': [round(b, 3) for b in bins], 'covered': len(hit),
               'x': [min(p[0] for p in pts), max(p[0] for p in pts)], 'y': [min(p[1] for p in pts), max(p[1] for p in pts)]}
        if hit:
            err = st.mean(abs(b - 1) for b in hit) * 100
            res.update(min=round(min(hit), 3), avg=round(st.mean(hit), 3), max=round(max(hit), 3),
                       circularity_pct=round(err, 1), verdict=_verdict(err, 5, 10) if len(hit) >= 32 else 'incomplete')
        out[side] = res
    return out


def analyze_deadzone(rows, secs):
    out = {}
    for side, (ix, iy) in (('left', (4, 5)), ('right', (6, 7))):
        pts = [(r[ix], r[iy]) for r in rows]
        nz = sorted(math.hypot(x, y) / FULL for x, y in pts if (x, y) != (0, 0))
        out[side] = {'min_output_pct': round(nz[0] * 100, 2) if nz else None,
                     'zero_share_pct': round(sum(1 for p in pts if p == (0, 0)) / max(len(pts), 1) * 100, 1),
                     'distinct_x': len({p[0] for p in pts}), 'distinct_y': len({p[1] for p in pts})}
        if nz:
            out[side]['verdict'] = _verdict(nz[0] * 100, 3, 8)
    return out


def analyze_triggers(rows, secs):
    out = {}
    for name, i in (('LT', 2), ('RT', 3)):
        v = [r[i] for r in rows]
        out[name] = {'min': min(v), 'max': max(v), 'distinct': len(set(v)),
                     'verdict': 'good' if max(v) == 255 and min(v) == 0 and len(set(v)) > 40
                     else 'ok' if max(v) >= 240 else 'poor'}
    return out


TESTS = {  # name: (seconds, analyzer)
    'poll': (5, analyze_poll), 'rest': (4, analyze_rest), 'range': (10, analyze_range),
    'deadzone': (8, analyze_deadzone), 'triggers': (6, analyze_triggers),
}


class Sampler:
    def __init__(self, get_state, st_type):
        self._get, self._ST = get_state, st_type
        self.test, self.rows, self.result, self.running, self.t0, self.secs = None, [], None, False, 0.0, 0

    def start(self, test):
        if self.running or test not in TESTS:
            return False
        self.secs = TESTS[test][0]
        self.test, self.rows, self.result, self.running = test, [], None, True
        threading.Thread(target=self._run, daemon=True, name='foxi-diag').start()
        return True

    def stop(self):
        self.running = False

    def _run(self):
        s, last = self._ST(), None
        self.t0 = time.perf_counter()
        while self.running and (t := time.perf_counter() - self.t0) < self.secs:
            if self._get(0, byref(s)) == 0 and s.dwPacket != last:  # store only real new reports
                last, p = s.dwPacket, s.pad
                self.rows.append((t, p.wButtons, p.bLT, p.bRT, p.sLX, p.sLY, p.sRX, p.sRY))
            else:
                time.sleep(0)  # yield the GIL; keeps the engine and UI responsive
        if not self.rows:
            self.result = {'error': 'No controller data. Is it connected (press Home)?'}
        elif self.running:
            try:
                self.result = TESTS[self.test][1](self.rows, self.secs)
            except Exception as e:
                self.result = {'error': f'Analysis failed: {e}'}
        else:
            self.result = {'error': 'Stopped.'}
        self.running = False

    def status(self):
        prog = min(1.0, (time.perf_counter() - self.t0) / self.secs) if self.running and self.secs else (1.0 if self.result else 0)
        live = None
        if self.running and self.test == 'range' and self.rows:  # live coverage drawing
            live = {'bins': [round(b, 3) for b in _bins([(r[4], r[5]) for r in self.rows])],
                    'bins_r': [round(b, 3) for b in _bins([(r[6], r[7]) for r in self.rows])]}
        return {'test': self.test, 'running': self.running, 'progress': round(prog, 3), 'reports': len(self.rows),
                'live': live, 'result': self.result}


if __name__ == '__main__':  # self-check with synthetic data
    rows = [(i / 1000, 0, 0, 0, int(FULL * math.cos(i / 50)), int(FULL * math.sin(i / 50)), 0, 0) for i in range(3000)]
    r = analyze_poll(rows, 3)
    assert 990 <= r['hz'] <= 1010 and r['verdict'] == 'good', r
    rg = analyze_range(rows, 3)['left']
    assert rg['covered'] == 36 and rg['circularity_pct'] < 1 and rg['verdict'] == 'good', rg
    rest = analyze_rest([(i / 1000, 0, 0, 0, 300, -300, 0, 0) for i in range(100)], 1)
    assert rest['drift_pct'] < 1 and rest['jitter_sd'] == 0 and rest['drift_verdict'] == 'good', rest
    assert 'error' in analyze_rest(rows, 3)  # moving stick is rejected
    dz = analyze_deadzone([(0, 0, 0, 0, 0, 0, 0, 0), (0, 0, 0, 0, 700, 0, 0, 0)], 1)['left']
    assert dz['min_output_pct'] == 2.14 and dz['zero_share_pct'] == 50.0, dz
    t = analyze_triggers([(0, 0, v, 0, 0, 0, 0, 0) for v in range(256)], 1)['LT']
    assert t['verdict'] == 'good', t
    print('ok')
