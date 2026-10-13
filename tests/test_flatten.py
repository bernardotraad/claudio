"""Nested sequences → clips straight on the timeline (the editor grades take by take)."""
import xml.etree.ElementTree as ET

from claudio import nle_read

R24 = '<rate><timebase>24</timebase><ntsc>FALSE</ntsc></rate>'
R48 = '<rate><timebase>48</timebase><ntsc>FALSE</ntsc></rate>'


def _file(fid, name, full=True):
    if not full:
        return f'<file id="{fid}"/>'
    return f'<file id="{fid}"><name>{name}</name><pathurl>file://localhost/E%3a/job/{name}</pathurl>{R24}<duration>9999</duration></file>'


def _clip(cid, name, rate, st, en, i, o, file, extra=''):
    return (f'<clipitem id="{cid}"><name>{name}</name>{rate}<start>{st}</start><end>{en}</end><in>{i}</in><out>{o}</out>'
            f'<pproTicksIn>{i * 1000}</pproTicksIn><pproTicksOut>{o * 1000}</pproTicksOut>{file}{extra}</clipitem>')


INNER = (f'<sequence id="sequence-2"><name>parte</name>{R24}<duration>96</duration><media>'
         f'<video><track>{_clip("c1", "fala", R24, 0, 48, 10, 58, _file("f1", "A.mp4"), "<link><linkclipref>c1a</linkclipref></link>")}'
         f'{_clip("c2", "slowmo", R48, 48, 96, 0, 96, _file("f2", "B.mp4"))}</track></video>'
         f'<audio><track>{_clip("c1a", "fala", R24, 0, 48, 10, 58, _file("f1", "A.mp4", False), "<link><linkclipref>c1</linkclipref></link>")}</track></audio>'
         '</media></sequence>')


def _xml(tmp_path):
    nest1 = _clip('n1', 'parte', R24, 0, 72, 24, 96, INNER)
    nest2 = _clip('n2', 'parte', R24, 72, 120, 0, 48, '<sequence id="sequence-2"/>')
    adj = _clip('adj', 'Adjustment Layer', R24, 0, 120, 0, 120, '<file id="f9"><name>Black Video</name><mediaSource>Slug</mediaSource></file>')
    na1 = _clip('na1', 'parte', R24, 0, 72, 24, 96, '<sequence id="sequence-2"/>')
    na2 = _clip('na2', 'parte', R24, 0, 72, 24, 96, '<sequence id="sequence-2"/>')
    x = (f'<?xml version="1.0"?><xmeml version="4"><sequence id="sequence-1"><name>completo</name>{R24}<duration>120</duration>'
         f'<media><video><track>{nest1}{nest2}</track><track>{adj}</track></video>'
         f'<audio><track>{na1}</track><track>{na2}</track></audio></media></sequence></xmeml>')
    p = tmp_path / 'nested.xml'
    p.write_text(x, encoding='utf-8')
    return p


def test_flatten_places_and_trims_every_take(tmp_path):
    r = nle_read.flatten_fcp7(str(_xml(tmp_path)), str(tmp_path / 'flat.xml'))
    assert r['nests_expanded'] == ['parte'] and r['clips_video'] == 3
    assert any('Adjustment Layer' in d for d in r['dropped'])            # never re-created as black video
    tl = nle_read.read_fcp7(str(tmp_path / 'flat.xml'))
    v = [(i['name'], round(i['tl_in'], 3), round(i['tl_out'], 3), i['src_in_f'], i['src_out_f']) for i in tl['items'] if i['track'] == 'V1']
    assert v == [('fala', 0.0, 1.0, 34, 58), ('slowmo', 1.0, 3.0, 0, 96), ('fala', 3.0, 5.0, 10, 58)]
    a = [i for i in tl['items'] if i['track'].startswith('A')]
    assert len(a) == 1                                                   # stereo nest on A1+A2 expanded once, not twice
    root = ET.parse(tmp_path / 'flat.xml').getroot()
    seen = set()
    for f in root.iter('file'):                                          # first use of each file carries its path
        if f.find('pathurl') is not None:
            seen.add(f.get('id'))
        else:
            assert f.get('id') in seen
    ids = {c.get('id') for c in root.iter('clipitem')}
    assert len(ids) == sum(1 for _ in root.iter('clipitem'))             # unique ids even with the nest used twice
    assert all(lk.text in ids for lk in root.iter('linkclipref'))


def test_read_fcp7_reads_through_nests(tmp_path):
    tl = nle_read.read_fcp7(str(_xml(tmp_path)))
    assert tl['nested'] == ['parte'] and len([i for i in tl['items'] if i['track'] == 'V1']) == 3
    slow = next(i for i in tl['items'] if i['name'] == 'slowmo')
    assert abs(slow['src_out'] - 2.0) < 1e-6                              # source seconds use the clip's own rate (48)
