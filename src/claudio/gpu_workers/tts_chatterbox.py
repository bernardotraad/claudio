"""Chatterbox Multilingual (Resemble AI, MIT) narration cloning a voice, run by claudio.motion.narration in its own
venv (it pins an older torch than the GPU venv) — never imported by claudio itself. Cloning a Brazilian take it keeps
the Brazilian accent (Qwen3-TTS doesn't). Its Perth watermark is off: the narration goes into client deliverables.

  python tts_chatterbox.py <job.json>     job: {"chunks": [...], "ref_audio": path, "lang": "pt", "out_dir", "seed"?}

Writes c0.wav, c1.wav… (one per paragraph) and prints {"wavs": [...], "sr": ..., "seconds": ..., "vram_gb": ...}.
"""
import json, os, sys, time

import perth

perth.PerthImplicitWatermarker = getattr(perth, 'DummyWatermarker', None) or \
    (lambda: type('NoMark', (), {'apply_watermark': lambda self, w, sample_rate=None: w})())

import torch  # noqa: E402
import torchaudio as ta  # noqa: E402
from chatterbox.mtl_tts import ChatterboxMultilingualTTS  # noqa: E402

job = json.load(open(sys.argv[1], encoding='utf-8'))
t0 = time.time()
m = ChatterboxMultilingualTTS.from_pretrained(device='cuda' if torch.cuda.is_available() else 'cpu')
wavs = []
for k, text in enumerate(job['chunks']):
    torch.manual_seed(int(job.get('seed', 1)))          # the same script reads the same way when redone
    w = m.generate(text, language_id=job.get('lang', 'pt'), audio_prompt_path=job['ref_audio'])
    p = os.path.join(job['out_dir'], f'c{k}.wav')
    ta.save(p, w, m.sr)
    wavs.append(p)
print(json.dumps({'wavs': wavs, 'sr': m.sr, 'seconds': round(time.time() - t0, 1),
                  'vram_gb': round(torch.cuda.max_memory_allocated() / 2 ** 30, 2) if torch.cuda.is_available() else 0}))
