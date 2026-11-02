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
