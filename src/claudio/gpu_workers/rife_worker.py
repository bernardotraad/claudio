"""RIFE frame interpolation, run by claudio.slowmo in the GPU venv (torch) — never imported by claudio itself.

  python rife_worker.py <rife_dir> <width> <height> <factor> <frames_in> [scale]

stdin: <frames_in> raw rgb48le frames · stdout: round(frames_in * factor) raw rgb48le frames, played at the source
rate = the shot slowed down `factor` times. Output frame k shows source time k / factor: on a source frame it is
that frame untouched, between two it is RIFE's in-between at that fraction (v4 models take any timestep, so 2.5x
works). Across a cut (the two frames don't look alike) it holds the nearer frame instead of morphing them.
16-bit in and out: log footage keeps its 10 bits (the editor grades it afterwards).
"""
import json, math, os, sys

import numpy as np
import torch
from torch.nn import functional as F

rife_dir, W, H, factor, n_in = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4]), int(sys.argv[5])
scale = float(sys.argv[6]) if len(sys.argv) > 6 else (0.5 if W * H > 2560 * 1440 else 1.0)
sys.path.insert(0, rife_dir)
from train_log.RIFE_HDv3 import Model  # noqa: E402

torch.set_grad_enabled(False)
dev = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
if dev.type == 'cuda':
    torch.backends.cudnn.benchmark = True
model = Model()
model.load_model(os.path.join(rife_dir, 'train_log'), -1)
model.eval()
model.device()
amp = dev.type == 'cuda'          # fp16 where it is safe (autocast keeps the warp's grid_sample in fp32)

tmp = max(128, int(128 / scale))
ph, pw = ((H - 1) // tmp + 1) * tmp, ((W - 1) // tmp + 1) * tmp
FRAME = W * H * 3 * 2
src, out = sys.stdin.buffer, sys.stdout.buffer


def read():
    b = src.read(FRAME)
    if len(b) < FRAME:
        return None
    x = torch.from_numpy(np.frombuffer(b, np.uint16).reshape(H, W, 3).astype(np.float32)).to(dev)
    x = (x.permute(2, 0, 1).unsqueeze(0) / 65535.0)
    x = F.pad(x, (0, pw - W, 0, ph - H))
    return x


def write(x):
    y = (x[0, :, :H, :W].float().clamp(0, 1) * 65535.0 + 0.5).to(torch.int32).permute(1, 2, 0).cpu().numpy().astype(np.uint16)
    out.write(y.tobytes())


def alike(a, b):
    sa = F.interpolate(a.float(), (32, 32), mode='bilinear', align_corners=False)
    sb = F.interpolate(b.float(), (32, 32), mode='bilinear', align_corners=False)
    return float((sa - sb).abs().mean()) < 0.12          # a cut changes the whole frame; motion only some of it


n_out = int(round(n_in * factor))
cur, nxt, i = read(), read(), 0
made = 0
for k in range(n_out):
    t = k / factor
    while t >= i + 1 and nxt is not None:
        cur, nxt, i = nxt, read(), i + 1
    frac = t - i
    if frac < 1e-3 or nxt is None:
        write(cur)
    elif frac > 1 - 1e-3:
        write(nxt)
    elif not alike(cur, nxt):
        write(cur if frac < 0.5 else nxt)
    else:
        with torch.autocast('cuda', dtype=torch.float16, enabled=amp):
            write(model.inference(cur, nxt, frac, scale))
    made += 1
out.flush()
print(json.dumps({'frames_out': made, 'scale': scale, 'device': dev.type}), file=sys.stderr)
