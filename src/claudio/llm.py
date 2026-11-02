"""claudio.llm — a local model through Ollama (no credits): for small, repetitive jobs only (describing shots, caption
proofreading).
Montage decisions stay with Claude.

  endpoint() / model()      where and which: settings.json {"ollama": "http://100.72.145.75:11434", "vlm_model": "..."}
  ensure(progress)          the server answers — or, when the machine with the GPU is off (on the Mac), wake it up and
                            wait (~3 min): Home Assistant {"wake_ha_url", "wake_ha_entity", token in ~/.config/claudio/ha_token}
                            or a plain Wake-on-LAN packet {"wake_mac", "wake_broadcast"} on the same network
  ask(prompt, schema, images)    a question (text, or with pictures) → the model's answer / JSON
  vision(jpeg, prompt, schema)   one picture → the model's JSON answer
  gpu_check()               the loaded model all in VRAM? (a model spilled onto the CPU is ~10x slower: say so)

Nothing here is required: with no "ollama" in the settings, or the server down, callers keep their non-AI path.
"""
import base64, json, os, socket, time, urllib.request

from claudio import core

DEFAULT_MODEL = 'qwen3-vl:8b-instruct'      # not 'qwen3-vl:8b': that tag is the thinking model (~1,500 tokens per answer)
WAIT_BOOT = 300                              # seconds: Windows boot + Ollama start (measured ~3 min)


def endpoint():
    u = core.env('OLLAMA')
    return u.rstrip('/') if u else None


def model():
    return core.env('VLM_MODEL', DEFAULT_MODEL)


def _req(path, body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(endpoint() + path, data, {'Content-Type': 'application/json'} if data else {},
                               method='POST' if data else 'GET')
    with core.urlopen(r, timeout=timeout) as f:
        return json.load(f)


def up(timeout=3):
    if not endpoint():
        return False
    try:
        return bool(_req('/api/version', timeout=timeout).get('version'))
    except Exception:
        return False


def _ha_token():
    t = core.env('WAKE_HA_TOKEN')
    p = os.path.expanduser(core.env('WAKE_HA_TOKEN_FILE', '~/.config/claudio/ha_token'))
    if not t and os.path.exists(p):
        t = open(p).read().strip()
    return t or None


def _magic(mac, broadcast='255.255.255.255', port=9):
    raw = bytes.fromhex(mac.replace(':', '').replace('-', ''))
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.sendto(b'\xff' * 6 + raw * 16, (broadcast, port))
    s.close()


def wake():
    """Turn the GPU machine on. Home Assistant first (works away from home), else a WoL packet (same network only).
    Returns how, or None when nothing is configured."""
    ha, ent, tok = core.env('WAKE_HA_URL'), core.env('WAKE_HA_ENTITY'), _ha_token()
    if ha and ent and tok:
        domain = ent.split('.')[0]
        r = urllib.request.Request(f"{ha.rstrip('/')}/api/services/{domain}/turn_on", json.dumps({'entity_id': ent}).encode(),
                                   {'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'}, method='POST')
        core.urlopen(r, timeout=15).read()
        return f'Home Assistant ({ent})'
    if core.env('WAKE_MAC'):
        _magic(core.env('WAKE_MAC'), core.env('WAKE_BROADCAST', '255.255.255.255'))
        return 'Wake-on-LAN'
    return None


def ensure(progress=lambda *_: None, wait=WAIT_BOOT):
    """(True, note) when the model server answers, waking the machine if it is off; (False, why) otherwise."""
    if not endpoint():
        return False, 'sem Ollama configurado ("ollama" em ~/.config/claudio/settings.json)'
    if up():
        return True, None
    try:
        how = wake()
    except Exception as e:
        return False, f'Ollama não respondeu e não consegui ligar o desktop: {e}'
    if not how:
        return False, f'Ollama não respondeu em {endpoint()} (desktop desligado? configure wake_ha_* ou wake_mac)'
    t0 = time.time()
    while time.time() - t0 < wait:
        progress(min(0.99, (time.time() - t0) / wait), f'ligando o desktop por {how}… {int(time.time() - t0)} s')
        if up():
            return True, f'desktop ligado por {how} ({int(time.time() - t0)} s)'
        time.sleep(5)
    return False, f'liguei o desktop por {how}, mas o Ollama não respondeu em {wait // 60} min'


def ask(prompt, schema=None, images=(), system=None, timeout=300, ctx=4096):
    """One question (with pictures, or text only) → the answer, parsed JSON when a schema is given. Low temperature:
    logging and proofreading, not prose. The first call after the model was unloaded also loads it (69 s measured on the
    desktop, Ollama as a SYSTEM task): hence the timeout."""
    msgs = ([{'role': 'system', 'content': system}] if system else []) + [
        {'role': 'user', 'content': prompt, **({'images': [base64.b64encode(i).decode() for i in images]} if images else {})}]
    body = {'model': model(), 'stream': False, 'keep_alive': core.env('OLLAMA_KEEP_ALIVE', '2m'),
            'options': {'temperature': 0.1, 'num_ctx': ctx}, 'messages': msgs}
    if schema:
        body['format'] = schema
    txt = _req('/api/chat', body, timeout)['message']['content'].strip()
    return json.loads(txt) if schema else txt


def vision(jpeg, prompt, schema=None, timeout=300):
    return ask(prompt, schema, [jpeg], timeout=timeout)


def loaded():
    """The model already in memory (no ~70 s load before the first answer)."""
    try:
        return any(m.get('name') == model() for m in _req('/api/ps').get('models', []))
    except Exception:
        return False


def gpu_check():
    """None when the loaded model sits fully in VRAM; otherwise a warning (part of it on the CPU)."""
    try:
        for m in _req('/api/ps').get('models', []):
            if m.get('name') == model() and m.get('size_vram', 0) < m.get('size', 0) * 0.98:
                pct = round(100 * (1 - m['size_vram'] / m['size']))
                return f'{pct}% do modelo ficou no processador (a placa está ocupada, Premiere aberto?): vai bem mais devagar'
    except Exception:
        pass
    return None
