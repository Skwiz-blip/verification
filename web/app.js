'use strict';

/* Controle mensuel — interface.
   Aucun calcul metier ici : le serveur renvoie des faits et des codes, ce
   fichier les met en forme. Tout texte venu des exports (noms de comptes,
   legendes) est insere par textContent, jamais par innerHTML. */

const PLATFORMS = { facebook: 'Facebook', instagram: 'Instagram' };
const FORMATS = {
  photo: 'Photo', reel: 'Reel', carousel: 'Carrousel', video: 'Vidéo', story: 'Story', other: 'Autre',
};
const LEVELS = {
  n3: { label: 'Niveau 3 atteint', short: 'N3', rank: 6 },
  n2: { label: 'Niveau 2 atteint', short: 'N2', rank: 5 },
  n1: { label: 'Niveau 1 atteint', short: 'N1', rank: 4 },
  below: { label: 'Sous le Niveau 1', short: '< N1', rank: 3 },
  few: { label: 'Trop peu de publications', short: 'peu', rank: 2 },
  fewc: { label: 'Trop peu de comptes', short: 'peu', rank: 2 },
  none: { label: "Pas d'objectif", short: '–', rank: 1 },
  nopost: { label: 'Aucune publication', short: '', rank: 0 },
};
const EVALUATED = new Set(['n3', 'n2', 'n1', 'below']);
const REACHED = new Set(['n3', 'n2', 'n1']);
const MONTHS = [
  'janvier', 'février', 'mars', 'avril', 'mai', 'juin',
  'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre',
];
const MONTHS_SHORT = [
  'janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.',
];
// La couleur suit la serie, jamais son rang : un filtre ne repeint rien.
const SERIES_SLOTS = {
  'instagram|photo': 1, 'instagram|carousel': 2, 'instagram|reel': 3,
  'facebook|photo': 4, 'facebook|reel': 5, 'facebook|video': 6,
};
const VIEWS = ['ensemble', 'compte', 'categorie', 'donnees'];
const URL_KEYS = ['vue', 'mois', 'ref', 'debut', 'fin', 'min', 'client', 'groupe', 'secteur', 'paire'];
const MAX_UPLOAD = 50 * 1024 * 1024;
const SEP = '\u001f';
const SVG_NS = 'http://www.w3.org/2000/svg';
const ICONS = {
  check: 'M20 6 9 17l-5-5',
  x: 'M18 6 6 18M6 6l12 12',
  info: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 16v-4M12 8h.01',
  alert: 'M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01',
  upload: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12',
  download: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3',
};

const state = {
  vue: 'ensemble', mois: null, ref: 'before', debut: null, fin: null, min: 3,
  client: null, groupe: null, secteur: null, paire: null,
  fCat: '', fPlat: '', fEval: false, tri: {},
};
let data = null;
let failure = null;
let index = null;
let imports = [];
let charts = [];
let requests = 0;

/* Deux sources de donnees, un seul affichage. En local, le serveur Python calcule a la
   demande. En ligne, `config.js` designe Supabase, ou les resultats ont ete publies :
   l'interface ne fait alors que lire. */
const REMOTE = window.CONTROLE_CONFIG?.supabaseUrl ? window.CONTROLE_CONFIG : null;
let published = null;

/* --- Outils ---------------------------------------------------------------- */
const $ = (id) => document.getElementById(id);

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (name === 'class') el.className = value;
    else if (name === 'text') el.textContent = value;
    else if (name.startsWith('on')) el.addEventListener(name.slice(2), value);
    else el.setAttribute(name, value === true ? '' : value);
  }
  el.append(...children.flat().filter((child) => child != null && child !== false));
  return el;
}

function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value != null) el.setAttribute(name, value);
  }
  el.append(...children);
  return el;
}

const icon = (name) => s('svg', { class: 'icon', viewBox: '0 0 24 24', 'aria-hidden': 'true' }, s('path', { d: ICONS[name] }));

const nf0 = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat('fr-FR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const nfCompact = new Intl.NumberFormat('fr-FR', { notation: 'compact', maximumFractionDigits: 1 });
const int = (v) => (v == null ? '' : nf0.format(v));
const pct = (v) => (v == null ? '' : `${nf0.format(v)} %`);
const signed = (v) => (v == null ? '' : `${v > 0 ? '+' : ''}${nf0.format(v)} %`);
const plural = (n, one, many) => `${int(n)} ${n > 1 ? many : one}`;

const monthLabel = (key) => `${MONTHS[Number(key.slice(5)) - 1]} ${key.slice(0, 4)}`;
const monthShort = (key) => `${MONTHS_SHORT[Number(key.slice(5)) - 1]} ${key.slice(2, 4)}`;
const dayLabel = (iso) => (iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : '');
const platformLabel = (code) => PLATFORMS[code] ?? code;
const formatLabel = (code) => FORMATS[code] ?? code;
const keyOf = (row) => [row.compte, row.plateforme, row.format].join(SEP);
const groupLabel = (row) => `${row.compte} · ${platformLabel(row.plateforme)} · ${formatLabel(row.format)}`;
// Un code inconnu (page ouverte avant une mise a jour du serveur) s'affiche tel quel
// au lieu de faire echouer toute la vue.
const levelOf = (code) => LEVELS[code] ?? { label: code ?? '', short: '', rank: -1 };
const badge = (code, text) => h('span', { class: `lv lv-${code}`, text: text ?? levelOf(code).label });

function debounce(fn, delay) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

function niceScale(max, target = 4) {
  if (!(max > 0)) return { max: 1, ticks: [0, 1] };
  const raw = max / target;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const unit = raw / pow;
  const step = (unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 2.5 ? 2.5 : unit <= 5 ? 5 : 10) * pow;
  const top = Math.ceil(max / step - 1e-9) * step;
  const ticks = [];
  for (let v = 0; v <= top + step / 2; v += step) ticks.push(v);
  return { max: top, ticks };
}

/* --- Bulle ----------------------------------------------------------------- */
function showTip(nodes, x, y) {
  const tip = $('bulle');
  tip.replaceChildren(...nodes);
  tip.hidden = false;
  const box = tip.getBoundingClientRect();
  let left = x + 14;
  let top = y + 14;
  if (left + box.width > window.innerWidth - 8) left = x - box.width - 14;
  if (top + box.height > window.innerHeight - 8) top = y - box.height - 14;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${Math.max(8, top)}px`;
}

function hideTip() {
  $('bulle').hidden = true;
}

/* --- Etat, adresse et serveur ---------------------------------------------- */
function readURL() {
  const params = new URLSearchParams(location.hash.slice(1));
  for (const key of URL_KEYS) {
    if (params.has(key)) state[key] = key === 'min' ? Number(params.get(key)) || 3 : params.get(key);
  }
  if (!VIEWS.includes(state.vue)) state.vue = 'ensemble';
}

function writeURL(push = false) {
  const params = new URLSearchParams();
  for (const key of URL_KEYS) {
    if (state[key] != null && state[key] !== '') params.set(key, state[key]);
  }
  const url = `#${params}`;
  if (url === location.hash) return;
  if (push) history.pushState(null, '', url);
  else history.replaceState(null, '', url);
}

async function getJSON(path, params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value != null && value !== '') query.set(key, value);
  }
  let response;
  try {
    response = await fetch(`${path}?${query}`, { headers: { Accept: 'application/json' } });
  } catch {
    throw new Error("Le serveur local ne répond pas. Vérifiez qu'il est toujours lancé.");
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(body?.erreur ?? `Erreur ${response.status}`);
  return body;
}

