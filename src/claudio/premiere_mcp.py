"""claudio.premiere_mcp — keeps the "premiere-pro" registration in ~/.claude.json lean and shared.

Shared: every Claude Code session used to start its own premiere-pro-mcp, and the first one up held the UXP port
(7777) — the panel's bridge and later sessions ran without UXP. With the panel installed, the registration runs
premiere_proxy.js (node premiere_proxy.js -- <real command>), which relays to the panel's one bridge (7788) and starts
it when it is down: sessions and panel share one premiere-pro-mcp. {"premiere_mcp_shared": false} goes back.

premiere-pro-mcp lists ~445 tools and repeats the same guard fields in every schema (~1.7k tokens per tool). Its own
switches cut that: PREMIERE_MCP_SCHEMA_PROFILE=compact halves each schema, PREMIERE_MCP_TOOL_PACKS=essential +
PREMIERE_MCP_TOOL_LIST list only what an edit uses. The list below is what Claude actually called in Cláudio's
sessions (~1600 calls, every tool used twice or more), plus import_fcp_xml and add_to_timeline.

sync() runs when the server starts (like panel.sync, so `uv tool upgrade` brings a new list) and in claudio-doctor.
SCHEMA_PROFILE / TOOL_PACKS already set by the editor are kept; TOOL_LIST is Cláudio's (extra names:
{"premiere_mcp_extra_tools": "a,b"} in ~/.config/claudio/settings.json). {"premiere_mcp_lean": false} turns it off.
An unknown TOOL_LIST name stops premiere-pro-mcp from starting, so names the installed package lacks are dropped.
"""
import glob, hashlib, json, os, re

from claudio import core

TOOLS = sorted('''
    export_frame overwrite_clip set_active_sequence get_active_sequence trim_clip remove_from_timeline set_clip_selection
    add_keyframe set_item_in_out move_clip set_clip_scale set_clip_properties_batch get_full_sequence_info
    list_project_items move_items_to_bin import_media get_track_info set_clip_duration deselect_all_clips
    list_sequence_tracks inspect_operation_recovery save_project list_clip_effects add_track acknowledge_operation_recovery
    link_selection inspect_media_streams slip_edit create_bin create_subclip get_clip_at_position get_clip_volume
    inspect_caption_tracks_uxp add_audio_keyframes create_caption_track get_sequence_structure set_sequence_resolution
    set_playhead_position get_effect_properties list_sequences add_to_timeline set_effect_property get_timeline_summary
    duplicate_clip apply_effect manage_track_state_uxp get_project_info export_as_fcp_xml verify_premiere_connection
    unlink_selection get_sequence_settings get_keyframes split_clip replace_clip get_linked_items get_bin_contents
    copy_effects_between_clips undo set_clips_volume find_project_item_by_name duplicate_sequence clear_item_in_out
    check_offline_media set_track_states set_source_in_out set_clip_pan select_all_clips rename_project_item
    remove_keyframe_range open_in_source import_fcp_xml get_full_clip_info get_clip_properties delete_bin
    set_clip_properties set_clip_opacity set_clip_position move_item_to_bin remove_effect set_sequence_in_out_points
    create_sequence create_sequence_from_clips read_sequence_captions setup_ducking mute_track add_transition
    remove_transition ripple_delete ripple_remove_timeline_ranges relink_media add_adjustment_layer
'''.split())

PROFILE, PACKS, LIST = 'PREMIERE_MCP_SCHEMA_PROFILE', 'PREMIERE_MCP_TOOL_PACKS', 'PREMIERE_MCP_TOOL_LIST'
_TAG = hashlib.sha1(' '.join(TOOLS).encode()).hexdigest()[:12]
CACHE = os.path.expanduser('~/.cache/claudio/premiere_mcp_tools.json')


def configs():
    """The ~/.claude.json files whose Claude Code talks to Premiere (in WSL: the Windows side, next to Premiere)."""
    if core.IS_WSL:
        return sorted(glob.glob('/mnt/c/Users/*/.claude.json'))
    return [os.path.expanduser('~/.claude.json')]


def package_dir(entry, cfg):
    """premiere-pro-mcp's install folder for this registration, or None: the dist/index.js in its args, else npm's
    global folder next to that ~/.claude.json (Windows: AppData/Roaming/npm; elsewhere: beside the bin on PATH)."""
    for a in [entry.get('command', '')] + list(entry.get('args') or []):
        m = re.match(r'(.*[\\/]premiere-pro-mcp)[\\/]dist[\\/]index\.js$', str(a))
        if m:
            d = core.local_path(m.group(1)) if core.IS_WSL else m.group(1)
            if os.path.isdir(d):
                return d
    home = os.path.dirname(cfg)
    cands = [os.path.join(home, 'AppData', 'Roaming', 'npm', 'node_modules', 'premiere-pro-mcp')]
    import shutil
    b = shutil.which('premiere-pro-mcp')
    if b:
        cands.append(os.path.join(os.path.dirname(os.path.realpath(b)), '..'))   # …/premiere-pro-mcp/dist/index.js's bin
        cands.append(os.path.join(os.path.dirname(b), '..', 'lib', 'node_modules', 'premiere-pro-mcp'))
    for d in cands:
        if os.path.isfile(os.path.join(d, 'package.json')):
            return os.path.normpath(d)
    return None


