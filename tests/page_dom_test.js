// Drives the network page's real script against a DOM stub and asserts on
// what it did.
//
// The script is the one extracted from the *rendered* page, so a template
// edit that breaks the page fails here. A server-side assertion on the
// template cannot see this: the failure lives in what the JS computes at
// runtime.
//
// Usage: node page_dom_test.js <path-to-rendered-page.html>
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');
const {buildDocument, FIXTURE} = require('./page_dom_stub.js');

const pagePath = process.argv[2];
if (!pagePath) {
  console.error('usage: node page_dom_test.js <rendered-page.html>');
  process.exit(2);
}

function runPage() {
  const doc = buildDocument();
  const requests = [];
  const intervals = [];
  const clock = {t: 1_700_000_000_000};
  const RealDate = Date;
  const FakeDate = class extends RealDate {
    static now() { return clock.t; }
  };
  const sandbox = {
    document: doc,
    location: {pathname: '/network/', search: '', origin: 'http://x'},
    window: {
      location: {pathname: '/network/', search: ''},
      confirm: () => true,
      addEventListener() {},
    },
    Date: FakeDate,
    console,
    setInterval(fn, ms) { intervals.push(fn); return intervals.length; },
    setTimeout() {},
    navigator: {clipboard: {writeText: () => Promise.resolve()}},
    localStorage: {getItem: () => null, setItem() {}},
    matchMedia: () => ({matches: false}),
    fetch(url) {
      requests.push(url);
      const key = Object.keys(FIXTURE).find(k => url.endsWith(k) || url.includes(k + '?'));
      if (key === undefined) {
        return Promise.reject(new Error('unexpected fetch: ' + url));
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(JSON.parse(JSON.stringify(FIXTURE[key]))),
      });
    },
  };
  sandbox.globalThis = sandbox;
  const page = fs.readFileSync(pagePath, 'utf8');
  const match = page.match(/<script>([\s\S]*?)<\/script>/);
  if (!match) throw new Error('no inline script found in the rendered page');
  const code = match[1];
  const scriptPath = path.join(__dirname, '.extracted-page.js');
  fs.writeFileSync(scriptPath, code);
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, {filename: 'page.js'});
  return {doc, sandbox, requests, intervals, clock};
}

function tick() {
  // Let every already-resolved promise callback run.
  return new Promise(resolve => setImmediate(resolve));
}

function rowsOf(doc, id) { return doc.getElementById(id).innerHTML; }

function rowCount(doc, id) {
  const html = doc.getElementById(id).innerHTML;
  if (!html) return 0;
  return (html.match(/<tr>/g) || []).length;
}

function chip(doc, group, preset) {
  return doc.getElementById(group)
    .querySelectorAll('.net-chip[data-preset]')
    .find(c => c.dataset.preset === preset);
}

