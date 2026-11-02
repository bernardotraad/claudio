"""Kokoro narration (82M, Apache-2.0), run by claudio.motion.narration in the GPU venv — never imported by claudio itself.
Native Brazilian voices (pm_alex, pm_santa, pf_dora): approved by ear over Qwen3-TTS, whose only "Portuguese" is
European. Small enough for the CPU too.

  python tts_kokoro.py <job.json>     job: {"chunks": [...], "voice": "pm_alex", "lang": "p", "speed": 1.0, "out_dir"}

Writes c0.wav, c1.wav… (one per paragraph, 24 kHz) and prints {"wavs": [...], "sr": 24000, "seconds": ...}.
"""
import json, os, sys, time

import numpy as np
import soundfile as sf
from kokoro import KPipeline

job = json.load(open(sys.argv[1], encoding='utf-8'))
t0 = time.time()
pipe = KPipeline(lang_code=job.get('lang', 'p'), repo_id='hexgrad/Kokoro-82M')
wavs = []
for k, text in enumerate(job['chunks']):
    a = np.concatenate([np.asarray(r.audio) for r in pipe(text, voice=job.get('voice', 'pm_alex'), speed=float(job.get('speed', 1.0)))])
    p = os.path.join(job['out_dir'], f'c{k}.wav')
    sf.write(p, a, 24000)
    wavs.append(p)
print(json.dumps({'wavs': wavs, 'sr': 24000, 'seconds': round(time.time() - t0, 1)}))