async function supabase(path) {
  let response;
  try {
    response = await fetch(`${REMOTE.supabaseUrl}/rest/v1/${path}`, {
      headers: {
        apikey: REMOTE.supabaseKey,
        Authorization: `Bearer ${REMOTE.supabaseKey}`,
        Accept: 'application/json',
      },
    });
  } catch {
    throw new Error('La base en ligne ne répond pas. Vérifiez votre connexion.');
  }
  if (response.status === 401 || response.status === 403) {
    throw new Error('Accès refusé par la base en ligne : la clé publique de config.js est invalide.');
  }
  if (!response.ok) throw new Error(`La base en ligne a répondu par une erreur ${response.status}.`);
  return response.json();
}

async function fetchControl() {
  if (!REMOTE) {
    return getJSON('/api/controle', {
      mois: state.mois, ref: state.ref, debut: state.debut, fin: state.fin, min: state.min,
    });
  }
  if (!REMOTE.supabaseKey) throw new Error('La clé publique Supabase est absente de config.js.');
  if (!published) {
    const rows = await supabase('etat?select=valeur&cle=eq.publication');
    if (!rows.length) return { vide: true, import_autorise: false };
    published = rows[0].valeur;
  }
  // Seuls les mois et les references publies existent en ligne.
  const month = published.mois.includes(state.mois) ? state.mois : published.mois_defaut;
  const ref = published.refs.includes(state.ref) ? state.ref : published.refs[0];
  const query = new URLSearchParams({ select: 'payload', mois: `eq.${month}`, ref: `eq.${ref}` });
  const rows = await supabase(`controles?${query}`);
  if (!rows.length) return { vide: true, import_autorise: false };
  return { ...rows[0].payload, import_autorise: false };
}

async function fetchPosts(row) {
  const month = data.meta.mois_controle;
  if (!REMOTE) {
    const payload = await getJSON('/api/publications', {
      compte: row.compte, plateforme: row.plateforme, format: row.format, mois: month,
    });
    return payload.publications;
  }
  return supabase(`publications?${new URLSearchParams({
    select: 'date:publie_le,vues,couverture,engagement,lien,texte',
    compte: `eq.${row.compte}`,
    plateforme: `eq.${row.plateforme}`,
    format: `eq.${row.format}`,
    mois: `eq.${month}`,
    order: 'publie_le.desc',
  })}`);
}

function setBusy(message) {
  document.body.classList.toggle('busy', Boolean(message));
  $('etat').textContent = message;
}

async function load() {
  const mine = ++requests;
  setBusy(REMOTE ? 'Chargement…' : data ? 'Calcul en cours…' : 'Lecture et nettoyage des exports…');
  try {
    const payload = await fetchControl();
    if (mine !== requests) return;
    data = payload;
    failure = null;
    if (!payload.vide) adopt(payload);
  } catch (error) {
    if (mine !== requests) return;
    failure = error;
  }
  setBusy('');
  render();
}

