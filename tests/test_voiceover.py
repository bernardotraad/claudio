"""ElevenLabs narration as tools (the Premiere panel has no shell): takes with word timings, one sentence redone and
spliced in, the picture retimed to the new words, clicks out of the pauses, lower-thirds. No network: the API is faked."""
import json, os, subprocess

import numpy as np
import pytest

from conftest import needs_ffmpeg
from claudio import voiceover as vo


def test_breaks_after_each_sentence_only():
    t = vo.add_breaks('Antes da chuva. O calor chega!  E a roda gira?', 0.55)
    assert t == 'Antes da chuva. <break time="0.55s" /> O calor chega! <break time="0.55s" /> E a roda gira?'
    assert vo.add_breaks('Uma frase.', 0) == 'Uma frase.'
    assert vo.sentences('Um. Dois! Três?') == ['Um.', 'Dois!', 'Três?']


def test_alignment_to_words_drops_break_tags():
    text = 'Oi mundo. <break time="0.55s" /> Tudo bem?'
    n = len(text)
    al = {'characters': list(text), 'character_start_times_seconds': [i * 0.1 for i in range(n)],
          'character_end_times_seconds': [i * 0.1 + 0.1 for i in range(n)]}
    ws = vo.words_from_alignment(al)
    assert [w['w'] for w in ws] == ['Oi', 'mundo.', 'Tudo', 'bem?']
    assert ws[2]['s'] == round(text.index('Tudo') * 0.1, 3)


def test_speed_below_one_is_warned():
    assert vo.check_speed(1.0) == (1.0, [])
    sp, warn = vo.check_speed(0.9)
    assert sp == 0.9 and warn and 'choke' in warn[0]
    with pytest.raises(ValueError):
        vo.check_speed(0.5)


def _words(spec):
    return [{'w': w, 's': s, 'e': e} for w, s, e in spec]


OLD = _words([('a', 0.5, 0.8), ('mata', 0.9, 1.4), ('Magé', 3.0, 3.5), ('Recife', 6.0, 6.6), ('fim.', 8.0, 8.4)])
NEW = _words([('a', 0.6, 0.9), ('mata', 1.0, 1.6), ('Magé', 3.6, 4.1), ('Recife', 7.0, 7.6), ('fim.', 9.2, 9.6)])


def test_retime_keeps_anchor_word_and_lead():
    clips = [{'label': 'A1', 'start': 0.0, 'end': 2.8, 'src_in': 10.0}, {'label': 'A2', 'start': 2.8, 'end': 5.9},
             {'label': 'A3', 'start': 5.9, 'end': 8.9, 'node_id': 'n3'}]
    r = vo.retime_plan(OLD, NEW, clips, fps='24000/1001')
    c = {x['label']: x for x in r['clips']}
    f = 24000 / 1001
    assert c['A1']['new_start'] == 0.0                              # the nest start stays put
    assert c['A2']['anchor'] == 'Magé' and abs(c['A2']['new_start'] - round((3.6 - 0.2) * f) / f) < 1e-6
    assert c['A3']['anchor'] == 'Recife' and abs(c['A3']['new_start'] - round(6.9 * f) / f) < 1e-6
    assert c['A1']['new_end'] == c['A2']['new_start'] and c['A2']['new_end'] == c['A3']['new_start']   # still butt-joined
    assert abs(c['A3']['new_end'] - round(10.1 * f) / f) < 1e-6    # same tail after the last word
    assert c['A1']['src_out'] == round(10.0 + c['A1']['duration'], 6)
    for x in r['clips']:
        assert abs(x['new_start'] * f - round(x['new_start'] * f)) < 1e-4
    moves = [s['clip'] for s in r['steps'] if s['do'] == 'move']
    assert moves == ['A3', 'A2']                                    # moving right: right to left
    kinds = [s['do'] for s in r['steps']]
    assert kinds.index('move') < max(i for i, k in enumerate(kinds) if k == 'trim_out')   # lengthen after the moves
    assert next(s for s in r['steps'] if s['clip'] == 'A3' and s['do'] == 'move')['node_id'] == 'n3'