def known(pkg):
    """(version, set of TOOLS the package defines) — a name counts when it appears as `name:` / "name" in dist.
    Cached per package version: dist is ~8 MB, slow to read through /mnt/c."""
    try:
        ver = json.load(open(os.path.join(pkg, 'package.json'), encoding='utf-8'))['version']
    except (OSError, ValueError, KeyError):
        return None, None
    try:
        c = json.load(open(CACHE))
        if c.get('version') == ver and c.get('tools_hash') == _TAG:
            return ver, set(c['found'])
    except (OSError, ValueError):
        pass
    alt = '|'.join(TOOLS)
    pat = re.compile(r'(?:\b(%s)\s*:|[\x22\x27](%s)[\x22\x27])' % (alt, alt))
    found = set()
    for f in glob.glob(os.path.join(pkg, 'dist', '**', '*.js'), recursive=True):
        try:
            txt = open(f, encoding='utf-8', errors='ignore').read()
        except OSError:
            continue
        found.update(a or b for a, b in pat.findall(txt))
        if len(found) == len(TOOLS):
            break
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump({'version': ver, 'tools_hash': _TAG, 'found': sorted(found)}, open(CACHE, 'w'))
    except OSError:
        pass
    return ver, found


def wanted(env, found=None):
    """The env this registration should have (a copy; the editor's own profile/packs win)."""
    out = dict(env or {})
    out.setdefault(PROFILE, 'compact')
    out.setdefault(PACKS, 'essential')
    extra = [x.strip() for x in str(core.env('PREMIERE_MCP_EXTRA_TOOLS') or '').split(',') if x.strip()]
    names = [n for n in TOOLS if found is None or n in found] + [x for x in extra if x not in TOOLS]
    out[LIST] = ','.join(names)
    return out


def enabled():
    return str(core.env('PREMIERE_MCP_LEAN', 'true')).strip().lower() not in ('0', 'false', 'no', 'off')


def shared_enabled():
    return str(core.env('PREMIERE_MCP_SHARED', 'true')).strip().lower() not in ('0', 'false', 'no', 'off')


def proxy_path():
    """premiere_proxy.js in the installed panel, as the NLE side sees it (Windows path from WSL), or None."""
    try:
        from claudio import panel
        p = os.path.join(panel._ext_dir(), panel.ID, 'premiere_proxy.js')
        return core.nle_path(p) if os.path.isfile(p) else None
    except Exception:
        return None


def _is_proxy(entry):
    return any(re.search(r'[\\/]premiere_proxy\.js$', str(a)) for a in entry.get('args') or [])


def real_command(entry):
    """(command, args) of the real premiere-pro-mcp, whether or not the entry goes through the proxy."""
    a = list(entry.get('args') or [])
    if _is_proxy(entry) and '--' in a:
        k = a.index('--')
        return a[k + 1], a[k + 2:]
    return entry.get('command'), a


def shaped(entry, proxy=None, lean=True, found=None):
    """The registration as it should be: through the proxy when one is given (Claude Code sessions and the panel then
    share ONE premiere-pro-mcp, so the UXP port stops going to whichever session opened first), lean env."""
    e = dict(entry)
    cmd, args = real_command(entry)
    if proxy:
        is_node = re.search(r'(^|[\\/])node(\.exe)?$', str(cmd), re.I)
        node = cmd if is_node else (entry.get('command') if _is_proxy(entry) else 'node')
        e['command'], e['args'] = node, [proxy, '--', cmd, *args]
    else:
        e['command'], e['args'] = cmd, args
    if lean:
        e['env'] = wanted(entry.get('env'), found)
    return e


def sync():
    """Bring every premiere-pro registration in step. Never raises.
    Returns [{config, version, dropped, changed, shared}]."""
    out = []
    lean, proxy = enabled(), (proxy_path() if shared_enabled() else None)
    for cfg in configs():
        try:
            with open(cfg, encoding='utf-8') as f:
                data = json.load(f)
            entry = (data.get('mcpServers') or {}).get('premiere-pro')
            if not isinstance(entry, dict):
                continue
            pkg = package_dir(entry, cfg) if lean else None
            ver, found = known(pkg) if pkg else (None, None)
            new = shaped(entry, proxy, lean, found)
            row = {'config': cfg, 'version': ver, 'dropped': sorted(set(TOOLS) - found) if found is not None else [],
                   'changed': new != entry, 'shared': bool(proxy), 'lean': lean}
            if row['changed']:
                # re-read right before writing: Claude Code rewrites this file often; keep the window tiny
                with open(cfg, encoding='utf-8') as f:
                    data = json.load(f)
                data['mcpServers']['premiere-pro'] = shaped(data['mcpServers']['premiere-pro'], proxy, lean, found)
                tmp = cfg + '.claudio-tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp, cfg)
            out.append(row)
        except Exception as e:
            out.append({'config': cfg, 'error': str(e)[:160]})
    return out
