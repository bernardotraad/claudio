"""Recipes: one Markdown file per kind of project. Every built-in one parses, names only tools that exist, and comes
with the common rules; the editor's own recipes add kinds or replace a built-in one."""
import asyncio, re

import pytest

from claudio import recipes

BUILTIN = {'depoimento', 'depoimento-horizontal', 'recap-evento', 'beat-edit', 'show-multicam', 'curta-narrativo'}

# snake_case words in the recipes that are not claudio tools: edit JSON keys, arguments, other MCPs' tools
NOT_TOOLS = {'pace_follows_music', 'cut_on', 'reframe_x', 'end_card', 'correct_drift', 'authorized_by_user',
             'import_media', 'import_timeline_from_file', 'search_audio', 'get_download_url', 'synced_sequence',
             'legendas_srt', 'entregar_no_nle'}


@pytest.fixture(scope='module')
def tools():
    from claudio import server
    return {t.name for t in asyncio.run(server.mcp.list_tools())}


@pytest.fixture
def editor_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAUDIO_RECIPES', str(tmp_path))
    return tmp_path


def test_builtin_recipes_parse(monkeypatch, tmp_path):
    monkeypatch.setenv('CLAUDIO_RECIPES', str(tmp_path / 'none'))
    got = {r['nome']: r for r in recipes.listing()}
    assert set(got) == BUILTIN
    for name, r in got.items():
        assert r['titulo'] and r['quando'] and isinstance(r['padroes'], dict) and r['origem'] == 'builtin'
        meta = recipes.load(name)['meta']
        assert meta['nome'] == name and isinstance(meta['ferramentas'], list) and isinstance(meta['entregas'], list)


def test_recipes_name_only_real_tools(tools):
    assert 'recipe' in tools
    for p, _ in recipes._files().values():
        meta, body = recipes._read(p)
        for t in meta.get('ferramentas', []):
            assert t in tools, f'{p}: ferramenta {t} não existe'
        for w in set(re.findall(r'\b[a-z]+(?:_[a-z]+)+\b', body)) - NOT_TOOLS:
            assert w in tools, f'{p}: "{w}" parece ferramenta e não existe'


def test_load_brings_the_common_rules():
    r = recipes.load('Depoimento')
    assert r['text'].startswith('# Receita: Depoimento') and '# Regras comuns' in r['text']
    assert 'nunca aninhe sequências' in r['text']
    assert '# Regras comuns' not in recipes.load('comum')['text']          # the common rules alone, once


def test_names_are_forgiving():
    assert recipes.load('recap evento')['nome'] == 'recap-evento'
    assert recipes.load('Aftermovie')['nome'] == 'recap-evento'             # by the start of the title
    with pytest.raises(KeyError, match='depoimento'):                      # the error lists what exists
        recipes.load('casamento-cinematico')


def test_one_section(editor_dir):
    assert recipes.load('recap-evento', 'etapas')['text'].startswith('## Etapas')
    assert recipes.load('recap-evento', 'entregar no NLE')['text'].startswith('## Entregar no NLE')   # from the common rules
    with pytest.raises(KeyError, match='etapas'):
        recipes.load('depoimento', 'figurino')


def test_editor_recipes_add_and_replace(editor_dir):
    (editor_dir / 'podcast.md').write_text('---\nnome: podcast\ntitulo: Podcast em vídeo\nquando: conversa gravada\n'
                                           'padroes: {"aspect": "16:9"}\n---\n## Etapas\n1. auto_cut_multicam\n',
                                           encoding='utf-8')
    (editor_dir / 'depoimento.md').write_text('---\nnome: depoimento\ntitulo: Depoimento do meu jeito\n---\n## Etapas\nx\n',
                                              encoding='utf-8')
    got = {r['nome']: r for r in recipes.listing()}
    assert got['podcast']['padroes'] == {'aspect': '16:9'} and got['podcast']['origem'] == 'editor'
    assert got['depoimento']['titulo'] == 'Depoimento do meu jeito' and got['depoimento']['origem'] == 'editor'
    assert '# Regras comuns' in recipes.load('podcast')['text']


def test_frontmatter_values():
    meta, body = recipes.parse('---\na: texto: com dois pontos\nb: [1, 2]\nc: {quebrado\n---\n# corpo\n')
    assert meta == {'a': 'texto: com dois pontos', 'b': [1, 2], 'c': '{quebrado'} and body == '# corpo\n'
    assert recipes.parse('sem frontmatter') == ({}, 'sem frontmatter\n')


def test_server_recipe_tool_and_montar():
    from claudio import server
    lst = server.recipe()
    assert {r['nome'] for r in lst['receitas']} >= BUILTIN
    r = server.recipe('show multicam')
    assert r['nome'] == 'show-multicam' and 'sync_multicam' in r['text'] and r['padroes']['aspect'] == '16:9'
    assert 'error' in server.recipe('nao-existe')
    assert 'recipe(' in server.MONTAR.format(pasta='x', pedido='y')
