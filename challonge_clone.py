"""Challonge Clone (spartano) — app desktop (pywebview), file unico.

Nessun file esterno: i dati vivono dentro questo stesso .py, nel blocco
DATA_JSON qui sotto. Ad ogni modifica l'app riscrive questo file su disco
(sostituendo solo quella riga), quindi al prossimo avvio i dati sono già lì.
Non aprire/salvare questo file con un editor mentre l'app è in esecuzione:
verrebbe sovrascritto.

Cosa fa: crea tornei a eliminazione singola o doppia, genera il bracket,
permette di inserire i risultati match per match e avanza automaticamente
i vincitori (bye compresi) fino a decretare il campione.

Avvio:
    pip install pywebview
    python challonge_clone.py
"""

import json
import os
import re
import threading
import uuid

import webview

# >>> CHALLONGE_DATA_START >>>
DATA_JSON = "{\"tournaments\": {}, \"__window__\": {\"width\": 900, \"height\": 913, \"x\": 674, \"y\": 174}}"
# <<< CHALLONGE_DATA_END <<<

_lock = threading.Lock()
_DATA_BLOCK_RE = re.compile(
    r'(# >>> CHALLONGE_DATA_START >>>\n).*?(\n# <<< CHALLONGE_DATA_END <<<)',
    re.DOTALL,
)

BYE_ID = '__bye__'


def _load_initial_data():
    try:
        return json.loads(DATA_JSON)
    except json.JSONDecodeError as e:
        print('DATA_JSON illeggibile, riparto vuoto:', e)
        return {'tournaments': {}, '__window__': {}}


def _persist_to_self(data):
    """Riscrive QUESTO file .py sostituendo solo la riga DATA_JSON."""
    script_path = os.path.abspath(__file__)
    with open(script_path, 'r', encoding='utf-8') as f:
        source = f.read()

    new_line = 'DATA_JSON = ' + json.dumps(json.dumps(data, ensure_ascii=True))

    def _replace(m):
        return m.group(1) + new_line + m.group(2)

    new_source, count = _DATA_BLOCK_RE.subn(_replace, source, count=1)
    if count != 1:
        raise RuntimeError('Blocco DATA_JSON non trovato: impossibile salvare nel file.')

    tmp_path = script_path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(new_source)
    os.replace(tmp_path, script_path)


# ---------------------------------------------------------------------------
# Generazione bracket
# ---------------------------------------------------------------------------

def _next_pow2(n):
    p = 1
    while p < n:
        p *= 2
    return max(p, 2)


def _new_match(mid, bracket, round_no, index, p1=None, p2=None,
               p1_source=None, p2_source=None, is_final=False,
               is_reset=False, gf0_id=None):
    return {
        'id': mid, 'bracket': bracket, 'round': round_no, 'index': index,
        'p1': p1, 'p2': p2,
        'p1_source': p1_source, 'p2_source': p2_source,
        'winner': None, 'loser': None,
        'is_final': is_final, 'is_reset': is_reset, 'active': not is_reset,
        'gf0_id': gf0_id,
    }


