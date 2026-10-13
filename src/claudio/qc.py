"""claudio.qc — check a timeline before the editor exports it. Reports only; never changes the timeline.

  qc_timeline(nle_xml, platform)  reads the timeline as it is NOW (FCP7 XML exported by Premiere/Resolve) and lists
  issues with timeline timecode and severity, ready to be dropped as markers by the NLE MCP:
      flash frames · gaps in V1 (black) · overlapping clips · offline media · disabled clips · clips past the end of
      their media · the same take used twice · jump cuts · speech level jumps between cuts · clipped audio ·
      sequence size / fps / duration vs the delivery platform
"""
import os
from fractions import Fraction

import numpy as np

from claudio import core

PLATFORMS = {
    'reels':   {'size': (1080, 1920), 'max_s': 90, 'label': 'Instagram Reels'},
    'tiktok':  {'size': (1080, 1920), 'max_s': 600, 'label': 'TikTok'},
    'shorts':  {'size': (1080, 1920), 'max_s': 60, 'label': 'YouTube Shorts'},
    'feed':    {'size': (1080, 1350), 'max_s': 60, 'label': 'Feed 4:5'},
    'youtube': {'size': (1920, 1080), 'max_s': None, 'label': 'YouTube 16:9'},
    'youtube4k': {'size': (3840, 2160), 'max_s': None, 'label': 'YouTube 4K'},
}


def _tc(sec, fps):
    f = int(round(sec * float(fps)))
    fi = round(float(fps))
    return f'{f // (fi * 3600):02d}:{f // (fi * 60) % 60:02d}:{f // fi % 60:02d}:{f % fi:02d}'


def _speech_db(path, s, e):
    """Median level of the loud part (speech) of a source range, from the index (no decoding)."""
    try:
        d = core.cache_dir(core.local_path(path))
        p = os.path.join(d, 'rms_db.npy')
        if not os.path.exists(p):
            return None, None
        db = np.load(p)
        v = db[int(s / core.HOP): int(e / core.HOP) + 1]
        if len(v) < 5:
            return None, None
        loud = v[v >= np.percentile(v, 60)]
        return float(np.median(loud)), float(v.max())
    except Exception:
        return None, None


