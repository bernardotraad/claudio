"""SFX for social cuts: whoosh into each b-roll run, pop on caption keywords, on a new audio track."""
import wave

from claudio import nle_read, sfx

R25 = '<rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>'


def _xml(tmp_path):
    fe = lambda fid, n: f'<file id="{fid}"><name>{n}</name><pathurl>file:///job/{n}</pathurl>{R25}<duration>9999</duration><media><video/><audio/></media></file>'
    c = lambda cid, st, en, f: f'<clipitem id="{cid}"><name>x</name>{R25}<start>{st}</start><end>{en}</end><in>0</in><out>{en - st}</out>{f}</clipitem>'
    v2 = c('b1', 50, 100, fe('f2', 'B1.mp4')) + c('b2', 150, 175, fe('f3', 'B2.mp4')) + c('b3', 175, 200, '<file id="f2"/>')
    x = (f'<?xml version="1.0"?><xmeml version="4"><sequence id="s"><name>INC — Estela v2</name>{R25}<duration>250</duration><media>'
         f'<video><track>{c("v1", 0, 250, fe("f1", "A.mp4"))}</track><track>{v2}</track></video>'
         f'<audio><track>{c("a1", 0, 250, "<file id=\"f1\"/>")}</track></audio></media></sequence></xmeml>')
    p = tmp_path / 'e.xml'
    p.write_text(x, encoding='utf-8')
    return p


def test_add_sfx(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAUDIO_CACHE', str(tmp_path / 'cache'))
    words = [{'w': 'entrada', 's': 3.0, 'e': 3.4}, {'w': 'de', 's': 3.45, 'e': 3.5}, {'w': 'R$599', 's': 4.5, 'e': 5.0},
             {'w': 'INC', 's': 6.1, 'e': 6.4}]
    r = sfx.add_sfx(str(_xml(tmp_path)), words=words)
    assert r['whooshes'] == 2                     # 2 s and 6 s; the clip at 7 s continues the run, no whoosh
    assert r['pops'] == 2                         # "entrada", "R$599"; "INC" at 6.1 is on top of the whoosh at 6 s
    tl = nle_read.read_fcp7(r['path'])
    assert tl['name'] == 'INC — Estela v2 (com SFX)'
    a2 = sorted([i for i in tl['items'] if i['track'] == 'A2'], key=lambda i: i['tl_in'])
    assert [i['name'] for i in a2] == ['whoosh', 'pop', 'pop', 'whoosh']
    w = a2[0]
    with wave.open(w['path']) as f:
        d = f.getnframes() / f.getframerate()
    assert abs(w['tl_in'] + sfx.PEAK_AT['whoosh'] * d - 2.0) < 0.05     # the whoosh hits on the cut
    assert all(abs(i['gain'] - 10 ** (sfx.LEVEL[i['name']] / 20)) < 1e-3 for i in a2)
    assert len([i for i in tl['items'] if i['track'] in ('V1', 'V2')]) == 4   # picture untouched


def test_captions_and_overlays_are_not_broll():
    tl = {'duration': 10, 'items': [
        {'track': 'V2', 'tl_in': 1.0, 'tl_out': 2.0, 'enabled': True, 'path': '/x/Claudio legenda marca.aegraphic'},
        {'track': 'V3', 'tl_in': 0.0, 'tl_out': 10.0, 'enabled': True, 'path': '/x/legendas_marca.mov'},
        {'track': 'V2', 'tl_in': 3.0, 'tl_out': 5.0, 'enabled': True, 'path': '/x/FACHADA.mp4'},
        {'track': 'V2', 'tl_in': 5.2, 'tl_out': 6.0, 'enabled': True, 'path': '/x/SALA.mp4'},      # same run (gap < 0.5 s)
        {'track': 'V2', 'tl_in': 8.0, 'tl_out': 9.0, 'enabled': True, 'path': '/x/LOGO.png'}]}
    assert sfx.broll_entries(tl) == [3.0]
