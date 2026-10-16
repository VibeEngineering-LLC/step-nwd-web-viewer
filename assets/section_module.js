const SECTION_CAP_UNIFORM = { value: new THREE.Color(0xd9534f) };
const SECTION_CAPON_UNIFORM = { value: 0 };

const SECTION = (() => {
  const state = {
    mode: 'none',
    axis: 'y',
    pos: 0,
    flip: false,
    box: { x: [0, 1], y: [0, 1], z: [0, 1] },
    fill: false,
    bounds: true
  };

  const planes = [
    new THREE.Plane(new THREE.Vector3(1, 0, 0), 0),
    new THREE.Plane(new THREE.Vector3(-1, 0, 0), 0),
    new THREE.Plane(new THREE.Vector3(0, 1, 0), 0),
    new THREE.Plane(new THREE.Vector3(0, -1, 0), 0),
    new THREE.Plane(new THREE.Vector3(0, 0, 1), 0),
    new THREE.Plane(new THREE.Vector3(0, 0, -1), 0)
  ];

  const PATCHED = new WeakSet();
  let helperGroup = null;
  let planeHelper = null;
  let boxHelper = null;
  let el = null;
  let empty = false;

  const fmt = (v) => Number(v).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' м';
  const axisVec = (a) => a === 'x' ? [1, 0, 0] : a === 'y' ? [0, 1, 0] : [0, 0, 1];
  const axisSpan = (a) => {
    const b = modelBox;
    return Math.max(0.01, b.max[a] - b.min[a]);
  };
  const axisMid = (a) => (modelBox.min[a] + modelBox.max[a]) / 2;

  function computePlanes() {
    if (state.mode === 'none') return [];
    if (state.mode === 'plane') {
      const [nx, ny, nz] = axisVec(state.axis);
      let n = [nx, ny, nz];
      if (state.axis === 'y') {
        n = state.flip ? [0, 1, 0] : [0, -1, 0];
      } else {
        n = state.flip ? [nx, ny, nz] : [-nx, -ny, -nz];
      }
      const c = -(n[0] * state.pos + n[1] * state.pos + n[2] * state.pos);
      planes[0].normal.set(n[0], n[1], n[2]);
      planes[0].constant = c;
      return [planes[0]];
    }
    const b = state.box;
    planes[0].normal.set(1, 0, 0); planes[0].constant = -b.x[0];
    planes[1].normal.set(-1, 0, 0); planes[1].constant = b.x[1];
    planes[2].normal.set(0, 1, 0); planes[2].constant = -b.y[0];
    planes[3].normal.set(0, -1, 0); planes[3].constant = b.y[1];
    planes[4].normal.set(0, 0, 1); planes[4].constant = -b.z[0];
    planes[5].normal.set(0, 0, -1); planes[5].constant = b.z[1];
    return planes;
  }

  function patchMaterial(mat) {
    if (PATCHED.has(mat)) return;
    PATCHED.add(mat);
    mat.side = THREE.DoubleSide;
    mat.onBeforeCompile = (shader) => {
      shader.uniforms.secCapColor = SECTION_CAP_UNIFORM;
      shader.uniforms.secCapOn = SECTION_CAPON_UNIFORM;
      shader.fragmentShader = 'uniform vec3 secCapColor;\nuniform float secCapOn;\n' + shader.fragmentShader.replace('#include <dithering_fragment>', '#include <dithering_fragment>\n if (secCapOn > 0.5 && !gl_FrontFacing) gl_FragColor = vec4(secCapColor, 1.0);');
    };
    mat.customProgramCacheKey = () => 'seccap';
  }

  function apply() {
    if (empty) return;
    const list = computePlanes();
    SECTION_CAPON_UNIFORM.value = state.fill && list.length > 0 ? 1 : 0;
    for (const mesh of meshes) {
      const mats = allMats(mesh);
      for (const mat of mats) {
        const prev = mat.userData.secCount || 0;
        if (prev !== list.length || !PATCHED.has(mat)) {
          mat.clippingPlanes = list;
          mat.clipIntersection = false;
          SECTION.onMaterial(mat);
          mat.needsUpdate = true;
          mat.userData.secCount = list.length;
        } else {
          mat.clippingPlanes = list;
        }
      }
      if (mesh.userData.edge && mesh.userData.edge.material) {
        const em = mesh.userData.edge.material;
        const prev = em.userData.secCount || 0;
        if (prev !== list.length) {
          em.clippingPlanes = list;
          em.clipIntersection = false;
          em.needsUpdate = true;
          em.userData.secCount = list.length;
        } else {
          em.clippingPlanes = list;
        }
      }
    }
    updateHelpers();
  }

  function onMaterial(mat) {
    patchMaterial(mat);
  }

  function clearHelpers() {
    if (helperGroup) {
      scene.remove(helperGroup);
      helperGroup.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) o.material.dispose();
      });
      helperGroup = null;
    }
    planeHelper = null;
    boxHelper = null;
  }

  function updateHelpers() {
    clearHelpers();
    if (!state.bounds || state.mode === 'none') return;
    helperGroup = new THREE.Group();
    if (state.mode === 'plane') {
      const b = modelBox;
      const a = state.axis;
      let w, h;
      if (a === 'y') { w = b.max.x - b.min.x; h = b.max.z - b.min.z; }
      else if (a === 'x') { w = b.max.z - b.min.z; h = b.max.y - b.min.y; }
      else { w = b.max.x - b.min.x; h = b.max.y - b.min.y; }
      const geo = new THREE.PlaneGeometry(w, h);
      const mat = new THREE.MeshBasicMaterial({ color: cssColor('--accent'), opacity: 0.12, transparent: true, depthWrite: false, side: THREE.DoubleSide });
      const m = new THREE.Mesh(geo, mat);
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color: cssColor('--accent') }));
      m.add(edges);
      if (a === 'y') {
        m.rotation.x = -Math.PI / 2;
        m.position.set((b.min.x + b.max.x) / 2, state.pos, (b.min.z + b.max.z) / 2);
      } else if (a === 'x') {
        m.rotation.y = Math.PI / 2;
        m.position.set(state.pos, (b.min.y + b.max.y) / 2, (b.min.z + b.max.z) / 2);
      } else {
        m.position.set((b.min.x + b.max.x) / 2, (b.min.y + b.max.y) / 2, state.pos);
      }
      helperGroup.add(m);
      planeHelper = m;
    } else if (state.mode === 'box') {
      const b = state.box;
      const box = new THREE.Box3(
        new THREE.Vector3(b.x[0], b.y[0], b.z[0]),
        new THREE.Vector3(b.x[1], b.y[1], b.z[1])
      );
      boxHelper = new THREE.Box3Helper(box, cssColor('--accent'));
      helperGroup.add(boxHelper);
    }
    scene.add(helperGroup);
  }

  function setMode(mode) {
    state.mode = mode;
    if (el) {
      el.querySelectorAll('[data-mode]').forEach((btn) => btn.classList.toggle('active', btn.dataset.mode === mode));
      const planeBlock = $('secPlaneBlock');
      const boxBlock = $('secBoxBlock');
      if (planeBlock) planeBlock.hidden = mode !== 'plane';
      if (boxBlock) boxBlock.hidden = mode !== 'box';
    }
    apply();
  }

  function setAxis(axis) {
    state.axis = axis;
    if (el) {
      el.querySelectorAll('[data-axis]').forEach((btn) => btn.classList.toggle('active', btn.dataset.axis === axis));
      const slider = $('secPos');
      if (slider) {
        slider.min = modelBox.min[axis];
        slider.max = modelBox.max[axis];
        slider.step = 0.01;
        state.pos = axisMid(axis);
        slider.value = state.pos;
        const val = $('secPosVal');
        if (val) val.textContent = fmt(state.pos);
      }
    }
    apply();
  }

  function setBoxRange(axis, lo, hi) {
    state.box[axis] = [lo, hi];
    if (el) {
      const min = $('secBox' + axis + 'Min');
      const max = $('secBox' + axis + 'Max');
      const val = $('secBox' + axis + 'Val');
      if (min) min.value = lo;
      if (max) max.value = hi;
      if (val) val.textContent = fmt(lo) + ' – ' + fmt(hi);
    }
    apply();
  }

  function init() {
    el = $('sectionPanel');
    if (!el) return;
    el.innerHTML = '';
    const cap = document.createElement('div');
    cap.className = 'slabel';
    cap.textContent = 'Сечения';
    el.appendChild(cap);

    if (modelBox.isEmpty()) {
      empty = true;
      return;
    }
    empty = false;
    for (const a of ['x', 'y', 'z']) state.box[a] = [modelBox.min[a], modelBox.max[a]];
    state.pos = axisMid(state.axis);

    const modeRow = document.createElement('div');
    modeRow.className = 'control';
    const modeLabel = document.createElement('label');
    modeLabel.textContent = 'Режим';
    const modeSeg = document.createElement('div');
    modeSeg.className = 'seg';
    modeSeg.id = 'secMode';
    const modes = [['none', 'Нет'], ['plane', 'Плоскость'], ['box', 'Коробка']];
    for (const [m, t] of modes) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = t;
      b.dataset.mode = m;
      if (m === state.mode) b.classList.add('active');
      b.addEventListener('click', () => setMode(m));
      modeSeg.appendChild(b);
    }
    modeRow.appendChild(modeLabel);
    modeRow.appendChild(modeSeg);
    el.appendChild(modeRow);

    const planeBlock = document.createElement('div');
    planeBlock.id = 'secPlaneBlock';
    planeBlock.hidden = state.mode !== 'plane';

    const axisRow = document.createElement('div');
    axisRow.className = 'control';
    const axisLabel = document.createElement('label');
    axisLabel.textContent = 'Ось';
    const axisSeg = document.createElement('div');
    axisSeg.className = 'seg';
    axisSeg.id = 'secAxis';
    const axes = [['x', 'X'], ['y', 'Высота'], ['z', 'Z']];
    for (const [a, t] of axes) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = t;
      b.dataset.axis = a;
      if (a === state.axis) b.classList.add('active');
      b.addEventListener('click', () => setAxis(a));
      axisSeg.appendChild(b);
    }
    axisRow.appendChild(axisLabel);
    axisRow.appendChild(axisSeg);
    planeBlock.appendChild(axisRow);

    const posRow = document.createElement('div');
    posRow.className = 'control';
    const posLabel = document.createElement('label');
    posLabel.textContent = 'Положение';
    const posSlider = document.createElement('input');
    posSlider.type = 'range';
    posSlider.id = 'secPos';
    posSlider.min = modelBox.min[state.axis];
    posSlider.max = modelBox.max[state.axis];
    posSlider.step = 0.01;
    posSlider.value = state.pos;
    const posVal = document.createElement('span');
    posVal.className = 'val num';
    posVal.id = 'secPosVal';
    posVal.textContent = fmt(state.pos);
    posSlider.addEventListener('input', () => {
      state.pos = parseFloat(posSlider.value);
      posVal.textContent = fmt(state.pos);
      apply();
    });
    posRow.appendChild(posLabel);
    posRow.appendChild(posSlider);
    posRow.appendChild(posVal);
    planeBlock.appendChild(posRow);

    const flipRow = document.createElement('div');
    flipRow.className = 'control';
    const flipCheck = document.createElement('label');
    flipCheck.className = 'check';
    const flipInput = document.createElement('input');
    flipInput.type = 'checkbox';
    flipInput.id = 'secFlip';
    flipInput.checked = state.flip;
    flipInput.addEventListener('change', () => {
      state.flip = flipInput.checked;
      apply();
    });
    flipCheck.appendChild(flipInput);
    flipCheck.appendChild(document.createTextNode('Обратить'));
    flipRow.appendChild(flipCheck);
    planeBlock.appendChild(flipRow);
    el.appendChild(planeBlock);

    const boxBlock = document.createElement('div');
    boxBlock.id = 'secBoxBlock';
    boxBlock.hidden = state.mode !== 'box';
    const boxAxes = [['x', 'X'], ['y', 'Высота'], ['z', 'Z']];
    for (const [a, t] of boxAxes) {
      const row = document.createElement('div');
      row.className = 'control';
      const label = document.createElement('label');
      label.textContent = t;
      const min = document.createElement('input');
      min.type = 'range';
      min.id = 'secBox' + a + 'Min';
      min.min = modelBox.min[a];
      min.max = modelBox.max[a];
      min.step = 0.01;
      min.value = state.box[a][0];
      const max = document.createElement('input');
      max.type = 'range';
      max.id = 'secBox' + a + 'Max';
      max.min = modelBox.min[a];
      max.max = modelBox.max[a];
      max.step = 0.01;
      max.value = state.box[a][1];
      const val = document.createElement('span');
      val.className = 'val num';
      val.id = 'secBox' + a + 'Val';
      val.textContent = fmt(state.box[a][0]) + ' – ' + fmt(state.box[a][1]);
      min.addEventListener('input', () => {
        let lo = parseFloat(min.value);
        let hi = parseFloat(max.value);
        if (lo > hi - 0.01) lo = hi - 0.01;
        min.value = lo;
        setBoxRange(a, lo, hi);
      });
      max.addEventListener('input', () => {
        let lo = parseFloat(min.value);
        let hi = parseFloat(max.value);
        if (hi < lo + 0.01) hi = lo + 0.01;
        max.value = hi;
        setBoxRange(a, lo, hi);
      });
      row.appendChild(label);
      row.appendChild(min);
      row.appendChild(max);
      row.appendChild(val);
      boxBlock.appendChild(row);
    }
    const btnRow = document.createElement('div');
    btnRow.className = 'control';
    const selBtn = document.createElement('button');
    selBtn.type = 'button';
    selBtn.className = 'btn';
    selBtn.textContent = 'По выделенному';
    selBtn.addEventListener('click', () => {
      if (!selection || selection.size === 0) return;
      const box = new THREE.Box3();
      for (const part of selection) {
        box.union(new THREE.Box3().setFromObject(part.object));
      }
      box.expandByScalar(0.3);
      box.min.clamp(modelBox.min, modelBox.max);
      box.max.clamp(modelBox.min, modelBox.max);
      state.box.x = [box.min.x, box.max.x];
      state.box.y = [box.min.y, box.max.y];
      state.box.z = [box.min.z, box.max.z];
      setMode('box');
      for (const a of ['x', 'y', 'z']) {
        const min = $('secBox' + a + 'Min');
        const max = $('secBox' + a + 'Max');
        const val = $('secBox' + a + 'Val');
        if (min) min.value = state.box[a][0];
        if (max) max.value = state.box[a][1];
        if (val) val.textContent = fmt(state.box[a][0]) + ' – ' + fmt(state.box[a][1]);
      }
      apply();
    });
    const resetBtn = document.createElement('button');
    resetBtn.type = 'button';
    resetBtn.className = 'btn';
    resetBtn.textContent = 'Сбросить';
    resetBtn.addEventListener('click', () => {
      state.box.x = [modelBox.min.x, modelBox.max.x];
      state.box.y = [modelBox.min.y, modelBox.max.y];
      state.box.z = [modelBox.min.z, modelBox.max.z];
      for (const a of ['x', 'y', 'z']) {
        const min = $('secBox' + a + 'Min');
        const max = $('secBox' + a + 'Max');
        const val = $('secBox' + a + 'Val');
        if (min) min.value = state.box[a][0];
        if (max) max.value = state.box[a][1];
        if (val) val.textContent = fmt(state.box[a][0]) + ' – ' + fmt(state.box[a][1]);
      }
      apply();
    });
    btnRow.appendChild(selBtn);
    btnRow.appendChild(resetBtn);
    boxBlock.appendChild(btnRow);
    el.appendChild(boxBlock);

    if (window.SECTION_PRESETS && Array.isArray(window.SECTION_PRESETS) && window.SECTION_PRESETS.length > 0) {
      const presetCap = document.createElement('div');
      presetCap.className = 'slabel';
      presetCap.textContent = 'Планы этажей';
      el.appendChild(presetCap);
      const presetRow = document.createElement('div');
      presetRow.className = 'control';
      for (const p of window.SECTION_PRESETS) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn';
        b.textContent = p.name;
        b.addEventListener('click', () => {
          state.axis = 'y';
          state.pos = p.y;
          state.flip = false;
          setMode('plane');
          setAxis('y');
          state.pos = p.y;
          const slider = $('secPos');
          const val = $('secPosVal');
          if (slider) slider.value = p.y;
          if (val) val.textContent = fmt(p.y);
          const flip = $('secFlip');
          if (flip) flip.checked = false;
          apply();
        });
        presetRow.appendChild(b);
      }
      el.appendChild(presetRow);
    }

    const fillRow = document.createElement('div');
    fillRow.className = 'control';
    const fillCheck = document.createElement('label');
    fillCheck.className = 'check';
    const fillInput = document.createElement('input');
    fillInput.type = 'checkbox';
    fillInput.id = 'secFill';
    fillInput.checked = state.fill;
    fillInput.addEventListener('change', () => {
      state.fill = fillInput.checked;
      apply();
    });
    fillCheck.appendChild(fillInput);
    fillCheck.appendChild(document.createTextNode('Заливка разреза'));
    fillRow.appendChild(fillCheck);
    el.appendChild(fillRow);

    const boundsRow = document.createElement('div');
    boundsRow.className = 'control';
    const boundsCheck = document.createElement('label');
    boundsCheck.className = 'check';
    const boundsInput = document.createElement('input');
    boundsInput.type = 'checkbox';
    boundsInput.id = 'secBounds';
    boundsInput.checked = state.bounds;
    boundsInput.addEventListener('change', () => {
      state.bounds = boundsInput.checked;
      updateHelpers();
    });
    boundsCheck.appendChild(boundsInput);
    boundsCheck.appendChild(document.createTextNode('Показать границы'));
    boundsRow.appendChild(boundsCheck);
    el.appendChild(boundsRow);

    apply();
  }

  return { init, apply, onMaterial };
})();
