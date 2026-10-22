// A DOM stub just wide enough to run the network page's script.
//
// It is deliberately dumb: it records what the page did, so a test can
// assert on behaviour rather than on the markup. Two things it has to get
// right, because getting them wrong produces failures that look like
// application bugs:
//
//   - classList.toggle(c, force) must honour the force argument.
//   - querySelectorAll('.x') must match on class *containment*, so an
//     element that gains a second class still matches.
'use strict';

class ClassList {
  constructor() { this.set = new Set(); }
  add(...names) { names.forEach(n => this.set.add(n)); }
  remove(...names) { names.forEach(n => this.set.delete(n)); }
  contains(name) { return this.set.has(name); }
  toggle(name, force) {
    if (force === undefined) force = !this.set.has(name);
    if (force) this.set.add(name); else this.set.delete(name);
    return force;
  }
}

class El {
  constructor(tag) {
    this.tagName = (tag || 'div').toUpperCase();
    this.children = [];
    this.parent = null;
    this.attributes = {};
    this.listeners = {};
    this._text = '';
    this._html = '';
    this.style = {};
    this.dataset = {};
    this.classList = new ClassList();
    this.hidden = false;
    this.disabled = false;
    this.value = '';
    this.checked = false;
    this.namespaceURI = '';
  }
  get className() { return Array.from(this.classList.set).join(' '); }
  set className(v) {
    this.classList.set = new Set(String(v).split(/\s+/).filter(Boolean));
  }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); this.children = []; }
  get id() { return this.attributes.id || ''; }
  set id(v) { this.attributes.id = v; }
  setAttribute(k, v) {
    this.attributes[k] = String(v);
    if (k === 'id') this.id = v;
    if (k === 'class') this.className = v;
    if (k.startsWith('data-')) this.dataset[k.slice(5)] = String(v);
  }
  getAttribute(k) { return this.attributes[k]; }
  hasAttribute(k) { return k in this.attributes; }
  removeAttribute(k) { delete this.attributes[k]; }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  removeEventListener(type, fn) {
    const list = this.listeners[type] || [];
    const i = list.indexOf(fn);
    if (i >= 0) list.splice(i, 1);
  }
  dispatch(type, event) {
    const e = event || {type, preventDefault() {}, stopPropagation() {}};
    (this.listeners[type] || []).forEach(fn => fn.call(this, e));
  }
  contains(node) {
    if (node === this) return true;
    return this.children.some(c => c.contains(node));
  }
  closest() { return null; }
  getBoundingClientRect() {
    return {x: 0, y: 0, width: 0, height: 0, top: 0, left: 0};
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    // Supports "#id", ".class", "tag", "[attr]" and compound selectors.
    const parts = sel.match(/\[[^\]]+\]|[.#]?[\w-]+/g) || [];
    const out = [];
    const walk = (node) => {
      for (const child of node.children) {
        let ok = true;
        for (const p of parts) {
          if (p[0] === '[') {
            if (!child.hasAttribute(p.slice(1, -1))) { ok = false; break; }
          } else if (p[0] === '#') {
            if (child.id !== p.slice(1)) { ok = false; break; }
          } else if (p[0] === '.') {
            if (!child.classList.contains(p.slice(1))) { ok = false; break; }
          } else if (child.tagName !== p.toUpperCase()) {
            ok = false; break;
          }
        }
        if (ok) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
  get ownerDocument() { return this._doc; }
  // The page builds table cells with createElement + textContent and then
  // reads .outerHTML, so the stub has to serialise like a browser.
  get outerHTML() {
    const attrs = Object.entries(this.attributes)
      .map(([k, v]) => ' ' + k + '="' +
        String(v).replace(/&/g, '&amp;').replace(/"/g, '&quot;') + '"')
      .join('');
    const body = this._text.replace(/&/g, '&amp;')
      .replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const tag = this.tagName.toLowerCase();
    return '<' + tag + attrs + '>' + body + '</' + tag + '>';
  }
}

// The data the page fetches, and the elements it writes to.
const FIXTURE = {
  '/network/summary': {
    hostname: {hostname: 'kourosh-pc', domain: ''},
    interfaces: 4, routes: 4, listening_ports: 4,
    dns_servers: ['127.0.0.1'], source: 'the host',
  },
  '/network/interfaces': [
    {name: 'lo', state: 'UNKNOWN', mac: '00:00:00:00:00:00', ipv4: ['127.0.0.1'],
     ipv6: ['::1'], mtu: 65536, rx_bytes: 100, tx_bytes: 100},
    {name: 'eno1', state: 'UP', mac: 'aa:bb:cc:dd:ee:ff', ipv4: ['192.168.1.13'],
     ipv6: [], mtu: 1500, rx_bytes: 5000, tx_bytes: 6000},
    {name: 'br-10b47f13e607', state: 'UP', mac: '11:22:33:44:55:66',
     ipv4: ['172.22.0.1'], ipv6: [], mtu: 1500, rx_bytes: 0, tx_bytes: 0},
    {name: 'veth0fe2367', state: 'UP', mac: '77:88:99:aa:bb:cc', ipv4: [],
     ipv6: [], mtu: 1500, rx_bytes: 126, tx_bytes: 900},
  ],
  '/network/routes': [
    {dst: '0.0.0.0/0', gateway: '192.168.1.1', dev: 'eno1', protocol: 'kernel',
     metric: 100, scope: 'global', family: 'inet'},
    {dst: '172.22.0.0/16', gateway: '', dev: 'br-10b47f13e607', protocol: 'kernel',
     metric: 0, scope: 'global', family: 'inet'},
    {dst: '::/0', gateway: '', dev: 'lo', protocol: 'kernel', metric: 1,
     scope: 'link', family: 'inet6'},
    {dst: 'fe80::/64', gateway: '', dev: 'eno1', protocol: 'kernel', metric: 0,
     scope: 'link', family: 'inet6'},
  ],
  '/network/dns': {servers: ['127.0.0.1'], resolver_servers: ['127.0.0.1'],
     source: 'dnsmasq configuration', scope: 'the host'},
  '/network/ports': [
    {proto: 'TCP', local_ip: '0.0.0.0', local_port: 22, pid: null, exe: ''},
    {proto: 'TCP', local_ip: '127.0.0.1', local_port: 8080, pid: 42,
     exe: '/usr/bin/python3'},
    {proto: 'UDP', local_ip: '0.0.0.0', local_port: 53, pid: null, exe: ''},
    {proto: 'UDP', local_ip: '0.0.0.0', local_port: 443, pid: 7,
     exe: '/usr/bin/lyrebird'},
  ],
  '/network/conntrack': [],
  '/network/neighbors': [
    {ip: '192.168.1.1', mac: '44:89:c8:ad:7a', device: 'eno1', state: 'COMPLETE'},
  ],
  '/network/dns/status': {upstreams: [], resolver: '127.0.0.1', source: 'dnsmasq'},
  '/network/dns/compare': {providers: [], recommended: null},
  '/network/topology': {
    host: 'kourosh-pc', scope: 'the host', containers: 1,
    nodes: [
      {id: 'host', kind: 'host', label: 'kourosh-pc', depth: 0,
       detail: 'the host'},
      {id: 'iface:eno1', kind: 'interface', subtype: 'physical', label: 'eno1',
       state: 'UP', depth: 1, addresses: ['192.168.1.13'], mtu: 1500, master: ''},
      {id: 'iface:br-10b47f13e607', kind: 'interface', subtype: 'bridge',
       label: 'br-10b47f13e607', state: 'UP', depth: 1,
       addresses: ['172.22.0.1'], mtu: 1500, master: '',
       members: ['veth0fe2367'], member_count: 1},
      {id: 'iface:veth0fe2367', kind: 'interface', subtype: 'port',
       label: 'veth0fe2367', state: 'UP', depth: 2, addresses: [], mtu: 1500,
       master: 'br-10b47f13e607'},
      {id: 'container:abc123def456', kind: 'container', label: 'python',
       container_id: 'abc123def456', pid: 1234, depth: 3,
       addresses: ['172.22.0.2'], command: 'python app.py',
       device: 'br-10b47f13e607'},
      {id: 'upstream', kind: 'upstream', label: 'Internet', depth: 2,
       gateway: '192.168.1.1', device: 'eno1',
       detail: 'via 192.168.1.1 on eno1'},
    ],
    links: [
      {source: 'host', target: 'iface:eno1', kind: 'attach'},
      {source: 'host', target: 'iface:br-10b47f13e607', kind: 'attach'},
      {source: 'iface:br-10b47f13e607', target: 'iface:veth0fe2367',
       kind: 'attach'},
      {source: 'iface:br-10b47f13e607', target: 'container:abc123def456',
       kind: 'container'},
      {source: 'iface:eno1', target: 'upstream', kind: 'upstream'},
    ],
  },
};

const ELEMENT_IDS = [
  'summary', 'topology', 'topology-count', 'topology-note',
  'ifaces', 'routes', 'ports', 'neighbors', 'ct', 'ct-note', 'dns', 'ports-note',
  'iface-filters', 'route-filters', 'port-filters',
  'iface-kind', 'route-family', 'iface-search', 'route-search', 'port-search',
  'iface-count', 'route-count', 'port-count',
  'topology-ports', 'topology-inactive',
  // The DNS diagnostics block runs on load; the stub needs its elements or
  // the page throws before the filters are ever wired.
  'dns-current-setup', 'dns-current-setup-value', 'dns-status',
  'dns-recommendation', 'dns-provider-table', 'dns-domains', 'dns-quick',
  'dns-run', 'dns-use-recommended-pair', 'dns-apply', 'dns-selected-pair',
  'dns-command-preview', 'dns-apply-command', 'dns-rollback-command-template',
  'dns-apply-result', 'dns-apply-message', 'dns-rollback-commands',
  'dns-rollback',
];

const CHIPS = {
  'iface-filters': ['all', 'up', 'ipv4', 'ipv6', 'traffic'],
  'route-filters': ['all', 'default', 'ipv4', 'ipv6', 'gateway'],
  'port-filters': ['all', 'tcp', 'udp', 'external', 'loopback', 'privileged',
                   'known'],
};

function buildDocument() {
  const doc = {
    _byId: new Map(),
    createElement(tag) { return new El(tag); },
    createElementNS(ns, tag) { const e = new El(tag); e._doc = doc; e.namespaceURI = ns; return e; },
    getElementById(id) { return doc._byId.get(id) || null; },
    querySelector(sel) { return doc.root.querySelector(sel); },
    querySelectorAll(sel) { return doc.root.querySelectorAll(sel); },
    addEventListener() {},
    body: new El('body'),
  };
  doc.root = new El('body');
  doc.root._doc = doc;
  for (const id of ELEMENT_IDS) {
    const el = new El('div');
    el.id = id;
    doc._byId.set(id, el);
    doc.root.appendChild(el);
  }
  for (const [group, presets] of Object.entries(CHIPS)) {
    const el = doc._byId.get(group);
    for (const preset of presets) {
      const chip = new El('button');
      chip.className = 'net-chip';
      chip.setAttribute('data-preset', preset);
      chip.setAttribute('aria-pressed', String(preset === 'all'));
      el.appendChild(chip);
    }
  }
  return doc;
}

module.exports = {El, buildDocument, FIXTURE};
