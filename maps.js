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

    let index = null, buildingsGeo = null, roomsIndex = null;
    // UCLA Space Inventory's public room list per floor: the exact floor for a
    // room, plus which stair and elevator "rooms" each floor has.
    async function loadRoomsIndex(){
        if(roomsIndex) return roomsIndex;
        try{ roomsIndex = (await getJSON(DIR + 'rooms_index.json')).buildings; }catch(e){ roomsIndex = {}; }
        return roomsIndex;
    }
    function indexFloor(code, room){
        const b = roomsIndex && roomsIndex[code];
        if(!b) return null;
        const want = roomKey(room);
        for(const [lv, f] of Object.entries(b.floors)) if(f.rooms.some(r => roomKey(r) === want)) return lv;
        return null;
    }
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
            const z = e.target.closest('[data-zoomby]');
            if(z) zoomBy(+z.dataset.zoomby);
            if(e.target.closest('[data-theme-toggle]')){
                savePlanTheme(planTheme() === 'dark' ? 'paper' : 'dark');
                const keep = ui.view; renderFloor(); ui.view = keep; drawPlan();
            }
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
            await loadRoomsIndex();
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
            ui.indexFloor = indexFloor(ui.code, opts.room);
            const f = ui.roomPos ? ui.roomPos.floor : (ui.indexFloor || floorOf(opts.room, ui.data.floor_rule));
            ui.roomFloor = f;
            const levels = new Set(ui.data.levels);
            for(const lv of Object.keys(((roomsIndex || {})[ui.code] || {floors:{}}).floors)) if(lv !== 'R' && lv !== 'RF') levels.add(lv);
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

    // ---------- floor map (SVG, drawn like a fire escape plan) ----------
    // The plan sits on a light "paper" panel (or a dark one; the choice is remembered).
    // All coordinates are metres east/south of the building centre; strokes don't scale.
    const THEME_KEY = 'smap-plan-theme';
    function planTheme(){ try{ return localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'paper'; }catch(e){ return 'paper'; } }
    function savePlanTheme(t){ try{ localStorage.setItem(THEME_KEY, t); }catch(e){} }

    const KIND = {entrance:'Door', elevator:'Elevator', stairs:'Stair door', restroom:'Restroom', steps:'Steps'};
    const ROOM_KIND = {room:'Room', corridor:'Corridor', stair:'Stairs', elevator:'Elevator', restroom:'Restroom', exit:'Exit',
        mech:'Service room', other:'Space the plan leaves unlabeled', xbox:'Crossed box (no legend on the plan)', pod:'Table or pod'};
    function srcLabel(f){
        return {osm:'OpenStreetMap', c3d:"UCLA accessibility survey", ucla_gis:'UCLA campus map GIS', ucla_pdf:"UCLA accessibility map (2020)"}[f.src] || f.src;
    }
    const f2 = v => (Math.round(v * 100) / 100).toString();
    const ptsAttr = pts => pts.map(q => f2(q[0]) + ',' + f2(q[1])).join(' ');
    const ringD = r => 'M' + r.map(q => f2(q[0]) + ' ' + f2(q[1])).join('L') + 'Z';

    // ---- small geometry helpers (projected metres) ----
    function ringArea(r){ let a = 0; for(let i = 0, j = r.length - 1; i < r.length; j = i++) a += r[j][0] * r[i][1] - r[i][0] * r[j][1]; return a / 2; }
    function bboxOf(pts){
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        for(const [x, y] of pts){ x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
        return {x0, y0, x1, y1, w:x1 - x0, h:y1 - y0};
    }
    // a point well inside the polygon: its centroid when that is comfortably inside, else a coarse pole of inaccessibility
    function edgeDist(p, r){ let d = Infinity; for(let k = 0; k < r.length; k++) d = Math.min(d, segDist(p, r[k], r[(k + 1) % r.length])); return d; }
    function innerPoint(r){
        const b = bboxOf(r);
        let best = null, bd = -1;
        for(let i = 1; i < 16; i++) for(let j = 1; j < 16; j++){
            const p = [b.x0 + b.w * i / 16, b.y0 + b.h * j / 16];
            if(!pip(p, r)) continue;
            const d = edgeDist(p, r);
            if(d > bd){ bd = d; best = p; }
        }
        let a = 0, cx = 0, cy = 0;
        for(let i = 0, j = r.length - 1; i < r.length; j = i++){
            const f = r[j][0] * r[i][1] - r[i][0] * r[j][1];
            a += f; cx += (r[j][0] + r[i][0]) * f; cy += (r[j][1] + r[i][1]) * f;
        }
        if(Math.abs(a) > 1e-9){
            const c = [cx / (3 * a), cy / (3 * a)];
            if(pip(c, r) && edgeDist(c, r) >= 0.6 * bd) return c;
        }
        return best || [b.x0 + b.w / 2, b.y0 + b.h / 2];
    }
    // the polygon's own frame: axis along its longest edge, extents along/across it
    function frameOf(r){
        let ang = 0, L = -1;
        for(let i = 0; i < r.length; i++){
            const a = r[i], b = r[(i + 1) % r.length], l = Math.hypot(b[0] - a[0], b[1] - a[1]);
            if(l > L){ L = l; ang = Math.atan2(b[1] - a[1], b[0] - a[0]); }
        }
        const e = [Math.cos(ang), Math.sin(ang)], n = [-e[1], e[0]];
        let s0 = Infinity, s1 = -Infinity, t0 = Infinity, t1 = -Infinity;
        for(const [x, y] of r){ const s = x*e[0] + y*e[1], t = x*n[0] + y*n[1]; s0 = Math.min(s0, s); s1 = Math.max(s1, s); t0 = Math.min(t0, t); t1 = Math.max(t1, t); }
        if(t1 - t0 > s1 - s0) return {e:n, n:[-e[0], -e[1]], s0:t0, s1:t1, t0:-s1, t1:-s0, ang:ang + Math.PI / 2};
        return {e, n, s0, s1, t0, t1, ang};
    }
    const at = (F, s, t) => [F.e[0]*s + F.n[0]*t, F.e[1]*s + F.n[1]*t];

    // ---- symbols ----
    const SYM = {
        elevator: (x, y, u) => `<rect x="${x-u}" y="${y-u}" width="${2*u}" height="${2*u}" class="pp-elev"/><path d="M${x-u*.45} ${y-u*.15}l${u*.45} ${-u*.55}l${u*.45} ${u*.55}zM${x-u*.45} ${y+u*.15}l${u*.45} ${u*.55}l${u*.45} ${-u*.55}z" class="pp-inkfill"/>`,
        stairs: (x, y, u) => `<rect x="${x-u}" y="${y-u}" width="${2*u}" height="${2*u}" class="pp-sym"/><path d="M${x-u} ${y-u*.5}h${2*u}M${x-u} ${y}h${2*u}M${x-u} ${y+u*.5}h${2*u}" class="pp-tread"/>`,
        restroom: (x, y, u) => `<rect x="${x-u}" y="${y-u}" width="${2*u}" height="${2*u}" rx="${u*.25}" class="pp-wcsym"/><text x="${x}" y="${y+u*.38}" class="pp-wctxt" font-size="${u*1.05}">WC</text>`,
        door: (x, y, u) => `<circle cx="${x}" cy="${y}" r="${u*.8}" class="pp-exitdot"/>`,
        you: (x, y, u) => `<circle cx="${x}" cy="${y}" r="${u*.75}" class="pp-me"/>`,
    };
    function exitTag(x, y, u, nx, ny){
        // arrow from the door outwards, then the EXIT sign
        const L = u * 1.6, w = u * 3.4, h = u * 1.5;
        const ax = x + nx * L, ay = y + ny * L;
        const cx = x + nx * (L + h * .55 + Math.abs(nx) * w * .32), cy = y + ny * (L + h * .55);
        const hx = nx * u * .55, hy = ny * u * .55, px = -ny * u * .4, py = nx * u * .4;
        return `<path d="M${f2(x + nx*u*.4)} ${f2(y + ny*u*.4)}L${f2(ax)} ${f2(ay)}" class="pp-exitline"/>`
            + `<path d="M${f2(ax + hx)} ${f2(ay + hy)}L${f2(ax + px)} ${f2(ay + py)}L${f2(ax - px)} ${f2(ay - py)}Z" class="pp-exitfill"/>`
            + `<rect x="${f2(cx - w/2)}" y="${f2(cy - h/2)}" width="${f2(w)}" height="${f2(h)}" rx="${f2(u*.25)}" class="pp-exitfill"/>`
            + `<text x="${f2(cx)}" y="${f2(cy + h*.32)}" class="pp-exittxt" font-size="${f2(h*.78)}">EXIT</text>`;
    }

    // what this floor has: geometry projected once per render
    function floorModel(){
        const d = ui.data, lv = ui.level;
        const P = projector(d.center[0], d.center[1]);
        const polys = d.outline.polys.map(p => ({outer:p.outer.map(q => P(...q)), holes:p.holes.map(h => h.map(q => P(...q)))}));
        const fl = (d.floors || {})[lv] || null;
        const rooms = fl ? fl.rooms.map(r => {
            const o = {...r};
            if(r.poly){ o.pts = r.poly.map(q => P(...q)); o.holesPts = (r.holes || []).map(h => h.map(q => P(...q))); o.lp = innerPoint(o.pts); }
            else if(r.at) o.lp = P(...r.at);
            return o;
        }) : [];
        const walls = fl ? (fl.walls || []).map(w => w.map(q => P(...q))) : [];
        const feats = d.features.filter(f => {
            // restrooms belong to one floor; one with no floor recorded shows on every floor
            if(f.t === 'restroom') return f.acc !== 'building' && (!f.floor || f.floor === lv);
            if(f.t === 'entrance' || f.t === 'stairs') return !f.floor || f.floor === lv;
            return true;
        });
        const want = ui.roomPos && ui.roomPos.floor === lv ? roomKey(ui.roomPos.key) : null;
        const sel = want ? rooms.find(r => r.ref && roomKey(r.ref) === want) : null;
        return {d, lv, P, polys, fl, rooms, walls, feats, sel};
    }

    function extentOf(M){
        const all = [];
        M.polys.forEach(p => all.push(...p.outer));
        M.feats.forEach(f => f.line ? f.line.forEach(q => all.push(M.P(...q))) : all.push(M.P(f.lat, f.lon)));
        M.rooms.forEach(r => r.pts ? all.push(...r.pts) : r.lp && all.push(r.lp));
        const b = bboxOf(all);
        const span = Math.max(b.w, b.h, 30), pad = span * 0.09 + 5;
        return {x:b.x0 - pad, y:b.y0 - pad, w:b.w + 2*pad, h:b.h + 2*pad + span * 0.05};
    }
    function startView(M, base){
        // a selected room: centre on it; a partial plan: show the traced part
        const fit = (b, minW) => {
            const asp = base.h / base.w;
            let w = Math.max(b.w * 1.25, minW), h = w * asp;
            if(b.h * 1.25 > h){ h = b.h * 1.25; w = h / asp; }
            if(w >= base.w) return {...base};
            const cx = b.x0 + b.w / 2, cy = b.y0 + b.h / 2;
            const v = clampView({x:cx - w/2, y:cy - h/2, w, h}, base);
            // slide toward the building so the view isn't half empty
            const pad = 2;
            if(v.w < C.w + 2*pad) v.x = Math.min(Math.max(v.x, C.x0 - pad), C.x1 + pad - v.w);
            if(v.h < C.h + 2*pad) v.y = Math.min(Math.max(v.y, C.y0 - pad), C.y1 + pad - v.h);
            return v;
        };
        const C = bboxOf([...M.polys.flatMap(p => p.outer), ...M.rooms.flatMap(r => r.pts || [])]);
        if(M.sel && M.sel.pts) return fit(bboxOf(M.sel.pts), Math.max(18, (ui.wpx || 600) / 14));
        const plan = M.fl && M.fl.plans.every(p => p.extent === 'partial') ? M.rooms.flatMap(r => r.pts || (r.lp ? [r.lp] : [])) : null;
        if(plan && plan.length) return fit(bboxOf(plan), Math.max(18, (ui.wpx || 600) / 14));
        return {...base};
    }
    function clampView(v, base){
        const w = Math.min(Math.max(v.w, base.w / 10), base.w), h = w * base.h / base.w;
        const cx = Math.min(Math.max(v.x + v.w / 2, base.x), base.x + base.w), cy = Math.min(Math.max(v.y + v.h / 2, base.y), base.y + base.h);
        return {x:cx - w/2, y:cy - h/2, w, h};
    }

    function renderFloor(){
        const d = ui.data; if(!d) return;
        const M = floorModel();
        ui.model = M;
        ui.base = extentOf(M);
        const theme = planTheme();
        const lv = M.lv;
        const levels = ui.levels.length ? ui.levels : [];
        const floorBtns = levels.length ? `<div class="smap-floors" role="group" aria-label="Floor">${levels.map(l =>
            `<button type="button" class="day-btn${l === lv ? ' active' : ''}" data-level="${esc(l)}" aria-pressed="${l === lv}">${esc(levelName(l))}${l === ui.roomFloor ? ' ★' : ''}${(d.floors || {})[l] ? ' ▦' : ''}</button>`).join('')}</div>` : '';
        const plans = M.fl ? M.fl.plans : [];
        const srcLine = plans.length
            ? plans.map(pl => `${esc(pl.title)}: <a href="${esc(pl.source.url || pl.source.page || pl.source.image || '#')}" target="_blank" rel="noopener">${esc(pl.source.publisher)}</a>, ${esc(pl.source.date || 'undated')}`).join(' · ')
            : 'No published plan for this floor: outline, doors, elevators and restrooms only';
        ui.floor.innerHTML = `${floorBtns}
            <p class="smap-roomline">${roomLine(M)}</p>
            <div class="smap-paper${theme === 'dark' ? ' pp-dark' : ''}">
                <div class="pp-head"><div><b class="pp-bname">${esc(d.name)}</b><span class="pp-lv">${esc(levelName(lv || '').toUpperCase())}</span></div>
                    <button type="button" class="pp-theme" data-theme-toggle aria-label="Switch the plan to ${theme === 'dark' ? 'paper' : 'dark'} colors">${theme === 'dark' ? '☀ Paper' : '☾ Dark'}</button></div>
                <div class="pp-mapwrap">
                    <svg class="smap-svg" role="img" aria-label="Floor plan of ${esc(d.name)}${lv ? ', ' + esc(levelName(lv)) : ''}"></svg>
                    <div class="pp-zoom"><button type="button" data-zoomby="0.6" aria-label="Zoom in">+</button><button type="button" data-zoomby="1.66" aria-label="Zoom out">−</button><button type="button" data-zoomby="0" aria-label="Whole building" title="Whole building">⤢</button></div>
                </div>
                <div class="pp-legend"></div>
                <p class="pp-src">${plans.length ? 'Rooms from ' : ''}${srcLine}.</p>
            </div>
            ${missingHtml(M)}
            <details class="smap-more"><summary>Doors, elevators and restrooms on this map</summary><ol class="smap-list"></ol></details>
            ${plans.map(planHtml).join('')}
            <p class="smap-note">${sourcesHtml()}</p>`;
        ui.svg = ui.floor.querySelector('.smap-svg');
        sizeSvg();
        ui.view = startView(M, ui.base);
        bindPan(ui.svg);
        drawPlan();
    }
    // The SVG gets an explicit height, and the view takes the panel's shape, so nothing is letterboxed.
    function sizeSvg(){
        const wrap = ui.floor.querySelector('.pp-mapwrap');
        const Wpx = (wrap && wrap.clientWidth) || 600;
        const maxH = window.innerHeight * (window.innerWidth <= 640 ? 0.56 : 0.6);
        const nat = Wpx * ui.base.h / ui.base.w;
        const Hpx = Math.max(Math.min(nat, maxH), Math.min(260, maxH));
        ui.svg.style.height = Math.round(Hpx) + 'px';
        const asp = Hpx / Wpx, B = ui.base;
        if(B.h / B.w < asp){ const h = B.w * asp; ui.base = {x:B.x, y:B.y - (h - B.h) / 2, w:B.w, h}; }
        else{ const w = B.h / asp; ui.base = {x:B.x - (w - B.w) / 2, y:B.y, w, h:B.h}; }
        ui.wpx = Wpx;
    }

    // Draws the plan for the current view. Called again after a zoom so labels fit the new scale.
    function drawPlan(){
        const M = ui.model, v = ui.view, svg = ui.svg;
        if(!M || !svg) return;
        const {P, polys, rooms, walls, feats, sel} = M;
        const u = 11 * v.w / (ui.wpx || 600);   // symbol unit: about 11 screen pixels
        ui.u = u;
        svg.setAttribute('viewBox', `${f2(v.x)} ${f2(v.y)} ${f2(v.w)} ${f2(v.h)}`);
        svg.classList.toggle('pp-zoomed', v.w < ui.base.w * 0.98);
        const B = ui.base;
        const outlineD = polys.map(p => [p.outer, ...p.holes].map(ringD).join('')).join('');
        const hasWalls = walls.length > 0;
        let s = `<defs><pattern id="ppHatch" width="1.2" height="1.2" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="1.2" height="1.2" class="pp-hatchbg"/><line x1="0" y1="0" x2="0" y2="1.2" class="pp-hatch"/></pattern><clipPath id="ppBldg"><path d="${outlineD}" clip-rule="evenodd"/></clipPath></defs>`;
        s += `<rect x="${f2(B.x - B.w)}" y="${f2(B.y - B.h)}" width="${f2(B.w * 3)}" height="${f2(B.h * 3)}" class="pp-out"/>`;
        s += `<path d="${outlineD}" class="pp-in" fill-rule="evenodd"/>`;

        // rooms
        const order = {corridor:0, other:1, mech:2, room:3, restroom:4, exit:4, stair:5, elevator:5, xbox:5, pod:6};
        const drawn = rooms.filter(r => r.pts).sort((a, b) => (order[a.kind] ?? 3) - (order[b.kind] ?? 3));
        let labels = '';
        for(const r of drawn){
            const isSel = r === sel;
            const d = ringD(r.pts) + r.holesPts.map(ringD).join('');
            const cls = `pp-r pp-${r.kind}${hasWalls ? ' pp-thin' : ''}${isSel ? ' pp-sel' : ''}`;
            const title = esc([r.name || ROOM_KIND[r.kind], r.ref && r.ref !== r.name ? r.ref : ''].filter(Boolean).join(' · '));
            s += `<path d="${d}" class="${cls}" fill-rule="evenodd"><title>${title}</title></path>`;
            if(r.kind === 'stair') s += stairTreads(r, u);
            if(r.kind === 'xbox'){
                const p = r.pts;
                if(p.length === 4) s += `<path d="M${f2(p[0][0])} ${f2(p[0][1])}L${f2(p[2][0])} ${f2(p[2][1])}M${f2(p[1][0])} ${f2(p[1][1])}L${f2(p[3][0])} ${f2(p[3][1])}" class="pp-xline"/>`;
            }
            if(r.kind === 'elevator'){
                const b = bboxOf(r.pts), k = Math.min(Math.min(b.w, b.h) * .32, u * 1.1);
                if(k > u * .25) s += SYM.elevator(r.lp[0], r.lp[1], k).replace('class="pp-elev"', 'class="pp-elev pp-noline"');
            }
            labels += roomLabel_(r, u, isSel);
        }
        // exits and connections the plan names ("to Molecular Sciences"): an EXIT tag pointing out, with where it goes
        const exitPts = [];
        for(const r of rooms.filter(r => !r.pts && r.lp && r.kind === 'exit')){
            const w = nearestWall(r.lp, polys);
            const p = w && w.dist < 6 ? w.p : r.lp, n = w ? w.n : [0, -1];
            exitPts.push(p);
            labels += exitTag(p[0], p[1], u, n[0], n[1]) + `<g><title>${esc(r.name || r.label || 'Exit')}</title></g>`;
            if(r.label){
                const tx = p[0] + n[0] * u * (Math.abs(n[0]) > .5 ? 5.2 : 2.6), ty = p[1] + n[1] * u * 2.6 + (n[1] >= -.5 ? u * 1.6 : -u * .3);
                labels += `<text x="${f2(tx)}" y="${f2(ty)}" class="pp-lbl pp-exitlbl" font-size="${f2(u*.85)}">${esc(r.label)}</text>`;
            }
        }
        for(const r of rooms.filter(r => !r.pts && r.lp && r.kind !== 'exit')){
            const isSel = r === sel;
            s += `<circle cx="${f2(r.lp[0])}" cy="${f2(r.lp[1])}" r="${f2(Math.max(u*.55, .5))}" class="pp-pod${isSel ? ' pp-sel' : ''}"><title>${esc(r.name || r.label || '')}</title></circle>`;
            if(r.label && (u < 1.2 || isSel)) labels += `<text x="${f2(r.lp[0])}" y="${f2(r.lp[1] - u*.85)}" class="pp-lbl${isSel ? ' pp-lblsel' : ''}" font-size="${f2(u*.8)}">${esc(r.label)}</text>`;
        }
        // walls: plan walls thin, the outline thick (fire plan style)
        if(hasWalls) s += `<path d="${walls.map(w => 'M' + w.map(q => f2(q[0]) + ' ' + f2(q[1])).join('L')).join('')}" class="pp-wall"/>`;
        s += `<path d="${outlineD}" class="pp-outer" fill-rule="evenodd"/>`;

        // doors, stairs, elevators, restrooms from the map sources
        const list = [];
        let n = 0;
        const ordered = feats.slice().sort((a, b) => ['entrance','stairs','steps','elevator','restroom'].indexOf(a.t) - ['entrance','stairs','steps','elevator','restroom'].indexOf(b.t));
        const tagged = [];
        let marks = '';
        for(const f of ordered){
            if(f.t === 'steps'){
                const pts = f.line.map(q => P(...q));
                marks += `<polyline points="${ptsAttr(pts)}" class="pp-steps" stroke-width="${f2(u*.9)}"><title>${esc(f.note || 'Steps')}</title></polyline>`;
                continue;
            }
            const [x, y] = P(f.lat, f.lon);
            f._n = ++n;
            const title = `<title>${esc(f.name || KIND[f.t])}</title>`;
            if(f.t === 'restroom'){
                const hit = rooms.find(r => r.pts && !['other', 'corridor'].includes(r.kind) && pip([x, y], r.pts));
                if(hit && hit.kind === 'restroom'){ f._in = true; list.push(f); continue; }
                if(hit){ f._conflict = `not drawn: this point falls inside ${hit.name || hit.label || 'another room'} on the published plan`; list.push(f); continue; }
            }
            if(f.t === 'elevator' && rooms.some(r => r.pts && (r.kind === 'elevator' || r.kind === 'xbox') && Math.hypot(r.lp[0] - x, r.lp[1] - y) < 8)){
                f._conflict = 'the traced plan draws the elevator here'; list.push(f); continue;
            }
            if(f.t === 'entrance'){
                const w = nearestWall([x, y], polys);
                if(w && w.dist <= 3){
                    marks += doorAt(w, u) + `<g>${title}</g>`;
                    if(!tagged.some(t => Math.hypot(t[0] - w.p[0], t[1] - w.p[1]) < u * 4) && !exitPts.some(t => Math.hypot(t[0] - w.p[0], t[1] - w.p[1]) < u * 4)){
                        tagged.push(w.p);
                        marks += exitTag(w.p[0], w.p[1], u, w.n[0], w.n[1]);
                    }
                }else{
                    marks += `<circle cx="${f2(x)}" cy="${f2(y)}" r="8" class="pp-approx"/>${SYM.door(x, y, u)}`;
                }
            }else{
                if(f.acc === 'approx') marks += `<circle cx="${f2(x)}" cy="${f2(y)}" r="8" class="pp-approx"/>`;
                marks += `<g class="${f.acc === 'approx' ? 'pp-isapprox' : ''}">${(SYM[f.t] || SYM.door)(x, y, u)}${title}</g>`;
            }
            marks += `<text x="${f2(x + u*1.3)}" y="${f2(y - u*1.0)}" class="pp-num" font-size="${f2(u*1.1)}">${f._n}</text>`;
            list.push(f);
        }
        s += marks + labels;
        // selected room that is too small to see: ring it
        if(sel && sel.pts){
            const b = bboxOf(sel.pts);
            if(Math.max(b.w, b.h) < u * 2.5) s += `<circle cx="${f2(sel.lp[0])}" cy="${f2(sel.lp[1])}" r="${f2(u*2.2)}" class="pp-selring"/>`;
        }
        // north arrow + scale bar, pinned to the view corners
        const nx = v.x + v.w - u*3, ny = v.y + u*3.4;
        s += `<g class="pp-northg"><circle cx="${f2(nx)}" cy="${f2(ny)}" r="${f2(u*2.1)}" class="pp-northc"/><path d="M${f2(nx)} ${f2(ny - u*1.7)}l${f2(u*.9)} ${f2(u*2.6)}l${f2(-u*.9)} ${f2(-u*.75)}z" class="pp-inkfill"/><path d="M${f2(nx)} ${f2(ny - u*1.7)}l${f2(-u*.9)} ${f2(u*2.6)}l${f2(u*.9)} ${f2(-u*.75)}z" class="pp-north2"/><text x="${f2(nx)}" y="${f2(ny + u*3.6)}" class="pp-lbl pp-strong" font-size="${f2(u*1.4)}">N</text></g>`;
        const nice = [2, 5, 10, 20, 25, 50, 100, 200];
        const L = nice.reduce((a, b) => Math.abs(b - v.w/5) < Math.abs(a - v.w/5) ? b : a);
        const sx = v.x + u*2, sy = v.y + v.h - u*2.2, bh = u*.55;
        s += `<g class="pp-scaleg"><rect x="${f2(sx - u*.6)}" y="${f2(sy - u*2.6)}" width="${f2(L + u*1.2)}" height="${f2(u*3.5)}" class="pp-scalebg"/><rect x="${f2(sx)}" y="${f2(sy - bh)}" width="${f2(L/2)}" height="${f2(bh)}" class="pp-inkfill"/><rect x="${f2(sx + L/2)}" y="${f2(sy - bh)}" width="${f2(L/2)}" height="${f2(bh)}" class="pp-scalewhite"/><text x="${f2(sx + L/2)}" y="${f2(sy - u*1.0)}" class="pp-lbl" font-size="${f2(u*1.15)}">${L} m · ${ft(L)} ft</text></g>`;
        s += `<g class="m-you"></g>`;
        svg.innerHTML = s;

        // legend: only what this view shows
        const has = k => rooms.some(r => r.kind === k);
        const key = (sym, label) => `<span><svg viewBox="-2 -2 4 4" aria-hidden="true">${sym}</svg>${label}</span>`;
        const items = [];
        if(feats.some(f => f.t === 'entrance') || has('exit')) items.push(key(`<rect x="-1.9" y="-1" width="3.8" height="2" rx=".3" class="pp-exitfill"/><text x="0" y=".55" class="pp-exittxt" font-size="1.3">EXIT</text>`, 'Exit (door)'));
        if(has('stair') || feats.some(f => f.t === 'stairs')) items.push(key(SYM.stairs(0, 0, 1.6), 'Stairs'));
        if(has('elevator') || feats.some(f => f.t === 'elevator')) items.push(key(SYM.elevator(0, 0, 1.6), 'Elevator'));
        if(has('restroom') || feats.some(f => f.t === 'restroom')) items.push(key(SYM.restroom(0, 0, 1.6), 'Restroom'));
        if(sel) items.push(key(`<rect x="-1.7" y="-1.7" width="3.4" height="3.4" class="pp-r pp-room pp-sel"/>`, 'Your room'));
        items.push(key(`<circle r="1" class="pp-me"/>`, 'You are here'));
        if(rooms.some(r => r.pts && r.kind === 'room')) items.push(key(`<rect x="-1.7" y="-1.7" width="3.4" height="3.4" class="pp-r pp-room"/>`, 'Room'));
        if(has('corridor')) items.push(key(`<rect x="-1.7" y="-1.7" width="3.4" height="3.4" class="pp-corridor"/>`, 'Corridor'));
        if(has('mech') || has('other')) items.push(key(`<rect x="-1.7" y="-1.7" width="3.4" height="3.4" class="pp-r pp-other"/>`, 'Unlabeled / service'));
        if(has('xbox')) items.push(key(`<rect x="-1.7" y="-1.7" width="3.4" height="3.4" class="pp-r pp-xbox"/><path d="M-1.7 -1.7L1.7 1.7M1.7 -1.7L-1.7 1.7" class="pp-xline"/>`, 'Crossed box (no legend)'));
        if(has('pod')) items.push(key(`<circle r="1" class="pp-pod"/>`, 'Table / pod'));
        if(feats.some(f => f.acc === 'approx')) items.push(key(`<circle r="1.6" class="pp-approx pp-approxkey"/>`, 'Approximate spot'));
        ui.floor.querySelector('.pp-legend').innerHTML = items.join('');
        ui.floor.querySelector('.smap-list').innerHTML = list.map(f => `<li value="${f._n}"><b>${esc(f.name || KIND[f.t])}</b>${featDetail(f)}</li>`).join('');
        redrawLocation();
    }

    function roomLabel_(r, u, isSel){
        const txt = r.kind === 'restroom' ? (r.label || 'WC') : r.label;
        if(!txt) return '';
        const b = bboxOf(r.pts);
        const F = frameOf(r.pts);
        const along = F.s1 - F.s0, across = F.t1 - F.t0;
        const len = txt.length * 0.6 + 0.3;
        // horizontal, or along the room's long side when that fits much better
        let fs = Math.min(u * 1.15, b.w * .88 / len, b.h * .55), rot = 0;
        const fsR = Math.min(u * 1.15, along * .88 / len, across * .55);
        const deg = F.ang * 180 / Math.PI;
        const tilt = ((deg % 180) + 180) % 180;
        if(fsR > fs * 1.4 && !(tilt < 20 || tilt > 160)){ fs = fsR; rot = tilt > 90 ? tilt - 180 : tilt; }
        if(fs < u * 0.5 && !isSel) return '';
        if(isSel) fs = Math.max(fs, u * 0.8);
        const [x, y] = r.lp;
        const sub = r.kind === 'restroom' && r.ref && fs > u * .55 ? `<tspan x="${f2(x)}" dy="${f2(fs*1.05)}" font-size="${f2(fs*.7)}">${esc(r.ref)}</tspan>` : '';
        return `<text x="${f2(x)}" y="${f2(y + fs*.35 - (sub ? fs*.45 : 0))}" class="pp-lbl${isSel ? ' pp-lblsel' : ''}${r.kind === 'restroom' ? ' pp-wclbl' : ''}" font-size="${f2(fs)}"${rot ? ` transform="rotate(${f2(rot)} ${f2(x)} ${f2(y)})"` : ''}>${esc(txt)}${sub}</text>`;
    }

    // stair symbol: treads across the run, a line up the middle when it's wide enough for two flights
    function stairTreads(r, u){
        const F = frameOf(r.pts);
        const L = F.s1 - F.s0, Wd = F.t1 - F.t0;
        const step = Math.max(0.28, L / 16);
        const a = F.s0 + L * .14, b = F.s1 - L * .14;
        let d = '';
        for(let s = a; s <= b + 1e-6; s += step){
            const p = at(F, s, F.t0), q = at(F, s, F.t1);
            d += `M${f2(p[0])} ${f2(p[1])}L${f2(q[0])} ${f2(q[1])}`;
        }
        if(Wd > 2.0){ const m = (F.t0 + F.t1) / 2, p = at(F, a, m), q = at(F, b, m); d += `M${f2(p[0])} ${f2(p[1])}L${f2(q[0])} ${f2(q[1])}`; }
        const id = 'ppc' + Math.random().toString(36).slice(2, 8);
        return `<clipPath id="${id}"><path d="${ringD(r.pts)}"/></clipPath><path d="${d}" class="pp-tread" clip-path="url(#${id})"/>`;
    }

    function nearestWall(p, polys){
        let best = null;
        for(const poly of polys) for(const ring of [poly.outer, ...poly.holes]) for(let i = 0; i < ring.length; i++){
            const a = ring[i], b = ring[(i + 1) % ring.length];
            const dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx*dx + dy*dy;
            if(!L2) continue;
            const t = Math.max(0, Math.min(1, ((p[0]-a[0])*dx + (p[1]-a[1])*dy) / L2));
            const q = [a[0] + t*dx, a[1] + t*dy], dist = Math.hypot(p[0]-q[0], p[1]-q[1]);
            if(!best || dist < best.dist){
                const L = Math.sqrt(L2);
                let n = [dy / L, -dx / L];
                const probe = [q[0] + n[0] * .5, q[1] + n[1] * .5];
                if(polys.some(pp => pip(probe, pp.outer) && !pp.holes.some(h => pip(probe, h)))) n = [-n[0], -n[1]];
                best = {p:q, dist, d:[dx / L, dy / L], n};
            }
        }
        return best;
    }
    // a door: a gap in the outer wall with a door leaf and its swing, inside
    function doorAt(w, u){
        const r = Math.max(1.0, u * .9), [px, py] = w.p, [dx, dy] = w.d, [nx, ny] = w.n;
        const h = [px - dx*r/2, py - dy*r/2], e = [px + dx*r/2, py + dy*r/2];
        const leaf = [h[0] - nx*r, h[1] - ny*r];
        return `<path d="M${f2(h[0])} ${f2(h[1])}L${f2(e[0])} ${f2(e[1])}" class="pp-gap"/>`
            + `<path d="M${f2(h[0])} ${f2(h[1])}L${f2(leaf[0])} ${f2(leaf[1])}A${f2(r)} ${f2(r)} 0 0 ${dx*ny - dy*nx > 0 ? 1 : 0} ${f2(e[0])} ${f2(e[1])}" class="pp-swing"/>`;
    }

    // ---- pan and zoom ----
    function zoomBy(k, cx, cy){
        const v = ui.view, B = ui.base;
        if(!k){ ui.view = {...B}; drawPlan(); return; }
        if(cx == null){ cx = v.x + v.w / 2; cy = v.y + v.h / 2; }
        const w = v.w * k, h = v.h * k;
        ui.view = clampView({x:cx - (cx - v.x) * k, y:cy - (cy - v.y) * k, w, h}, B);
        drawPlan();
    }
    function bindPan(svg){
        const pts = new Map();
        let start = null, moved = false, timer = null;
        const toM = (ev) => { const r = svg.getBoundingClientRect(); const v = ui.view; return [v.x + (ev.clientX - r.left) / r.width * v.w, v.y + (ev.clientY - r.top) / r.height * v.h]; };
        svg.addEventListener('pointerdown', ev => {
            if(ev.pointerType === 'mouse' && ev.button !== 0) return;
            if(ev.pointerType !== 'mouse' && ui.view.w >= ui.base.w * 0.98 && pts.size === 0){ pts.set(ev.pointerId, {x:ev.clientX, y:ev.clientY}); start = null; return; }
            pts.set(ev.pointerId, {x:ev.clientX, y:ev.clientY});
            try{ svg.setPointerCapture(ev.pointerId); }catch(e){}
            start = {view:{...ui.view}, pts:new Map([...pts].map(([k, p]) => [k, {...p}]))};
            moved = false;
        });
        svg.addEventListener('pointermove', ev => {
            if(!pts.has(ev.pointerId) || !start) return;
            pts.set(ev.pointerId, {x:ev.clientX, y:ev.clientY});
            const r = svg.getBoundingClientRect(), v0 = start.view;
            const now = [...pts.values()], was = [...pts.keys()].map(k => start.pts.get(k) || pts.get(k));
            if(now.length >= 2){
                const d1 = Math.hypot(now[0].x - now[1].x, now[0].y - now[1].y), d0 = Math.hypot(was[0].x - was[1].x, was[0].y - was[1].y);
                if(d0 > 10 && d1 > 10){
                    const k = d0 / d1, mx = (now[0].x + now[1].x) / 2, my = (now[0].y + now[1].y) / 2;
                    const m0x = (was[0].x + was[1].x) / 2, m0y = (was[0].y + was[1].y) / 2;
                    const gx = v0.x + (m0x - r.left) / r.width * v0.w, gy = v0.y + (m0y - r.top) / r.height * v0.h;
                    const w = v0.w * k, h = v0.h * k;
                    ui.view = clampView({x:gx - (mx - r.left) / r.width * w, y:gy - (my - r.top) / r.height * h, w, h}, ui.base);
                    svg.setAttribute('viewBox', `${ui.view.x} ${ui.view.y} ${ui.view.w} ${ui.view.h}`);
                    moved = true;
                }
                return;
            }
            const dx = (now[0].x - was[0].x) / r.width * v0.w, dy = (now[0].y - was[0].y) / r.height * v0.h;
            if(Math.abs(now[0].x - was[0].x) + Math.abs(now[0].y - was[0].y) > 3) moved = true;
            ui.view = clampView({x:v0.x - dx, y:v0.y - dy, w:v0.w, h:v0.h}, ui.base);
            svg.setAttribute('viewBox', `${ui.view.x} ${ui.view.y} ${ui.view.w} ${ui.view.h}`);
        });
        const end = ev => {
            pts.delete(ev.pointerId);
            if(start && moved && pts.size === 0){ start = null; drawPlan(); }
            else if(pts.size) start = {view:{...ui.view}, pts:new Map([...pts].map(([k, p]) => [k, {...p}]))};
            else start = null;
        };
        svg.addEventListener('pointerup', end);
        svg.addEventListener('pointercancel', end);
        svg.addEventListener('dblclick', ev => { const [x, y] = toM(ev); zoomBy(0.5, x, y); });
        svg.addEventListener('wheel', ev => {
            if(!ev.ctrlKey && !ev.metaKey) return;   // plain wheel scrolls the dialog; pinch/ctrl+wheel zooms
            ev.preventDefault();
            const [x, y] = toM(ev);
            const v = ui.view, k = Math.exp(ev.deltaY * 0.01);
            ui.view = clampView({x:x - (x - v.x) * k, y:y - (y - v.y) * k, w:v.w * k, h:v.h * k}, ui.base);
            svg.setAttribute('viewBox', `${ui.view.x} ${ui.view.y} ${ui.view.w} ${ui.view.h}`);
            clearTimeout(timer); timer = setTimeout(drawPlan, 120);
        }, {passive:false});
    }

    function featDetail(f){
        const bits = [];
        if(f.name && f.t !== 'restroom') bits.push(KIND[f.t].toLowerCase());
        if(f.note) bits.push(esc(f.note));
        if(f._in) bits.push('drawn as the restroom on the published plan');
        if(f._conflict) bits.push(`<em>${esc(f._conflict)}</em>`);
        if(f.side) bits.push(`${esc(f.side)} side`);
        if(f.t === 'entrance' || (f.t === 'restroom' && !f.floor)) bits.push(f.floor ? `opens on ${esc(levelName(f.floor)).toLowerCase()}` : 'floor not recorded');
        if(f.osm_level) bits.push(`level ${esc(f.osm_level)} in OSM`);
        bits.push(f.acc === 'approx' ? `<em>approximate, ${esc(srcLabel(f))}</em>` : esc(srcLabel(f)));
        return ` <span>${bits.join(' · ')}</span>`;
    }

    function roomLine(M){
        const d = ui.data, room = ui.opts.room;
        if(!room) return '';
        const rl = esc(roomLabel(room));
        const f = ui.roomFloor, r = d.floor_rule || {};
        let floor;
        if(!f) floor = `Can't tell the floor from the room number`;
        else if(ui.indexFloor) floor = `${esc(levelName(f))}, from UCLA Space Inventory's room list for this building`;
        else if(r.checked && r.agree === r.checked) floor = `${esc(levelName(f))}, from the room number (UCLA's own restroom records for this building follow that rule: ${r.agree} of ${r.checked})`;
        else if(r.checked) floor = `Probably ${esc(levelName(f).toLowerCase())}, from the room number, but only ${r.agree} of ${r.checked} UCLA records here follow that rule`;
        else floor = `${esc(levelName(f))}, from the room number (UCLA's usual numbering, not confirmed for this building)`;
        if(ui.roomPos){
            const pl = (((d.floors || {})[ui.roomPos.floor] || {}).plans || []).find(p => p.file === ui.roomPos.plan) || {};
            const src = pl.source || {};
            const here = M && M.lv === ui.roomPos.floor ? 'highlighted in yellow' : `on ${esc(levelName(ui.roomPos.floor).toLowerCase())} (pick it above)`;
            return `<b>Room ${rl}</b>: ${esc(levelName(ui.roomPos.floor))}, ${here}. Position from ${esc(src.what || 'a published plan')}${src.date && !(src.what || '').includes(src.date) ? ` (${esc(src.date)})` : ''}; source under the map.`;
        }
        const traced = M && M.fl;
        const pos = traced ? ` This floor's plan doesn't show a room numbered ${rl}, so there's no room marker.` : ' Room position not mapped: no public floor plan places it, so there\'s no room marker.';
        return `<b>Room ${rl}</b>: ${floor}.${pos}`;
    }

    function missingHtml(M){
        const d = ui.data, lv = M.lv, has = t => d.features.some(f => f.t === t && f.acc !== 'building');
        const out = [];
        if(!M.fl) out.push('No public floor plan for this floor, so rooms and corridors aren\'t drawn.');
        else if(M.fl.plans.every(p => p.extent === 'partial')) out.push('The published plan covers only part of this floor; rooms outside it aren\'t drawn.');
        const listed = d.features.filter(f => f.t === 'restroom' && f.acc === 'building' && f.floor === lv);
        if(listed.length) out.push(`UCLA lists restroom${listed.length > 1 ? 's' : ''} ${listed.map(f => esc(f.room)).join(', ')} on this floor without a position.`);
        if(!M.rooms.some(r => r.kind === 'restroom') && !d.features.some(f => f.t === 'restroom' && (f.floor === lv || !f.floor) && f.acc !== 'building')) out.push('No restroom data for this floor.');
        if(!has('elevator') && !M.rooms.some(r => r.kind === 'elevator')) out.push('No elevator data (that doesn\'t mean there isn\'t one).');
        if(!M.rooms.some(r => r.kind === 'stair')){
            if(!has('stairs') && !has('steps')) out.push('No stair data.');
            else out.push('Indoor stairwells aren\'t in any public source for this floor; only stair doors and outdoor steps are drawn.');
        }
        if(!has('entrance')) out.push('No door data.');
        if(d.features.some(f => f.t === 'entrance' && !f.floor)) out.push('Exits are the building\'s outside doors; they show on every floor because the data doesn\'t say which floor each one opens onto. Follow the posted evacuation signs in an emergency.');
        if(has('elevator')) out.push('Elevators show on every floor; which floors each one stops at isn\'t recorded.');
        if(d.features.some(f => f.osm_level)) out.push('"Level N in OSM" is OpenStreetMap\'s level tag, which doesn\'t always match UCLA\'s floor numbers.');
        return `<p class="smap-note smap-missing">${out.join(' ')}</p>`;
    }

    function planHtml(pl){
        const src = pl.source || {}, fit = pl.fit || {};
        const link = src.url || src.page || src.image;
        const notes = (pl.notes || []).map(esc).join(' ');
        return `<p class="smap-note"><b>${esc(pl.title)}</b>: traced from <a href="${esc(link)}" target="_blank" rel="noopener">${esc(src.publisher)}: ${esc(src.what || 'plan')}</a> (${esc(src.date || 'undated')}). ${esc(pl.how || '')} Fit error at the control points: up to ${esc(fit.max_m)} m${fit.loo_max_m != null ? ` (${esc(fit.loo_max_m)} m when each point is left out of the fit)` : ''}. ${notes}</p>`;
    }

    function sourcesHtml(){
        const d = ui.data, o = d.outline, S = index.sources;
        const link = k => `<a href="${esc(S[k].url)}" target="_blank" rel="noopener">${esc(S[k].label)}</a>`;
        const a = o.agree_pct || {};
        const agree = [a.ucla != null ? `${a.ucla}% overlap with UCLA's GIS outline` : '', a.c3d != null ? `${a.c3d}% with UCLA's interactive map` : ''].filter(Boolean).join(', ');
        const weak = (a.ucla != null && a.ucla < 70) || (a.c3d != null && a.c3d < 70);
        let s = `Outline (the thick outer wall): ${link(o.src)}${agree ? ` (${agree})` : ''}.${weak ? ' The sources disagree on this outline, so treat its shape as approximate.' : ''} The outline is the building's footprint; upper floors can be smaller.`;
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
            if(g && ui.view){
                const v = ui.view, u = ui.u;
                const inView = true;
                const tw = u * 9.2, th = u * 1.9;
                if(inView && me[0] > v.x && me[0] < v.x + v.w && me[1] > v.y && me[1] < v.y + v.h){
                    const below = me[1] - v.y < u * 5;
                    const tx = Math.min(Math.max(me[0] - tw / 2, v.x + u * .5), v.x + v.w - tw - u * .5);
                    const ty = below ? me[1] + u * 1.6 : me[1] - u * 1.6 - th;
                    g.innerHTML = `<circle cx="${f2(me[0])}" cy="${f2(me[1])}" r="${f2(acc)}" class="pp-acc"/>`
                        + `<path d="M${f2(me[0])} ${f2(me[1])}L${f2(Math.min(Math.max(me[0], tx + u), tx + tw - u))} ${f2(below ? ty : ty + th)}" class="pp-meline"/>`
                        + `<rect x="${f2(tx)}" y="${f2(ty)}" width="${f2(tw)}" height="${f2(th)}" rx="${f2(u*.3)}" class="pp-metag"/>`
                        + `<text x="${f2(tx + tw/2)}" y="${f2(ty + th*.7)}" class="pp-metxt" font-size="${f2(th*.62)}">YOU ARE HERE</text>`
                        + SYM.you(me[0], me[1], u);
                }else if(dist < 400){
                    // off this view: an arrow on the edge pointing at you
                    const cx = v.x + v.w / 2, cy = v.y + v.h / 2, dx = me[0] - cx, dy = me[1] - cy;
                    const k = Math.min((v.w / 2 - u * 1.5) / Math.abs(dx || 1e-9), (v.h / 2 - u * 1.5) / Math.abs(dy || 1e-9));
                    const ex = cx + dx * k, ey = cy + dy * k, L = Math.hypot(dx, dy), ux = dx / L, uy = dy / L;
                    const tip = [ex + ux * u, ey + uy * u], b1 = [ex - uy * u * .7, ey + ux * u * .7], b2 = [ex + uy * u * .7, ey - ux * u * .7];
                    const tx = Math.min(Math.max(ex - ux * u * 1.2 - tw / 2, v.x + u * .5), v.x + v.w - tw - u * .5);
                    const ty = Math.min(Math.max(ey - uy * u * 2.4 - th / 2, v.y + u * .5), v.y + v.h - th - u * .5);
                    g.innerHTML = `<circle cx="${f2(me[0])}" cy="${f2(me[1])}" r="${f2(acc)}" class="pp-acc"/>`
                        + `<path d="M${f2(tip[0])} ${f2(tip[1])}L${f2(b1[0])} ${f2(b1[1])}L${f2(b2[0])} ${f2(b2[1])}Z" class="pp-metag"/>`
                        + `<rect x="${f2(tx)}" y="${f2(ty)}" width="${f2(tw)}" height="${f2(th)}" rx="${f2(u*.3)}" class="pp-metag"/>`
                        + `<text x="${f2(tx + tw/2)}" y="${f2(ty + th*.7)}" class="pp-metxt" font-size="${f2(th*.62)}">YOU ARE HERE</text>`;
                }
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

    let resizeT = null;
    window.addEventListener('resize', () => {
        clearTimeout(resizeT);
        resizeT = setTimeout(() => { if(ui.root && !ui.root.hidden && ui.tab === 'floor' && ui.data) renderFloor(); }, 200);
    });

    window.StudyMaps = {open, close, floorOf};
})();
