"""Local narration: Whisper's word timings with the script's spelling, each word in its paragraph (act)."""
from claudio import motion


def test_script_words_keep_the_script_spelling():
    heard = [{'w': 'Na', 's': 4.76, 'e': 5.0}, {'w': 'Ink,', 's': 5.28, 'e': 5.6}, {'w': 'queda', 's': 5.7, 'e': 6.0},
             {'w': 'apartamento', 's': 6.1, 'e': 6.8}, {'w': 'vinte', 's': 7.0, 'e': 7.3}, {'w': 'e', 's': 7.3, 'e': 7.4},
             {'w': 'quatro', 's': 7.4, 'e': 7.8}]
    ws = motion._script_words(heard, 'Na INC, cada apartamento\n\n24', [0.2, 6.9])
    assert [w['w'] for w in ws] == ['Na', 'INC,', 'cada', 'apartamento', '24']
    assert ws[1]['s'] == 5.28                        # the timing is the voice's
    assert [w['act'] for w in ws] == [0, 0, 0, 0, 1]
    assert ws[-1]['s'] == 7.0 and ws[-1]['e'] == 7.8   # "vinte e quatro" heard → "24" written, over the same time


def test_qwen_voice_must_be_known(tmp_path, monkeypatch):
    import pytest
    with pytest.raises(ValueError):
        motion._qwen(['oi'], str(tmp_path), 'voz-que-nao-existe', 'pt')


def test_spoken_reads_brands_as_they_sound():
    assert motion.spoken('Na INC, cada apartamento da inc', {'INC': 'Ínqui'}) == 'Na Ínqui, cada apartamento da Ínqui'
    assert motion.spoken('Na INC, cada apartamento', {}) == 'Na INC. cada apartamento'    # "INCada" no more
    assert motion.spoken('Incrível, não?', {'INC': 'Ínqui'}) == 'Incrível, não?'          # whole words only


def test_remote_narration_round_trip(tmp_path, monkeypatch):
    """The Mac side: script, pronunciation and the voice file go out, narration.wav + words.json come back."""
    import base64, json, subprocess
    from claudio import share
    from conftest import ff
    voice = str(tmp_path / 'voz.wav')
    ff('-f', 'lavfi', '-i', 'sine=frequency=220:duration=2', voice)
    sent = {}

    def fake_ssh(args, timeout=120, data=None):                    # what the desktop's claudio-run narrate answers
        a = json.loads(base64.b64decode(args[-1][4:]))
        sent.update(a, cmd=args[:-2], voice_bytes=len(base64.b64decode(data)) if data else 0)
        flac = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-c:a', 'flac',
                               '-f', 'flac', '-'], capture_output=True).stdout
        out = {'ok': True, 'info': 'Chatterbox · voz clonada', 'duration_s': 1.0, 'flac': base64.b64encode(flac).decode(),
               'words': {'words': [{'w': 'INC', 's': 0.2, 'e': 0.5, 'act': 0}], 'chunks': [], 'dur': 1.0}}
        return subprocess.CompletedProcess(args, 0, (json.dumps(out) + '\n').encode(), b'')
    monkeypatch.setattr(share, '_ssh', fake_ssh)
    monkeypatch.setattr(share, '_reachable', lambda progress: (True, None))
    monkeypatch.setattr(motion, '_remote_ready', lambda engine: True)
    monkeypatch.setenv('CLAUDIO_SHARE_FROM', 'bernardo@desktop')
    r = motion.narration('Na INC, cada apartamento', str(tmp_path / 'out'), engine='chatterbox', voice=voice, said={'INC': 'Ink'})
    assert sent['engine'] == 'chatterbox' and sent['said'] == {'INC': 'Ink'} and sent['voice_stdin'] == 'voz.wav'
    assert sent['voice_bytes'] > 1000 and sent['cmd'][-1] == '/home/bernardo/.local/bin/claudio-run'
    assert r['duration_s'] == 1.0 and 'no desktop' in r['info']
    assert json.load(open(tmp_path / 'out' / 'words.json'))['words'][0]['w'] == 'INC'
