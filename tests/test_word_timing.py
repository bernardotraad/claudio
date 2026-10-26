"""Word edges on the measured voice, words at the edge of a cut, and the ElevenLabs Scribe transcript."""
import json, os

import numpy as np

from claudio import core

THR = -35.0


def envelope(voiced, dur=10.0):
    """rms_db at 20 ms: -60 dB of silence with voice (-20 dB) on the given (start, end) spans."""
    db = np.full(int(dur / core.HOP), -60.0, np.float32)
    for a, b in voiced:
        db[int(a / core.HOP):int(b / core.HOP)] = -20.0
    return db


def test_start_after_a_pause_moves_onto_the_voice():
    """INC C0011: Whisper put "que" at 6.95 s, the voice starts at 7.26 s — the caption came 9 frames early."""
    db = envelope([(5.0, 6.6), (7.26, 7.6)])
    w = core.align_to_voice([{'w': 'Cerâmica,', 's': 6.0, 'e': 6.6}, {'w': 'que', 's': 6.95, 'e': 7.33}], db, THR)
    assert w[1]['s'] == round(7.26 - 0.04, 3) and w[1]['aligned']
    assert w[0]['s'] == 6.0                        # no pause inside it: untouched


def test_end_runs_on_while_the_voice_holds_and_never_comes_back():
    db = envelope([(1.0, 2.5), (3.5, 4.0)])
    w = core.align_to_voice([{'w': 'fechado.', 's': 1.0, 'e': 2.0}, {'w': 'Boa.', 's': 3.5, 'e': 4.0}], db, THR)
    assert abs(w[0]['e'] - 2.54) < 0.03            # Whisper ended it 0.5 s before the voice did: a tight cut clipped it
    db = envelope([(1.0, 1.5)])
    w = core.align_to_voice([{'w': 'Excelente.', 's': 1.0, 'e': 2.0}], db, THR)
    assert w[0]['e'] == 2.0                        # a weak last syllable under the threshold keeps its end


def test_a_consonant_closure_or_unmeasured_voice_is_left_alone():
    db = envelope([(1.0, 1.3), (1.4, 1.8)])        # 100 ms gap: a stop consonant, not a pause
    assert core.align_to_voice([{'w': 'apartamento', 's': 1.0, 'e': 1.8}], db, THR)[0]['s'] == 1.0
    db = envelope([])                              # whispered / under the noise: nothing to measure
    assert core.align_to_voice([{'w': 'Pode', 's': 0.2, 'e': 0.7}], db, THR)[0]['s'] == 0.2


def test_a_word_leaking_past_the_in_point_is_still_captioned():
    words = [{'w': 'Você', 's': 9.9, 'e': 10.4}, {'w': 'sabe', 's': 10.4, 'e': 10.7}, {'w': 'né', 's': 17.9, 'e': 18.3}]
    got = core.words_in(words, 10.1, 18.0)
    assert [w['w'] for w in got] == ['Você', 'sabe']      # "né" is mostly after the out-point: not played
    assert got[0]['s'] == 10.1                            # clamped to the cut: the caption starts with the clip


def test_scribe_answer_becomes_words_and_segments(tmp_path):
    d = str(tmp_path)
    json.dump({'language_code': 'por', 'words': [
        {'text': 'Excelente.', 'start': 12.76, 'end': 13.36, 'type': 'word', 'logprob': -0.01},
        {'text': ' ', 'start': 13.36, 'end': 13.66, 'type': 'spacing'},
        {'text': 'Foi', 'start': 13.66, 'end': 13.72, 'type': 'word', 'logprob': 0},
        {'text': 'bom,', 'start': 13.76, 'end': 13.88, 'type': 'word', 'logprob': 0},
        {'text': '(risos)', 'start': 13.9, 'end': 14.2, 'type': 'audio_event'},
        {'text': 'Boa.', 'start': 15.0, 'end': 15.3, 'type': 'word', 'logprob': 0}]}, open(os.path.join(d, 'scribe.json'), 'w'))
    r = core.transcribe_scribe(d, 'pt')            # cached answer: no audio, no network
    assert r['language'] == 'pt'
    assert [w['w'] for w in r['words']] == ['Excelente.', 'Foi', 'bom,', 'Boa.']
    assert [s['text'] for s in r['segments']] == ['Excelente.', 'Foi bom,', 'Boa.']


def test_engine_comes_from_the_settings_file(tmp_path, monkeypatch):
    s = tmp_path / 'settings.json'
    s.write_text('{"stt": "elevenlabs"}')
    monkeypatch.setattr(core, 'SETTINGS', str(s))
    monkeypatch.delenv('CLAUDIO_STT', raising=False)
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'x')
    assert core.stt_engine() == 'elevenlabs'
    monkeypatch.setenv('CLAUDIO_STT', 'whisper')   # the environment wins
    assert core.stt_engine() == 'whisper'


def test_panel_switch_writes_the_setting(tmp_path, monkeypatch):
    from claudio import run
    monkeypatch.setattr(core, 'SETTINGS', str(tmp_path / 'claudio' / 'settings.json'))
    monkeypatch.delenv('CLAUDIO_STT', raising=False)
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'x')
    assert run._stt({'set': 'elevenlabs'})['stt'] == 'elevenlabs'
    assert json.load(open(core.SETTINGS)) == {'stt': 'elevenlabs'}
    assert run._stt({'set': 'whisper'})['stt'] == 'whisper'
    assert run._stt_check({'paths': [str(tmp_path / 'nada.mp4')]})['clips'][str(tmp_path / 'nada.mp4')]['indexed'] is False
