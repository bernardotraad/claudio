"""claudio.ingest — offload a card into the job folder, verified, then start the logging.

  ingest(card, job, camera=None)
      • finds the media on the card (Sony PRIVATE/M4ROOT, Canon DCIM, Blackmagic, DJI, GoPro, recorders, photos)
      • copies with a checksum (xxHash64): hashed while copying, the destination is READ BACK and hashed again
      • lays out the job with the profile's folders (claudio.profile): brutos/<camera>/ · audio/ · fotos/ ·
        projeto/ · trilhas/   (Sony XML sidecars travel with their clips)
      • never overwrites: a file with the same name and other content goes to brutos/<camera>/CARD_n/
      • never deletes or changes anything on the card
      • writes _ingest.md + _ingest.json (every file, size, hash, verified) in the job folder
"""
import datetime, json, os, shutil

try:
    import xxhash
    _hasher = lambda: xxhash.xxh64()
    HASH = 'xxh64'
except ImportError:                       # pragma: no cover
    import hashlib
    _hasher = lambda: hashlib.blake2b(digest_size=8)
    HASH = 'blake2b64'

from claudio import core, profile
from claudio.multicam import AUDIO_EXT

PHOTO_EXT = {'.arw', '.cr2', '.cr3', '.nef', '.raf', '.dng', '.rw2', '.orf', '.jpg', '.jpeg', '.heic', '.tif', '.tiff'}
SIDECAR_EXT = {'.xml', '.xmp', '.thm', '.lrf', '.srt'}
SKIP_DIRS = {'.trashes', '.spotlight-v100', '.fseventsd', 'system volume information', '$recycle.bin', 'thmbnl', 'misc', 'sub', 'avf_info'}
CHUNK = 8 * 1024 * 1024


def _hash_file(p, progress=None):
    h = _hasher()
    with open(p, 'rb') as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
            if progress:
                progress(len(b))
    return h.hexdigest()


def scan_card(card):
    card = core.local_path(card)
    media = []
    for dp, dn, fn in os.walk(card):
        dn[:] = [d for d in dn if d.lower() not in SKIP_DIRS and not d.startswith('.')]
        for f in sorted(fn):
            if f.startswith('._'):
                continue
            ext = os.path.splitext(f)[1].lower()
            p = os.path.join(dp, f)
            kind = 'video' if ext in core.VIDEO_EXT else 'audio' if ext in AUDIO_EXT else 'photo' if ext in PHOTO_EXT else None
            if kind:
                media.append({'src': p, 'kind': kind, 'size': os.path.getsize(p)})
    # sidecars that belong to a clip (Sony C0001M01.XML next to C0001.MP4)
    stems = {os.path.splitext(m['src'])[0] for m in media if m['kind'] == 'video'}
    for dp, dn, fn in os.walk(card):
        for f in fn:
            if os.path.splitext(f)[1].lower() in SIDECAR_EXT:
                base = os.path.join(dp, f)
                stem = os.path.splitext(base)[0]
                owner = next((s for s in stems if stem.startswith(s) or os.path.basename(stem).startswith(os.path.basename(s))), None)
                if owner:
                    media.append({'src': base, 'kind': 'sidecar', 'size': os.path.getsize(base), 'owner': owner})
    return media


def _camera_name(media, camera):
    if camera:
        return camera
    vids = [m for m in media if m['kind'] == 'video']
    for m in vids[:3]:
        cam = core.camera_of(m['src'])
        if cam:
            return cam
    return 'CAM'