def _generate_wb(slots):
    """Costruisce il bracket dei vincitori (o l'intero bracket, per la
    eliminazione singola). Ritorna (matches, rounds_ids) dove rounds_ids è
    una lista di round, ognuno lista di id match in ordine."""
    matches = {}
    rounds_ids = []
    size = len(slots)
    round_no = 1
    cur_ids = []
    for i in range(0, size, 2):
        mid = f'wb-1-{i // 2}'
        m = _new_match(mid, 'WB', 1, i // 2, p1=slots[i], p2=slots[i + 1])
        matches[mid] = m
        cur_ids.append(mid)
    rounds_ids.append(cur_ids)
    prev = cur_ids
    round_no = 2
    while len(prev) > 1:
        cur_ids = []
        for i in range(0, len(prev), 2):
            mid = f'wb-{round_no}-{i // 2}'
            m = _new_match(
                mid, 'WB', round_no, i // 2,
                p1_source={'match': prev[i], 'slot': 'winner'},
                p2_source={'match': prev[i + 1], 'slot': 'winner'},
            )
            matches[mid] = m
            cur_ids.append(mid)
        rounds_ids.append(cur_ids)
        prev = cur_ids
        round_no += 1
    matches[prev[0]]['is_final'] = True
    return matches, rounds_ids


def generate_single(participant_ids):
    size = _next_pow2(len(participant_ids))
    slots = list(participant_ids) + [BYE_ID] * (size - len(participant_ids))
    matches, _rounds = _generate_wb(slots)
    return matches


def generate_double(participant_ids):
    size = _next_pow2(len(participant_ids))
    slots = list(participant_ids) + [BYE_ID] * (size - len(participant_ids))
    wb_matches, wb_rounds = _generate_wb(slots)
    matches = dict(wb_matches)
    k = len(wb_rounds)  # numero round WB

    if k <= 1:
        # 2 soli partecipanti: niente losers bracket, la finale WB decide tutto.
        return matches

    lb_rounds = []  # lista di round LB, ognuno lista di id match
    prev_major_ids = None
    for i in range(1, k):
        minor_round_no = 2 * i - 1
        minor_ids = []
        if i == 1:
            wb1 = wb_rounds[0]
            for j in range(0, len(wb1), 2):
                mid = f'lb-{minor_round_no}-{j // 2}'
                m = _new_match(
                    mid, 'LB', minor_round_no, j // 2,
                    p1_source={'match': wb1[j], 'slot': 'loser'},
                    p2_source={'match': wb1[j + 1], 'slot': 'loser'},
                )
                matches[mid] = m
                minor_ids.append(mid)
        else:
            for j in range(0, len(prev_major_ids), 2):
                mid = f'lb-{minor_round_no}-{j // 2}'
                m = _new_match(
                    mid, 'LB', minor_round_no, j // 2,
                    p1_source={'match': prev_major_ids[j], 'slot': 'winner'},
                    p2_source={'match': prev_major_ids[j + 1], 'slot': 'winner'},
                )
                matches[mid] = m
                minor_ids.append(mid)
        lb_rounds.append(minor_ids)

        major_round_no = 2 * i
        wb_feed = wb_rounds[i]  # perdenti del round WB i+1 (0-based: wb_rounds[i])
        major_ids = []
        for j in range(0, len(minor_ids)):
            mid = f'lb-{major_round_no}-{j}'
            m = _new_match(
                mid, 'LB', major_round_no, j,
                p1_source={'match': minor_ids[j], 'slot': 'winner'},
                p2_source={'match': wb_feed[j], 'slot': 'loser'},
            )
            matches[mid] = m
            major_ids.append(mid)
        lb_rounds.append(major_ids)
        prev_major_ids = major_ids

    lb_final_id = lb_rounds[-1][0]
    wb_final_id = wb_rounds[-1][0]

    gf0 = _new_match(
        'gf-0', 'GF', 1, 0,
        p1_source={'match': wb_final_id, 'slot': 'winner'},
        p2_source={'match': lb_final_id, 'slot': 'winner'},
    )
    matches['gf-0'] = gf0
    gf1 = _new_match('gf-1', 'GF', 2, 0, is_reset=True, gf0_id='gf-0')
    matches['gf-1'] = gf1
    return matches


def _get_slot(matches, source):
    src = matches.get(source['match'])
    if not src:
        return None
    return src.get(source['slot'])


def resolve(matches):
    changed = True
    while changed:
        changed = False
        for m in matches.values():
            if m.get('is_reset'):
                if not m['active']:
                    src = matches.get(m['gf0_id'])
                    if src and src.get('winner') is not None and src['winner'] == src.get('p2'):
                        m['active'] = True
                        m['p1'] = src['p1']
                        m['p2'] = src['p2']
                        changed = True
                continue
            if m['winner'] is not None:
                continue
            if m['p1'] is None and m.get('p1_source'):
                v = _get_slot(matches, m['p1_source'])
                if v is not None:
                    m['p1'] = v
                    changed = True
            if m['p2'] is None and m.get('p2_source'):
                v = _get_slot(matches, m['p2_source'])
                if v is not None:
                    m['p2'] = v
                    changed = True
            if m['p1'] is not None and m['p2'] is not None and m['winner'] is None:
                if m['p1'] == BYE_ID and m['p2'] == BYE_ID:
                    m['winner'], m['loser'] = BYE_ID, BYE_ID
                    changed = True
                elif m['p1'] == BYE_ID:
                    m['winner'], m['loser'] = m['p2'], BYE_ID
                    changed = True
                elif m['p2'] == BYE_ID:
                    m['winner'], m['loser'] = m['p1'], BYE_ID
                    changed = True
    return matches


def compute_status(t):
    matches = t['matches']
    if t['mode'] == 'single' or 'gf-0' not in matches:
        final = next((m for m in matches.values() if m.get('is_final')), None)
        if final and final['winner']:
            t['status'] = 'done'
            t['champion'] = final['winner']
        else:
            t['status'] = 'in_progress'
            t['champion'] = None
        return
    gf0 = matches['gf-0']
    gf1 = matches['gf-1']
    if gf1['active'] and gf1['winner']:
        t['status'] = 'done'
        t['champion'] = gf1['winner']
    elif gf0['winner'] and gf0['winner'] == gf0['p1']:
        t['status'] = 'done'
        t['champion'] = gf0['winner']
    else:
        t['status'] = 'in_progress'
        t['champion'] = None


def _downstream_ids(matches, start_id):
    reverse = {}
    for m in matches.values():
        for src_key in ('p1_source', 'p2_source'):
            src = m.get(src_key)
            if src:
                reverse.setdefault(src['match'], []).append(m['id'])
        if m.get('is_reset') and m.get('gf0_id'):
            reverse.setdefault(m['gf0_id'], []).append(m['id'])
    seen = set()
    stack = [start_id]
    while stack:
        cur = stack.pop()
        for nxt in reverse.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


class Api:
    """Esposta al JS come window.pywebview.api. Ogni chiamata è async lato JS
    (ritorna una Promise) anche se qui è sincrona."""

    def __init__(self):
        self._data = _load_initial_data()
        self._data.setdefault('tournaments', {})

    def load_all(self):
        with _lock:
            return self._data

    def create_tournament(self, name, participant_names, mode):
        name = (name or '').strip() or 'Untitled tournament'
        clean_names = [n.strip() for n in participant_names if n and n.strip()]
        if len(clean_names) < 2:
            return {'error': 'At least 2 participants are required.'}
        with _lock:
            participants = {}
            pids = []
            for n in clean_names:
                pid = 'p-' + uuid.uuid4().hex[:8]
                participants[pid] = n
                pids.append(pid)
            if mode == 'double':
                matches = generate_double(pids)
            else:
                mode = 'single'
                matches = generate_single(pids)
            resolve(matches)
            tid = 't-' + uuid.uuid4().hex[:8]
            t = {
                'id': tid, 'name': name, 'mode': mode,
                'participants': participants, 'matches': matches,
                'status': 'in_progress', 'champion': None,
            }
            compute_status(t)
            self._data['tournaments'][tid] = t
            _persist_to_self(self._data)
            return t

    def set_result(self, tid, match_id, winner_id):
        with _lock:
            t = self._data['tournaments'].get(tid)
            if not t:
                return {'error': 'Tournament not found.'}
            m = t['matches'].get(match_id)
            if not m or m['p1'] is None or m['p2'] is None:
                return {'error': 'Match is not ready yet.'}
            if winner_id not in (m['p1'], m['p2']):
                return {'error': 'Invalid winner.'}
            m['winner'] = winner_id
            m['loser'] = m['p2'] if winner_id == m['p1'] else m['p1']
            resolve(t['matches'])
            compute_status(t)
            _persist_to_self(self._data)
            return t

    def clear_result(self, tid, match_id):
        with _lock:
            t = self._data['tournaments'].get(tid)
            if not t:
                return {'error': 'Tournament not found.'}
            matches = t['matches']
            m = matches.get(match_id)
            if not m or m['winner'] is None:
                return t
            downstream = _downstream_ids(matches, match_id)
            m['winner'] = None
            m['loser'] = None
            for did in downstream:
                dm = matches[did]
                if dm.get('is_reset'):
                    dm['active'] = False
                    dm['p1'] = None
                    dm['p2'] = None
                else:
                    if dm.get('p1_source'):
                        dm['p1'] = None
                    if dm.get('p2_source'):
                        dm['p2'] = None
                dm['winner'] = None
                dm['loser'] = None
            resolve(matches)
            compute_status(t)
            _persist_to_self(self._data)
            return t

    def delete_tournament(self, tid):
        with _lock:
            self._data['tournaments'].pop(tid, None)
            _persist_to_self(self._data)
            return True


HTML = """
<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>Challonge Clone</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@500;600;700;800&family=Manrope:ital@0&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0a0b0f;
    --bg-soft: #0f1117;
    --surface: #161923;
    --surface-2: #1d2130;
    --surface-3: #262c3f;
    --border: #2a2f42;
    --border-strong: #3a4159;
    --text: #eef0f6;
    --text-muted: #99a0b8;
    --text-faint: #656d84;
    --accent: #e5233f;
    --accent-hover: #ff2f4d;
    --accent-soft: rgba(229, 35, 63, .14);
    --gold: #f2b705;
    --gold-soft: rgba(242, 183, 5, .14);
    --success: #2fd66b;
    --success-soft: rgba(47, 214, 107, .14);
    --radius-sm: 7px;
    --radius-md: 11px;
    --radius-lg: 16px;
    --shadow-sm: 0 1px 2px rgba(0,0,0,.35);
    --shadow-md: 0 10px 28px -12px rgba(0,0,0,.65);
    --shadow-lg: 0 24px 56px -20px rgba(0,0,0,.7);
    --font: 'Manrope', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
    --ease-out-expo: cubic-bezier(.16,1,.3,1);
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0; background: var(--bg); color: var(--text); font-family: var(--font);
    -webkit-font-smoothing: antialiased; font-size: 15px; line-height: 1.5;
    position: relative; overflow-x: hidden;
  }
  h1, h2, h3 { font-family: var(--font); letter-spacing: -0.02em; font-weight: 800; }
  ::selection { background: var(--accent); color: #fff; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
  ::-webkit-scrollbar { height: 10px; width: 10px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border-strong); border-radius: 8px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--accent); }
  .hidden { display: none !important; }
  .icon { width: 1em; height: 1em; display: inline-block; vertical-align: -0.15em; flex-shrink: 0; }

  /* Sfondo atmosferico: due aloni che respirano lentamente dietro tutto */
  .ambient { position: fixed; inset: 0; z-index: 0; overflow: hidden; pointer-events: none; }
  .ambient::before, .ambient::after {
    content: ''; position: absolute; width: 60vw; height: 60vw; border-radius: 50%;
    filter: blur(90px); opacity: .16; will-change: transform;
  }
  .ambient::before {
    background: radial-gradient(circle, var(--accent), transparent 70%);
    top: -22vw; left: -14vw; animation: driftA 26s ease-in-out infinite;
  }
  .ambient::after {
    background: radial-gradient(circle, var(--gold), transparent 70%);
    bottom: -26vw; right: -16vw; animation: driftB 32s ease-in-out infinite;
  }
  .grain { position: fixed; inset: 0; z-index: 0; pointer-events: none; opacity: .5;
    background-image: radial-gradient(rgba(255,255,255,.035) 1px, transparent 1px);
    background-size: 3px 3px;
  }
  @keyframes driftA {
    0%, 100% { transform: translate(0, 0) scale(1); }
    50% { transform: translate(6vw, 5vw) scale(1.12); }
  }
  @keyframes driftB {
    0%, 100% { transform: translate(0, 0) scale(1); }
    50% { transform: translate(-5vw, -6vw) scale(1.1); }
  }
  @keyframes fadeSlideUp {
    from { opacity: 0; transform: translateY(16px); }
    to { opacity: 1; transform: translateY(0); }
  }
  @keyframes popIn {
    from { opacity: 0; transform: scale(.92); }
    to { opacity: 1; transform: scale(1); }
  }
  @keyframes glowPulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(242,183,5,.45), var(--shadow-md); }
    50% { box-shadow: 0 0 28px 6px rgba(242,183,5,.4), var(--shadow-md); }
  }
  @keyframes ringPulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(242,183,5,.5); }
    50% { box-shadow: 0 0 0 5px rgba(242,183,5,0); }
  }
  @keyframes shine {
    from { transform: translateX(-140%) skewX(-12deg); }
    to { transform: translateX(240%) skewX(-12deg); }
  }
  @keyframes chevronFlow {
    0% { opacity: .35; transform: translateX(0); }
    50% { opacity: 1; transform: translateX(3px); }
    100% { opacity: .35; transform: translateX(0); }
  }
  @keyframes twinkle {
    0%, 100% { opacity: .25; transform: scale(.7); }
    50% { opacity: 1; transform: scale(1.2); }
  }
  main, header { position: relative; z-index: 1; }
  #list-view, #tournament-view { animation: fadeSlideUp .5s var(--ease-out-expo) both; }

  /* Header */
  header {
    padding: 14px 28px; background: rgba(15,17,23,.78); backdrop-filter: blur(14px);
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between;
    position: sticky; top: 0; z-index: 20;
  }
  header::after {
    content: ''; position: absolute; left: 0; right: 0; bottom: -1px; height: 1px;
    background: linear-gradient(90deg, transparent, var(--accent), var(--gold), transparent);
    background-size: 200% 100%; animation: chevronFlow 6s linear infinite;
    opacity: .7;
  }
  .brand { display: flex; align-items: center; gap: 12px; }
  .brand-mark {
    width: 38px; height: 38px; border-radius: 11px;
    background: linear-gradient(145deg, var(--accent-hover), var(--accent) 60%, #a11530);
    display: flex; align-items: center; justify-content: center; color: #fff; font-size: 19px;
    box-shadow: 0 8px 20px -4px rgba(229,35,63,.6), inset 0 1px 0 rgba(255,255,255,.25);
    transition: transform .3s var(--ease-out-expo), box-shadow .3s;
  }
  .brand:hover .brand-mark { transform: rotate(-8deg) scale(1.06); box-shadow: 0 10px 26px -4px rgba(229,35,63,.75), inset 0 1px 0 rgba(255,255,255,.3); }
  .brand-text h1 { margin: 0; font-size: 17px; line-height: 1.1; }
  .brand-text p { margin: 2px 0 0; font-size: 12px; color: var(--text-faint); font-weight: 500; }

  .header-actions { display: flex; align-items: center; gap: 10px; }
  .theme-picker {
    position: relative; display: flex; align-items: center; gap: 6px;
    border: 1px solid var(--border); background: var(--surface-2); border-radius: var(--radius-sm);
    padding: 7px 10px; transition: border-color .15s, background .15s;
  }
  .theme-picker:hover { border-color: var(--border-strong); background: var(--surface-3); }
  .theme-picker-icon { display: flex; color: var(--text-faint); width: 14px; height: 14px; }
  .theme-picker-icon .icon { width: 14px; height: 14px; }
  .theme-picker-chevron { display: flex; color: var(--text-faint); width: 12px; height: 12px; pointer-events: none; }
  .theme-picker-chevron .icon { width: 12px; height: 12px; transform: rotate(90deg); }
  #theme-select {
    appearance: none; -webkit-appearance: none; border: none; background: transparent; color: var(--text);
    font-family: var(--font); font-size: 13px; font-weight: 700; cursor: pointer; padding-right: 2px;
  }
  #theme-select:focus { outline: none; }
  #theme-select option { background: var(--surface-2); color: var(--text); }

  /* Buttons */
  button { font-family: var(--font); }
  .btn {
    cursor: pointer; border: 1px solid var(--border); background: var(--surface-2); color: var(--text);
    border-radius: var(--radius-sm); padding: 9px 16px; font-size: 13.5px; font-weight: 700;
    display: inline-flex; align-items: center; gap: 7px;
    transition: border-color .18s, background .18s, transform .15s var(--ease-out-expo), box-shadow .25s;
    position: relative; overflow: hidden;
  }
  .btn:hover { border-color: var(--border-strong); background: var(--surface-3); transform: translateY(-1px); }
  .btn:active { transform: translateY(0) scale(.97); }
  .btn-primary {
    background: linear-gradient(135deg, var(--accent-hover), var(--accent) 55%, #b5192f);
    border-color: var(--accent); color: #fff; box-shadow: 0 8px 22px -8px rgba(229,35,63,.7);
  }
  .btn-primary::before {
    content: ''; position: absolute; inset: 0; width: 40%;
    background: linear-gradient(120deg, transparent, rgba(255,255,255,.35), transparent);
    transform: translateX(-140%) skewX(-12deg);
  }
  .btn-primary:hover { box-shadow: 0 12px 30px -8px rgba(229,35,63,.85); transform: translateY(-1px); transition-duration: .35s; }
  .btn-primary:hover::before { animation: shine 1.4s var(--ease-out-expo); }
  .btn-danger { background: transparent; border-color: var(--border); color: var(--accent); }
  .btn-danger:hover { background: var(--accent-soft); border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  .btn-ghost { background: transparent; border-color: transparent; color: var(--text-muted); padding: 7px 10px; }
  .btn-ghost:hover { background: var(--surface-2); color: var(--text); }
  .btn-sm { padding: 6px 11px; font-size: 12px; }
  .btn-block { width: 100%; justify-content: center; }

  main { padding: 32px; max-width: 1400px; margin: 0 auto; }

  /* Create card */
  #create-box {
    background: linear-gradient(180deg, var(--surface), var(--bg-soft));
    border: 1px solid var(--border); border-radius: var(--radius-lg);
    padding: 28px; margin: 0 auto 32px; max-width: 820px;
    animation: fadeSlideUp .5s var(--ease-out-expo) both;
  }
  .create-header { display: flex; align-items: center; gap: 10px; margin-bottom: 18px; }
  .create-header .icon { color: var(--gold); width: 20px; height: 20px; }
  #create-box h2 { margin: 0; font-size: 16px; }
  .field { margin-bottom: 16px; }
  .field:last-of-type { margin-bottom: 0; }
  .field-grid { display: grid; grid-template-columns: 1.3fr 1fr; gap: 16px; margin-bottom: 16px; }
  .field-grid .field { margin-bottom: 0; }
  @media (max-width: 560px) { .field-grid { grid-template-columns: 1fr; } }
  .field > label, .field-row > label {
    display: block; margin-bottom: 7px; font-size: 11.5px; color: var(--text-faint);
    text-transform: uppercase; letter-spacing: .06em; font-weight: 700;
  }
  #create-box input[type=text], #create-box textarea {
    width: 100%; background: var(--bg-soft); border: 1px solid var(--border); color: var(--text);
    border-radius: var(--radius-sm); padding: 10px 12px; font-size: 14px; font-family: var(--font);
    transition: border-color .15s, box-shadow .15s;
  }
  #create-box input[type=text]::placeholder, #create-box textarea::placeholder { color: var(--text-faint); }
  #create-box input[type=text]:focus, #create-box textarea:focus {
    outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft);
  }
  #create-box textarea { min-height: 120px; resize: vertical; line-height: 1.6; }
  .field-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .field-hint { font-size: 12px; color: var(--text-faint); }

  .switch-track {
    position: relative; display: flex; width: 100%; height: 42px;
    background: var(--bg-soft); border: 1px solid var(--border); border-radius: 11px; padding: 4px;
  }
  .switch-thumb {
    position: absolute; top: 4px; left: 4px; width: calc(50% - 4px); height: calc(100% - 8px);
    border-radius: 8px; z-index: 0; pointer-events: none;
    background: linear-gradient(135deg, var(--accent-hover), var(--accent) 60%, #b5192f);
    box-shadow: 0 8px 20px -6px rgba(229,35,63,.7), inset 0 1px 0 rgba(255,255,255,.2);
    transition: transform .4s var(--ease-out-expo);
  }
  .switch-track.double .switch-thumb { transform: translateX(100%); }
  .seg-option {
    position: relative; z-index: 1; flex: 1; text-align: center; font-size: 14px;
    color: var(--text-muted); cursor: pointer; transition: color .25s; font-weight: 700;
    display: flex; align-items: center; justify-content: center; gap: 7px;
  }
  .seg-option input { position: absolute; opacity: 0; pointer-events: none; }
  .seg-option.active { color: #fff; }
  .seg-option .icon { width: 16px; height: 16px; }

  #err-msg { color: var(--accent); font-size: 13px; margin-top: 12px; min-height: 16px; font-weight: 600; }

  /* List */
  .section-heading {
    display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px;
  }
  .section-heading h2 {
    font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: var(--text-faint); margin: 0;
  }
  .section-heading .count { color: var(--text-faint); font-weight: 600; font-size: 12px; }

  #list-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
  .t-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg);
    padding: 18px; transition: transform .25s var(--ease-out-expo), box-shadow .3s, border-color .25s;
    display: flex; flex-direction: column; gap: 12px; position: relative;
    animation: fadeSlideUp .5s var(--ease-out-expo) both;
    animation-delay: calc(var(--i, 0) * 55ms);
  }
  .t-card::before {
    content: ''; position: absolute; inset: -1px; border-radius: inherit; padding: 1px;
    background: linear-gradient(135deg, var(--accent), transparent 40%, transparent 60%, var(--gold));
    -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
    -webkit-mask-composite: xor; mask-composite: exclude;
    opacity: 0; transition: opacity .3s;
  }
  .t-card:hover { transform: translateY(-4px); box-shadow: var(--shadow-lg); border-color: transparent; }
  .t-card:hover::before { opacity: .8; }
  .t-card-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
  .t-card h3 { margin: 0; font-size: 15.5px; line-height: 1.3; }

  .status-pill {
    display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 20px;
    font-size: 10.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .05em; white-space: nowrap;
  }
  .status-pill .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
  .status-pill.done { background: var(--success-soft); color: var(--success); }
  .status-pill.in_progress { background: var(--gold-soft); color: var(--gold); box-shadow: 0 0 0 0 rgba(242,183,5,.5); animation: ringPulse 2.4s ease-in-out infinite; }

  .t-card .meta { display: flex; flex-direction: column; gap: 6px; font-size: 12.5px; color: var(--text-muted); }
  .t-card .meta-row { display: flex; align-items: center; gap: 7px; }
  .t-card .meta-row .icon { color: var(--text-faint); width: 15px; height: 15px; }
  .t-card .meta-row.champion { color: var(--gold); font-weight: 700; }
  .t-card .meta-row.champion .icon { color: var(--gold); }

  .t-card .card-actions { display: flex; gap: 8px; margin-top: auto; padding-top: 4px; }
  .t-card .card-actions .btn-primary { flex: 1; }

  .empty-state {
    border: 1.5px dashed var(--border); border-radius: var(--radius-lg); padding: 48px 24px;
    text-align: center; color: var(--text-faint);
  }
  .empty-state .icon { width: 34px; height: 34px; margin-bottom: 12px; color: var(--border-strong); }
  .empty-state p { margin: 0; font-size: 13.5px; }

  /* Tournament view */
  #view-header { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 8px; flex-wrap: wrap; }
  .view-title-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  #view-title { font-size: 21px; margin: 0; }
  .view-meta { display: flex; align-items: center; gap: 8px; color: var(--text-faint); font-size: 12.5px; font-weight: 600; }
  .view-meta .sep { color: var(--border-strong); }

  #champion-wrap { margin: 20px 0; }
  #champion-banner {
    position: relative; background: linear-gradient(135deg, #ffcf3d, var(--gold) 60%, #d99a00);
    color: #23180a; font-weight: 800; padding: 14px 22px; border-radius: var(--radius-md);
    display: inline-flex; align-items: center; gap: 10px; font-size: 15px; overflow: hidden;
    animation: popIn .5s var(--ease-out-expo) both, glowPulse 2.6s ease-in-out .5s infinite;
  }
  #champion-banner .icon { width: 20px; height: 20px; animation: twinkle 1.8s ease-in-out infinite; }
  #champion-banner .spark {
    position: absolute; width: 4px; height: 4px; border-radius: 50%; background: #fff8;
    animation: twinkle 2.2s ease-in-out infinite;
  }

  .bracket-section { margin: 32px 0; }
  .bracket-section-head { display: flex; align-items: center; gap: 10px; margin-bottom: 18px; }
  .bracket-section-head .icon { width: 16px; height: 16px; color: var(--text-faint); }
  .bracket-section h2 {
    font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: var(--text-muted); margin: 0;
  }
  .bracket-section-head::after { content: ''; flex: 1; height: 1px; background: var(--border); }

  .rounds-row { display: flex; align-items: flex-start; overflow: auto; padding: 4px 34px 14px 4px; gap: 34px; }
  .round-col { min-width: 236px; width: 236px; display: flex; flex-direction: column; flex-shrink: 0; }
  .round-title { font-size: 11px; color: var(--text-faint); text-align: center; margin-bottom: 10px; text-transform: uppercase; letter-spacing: .06em; font-weight: 700; }
  .round-title .sub { display: block; text-transform: none; letter-spacing: 0; font-weight: 500; color: var(--text-faint); opacity: .75; margin-top: 2px; font-size: 10.5px; }

  .match-connector {
    position: absolute; top: 50%; right: -23px; transform: translateY(-50%);
    display: flex; align-items: center; justify-content: center; color: var(--border-strong); pointer-events: none;
  }
  .match-connector .icon { width: 15px; height: 15px; }
  .match-connector.filled { color: var(--accent); animation: chevronFlow 1.6s ease-in-out infinite; }

  .match-box {
    position: relative; border: 1px solid var(--border); border-radius: var(--radius-md);
    transition: border-color .2s, box-shadow .3s, transform .2s var(--ease-out-expo);
    animation: fadeSlideUp .45s var(--ease-out-expo) both;
  }
  .match-box.done { border-color: var(--border-strong); }
  .match-box.live { border-color: var(--gold); animation: fadeSlideUp .45s var(--ease-out-expo) both, ringPulse 2.2s ease-in-out infinite; }
  .match-box.live:hover { transform: translateY(-2px); }
  .mb-body { background: var(--surface); border-radius: calc(var(--radius-md) - 1px); overflow: hidden; }

  .slot { display: flex; align-items: center; gap: 9px; padding: 0 10px; height: 40px; font-size: 13px; border-bottom: 1px solid var(--border); background: var(--surface); transition: background .2s; }
  .slot:last-of-type { border-bottom: none; }
  .slot-avatar {
    width: 22px; height: 22px; border-radius: 6px; background: var(--surface-3); color: var(--text-faint);
    display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 800; flex-shrink: 0;
    transition: transform .2s var(--ease-out-expo);
  }
  .slot .name { flex: 1; font-weight: 600; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .slot.winner { background: linear-gradient(90deg, var(--success-soft), transparent); }
  .slot.winner .name { color: var(--success); }
  .slot.winner .slot-avatar { background: rgba(47,214,107,.2); color: var(--success); box-shadow: 0 0 0 3px rgba(47,214,107,.15); }
  .slot.loser .name { color: var(--text-faint); font-weight: 500; }
  .slot.loser .slot-avatar { opacity: .5; }
  .slot.tbd .name { color: var(--text-faint); font-style: italic; font-weight: 500; }
  .slot.tbd .slot-avatar { border: 1.5px dashed var(--border-strong); background: transparent; }
  .slot.bye { opacity: .5; }
  .slot.bye .name { font-style: italic; }
  .pick-btn {
    border: 1px solid var(--border); background: transparent; color: var(--text-faint); cursor: pointer;
    border-radius: 6px; padding: 4px 9px; font-size: 11px; font-weight: 700; font-family: var(--font);
    display: flex; align-items: center; gap: 4px; flex-shrink: 0; transition: all .15s;
  }
  .pick-btn:hover { background: var(--accent); border-color: var(--accent); color: #fff; }
  .pick-btn .icon { width: 12px; height: 12px; }
  .win-icon {
    border: none; background: transparent; cursor: pointer; color: var(--success); flex-shrink: 0;
    width: 22px; height: 22px; border-radius: 6px; display: flex; align-items: center; justify-content: center;
    padding: 0; transition: background .15s, color .15s, transform .15s;
  }
  .win-icon .icon { width: 14px; height: 14px; }
  .win-icon:hover { background: var(--accent-soft); color: var(--accent); transform: scale(1.08); }

  /* Glassmorphism theme: frosted, translucent surfaces over a more visible ambient glow */
  body.theme-glass .ambient::before, body.theme-glass .ambient::after { opacity: .34; }
  body.theme-glass header {
    background: rgba(18, 20, 28, .45); backdrop-filter: blur(20px) saturate(160%); -webkit-backdrop-filter: blur(20px) saturate(160%);
    border-bottom-color: rgba(255,255,255,.08);
  }
  body.theme-glass .theme-picker, body.theme-glass .btn:not(.btn-primary) {
    background: rgba(255,255,255,.06); border-color: rgba(255,255,255,.14);
    backdrop-filter: blur(10px) saturate(150%); -webkit-backdrop-filter: blur(10px) saturate(150%);
  }
  body.theme-glass #create-box {
    background: rgba(255,255,255,.055); border-color: rgba(255,255,255,.14);
    backdrop-filter: blur(22px) saturate(160%); -webkit-backdrop-filter: blur(22px) saturate(160%);
    box-shadow: 0 8px 32px -8px rgba(0,0,0,.3);
  }
  body.theme-glass #create-box input[type=text], body.theme-glass #create-box textarea, body.theme-glass .switch-track {
    background: rgba(0,0,0,.18); border-color: rgba(255,255,255,.12);
    backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
  }
  body.theme-glass .t-card {
    background: rgba(255,255,255,.05); border-color: rgba(255,255,255,.12);
    backdrop-filter: blur(18px) saturate(150%); -webkit-backdrop-filter: blur(18px) saturate(150%);
  }
  body.theme-glass .mb-body {
    background: rgba(255,255,255,.045); backdrop-filter: blur(16px) saturate(150%); -webkit-backdrop-filter: blur(16px) saturate(150%);
  }
  body.theme-glass .slot { background: transparent; }
  body.theme-glass .match-box { border-color: rgba(255,255,255,.14); }
  body.theme-glass .match-box.done { border-color: rgba(255,255,255,.2); }
  body.theme-glass .empty-state {
    background: rgba(255,255,255,.035); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
  }
  body.theme-glass .btn-primary {
    background: linear-gradient(135deg, rgba(255,47,77,.62), rgba(229,35,63,.55) 55%, rgba(181,25,47,.55));
    backdrop-filter: blur(10px) saturate(160%); -webkit-backdrop-filter: blur(10px) saturate(160%);
    border-color: rgba(255,255,255,.2);
  }
  body.theme-glass #champion-banner {
    background: linear-gradient(135deg, rgba(255,207,61,.55), rgba(242,183,5,.5) 60%, rgba(217,154,0,.5));
    backdrop-filter: blur(14px) saturate(160%); -webkit-backdrop-filter: blur(14px) saturate(160%);
    border: 1px solid rgba(255,255,255,.25); color: #1a1305;
  }
</style>
</head>
<body>
<div class="ambient"></div>
<div class="grain"></div>

<header>
  <div class="brand">
    <div class="brand-mark" id="logo-mark"></div>
    <div class="brand-text">
      <h1>Challonge Clone</h1>
      <p>Single and double elimination tournaments</p>
    </div>
  </div>
  <div class="header-actions">
    <div class="theme-picker">
      <span class="theme-picker-icon" id="theme-icon"></span>
      <select id="theme-select">
        <option value="default">Default</option>
        <option value="glass">Glassmorphism</option>
      </select>
      <span class="theme-picker-chevron" id="theme-chevron"></span>
    </div>
    <button class="btn btn-ghost hidden" id="back-btn"></button>
  </div>
</header>

<main>
  <section id="list-view">
    <div id="create-box">
      <div class="create-header">
        <span class="icon" id="create-icon"></span>
        <h2>New tournament</h2>
      </div>
      <div class="field-grid">
        <div class="field">
          <label>Tournament name</label>
          <input type="text" id="t-name" placeholder="E.g. Friday Night Tournament">
        </div>
        <div class="field">
          <label>Elimination format</label>
          <div class="switch-track" id="mode-segmented">
            <div class="switch-thumb"></div>
            <label class="seg-option active" data-value="single">
              <input type="radio" name="mode" value="single" checked> Single
            </label>
            <label class="seg-option" data-value="double">
              <input type="radio" name="mode" value="double"> Double
            </label>
          </div>
        </div>
      </div>
      <div class="field">
        <div class="field-row">
          <label style="margin-bottom:0;">Participants (one per line, minimum 2)</label>
        </div>
        <textarea id="t-participants" placeholder="Mario&#10;Luigi&#10;Peach&#10;Bowser"></textarea>
        <div class="field-row" style="margin-top:8px;">
          <span class="field-hint">Entry order decides the first pairings.</span>
          <button type="button" class="btn btn-sm" id="shuffle-btn"></button>
        </div>
      </div>
      <div id="err-msg"></div>
      <button class="btn btn-primary btn-block" id="create-btn" style="margin-top:14px;"></button>
    </div>

    <div class="section-heading">
      <h2>Tournaments</h2>
      <span class="count" id="list-count"></span>
    </div>
    <div id="list-grid"></div>
  </section>

  <section id="tournament-view" class="hidden">
    <div id="view-header">
      <div class="view-title-row">
        <h2 id="view-title"></h2>
        <div class="view-meta" id="view-meta"></div>
      </div>
      <button class="btn btn-danger" id="delete-btn"></button>
    </div>
    <div id="champion-wrap"></div>
    <div id="brackets-wrap"></div>
  </section>
</main>

<script>
var STATE = { tournaments: {}, currentId: null };
var api = null;
var MATCH_H = 82;
var MATCH_GAP = 16;
var UNIT = MATCH_H + MATCH_GAP;

var ICONS = {
  trophy: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M8 21h8"/><path d="M12 17v4"/><path d="M7 4h10v6a5 5 0 0 1-10 0V4Z"/><path d="M7 5H4a2 2 0 0 0 0 4h3"/><path d="M17 5h3a2 2 0 0 1 0 4h-3"/></svg>',
  dice: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8.5" cy="8.5" r="1.25" fill="currentColor" stroke="none"/><circle cx="15.5" cy="8.5" r="1.25" fill="currentColor" stroke="none"/><circle cx="8.5" cy="15.5" r="1.25" fill="currentColor" stroke="none"/><circle cx="15.5" cy="15.5" r="1.25" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.25" fill="currentColor" stroke="none"/></svg>',
  back: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5"/><path d="M11 18l-6-6 6-6"/></svg>',
  trash: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>',
  plus: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="M5 12h14"/></svg>',
  check: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
  undo: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7v6h6"/><path d="M3 13a9 9 0 1 0 3-6.7L3 9"/></svg>',
  chevron: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>',
  users: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  layers: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5"/><path d="m3 17 9 5 9-5"/></svg>',
  empty: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16v12H8l-4 4V4Z"/></svg>',
  bracket: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h4"/><path d="M4 19h4"/><path d="M8 5v6h4"/><path d="M8 19v-6"/><path d="M16 8h4"/><path d="M12 11h4v-3"/><path d="M12 11v2a2 2 0 0 0 2 2h2"/></svg>'
};

function icon(name) { return ICONS[name] || ''; }

function esc(s) {
  var d = document.createElement('div');
  d.innerText = (s === null || s === undefined) ? '' : s;
  return d.innerHTML;
}

function initials(name) {
  if (!name) return '';
  var parts = name.trim().split(/\\s+/);
  var s = parts[0].charAt(0) + (parts.length > 1 ? parts[1].charAt(0) : (parts[0].charAt(1) || ''));
  return s.toUpperCase();
}

function participantName(t, pid) {
  if (pid === '__bye__') return 'BYE';
  if (!pid) return 'TBD';
  return t.participants[pid] || '???';
}

function renderStaticChrome() {
  document.getElementById('logo-mark').innerHTML = icon('trophy');
  document.getElementById('back-btn').innerHTML = icon('back') + ' Back to list';
  document.getElementById('create-icon').innerHTML = icon('plus');
  document.getElementById('shuffle-btn').innerHTML = icon('dice') + ' Shuffle order';
  document.getElementById('create-btn').innerHTML = icon('plus') + ' Create tournament';
  document.getElementById('delete-btn').innerHTML = icon('trash') + ' Delete tournament';
  document.getElementById('theme-icon').innerHTML = icon('layers');
  document.getElementById('theme-chevron').innerHTML = icon('chevron');
}

function renderList() {
  var grid = document.getElementById('list-grid');
  var ids = Object.keys(STATE.tournaments);
  document.getElementById('list-count').innerText = ids.length + (ids.length === 1 ? ' tournament' : ' tournaments');
  if (ids.length === 0) {
    grid.innerHTML = '';
    var empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.style.gridColumn = '1 / -1';
    empty.innerHTML = icon('empty') + '<p>No tournaments yet.<br>Create one from the form above.</p>';
    grid.appendChild(empty);
    return;
  }
  ids.sort(function(a,b){ return (STATE.tournaments[b].id > STATE.tournaments[a].id) ? -1 : 1; });
  var html = '';
  ids.forEach(function(id, i){
    var t = STATE.tournaments[id];
    var pcount = Object.keys(t.participants).length;
    var modeLabel = t.mode === 'double' ? 'Double elimination' : 'Single elimination';
    var statusLabel = t.status === 'done' ? 'Completed' : 'In progress';
    html += '<div class="t-card" style="--i:' + i + '">' +
      '<div class="t-card-top"><h3>' + esc(t.name) + '</h3>' +
        '<span class="status-pill ' + t.status + '"><span class="dot"></span>' + statusLabel + '</span>' +
      '</div>' +
      '<div class="meta">' +
        '<div class="meta-row">' + icon('layers') + '<span>' + modeLabel + '</span></div>' +
        '<div class="meta-row">' + icon('users') + '<span>' + pcount + ' participants</span></div>' +
        (t.champion ? '<div class="meta-row champion">' + icon('trophy') + '<span>' + esc(participantName(t, t.champion)) + '</span></div>' : '') +
      '</div>' +
      '<div class="card-actions">' +
        '<button class="btn btn-primary open-btn" data-id="' + t.id + '">Open tournament</button>' +
        '<button class="btn btn-ghost del-btn" data-id="' + t.id + '" title="Delete">' + icon('trash') + '</button>' +
      '</div>' +
    '</div>';
  });
  grid.innerHTML = html;
  grid.querySelectorAll('.open-btn').forEach(function(b){
    b.addEventListener('click', function(){ openTournament(b.dataset.id); });
  });
  grid.querySelectorAll('.del-btn').forEach(function(b){
    b.addEventListener('click', function(){
      if (confirm('Delete this tournament?')) {
        api.delete_tournament(b.dataset.id).then(function(){
          delete STATE.tournaments[b.dataset.id];
          renderList();
        });
      }
    });
  });
}

function groupByRound(t, bracket) {
  var rounds = {};
  Object.values(t.matches).forEach(function(m){
    if (m.bracket !== bracket) return;
    if (m.is_reset && !m.active) return;
    (rounds[m.round] = rounds[m.round] || []).push(m);
  });
  var roundNums = Object.keys(rounds).map(Number).sort(function(a,b){return a-b;});
  return roundNums.map(function(r){
    var ms = rounds[r].slice().sort(function(a,b){return a.index-b.index;});
    return { round: r, matches: ms };
  });
}

function roundTitle(bracket, roundNum, totalRounds, mode) {
  if (bracket === 'GF') return roundNum === 1 ? { main: 'Grand Final' } : { main: 'Bracket reset' };
  var fromEnd = totalRounds - roundNum;
  if (bracket === 'WB') {
    if (fromEnd === 0) return { main: mode === 'double' ? 'Winners Final' : 'Final', sub: mode === 'double' ? 'advances to Grand Final' : '' };
    if (fromEnd === 1) return { main: 'Semifinal' };
    if (fromEnd === 2) return { main: 'Quarterfinal' };
    return { main: 'Round ' + roundNum };
  }
  if (fromEnd === 0) return { main: 'Losers Final', sub: 'advances to Grand Final' };
  return { main: 'Losers R' + roundNum };
}

function slotHtml(t, m, pid, done) {
  var isBye = pid === '__bye__';
  var isWinner = done && pid === m.winner && !isBye;
  var slotClass = 'slot';
  if (isBye) slotClass += ' bye';
  else if (done) slotClass += isWinner ? ' winner' : ' loser';
  else if (!pid) slotClass += ' tbd';
  var label = esc(participantName(t, pid));
  var av = pid && !isBye ? esc(initials(participantName(t, pid))) : (isBye ? '—' : '?');
  var trailing = '';
  if (isWinner) {
    trailing = '<button class="win-icon" data-match="' + m.id + '" title="Undo result">' + icon('check') + '</button>';
  } else if (m.p1 && m.p2 && !done) {
    trailing = '<button class="pick-btn" data-match="' + m.id + '" data-winner="' + pid + '">' + icon('check') + ' Wins</button>';
  }
  return '<div class="' + slotClass + '"><span class="slot-avatar">' + av + '</span><span class="name">' + label + '</span>' + trailing + '</div>';
}

function matchBoxHtml(t, m, marginY, hasNext) {
  var done = !!m.winner;
  var cls = 'match-box' + (done ? ' done' : (m.p1 && m.p2 ? ' live' : ''));
  var connector = hasNext
    ? '<span class="match-connector' + (m.winner ? ' filled' : '') + '">' + icon('chevron') + '</span>'
    : '';
  return '<div class="' + cls + '" style="margin:' + marginY + 'px 0">' +
    '<div class="mb-body">' + slotHtml(t, m, m.p1, done) + slotHtml(t, m, m.p2, done) + '</div>' +
    connector + '</div>';
}

function renderBracketSection(t, bracket, label, labelIcon) {
  var groups = groupByRound(t, bracket);
  if (groups.length === 0) return '';
  var totalRounds = Math.max.apply(null, groups.map(function(g){return g.round;}));
  var maxCount = groups[0].matches.length;
  var html = '<div class="bracket-section">' +
    '<div class="bracket-section-head">' + icon(labelIcon) + '<h2>' + label + '</h2></div>' +
    '<div class="rounds-row">';
  groups.forEach(function(g, gi){
    var title = roundTitle(bracket, g.round, totalRounds, t.mode);
    var scale = maxCount / g.matches.length;
    var marginY = Math.round((UNIT * scale - MATCH_H) / 2);
    var hasNext = gi < groups.length - 1;
    html += '<div class="round-col">' +
      '<div class="round-title">' + esc(title.main) + (title.sub ? '<span class="sub">' + esc(title.sub) + '</span>' : '') + '</div>';
    g.matches.forEach(function(m){ html += matchBoxHtml(t, m, marginY, hasNext); });
    html += '</div>';
  });
  html += '</div></div>';
  return html;
}

function renderTournament() {
  var t = STATE.tournaments[STATE.currentId];
  if (!t) return;
  document.getElementById('view-title').innerText = t.name;
  var pcount = Object.keys(t.participants).length;
  var modeLabel = t.mode === 'double' ? 'Double elimination' : 'Single elimination';
  document.getElementById('view-meta').innerHTML =
    '<span>' + esc(modeLabel) + '</span><span class="sep">&middot;</span>' +
    '<span>' + pcount + ' participants</span><span class="sep">&middot;</span>' +
    '<span class="status-pill ' + t.status + '"><span class="dot"></span>' + (t.status === 'done' ? 'Completed' : 'In progress') + '</span>';

  var champWrap = document.getElementById('champion-wrap');
  if (t.champion) {
    var sparks = '';
    for (var s = 0; s < 6; s++) {
      var sx = 8 + Math.random() * 84, sy = 8 + Math.random() * 84, sd = (Math.random() * 1.6).toFixed(2);
      sparks += '<span class="spark" style="left:' + sx + '%; top:' + sy + '%; animation-delay:' + sd + 's"></span>';
    }
    champWrap.innerHTML = '<div id="champion-banner">' + sparks + icon('trophy') +
      ' Champion: ' + esc(participantName(t, t.champion)) + '</div>';
  } else {
    champWrap.innerHTML = '';
  }
  var html = '';
  html += renderBracketSection(t, 'WB', t.mode === 'double' ? 'Winners Bracket' : 'Bracket', 'bracket');
  html += renderBracketSection(t, 'LB', 'Losers Bracket', 'bracket');
  html += renderBracketSection(t, 'GF', 'Grand Final', 'trophy');
  var wrap = document.getElementById('brackets-wrap');
  wrap.innerHTML = html;
  wrap.querySelectorAll('.pick-btn').forEach(function(b){
    b.addEventListener('click', function(){
      api.set_result(t.id, b.dataset.match, b.dataset.winner).then(function(updated){
        if (updated && updated.error) { alert(updated.error); return; }
        STATE.tournaments[t.id] = updated;
        renderTournament();
      });
    });
  });
  wrap.querySelectorAll('.win-icon').forEach(function(b){
    b.addEventListener('click', function(){
      api.clear_result(t.id, b.dataset.match).then(function(updated){
        STATE.tournaments[t.id] = updated;
        renderTournament();
      });
    });
  });
}

function openTournament(id) {
  STATE.currentId = id;
  document.getElementById('list-view').classList.add('hidden');
  document.getElementById('tournament-view').classList.remove('hidden');
  document.getElementById('back-btn').classList.remove('hidden');
  renderTournament();
}

function backToList() {
  STATE.currentId = null;
  document.getElementById('tournament-view').classList.add('hidden');
  document.getElementById('list-view').classList.remove('hidden');
  document.getElementById('back-btn').classList.add('hidden');
  renderList();
}

function applyTheme(name) {
  document.body.classList.toggle('theme-glass', name === 'glass');
  try { localStorage.setItem('challonge-theme', name); } catch (e) {}
}

function boot(data, apiRef) {
  api = apiRef;
  STATE.tournaments = data.tournaments || {};
  renderStaticChrome();
  renderList();

  var savedTheme = 'default';
  try { savedTheme = localStorage.getItem('challonge-theme') || 'default'; } catch (e) {}
  document.getElementById('theme-select').value = savedTheme;
  applyTheme(savedTheme);
  document.getElementById('theme-select').addEventListener('change', function(e){
    applyTheme(e.target.value);
  });

  document.getElementById('back-btn').addEventListener('click', backToList);
  document.getElementById('delete-btn').addEventListener('click', function(){
    if (!STATE.currentId) return;
    if (confirm('Delete this tournament?')) {
      api.delete_tournament(STATE.currentId).then(function(){
        delete STATE.tournaments[STATE.currentId];
        backToList();
      });
    }
  });

  var modeTrack = document.getElementById('mode-segmented');
  modeTrack.querySelectorAll('.seg-option').forEach(function(opt){
    opt.addEventListener('click', function(){
      modeTrack.querySelectorAll('.seg-option').forEach(function(o){ o.classList.remove('active'); });
      opt.classList.add('active');
      modeTrack.classList.toggle('double', opt.dataset.value === 'double');
    });
  });

  document.getElementById('shuffle-btn').addEventListener('click', function(){
    var box = document.getElementById('t-participants');
    var names = box.value.split('\\n').map(function(s){return s.trim();}).filter(Boolean);
    for (var i = names.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var tmp = names[i]; names[i] = names[j]; names[j] = tmp;
    }
    box.value = names.join('\\n');
  });

  document.getElementById('create-btn').addEventListener('click', function(){
    var name = document.getElementById('t-name').value;
    var raw = document.getElementById('t-participants').value;
    var names = raw.split('\\n').map(function(s){return s.trim();}).filter(Boolean);
    var mode = document.querySelector('input[name=mode]:checked').value;
    var errBox = document.getElementById('err-msg');
    errBox.innerText = '';
    if (names.length < 2) {
      errBox.innerText = 'At least 2 participants are required.';
      return;
    }
    api.create_tournament(name, names, mode).then(function(t){
      if (t && t.error) { errBox.innerText = t.error; return; }
      STATE.tournaments[t.id] = t;
      document.getElementById('t-name').value = '';
      document.getElementById('t-participants').value = '';
      openTournament(t.id);
    });
  });
}

if (window.pywebview) {
  window.pywebview.api.load_all().then(function (data) {
    boot(data, window.pywebview.api);
  });
} else {
  window.addEventListener('pywebviewready', function () {
    window.pywebview.api.load_all().then(function (data) {
      boot(data, window.pywebview.api);
    });
  });
}
</script>
</body>
</html>
"""

WINDOW_KEY = '__window__'


def _remember_window_geometry(api, window):
    def on_closing():
        try:
            geom = {'width': window.width, 'height': window.height, 'x': window.x, 'y': window.y}
        except Exception as e:
            print('Impossibile leggere la geometria finestra:', e)
            return
        with _lock:
            api._data[WINDOW_KEY] = geom
            try:
                _persist_to_self(api._data)
            except Exception as e:
                print('Impossibile salvare la geometria finestra:', e)
    window.events.closing += on_closing


def main():
    api = Api()
    geom = api._data.get(WINDOW_KEY) or {}
    x, y = geom.get('x'), geom.get('y')
    if x is None or y is None or x < -50 or y < -50:
        # posizione salvata fuori schermo (es. finestra minimizzata all'ultima chiusura):
        # ignorala per evitare di riaprire l'app invisibile.
        x = y = None
    window = webview.create_window(
        'Challonge Clone',
        html=HTML,
        js_api=api,
        width=max(geom.get('width', 1300), 900),
        height=max(geom.get('height', 860), 600),
        x=x,
        y=y,
        min_size=(900, 600),
    )
    _remember_window_geometry(api, window)
    webview.start()


if __name__ == '__main__':
    main()