def test_retime_follows_a_changed_word_and_offset():
    new = [dict(w) for w in NEW]
    new[2]['w'] = 'Magé,'                                           # punctuation changes nothing
    new.insert(3, {'w': 'Pernambuco', 's': 5.0, 'e': 5.5})          # a word added
    r = vo.retime_plan(json.dumps({'words': [{**w, 's': w['s'] + 10, 'e': w['e'] + 10} for w in OLD]}),
                       {'words': [{**w, 's': w['s'] + 10, 'e': w['e'] + 10} for w in new]},
                       [{'start': 0, 'end': 5.9}, {'start': 5.9, 'end': 8.9}], offset=10.0)
    assert r['clips'][1]['anchor'] == 'Recife' and r['words_changed']['new_only'] == 1
    assert abs(r['clips'][1]['new_start'] - 6.9) < 0.05


def _tone(dur, spans, click_at=()):
    """Stereo 48 kHz: a 220 Hz 'voice' in each span, silence elsewhere, a one-sample click at each click_at."""
    n = int(dur * vo.SR)
    t = np.arange(n) / vo.SR
    x = np.zeros(n, np.float32)
    for a, b in spans:
        i, j = int(a * vo.SR), int(b * vo.SR)
        x[i:j] = 0.3 * np.sin(2 * np.pi * 220 * t[i:j])
    for c in click_at:
        k = int(c * vo.SR)
        x[k:k + 96] = 0.5 * np.sign(np.sin(2 * np.pi * 2000 * t[k:k + 96]))
    return np.stack([x, x], 1)


@needs_ffmpeg
def test_clean_gaps_silences_the_click_keeps_the_words(tmp_path):
    src = str(tmp_path / 'take.wav')
    vo._write_wav(_tone(3.0, [(0.2, 1.0), (2.0, 2.8)], click_at=[1.6]), src)
    json.dump({'words': _words([('Um', 0.2, 1.0), ('dois.', 2.0, 2.8)]), 'text': 'Um dois.'},
              open(tmp_path / 'take_words.json', 'w'))
    r = vo.clean_gaps(src)
    assert r['pauses_cleaned'] == 1 and r['silenced'][0]['clicks'] >= 1
    y = vo._pcm(str(tmp_path / 'take_clean.wav'))
    seg = lambda a, b: np.abs(y[int(a * vo.SR):int(b * vo.SR)]).max()
    assert seg(1.55, 1.65) < 1e-3                                  # the click is gone
    assert seg(0.3, 0.9) > 0.2 and seg(2.05, 2.7) > 0.2             # the words are not
    assert json.load(open(tmp_path / 'take_clean_words.json'))['words'][1]['w'] == 'dois.'