def qc_timeline(nle_xml, platform=None, seq_size=None):
    from claudio import nle_read
    tl = nle_read.read_fcp7(nle_xml)
    fps = Fraction(tl['fps']); F = float(fps)
    issues = []

    def add(sev, t, what, detail=''):
        issues.append({'severity': sev, 'tl': round(t, 3), 'tc': _tc(t, fps), 'issue': what, 'detail': detail})

    vids = [i for i in tl['items'] if i['track'].startswith('V')]
    auds = [i for i in tl['items'] if i['track'].startswith('A')]
    by_track = {}
    for i in tl['items']:
        by_track.setdefault(i['track'], []).append(i)
    for tr, items in by_track.items():
        items.sort(key=lambda i: i['tl_in_f'])
        for a, b in zip(items, items[1:]):
            if b['tl_in_f'] < a['tl_out_f']:
                add('erro', b['tl_in'], f'clipes sobrepostos em {tr}', f"{a['name']} / {b['name']}")
    v1 = sorted(by_track.get('V1', []), key=lambda i: i['tl_in_f'])
    if v1 and v1[0]['tl_in_f'] > 0:
        add('aviso', 0, 'preto no começo (V1 vazia)', f"{v1[0]['tl_in']:.2f}s")
    covered = sorted([(i['tl_in_f'], i['tl_out_f']) for i in vids if i['enabled']])
    end = 0
    for s, e in covered:
        if s > end + 0 and end > 0:
            add('erro', end / F, 'buraco na imagem (preto)', f'{(s - end) / F:.2f}s sem nenhum vídeo')
        end = max(end, e)
    for i in tl['items']:
        n = i['tl_out_f'] - i['tl_in_f']
        if i['track'].startswith('V') and 0 < n <= 3:
            add('erro', i['tl_in'], 'flash frame', f"{i['name']}: {n} frame(s)")
        if not i['enabled']:
            add('aviso', i['tl_in'], 'clipe desativado', f"{i['track']} {i['name']}")
        if i['path'] and not os.path.exists(i['path']):
            add('erro', i['tl_in'], 'mídia offline', core.nle_path(i['path']))
        elif i['path']:
            try:
                dur = core.light_index(i['path'])['info']['duration']
                if i['src_out'] > dur + 1 / F:
                    add('erro', i['tl_in'], 'clipe passa do fim da mídia', f"{i['name']}: out {i['src_out']:.2f}s > {dur:.2f}s")
            except Exception:
                pass
    # the same take twice / jump cuts on the spine
    vv = [i for i in vids if i['path'] and i['enabled']]
    for k, a in enumerate(vv):
        for b in vv[k + 1:]:
            if a['path'] == b['path']:
                ov = min(a['src_out'], b['src_out']) - max(a['src_in'], b['src_in'])
                if ov > 0.5 * min(a['src_out'] - a['src_in'], b['src_out'] - b['src_in']):
                    add('aviso', b['tl_in'], 'mesmo trecho usado duas vezes', f"{a['name']} em {a['tl_in']:.1f}s e {b['tl_in']:.1f}s")
    for a, b in zip(v1, v1[1:]):
        if a['path'] and a['path'] == b['path'] and b['tl_in_f'] == a['tl_out_f'] and 0 < b['src_in'] - a['src_out'] < 1.0:
            covered_by_broll = any(i['track'] != 'V1' and i['tl_in'] <= b['tl_in'] < i['tl_out'] for i in vids)
            if not covered_by_broll:
                add('aviso', b['tl_in'], 'jump cut (mesmo plano, pulo curto)', f"{a['name']}: pula {b['src_in'] - a['src_out']:.2f}s — cobrir com b-roll?")
    # speech level jumps between consecutive dialogue clips + clipped audio
    a1 = sorted([i for i in auds if i['track'] == 'A1' and i['path']], key=lambda i: i['tl_in_f'])
    levels = []
    for i in a1:
        lv, pk = _speech_db(i['path'], i['src_in'], i['src_out'])
        if lv is not None:
            levels.append((i, lv))
        if pk is not None and pk > -0.5:
            add('aviso', i['tl_in'], 'áudio estourando na fonte', f"{i['name']}: pico {pk:.1f} dBFS")
    for (a, la), (b, lb) in zip(levels, levels[1:]):
        if abs(la - lb) > 6:
            add('aviso', b['tl_in'], 'salto de volume entre falas', f"{a['name']} {la:.0f} dB → {b['name']} {lb:.0f} dB (ajustar ganho)")
    # delivery
    if platform:
        pf = PLATFORMS.get(platform.lower())
        if pf:
            if seq_size and tuple(seq_size) != pf['size']:
                add('erro', 0, f"formato da sequência ≠ {pf['label']}", f"{seq_size[0]}x{seq_size[1]} (esperado {pf['size'][0]}x{pf['size'][1]})")
            if pf['max_s'] and tl['duration'] > pf['max_s']:
                add('aviso', tl['duration'], f"mais longo que o ideal para {pf['label']}", f"{tl['duration']:.1f}s > {pf['max_s']}s")
    issues.sort(key=lambda x: ({'erro': 0, 'aviso': 1}.get(x['severity'], 2), x['tl']))
    return {'timeline': tl['name'], 'duration': round(tl['duration'], 2), 'fps': tl['fps'],
            'summary': {'erros': sum(x['severity'] == 'erro' for x in issues), 'avisos': sum(x['severity'] == 'aviso' for x in issues)},
            'issues': issues,
            'markers_hint': 'add each issue as a marker in the NLE (Premiere add_marker / Resolve add_marker) so the editor sees it in place'}


# ───────────────────────────── loudness of the mix + music ducking ─────────────────────────────
TARGETS = {'reels': -14, 'tiktok': -14, 'shorts': -14, 'feed': -14, 'youtube': -14, 'youtube4k': -14, 'tv': -23, 'podcast': -16}
MSR_MIX = 16000


def _clip_audio(path, s, e):
    """Mono 16 kHz samples of a source range: the index's a16.pcm when there is one (no re-reading 4K camera files),
    else decoded (music files, recorders)."""
    import subprocess
    lp = core.local_path(path)
    pcm = os.path.join(core.cache_dir(lp), 'a16.pcm')
    if os.path.exists(pcm):
        x = np.memmap(pcm, np.int16, 'r')
        return np.asarray(x[int(s * MSR_MIX):int(e * MSR_MIX)], np.float32) / 32768.0
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{s:.3f}', '-t', f'{e - s:.3f}', '-i', lp, '-vn', '-ac', '1',
                          '-ar', str(MSR_MIX), '-f', 's16le', '-'], capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def mix_audio(tl):
    """The timeline's audio mixed to mono 16 kHz from its clips (each stereo pair counted once, Audio Levels applied).
    Returns (mix, stems) with stems = {'speech': …, 'music': …} split by whether a clip has transcribed speech."""
    dur = max([i['tl_out'] for i in tl['items']] + [tl['duration']])
    n = int(dur * MSR_MIX) + 1
    mix, speech, music = np.zeros(n, np.float32), np.zeros(n, np.float32), np.zeros(n, np.float32)
    seen = set()
    for it in tl['items']:
        if not it['track'].startswith('A') or not it['enabled'] or not it['path'] or not os.path.exists(it['path']):
            continue
        key = (it['path'], round(it['tl_in'], 2), round(it['src_in'], 2))
        if key in seen:                       # A1/A2 = left/right of the same clip
            continue
        seen.add(key)
        x = _clip_audio(it['path'], it['src_in'], it['src_out']) * float(it.get('gain', 1.0))
        a = int(it['tl_in'] * MSR_MIX)
        x = x[:max(0, min(len(x), n - a, int((it['tl_out'] - it['tl_in']) * MSR_MIX)))]
        mix[a:a + len(x)] += x
        has_words = os.path.exists(os.path.join(core.cache_dir(core.local_path(it['path'])), 'words.json'))
        (speech if has_words and os.path.splitext(it['path'])[1].lower() in core.VIDEO_EXT else music)[a:a + len(x)] += x
    return mix, {'speech': speech, 'music': music}


