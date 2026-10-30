"""Soundtrack: the song's ending lands on the end of the cut, entry on a downbeat, ducked under speech by keyframes,
one plan for several sequences that the panel applies."""
import json, re

import pytest

from conftest import ff, needs_ffmpeg
from claudio import music

R25 = '<rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>'


def _xml(tmp_path, name, frames):
    fe = f'<file id="f1"><name>A.mp4</name><pathurl>file:///job/A.mp4</pathurl>{R25}<duration>99999</duration><media><video/><audio/></media></file>'
    c = lambda cid, f: f'<clipitem id="{cid}"><name>A</name>{R25}<start>0</start><end>{frames}</end><in>0</in><out>{frames}</out>{f}</clipitem>'
    x = (f'<?xml version="1.0"?><xmeml version="4"><sequence id="s"><name>{name}</name>{R25}<duration>{frames}</duration><media>'
         f'<video><track>{c("v1", fe)}</track></video><audio><track>{c("a1", "<file id=\"f1\"/>")}</track></audio></media></sequence></xmeml>')
    p = tmp_path / f'{name}.xml'
    p.write_text(x, encoding='utf-8')
    return str(p)


@pytest.fixture(scope='module')
def song(tmp_path_factory):
    """40 s at 120 BPM: a kick on every beat over a pad, 1 s of silence at the end (the reverb tail is not the ending)."""
    p = tmp_path_factory.mktemp('song') / 'Upbeat Corporate.wav'
    ff('-f', 'lavfi', '-i', "aevalsrc='0.6*sin(2*PI*60*t)*exp(-30*mod(t,0.5))+0.08*sin(2*PI*220*t)':s=44100:d=40",
       '-af', 'afade=t=out:st=39:d=0.01', '-ac', '2', str(p))
    return str(p)


def test_envelope_ducks_under_speech_and_holds_short_pauses():
    k = music.envelope([(2.0, 4.0), (5.0, 7.0), (12.0, 14.0)], 0.0, 20.0, -24.0, -12.0)
    at = lambda t: music._at(k, t, None)
    assert at(0.5) == -12.0 and at(10.0) == -12.0 and at(18.0) == -12.0      # gaps up
    assert at(3.0) == -24.0 and at(4.5) == -24.0 and at(13.0) == -24.0       # 1 s pause between lines stays down
    assert at(2.0 - music.PRE) == -24.0                                        # already down when the voice starts
    assert k[0][0] == 0.0 and k[-1][0] == 20.0


def test_closing_card_comes_up_near_the_voice():
    k = music.envelope([(0.5, 22.5)], 0.0, 28.0, -24.0, -12.0, -8.0, (0.0, 28.0))
    at = lambda t: music._at(k, t, None)
    assert at(10.0) == -24.0 and at(27.0) == -8.0          # vinheta: 5.5 s without speech at the end
    assert at(0.0) == -24.0                                  # 0.5 s before the first line is not an opening
    k = music.envelope([(5.0, 10.0), (20.0, 26.0)], 0.0, 28.0, -24.0, -12.0, -8.0, (0.0, 28.0))
    assert music._at(k, 1.0, None) == -8.0 and music._at(k, 15.0, None) == -12.0 and music._at(k, 27.9, None) == -12.0   # 2 s after the last line: just a pause


def test_fades_on_the_ends():
    k = music.with_fades([(0.0, -10.0), (30.0, -10.0)], 0.0, 30.0, 1.0, 2.0)
    assert k[0] == (0.0, music.FLOOR_DB) and k[-1] == (30.0, music.FLOOR_DB)
    assert (1.0, -10.0) in k and (28.0, -10.0) in k


@needs_ffmpeg
def test_fit_ending_lands_on_the_end_of_the_cut(song):
    f = music.fit(song, 25.0, 'fim')
    s = f['segments'][0]
    assert abs(s['src_out'] - 39.0) < 0.4                      # the audible end, not the silence after it
    assert abs(s['tl_in'] + (s['src_out'] - s['src_in']) - 25.0) < 0.01
    assert 0 <= s['tl_in'] <= music.ENTRY_SNAP                 # music from the start of the cut, never a bar late
    on_grid = abs((s['src_in'] * 2) - round(s['src_in'] * 2)) < 0.06
    assert on_grid or (s['tl_in'] == 0 and f['fade_in'] == 1.0)    # on a downbeat, or faded in from 0 s


@needs_ffmpeg
def test_fit_short_song_is_chained(song):
    f = music.fit(song, 70.0, 'auto')
    assert f['mode'] == 'repetida' and len(f['segments']) == 2
    last = f['segments'][-1]
    assert abs(last['tl_in'] + last['src_out'] - last['src_in'] - 70.0) < 0.01
    assert f['segments'][0]['tl_in'] == 0.0


@needs_ffmpeg
def test_plan_two_sequences(tmp_path, song):
    a = _xml(tmp_path, 'INC — Estela', 750)       # 30 s
    b = _xml(tmp_path, 'INC — Robson', 500)       # 20 s
    words = [{'w': 'oi', 's': 3.0, 'e': 5.0}, {'w': 'tchau', 's': 12.0, 'e': 15.0}]
    r = music.plan(str(tmp_path), [{'nle_xml': a, 'music': song, 'sequence_id': '1'}, {'nle_xml': b, 'music': song, 'duck': False}],
                   words=words)
    p = r['plan']
    assert p.endswith('.claudio-trilha.jsx') and '/trilhas/' in p.replace('\\', '/')
    txt = open(music.core.local_path(p), encoding='utf-8').read()
    P = json.loads(re.match(r'var CLAUDIO_MUSIC = (.*);\s*$', txt, re.S).group(1))
    s1, s2 = P['sequences']
    assert s1['sequence'] == 'INC — Estela' and s1['id'] == '1'
    assert s1['under_db'] < s1['gaps_db']
    seg = s1['segments'][0]
    assert abs(seg['tl_out'] - 30.0) < 0.01
    vals = {v for _, v in seg['keys']}
    assert s1['under_db'] in vals and s1['gaps_db'] in vals
    assert s2['under_db'] == s2['gaps_db']                    # no ducking asked
    assert abs(s2['segments'][0]['tl_out'] - 20.0) < 0.01


def test_fetch_copies_a_local_file(tmp_path, song):
    r = music.fetch(song, str(tmp_path), 'Minha: trilha')
    assert 'error' not in r and r['path'].endswith('Minha- trilha.wav')
    assert (tmp_path / 'trilhas' / 'Minha- trilha.wav').exists()
