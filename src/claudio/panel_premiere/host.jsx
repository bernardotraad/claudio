// Cláudio, o Editor — panel, ExtendScript side: what is open in Premiere right now (read-only), plus the one-click XML import.
function q(s) { return '"' + String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\r|\n/g, ' ') + '"'; }

function claudioContext() {
    var out = [];
    try {
        var p = app.project;
        out.push('"project":' + q(p ? p.path : ''));
        var s = p ? p.activeSequence : null;
        if (s) {
            out.push('"sequence":' + q(s.name));
            out.push('"playhead_s":' + s.getPlayerPosition().seconds);
            out.push('"frame_size":' + q(s.frameSizeHorizontal + 'x' + s.frameSizeVertical));
            var sel = [];
            try {
                var items = s.getSelection();
                for (var i = 0; i < items.length && i < 20; i++) {
                    var it = items[i];
                    var media = '';
                    try { media = it.projectItem ? it.projectItem.getMediaPath() : ''; } catch (e1) {}
                    sel.push('{"name":' + q(it.name) + ',"track_type":' + q(it.mediaType) + ',"start_s":' + it.start.seconds +
                             ',"end_s":' + it.end.seconds + ',"in_s":' + it.inPoint.seconds + ',"out_s":' + it.outPoint.seconds +
                             ',"media":' + q(media) + '}');
                }
            } catch (e2) {}
            out.push('"selection":[' + sel.join(',') + ']');
        }
    } catch (e) {
        out.push('"error":' + q(e.toString()));
    }
    return '{' + out.join(',') + '}';
}

// Import ONE FCP XML into the open project. The project is saved first, and importFiles runs with the UI on
// (suppressUI=false): the Premiere MCP's import_media (several XMLs at once, UI suppressed) crashed Premiere.
function claudioImportXml(xmlPath) {
    try {
        var f = new File(xmlPath);
        if (!f.exists) return '{"ok":false,"error":' + q('arquivo não encontrado: ' + xmlPath) + '}';
        var p = app.project;
        if (!p) return '{"ok":false,"error":"nenhum projeto aberto"}';
        var before = p.rootItem.children.numItems;
        try { p.save(); } catch (e0) {}
        var ok = p.importFiles([f.fsName], false, p.getInsertionBin(), false);
        return '{"ok":' + (ok ? 'true' : 'false') + ',"new_items":' + (p.rootItem.children.numItems - before) + '}';
    } catch (e) {
        return '{"ok":false,"error":' + q(e.toString()) + '}';
    }
}

// ── safety net: what is on the tracks before / after Claude acts ──────────────────────────────────────────────────
function claudioSeqById(id) {
    var p = app.project;
    if (!p) return null;
    if (!id) return p.activeSequence;
    for (var i = 0; i < p.sequences.numSequences; i++) {
        if (p.sequences[i].sequenceID == id) return p.sequences[i];
    }
    return null;
}

// Every clip on every track of a sequence (the active one when id is empty), as JSON.
function claudioSnapshot(id) {
    try {
        var s = claudioSeqById(id);
        if (!s) return '{"ok":false}';
        var tr = [];
        var kinds = [['V', s.videoTracks], ['A', s.audioTracks]];
        for (var k = 0; k < 2; k++) {
            var ts = kinds[k][1];
            for (var t = 0; t < ts.numTracks; t++) {
                var cl = [], c = ts[t].clips;
                for (var j = 0; j < c.numItems; j++) {
                    var it = c[j], m = '';
                    try { m = it.projectItem ? it.projectItem.getMediaPath() : ''; } catch (e1) {}
                    cl.push('{"name":' + q(it.name) + ',"start":' + it.start.seconds + ',"end":' + it.end.seconds +
                            ',"in":' + it.inPoint.seconds + ',"out":' + it.outPoint.seconds + ',"media":' + q(m) + '}');
                }
                tr.push('{"track":"' + kinds[k][0] + (t + 1) + '","clips":[' + cl.join(',') + ']}');
            }
        }
        return '{"ok":true,"id":' + q(s.sequenceID) + ',"name":' + q(s.name) + ',"tracks":[' + tr.join(',') + ']}';
    } catch (e) {
        return '{"ok":false,"error":' + q(e.toString()) + '}';
    }
}

// FCP XML backup of a sequence, so a version from before Claude acted can always be imported back.
function claudioBackup(id, path) {
    try {
        var s = claudioSeqById(id);
        if (!s) return '{"ok":false}';
        var ok = s.exportAsFinalCutProXML(path, 1);
        return '{"ok":' + (ok ? 'true' : 'false') + '}';
    } catch (e) {
        return '{"ok":false,"error":' + q(e.toString()) + '}';
    }
}