def ingest(card, job, camera=None, progress=lambda *_: None, dry_run=False):
    job = core.local_path(job)
    media = scan_card(card)
    if not media:
        raise ValueError(f'no media found on {card}')
    cam = _camera_name(media, camera)
    total = sum(m['size'] for m in media)
    free = shutil.disk_usage(os.path.dirname(job.rstrip('/')) if not os.path.exists(job) else job).free
    if total * 1.02 > free:
        raise OSError(f'not enough space: need {total / 1e9:.1f} GB, free {free / 1e9:.1f} GB')
    dirs = {'video': os.path.join(profile.folder('brutos', job), cam), 'audio': profile.folder('audio', job),
            'photo': profile.folder('fotos', job)}
    plan = []
    card_sub = None
    for m in sorted(media, key=lambda m: (m['kind'] == 'sidecar', m['src'])):
        kind = m['kind'] if m['kind'] != 'sidecar' else 'video'
        dst_dir = dirs[kind]
        name = os.path.basename(m['src'])
        if m['kind'] == 'sidecar':
            owner_dst = next((p['dst'] for p in plan if os.path.splitext(p['src'])[0] == m['owner']), None)
            dst_dir = os.path.dirname(owner_dst) if owner_dst else dst_dir
        dst = os.path.join(dst_dir, name)
        if os.path.exists(dst) and m['kind'] == 'sidecar':
            if _hash_file(dst) == _hash_file(m['src']):
                plan.append({**m, 'dst': dst, 'status': 'já copiado (idêntico)'})
                continue
            stem, ext = os.path.splitext(name)
            k = 2
            while os.path.exists(os.path.join(dst_dir, f'{stem}_{k}{ext}')):
                k += 1
            dst = os.path.join(dst_dir, f'{stem}_{k}{ext}')
        elif os.path.exists(dst):
            # already ingested? look in the camera folder and in every CARD_n folder before making a new one
            same = None
            for cand in [dst] + [os.path.join(dirs[kind], d, name) for d in sorted(os.listdir(dirs[kind])) if d.startswith('CARD_')]:
                if os.path.exists(cand) and os.path.getsize(cand) == m['size'] and _hash_file(cand) == _hash_file(m['src']):
                    same = cand
                    break
            if same:
                plan.append({**m, 'dst': same, 'status': 'já copiado (idêntico)'})
                continue
            if card_sub is None:                          # same name, other content: a new card → its own folder
                k = 2
                while os.path.exists(os.path.join(dirs[kind], f'CARD_{k}', name)):
                    k += 1
                card_sub = f'CARD_{k}'
            dst = os.path.join(dirs[kind], card_sub, name)
        plan.append({**m, 'dst': dst, 'status': 'copiar'})
    if dry_run:
        return {'camera': cam, 'files': len(plan), 'bytes': total, 'plan': [(core.nle_path(p['src']), core.nle_path(p['dst']), p['status']) for p in plan]}
    for d in ('projeto', 'trilhas'):
        profile.folder(d, job, create=True)
    done = [0]
    def tick(n):
        done[0] += n
        progress('copiando', done[0] / (2 * total))
    for p in plan:
        if p['status'] != 'copiar':
            done[0] += 2 * p['size']
            continue
        os.makedirs(os.path.dirname(p['dst']), exist_ok=True)
        part = p['dst'] + '.part'
        h = _hasher()
        with open(p['src'], 'rb') as fi, open(part, 'wb') as fo:
            while True:
                b = fi.read(CHUNK)
                if not b:
                    break
                h.update(b); fo.write(b); tick(len(b))
            fo.flush(); os.fsync(fo.fileno())
        src_hash = h.hexdigest()
        dst_hash = _hash_file(part, tick)                      # read back what landed on disk
        if dst_hash != src_hash:
            os.remove(part)
            p.update(status='ERRO: checksum diferente', hash=src_hash)
            continue
        os.replace(part, p['dst'])
        shutil.copystat(p['src'], p['dst'])
        p.update(status='ok', hash=src_hash)
    ok = [p for p in plan if p['status'] in ('ok', 'já copiado (idêntico)')]
    bad = [p for p in plan if p['status'].startswith('ERRO')]
    manifest = {'when': datetime.datetime.now().isoformat(timespec='seconds'), 'card': core.nle_path(core.local_path(card)),
                'camera': cam, 'hash': HASH, 'files': [{'src': core.nle_path(p['src']), 'dst': core.nle_path(p['dst']), 'size': p['size'],
                                                         'hash': p.get('hash'), 'status': p['status']} for p in plan]}
    jp = os.path.join(job, '_ingest.json')
    hist = json.load(open(jp)) if os.path.exists(jp) else []
    hist.append(manifest)
    json.dump(hist, open(jp, 'w'), ensure_ascii=False, indent=1)
    md = os.path.join(job, '_ingest.md')
    with open(md, 'a', encoding='utf-8') as f:
        f.write(f"\n## {manifest['when']} · {cam} · {core.nle_path(core.local_path(card))}\n\n"
                f"{len(ok)} arquivos ok · {len(bad)} com erro · {total / 1e9:.2f} GB · {HASH} verificado relendo o destino\n\n"
                "| arquivo | destino | tamanho | hash | status |\n|---|---|---|---|---|\n")
        for p in plan:
            f.write(f"| {os.path.basename(p['src'])} | {os.path.relpath(p['dst'], job)} | {p['size'] / 1e6:.1f} MB | {p.get('hash', '')} | {p['status']} |\n")
    return {'camera': cam, 'copied': sum(1 for p in plan if p['status'] == 'ok'), 'already_there': sum(1 for p in plan if p['status'].startswith('já')),
            'errors': [core.nle_path(p['src']) for p in bad], 'gb': round(total / 1e9, 2), 'manifest': core.nle_path(md),
            'video_files': [p['dst'] for p in plan if p['kind'] == 'video' and p['status'] in ('ok', 'já copiado (idêntico)')]}