@needs_ffmpeg
def test_fix_line_splices_one_sentence_and_shifts_the_rest(tmp_path):
    src = str(tmp_path / 'abertura.mp3')
    wav = str(tmp_path / 'src.wav')
    vo._write_wav(_tone(4.0, [(0.2, 1.0), (1.6, 2.4), (3.0, 3.6)]), wav)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', wav, src], check=True)
    json.dump({'words': _words([('Um.', 0.2, 1.0), ('Dois', 1.6, 2.0), ('três.', 2.0, 2.4), ('Fim.', 3.0, 3.6)]),
               'text': 'Um. Dois três. Fim.', 'voice': 'VOZ', 'speed': 1.0, 'sentence_break': 0.55},
              open(tmp_path / 'abertura_words.json', 'w'))
    asked = {}

    def fake(text, voice, speed, prev, nxt):                       # the new line is 0.4 s longer
        asked.update(text=text, voice=voice, prev=prev, next=nxt)
        tmp = str(tmp_path / 'line.wav')
        vo._write_wav(_tone(1.6, [(0.1, 1.3)]), tmp)
        mp3 = subprocess.run(['ffmpeg', '-v', 'error', '-i', tmp, '-f', 'mp3', '-'], capture_output=True, check=True).stdout
        return mp3, _words([('Dois', 0.1, 0.7), ('e', 0.7, 0.8), ('três.', 0.8, 1.3)])
    r = vo.fix_line(src, 2, 'Dois e três.', generate=fake, said={})
    assert asked == {'text': 'Dois e três.', 'voice': 'VOZ', 'prev': 'Um.', 'next': 'Fim.'}
    assert r['line'] == 2 and r['old_text'] == 'Dois três.' and abs(r['delta_s'] - 0.4) < 0.01
    w = json.load(open(core_local(r['words']), encoding='utf-8'))
    assert [x['w'] for x in w['words']] == ['Um.', 'Dois', 'e', 'três.', 'Fim.']
    assert w['words'][0]['s'] == 0.2 and abs(w['words'][1]['s'] - 1.6) < 0.01   # same lead as the old line
    assert abs(w['words'][-1]['s'] - 3.4) < 0.01                    # the rest moved by delta
    assert w['text'] == 'Um. Dois e três. Fim.' and w['fixed'][0]['line'] == 2
    assert os.path.basename(core_local(r['audio'])) == 'abertura_fix1.wav'
    assert abs(r['duration_s'] - 4.4) < 0.05


def core_local(p):
    from claudio import core
    return core.local_path(p)


@needs_ffmpeg
def test_take_writes_mp3_and_words_and_never_the_key(tmp_path, monkeypatch):
    from claudio import core
    monkeypatch.setattr(core, 'elevenlabs_key', lambda: 'sk-SEGREDO')
    sent = []

    def fake_tts(text, voice, speed=1.0, previous_text=None, next_text=None, model=None, settings=None):
        sent.append((text, voice, speed))
        mp3 = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=220:duration=1', '-f', 'mp3', '-'],
                             capture_output=True, check=True).stdout
        toks = [t for t in text.replace('<break time="0.55s" />', ' ').split()]
        return mp3, [{'w': t, 's': 0.1 * i, 'e': 0.1 * i + 0.08} for i, t in enumerate(toks)]
    monkeypatch.setattr(vo, 'tts', fake_tts)
    r = vo.take('Na INC, a mata. O rio.\n\nFim do filme.', str(tmp_path), 'VOZ', speed=0.9, said={'INC': 'Ínqui'})
    assert len(r['takes']) == 2 and r['warnings']
    assert sent[0][0] == 'Na Ínqui, a mata. <break time="0.55s" /> O rio.' and sent[0][2] == 0.9
    w = json.load(open(tmp_path / 'narration_1_words.json', encoding='utf-8'))
    assert [x['w'] for x in w['words']] == ['Na', 'INC,', 'a', 'mata.', 'O', 'rio.'] and w['voice'] == 'VOZ'
    assert os.path.exists(tmp_path / 'narration_2.mp3')
    assert 'SEGREDO' not in json.dumps(r) and 'SEGREDO' not in json.dumps(w)


@needs_ffmpeg
def test_lower_third_has_alpha(tmp_path):
    r = vo.lower_third(['Magé | Rio de Janeiro', {'title': 'São Luís', 'subtitle': 'Maranhão', 'name': 'SaoLuis'}],
                       str(tmp_path), size='640x360', duration=1.2)
    assert [os.path.basename(core_local(x['overlay'])) for x in r['lower_thirds']] == ['LT_Mage.mov', 'LT_SaoLuis.mov']
    p = core_local(r['lower_thirds'][0]['overlay'])
    st = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', p], capture_output=True).stdout)
    v = st['streams'][0]
    assert v['codec_name'] == 'prores' and 'yuva' in v['pix_fmt'] and (v['width'], v['height']) == (640, 360)
    assert os.path.exists(core_local(r['lower_thirds'][0]['preview']))
