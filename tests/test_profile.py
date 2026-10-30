"""The editor's profile: no folder name is spelled in the engine. Without perfil.json the job uses neutral names; with
Bernardo's perfil.json every path comes out exactly as it did before the profile existed."""
import json, os

import pytest

from claudio import clients, ingest, music, profile

BERNARDO = {
    'pastas': {'brutos': 'BRUTOS', 'audio': 'BRUTOS/00 AUDIO', 'fotos': 'BRUTOS/03 FOTOS', 'projeto': '#projeto',
               'trilhas': '#trilhas', 'legendas': '#legendas', 'overlays': '#projeto/claudio_overlays',
               'backups': 'claudio_backups'},
}


@pytest.fixture
def mine(tmp_path, monkeypatch):
    def use(prof):
        p = tmp_path / 'perfil.json'
        p.write_text(json.dumps(prof), encoding='utf-8')
        monkeypatch.setenv('CLAUDIO_PROFILE', str(p))
    return use


def test_neutral_defaults(tmp_path):
    job = str(tmp_path / 'job')
    assert profile.folder('legendas', job) == os.path.join(job, 'legendas')
    assert profile.folder('audio', job) == os.path.join(job, 'brutos', 'audio')
    assert profile.job_of(os.path.join(job, 'projeto', 'x.prproj')) == job
    assert profile.job_of(os.path.join(job, 'x.prproj')) == job               # a project at the job's root
    assert profile.summary()['file'] is None


def test_bernardo_profile_keeps_the_old_paths(tmp_path, mine):
    mine(BERNARDO)
    job = str(tmp_path / '2026-10-19 INC CORRETORES 9')
    assert profile.folder('legendas', job) == os.path.join(job, '#legendas')
    assert profile.folder('trilhas', job) == os.path.join(job, '#trilhas')
    assert profile.folder('audio', job) == os.path.join(job, 'BRUTOS', '00 AUDIO')
    assert profile.folder('fotos', job) == os.path.join(job, 'BRUTOS', '03 FOTOS')
    assert profile.job_of(os.path.join(job, '#projeto', 'INC 9.prproj')) == job   # was dirname(dirname(prproj))
    assert profile.raw_names()[0] == 'BRUTOS'


def test_partial_profile_merges_over_defaults(tmp_path, mine):
    mine({'pastas': {'legendas': 'subs'}})
    job = str(tmp_path)
    assert profile.folder('legendas', job) == os.path.join(job, 'subs')
    assert profile.folder('trilhas', job) == os.path.join(job, 'trilhas')


def test_a_broken_profile_falls_back_to_defaults(tmp_path, monkeypatch):
    p = tmp_path / 'perfil.json'
    p.write_text('{ not json', encoding='utf-8')
    monkeypatch.setenv('CLAUDIO_PROFILE', str(p))
    assert profile.get('pastas.legendas') == 'legendas'


def test_footage_walks_skip_work_folders(mine):
    assert profile.skip_dir('trilhas') and profile.skip_dir('projeto') and profile.skip_dir('legendas')
    assert profile.skip_dir('#qualquer') and profile.skip_dir('.git')
    assert not profile.skip_dir('A7III') and not profile.skip_dir('brutos')
    mine(BERNARDO)
    assert profile.skip_dir('#trilhas') and profile.skip_dir('claudio_backups')


def test_ingest_lays_out_the_job_from_the_profile(tmp_path, mine):
    card = tmp_path / 'card'
    (card / 'PRIVATE' / 'M4ROOT' / 'CLIP').mkdir(parents=True)
    (card / 'PRIVATE' / 'M4ROOT' / 'CLIP' / 'C0001.MP4').write_bytes(b'x' * 10)
    (card / 'ZOOM0001.WAV').write_bytes(b'x' * 10)
    (card / 'DSC0001.JPG').write_bytes(b'x' * 10)
    mine(BERNARDO)
    job = tmp_path / 'job'
    job.mkdir()
    r = ingest.ingest(str(card), str(job), camera='A7III', dry_run=True)
    dst = sorted(os.path.relpath(ingest.core.local_path(d), job) for _, d, _ in r['plan'])
    assert dst == sorted([os.path.join('BRUTOS', 'A7III', 'C0001.MP4'), os.path.join('BRUTOS', '00 AUDIO', 'ZOOM0001.WAV'),
                          os.path.join('BRUTOS', '03 FOTOS', 'DSC0001.JPG')])


def test_music_goes_to_the_profile_folder(tmp_path, mine):
    song = tmp_path / 'song.wav'
    song.write_bytes(b'RIFF')
    mine({'pastas': {'trilhas': 'Música/Trilhas'}})
    r = music.fetch(str(song), str(tmp_path / 'job'), 'trilha')
    assert os.path.isfile(tmp_path / 'job' / 'Música' / 'Trilhas' / 'trilha.wav'), r


def test_clients_folder_from_the_profile(tmp_path, mine, monkeypatch):
    monkeypatch.delenv('CLAUDIO_CLIENTS', raising=False)
    monkeypatch.delenv('DECUPA_CLIENTS', raising=False)
    mine({'clientes': str(tmp_path / 'clientes')})
    assert clients.clients_dir() == str(tmp_path / 'clientes')
    vault = tmp_path / 'vault'
    (vault / 'trabalho').mkdir(parents=True)
    (vault / 'CLAUDE.md').write_text('regras', encoding='utf-8')
    mine({'vault': str(vault)})
    assert clients.clients_dir() == str(vault / 'trabalho' / 'clientes')


def test_nle_rules_follow_the_profile(mine):
    from claudio import targets
    both = ' '.join(targets.rules(targets.active()))                     # no profile: both NLEs + ask once
    assert 'crashes Premiere' in both and 'import_timeline_from_file' in both and 'ask which one' in both
    mine({'nle': 'resolve'})
    only = ' '.join(targets.rules(targets.active()))
    assert targets.active() == 'resolve' and 'import_timeline_from_file' in only and 'Premiere' not in only
    mine({'nle': 'Premiere'})
    only = ' '.join(targets.rules(targets.active()))
    assert 'crashes Premiere' in only and 'Resolve' not in only
    mine({'nle': 'avid'})                                                # unknown NLE: as if not set
    assert targets.active() is None