(async () => {
  const {doc, requests, intervals, clock} = runPage();
  await tick();
  await tick();

  assert.ok(requests.some(u => u.endsWith('/topology')),
            'page did not fetch /network/topology');

  // --- Interfaces: all four rows by default -----------------------------
  assert.strictEqual(rowCount(doc, 'ifaces'), 4, 'expected 4 interface rows');
  assert.match(doc.getElementById('iface-count').textContent, /4 of 4/);
  // Traffic column: on first render no previous sample exists, so every
  // row shows the em-dash placeholder rather than a rate.
  assert.match(rowsOf(doc, 'ifaces'), /—/,
               'traffic column did not render a placeholder on first load');
  // No previous sample exists yet, so no rate arrow should appear.
  assert.doesNotMatch(rowsOf(doc, 'ifaces'), /↓/,
                      'a rate appeared before a second sample existed');

  // Fire the traffic poll: bump the counters the next fetch will see, then
  // advance the fake clock one second and run the poll. The diff over the
  // known second must turn into a rate arrow in the table.
  assert.ok(intervals.length >= 1, 'the traffic poller never registered');
  FIXTURE['/network/interfaces']
    .filter(i => i.name === 'eno1')
    .forEach(i => { i.rx_bytes += 2048000; i.tx_bytes += 2048000; });
  clock.t += 1000;
  await intervals[0]();
  await tick();
  assert.match(rowsOf(doc, 'ifaces'), /↓2\.0 MB\/s ↑2\.0 MB\/s/,
               'the second sample did not produce the expected rate');
  // Traffic from counters that would wrap is shown as unknown, never as a
  // negative number.
  FIXTURE['/network/interfaces'].find(i => i.name === 'eno1').rx_bytes = 1;
  clock.t += 1000;
  await intervals[0]();
  await tick();
  assert.doesNotMatch(rowsOf(doc, 'ifaces'), /↓2\.0 MB\/s/,
                      'wrapped counters should not produce a rate');

  // "Up only" drops the UNKNOWN loopback.
  chip(doc, 'iface-filters', 'up').dispatch('click');
  assert.strictEqual(rowCount(doc, 'ifaces'), 3, 'up-only should drop lo');
  assert.match(doc.getElementById('iface-count').textContent, /3 of 4/);

  // The pressed state must follow the selection, so the user can see which
  // filter is active.
  assert.strictEqual(chip(doc, 'iface-filters', 'up').getAttribute('aria-pressed'), 'true');
  assert.strictEqual(chip(doc, 'iface-filters', 'all').getAttribute('aria-pressed'), 'false');

  // "IPv6 only" keeps the one interface that has a v6 address.
  chip(doc, 'iface-filters', 'ipv6').dispatch('click');
  assert.strictEqual(rowCount(doc, 'ifaces'), 1, 'ipv6-only should keep only lo');
  assert.match(rowsOf(doc, 'ifaces'), /lo/);

  // Back to all, then the kind select: the topology supplies the kind, so
  // this proves the two are wired together.
  chip(doc, 'iface-filters', 'all').dispatch('click');
  const kindSelect = doc.getElementById('iface-kind');
  kindSelect.value = 'bridge';
  kindSelect.dispatch('change');
  assert.strictEqual(rowCount(doc, 'ifaces'), 1, 'bridge-only should keep one');
  assert.match(rowsOf(doc, 'ifaces'), /br-10b47f13e607/);

  kindSelect.value = 'port';
  kindSelect.dispatch('change');
  assert.strictEqual(rowCount(doc, 'ifaces'), 1, 'port-only should keep the veth');
  assert.match(rowsOf(doc, 'ifaces'), /veth0fe2367/);

  // Free-text search.
  kindSelect.value = '';
  kindSelect.dispatch('change');
  const search = doc.getElementById('iface-search');
  search.value = 'eno1';
  search.dispatch('input');
  assert.strictEqual(rowCount(doc, 'ifaces'), 1, 'search should match one row');
  assert.match(rowsOf(doc, 'ifaces'), /eno1/);
  assert.doesNotMatch(rowsOf(doc, 'ifaces'), /br-10b47f13e607/);

  // --- Routes -----------------------------------------------------------
  assert.strictEqual(rowCount(doc, 'routes'), 4);
  chip(doc, 'route-filters', 'default').dispatch('click');
  assert.strictEqual(rowCount(doc, 'routes'), 2, 'default-only should keep 0.0.0.0/0 and ::/0');
  assert.match(rowsOf(doc, 'routes'), /0\.0\.0\.0\/0/);
  assert.doesNotMatch(rowsOf(doc, 'routes'), /172\.22\.0\.0/);

  chip(doc, 'route-filters', 'ipv4').dispatch('click');
  assert.strictEqual(rowCount(doc, 'routes'), 2, 'ipv4-only should keep two');
  assert.doesNotMatch(rowsOf(doc, 'routes'), /fe80/);

  chip(doc, 'route-filters', 'ipv6').dispatch('click');
  assert.strictEqual(rowCount(doc, 'routes'), 2, 'ipv6-only should keep two');
  assert.match(rowsOf(doc, 'routes'), /fe80/);

  chip(doc, 'route-filters', 'gateway').dispatch('click');
  assert.strictEqual(rowCount(doc, 'routes'), 1, 'gateway-only should keep one');
  assert.match(rowsOf(doc, 'routes'), /192\.168\.1\.1/);

  // --- Ports ------------------------------------------------------------
  assert.strictEqual(rowCount(doc, 'ports'), 4);
  chip(doc, 'port-filters', 'tcp').dispatch('click');
  assert.strictEqual(rowCount(doc, 'ports'), 2, 'tcp-only should keep two');
  assert.doesNotMatch(rowsOf(doc, 'ports'), /UDP/);

  chip(doc, 'port-filters', 'external').dispatch('click');
  // Externally reachable means bound to the wildcard address, not 127.0.0.1.
  assert.strictEqual(rowCount(doc, 'ports'), 3, 'external should drop the loopback bind');
  assert.doesNotMatch(rowsOf(doc, 'ports'), /127\.0\.0\.1/);

  chip(doc, 'port-filters', 'loopback').dispatch('click');
  assert.strictEqual(rowCount(doc, 'ports'), 1, 'loopback-only should keep one');
  assert.match(rowsOf(doc, 'ports'), /127\.0\.0\.1/);

  chip(doc, 'port-filters', 'privileged').dispatch('click');
  // 22, 53 and 443 are all below 1024; 8080 is not.
  assert.strictEqual(rowCount(doc, 'ports'), 3, 'privileged should keep 22, 53 and 443');
  assert.doesNotMatch(rowsOf(doc, 'ports'), /8080/);

  chip(doc, 'port-filters', 'known').dispatch('click');
  assert.strictEqual(rowCount(doc, 'ports'), 2, 'known-process should keep the two with a pid');
  assert.match(doc.getElementById('ports-note').textContent, /PID is unknown for 2 of 4/);

  // --- Topology ---------------------------------------------------------
  const topo = doc.getElementById('topology');
  assert.ok(topo.children.length > 0, 'topology rendered nothing');
  const svg = topo.children[0];
  assert.strictEqual(svg.tagName, 'SVG', 'topology should be an svg');
  const nodeGroups = svg.querySelectorAll('g');
  // The bridge port is hidden by default, so: host, eno1, br, container,
  // upstream.
  assert.strictEqual(nodeGroups.length, 5, 'expected 5 nodes, got ' + nodeGroups.length);
  const paths = svg.querySelectorAll('path');
  // The link to the hidden port is dropped with it.
  assert.strictEqual(paths.length, 4, 'expected 4 links, got ' + paths.length);
  assert.match(doc.getElementById('topology-count').textContent,
               /3 interfaces, 1 containers/);
  assert.match(doc.getElementById('topology-count').textContent,
               /1 bridge ports hidden/);

  // Every node must carry a label the user can read.
  const labels = svg.querySelectorAll('text').map(t => t.textContent);
  for (const want of ['kourosh-pc', 'eno1', 'br-10b47f13e607', 'python', 'Internet']) {
    assert.ok(labels.includes(want), 'topology is missing the label ' + want +
      ' (got ' + JSON.stringify(labels) + ')');
  }
  // The container's subtitle carries its compose project.
  assert.ok(labels.some(t => t.includes('app_stack')),
            'topology did not print the container project (got '
            + JSON.stringify(labels) + ')');
  // A hidden port must not be drawn, and the bridge must say how many
  // ports it has instead.
  assert.ok(!labels.includes('veth0fe2367'), 'bridge port was drawn by default');
  assert.ok(labels.includes('1 port'), 'bridge did not report its port count');

  // Turning the ports on brings the port back.
  const portsToggle = doc.getElementById('topology-ports');
  portsToggle.checked = true;
  portsToggle.dispatch('change');
  const svg2 = doc.getElementById('topology').children[0];
  assert.strictEqual(svg2.querySelectorAll('g').length, 6,
                     'ports toggle should add the port node');
  assert.ok(svg2.querySelectorAll('text').map(t => t.textContent).includes('veth0fe2367'),
            'ports toggle did not bring the port back');
  assert.doesNotMatch(doc.getElementById('topology-count').textContent,
                      /bridge ports hidden/);

  // A down interface is hidden by default, and the count says so.
  assert.match(doc.getElementById('topology-count').textContent,
               /down interfaces hidden/);

  console.log('ALL PAGE ASSERTIONS PASSED');
  process.exit(0);
})().catch(err => {
  console.error('FAILED:', err.message);
  process.exit(1);
});
