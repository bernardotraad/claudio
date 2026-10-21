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
                    var it = c[j], m = '', mc = false, src = '', en = true;
                    try { m = it.projectItem ? it.projectItem.getMediaPath() : ''; } catch (e1) {}
                    // a multicam clip has no media path: say so, and name its source sequence (cameras + music live there)
                    try { mc = !!(it.projectItem && it.projectItem.isMulticamClip && it.projectItem.isMulticamClip()); } catch (e2) {}
                    try { if (!m && it.projectItem) src = it.projectItem.name; } catch (e3) {}
                    try { en = !(it.disabled === true); } catch (e4) {}
                    cl.push('{"name":' + q(it.name) + ',"start":' + it.start.seconds + ',"end":' + it.end.seconds +
                            ',"in":' + it.inPoint.seconds + ',"out":' + it.outPoint.seconds + ',"media":' + q(m) +
                            (mc ? ',"multicam":true' : '') + (src ? ',"source":' + q(src) : '') + (en ? '' : ',"enabled":false') + '}');
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

// ── caption MOGRTs: one clip per block on a NEW top video track, Texto/Tempos written (no AI) ──────────────────────
var CLAUDIO_TPS = 254016000000;
function claudioMgtParam(comp, name) {
    for (var i = 0; i < comp.properties.numItems; i++) {
        if (comp.properties[i].displayName === name) return comp.properties[i];
    }
    return null;
}
function claudioMgtWrite(prop, text) {
    var before = prop.getValue(), next = text;
    // a text control holds JSON with the visible string in "textEditValue": change only that
    if (typeof before === 'string' && /"textEditValue"\s*:\s*"/.test(before)) {
        var enc = '"' + String(text).replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';
        next = before.replace(/"textEditValue"\s*:\s*"(?:[^"\\]|\\.)*"/, function () { return '"textEditValue":' + enc; });
        // the style run must cover the new text: with the template's old length Premiere drops the text (nothing shows)
        next = next.replace(/"fontTextRunLength"\s*:\s*\[[^\]]*\]/, '"fontTextRunLength":[' + String(text).length + ']')
                   .replace(/"capPropTextRunCount"\s*:\s*\d+/, '"capPropTextRunCount":1');
        var keep1 = function (key) {                                  // one run → one value per style array
            next = next.replace(new RegExp('"' + key + '"\\s*:\\s*\\[([^\\],]*)(,[^\\]]*)?\\]'), '"' + key + '":[$1]');
        };
        keep1('fontEditValue'); keep1('fontFSAllCapsValue'); keep1('fontFSBoldValue'); keep1('fontFSItalicValue');
        keep1('fontFSSmallCapsValue'); keep1('fontSizeEditValue');
    }
    prop.setValue(next, true);
}
function claudioPlaceMogrts(planPath) {
    try {
        var f = new File(planPath);
        if (!f.exists) return '{"ok":false,"error":' + q('plano não encontrado: ' + planPath) + '}';
        $.evalFile(f);
        var P = CLAUDIO_PLAN;
        var seq = app.project.activeSequence;
        if (!seq) return '{"ok":false,"error":"abra a sequência na timeline"}';
        if (!new File(P.mogrt).exists) return '{"ok":false,"error":' + q('MOGRT não encontrado: ' + P.mogrt) + '}';
        // a new empty video track on top: captions never go onto a track that already has something
        var n0 = seq.videoTracks.numTracks;
        app.enableQE();
        qe.project.getActiveSequence().addTracks(1, n0, 0);
        var vi = seq.videoTracks.numTracks - 1;
        if (vi < n0) return '{"ok":false,"error":"não consegui criar a trilha de vídeo nova"}';
        try { seq.videoTracks[vi].name = 'Legendas ' + P.style; } catch (eN) {}
        var placed = 0, errs = [];
        for (var i = 0; i < P.blocks.length; i++) {
            var b = P.blocks[i];
            try {
                var clip = seq.importMGT(P.mogrt, String(Math.round(b.start * CLAUDIO_TPS)), vi, 0);
                if (!clip) { errs.push((i + 1) + ': importMGT falhou'); continue; }
                var end = new Time(); end.ticks = String(Math.round(b.end * CLAUDIO_TPS));
                try { clip.end = end; } catch (eE) { errs.push((i + 1) + ': duração ' + eE); }
                var mgt = clip.getMGTComponent();
                var pt = mgt ? claudioMgtParam(mgt, P.texto) : null, pm = mgt ? claudioMgtParam(mgt, P.tempos) : null;
                if (!pt || !pm) { errs.push((i + 1) + ': controles Texto/Tempos não encontrados'); continue; }
                claudioMgtWrite(pt, b.text);
                claudioMgtWrite(pm, b.tempos);
                try { clip.name = b.text; } catch (eNm) {}
                placed++;
            } catch (eB) { errs.push((i + 1) + ': ' + eB); }
        }
        return '{"ok":' + (placed > 0 ? 'true' : 'false') + ',"placed":' + placed + ',"blocks":' + P.blocks.length +
               ',"track":"V' + (vi + 1) + '","errors":[' + (function () { var o = []; for (var k = 0; k < errs.length && k < 20; k++) o.push(q(errs[k])); return o.join(','); })() + ']}';
    } catch (e) {
        return '{"ok":false,"error":' + q(e.toString()) + '}';
    }
}


// id of the sequence called `name` (a multicam clip's source sequence), '' when there is none
function claudioSeqIdByName(name) {
    try {
        var p = app.project;
        for (var i = 0; i < p.sequences.numSequences; i++) if (p.sequences[i].name == name) return String(p.sequences[i].sequenceID);
    } catch (e) {}
    return '';
}
