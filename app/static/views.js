export const q = (selector, root = document) => root.querySelector(selector);
export const text = (selector, value) => { q(selector).textContent = value ?? '—'; };
export const vector = (point) => point ? `(${point.join(', ')})` : '—';
export const number = (value) => value == null ? '—' : Number(value).toLocaleString('zh-CN');
export const labels = {
  PASS: '通过', FAIL: '失败', PENDING: '待测', CONFLICT: '冲突', ACTIVE: '有效芯粒',
  EDGE_EXCLUSION: '边缘保留带', NOTCH_VOID: '缺口空位', OUTSIDE_DISC: '圆片外',
  OUT_OF_GRID: '超出阵列', MATCH: '一致', REVIEW: '台账有差异', DIFFERENCE: '有差异',
  MISSING_REFERENCE: '缺交接台账', UNBALANCED: '阵列或行数失衡',
};

export function element(tag, value, className) {
  const item = document.createElement(tag);
  if (value != null) item.textContent = value;
  if (className) item.className = className;
  return item;
}

export function options(select, choices, value) {
  select.replaceChildren(...choices.map(([id, title]) => new Option(title, id)));
  if (choices.some(([id]) => id === value)) select.value = value;
}

export function pairs(items) {
  const list = element('dl', null, 'pairs');
  for (const [title, value] of items) {
    const content = element('dd', null, 'mono');
    content.append(value instanceof Node ? value : String(value ?? '—'));
    list.append(element('dt', title), content);
  }
  return list;
}

export function status(code) {
  const variant = {PASS:'pass', FAIL:'fail', CONFLICT:'conflict', PENDING:'pending', MATCH:'pass',
    REVIEW:'pending', DIFFERENCE:'pending', UNBALANCED:'fail'}[code] ?? '';
  return element('span', labels[code] ?? code, `status ${variant}`);
}

export function failure(box, error) {
  box.replaceChildren(element('p', error.message || '请求失败，请重试。', 'inline-error'));
}

function table(box, records, columns, empty = '没有符合条件的记录。') {
  if (records.length === 0) {
    box.replaceChildren(element('p', empty, 'empty'));
    return;
  }
  const grid = element('table', null, 'ledger');
  const headers = grid.createTHead().insertRow();
  for (const [name, , css] of columns) {
    const th = element('th', name, css);
    th.scope = 'col';
    headers.append(th);
  }
  const body = grid.createTBody();
  for (const record of records) {
    const row = body.insertRow();
    for (const [, value, css] of columns) {
      const cell = row.insertCell();
      const item = typeof value === 'function' ? value(record) : record[value];
      if (css) cell.className = css;
      if (item instanceof Node) cell.append(item);
      else {
        cell.textContent = item ?? '—';
        cell.title = cell.textContent;
      }
    }
  }
  box.replaceChildren(grid);
}

function rowButton(label, action) {
  const button = element('button', label, 'row-link');
  button.type = 'button';
  button.addEventListener('click', () => {
    for (const peer of button.closest('tbody').querySelectorAll('[aria-pressed]')) peer.setAttribute('aria-pressed', 'false');
    button.setAttribute('aria-pressed', 'true');
    action();
  });
  button.setAttribute('aria-pressed', 'false');
  return button;
}

const original = (row) => row.fields ? vector([row.fields.x, row.fields.y]) : '—';
const line = (row) => row.end_line === row.line ? row.line : `${row.line}–${row.end_line}`;
const issueColumns = [
  ['图档', 'source', 'mono'], ['行号', line, 'num'], ['晶圆', 'wafer', 'mono'],
  ['原因码', 'code', 'mono'], ['原因', 'reason'], ['原始字段', (row) => row.raw.map((value) => JSON.stringify(value)).join(' · ')],
];

function evidenceCard(row) {
  const card = element('article', null, 'evidence-row');
  const header = element('div', null, 'evidence-id');
  header.append(element('strong', `${row.source}:${line(row)}`, 'mono'), element('span', row.code ?? '采信'));
  const fields = row.fields;
  const measurement = fields ? `${original(row)}   T${fields.attempt}   ${fields.result || '∅'}   BIN ${fields.bin || '∅'}` : '';
  card.append(header, element('p', measurement, 'evidence-values'));
  if (row.transform) {
    const {translated, mirrored, turn_degrees, canonical} = row.transform;
    card.append(element('p', `轴向 ${vector(translated)} · 镜像 ${vector(mirrored)} · ${turn_degrees}° → ${vector(canonical)}`, 'evidence-note'));
  }
  if (fields?.note) card.append(element('p', fields.note, 'evidence-note'));
  return card;
}

