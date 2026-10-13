"""A lav on one side becomes dual mono; two live mics are kept; speech is levelled."""
import xml.etree.ElementTree as ET

from conftest import ff, needs_ffmpeg
from claudio import nle_read, stereo

R24 = '<rate><timebase>24</timebase><ntsc>FALSE</ntsc></rate>'


def _clip(cid, tidx, fid, path, full):
    f = (f'<file id="{fid}"><name>x.mov</name><pathurl>file://localhost{path}</pathurl>{R24}<duration>240</duration></file>'
         if full else f'<file id="{fid}"/>')
    return (f'<clipitem id="{cid}"><name>{fid}</name>{R24}<start>0</start><end>96</end><in>0</in><out>96</out>{f}'
            f'<sourcetrack><mediatype>audio</mediatype><trackindex>{tidx}</trackindex></sourcetrack></clipitem>')


@needs_ffmpeg
def test_one_sided_lav_becomes_dual_mono(tmp_path):
    lav = tmp_path / 'lav.mov'        # tone on L, silence on R
    two = tmp_path / 'two.mov'        # tone on both
    ff('-f', 'lavfi', '-i', 'sine=f=440:d=5', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=mono:d=5',
       '-filter_complex', '[0][1]amerge=inputs=2', '-c:a', 'pcm_s16le', '-ar', '48000', str(lav))
    ff('-f', 'lavfi', '-i', 'sine=f=440:d=5', '-ac', '2', '-c:a', 'pcm_s16le', '-ar', '48000', str(two))
    x = (f'<?xml version="1.0"?><xmeml version="4"><sequence id="s1"><name>corte</name>{R24}<duration>96</duration><media><audio>'
         f'<track>{_clip("a1", 1, "f1", lav, True)}</track><track>{_clip("a2", 2, "f1", lav, False)}</track>'
         '</audio></media></sequence></xmeml>')
    src = tmp_path / 'tl.xml'
    src.write_text(x, encoding='utf-8')
    r = stereo.stereo_fix(str(src), str(tmp_path / 'out.xml'), flatten=False)
    assert r['sequence'] == 'corte (estéreo)' and len(r['made_dual_mono']) == 1 and r['made_dual_mono'][0]['side'] == 'L'
    tl = nle_read.read_fcp7(str(tmp_path / 'out.xml'))
    assert [i['channel'] for i in tl['items']] == [1, 1]
    src.write_text(x.replace(str(lav), str(two)), encoding='utf-8')
    r2 = stereo.stereo_fix(str(src), str(tmp_path / 'out2.xml'), flatten=False)
    assert r2['made_dual_mono'] == [] and [i['channel'] for i in nle_read.read_fcp7(str(tmp_path / 'out2.xml'))['items']] == [1, 2]


def test_gain_is_capped_and_keyframes_respected():
    c = ET.fromstring('<clipitem><name>x</name></clipitem>')
    assert stereo._set_gain(c, 10.0) and float(stereo._levels_filter(c).findtext('value')) <= stereo.MAX_GAIN
    k = ET.fromstring('<clipitem><filter><effect><name>Audio Levels</name><parameter><parameterid>level</parameterid>'
                      '<value>1</value><keyframe><when>0</when><value>1</value></keyframe></parameter></effect></filter></clipitem>')
    assert stereo._set_gain(k, 2.0) is False
