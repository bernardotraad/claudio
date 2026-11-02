"""Slow motion: the clips slowed in the NLE are found, and the remade ones go on new tracks at 100 %, effects kept."""
import os, shutil

import pytest

from claudio import nle_read, slowmo
from conftest import ff, needs_ffmpeg

R24 = '<rate><timebase>24</timebase><ntsc>FALSE</ntsc></rate>'


def _remap(speed, extra=''):
    return (f'<filter><effect><name>Time Remap</name><effectid>timeremap</effectid><effectcategory>motion</effectcategory>'
            f'<effecttype>motion</effecttype><mediatype>video</mediatype><parameter><parameterid>variablespeed</parameterid>'
            f'<value>0</value></parameter><parameter><parameterid>speed</parameterid><value>{speed}</value></parameter>{extra}'
            f'</effect></filter>')


MOTION = ('<filter><effect><name>Basic Motion</name><effectid>basic</effectid><effectcategory>motion</effectcategory>'
          '<effecttype>motion</effecttype><mediatype>video</mediatype><parameter><parameterid>scale</parameterid>'
          '<value>120</value></parameter></effect></filter>')


def _xml(tmp_path, broll):
    fe = lambda fid, p: (f'<file id="{fid}"><name>{os.path.basename(p)}</name><pathurl>file://{p}</pathurl>{R24}'
                         f'<duration>240</duration><media><video/></media></file>')
    c = lambda cid, st, en, i, o, f, flt='': (f'<clipitem id="{cid}"><name>{cid}</name>{R24}<start>{st}</start><end>{en}</end>'
                                             f'<in>{i}</in><out>{o}</out>{f}{flt}</clipitem>')
    v1 = c('a', 0, 96, 0, 96, fe('f1', broll))
    v2 = c('slow', 24, 72, 10, 34, '<file id="f1"/>', _remap(50) + MOTION)          # 2 s on the timeline from 1 s of source
    v3 = c('slow2', 48, 60, 60, 66, '<file id="f1"/>', _remap(50))                 # overlaps the first one
    x = (f'<?xml version="1.0"?><xmeml version="4"><sequence id="s"><name>Recap</name>{R24}<duration>96</duration><media>'
         f'<video><track>{v1}</track><track>{v2}</track><track>{v3}</track></video><audio/></media></sequence></xmeml>')
    p = tmp_path / 'e.xml'
    p.write_text(x, encoding='utf-8')
    return p


@pytest.fixture
def broll(tmp_path):
    p = str(tmp_path / 'broll.mov')
    ff('-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24', '-t', '10', '-c:v', 'prores_ks', '-profile:v', '3', p)
    return p


@needs_ffmpeg
def test_plan_finds_slowed_clips(tmp_path, broll):
    *_, items = slowmo.plan(str(_xml(tmp_path, broll)))
    assert sorted((round(i['start'], 2), i['speed'], round(i['factor'], 2)) for i in items) == [(1.0, 50, 2.0), (2.0, 50, 2.0)]
    r = slowmo.slowmo_timeline(str(_xml(tmp_path, broll)), dry_run=True)
    assert r['to_make'] == 2 and r['frames'] == 48 + 12


def test_speed_of():
    import xml.etree.ElementTree as ET
    clip = lambda f: ET.fromstring(f'<clipitem>{f}</clipitem>')
    assert slowmo.speed_of(clip('')) == 100
    assert slowmo.speed_of(clip(_remap(40))) == 40
    assert slowmo.speed_of(clip(_remap(50, '<parameter><parameterid>reverse</parameterid><value>TRUE</value></parameter>'))) is None


@needs_ffmpeg
def test_timeline_with_fake_rife(tmp_path, broll, monkeypatch):
    def fake(path, src_in, n_in, factor, out_path, fps_out, n_out=None, scale=None):   # no GPU in the tests
        ff('-ss', str(src_in), '-i', path, '-vf', f'setpts={factor}*PTS', '-r', fps_out, '-frames:v', str(n_out),
           '-c:v', 'prores_ks', out_path)
        return {'path': out_path, 'frames': n_out, 'seconds': 0}
    monkeypatch.setattr(slowmo, 'interpolate', fake)
    monkeypatch.setattr(slowmo, 'available', lambda: None)
    r = slowmo.slowmo_timeline(str(_xml(tmp_path, broll)))
    assert r['slowed'] == 2 and r['slowmo_tracks'] == ['V4', 'V5']
    tl = nle_read.read_fcp7(r['path'])
    new = {i['track']: i for i in tl['items'] if i['track'] in ('V4', 'V5')}
    assert (new['V4']['tl_in'], new['V4']['tl_out']) == (1.0, 3.0) and new['V4']['src_out_f'] == 48
    assert (new['V5']['tl_in'], new['V5']['tl_out']) == (2.0, 2.5)
    xml = open(r['path'], encoding='utf-8').read()
    assert xml.count('timeremap') == 2               # only the originals keep theirs
    assert '<value>120</value>' in xml.split('slowmo-0')[1]   # the clip's Motion travels with it
    assert len([i for i in tl['items'] if i['track'] in ('V1', 'V2', 'V3')]) == 3
