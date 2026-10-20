// Cláudio, o Editor — After Effects side of the caption MOGRTs (ExtendScript, ES3).
// Prepended by mogrt.py: CLAUDIO_JOBS = [{name, spec, ex, box, chips}], CLAUDIO_OUT (folder), CLAUDIO_RESULT (json path).
// Builds one comp per job in a scratch project, exports <name>.mogrt to CLAUDIO_OUT, writes the result and quits.
(function () {
    function q(s) { return '"' + String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\r|\n/g, ' ') + '"'; }
    var results = [];
    function done(extra) {
        var f = new File(CLAUDIO_RESULT + '.part');
        f.encoding = 'UTF-8'; f.open('w');
        var parts = [];
        for (var i = 0; i < results.length; i++) {
            var r = results[i];
            parts.push('{"name":' + q(r.name) + ',"ok":' + (r.ok ? 'true' : 'false') + ',"path":' + q(r.path || '') +
                       ',"error":' + q(r.error || '') + ',"controls":' + q(r.controls || '') + '}');
        }
        f.write('{"templates":[' + parts.join(',') + ']' + (extra ? ',' + extra : '') + '}');
        f.close();
        f.rename(File(CLAUDIO_RESULT).name);
    }

    function textLayer(comp, name, spec, justify) {
        var l = comp.layers.addText('Sua legenda aqui');
        l.name = name;
        var sp = l.property('ADBE Text Properties').property('ADBE Text Document');
        var d = sp.value;
        d.resetCharStyle();
        d.resetParagraphStyle();
        d.font = spec.font;
        d.fontSize = spec.size;
        d.applyFill = true;
        d.fillColor = spec.color;
        if (spec.stroke > 0) {
            d.applyStroke = true; d.strokeColor = spec.stroke_color || [0, 0, 0]; d.strokeWidth = spec.stroke; d.strokeOverFill = false;
        } else { d.applyStroke = false; }
        try { d.fontCapsOption = spec.caps ? FontCapsOption.FONT_ALL_CAPS : FontCapsOption.FONT_NORMAL_CAPS; }
        catch (eC) { try { d.allCaps = !!spec.caps; } catch (eC2) {} }
        d.justification = justify;
        try { d.leading = spec.size * (spec.line_height || 1.1); d.autoLeading = false; } catch (e0) {}
        sp.setValue(d);
        return l;
    }

    function hidden(l) {
        l.property('ADBE Transform Group').property('ADBE Opacity').setValue(0);
        l.shy = true;
    }

    function animator(l, name, base, amountExpr, props) {
        var an = l.property('ADBE Text Properties').property('ADBE Text Animators').addProperty('ADBE Text Animator');
        an.name = name;
        var sel = an.property('ADBE Text Selectors').addProperty('ADBE Text Expressible Selector');
        sel.property('ADBE Text Range Type2').setValue(3);           // based on: words
        sel.property('ADBE Text Expressible Amount').expression = amountExpr;
        var ps = an.property('ADBE Text Animator Properties');
        for (var i = 0; i < props.length; i++) {
            var p = ps.addProperty(props[i][0]);
            p.setValue(props[i][1]);
        }
        return an;
    }

    function rectShape(comp, name, parent, color, alpha, radius, sizeExpr, posExpr) {
        var s = comp.layers.addShape();
        s.name = name;
        s.parent = parent;
        // parenting makes AE compensate the child for the parent's CURRENT scale (the entry animation is at ~88%
        // at frame 0): reset the whole transform so the box lives 1:1 in LEGENDA's own space
        var st = s.property('ADBE Transform Group');
        st.property('ADBE Anchor Point').setValue([0, 0]);
        st.property('ADBE Position').setValue([0, 0]);
        st.property('ADBE Scale').setValue([100, 100]);
        st.property('ADBE Rotate Z').setValue(0);
        var g = s.property('ADBE Root Vectors Group').addProperty('ADBE Vector Group');
        g.name = name;
        var v = g.property('ADBE Vectors Group');
        var r = v.addProperty('ADBE Vector Shape - Rect');
        r.property('ADBE Vector Rect Size').expression = sizeExpr;
        r.property('ADBE Vector Rect Roundness').setValue(radius);
        var fill = v.addProperty('ADBE Vector Graphic - Fill');
        fill.property('ADBE Vector Fill Color').setValue(color);
        fill.property('ADBE Vector Fill Opacity').setValue(alpha * 100);
        // the group's transform carries the position (so a chip can bounce around its own centre)
        var tr = g.property('ADBE Vector Transform Group');
        tr.property('ADBE Vector Position').expression = posExpr;
        s.property('ADBE Transform Group').property('ADBE Opacity').expression = 'thisComp.layer("LEGENDA").transform.opacity';
        return { layer: s, group: g, fill: fill };
    }

    function exposeAs(prop, comp, label) {
        try {
            if (prop.canAddToMotionGraphicsTemplate && !prop.canAddToMotionGraphicsTemplate(comp)) return false;
            return prop.addToMotionGraphicsTemplateAs(comp, label) === true;
        } catch (e) { return false; }
    }

    function build(job) {
        var sp = job.spec, ex = job.ex;
        var comp = app.project.items.addComp(job.name, sp.W, sp.H, 1, 30, 30);
        comp.motionGraphicsTemplateName = job.name;
        // NO protected region: measured in Premiere 26.5, a protected comp is squeezed into the clip (a 1.1 s block
        // ran 9× fast and was gone after its first frame); without one, comp time runs 1:1 with the clip
        var C = ParagraphJustification.CENTER_JUSTIFY, Lj = ParagraphJustification.LEFT_JUSTIFY;

        var tempos = textLayer(comp, 'TEMPOS', sp, Lj); hidden(tempos);
        tempos.property('ADBE Text Properties').property('ADBE Text Document').setValue('0,0.35,0.7,1.05||2');
        var texto = textLayer(comp, 'TEXTO', sp, C); hidden(texto);
        var medida = textLayer(comp, 'MEDIDA', sp, C); hidden(medida);
        medida.property('ADBE Text Properties').property('ADBE Text Document').expression = ex.medida_text;
        var vis = null;
        if (sp.box && sp.box.hug === 'visible') {
            vis = textLayer(comp, 'VISIVEL', sp, Lj); hidden(vis);
            vis.property('ADBE Text Properties').property('ADBE Text Document').expression = ex.visivel_text;
        }
        var chipsMeasure = [];
        if (sp.chips) {
            for (var k = 1; k <= sp.chips.n; k++) {
                var inc = textLayer(comp, 'INC' + k, sp, Lj); hidden(inc);
                inc.property('ADBE Text Properties').property('ADBE Text Document').expression = job.chips[k - 1].inc;
                var pal = textLayer(comp, 'PAL' + k, sp, Lj); hidden(pal);
                pal.property('ADBE Text Properties').property('ADBE Text Document').expression = job.chips[k - 1].word;
                chipsMeasure.push([inc, pal]);
            }
        }

        var leg = textLayer(comp, 'LEGENDA', sp, C);
        leg.property('ADBE Text Properties').property('ADBE Text Document').expression = ex.legenda_text;
        var T = leg.property('ADBE Transform Group');
        T.property('ADBE Anchor Point').expression = ex.anchor;
        T.property('ADBE Position').expression = ex.position;
        T.property('ADBE Scale').expression = ex.scale;
        T.property('ADBE Opacity').expression = ex.opacity;
        if (sp.shadow) {
            var dsh = leg.property('ADBE Effect Parade').addProperty('ADBE Drop Shadow');
            dsh.property('ADBE Drop Shadow-0002').setValue(sp.shadow.opacity * 2.55);    // opacity 0–255
            dsh.property('ADBE Drop Shadow-0003').setValue(180);                         // straight down
            dsh.property('ADBE Drop Shadow-0004').setValue(sp.shadow.distance);
            dsh.property('ADBE Drop Shadow-0005').setValue(sp.shadow.softness);
        }
        var accentProp = null;
        if (ex.sel_appear) {
            var props = [['ADBE Text Opacity', 0]];
            if (sp.appear === 'rise') { props.push(['ADBE Text Position 3D', [0, 14, 0]]); props.push(['ADBE Text Scale 3D', [90, 90, 100]]); }
            if (sp.appear === 'brand_dynamic') {
                props = [['ADBE Text Opacity', 30], ['ADBE Text Position 3D', [0, 12 * sp.W / 1080, 0]], ['ADBE Text Scale 3D', [85, 85, 100]]];
            }
            animator(leg, 'aparece', null, ex.sel_appear, props);
        }
        if (ex.sel_dim) animator(leg, 'apagada', null, ex.sel_dim, [['ADBE Text Opacity', sp.dim * 100]]);
        if (ex.sel_current) {
            var an = animator(leg, 'atual', null, ex.sel_current, [['ADBE Text Fill Color', sp.current]]);
            accentProp = an.property('ADBE Text Animator Properties').property('ADBE Text Fill Color');
        }
        if (ex.sel_current_pop) animator(leg, 'atual pop', null, ex.sel_current_pop, [['ADBE Text Scale 3D', [100 + sp.current_pop * 100, 100 + sp.current_pop * 100, 100]]]);
        if (ex.sel_em_color) {
            animator(leg, 'destaque cor', null, ex.sel_em_color, [['ADBE Text Fill Color', sp.chips.text]]);
            animator(leg, 'destaque pop', null, ex.sel_em_pop, [['ADBE Text Scale 3D', [70, 70, 100]]]);
        }

        var boxFill = null;
        if (sp.chips) {
            for (var c = sp.chips.n; c >= 1; c--) {
                var ce = job.chips[c - 1];
                var chip = rectShape(comp, 'CHIP' + c, leg, sp.chips.bg, 1, 0, ce.size, ce.pos);
                var tr = chip.group.property('ADBE Vector Transform Group');
                tr.property('ADBE Vector Scale').expression = ce.scale;
                tr.property('ADBE Vector Rotation').expression = ce.rot;
                chip.layer.moveAfter(leg);
            }
        }
        if (sp.box) {
            var bx = rectShape(comp, 'CAIXA', leg, sp.box.color, sp.box.alpha, sp.box.radius, job.box.size, job.box.pos);
            boxFill = bx.fill.property('ADBE Vector Fill Color');
            bx.layer.moveToEnd();
        }
        // hidden helpers at the very bottom, out of the way
        tempos.moveToEnd(); texto.moveToEnd(); medida.moveToEnd(); if (vis) vis.moveToEnd();
        for (var m = 0; m < chipsMeasure.length; m++) { chipsMeasure[m][0].moveToEnd(); chipsMeasure[m][1].moveToEnd(); }

        var ok1 = exposeAs(texto.property('ADBE Text Properties').property('ADBE Text Document'), comp, 'Texto');
        var ok2 = exposeAs(tempos.property('ADBE Text Properties').property('ADBE Text Document'), comp, 'Tempos');
        var extra = [];
        if (accentProp && exposeAs(accentProp, comp, 'Cor de destaque')) extra.push('Cor de destaque');
        if (boxFill && exposeAs(boxFill, comp, 'Cor da caixa')) extra.push('Cor da caixa');
        if (!ok1 || !ok2) throw new Error('não consegui expor Texto/Tempos no Essential Graphics');
        return { comp: comp, controls: ['Texto', 'Tempos'].concat(extra).join(', ') };
    }

    try {
        // whatever is open is closed WITHOUT saving first: otherwise AE stops on "save changes?"
        try { if (app.project) app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES); } catch (eP) {}
        app.beginSuppressDialogs();
        app.newProject();
        app.project.expressionEngine = 'javascript-1.0';
        var built = [];
        for (var i = 0; i < CLAUDIO_JOBS.length; i++) {
            var job = CLAUDIO_JOBS[i];
            try { var b = build(job); built.push([job, { id: b.comp.id, controls: b.controls }]); }
            catch (e1) { results.push({ name: job.name, ok: false, error: 'montagem: ' + e1.toString() + ' (linha ' + e1.line + ')' }); }
        }
        app.project.save(new File(CLAUDIO_OUT + '/_claudio_captions.aep'));
        // dialogs back on for the export (with dialogs suppressed AE cancels it). The warning about fonts that are
        // not from Adobe Fonts (Gilroy, Montserrat installed by hand) comes from "include Adobe Fonts" in the MOGRT
        // export: off while we export, back to what it was after — measured: no dialog, the .mogrt is written
        try { app.endSuppressDialogs(false); } catch (eS) {}
        var TK = ['Main Pref Section v2', 'Pref_MOGRT_EXPORT_TYPEKIT_FONTS'], tkBefore = null;
        try { tkBefore = app.preferences.getPrefAsBool(TK[0], TK[1]); app.preferences.savePrefAsBool(TK[0], TK[1], false); } catch (eT) {}
        if (typeof CLAUDIO_NO_EXPORT !== 'undefined' && CLAUDIO_NO_EXPORT) built = [];   // build-only (preview frames)
        for (var j = 0; j < built.length; j++) {
            var jb = built[j][0], name = jb.name, controls = built[j][1].controls;
            var target = new File(CLAUDIO_OUT + '/' + name + '.mogrt');
            var err = '';
            // look the comp up again: after an export AE invalidates the scripting objects it handed out
            // each export leaves the project "changed" and the next one would ask to save it: save first
            try { app.project.save(); } catch (eSv) {}
            // the path is the FOLDER: AE writes <comp name>.mogrt inside it
            try { app.project.itemByID(built[j][1].id).exportAsMotionGraphicsTemplate(true, new Folder(CLAUDIO_OUT).fsName); }
            catch (e2) { err = e2.toString(); }
            var made = target.exists ? target : new File(CLAUDIO_OUT + '/' + name + '.mogrt');
            results.push({ name: name, ok: made.exists, path: made.fsName, controls: controls,
                           error: made.exists ? '' : ('exportação: ' + (err || 'o AE não gravou o arquivo (fonte fora do Adobe Fonts? aviso bloqueado?)')) });
        }
        try { if (tkBefore !== null) app.preferences.savePrefAsBool(TK[0], TK[1], tkBefore); } catch (eT2) {}
        done('"ae":' + q(app.version));
    } catch (e) {
        results.push({ name: '*', ok: false, error: e.toString() + ' (linha ' + e.line + ')' });
        done('"ae":' + q(app.version));
    }
    try { app.endSuppressDialogs(false); } catch (e3) {}
    try { app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES); } catch (e4) {}
    app.quit();
})();
