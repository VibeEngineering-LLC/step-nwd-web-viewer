const SHARE = (() => {
  const CB = ['xray', 'grid', 'edges', 'clipOn', 'clipFlip', 'secFlip', 'secFill', 'secBounds'];
  const RG = ['explode', 'clipPos', 'secPos', 'secBoxxMin', 'secBoxxMax', 'secBoxyMin', 'secBoxyMax', 'secBoxzMin', 'secBoxzMax'];
  const SG = [['secMode', 'mode'], ['secAxis', 'axis'], ['clipAxis', 'axis']];
  const BOX = ['secBoxxMin', 'secBoxxMax', 'secBoxyMin', 'secBoxyMax', 'secBoxzMin', 'secBoxzMax'];
  const DEF = {};
  let inited = false;
  let timer = 0;

  function packIdx(arr) {
    const a = [...arr].sort((x, y) => x - y);
    const out = [];
    let i = 0;
    while (i < a.length) {
      let j = i;
      while (j + 1 < a.length && a[j + 1] === a[j] + 1) j++;
      if (j - i + 1 >= 3) out.push(a[i] + '-' + a[j]);
      else for (let k = i; k <= j; k++) out.push(String(a[k]));
      i = j + 1;
    }
    return out.join('.');
  }

  function unpackIdx(str) {
    const out = [];
    if (!str) return out;
    for (const t of String(str).split('.')) {
      if (!t) continue;
      const m = t.match(/^(\d+)-(\d+)$/);
      if (m) {
        const a = +m[1], b = +m[2];
        for (let k = a; k <= b; k++) out.push(k);
      } else if (/^\d+$/.test(t)) out.push(+t);
    }
    return out;
  }

  function sgActive(id, attr) {
    const el = $(id);
    if (!el) return null;
    const b = el.querySelector('button[data-' + attr + '].active');
    return b ? b.getAttribute('data-' + attr) : null;
  }

  function encode() {
    const q = [];
    const cam = [
      camera.position.x, camera.position.y, camera.position.z,
      controls.target.x, controls.target.y, controls.target.z
    ].map(n => (Math.round(n * 1000) / 1000).toString()).join(',');
    q.push('c=' + cam);
    const hid = [];
    for (let i = 0; i < parts.length; i++) if (!parts[i].object.visible) hid.push(i);
    if (hid.length) q.push('h=' + packIdx(hid));
    const sel = [];
    for (let i = 0; i < parts.length; i++) if (selection.has(parts[i])) sel.push(i);
    if (sel.length) q.push('s=' + packIdx(sel));
    for (const id of CB) {
      const el = $(id);
      if (!el) continue;
      const v = el.checked ? '1' : '0';
      if (v !== DEF[id]) q.push(id + '=' + v);
    }
    for (const id of RG) {
      const el = $(id);
      if (!el) continue;
      if (el.value !== DEF[id]) q.push(id + '=' + el.value);
    }
    for (const [id, attr] of SG) {
      const v = sgActive(id, attr);
      if (v !== null && v !== DEF[id]) q.push(id + '=' + v);
    }
    return q.join('&');
  }

  function save() {
    try {
      const s = encode();
      history.replaceState(null, '', s ? '#' + s : location.pathname + location.search);
    } catch (e) { /* ignore */ }
  }

  function restore() {
    const raw = location.hash.replace(/^#/, '');
    if (!raw) return false;
    const map = {};
    for (const pair of raw.split('&')) {
      if (!pair) continue;
      const eq = pair.indexOf('=');
      if (eq < 0) continue;
      try {
        map[decodeURIComponent(pair.slice(0, eq))] = decodeURIComponent(pair.slice(eq + 1));
      } catch (e) { /* ignore */ }
    }
    let camOk = false;
    try {
      for (const [id, attr] of SG) {
        if (!(id in map)) continue;
        const el = $(id);
        if (!el) continue;
        const b = el.querySelector('button[data-' + attr + '="' + map[id] + '"]');
        if (b) b.click();
      }
    } catch (e) { /* ignore */ }
    try {
      for (const id of RG) {
        if (!(id in map)) continue;
        const el = $(id);
        if (!el) continue;
        el.value = map[id];
        el.dispatchEvent(new Event('input', { bubbles: true }));
      }
      for (const id of BOX) {
        if (!(id in map)) continue;
        const el = $(id);
        if (!el) continue;
        el.value = map[id];
        el.dispatchEvent(new Event('input', { bubbles: true }));
      }
    } catch (e) { /* ignore */ }
    try {
      for (const id of CB) {
        if (!(id in map)) continue;
        const el = $(id);
        if (!el) continue;
        el.checked = map[id] === '1';
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }
    } catch (e) { /* ignore */ }
    try {
      for (let i = 0; i < parts.length; i++) parts[i].object.visible = true;
      if ('h' in map) {
        for (const i of unpackIdx(map.h)) {
          if (i >= 0 && i < parts.length) parts[i].object.visible = false;
        }
      }
      const byUuid = new Map();
      for (const p of parts) byUuid.set(p.object.uuid, p);
      for (const row of document.querySelectorAll('.row[data-parts]')) {
        const eye = row.querySelector('.eye');
        if (!eye) continue;
        const ids = row.getAttribute('data-parts').split(',');
        let any = false;
        for (const u of ids) {
          const p = byUuid.get(u);
          if (p && p.object.visible) { any = true; break; }
        }
        eye.classList.toggle('off', !any);
      }
    } catch (e) { /* ignore */ }
    try {
      const list = [];
      if ('s' in map) {
        for (const i of unpackIdx(map.s)) {
          if (i >= 0 && i < parts.length) list.push(parts[i]);
        }
      }
      setSelection(list);
      updateTreeSelection();
    } catch (e) { /* ignore */ }
    try {
      if ('c' in map) {
        const n = map.c.split(',').map(Number);
        if (n.length === 6 && n.every(Number.isFinite)) {
          camAnim = null;
          camera.position.set(n[0], n[1], n[2]);
          controls.target.set(n[3], n[4], n[5]);
          controls.update();
          camOk = true;
        }
      }
    } catch (e) { /* ignore */ }
    return camOk;
  }

  function init() {
    if (inited) return;
    inited = true;
    for (const id of CB) {
      const el = $(id);
      if (el) DEF[id] = el.checked ? '1' : '0';
    }
    for (const id of RG) {
      const el = $(id);
      if (el) DEF[id] = el.value;
    }
    for (const [id, attr] of SG) {
      const v = sgActive(id, attr);
      if (v !== null) DEF[id] = v;
    }
    const vb = $('viewbar');
    if (vb && !$('btnShare')) {
      const sep = document.createElement('span');
      sep.className = 'sep';
      sep.setAttribute('aria-hidden', 'true');
      const btn = document.createElement('button');
      btn.id = 'btnShare';
      btn.type = 'button';
      btn.textContent = 'Ссылка';
      btn.addEventListener('click', () => {
        const s = encode();
        const url = location.href.split('#')[0] + (s ? '#' + s : '');
        const done = () => {
          btn.textContent = 'Скопировано';
          setTimeout(() => { btn.textContent = 'Ссылка'; }, 1500);
        };
        try {
          navigator.clipboard.writeText(url).then(done, () => {
            window.prompt('Скопируйте ссылку', url);
            done();
          });
        } catch (e) {
          window.prompt('Скопируйте ссылку', url);
          done();
        }
      });
      vb.appendChild(sep);
      vb.appendChild(btn);
    }
    const deb = () => {
      clearTimeout(timer);
      timer = setTimeout(save, 700);
    };
    document.addEventListener('input', deb, true);
    document.addEventListener('change', deb, true);
    document.addEventListener('click', deb, true);
    controls.addEventListener('end', deb);
    window.addEventListener('hashchange', () => restore());
  }

  return { init, restore };
})();
