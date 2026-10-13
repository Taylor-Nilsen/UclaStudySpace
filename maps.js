// Building floor maps + campus map for UCLA StudySpace.
// Nothing here runs until someone presses a "🗺️ Map" button on a room card.
// Data: maps/index.json and maps/<CODE>.json, built by maps/build_maps.py.
(function(){
    'use strict';
    const DIR = 'maps/';
    const LEAFLET = {
        js: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js',
        jsSri: 'sha512-BwHfrr4c9kmRkLw6iXFdzcdWV/PGkVgiIyIWLLlTSXzWQzxuSg4DiQUCpauz/EWjgk5TYQqX/kvn9pG1NpYfqg==',
        css: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.css',
        cssSri: 'sha512-Zcn6bjR/8RZbLEpLIeOwNtzREBAJnUKESxces60Mpoj+2okopSAcSUIUOseddDm0cxnGQzxIR7vJgsLZbdLE3w==',
    };
    const LEVEL_LETTERS = new Set(['A','B','C','M']);
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const ft = m => Math.round(m * 3.281);
    const distLabel = m => m < 1000 ? `${Math.round(m)} m (${ft(m)} ft)` : `${(m/1000).toFixed(2)} km (${(m/1609.34).toFixed(2)} mi)`;

    let index = null, buildingsGeo = null;
    const cache = {};
    const ui = {};          // modal state
    const loc = {fix:null, stop:null, err:'', on:false};

    // ---------- data ----------
    async function getJSON(url){
        if(cache[url]) return cache[url];
        const res = await fetch(url);
        if(!res.ok) throw new Error(`${url}: ${res.status}`);
        return (cache[url] = await res.json());
    }
    async function loadIndex(){
        if(!index) index = await getJSON(DIR + 'index.json');
        return index;
    }
    async function loadBuildingsGeo(){
        if(buildingsGeo) return buildingsGeo;
        try{ buildingsGeo = (await getJSON('buildings.json')).buildings || {}; }
        catch(e){ buildingsGeo = {}; }
        return buildingsGeo;
    }
    const normCode = s => String(s || '').toUpperCase().replace(/\s+/g, ' ').trim();
    const normName = s => String(s || '').toLowerCase().replace(/&/g, 'and').replace(/\b(the|hall|building|bldg|center)\b/g, ' ').replace(/[^a-z0-9]+/g, ' ').trim();
    function resolveCode(code, name){
        const c = normCode(code);
        if(index.buildings[c]) return c;
        if(index.aliases[c]) return index.aliases[c];
        const n = normName(name);
        if(n) for(const [k, b] of Object.entries(index.buildings)) if(normName(b.name) === n || normName(b.name).startsWith(n + ' ')) return k;
        return null;
    }

    // Floor from a UCLA room number. Mirrors floor_of() in maps/build_maps.py.
    function floorOf(room, rule){
        let r = String(room || '').toUpperCase().replace(/[\s-]+/g, '');
        const wings = ((rule && rule.wing_prefixes) || []).slice().sort((a, b) => b.length - a.length);
        for(const w of wings) if(r.startsWith(w) && r.length > w.length){ r = r.slice(w.length); break; }
        const m = /^([A-Z]*)0*(\d+)[A-Z]?$/.exec(r);
        if(!m) return null;
        const [, prefix, digits] = m;
        if(prefix){
            if(LEVEL_LETTERS.has(prefix)) return prefix;
            if(rule && rule.level_first_letter && LEVEL_LETTERS.has(prefix[0])) return prefix[0];
            return null;
        }
        return digits.length <= 2 ? '0' : digits[0];
    }
    const LEVEL_ORDER = {C:-3, B:-2, A:-1, '0':0, M:1.5};
    const levelKey = lv => lv in LEVEL_ORDER ? LEVEL_ORDER[lv] : (/^\d+$/.test(lv) ? +lv : 50);
    const levelName = lv => /^\d+$/.test(lv) ? (lv === '0' ? 'Level 0' : `Floor ${lv}`) : `${lv} level`;
    // registrar numbers are zero padded to 5 digits ("02444", "A00152"); library names aren't ("G05")
    const roomLabel = room => /^[A-Z]*\d{5}/i.test(String(room || '')) ? String(room).replace(/^([A-Z]*)0+(?=\d)/i, '$1') : String(room || '');

    // A site room -> a room traced from a plan ("G01", "YRL G01", "Group Study Room G1"...).
    const roomKey = s => String(s || '').toUpperCase().replace(/[^A-Z0-9]/g, '').replace(/^([A-Z]*)0+(?=\d)/, '$1');
    function findRoom(d, room, label){
        if(!d.rooms) return null;
        const keys = Object.keys(d.rooms);
        const want = roomKey(room);
        let k = keys.find(x => roomKey(x) === want);
        if(!k && label){
            const toks = String(label).toUpperCase().split(/[^A-Z0-9]+/).map(roomKey);
            k = keys.find(x => toks.includes(roomKey(x)));
        }
        return k ? {key:k, ...d.rooms[k]} : null;
    }

    // ---------- geometry ----------
    function projector(lat0, lon0){
        const kx = Math.cos(lat0 * Math.PI / 180) * 111320, ky = 110540;
        return (lat, lon) => [(lon - lon0) * kx, -(lat - lat0) * ky];
    }
    function pip([x, y], ring){
        let inside = false;
        for(let i = 0, j = ring.length - 1; i < ring.length; j = i++){
            const [xi, yi] = ring[i], [xj, yj] = ring[j];
            if((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
        }
        return inside;
    }
    function segDist([px, py], [ax, ay], [bx, by]){
        const dx = bx - ax, dy = by - ay, L = dx*dx + dy*dy;
        const t = L ? Math.max(0, Math.min(1, ((px-ax)*dx + (py-ay)*dy) / L)) : 0;
        return Math.hypot(px - ax - t*dx, py - ay - t*dy);
    }
    // 0 when inside the outline, else metres to the nearest wall
    function distToOutline(pt, polys){
        if(polys.some(p => pip(pt, p.outer) && !p.holes.some(h => pip(pt, h)))) return 0;
        let best = Infinity;
        for(const p of polys) for(const ring of [p.outer, ...p.holes])
            for(let i = 0; i < ring.length; i++) best = Math.min(best, segDist(pt, ring[i], ring[(i + 1) % ring.length]));
        return best;
    }
    function haversine(a, b){
        const R = 6371000, rad = x => x * Math.PI / 180;
        const h = Math.sin(rad(b[0]-a[0])/2)**2 + Math.cos(rad(a[0])) * Math.cos(rad(b[0])) * Math.sin(rad(b[1]-a[1])/2)**2;
        return 2 * R * Math.asin(Math.sqrt(h));
    }

    // ---------- location ----------
    // Uses window.StudyLocate when the page provides it, else the browser API.
    // Safari only shows the permission prompt when geolocation is requested
    // synchronously inside a user gesture, so startLocation(true) must be
    // called straight from a click handler (no await before it).
    function onFix(fix){
        if(!fix || typeof fix.lat !== 'number') return;
        loc.fix = fix; loc.err = '';
        redrawLocation();
    }
    function onErr(err){
        loc.err = (window.StudyLocate && StudyLocate.explainError) ? StudyLocate.explainError(err)
            : err && err.code === 1 ? 'Location permission is off for this site.'
            : err && err.code === 3 ? 'Location timed out. Try again outside or near a window.'
            : 'Could not get your location.';
        redrawLocation();
    }
    function startLocation(fromGesture){
        if(loc.on) return;
        const SL = window.StudyLocate;
        if(SL && typeof SL.watch === 'function'){
            loc.on = true;
            if(fromGesture && typeof SL.get === 'function'){
                try{ SL.get({enableHighAccuracy:true}).then(onFix, onErr); }catch(e){ onErr(e); }
            }
            try{ loc.stop = SL.watch(f => (f && typeof f.lat === 'number') ? onFix(f) : f && onErr(f)); }
            catch(e){ onErr(e); }
            redrawLocation();
            return;
        }
        if(!('geolocation' in navigator)){ loc.err = 'Location is not available in this browser.'; redrawLocation(); return; }
        loc.on = true;
        const opts = {enableHighAccuracy:true, timeout:20000, maximumAge:30000};
        const ok = p => onFix({lat:p.coords.latitude, lon:p.coords.longitude, accuracy:p.coords.accuracy, at:p.timestamp});
        if(fromGesture) navigator.geolocation.getCurrentPosition(ok, onErr, opts);
        const id = navigator.geolocation.watchPosition(ok, onErr, opts);
        loc.stop = () => navigator.geolocation.clearWatch(id);
        redrawLocation();
    }
    function stopLocation(){
        if(loc.stop) try{ loc.stop(); }catch(e){}
        loc.stop = null; loc.on = false;
    }
    async function autoLocation(){
        // Start without a click only when it can't trigger a prompt.
        const SL = window.StudyLocate;
        if(SL && typeof SL.last === 'function'){
            const f = SL.last();
            if(f && typeof f.lat === 'number'){ onFix(f); startLocation(false); return; }
        }
        try{
            if(navigator.permissions && navigator.permissions.query){
                const st = await navigator.permissions.query({name:'geolocation'});
                if(st.state === 'granted') startLocation(false);
            }
        }catch(e){}
    }

    // ---------- maps app link ----------
    function mapsLink(lat, lon, name){
        const ua = navigator.userAgent || '';
        const apple = /iPhone|iPad|iPod|Macintosh/.test(ua);
        const android = /Android/.test(ua);
        const ll = `${lat.toFixed(6)},${lon.toFixed(6)}`;
        if(apple) return {href:`https://maps.apple.com/?daddr=${ll}&dirflg=w`, app:'Apple Maps'};
        const g = {href:`https://www.google.com/maps/dir/?api=1&destination=${ll}&travelmode=walking`, app:'Google Maps'};
        if(android) g.geo = `geo:${ll}?q=${ll}(${encodeURIComponent(name)})`;
        return g;
    }

    // ---------- modal ----------
    function build(){
        if(ui.root) return;
        const el = document.createElement('div');
        el.className = 'smap-overlay';
        el.hidden = true;
        el.innerHTML = `<div class="smap-box" role="dialog" aria-modal="true" aria-labelledby="smapTitle">
            <div class="smap-head">
                <div><h2 id="smapTitle"></h2><div class="smap-sub" id="smapSub"></div></div>
                <button type="button" class="btn smap-close" aria-label="Close map">✕</button>
            </div>
            <div class="smap-bar">
                <div class="smap-tabs" role="tablist">
                    <button type="button" role="tab" data-tab="floor" aria-selected="true">Floor</button>
                    <button type="button" role="tab" data-tab="campus" aria-selected="false">Campus</button>
                </div>
                <div class="smap-acts">
                    <button type="button" class="btn btn-sm smap-me">📍 Show me</button>
                    <a class="btn btn-sm smap-go" target="_blank" rel="noopener">Directions ↗</a>
                    <a class="btn btn-sm smap-geo" target="_blank" rel="noopener" hidden>Other app</a>
                </div>
            </div>
            <div class="smap-loc" aria-live="polite"></div>
            <div class="smap-body">
                <section class="smap-panel" data-panel="floor"></section>
                <section class="smap-panel" data-panel="campus" hidden><div class="smap-leaflet"></div><p class="smap-note smap-campus-note"></p></section>
            </div>
        </div>`;
        document.body.appendChild(el);
        ui.root = el;
        ui.box = el.querySelector('.smap-box');
        ui.floor = el.querySelector('[data-panel="floor"]');
        ui.campus = el.querySelector('[data-panel="campus"]');
        ui.locMsg = el.querySelector('.smap-loc');
        el.addEventListener('click', e => { if(e.target === el) close(); });
        el.querySelector('.smap-close').addEventListener('click', close);
        el.querySelector('.smap-tabs').addEventListener('click', e => {
            const b = e.target.closest('[data-tab]'); if(b) showTab(b.dataset.tab);
        });
        // Called synchronously from the click: Safari needs that for the prompt.
        el.querySelector('.smap-me').addEventListener('click', () => {
            if(loc.on && loc.fix){ stopLocation(); loc.fix = null; redrawLocation(); return; }
            loc.err = ''; startLocation(true);
        });
        ui.floor.addEventListener('click', e => {
            const b = e.target.closest('[data-level]');
            if(b){ ui.level = b.dataset.level; renderFloor(); }
            if(e.target.closest('[data-zoom]')){ ui.zoomPlan = !ui.zoomPlan; renderFloor(); }
        });
        document.addEventListener('keydown', e => {
            if(ui.root.hidden) return;
            if(e.key === 'Escape'){ e.preventDefault(); close(); }
            if(e.key === 'Tab'){   // keep focus inside the dialog
                const f = [...ui.box.querySelectorAll('button:not([hidden]),a[href]:not([hidden]),[tabindex="0"]')].filter(x => x.offsetParent);
                if(!f.length) return;
                if(e.shiftKey && document.activeElement === f[0]){ e.preventDefault(); f[f.length-1].focus(); }
                else if(!e.shiftKey && document.activeElement === f[f.length-1]){ e.preventDefault(); f[0].focus(); }
            }
        });
    }

    async function open(opts){
        build();
        ui.lastFocus = document.activeElement;
        ui.opts = opts;
        ui.code = null; ui.data = null; ui.level = null;
        ui.root.hidden = false;
        document.documentElement.classList.add('smap-lock');
        ui.root.querySelector('.smap-close').focus();
        ui.root.querySelector('#smapTitle').textContent = opts.label || opts.building;
        ui.root.querySelector('#smapSub').textContent = 'Loading map…';
        ui.floor.innerHTML = '<p class="smap-note">Loading…</p>';
        showTab('floor');
        autoLocation();
        try{
            await loadIndex();
            await loadBuildingsGeo();
            ui.code = resolveCode(opts.building, opts.name);
            if(ui.code) ui.data = await getJSON(DIR + index.buildings[ui.code].file);
        }catch(err){
            console.warn('maps', err);
            ui.floor.innerHTML = '<p class="smap-note">Could not load the map data. Check your connection and try again.</p>';
            ui.root.querySelector('#smapSub').textContent = '';
            return;
        }
        if(ui.opts !== opts) return;   // reopened meanwhile
        setupHeader();
        if(ui.data){
            ui.roomPos = findRoom(ui.data, opts.room, opts.label);
            const f = ui.roomPos ? ui.roomPos.floor : floorOf(opts.room, ui.data.floor_rule);
            ui.roomFloor = f;
            ui.zoomPlan = !!ui.roomPos;
            const levels = new Set(ui.data.levels);
            if(f) levels.add(f);
            ui.levels = [...levels].sort((a, b) => levelKey(a) - levelKey(b) || a.localeCompare(b));
            ui.level = f || (ui.levels.includes('1') ? '1' : ui.levels[0] || null);
            renderFloor();
        }else{
            ui.floor.innerHTML = `<p class="smap-note">There's no floor map for this building yet. The Campus tab shows where it is.</p>`;
            showTab('campus');
        }
        if(ui.tab === 'campus') renderCampus();
    }

    function close(){
        if(!ui.root || ui.root.hidden) return;
        ui.root.hidden = true;
        ui.opts = null;
        document.documentElement.classList.remove('smap-lock');
        stopLocation();
        if(ui.lastFocus && ui.lastFocus.focus) ui.lastFocus.focus();
    }

    function showTab(tab){
        ui.tab = tab;
        ui.root.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-selected', b.dataset.tab === tab));
        ui.floor.hidden = tab !== 'floor';
        ui.campus.hidden = tab !== 'campus';
        if(tab === 'campus' && ui.opts && index) renderCampus();
    }

    // Where "Directions" points: the building's coordinates from buildings.json,
    // unless that point is clearly off the mapped outline.
    function target(){
        const b = index && ui.code ? index.buildings[ui.code] : null;
        const g = buildingsGeo && (buildingsGeo[normCode(ui.opts.building)] || (ui.code && buildingsGeo[ui.code]));
        let pt = g && typeof g.lat === 'number' ? [g.lat, g.lon] : null;
        if(pt && ui.data){
            const P = projector(ui.data.center[0], ui.data.center[1]);
            const polys = ui.data.outline.polys.map(p => ({outer:p.outer.map(q => P(...q)), holes:p.holes.map(h => h.map(q => P(...q)))}));
            if(distToOutline(P(...pt), polys) > 30) pt = null;
        }
        if(!pt && b) pt = b.center;
        return pt;
    }

    function setupHeader(){
        const name = ui.data ? ui.data.name : (index.buildings[ui.code] || {}).name || ui.opts.building;
        const room = ui.opts.room ? roomLabel(ui.opts.room) : '';
        ui.root.querySelector('#smapTitle').textContent = room ? `${name} · ${room}` : name;
        ui.root.querySelector('#smapSub').textContent = '';
        const pt = target();
        const go = ui.root.querySelector('.smap-go'), geo = ui.root.querySelector('.smap-geo');
        if(pt){
            const l = mapsLink(pt[0], pt[1], name);
            go.href = l.href; go.hidden = false;
            go.textContent = `Directions (${l.app}) ↗`;
            go.title = `Walking directions to ${name} in ${l.app}`;
            if(l.geo){ geo.href = l.geo; geo.hidden = false; } else geo.hidden = true;
        }else{ go.hidden = true; geo.hidden = true; }
    }

    // ---------- floor map (SVG) ----------
    const ICON = {
        entrance: (x, y, u) => `<circle cx="${x}" cy="${y}" r="${u*1.1}" class="m-door"/>`,
        elevator: (x, y, u) => `<rect x="${x-u}" y="${y-u}" width="${2*u}" height="${2*u}" class="m-elev"/><path d="M${x-u*.45} ${y-u*.15}l${u*.45} ${-u*.55}l${u*.45} ${u*.55}zM${x-u*.45} ${y+u*.15}l${u*.45} ${u*.55}l${u*.45} ${-u*.55}z" class="m-glyph"/>`,
        stairs: (x, y, u) => `<rect x="${x-u}" y="${y-u}" width="${2*u}" height="${2*u}" class="m-stair"/><path d="M${x-u*.7} ${y+u*.6}h${u*.45}v${-u*.4}h${u*.45}v${-u*.4}h${u*.45}v${-u*.4}" class="m-stroke"/>`,
        restroom: (x, y, u) => `<rect x="${x-u}" y="${y-u}" width="${2*u}" height="${2*u}" rx="${u*.3}" class="m-wc"/><text x="${x}" y="${y+u*.38}" class="m-txt" font-size="${u*1.05}">WC</text>`,
    };
    const KIND = {entrance:'Door', elevator:'Elevator', stairs:'Stair door', restroom:'Restroom', steps:'Steps'};
    function srcLabel(f){
        return {osm:'OpenStreetMap', c3d:"UCLA accessibility survey", ucla_gis:'UCLA campus map GIS', ucla_pdf:"UCLA accessibility map (2020)"}[f.src] || f.src;
    }

    function renderFloor(){
        const d = ui.data; if(!d) return;
        const P = projector(d.center[0], d.center[1]);
        const polys = d.outline.polys.map(p => ({outer:p.outer.map(q => P(...q)), holes:p.holes.map(h => h.map(q => P(...q)))}));
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        const grow = ([x, y]) => { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); };
        polys.forEach(p => p.outer.forEach(grow));
        const lv = ui.level;
        // what this floor shows
        const feats = d.features.filter(f => {
            // restrooms belong to one floor; one with no floor recorded shows on every floor
            if(f.t === 'restroom') return f.acc !== 'building' && (!f.floor || f.floor === lv);
            return true;
        });
        feats.forEach(f => f.line ? f.line.forEach(q => grow(P(...q))) : grow(P(f.lat, f.lon)));
        const plans = (d.plans || []).filter(pl => pl.floor === lv);
        if(plans.length && ui.zoomPlan){
            x0 = y0 = Infinity; x1 = y1 = -Infinity;
            plans.forEach(pl => pl.spaces.forEach(sp => (sp.poly || [sp.at]).forEach(q => grow(P(...q)))));
        }
        const span = Math.max(x1 - x0, y1 - y0, 30);
        const pad = plans.length && ui.zoomPlan ? span * 0.06 + 2 : span * 0.1 + 6;
        x0 -= pad; y0 -= pad; x1 += pad; y1 += pad + span * 0.06;
        const W = x1 - x0, H = y1 - y0;
        const u = Math.max(W, H) / 70;   // marker unit
        const path = polys.map(p => [p.outer, ...p.holes].map(r => 'M' + r.map(q => q.map(v => v.toFixed(1)).join(' ')).join('L') + 'Z').join('')).join('');
        let s = `<rect x="${x0}" y="${y0}" width="${W}" height="${H}" class="m-bg"/>`;
        s += `<path d="${path}" class="m-wall" fill-rule="evenodd"/>`;
        // traced rooms (only where a published plan gives them)
        const want = ui.roomPos && ui.roomPos.floor === lv ? ui.roomPos.key : null;
        for(const pl of plans){
            for(const sp of pl.spaces){
                if(sp.poly){
                    const pts = sp.poly.map(q => P(...q));
                    const ptsS = pts.map(q => q.map(v => v.toFixed(2)).join(',')).join(' ');
                    const cx = pts.reduce((a, q) => a + q[0], 0) / pts.length, cy = pts.reduce((a, q) => a + q[1], 0) / pts.length;
                    const w = Math.max(...pts.map(q => q[0])) - Math.min(...pts.map(q => q[0]));
                    const sel = want && sp.id === want;
                    s += `<polygon points="${ptsS}" class="r-${sp.kind}${sel ? ' r-sel' : ''}"><title>${esc(sp.name || sp.label || '')}</title></polygon>`;
                    if(sp.kind === 'xbox') s += `<path d="M${pts[0].join(' ')}L${pts[2].join(' ')}M${pts[1].join(' ')}L${pts[3].join(' ')}" class="r-xline"/>`;
                    else if(sp.label){
                        const fs = Math.min(u * 0.95, w / Math.max(2.2, sp.label.length * 0.62));
                        s += `<text x="${cx}" y="${cy + fs * 0.35}" class="r-lbl${sel ? ' r-lbl-sel' : ''}" font-size="${fs}">${esc(sp.label)}</text>`;
                    }
                }else if(sp.at){
                    const [x, y] = P(...sp.at), sel = want && sp.id === want;
                    s += `<circle cx="${x}" cy="${y}" r="${u*.55}" class="r-pod${sel ? ' r-sel' : ''}"><title>${esc(sp.name || sp.label)}</title></circle><text x="${x}" y="${y - u*.8}" class="r-lbl" font-size="${u*.75}">${esc(sp.label)}</text>`;
                }
            }
        }
        // numbered list of what's drawn
        const list = [];
        let n = 0;
        const ordered = feats.slice().sort((a, b) => ['entrance','stairs','steps','elevator','restroom'].indexOf(a.t) - ['entrance','stairs','steps','elevator','restroom'].indexOf(b.t));
        for(const f of ordered){
            if(f.t === 'steps'){
                const pts = f.line.map(q => P(...q).map(v => v.toFixed(1)).join(',')).join(' ');
                s += `<polyline points="${pts}" class="m-steps" stroke-width="${u*.9}"/>`;
                continue;
            }
            const [x, y] = P(f.lat, f.lon);
            n++;
            f._n = n;
            // a restroom point that sits in a traced restroom is drawn by the plan already;
            // one that sits in a traced non-restroom room contradicts the plan, so it isn't drawn
            const hit = f.t === 'restroom' ? plans.flatMap(pl => pl.spaces).find(sp => sp.poly && sp.kind !== 'other' && pip([x, y], sp.poly.map(q => P(...q)))) : null;
            if(hit){
                if(hit.kind === 'restroom') s += `<text x="${x}" y="${y - u*.4}" class="m-num" font-size="${u*1.1}">${n}</text>`;
                else f._conflict = `not drawn: this point falls inside ${hit.name || hit.label} on the published plan`;
                list.push(f); continue;
            }
            if(f.acc === 'approx') s += `<circle cx="${x}" cy="${y}" r="8" class="m-approx"/>`;
            s += `<g class="${f.acc === 'approx' ? 'm-isapprox' : ''}">${(ICON[f.t] || ICON.entrance)(x, y, u)}</g>`;
            s += `<text x="${x + u*1.5}" y="${y - u*1.1}" class="m-num" font-size="${u*1.5}">${n}</text>`;
            list.push(f);
        }
        // the selected room, only where a plan places it
        if(want){
            const [x, y] = P(ui.roomPos.lat, ui.roomPos.lon);
            s += `<circle cx="${x}" cy="${y}" r="${u*2.2}" class="m-room"/>`;
        }
        // north arrow + scale bar
        const na = [x1 - u*3, y0 + u*3.2];
        s += `<path d="M${na[0]} ${na[1]-u*2}l${u} ${u*2.6}l${-u} ${-u*.7}l${-u} ${u*.7}z" class="m-north"/><text x="${na[0]}" y="${na[1]+u*2.2}" class="m-lbl" font-size="${u*1.5}">N</text>`;
        const nice = [5, 10, 20, 25, 50, 100, 200];
        const L = nice.reduce((a, b) => Math.abs(b - W/5) < Math.abs(a - W/5) ? b : a);
        const sb = [x0 + u*2, y1 - u*2.2];
        s += `<path d="M${sb[0]} ${sb[1]-u*.6}v${u*.6}h${L}v${-u*.6}" class="m-scale"/><text x="${sb[0] + L/2}" y="${sb[1] - u*1}" class="m-lbl" font-size="${u*1.3}">${L} m · ${ft(L)} ft</text>`;
        s += `<g class="m-you"></g>`;

        const levels = ui.levels.length ? ui.levels : [];
        const floorBtns = levels.length ? `<div class="smap-floors" role="group" aria-label="Floor">${levels.map(l =>
            `<button type="button" class="day-btn${l === lv ? ' active' : ''}" data-level="${esc(l)}" aria-pressed="${l === lv}">${esc(levelName(l))}${l === ui.roomFloor ? ' ★' : ''}</button>`).join('')}</div>` : '';
        ui.floor.innerHTML = `${floorBtns}
            <p class="smap-roomline">${roomLine()}</p>
            ${plans.length ? `<button type="button" class="btn btn-sm smap-zoom" data-zoom>${ui.zoomPlan ? 'Show whole building' : 'Zoom to the traced rooms'}</button>` : ''}
            <div class="smap-svgwrap"><svg class="smap-svg" viewBox="${x0.toFixed(1)} ${y0.toFixed(1)} ${W.toFixed(1)} ${H.toFixed(1)}" role="img" aria-label="Line map of ${esc(d.name)}${lv ? ', ' + esc(levelName(lv)) : ''}">${s}</svg></div>
            <div class="smap-legend"><span><svg viewBox="-2 -2 4 4">${ICON.entrance(0,0,1.6)}</svg>Door / exit</span><span><svg viewBox="-2 -2 4 4">${ICON.stairs(0,0,1.6)}</svg>Stairs</span><span><svg viewBox="-2 -2 4 4">${ICON.elevator(0,0,1.6)}</svg>Elevator</span><span><svg viewBox="-2 -2 4 4">${ICON.restroom(0,0,1.6)}</svg>Restroom</span><span><i class="m-key-approx"></i>Approximate spot</span>${plans.length ? '<span><i class="m-key-room"></i>Room from a published plan</span>' : ''}</div>
            <ol class="smap-list">${list.map(f => `<li value="${f._n}"><b>${esc(f.name || KIND[f.t])}</b>${featDetail(f)}</li>`).join('')}</ol>
            ${missingHtml(lv)}
            ${plans.map(planHtml).join('')}
            <p class="smap-note">${sourcesHtml()}</p>`;
        redrawLocation();
    }

    function featDetail(f){
        const bits = [];
        if(f.name && f.t !== 'restroom') bits.push(KIND[f.t].toLowerCase());
        if(f.note) bits.push(esc(f.note));
        if(f._conflict) bits.push(`<em>${esc(f._conflict)}</em>`);
        if(f.side) bits.push(`${esc(f.side)} side`);
        if(f.t === 'entrance' || (f.t === 'restroom' && !f.floor)) bits.push(f.floor ? `opens on ${esc(levelName(f.floor)).toLowerCase()}` : 'floor not recorded');
        if(f.osm_level) bits.push(`level ${esc(f.osm_level)} in OSM`);
        bits.push(f.acc === 'approx' ? `<em>approximate, ${esc(srcLabel(f))}</em>` : esc(srcLabel(f)));
        return ` <span>${bits.join(' · ')}</span>`;
    }

    function roomLine(){
        const d = ui.data, room = ui.opts.room;
        if(!room) return '';
        const rl = esc(roomLabel(room));
        const f = ui.roomFloor, r = d.floor_rule || {};
        let floor;
        if(!f) floor = `Can't tell the floor from the room number`;
        else if(r.checked && r.agree === r.checked) floor = `${esc(levelName(f))}, from the room number (UCLA's own restroom records for this building follow that rule: ${r.agree} of ${r.checked})`;
        else if(r.checked) floor = `Probably ${esc(levelName(f).toLowerCase())}, from the room number, but only ${r.agree} of ${r.checked} UCLA records here follow that rule`;
        else floor = `${esc(levelName(f))}, from the room number (UCLA's usual numbering, not confirmed for this building)`;
        if(ui.roomPos){
            const pl = (d.plans || []).find(p => p.file === ui.roomPos.plan) || {};
            return `<b>Room ${rl}</b>: ${esc(levelName(f))}, outlined in blue. Position from ${esc((pl.source || {}).publisher || 'a published plan')}'s map (see the note below the map).`;
        }
        const pos = ' Where the room is inside the building isn\'t mapped by any public source, so there\'s no room marker.';
        return `<b>Room ${rl}</b>: ${floor}.${pos}`;
    }

    function missingHtml(lv){
        const d = ui.data, has = t => d.features.some(f => f.t === t && f.acc !== 'building');
        const out = [];
        if(!(d.plans || []).some(p => p.floor === lv)) out.push('No public floor plan for this floor, so rooms and corridors aren\'t drawn.');
        const listed = d.features.filter(f => f.t === 'restroom' && f.acc === 'building' && f.floor === lv);
        if(listed.length) out.push(`UCLA lists restroom${listed.length > 1 ? 's' : ''} ${listed.map(f => esc(f.room)).join(', ')} on this floor without a position.`);
        if(!d.features.some(f => f.t === 'restroom' && (f.floor === lv || !f.floor) && f.acc !== 'building')) out.push('No restroom data for this floor.');
        if(!has('elevator')) out.push('No elevator data (that doesn\'t mean there isn\'t one).');
        if(!has('stairs') && !has('steps')) out.push('No stair data.');
        else out.push('Indoor stairwells aren\'t in any public source; only stair doors and outdoor steps are drawn.');
        if(!has('entrance')) out.push('No door data.');
        if(d.features.some(f => f.t === 'entrance' && !f.floor)) out.push('Doors show on every floor because the data doesn\'t say which floor each one opens onto.');
        if(has('elevator')) out.push('Elevators show on every floor; which floors each one stops at isn\'t recorded.');
        if(d.features.some(f => f.osm_level)) out.push('"Level N in OSM" is OpenStreetMap\'s level tag, which doesn\'t always match UCLA\'s floor numbers.');
        return `<p class="smap-note smap-missing">${out.join(' ')}</p>`;
    }

    function planHtml(pl){
        const src = pl.source || {};
        return `<p class="smap-note"><b>${esc(pl.title)}</b>: rooms traced from <a href="${esc(src.page || src.image)}" target="_blank" rel="noopener">${esc(src.publisher)}'s map</a> (${esc(src.date || '')}). ${esc(pl.how)} Fit error: up to ${pl.fit.max_m} m at the control points. ${pl.notes.map(esc).join(' ')} Rooms outside this plan aren't drawn.</p>`;
    }

    function sourcesHtml(){
        const d = ui.data, o = d.outline, S = index.sources;
        const link = k => `<a href="${esc(S[k].url)}" target="_blank" rel="noopener">${esc(S[k].label)}</a>`;
        const a = o.agree_pct || {};
        const agree = [a.ucla != null ? `${a.ucla}% overlap with UCLA's GIS outline` : '', a.c3d != null ? `${a.c3d}% with UCLA's interactive map` : ''].filter(Boolean).join(', ');
        const weak = (a.ucla != null && a.ucla < 70) || (a.c3d != null && a.c3d < 70);
        let s = `Outline: ${link(o.src)}${agree ? ` (${agree})` : ''}.${weak ? ' The sources disagree on this outline, so treat its shape as approximate.' : ''} The outline is the building's footprint; upper floors can be smaller.`;
        const used = [...new Set(d.features.map(f => f.src))].filter(k => S[k]);
        if(used.length) s += ` Doors, elevators, stairs and restrooms: ${used.map(link).join(', ')}.`;
        if(d.features.some(f => f.src === 'ucla_pdf')) s += ' Spots from the 2020 accessibility map are approximate (about 7 m off on average, sometimes more) and are drawn with a dashed ring.';
        if(d.floors_ucla) s += ` UCLA lists ${d.floors_ucla} floors for this building.`;
        return s;
    }

    function redrawLocation(){
        if(!ui.root || ui.root.hidden) return;
        const btn = ui.root.querySelector('.smap-me');
        btn.textContent = loc.on && loc.fix ? '📍 Hide me' : loc.on ? '📍 Locating…' : '📍 Show me';
        btn.classList.toggle('on', !!(loc.on && loc.fix));
        let msg = loc.err || '';
        const g = ui.floor.querySelector('.m-you');
        if(g) g.innerHTML = '';
        if(loc.fix && ui.data){
            const d = ui.data, P = projector(d.center[0], d.center[1]);
            const polys = d.outline.polys.map(p => ({outer:p.outer.map(q => P(...q)), holes:p.holes.map(h => h.map(q => P(...q)))}));
            const me = P(loc.fix.lat, loc.fix.lon), acc = Math.max(loc.fix.accuracy || 0, 3);
            const dist = distToOutline(me, polys);
            if(g){
                const vb = ui.floor.querySelector('svg').viewBox.baseVal;
                const u = Math.max(vb.width, vb.height) / 70;
                const inView = me[0] > vb.x - acc && me[0] < vb.x + vb.width + acc && me[1] > vb.y - acc && me[1] < vb.y + vb.height + acc;
                if(inView) g.innerHTML = `<circle cx="${me[0]}" cy="${me[1]}" r="${acc}" class="m-acc"/><circle cx="${me[0]}" cy="${me[1]}" r="${u*.9}" class="m-me"/>`;
            }
            if(dist === 0) msg = `You're inside ${d.name} (±${Math.round(acc)} m). GPS can't tell which floor you're on.`;
            else if(dist <= acc) msg = `You're at or near ${d.name} (±${Math.round(acc)} m).`;
            else msg = `You're outside the building, about ${distLabel(dist)} from it (±${Math.round(acc)} m).`;
        }else if(loc.on && !loc.err) msg = 'Finding you…';
        ui.locMsg.textContent = msg;
        if(ui.tab === 'campus') updateCampusMe();
    }

    // ---------- campus map (Leaflet, loaded on first use) ----------
    let leafletP = null;
    function loadLeaflet(){
        if(window.L) return Promise.resolve(window.L);
        if(leafletP) return leafletP;
        leafletP = new Promise((ok, fail) => {
            const css = document.createElement('link');
            css.rel = 'stylesheet'; css.href = LEAFLET.css; css.integrity = LEAFLET.cssSri; css.crossOrigin = 'anonymous';
            document.head.appendChild(css);
            const js = document.createElement('script');
            js.src = LEAFLET.js; js.integrity = LEAFLET.jsSri; js.crossOrigin = 'anonymous';
            js.onload = () => ok(window.L);
            js.onerror = () => { leafletP = null; fail(new Error('Leaflet failed to load')); };
            document.head.appendChild(js);
        });
        return leafletP;
    }
    async function renderCampus(){
        const note = ui.campus.querySelector('.smap-campus-note');
        let L;
        try{ L = await loadLeaflet(); }
        catch(e){ note.textContent = 'The campus map could not load (no connection?). The Directions button still works.'; return; }
        if(!ui.opts) return;
        const el = ui.campus.querySelector('.smap-leaflet');
        if(!ui.map){
            ui.map = L.map(el, {zoomControl:true, attributionControl:true});
            L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom:19,
                attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
            }).addTo(ui.map);
            ui.others = L.layerGroup().addTo(ui.map);
            ui.sel = L.layerGroup().addTo(ui.map);
            ui.me = L.layerGroup().addTo(ui.map);
            for(const [code, b] of Object.entries(index.buildings)){
                L.polygon(b.outline, {color:'#94a3b8', weight:1, fillOpacity:0.08, interactive:true})
                    .bindTooltip(b.name, {sticky:true}).addTo(ui.others);
            }
        }
        ui.sel.clearLayers();
        const b = ui.code && index.buildings[ui.code];
        let bounds;
        if(b){
            const poly = L.polygon(b.outline, {color:'#60a5fa', weight:3, fillColor:'#60a5fa', fillOpacity:0.45}).addTo(ui.sel);
            bounds = poly.getBounds();
            note.innerHTML = `${esc(b.name)} is highlighted in blue. Other StudySpace buildings are outlined in gray. Outlines: OpenStreetMap or UCLA campus GIS.`;
        }else{
            const pt = target();
            if(pt){ L.circleMarker(pt, {radius:8, color:'#60a5fa'}).addTo(ui.sel); bounds = L.latLngBounds([pt, pt]); }
            note.textContent = 'No outline for this building yet; the dot is its location from buildings.json.';
        }
        ui.bounds = bounds;
        setTimeout(() => {
            if(!ui.map) return;
            ui.map.invalidateSize();
            if(bounds) ui.map.fitBounds(bounds.pad(1.2), {maxZoom:18});
            else ui.map.setView([34.0705, -118.4445], 16);
            updateCampusMe(true);
        }, 30);
    }
    function updateCampusMe(fit){
        if(!ui.map || !window.L) return;
        ui.me.clearLayers();
        if(!loc.fix) return;
        const ll = [loc.fix.lat, loc.fix.lon];
        L.circle(ll, {radius:Math.max(loc.fix.accuracy || 0, 3), color:'#34d399', weight:1, fillOpacity:0.15, interactive:false}).addTo(ui.me);
        L.circleMarker(ll, {radius:7, color:'#fff', weight:2, fillColor:'#34d399', fillOpacity:1}).bindTooltip('You').addTo(ui.me);
        if(fit && ui.bounds && haversine(ll, [ui.bounds.getCenter().lat, ui.bounds.getCenter().lng]) < 2500){
            ui.map.fitBounds(L.latLngBounds([ui.bounds.getSouthWest(), ui.bounds.getNorthEast(), ll]).pad(0.3), {maxZoom:18});
        }
    }

    // ---------- hook: any [data-map] button opens the map ----------
    // Capture phase so the room card's own click handler (expand details) doesn't also fire.
    document.addEventListener('click', e => {
        const b = e.target.closest && e.target.closest('[data-map]');
        if(!b) return;
        e.preventDefault();
        e.stopPropagation();
        open({building:b.dataset.map, room:b.dataset.room || '', label:b.dataset.label || '', name:b.dataset.name || ''});
    }, true);

    window.StudyMaps = {open, close, floorOf};
})();
