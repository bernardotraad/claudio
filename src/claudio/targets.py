"""claudio.targets — what each destination needs to know, in one place. The engine writes the same FCP7 XML for both
NLEs; how it gets in, how the current timeline comes out and what must never be done differ per NLE.

The profile's "nle" ("premiere" · "resolve") picks the rules the server sends; without it, both go and the model asks
which NLE once.
"""
from claudio import profile

RULES = {
    'premiere': [
        'The timeline AS IT IS: export the sequence first (Premiere MCP export_as_fcp_xml); the Cláudio panel already '
        'puts that XML in the message.',
        'Write the full path of every .xml / .mov / .jsx you produce: the Cláudio panel shows its import / apply button. '
        'NEVER import an .xml through the Premiere MCP (importFiles on an FCP XML crashes Premiere) — the editor imports '
        'it (Ctrl+I).',
        'Overlays and music go in by overwrite on the new track (the panel\'s buttons, or overwrite_clip); never '
        'add_to_timeline / insert, which ripples the whole sequence right.',
    ],
    'resolve': [
        'The timeline AS IT IS: export it first (Resolve MCP export_timeline, format FCP_7_XML).',
        'Import a timeline .xml with import_timeline_from_file; place clips with nle_cut_list + the insert tools.',
        'Overlays (.mov with alpha) and music: import to the media pool and put them on a new track at the right '
        'timecode; never on a track that has clips.',
    ],
}

NAMES = {'premiere': 'Premiere Pro', 'resolve': 'DaVinci Resolve'}


def active():
    """The profile's NLE, or None when the editor hasn't said."""
    n = (profile.get('nle') or '').strip().lower()
    return n if n in RULES else None


def rules(nle=None):
    """The instruction lines for one NLE, or for every NLE (each line prefixed with its name) when nle is unknown."""
    nle = nle if nle in RULES else None
    if nle:
        return [f'{NAMES[nle]} (this editor\'s NLE): ' + RULES[nle][0]] + RULES[nle][1:]
    return [f'{NAMES[n]}: {r}' for n in RULES for r in RULES[n]] + \
        ['The editor\'s NLE is not in the profile: ask which one once ("nle" in perfil.json saves it).']