/* Le serveur ramene chaque parametre a une valeur valide : on s'aligne dessus. */
function adopt(payload) {
  const { meta } = payload;
  state.mois = meta.mois_controle;
  state.ref = meta.ref.preset;
  state.min = meta.min;
  state.debut = state.ref === 'custom' ? meta.ref.debut : null;
  state.fin = state.ref === 'custom' ? meta.ref.fin : null;

  const groups = new Map();
  for (const row of payload.historique) {
    const key = keyOf(row);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  const sorted = (values) => [...new Set(values)].sort((a, b) => a.localeCompare(b, 'fr'));
  index = {
    groups,
    clients: sorted(payload.historique.map((row) => row.client)),
    sectors: sorted(payload.historique.map((row) => row.secteur)),
  };
}

/* --- Bandeau --------------------------------------------------------------- */
function fillSelect(select, options, value) {
  const signature = options.map((option) => option.value).join(SEP);
  if (select.dataset.options !== signature) {
    select.replaceChildren(...options.map((o) => h('option', { value: o.value, text: o.label })));
    select.dataset.options = signature;
  }
  if (value != null) select.value = value;
}

function renderControls() {
  // En mode reseau, seul le poste qui heberge l'interface peut ajouter des exports.
  $('importer').hidden = data?.import_autorise === false;
  const ready = Boolean(data && !data.vide && !failure);
  for (const id of ['mois', 'ref', 'debut', 'fin', 'min']) $(id).disabled = !ready;
  // En ligne, le minimum de publications est celui retenu a la publication.
  if (REMOTE) $('min').disabled = true;
  if (!ready) {
    $('resume').textContent = '';
    return;
  }
  const { meta } = data;
  const options = meta.mois.map((key) => ({ value: key, label: monthLabel(key) }));
  fillSelect($('mois'), [...options].reverse(), state.mois);
  fillSelect($('debut'), options, meta.ref.debut);
  fillSelect($('fin'), options, meta.ref.fin);
  $('ref').value = state.ref;
  $('min').value = state.min;
  for (const field of document.querySelectorAll('[data-perso]')) field.hidden = state.ref !== 'custom';
  $('resume').textContent = [
    plural(meta.fichiers, 'fichier', 'fichiers'),
    `${int(meta.publications)} publications organiques`,
    `${monthLabel(meta.mois[0])} → ${monthLabel(meta.mois.at(-1))}`,
    published && `publié le ${dayLabel(published.publie_le)}`,
  ].filter(Boolean).join(' · ');
  if (!document.body.classList.contains('busy')) {
    $('etat').textContent =
      `Objectifs : ${monthLabel(meta.ref.debut)} → ${monthLabel(meta.ref.fin)} · ${int(meta.ref.publications)} publications`;
  }
}

function renderTabs() {
  for (const tab of document.querySelectorAll('[role="tab"]')) {
    const selected = tab.dataset.vue === state.vue;
    tab.id = `onglet-${tab.dataset.vue}`;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
  }
  $('contenu').setAttribute('aria-labelledby', `onglet-${state.vue}`);
}

function goTo(view, changes = {}) {
  Object.assign(state, changes, { vue: view });
  writeURL(true);
  render();
  window.scrollTo({ top: 0 });
}

/* --- Alertes --------------------------------------------------------------- */
function alertBox(kind, title, body, action) {
  return h('div', { class: `alert ${kind === 'warn' ? 'alert-warn' : ''}` },
    icon(kind === 'warn' ? 'alert' : 'info'),
    h('div', { class: 'alert-body' }, h('strong', { text: title }), body),
    action,
  );
}

function importPanel() {
  const labels = {
    added: ['Ajouté', 'good', 'check'],
    duplicate: ['Déjà chargé', 'good', 'check'],
    rejected: ['Refusé', 'critical', 'x'],
  };
  const items = imports.map((item) => {
    const [label, tone, glyph] = labels[item.statut] ?? labels.rejected;
    let detail = item.detail ?? '';
    if (item.statut === 'added') {
      detail = `${int(item.publications)} publications ${platformLabel(item.plateforme)}, `
        + `${plural(item.comptes, 'compte', 'comptes')}, du ${dayLabel(item.debut)} au ${dayLabel(item.fin)}`;
    } else if (item.statut === 'duplicate') {
      detail = 'contenu identique à un fichier déjà présent, rien à ajouter';
    }
    return h('li', {},
      h('span', { class: `mark mark-${tone}` }, icon(glyph), label),
      h('span', { text: `${item.nom} : ${detail}` }),
    );
  });
  const close = h('button', {
    class: 'btn btn-quiet', type: 'button',
    onclick: () => { imports = []; renderAlerts(); },
  }, 'Fermer');
  return alertBox('info', 'Import des exports', h('ul', {}, items), close);
}

function renderAlerts() {
  const alerts = [];
  if (imports.length) alerts.push(importPanel());
  if (data && !data.vide && !failure) {
    const { ref } = data.meta;
    const month = monthLabel(data.meta.mois_controle);
    if (ref.sans_historique) {
      alerts.push(alertBox('warn', `Aucun mois chargé avant ${month}`, h('span', {
        text: 'Les objectifs sont calculés sur toute la période chargée, mois contrôlé compris.',
      })));
    } else if (ref.inclut_mois) {
      alerts.push(alertBox('info', `${month} fait partie de la période de référence`, h('span', {
        text: 'Ses publications ont servi à fixer les seuils auxquels elles sont comparées. Pour un '
          + "contrôle indépendant, arrêtez la référence avant ce mois.",
      })));
    }
    if (data.ruptures.length) {
      const series = data.ruptures.map((r) =>
        `${platformLabel(r.plateforme)} ${formatLabel(r.format).toLowerCase()} : `
        + `${nf2.format(r.ratio_haut)} vues par personne touchée en ${monthLabel(r.mois_haut)}, `
        + `${nf2.format(r.ratio_bas)} en ${monthLabel(r.mois_bas)}`); 
    }
    if (!data.resultats.some((row) => row.n1 != null)) {
      alerts.push(alertBox('warn', 'Aucun objectif calculable sur cette référence', h('span', {
        text: `Il faut au moins ${data.meta.min_observations} publications par compte et par format `
          + 'dans la période de référence.',
      })));
    }
  }
  $('alertes').replaceChildren(...alerts);
}

/* --- Tables ---------------------------------------------------------------- */
const COLUMNS = {
  compte: {
    key: 'compte', label: 'Compte', cls: 'name', sort: (r) => r.compte.toLowerCase(),
    cell: (r) => h('button', {
      class: 'link', type: 'button',
      onclick: () => goTo('compte', { client: r.client, groupe: keyOf(r) }),
    }, r.compte),
  },
  secteur: { key: 'secteur', label: 'Catégorie' },
  plateforme: { key: 'plateforme', label: 'Plateforme', text: (r) => platformLabel(r.plateforme) },
  format: { key: 'format', label: 'Format', text: (r) => formatLabel(r.format) },
  mois: { key: 'mois', label: 'Mois', text: (r) => monthLabel(r.mois) },
  publications: { key: 'publications', label: 'Publications', num: true, text: (r) => int(r.publications) },
  mediane: { key: 'mediane', label: 'Médiane', num: true, text: (r) => int(r.mediane) },
  n1: { key: 'n1', label: 'N1', num: true, text: (r) => int(r.n1) },
  n2: { key: 'n2', label: 'N2', num: true, text: (r) => int(r.n2) },
  n3: { key: 'n3', label: 'N3', num: true, text: (r) => int(r.n3) },
  ecart: { key: 'ecart', label: 'Écart au N1', num: true, text: (r) => signed(r.ecart) },
  att1: { key: 'att1', label: 'Publis ≥ N1', num: true, text: (r) => pct(r.att1) },
  att2: { key: 'att2', label: 'Publis ≥ N2', num: true, text: (r) => pct(r.att2) },
  att3: { key: 'att3', label: 'Publis ≥ N3', num: true, text: (r) => pct(r.att3) },
  niveau: {
    key: 'niveau', label: 'Niveau atteint', sort: (r) => levelOf(r.niveau).rank,
    text: (r) => levelOf(r.niveau).label, cell: (r) => badge(r.niveau),
  },
};
const pick = (...keys) => keys.map((key) => COLUMNS[key]);

function sortRows(id, columns, rows) {
  const order = state.tri[id];
  const column = order && columns.find((c) => c.key === order.key);
  if (!column) return rows;
  const value = column.sort ?? ((row) => row[column.key]);
  return [...rows].sort((a, b) => {
    const [x, y] = [value(a), value(b)];
    if (x == null || y == null) return (x == null) - (y == null);
    const delta = typeof x === 'string' ? x.localeCompare(y, 'fr') : x - y;
    return order.dir === 'desc' ? -delta : delta;
  });
}

function dataTable(id, caption, columns, rows, options = {}) {
  const order = state.tri[id];
  const head = columns.map((column) => {
    const active = order?.key === column.key;
    const toggle = () => {
      state.tri[id] = { key: column.key, dir: active && order.dir === 'asc' ? 'desc' : 'asc' };
      render();
    };
    return h('th', {
      scope: 'col', class: column.num ? 'num' : null,
      'aria-sort': active ? (order.dir === 'asc' ? 'ascending' : 'descending') : null,
    }, options.sortable === false
      ? column.label
      : h('button', { class: 'sort', type: 'button', onclick: toggle }, column.label));
  });
  const body = sortRows(id, columns, rows).map((row) =>
    h('tr', { 'aria-current': options.current?.(row) ? 'true' : null },
      columns.map((column) => h('td', { class: column.num ? 'num' : column.wrap ? 'wrap' : column.cls },
        column.cell ? column.cell(row) : (column.text ? column.text(row) : row[column.key] ?? '')))));
  if (!body.length) return h('p', { class: 'empty', text: options.empty ?? 'Aucune ligne à afficher.' });
  return h('div', { class: 'table-wrap' },
    h('table', {}, h('caption', { text: caption }), h('thead', {}, h('tr', {}, head)), h('tbody', {}, body)));
}

function downloadCSV(name, columns, rows) {
  const escape = (value) => {
    let text = value == null ? '' : String(value);
    // Un nom de compte ne doit jamais etre interprete comme une formule par le tableur.
    if (typeof value === 'string' && /^[=+\-@\t]/.test(text)) text = `'${text}`;
    if (typeof value === 'number') text = text.replace('.', ',');
    return /[";\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  };
  const cell = (column, row) => (column.num ? row[column.key] : (column.text ? column.text(row) : row[column.key]));
  const lines = [
    columns.map((column) => escape(column.label)).join(';'),
    ...rows.map((row) => columns.map((column) => escape(cell(column, row))).join(';')),
  ];
  const url = URL.createObjectURL(new Blob([`﻿${lines.join('\r\n')}`], { type: 'text/csv;charset=utf-8' }));
  const link = h('a', { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* --- Graphiques ------------------------------------------------------------ */
function chartBox(draw) {
  const box = h('div', { class: 'chart' });
  charts.push(() => draw(box));
  return box;
}

function column(cx, width, top, base) {
  const r = Math.min(4, width / 2, base - top);
  const [left, right] = [cx - width / 2, cx + width / 2];
  return `M${left},${base}V${top + r}Q${left},${top} ${left + r},${top}H${right - r}`
    + `Q${right},${top} ${right},${top + r}V${base}Z`;
}

function monthAxis(svg, months, x, band, y, current) {
  const step = band >= 46 ? 1 : band >= 24 ? 2 : 3;
  const pinned = months.indexOf(current);
  months.forEach((key, i) => {
    if (i % step && i !== pinned) return;
    // Le mois controle est toujours etiquete : ses voisins lui cedent la place.
    if (pinned >= 0 && i !== pinned && Math.abs(i - pinned) < step) return;
    svg.append(s('text', { x: x(i), y, 'text-anchor': 'middle', class: key === current ? 'now' : null }, monthShort(key)));
  });
}

/* Valeur mensuelle en colonnes, paliers en pointilles, reference en fond gris.
   Les colonnes portent la couleur du niveau atteint, comme partout ailleurs. */
function drawBars(box, cfg) {
  const months = data.meta.mois;
  const W = Math.max(box.clientWidth, 320);
  const H = 260;
  const m = { l: 48, r: 72, t: 10, b: 28 };
  const [pw, ph] = [W - m.l - m.r, H - m.t - m.b];
  const peak = Math.max(0, ...months.map((key) => cfg.points.get(key)?.value ?? 0));
  const levels = ['n1', 'n2', 'n3']
    .map((key) => ({ key, value: cfg.levels[key] }))
    .filter((level) => level.value != null);
  // Un palier tres au-dessus des colonnes ecraserait le trace : il reste lu dans la legende.
  const ceiling = Math.max(peak, levels[0]?.value ?? 0) * 2.5;
  const drawn = levels.filter((level) => level.value <= ceiling);
  const scale = niceScale(Math.max(peak, ...drawn.map((level) => level.value), 1));
  const band = pw / months.length;
  const width = Math.min(24, band * 0.62);
  const x = (i) => m.l + band * (i + 0.5);
  const y = (v) => m.t + ph * (1 - v / scale.max);
  const svg = s('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': cfg.summary });

  const reference = new Set(data.meta.ref.mois);
  let start = null;
  months.forEach((key, i) => {
    if (reference.has(key) && start == null) start = i;
    const closes = start != null && (!reference.has(months[i + 1]) || i === months.length - 1);
    if (!closes) return;
    svg.append(s('rect', { class: 'band', x: m.l + band * start, y: m.t, width: band * (i - start + 1), height: ph }));
    start = null;
  });

  for (const tick of scale.ticks) {
    if (tick > 0) svg.append(s('line', { class: 'gridline', x1: m.l, x2: m.l + pw, y1: y(tick), y2: y(tick) }));
    svg.append(s('text', { x: m.l - 8, y: y(tick) + 4, 'text-anchor': 'end' }, int(tick)));
  }
  svg.append(s('line', { class: 'baseline', x1: m.l, x2: m.l + pw, y1: y(0), y2: y(0) }));

  const bars = new Map();
  months.forEach((key, i) => {
    const point = cfg.points.get(key);
    if (key === data.meta.mois_controle) {
      svg.append(s('rect', { class: 'now-mark', x: x(i) - width / 2, y: y(0) + 3, width, height: 2 }));
    }
    if (!point || !(point.value > 0)) return;
    const bar = s('path', { class: `bar ${point.niveau}`, d: column(x(i), width, y(point.value), y(0)) });
    bars.set(key, bar);
    svg.append(bar);
  });

  let lastLabel = Infinity;
  for (const level of [...drawn].reverse()) {
    const at = y(level.value);
    svg.append(s('line', { class: 'thr', x1: m.l, x2: m.l + pw, y1: at, y2: at }));
    if (Math.abs(at - lastLabel) < 13) continue;
    svg.append(s('text', { class: 'thr-label', x: m.l + pw + 6, y: at + 4 }, `${level.key.toUpperCase()} ${int(level.value)}`));
    lastLabel = at;
  }

  monthAxis(svg, months, x, band, H - 8, data.meta.mois_controle);

  months.forEach((key, i) => {
    const point = cfg.points.get(key);
    if (!point) return;
    const lines = [
      monthLabel(key),
      `${int(point.value)} ${cfg.unit}`,
      cfg.count(point),
      levelOf(point.niveau).label,
    ];
    const tip = () => [
      h('span', { class: 't-title', text: lines[0] }),
      h('b', { text: lines[1] }),
      h('div', { text: lines[2] }),
      h('div', { text: lines[3] }),
    ];
    const hit = s('rect', {
      class: 'hit', x: m.l + band * i, y: m.t, width: band, height: ph,
      tabindex: 0, 'aria-label': lines.join(', '),
    });
    const leave = () => { bars.get(key)?.classList.remove('on'); hideTip(); };
    hit.addEventListener('pointermove', (event) => {
      bars.get(key)?.classList.add('on');
      showTip(tip(), event.clientX, event.clientY);
    });
    hit.addEventListener('pointerleave', leave);
    hit.addEventListener('focus', () => {
      const rect = hit.getBoundingClientRect();
      bars.get(key)?.classList.add('on');
      showTip(tip(), rect.left + rect.width / 2, rect.top + 24);
    });
    hit.addEventListener('blur', leave);
    svg.append(hit);
  });
  box.replaceChildren(svg);
}

function barLegend(points, levels) {
  const present = new Set([...points.values()].map((point) => point.niveau));
  const keys = ['n3', 'n2', 'n1', 'below', 'few', 'fewc', 'none'].filter((code) => present.has(code));
  const thresholds = ['n1', 'n2', 'n3'].filter((key) => levels[key] != null)
    .map((key) => `${key.toUpperCase()} ${int(levels[key])}`).join(' · ');
  return h('div', { class: 'legend' },
    keys.map((code) => h('span', { class: 'key' }, h('i', { class: `k-${code}` }), LEVELS[code].label)),
    thresholds && h('span', { class: 'key' }, h('i', { class: 'dash' }), `Paliers : ${thresholds}`),
    h('span', { class: 'key' }, h('i', { class: 'shade' }), 'Période de référence'),
  );
}

/* Emphase : seules les series dont le comptage a change sont colorees. */
function drawLines(box, cfg) {
  const { months, series } = cfg;
  const W = Math.max(box.clientWidth, 320);
  const H = 280;
  const m = { l: 40, r: 140, t: 12, b: 28 };
  const [pw, ph] = [W - m.l - m.r, H - m.t - m.b];
  const peak = Math.max(0, ...series.flatMap((serie) => [...serie.points.values()].map((p) => p.ratio)));
  const scale = niceScale(peak, 5);
  const gap = months.length > 1 ? pw / (months.length - 1) : 0;
  const x = (i) => (months.length > 1 ? m.l + gap * i : m.l + pw / 2);
  const y = (v) => m.t + ph * (1 - v / scale.max);
  const svg = s('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': cfg.summary });

  for (const tick of scale.ticks) {
    if (tick > 0) svg.append(s('line', { class: 'gridline', x1: m.l, x2: m.l + pw, y1: y(tick), y2: y(tick) }));
    svg.append(s('text', { x: m.l - 8, y: y(tick) + 4, 'text-anchor': 'end' }, nf0.format(tick)));
  }
  svg.append(s('line', { class: 'baseline', x1: m.l, x2: m.l + pw, y1: y(0), y2: y(0) }));
  monthAxis(svg, months, x, gap || pw, H - 8, null);

  const cross = s('line', { class: 'cross', y1: m.t, y2: m.t + ph, visibility: 'hidden' });
  svg.append(cross);

  const ends = [];
  // Les series neutres d'abord : les series mises en avant passent au-dessus.
  for (const serie of [...series].sort((a, b) => Boolean(a.slot) - Boolean(b.slot))) {
    const tone = serie.slot ? ` c${serie.slot}` : '';
    let path = '';
    let previous = -2;
    months.forEach((key, i) => {
      const point = serie.points.get(key);
      if (!point) return;
      path += `${previous === i - 1 ? 'L' : 'M'}${x(i)},${y(point.ratio)}`;
      previous = i;
    });
    svg.append(s('path', { class: `serie${tone}`, d: path }));
    months.forEach((key, i) => {
      const point = serie.points.get(key);
      if (point) svg.append(s('circle', { class: `dot${tone}`, cx: x(i), cy: y(point.ratio), r: 4 }));
    });
    if (serie.slot && previous >= 0) {
      ends.push({ name: serie.name, x: x(previous) + 10, y: y(serie.points.get(months[previous]).ratio) + 4 });
    }
  }
  ends.sort((a, b) => a.y - b.y);
  ends.forEach((end, i) => {
    if (i && end.y - ends[i - 1].y < 14) end.y = ends[i - 1].y + 14;
    svg.append(s('text', { class: 'end-label', x: end.x, y: end.y }, end.name));
  });

  const overlay = s('rect', {
    class: 'hit', x: m.l - gap / 2, y: m.t, width: pw + gap, height: ph,
    tabindex: 0, 'aria-label': cfg.summary,
  });
  let current = months.length - 1;
  const show = (i, clientX, clientY) => {
    current = Math.min(months.length - 1, Math.max(0, i));
    const key = months[current];
    cross.setAttribute('x1', x(current));
    cross.setAttribute('x2', x(current));
    cross.setAttribute('visibility', 'visible');
    const rows = series
      .filter((serie) => serie.points.has(key))
      .sort((a, b) => b.points.get(key).ratio - a.points.get(key).ratio)
      .map((serie) => h('div', { class: 't-row' },
        h('span', { class: `t-key${serie.slot ? ` c${serie.slot}` : ''}` }),
        h('strong', { text: nf2.format(serie.points.get(key).ratio) }),
        h('span', { text: serie.name })));
    showTip([h('span', { class: 't-title', text: monthLabel(key) }), ...rows], clientX, clientY);
  };
  const hide = () => { cross.setAttribute('visibility', 'hidden'); hideTip(); };
  const anchor = () => {
    const rect = overlay.getBoundingClientRect();
    return [rect.left + (gap ? (x(current) - m.l + gap / 2) : rect.width / 2), rect.top + 24];
  };
  overlay.addEventListener('pointermove', (event) => {
    const rect = svg.getBoundingClientRect();
    show(gap ? Math.round((event.clientX - rect.left - m.l) / gap) : 0, event.clientX, event.clientY);
  });
  overlay.addEventListener('pointerleave', hide);
  overlay.addEventListener('focus', () => show(current, ...anchor()));
  overlay.addEventListener('blur', hide);
  overlay.addEventListener('keydown', (event) => {
    const move = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    if (!move) return;
    event.preventDefault();
    current = Math.min(months.length - 1, Math.max(0, current + move));
    show(current, ...anchor());
  });
  svg.append(overlay);
  box.replaceChildren(svg);
}

/* --- Vue d'ensemble -------------------------------------------------------- */
function tile(label, value, sub, meter) {
  return h('div', { class: 'tile' },
    h('span', { class: 'tile-label', text: label }),
    h('span', { class: 'tile-value', text: value }),
    h('span', { class: 'tile-sub', text: sub ?? '' }),
    meter,
  );
}

function versusPrevious(field) {
  const { mois, mois_controle: current } = data.meta;
  const previous = mois[mois.indexOf(current) - 1];
  if (!previous) return '';
  const sum = (month) => data.historique.filter((r) => r.mois === month).reduce((total, r) => total + r[field], 0);
  const before = sum(previous);
  if (!(before > 0)) return '';
  return `${signed((100 * (sum(current) - before)) / before)} par rapport à ${monthLabel(previous)}`;
}

function monthGrid(rows) {
  const { mois, mois_controle: current } = data.meta;
  const groups = new Map();
  for (const row of rows) {
    const key = keyOf(row);
    if (!groups.has(key)) groups.set(key, { row, cells: new Map() });
    groups.get(key).cells.set(row.mois, row);
  }
  if (!groups.size) return h('p', { class: 'empty', text: 'Aucune publication dans ce périmètre.' });
  const head = h('tr', {},
    h('th', { scope: 'col', text: 'Compte et format' }),
    mois.map((key) => h('th', { scope: 'col', 'aria-current': key === current ? 'true' : null, text: monthShort(key) })));
  const body = [...groups.values()].map(({ row, cells }) => h('tr', {},
    h('th', { scope: 'row' },
      h('button', {
        class: 'link', type: 'button',
        onclick: () => goTo('compte', { client: row.client, groupe: keyOf(row) }),
      }, row.compte),
      h('span', { class: 'sub', text: ` · ${platformLabel(row.plateforme)} · ${formatLabel(row.format)}` })),
    mois.map((key) => {
      const cell = cells.get(key);
      if (!cell) return h('td', {});
      const title = `${monthLabel(key)} : médiane ${int(cell.mediane)} vues, `
        + `${plural(cell.publications, 'publication', 'publications')} — ${levelOf(cell.niveau).label}`;
      return h('td', { class: `c-${cell.niveau}`, title, text: levelOf(cell.niveau).short });
    })));
  return h('div', { class: 'grid-wrap' },
    h('table', { class: 'hm' },
      h('caption', { text: 'Niveau atteint par compte et par format, mois par mois' }),
      h('thead', {}, head), h('tbody', {}, body)));
}

function viewOverview() {
  const month = monthLabel(data.meta.mois_controle);
  const all = data.resultats;
  const evaluated = all.filter((row) => EVALUATED.has(row.niveau));
  const reached = evaluated.filter((row) => REACHED.has(row.niveau)).length;
  const active = new Set(all.filter((row) => row.publications > 0).map((row) => `${row.compte}${SEP}${row.plateforme}`));
  const views = all.reduce((total, row) => total + row.vues, 0);
  const meter = h('div', { class: 'meter', 'aria-hidden': 'true' }, h('span'));
  meter.firstChild.style.width = evaluated.length ? `${(100 * reached) / evaluated.length}%` : '0';

  const tiles = h('div', { class: 'tiles' },
    tile('Publications du mois', int(all.reduce((total, row) => total + row.publications, 0)), versusPrevious('publications')),
    tile('Vues organiques', views >= 10000 ? nfCompact.format(views) : int(views), versusPrevious('vues')),
    tile('Comptes actifs', int(active.size), 'un compte par plateforme'),
    tile('Au Niveau 1 ou plus', `${reached} / ${evaluated.length}`, 'comptes × formats évalués ce mois', meter),
  );

  const scope = (rows) => rows.filter((row) =>
    (!state.fCat || row.secteur === state.fCat) && (!state.fPlat || row.plateforme === state.fPlat));
  const rows = scope(all).filter((row) => !state.fEval || EVALUATED.has(row.niveau));
  const columns = pick('compte', 'secteur', 'plateforme', 'format', 'publications', 'mediane',
    'n1', 'n2', 'n3', 'ecart', 'att1', 'niveau');

  const categorySelect = h('select', { onchange: (e) => { state.fCat = e.target.value; render(); } },
    h('option', { value: '', text: 'Toutes' }),
    index.sectors.map((sector) => h('option', { value: sector, text: sector })));
  categorySelect.value = state.fCat;
  const platformSelect = h('select', { onchange: (e) => { state.fPlat = e.target.value; render(); } },
    h('option', { value: '', text: 'Toutes' }),
    Object.keys(PLATFORMS).map((code) => h('option', { value: code, text: PLATFORMS[code] })));
  platformSelect.value = state.fPlat;
  const onlyEvaluated = h('input', { type: 'checkbox', onchange: (e) => { state.fEval = e.target.checked; render(); } });
  onlyEvaluated.checked = state.fEval;

  const filters = h('div', { class: 'row' },
    h('label', { class: 'field' }, h('span', { text: 'Catégorie' }), categorySelect),
    h('label', { class: 'field' }, h('span', { text: 'Plateforme' }), platformSelect),
    h('label', { class: 'check' }, onlyEvaluated, 'Lignes évaluées seulement'),
  );

  return [
    tiles,
    h('section', {},
      h('div', { class: 'section-head' },
        h('div', {},
          h('h2', { text: `Résultats de ${month}` }),
          h('p', { class: 'hint', text: 'Une ligne par compte et par format. Le niveau compare la médiane des vues organiques du mois aux paliers du compte.' })),
        h('button', {
          class: 'btn', type: 'button',
          onclick: () => downloadCSV(`controle_${data.meta.mois_controle}.csv`, columns, sortRows('ensemble', columns, rows)),
        }, icon('download'), 'Exporter en CSV')),
      filters,
      dataTable('ensemble', `Résultats de ${month}`, columns, rows)),
    h('section', {},
      h('h2', { text: 'Mois par mois' }),
      h('p', { class: 'hint', text: 'N1, N2, N3 : palier atteint par la médiane du mois. « peu » : trop peu de publications. « – » : pas d\'objectif. Case vide : aucune publication.' }),
      monthGrid(scope(data.historique))),
  ];
}

/* --- Par compte ------------------------------------------------------------ */
function postsTable(row) {
  const box = h('div', {}, h('p', { class: 'muted', text: 'Chargement des publications…' }));
  const palier = (views) => {
    if (row.n1 == null) return badge('none', '–');
    if (views >= row.n3) return badge('n3', '≥ N3');
    if (views >= row.n2) return badge('n2', '≥ N2');
    if (views >= row.n1) return badge('n1', '≥ N1');
    return badge('below', '< N1');
  };
  const columns = [
    { key: 'date', label: 'Date', text: (p) => `${dayLabel(p.date)} ${p.date.slice(11, 16)}` },
    { key: 'vues', label: 'Vues', num: true, text: (p) => int(p.vues) },
    { key: 'couverture', label: 'Couverture', num: true, text: (p) => int(p.couverture) },
    { key: 'engagement', label: 'Engagement', num: true, text: (p) => (p.engagement == null ? '' : `${nf1.format(p.engagement)} %`) },
    { key: 'palier', label: 'Palier', sort: (p) => p.vues, cell: (p) => palier(p.vues) },
    {
      key: 'lien', label: 'Lien',
      cell: (p) => (/^https?:\/\//.test(p.lien ?? '')
        ? h('a', { href: p.lien, target: '_blank', rel: 'noopener noreferrer', class: 'link' }, 'Ouvrir')
        : ''),
    },
    { key: 'texte', label: 'Texte', wrap: true, text: (p) => p.texte ?? '' },
  ];
  fetchPosts(row).then((posts) => {
    if (!box.isConnected) return;
    box.replaceChildren(dataTable('publications', 'Publications du mois', columns, posts,
      { sortable: false, empty: 'Aucune publication ce mois-ci.' }));
  }).catch((error) => {
    if (box.isConnected) box.replaceChildren(h('p', { class: 'empty', text: error.message }));
  });
  return box;
}

function viewAccount() {
  if (!index.clients.includes(state.client)) state.client = index.clients[0];
  const groups = [...index.groups.entries()]
    .filter(([, rows]) => rows[0].client === state.client)
    .map(([key, rows]) => ({ key, rows, total: rows.reduce((sum, row) => sum + row.publications, 0) }))
    .sort((a, b) => b.total - a.total);
  if (!groups.some((group) => group.key === state.groupe)) state.groupe = groups[0]?.key ?? null;
  const group = groups.find((g) => g.key === state.groupe);
  const month = monthLabel(data.meta.mois_controle);

  const clientSelect = h('select', { onchange: (e) => { state.client = e.target.value; state.groupe = null; render(); } },
    index.clients.map((client) => h('option', { value: client, text: client })));
  clientSelect.value = state.client;

  const select = { ...COLUMNS.compte,
    cell: (r) => h('button', {
      class: 'link', type: 'button', 'aria-pressed': String(keyOf(r) === state.groupe),
      onclick: () => { state.groupe = keyOf(r); render(); },
    }, r.compte),
  };
  const columns = [select, ...pick('plateforme', 'format', 'publications', 'mediane', 'n1', 'n2', 'n3', 'ecart', 'att1', 'niveau')];
  const summary = h('section', {},
    h('div', { class: 'row' }, h('label', { class: 'field' }, h('span', { text: 'Client' }), clientSelect)),
    h('h2', { text: `${state.client} : ${month}` }),
    dataTable('client', `Résultats de ${state.client}`, columns,
      data.resultats.filter((row) => row.client === state.client),
      { current: (row) => keyOf(row) === state.groupe }));
  if (!group) return [summary];

  const first = group.rows[0];
  const levels = { n1: first.n1, n2: first.n2, n3: first.n3 };
  const points = new Map(group.rows.map((row) => [row.mois, { value: row.mediane, publications: row.publications, niveau: row.niveau }]));
  const now = points.get(data.meta.mois_controle);
  const chart = h('figure', { class: 'figure' },
    barLegend(points, levels),
    chartBox((box) => drawBars(box, {
      points, levels, unit: 'vues médianes',
      count: (point) => plural(point.publications, 'publication', 'publications'),
      summary: `Médiane mensuelle des vues organiques de ${groupLabel(first)}. ${month} : `
        + (now ? `${int(now.value)} vues, ${levelOf(now.niveau).label}.` : 'aucune publication.'),
    })));

  const monthsTable = dataTable('mois', `Historique mensuel de ${groupLabel(first)}`,
    pick('mois', 'publications', 'mediane', 'niveau', 'att1', 'att2', 'att3'),
    [...group.rows].reverse(), { current: (row) => row.mois === data.meta.mois_controle });
  const current = data.resultats.find((row) => keyOf(row) === group.key) ?? first;

  return [
    summary,
    h('section', {},
      h('h2', { text: groupLabel(first) }),
      h('p', { class: 'hint', text: 'Médiane mensuelle des vues organiques. Le mois contrôlé est souligné sur l\'axe.' }),
      chart),
    h('section', {}, h('h3', { text: 'Mois par mois' }), monthsTable),
    h('section', {}, h('h3', { text: `Publications de ${month}` }), postsTable(current)),
  ];
}

/* --- Par categorie --------------------------------------------------------- */
function viewCategory() {
  if (!index.sectors.includes(state.secteur)) state.secteur = index.sectors[0];
  const month = monthLabel(data.meta.mois_controle);
  const history = data.categories.filter((row) => row.secteur === state.secteur);
  const current = history.filter((row) => row.mois === data.meta.mois_controle);

  const sectorSelect = h('select', { onchange: (e) => { state.secteur = e.target.value; state.paire = null; render(); } },
    index.sectors.map((sector) => h('option', { value: sector, text: sector })));
  sectorSelect.value = state.secteur;

  const columns = [
    ...pick('plateforme', 'format'),
    { key: 'comptes_actifs', label: 'Comptes actifs', num: true, text: (r) => int(r.comptes_actifs) },
    { key: 'comptes_evalues', label: 'Comptes évalués', num: true, text: (r) => int(r.comptes_evalues) },
    COLUMNS.publications,
    { ...COLUMNS.mediane, label: 'Médiane des comptes' },
    ...pick('n1', 'n2', 'n3', 'niveau'),
    {
      key: 'comptes_n1', label: 'Comptes à leur N1', num: true,
      sort: (r) => (r.comptes_objectif ? r.comptes_n1 / r.comptes_objectif : null),
      text: (r) => `${r.comptes_n1} / ${r.comptes_objectif}`,
    },
  ];

  const notes = []; 
  const mixed = current.filter((row) => row.heterogene); 

  const pairs = new Map();
  for (const row of history) {
    const key = `${row.plateforme}|${row.format}`;
    pairs.set(key, (pairs.get(key) ?? 0) + row.publications);
  }
  const pairKeys = [...pairs.keys()].sort((a, b) => pairs.get(b) - pairs.get(a));
  if (!pairKeys.includes(state.paire)) state.paire = pairKeys[0] ?? null;
  const sections = [
    h('section', {},
      h('div', { class: 'row' }, h('label', { class: 'field' }, h('span', { text: 'Catégorie' }), sectorSelect)),
      h('h2', { text: `${state.secteur} : ${month}` }),
      h('p', { class: 'hint', text: 'La valeur d\'une catégorie est la médiane des médianes de ses comptes : chaque compte pèse pour un, quel que soit son volume. « Comptes à leur N1 » compte ceux qui atteignent leur propre Niveau 1.' }),
      dataTable('categorie', `Résultats de la catégorie ${state.secteur}`, columns, current,
        { empty: 'Aucune publication dans cette catégorie ce mois-ci.' }),
      notes.map((note) => alertBox('warn', 'À savoir', h('span', { text: note })))),
    h('section', {},
      h('h2', { text: 'Comptes de la catégorie' }),
      dataTable('comptes-categorie', `Comptes de la catégorie ${state.secteur}`,
        pick('compte', 'plateforme', 'format', 'publications', 'mediane', 'n1', 'n2', 'n3', 'ecart', 'att1', 'niveau'),
        data.resultats.filter((row) => row.secteur === state.secteur))),
  ];
  if (!state.paire) return sections;

  const [platform, format] = state.paire.split('|');
  const rows = history.filter((row) => row.plateforme === platform && row.format === format);
  const levels = { n1: rows[0].n1, n2: rows[0].n2, n3: rows[0].n3 };
  const points = new Map(rows.map((row) => [row.mois, { value: row.mediane, comptes: row.comptes_evalues, niveau: row.niveau }])
    .filter(([, point]) => point.value != null));
  const pairSelect = h('select', { onchange: (e) => { state.paire = e.target.value; render(); } },
    pairKeys.map((key) => {
      const [p, f] = key.split('|');
      return h('option', { value: key, text: `${platformLabel(p)} · ${formatLabel(f)}` });
    }));
  pairSelect.value = state.paire;
  const title = `${state.secteur} · ${platformLabel(platform)} · ${formatLabel(format)}`;
  sections.push(h('section', {},
    h('div', { class: 'section-head' },
      h('h2', { text: 'Évolution de la catégorie' }),
      h('label', { class: 'field' }, h('span', { text: 'Plateforme et format' }), pairSelect)),
    h('figure', { class: 'figure' },
      barLegend(points, levels),
      chartBox((box) => drawBars(box, {
        points, levels, unit: 'vues (médiane des comptes)',
        count: (point) => plural(point.comptes, 'compte évalué', 'comptes évalués'),
        summary: `Médiane mensuelle des comptes, ${title}.`,
      })))));
  return sections;
}

/* --- Donnees --------------------------------------------------------------- */
function ratioSection() {
  const months = data.meta.ref.mois;
  const flagged = new Set(data.ruptures.map((r) => `${r.plateforme}|${r.format}`));
  const byKey = new Map();
  for (const row of data.ratios) {
    const key = `${row.plateforme}|${row.format}`;
    if (!byKey.has(key)) {
      byKey.set(key, {
        key, name: `${platformLabel(row.plateforme)} · ${formatLabel(row.format)}`,
        slot: flagged.has(key) ? SERIES_SLOTS[key] ?? null : null, points: new Map(),
      });
    }
    byKey.get(key).points.set(row.mois, row);
  }
  const series = [...byKey.values()].sort((a, b) => a.name.localeCompare(b.name, 'fr'));
  const head = h('div', {},
    h('h2', { text: 'Comptage des vues' }),
    h('p', { class: 'hint', text: 'Nombre médian de vues par personne touchée, sur la période de référence. Ce ratio ne dépend pas de la taille de l\'audience : une marche d\'escalier signale un changement dans la façon de compter les vues, pas une variation de performance.' }));
  if (!series.length) {
    return h('section', {}, head, h('p', { class: 'empty', text: 'Pas assez de publications avec couverture pour tracer ce contrôle.' }));
  }

  const coloured = series.filter((serie) => serie.slot);
  const legend = h('div', { class: 'legend' },
    coloured.map((serie) => h('span', { class: 'key' }, h('i', { class: `line c${serie.slot}` }), serie.name)),
    coloured.length < series.length && h('span', { class: 'key' }, h('i', { class: 'line' }),
      coloured.length ? 'Séries stables' : 'Aucun changement de comptage détecté'));
  const tableRows = months.map((key) => ({ mois: key, ...Object.fromEntries(series.map((serie) => [serie.key, serie.points.get(key)?.ratio ?? null])) }));
  const tableColumns = [COLUMNS.mois, ...series.map((serie) => ({
    key: serie.key, label: serie.name, num: true, text: (row) => (row[serie.key] == null ? '' : nf2.format(row[serie.key])),
  }))];

  return h('section', {}, head,
    h('figure', { class: 'figure' }, legend,
      chartBox((box) => drawLines(box, {
        months, series,
        summary: `Vues par personne touchée, ${series.length} séries sur ${months.length} mois.`
          + (coloured.length ? ` Séries dont le comptage a changé : ${coloured.map((serie) => serie.name).join(', ')}.` : ''),
      })),
      h('details', {}, h('summary', { text: 'Voir les valeurs' }),
        dataTable('ratios', 'Vues par personne touchée, par mois', tableColumns, tableRows, { sortable: false }))));
}

function viewData() {
  const { donnees } = data;
  const clean = donnees.nettoyage;
  const files = dataTable('fichiers', 'Exports chargés', [
    { key: 'nom', label: 'Fichier' },
    COLUMNS.plateforme,
    { key: 'publications', label: 'Publications retenues', num: true, text: (f) => int(f.publications) },
    { key: 'debut', label: 'Du', text: (f) => dayLabel(f.debut) },
    { key: 'fin', label: 'Au', text: (f) => dayLabel(f.fin) },
    { key: 'colonnes_utilisees', label: 'Colonnes utilisées', num: true, text: (f) => `${f.colonnes_utilisees} / ${f.colonnes}` },
  ], donnees.fichiers);

  const fact = (label, value) => (value == null ? null : h('div', {}, h('dt', { text: label }), h('dd', { text: value })));
  const alerts = [];
  if (donnees.ignores.length) {
    alerts.push(alertBox('warn', 'Fichiers ignorés', h('span', { text: `Format non reconnu : ${donnees.ignores.join(', ')}.` })));
  }
  if (donnees.sans_categorie.length) {
    alerts.push(alertBox('warn', 'Comptes sans catégorie', h('span', {
      text: `À déclarer dans config/clients.csv : ${donnees.sans_categorie.join(', ')}.`,
    })));
  }

  return [
    h('section', {},
      h('h2', { text: 'Exports chargés' }),
      h('p', { class: 'hint', text: `${donnees.dossier ? `Dossier : ${donnees.dossier}. ` : ''}« Publications retenues » : organiques, dédupliquées, sur des comptes suffisamment alimentés.` }),
      files, alerts),
    h('section', {},
      h('h2', { text: 'Nettoyage' }),
      h('dl', { class: 'facts' },
        fact('Lignes lues', int(clean.lignes_lues)),
        fact('Doublons fusionnés', int(clean.doublons)),
        fact('Lignes en quarantaine', int(clean.quarantaine)),
        fact('Publications sponsorisées exclues', int(clean.sponsorisees)),
        fact(`Comptes écartés (moins de ${clean.seuil_compte} publications)`, int(clean.comptes_ecartes.length)),
        fact('Publications retenues', int(clean.retenues))),
      clean.comptes_ecartes.length
        ? h('p', { class: 'hint', text: `Comptes écartés : ${clean.comptes_ecartes.join(', ')}.` })
        : null),
    ratioSection(),
  ];
}

/* --- Rendu ----------------------------------------------------------------- */
function statePanel(title, text, label, action) {
  return h('div', { class: 'empty' },
    h('h2', { text: title }),
    h('p', { class: 'hint', text }),
    h('p', {}, h('button', { class: 'btn btn-primary', type: 'button', onclick: action }, label)));
}

function drawCharts() {
  for (const draw of charts) draw();
}

function render() {
  hideTip();
  charts = [];
  renderControls();
  renderAlerts();
  renderTabs();
  const main = $('contenu');
  if (failure) {
    main.replaceChildren(statePanel('Les résultats ne peuvent pas être affichés', failure.message, 'Réessayer', load));
    return;
  }
  if (!data) return;
  if (data.vide) {
    main.replaceChildren(REMOTE
      ? statePanel('Aucun résultat publié',
        'Les résultats apparaîtront ici après la prochaine publication.', 'Actualiser', load)
      : data.import_autorise === false
      ? statePanel('Aucun export chargé',
        "Les exports s'ajoutent depuis le poste qui héberge l'interface.", 'Actualiser', load)
      : statePanel('Aucun export chargé',
        'Ajoutez vos exports Meta Business Suite (CSV) pour calculer les objectifs et contrôler un mois.',
        'Ajouter des exports', () => $('fichiers').click()));
    return;
  }
  const views = { ensemble: viewOverview, compte: viewAccount, categorie: viewCategory, donnees: viewData };
  main.replaceChildren(...views[state.vue]());
  writeURL();
  drawCharts();
}

async function upload(files) {
  if (!files.length) return;
  $('importer').disabled = true;
  setBusy('Import en cours…');
  imports = [];
  for (const file of files) {
    if (file.size > MAX_UPLOAD) {
      imports.push({ nom: file.name, statut: 'rejected', detail: 'fichier trop volumineux (50 Mo maximum)' });
      continue;
    }
    try {
      const response = await fetch(`/api/import?nom=${encodeURIComponent(file.name)}`, {
        method: 'POST',
        headers: { 'X-Requested-With': 'socialstats', 'Content-Type': 'text/csv' },
        body: file,
      });
      const body = await response.json().catch(() => null);
      imports.push(response.ok && body
        ? body
        : { nom: file.name, statut: 'rejected', detail: body?.erreur ?? `erreur ${response.status}` });
    } catch {
      imports.push({ nom: file.name, statut: 'rejected', detail: 'le serveur local ne répond pas' });
    }
  }
  $('fichiers').value = '';
  $('importer').disabled = false;
  await load();
}

function init() {
  readURL();
  // La periode personnalisee demande un calcul a la demande : elle reste locale.
  if (REMOTE) $('ref').querySelector('[value="custom"]').remove();
  $('importer').prepend(icon('upload'));
  $('mois').addEventListener('change', (e) => { state.mois = e.target.value; load(); });
  $('ref').addEventListener('change', (e) => {
    state.ref = e.target.value;
    if (state.ref === 'custom' && data && !data.vide) {
      state.debut = data.meta.ref.debut;
      state.fin = data.meta.ref.fin;
    }
    load();
  });
  $('debut').addEventListener('change', (e) => { state.debut = e.target.value; state.fin = $('fin').value; load(); });
  $('fin').addEventListener('change', (e) => { state.fin = e.target.value; state.debut = $('debut').value; load(); });
  $('min').addEventListener('change', (e) => {
    state.min = Math.min(30, Math.max(1, Math.round(Number(e.target.value)) || 3));
    load();
  });
  $('importer').addEventListener('click', () => $('fichiers').click());
  $('fichiers').addEventListener('change', (e) => upload([...e.target.files]));

  const tabs = [...document.querySelectorAll('[role="tab"]')];
  for (const tab of tabs) {
    tab.addEventListener('click', () => goTo(tab.dataset.vue));
    tab.addEventListener('keydown', (event) => {
      const move = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
      if (!move) return;
      const next = tabs[(tabs.indexOf(tab) + move + tabs.length) % tabs.length];
      next.focus();
      goTo(next.dataset.vue);
    });
  }

  window.addEventListener('popstate', () => { readURL(); load(); });
  let width = 0;
  new ResizeObserver(debounce(() => {
    const now = $('contenu').clientWidth;
    if (now === width) return;
    width = now;
    drawCharts();
  }, 120)).observe($('contenu'));

  renderTabs();
  load();
}

init();