def loudness(x):
    """Integrated loudness (LUFS), loudness range and true peak of mono samples, by ffmpeg's ebur128."""
    import re, subprocess, tempfile
    with tempfile.NamedTemporaryFile(suffix='.f32', dir=core.CACHE) as f:
        np.asarray(x, np.float32).tofile(f.name)
        # measured as dual mono (the same signal on L and R, as the export plays it): +3 dB vs reading it as mono
        r = subprocess.run(['ffmpeg', '-v', 'info', '-nostats', '-f', 'f32le', '-ar', str(MSR_MIX), '-ac', '1', '-i', f.name,
                            '-af', 'pan=stereo|c0=c0|c1=c0,ebur128=peak=true', '-f', 'null', '-'], capture_output=True, text=True).stderr
    summ = r[r.rfind('Summary:'):]
    g = lambda k: float(re.search(rf'{k}:\s+(-?[\d.]+|-inf)', summ).group(1).replace('-inf', '-99')) if re.search(rf'{k}:\s+(-?[\d.]+|-inf)', summ) else None
    return {'lufs': g('I'), 'lra': g('LRA'), 'true_peak_db': g('Peak')}


def _windows_db(x, win=0.4):
    k = int(win * MSR_MIX)
    m = len(x) // k
    return 20 * np.log10(np.sqrt((x[:m * k].reshape(m, k) ** 2).mean(1)) + 1e-9)


def ducking(stems, fps, min_gap_db=10.0):
    """Stretches where music plays under speech without being at least `min_gap_db` below it: start/end, how loud each
    is and how much to lower the music (keyframe it on the music track)."""
    sp, mu = _windows_db(stems['speech']), _windows_db(stems['music'])
    out, cur = [], None
    for i, (a, b) in enumerate(zip(sp, mu)):
        bad = a > -40 and b > -50 and b > a - min_gap_db
        t = i * 0.4
        if bad:
            if cur and t - cur['end'] <= 0.8:
                cur['end'] = t + 0.4; cur['sp'].append(a); cur['mu'].append(b)
            else:
                cur = {'start': t, 'end': t + 0.4, 'sp': [a], 'mu': [b]}
                out.append(cur)
    res = []
    for c in out:
        if c['end'] - c['start'] < 1.2:
            continue
        s, m = float(np.median(c['sp'])), float(np.median(c['mu']))
        res.append({'start': round(c['start'], 2), 'end': round(c['end'], 2), 'tc': _tc(c['start'], fps),
                    'speech_db': round(s, 1), 'music_db': round(m, 1), 'lower_music_by_db': round(m - (s - min_gap_db - 2), 1)})
    return res


def audio_qc(nle_xml, platform=None):
    """Loudness of the whole mix vs the platform target, true peak, and music-under-speech ducking suggestions.
    Built from the clips' indexed audio with the timeline's Audio Levels: an estimate of the export, before track
    effects / bus processing."""
    from claudio import nle_read
    tl = nle_read.read_fcp7(nle_xml)
    fps = Fraction(tl['fps'])
    mix, stems = mix_audio(tl)
    L = loudness(mix)
    target = TARGETS.get(platform or 'youtube', -14)
    notes = []
    if L['lufs'] is not None:
        d = round(L['lufs'] - target, 1)
        if abs(d) > 1.0:
            notes.append(f"loudness {L['lufs']} LUFS, alvo {target}: {'abaixe' if d > 0 else 'suba'} ~{abs(d)} dB no master (ou normalize no export)")
    if L['true_peak_db'] is not None and L['true_peak_db'] > -1.0:
        notes.append(f"true peak {L['true_peak_db']} dBTP: acima de −1 dBTP, use um limiter no master")
    duck = ducking(stems, fps) if stems['music'].any() and stems['speech'].any() else []
    if duck:
        notes.append(f"{len(duck)} trecho(s) com música alta demais por baixo da fala: abaixe a música nos tempos de `ducking`")
    return {'platform_target_lufs': target, **L, 'ducking': duck, 'notes': notes,
            'estimate': 'mix rebuilt from the indexed clip audio (16 kHz mono, measured as dual mono) with the timeline '
                        'Audio Levels; accurate to about ±3 dB (stereo width of the camera mics is lost); track/master '
                        'effects are not included'}
