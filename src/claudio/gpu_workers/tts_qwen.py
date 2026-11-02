"""Qwen3-TTS narration, run by claudio.motion.narration in the GPU venv (torch) — never imported by claudio itself.

  python tts_qwen.py <job.json>

job: {"chunks": [text, ...], "language": "Portuguese", "out_dir": ..., "size": "1.7B",
      one of  "speaker": "Ryan" (+ "instruct"?)                → CustomVoice (the 9 built-in voices)
              "design": "locutor brasileiro, grave, calmo"    → VoiceDesign (a voice from a description)
              "ref_audio": path, "ref_text": its transcript    → Base (clones the voice in the clip)}
Writes c0.wav, c1.wav… in out_dir (one per paragraph) and prints {"wavs": [...], "sr": ...}. A designed voice is
made once, on the first paragraph, and the rest clone that take — so every paragraph is the same person.
"""
import json, os, sys, time

import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel

job = json.load(open(sys.argv[1], encoding='utf-8'))
size, lang, out_dir = job.get('size', '1.7B'), job.get('language', 'Portuguese'), job['out_dir']
try:
    import flash_attn  # noqa: F401
    attn = {'attn_implementation': 'flash_attention_2'}
except ImportError:
    attn = {}


def load(kind):
    return Qwen3TTSModel.from_pretrained(f'Qwen/Qwen3-TTS-12Hz-{size}-{kind}', device_map='cuda:0', dtype=torch.bfloat16, **attn)


t0 = time.time()
wavs, sr, chunks = [], None, job['chunks']
clone = None
if job.get('design'):
    m = load('VoiceDesign')
    w, sr = m.generate_voice_design(text=chunks[0], language=lang, instruct=job['design'])
    p = os.path.join(out_dir, 'c0.wav'); sf.write(p, w[0], sr); wavs.append(p)
    clone = (p, chunks[0])
    del m; torch.cuda.empty_cache()
    chunks = chunks[1:]
elif job.get('ref_audio'):
    clone = (job['ref_audio'], job.get('ref_text'))
if clone:
    if chunks:
        m = load('Base')
        prompt = m.create_voice_clone_prompt(ref_audio=clone[0], ref_text=clone[1], x_vector_only_mode=not clone[1]) \
            if hasattr(m, 'create_voice_clone_prompt') else None
        for text in chunks:
            kw = {'voice_clone_prompt': prompt} if prompt is not None else \
                {'ref_audio': clone[0], 'ref_text': clone[1], 'x_vector_only_mode': not clone[1]}
            w, sr = m.generate_voice_clone(text=text, language=lang, **kw)
            p = os.path.join(out_dir, f'c{len(wavs)}.wav'); sf.write(p, w[0], sr); wavs.append(p)
else:
    m = load('CustomVoice')
    for text in chunks:
        w, sr = m.generate_custom_voice(text=text, language=lang, speaker=job.get('speaker') or 'Ryan',
                                        **({'instruct': job['instruct']} if job.get('instruct') else {}))
        p = os.path.join(out_dir, f'c{len(wavs)}.wav'); sf.write(p, w[0], sr); wavs.append(p)
print(json.dumps({'wavs': wavs, 'sr': sr, 'seconds': round(time.time() - t0, 1),
                  'vram_gb': round(torch.cuda.max_memory_allocated() / 2 ** 30, 2)}))
