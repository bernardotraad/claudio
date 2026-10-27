"""QC of how the voice sounds: a muffled mic and two different mics on L/R are measured, not guessed."""
from conftest import ff, needs_ffmpeg
from claudio import qc


@needs_ffmpeg
def test_muffled_and_two_mics(tmp_path):
    clean = tmp_path / 'clean.wav'
    ff('-f', 'lavfi', '-i', 'anoisesrc=d=4:c=pink:a=0.3', '-ac', '2', '-ar', '48000', str(clean))
    muff = tmp_path / 'muff.wav'
    ff('-f', 'lavfi', '-i', 'anoisesrc=d=4:c=pink:a=0.3', '-af', 'lowpass=f=900,lowpass=f=900', '-ac', '2', '-ar', '48000', str(muff))
    two = tmp_path / 'two.wav'
    ff('-f', 'lavfi', '-i', 'anoisesrc=d=4:c=pink:a=0.3:seed=1', '-f', 'lavfi', '-i', 'anoisesrc=d=4:c=pink:a=0.3:seed=2',
       '-filter_complex', '[0][1]amerge=inputs=2', '-ar', '48000', str(two))
    c, m, t = (qc.voice_report(str(p), 0, 4) for p in (clean, muff, two))
    assert c['above_3k_db'] > qc.MUFFLED_DB > m['above_3k_db']
    assert c['lr_corr'] > 0.9 and t['lr_corr'] < 0.5