export function renderCell(box, cell) {
  const decision = cell.decision;
  const heading = element('div', null, 'detail-title');
  heading.append(element('strong', vector([cell.x, cell.y])), status(decision?.result ?? cell.eligibility));
  if (cell.cluster) heading.append(element('span', cell.cluster, 'mono'));
  box.replaceChildren(heading, pairs([['晶圆', cell.wafer], ['测次', decision?.attempt], ['分档', decision?.bin], ['原始行', cell.evidence.length]]));
  if (decision?.history.length) {
    const history = decision.history.map((item) => `T${item.attempt} ${labels[item.result]}`).join(' → ');
    box.append(element('p', history, 'data-strip detail-group'));
  }
  for (const row of cell.evidence) box.append(evidenceCard(row));
  if (!cell.evidence.length) box.append(element('p', '该位置暂无原始行。', 'empty'));
}

const verdictLabels = {
  RETURN_BATCH: '整批退回',
  ACCEPT_PARTIAL: '接收可识别段',
  QUARANTINED: '含未知段待裁决',
  CLEAN: '全部段可识别',
  recognized: '采信',
  unknown: '隔离',
};

function verdictStatus(decision) {
  return element('span', verdictLabels[decision] ?? decision,
    `status ${decision === 'RETURN_BATCH' ? 'fail' : decision === 'ACCEPT_PARTIAL' ? 'pass' : 'pending'}`);
}

