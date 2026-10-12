// decupa panel — ExtendScript side: what is open in Premiere right now (read-only).
function q(s) { return '"' + String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\r|\n/g, ' ') + '"'; }

function decupaContext() {
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
