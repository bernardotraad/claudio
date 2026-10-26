"""The caption the editor fixed in Premiere is read back from the .prproj and taught to the song."""
import base64, gzip, struct

from claudio import core, motion, nle_read

TK = 254016000000


def _payload(text):
    b = text.encode()
    return base64.b64encode(b'\x00' * 8 + struct.pack('<I', 13) + b'LucidaConsole\x00' + struct.pack('<I', len(b)) + b + b'\x00\x00').decode()


def _prproj(path, cues, name='multicam valendo'):
    items, blocks, refs = [], [], []
    for k, (s, e, t) in enumerate(cues):
        iid, bid = 100 + k, 200 + k
        refs.append(f'<TrackItem Index="{k}" ObjectRef="{iid}"/>')
        items.append(f'<CaptionDataClipTrackItem ObjectID="{iid}"><DataClipTrackItem><ClipTrackItem><TrackItem>'
                     f'<Start>{int(s * TK)}</Start><End>{int(e * TK)}</End></TrackItem></ClipTrackItem></DataClipTrackItem>'
                     f'<BlockVector><BlockVectorItem Index="0" ObjectRef="{bid}"/></BlockVector></CaptionDataClipTrackItem>')
        blocks.append(f'<Block ObjectID="{bid}"><FormattedTextData Encoding="base64" BinaryHash="h{k}">{_payload(t)}</FormattedTextData></Block>')
    xml = (f'<PremiereData><Sequence ObjectUID="seq"><TrackGroups><TrackGroup Index="0"><Second ObjectRef="10"/></TrackGroup>'
           f'</TrackGroups><Name>{name}</Name></Sequence>'
           f'<DataTrackGroup ObjectID="10"><TrackGroup><Tracks><Track Index="0" ObjectURef="trk"/></Tracks></TrackGroup></DataTrackGroup>'
           f'<CaptionDataClipTrack ObjectUID="trk"><ClipTrack><ClipItems><TrackItems>{"".join(refs)}</TrackItems></ClipItems>'
           f'</ClipTrack></CaptionDataClipTrack>{"".join(items)}{"".join(blocks)}</PremiereData>')
    open(path, 'wb').write(gzip.compress(xml.encode()))


def test_reads_the_edited_caption_track(tmp_path):
    p = tmp_path / 'job.prproj'
    _prproj(p, [(7.466, 12.304, 'Ela mudou aquilo que eu pensava'), (117.993, 124.583, 'Sinto sua falta, não tem explicação')])
    cues = nle_read.premiere_captions(str(p), 'multicam valendo')
    assert cues == [{'s': 7.466, 'e': 12.304, 'text': 'Ela mudou aquilo que eu pensava'},
                    {'s': 117.993, 'e': 124.583, 'text': 'Sinto sua falta, não tem explicação'}]
    assert '00:00:07,466 --> 00:00:12,304' in open(nle_read.write_srt(cues, str(tmp_path / 'r.srt')), encoding='utf-8-sig').read()


def test_learns_only_real_text_changes(tmp_path, monkeypatch):
    saved = {}
    monkeypatch.setattr(core, 'save_lyric_fix', lambda src, old, new: saved.update({old: new}))
    lines = ['No fim ela partiu sem dizer nada', 'Eu fiquei no chão', 'Sinto sua falta, não tenho explicação', 'Vai ver']
    W, t = [], 0.0
    for ln in lines:
        for k, w in enumerate(ln.split()):
            W.append({'w': w, 's': t, 'e': t + 0.4, 'lyrics': True, 'src': 'song.wav', **({'line': True} if k == 0 else {})})
            t += 0.5
    monkeypatch.setattr(motion, 'timeline_words', lambda x: {'words': W})
    # the editor moved "Eu" into the first block's time (re-timed only) and fixed one word
    cues = [{'s': 0, 'e': 3, 'text': 'No fim ela partiu sem dizer nada'}, {'s': 3, 'e': 5, 'text': 'Eu fiquei no chão'},
            {'s': 5, 'e': 8, 'text': 'Sinto sua falta, não tem explicação'}, {'s': 8, 'e': 9, 'text': 'Vai ver'}]
    learned = motion.learn_caption_edits('x.xml', cues)
    assert saved == {'Sinto sua falta, não tenho explicação': 'Sinto sua falta, não tem explicação'} and len(learned) == 1


def test_lists_the_sequences_with_a_caption_track(tmp_path):
    p = tmp_path / 'job.prproj'
    _prproj(p, [(1.0, 2.0, 'Você que paga aluguel'), (2.0, 3.0, 'e sente que o')], name='INC - 01 Robson (limpo)')
    assert nle_read.caption_sequences(str(p)) == {'INC - 01 Robson (limpo)': 2}