function verdictCard(data, base, refresh) {
  const box = q('#intake-verdict');
  const state = data.intake.state;
  const saved = data.adjudication;
  text('#verdict-state', state === 'QUARANTINED'
    ? `${verdictLabels.QUARANTINED} · ${data.intake.unknown_segments} 段` : verdictLabels.CLEAN);
  const head = pairs([
    ['采信状态', verdictStatus(state)],
    ['未知段', data.intake.unknown_segments],
    ['当前裁决', saved ? verdictStatus(saved.decision) : element('span', '待资料员裁决', 'status pending')],
  ]);
  if (saved) {
    head.append(pairs([
      ['裁决时间', saved.decided_at.replace('T', ' ').replace('+00:00', ' UTC')],
      ['重试次数', saved.attempt_count],
      ['裁决说明', saved.reason || '—'],
    ]));
  }
  const actions = element('div', null, 'verdict-actions');
  const reasonInput = element('input');
  reasonInput.type = 'text';
  reasonInput.maxLength = 200;
  reasonInput.placeholder = '裁决说明（可选）';
  reasonInput.setAttribute('aria-label', '裁决说明');
  if (saved) {
    reasonInput.value = saved.reason;
    reasonInput.disabled = true;
  }
  const submit = async (decision) => {
    if (saved && saved.decision !== decision) return;
    actions.querySelectorAll('button').forEach((button) => { button.disabled = true; });
    try {
      const response = await fetch(`${base}/adjudication`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({decision, reason: reasonInput.value}),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(body?.message ?? `请求失败 HTTP ${response.status}`);
      await refresh(body.reused ? '相同裁决重试已记录，未重复建档。' : '裁决已归档。');
    } catch (error) {
      const panel = q('#feedback');
      panel.hidden = false;
      panel.classList.add('error');
      panel.textContent = error.message;
      actions.querySelectorAll('button').forEach((button) => { button.disabled = false; });
    }
  };
  const returnBtn = element('button', '整批退回', 'btn');
  returnBtn.type = 'button';
  returnBtn.addEventListener('click', () => submit('RETURN_BATCH'));
  const acceptBtn = element('button', '接收可识别段', 'btn primary');
  acceptBtn.type = 'button';
  acceptBtn.addEventListener('click', () => submit('ACCEPT_PARTIAL'));
  if (state !== 'QUARANTINED') acceptBtn.disabled = true;
  if (saved) {
    returnBtn.disabled = true;
    acceptBtn.disabled = true;
  }
  actions.append(reasonInput, returnBtn, acceptBtn);
  const hint = element('p', state === 'QUARANTINED'
    ? '未知栏名段已整段隔离，不参与对表；接收后可识别段继续流转，退回则整批交还客户。'
    : '本批全部段可识别，仅可整批退回。', 'evidence-note');
  box.replaceChildren(head, hint, actions);
}

function segmentRows(files) {
  const rows = [];
  for (const file of files) {
    for (seg of file.segments_detail ?? []) {
      rows.push({...seg, source: file.name});
    }
  }
  return rows;
}

function drawIntake(data, api, base, refresh) {
  text('#source-total', `${data.files.length} 档`);
  table(q('#intake-files'), data.files, [
    ['图档', 'name', 'mono'], ['编码', 'encoding', 'mono'], ['段', 'segments', 'num'],
    ['可识别段', 'recognized_segments', 'num'], ['未知段', 'unknown_segments', 'num'],
    ['物理行', 'physical_lines', 'num'], ['数据记录', 'data_count', 'num'],
  ]);
  const isolatedCount = data.issues.filter((row) => row.bucket === 'isolated').length;
  q('#intake-summary').replaceChildren(pairs([
    ['数据记录', data.files.reduce((sum, file) => sum + file.data_count, 0)],
    ['可识别段', data.files.reduce((sum, file) => sum + file.recognized_segments, 0)],
    ['未知段', data.files.reduce((sum, file) => sum + file.unknown_segments, 0)],
    ['隔离记录', isolatedCount],
    ['重复档头', data.issues.filter((row) => row.bucket === 'header').length],
    ['空记录', data.issues.filter((row) => row.code === 'BLANK_ROW').length],
  ]));
  const segments = segmentRows(data.files);
  text('#segment-total', `${segments.length} 段 · ${data.intake.unknown_segments} 段隔离`);
  table(q('#intake-segments'), segments, [
    ['图档', 'source', 'mono'],
    ['段号', (seg) => `#${seg.index + 1}`, 'num'],
    ['采信', (seg) => verdictStatus(seg.status)],
    ['起止行', (seg) => `${seg.start_line}–${seg.end_line}`, 'num'],
    ['数据行', 'data_rows', 'num'],
    ['栏位绑定', (seg) => seg.status === 'recognized'
      ? seg.columns.join(' / ') : `未知栏名：${seg.unknown_columns.join(' / ')}`],
  ]);
  verdictCard(data, base, refresh);
  drawIssues(data);
}

function drawIssues(data) {
  table(q('#intake-issues'), data.issues, [
    ['图档', 'source', 'mono'], ['行号', line, 'num'],
    ['段', (row) => row.segment == null ? '—' : `#${row.segment + 1}`, 'num'],
    ['晶圆', 'wafer', 'mono'], ['原因码', 'code', 'mono'], ['原因', 'reason'],
    ['原始字段', (row) => row.raw.map((value) => JSON.stringify(value)).join(' · ')],
  ]);
}

async function intake(context) {
  const {api, base} = context;
  const data = await api(`${base}/intake`);
  const refresh = async (message) => {
    const fresh = await api(`${base}/intake`);
    Object.assign(data, fresh);
    drawIntake(data, api, base, refresh);
    if (message) {
      const panel = q('#feedback');
      panel.hidden = false;
      panel.classList.remove('error');
      panel.textContent = message;
    }
  };
  drawIntake(data, api, base, refresh);
}

async function coordinates({api, base, signal}) {
  const data = await api(`${base}/coordinates`);
  table(q('#coordinate-sources'), data.sources, [
    ['图档', 'name', 'mono'], ['原点', (s) => vector([s.origin_x, s.origin_y]), 'num'],
    ['原缺口', 'notch', 'mono'], ['原轴向', (s) => `${s.x_left ? 'X←' : 'X→'} / ${s.y_down ? 'Y↓' : 'Y↑'}`],
    ['镜像', (s) => s.mirror ? 'X 反射' : '—'], ['对表', 'aligned', 'num'],
  ]);
  const form = q('#probe-form');
  const choices = data.sources.map((s) => [s.name, s.name]);
  options(form.elements.source, choices, choices.at(-1)?.[0]);
  const drawProbe = async () => {
    const target = q('#probe-output');
    const button = q('button', form);
    button.disabled = true;
    try {
      const result = await api(`${base}/coordinates/probe?${new URLSearchParams(new FormData(form))}`);
      target.replaceChildren(pairs([['平移与轴向', vector(result.translated)], ['镜像后', vector(result.mirrored)],
        ['逆时针旋转', `${result.turn_degrees}°`], ['标准坐标', vector(result.canonical)]]));
    } catch (error) {
      if (!signal.aborted) failure(target, error);
    } finally {
      if (!signal.aborted) button.disabled = false;
    }
  };
  form.addEventListener('submit', (event) => { event.preventDefault(); drawProbe(); }, {signal});
  drawProbe();
  const filter = q('#coordinate-filter');
  options(filter, [['all', '全部'], ...choices]);
  const draw = () => {
    const rows = data.rows.filter((row) => filter.value === 'all' || row.source === filter.value);
    text('#aligned-total', `${rows.length} 行`);
    table(q('#coordinate-rows'), rows, [
      ['图档', 'source', 'mono'], ['行号', line, 'num'], ['晶圆', 'wafer', 'mono'], ['原点位', original, 'num'],
      ['轴向后', (row) => vector(row.transform.translated), 'num'],
      ['镜像后', (row) => vector(row.transform.mirrored), 'num'], ['旋转 °', (row) => row.transform.turn_degrees, 'num'],
      ['标准点位', (row) => vector(row.transform.canonical), 'num'],
    ]);
  };
  filter.addEventListener('change', draw, {signal});
  draw();
  table(q('#coordinate-issues'), data.issues, issueColumns);
}

function detailLoader(target, signal) {
  let current = 0;
  return async (request, render) => {
    const token = ++current;
    target.replaceChildren(element('p', '正在读取证据…', 'empty'));
    try {
      const result = await request();
      if (!signal.aborted && token === current) render(result);
    } catch (error) {
      if (!signal.aborted && token === current) failure(target, error);
    }
  };
}

async function retest(context) {
  const {api, base, wafer, signal} = context;
  const data = await api(`${base}/retest/${wafer}`);
  const filter = q('#retest-filter');
  const target = q('#retest-detail');
  const load = detailLoader(target, signal);
  const show = (x, y) => load(() => api(`${base}/mask/${wafer}/cell?x=${x}&y=${y}`), (cell) => renderCell(target, cell));
  const draw = () => {
    const rows = data.cells.filter((cell) => filter.value === 'all' || cell.result === filter.value);
    text('#retest-total', `${rows.length} 枚`);
    table(q('#retest-rows'), rows, [
      ['坐标', (cell) => rowButton(vector([cell.x,cell.y]), () => show(cell.x, cell.y))],
      ['裁决', (cell) => status(cell.result)], ['末次', 'attempt', 'num'], ['分档', 'bin', 'num'],
      ['测次链', (cell) => cell.history.map((step) => `T${step.attempt} ${labels[step.result]}`).join(' → ')],
    ]);
    if (rows.length) q('button', q('#retest-rows')).click();
    else load(async () => null, () => target.replaceChildren(element('p', '该状态无芯粒。', 'empty')));
  };
  filter.addEventListener('change', draw, {signal});
  draw();
  table(q('#retest-issues'), data.issues, issueColumns);
}

async function clusters(context) {
  const {api, base, wafer, run, signal} = context;
  const data = await api(`${base}/clusters/${wafer}`);
  const target = q('#cluster-detail');
  const load = detailLoader(target, signal);
  const evidence = q('#cluster-evidence');
  const show = (id) => {
    evidence.replaceChildren(element('p', '正在载入原行…', 'empty'));
    return load(() => api(`${base}/clusters/${wafer}/${id}`).catch((error) => ({error})), (group) => {
      if (group.error) {
        failure(target, group.error);
        evidence.replaceChildren();
        return;
      }
      const graph = element('a', '在阵列查看', 'btn link-btn');
      const params = new URLSearchParams({run: run.run_id, wafer, cluster: id, x: group.anchor[0], y: group.anchor[1]});
      graph.href = `/?${params}`;
      target.replaceChildren(pairs([['簇号', group.id], ['成员', group.size], ['分档', Object.entries(group.bins).map(([bin, qty]) => `${bin} × ${qty}`).join(' · ')],
        ['点位', group.points.map(vector).join(' ')], ['晶圆阵列', graph]]));
      table(evidence, group.rows, [['图档', 'source', 'mono'], ['行号', line, 'num'], ['原点位', original, 'num'],
        ['标准点位', (row) => vector(row.transform.canonical), 'num'], ['测次', (row) => row.measurement.attempt, 'num'],
        ['分档', (row) => row.measurement.bin, 'num']]);
    });
  };
  text('#cluster-total', `${data.length} 簇 · ${data.reduce((sum, group) => sum + group.size, 0)} 枚`);
  table(q('#cluster-rows'), data, [['簇号', (group) => rowButton(group.id, () => show(group.id))], ['成员', 'size', 'num'],
    ['起点', (group) => vector(group.anchor), 'num'], ['包围盒', (group) => `${vector(group.bounds.slice(0,2))} → ${vector(group.bounds.slice(2))}`]]);
  if (data.length) q('button', q('#cluster-rows')).click();
  else for (const box of [target, evidence]) box.replaceChildren(element('p', '此片无最终失败芯粒。', 'empty'));
}

const countNames = {valid: '有效', passed: '通过', failed: '失败', pending: '待测', conflict: '冲突'};
const rowNames = {total: '实收记录', declared_data: '声明数据', accepted: '采信', isolated: '隔离', duplicate: '重复', blank: '空记录', header: '重复档头'};

function auditView({audit, signal}) {
  text('#audit-total', `${audit.wafers.length} 片 · 批良率 ${audit.summary.yield_pct ?? '—'}%`);
  const names = Object.entries(countNames);
  const wa = audit.wafers.flatMap((wafer) => [
    {...wafer, field: '阵列', waferStatus: wafer.balanced ? 'MATCH' : 'UNBALANCED'},
    {...wafer.reference, wafer: wafer.wafer, field: '台账', waferStatus: wafer.status},
  ]);
  table(q('#audit-wafers'), wa, [['晶圆', 'wafer', 'mono'], ['来源', 'field'], ...names.map(([key, name]) => [name,key,'num']),
    ['良率 %', 'yield_pct', 'num'], ['核验', (item) => status(item.waferStatus)]]);
  const differences = audit.wafers.filter((row) => row.status !== 'MATCH' || !row.balanced).map((row) => {
    const values = names.filter(([key]) => row.delta?.[key]).map(([key, name]) => {
      const delta = row.delta[key];
      return `${name} ${delta > 0 ? '+' : ''}${delta}`;
    });
    if (row.missing.length || row.extra.length) values.push(`缺 ${row.missing.join(' ') || '—'} · 多 ${row.extra.join(' ') || '—'}`);
    return [row.wafer, values.join('　') || labels[row.status]];
  });
  if (audit.unknown_reference.length) differences.push(['台账多片', audit.unknown_reference.join('、')]);
  q('#audit-differences').replaceChildren(differences.length ? pairs(differences) : status('MATCH'));
  q('#audit-rows').replaceChildren(status(audit.rows.balanced ? 'MATCH' : 'UNBALANCED'),
    pairs(Object.entries(rowNames).map(([key, name]) => [name, audit.rows[key]])));
  const filter = q('#reason-filter');
  options(filter, [['all', `全部 · ${audit.isolated.length}`], ...Object.entries(audit.reasons).map(([code,count]) => [code, `${code} · ${count}`])]);
  const draw = () => table(q('#audit-issues'), audit.isolated.filter((row) => filter.value === 'all' || row.code === filter.value), issueColumns, '当前图批无隔离行。');
  filter.addEventListener('change', draw, {signal});
  draw();
}

function runs({run, manifests}) {
  text('#history-total', `${manifests.length} 批`);
  table(q('#history-rows'), manifests, [
    ['运行号', (item) => {
      const link = element('a', item.run_id.slice(0, 12), 'row-link');
      link.href = `/runs?${new URLSearchParams({run: item.run_id})}`;
      if (item.run_id === run.run_id) link.setAttribute('aria-current','true');
      return link;
    }], ['图批', 'lot', 'mono'], ['样例', 'label'],
    ['采信', (item) => element('span', item.intake ? verdictLabels[item.intake.state] ?? item.intake.state : '—',
      `status ${item.intake?.state === 'QUARANTINED' ? 'conflict' : 'pass'}`)],
    ['裁决', (item) => item.adjudication ? verdictStatus(item.adjudication.decision)
      : element('span', '待裁决', 'status pending')],
    ['状态', (item) => status(item.status)],
    ['生成 UTC', 'created_at', 'mono'],
  ]);
  q('#run-manifest').replaceChildren(pairs([['运行号', run.run_id], ['规则版本', run.rule_version],
    ['计算耗时', `${run.duration_ms} ms`], ['晶圆', run.wafers.join('、')], ['契约 SHA-256', run.contract_sha256], ['结果 SHA-256', run.result_sha256]]));
  table(q('#run-sources'), Object.entries(run.sources).map(([name, value]) => ({name, ...value})), [
    ['图档', 'name', 'mono'], ['字节', 'bytes', 'num'], ['SHA-256', 'sha256', 'mono'],
  ]);
}

export const views = {intake, coordinates, retest, clusters, yield: auditView, runs};
