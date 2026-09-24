/** System Manager — small client helpers for the dashboard. */
(() => {
  const fmt = {
    bytes(b) {
      if (b == null) return "—";
      const u = ["B","KB","MB","GB","TB"]; let i = 0;
      while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
      return `${b.toFixed(i ? 1 : 0)} ${u[i]}`;
    },
    pct(n, d) { return (n == null || d == null || d === 0) ? "—" : `${((n/d)*100).toFixed(1)}%`; },
    uptime(s) {
      if (!s) return "—";
      const d = Math.floor(s/86400), h = Math.floor(s%86400/3600), m = Math.floor(s%3600/60);
      return `${d}d ${h}h ${m}m`;
    },
    load(arr) { return Array.isArray(arr) ? arr.map(x => x.toFixed(2)).join(" · ") : "—"; },
  };
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function fill(data) {
    if (!data || data.state !== "observed") return;

    const d = data.data;
    // Memory
    const mem = d.memory || {};
    const total = mem.MemTotal?.value;
    const avail = mem.MemAvailable?.value;
    const used = (total != null && avail != null) ? total - avail : null;
    document.getElementById("statMemory").textContent = fmt.pct(used, total);
    document.getElementById("memBar").style.width = fmt.pct(used, total);
    document.getElementById("memSub").textContent = `${fmt.bytes(used)} / ${fmt.bytes(total)}`;

    // Disk
    const fs = d.filesystem?.value;
    if (fs) {
      const diskUsed = fs.total - fs.available;
      document.getElementById("statDisk").textContent = fmt.pct(diskUsed, fs.total);
      document.getElementById("diskBar").style.width = fmt.pct(diskUsed, fs.total);
      document.getElementById("diskSub").textContent = `${fmt.bytes(diskUsed)} / ${fmt.bytes(fs.total)}`;
    }

    // Load
    const load = d.system?.load?.value;
    document.getElementById("statLoad").textContent = fmt.load(load);
    // sparkline placeholder - could use <svg> later

    // Uptime + OS
    const up = d.system?.uptime?.value;
    document.getElementById("statUptime").textContent = fmt.uptime(up);
    const os = d.system?.os?.value;
    const kernel = d.system?.kernel?.value;
    document.getElementById("osSub").textContent = `${os || "—"} · kernel ${kernel || "—"}`;

    // Network
    const ifaces = d.interfaces?.value;
    const box = document.getElementById("netBox");
    if (ifaces && Array.isArray(ifaces)) {
      const rows = ifaces
        .filter(i => i.operstate === "UP" && !i.ifname.startsWith("lo"))
        .slice(0, 6)
        .map(i => {
          const addrs = (i.addr_info || [])
            .filter(a => a.family === "inet")
            .map(a => `${a.local}/${a.prefixlen}`)
            .join(", ");
          return `<div class="net-item"><span class="iface">${esc(i.ifname)}</span><span class="cidr">${esc(addrs)}</span></div>`;
        }).join("");
      box.innerHTML = rows || '<span class="muted">no UP interfaces</span>';
    }

    // Advisories
    const suggs = d.suggestions || [];
    const sbox = document.getElementById("suggBox");
    if (suggs.length) {
      sbox.innerHTML = suggs.map(s => `
        <div class="suggestion">
          <div class="title">${s.title}</div>
          <div class="detail">${s.detail}</div>
        </div>`).join("");
    } else {
      sbox.innerHTML = '<span class="muted">no advisories</span>';
    }

    // Hardware
    const hw = d.hardware || {};
    document.getElementById("hwMaker").textContent = hw.Manufacturer?.value || "—";
    document.getElementById("hwModel").textContent = hw.Model?.value || "—";
    document.getElementById("hwCpu").textContent = hw.Processor?.value || "—";
    document.getElementById("hwKernel").textContent = d.system?.kernel?.value || "—";
  }

  // initial fill
  const saved = window.__INITIAL_SNAP__;
  if (saved) fill(saved);

  // poll
  async function poll() {
    try {
      const res = await fetch("/api/status", { cache: "no-store" });
      if (res.ok) fill(await res.json());
    } catch (e) { console.debug("status poll failed", e); }
  }
  setInterval(poll, 10000);
  poll();

  // unlock (when auth is enabled)
  const lockForm = document.getElementById("lockForm");
  const lockError = document.getElementById("lockError");
  lockForm?.addEventListener("submit", async e => {
    e.preventDefault();
    const code = document.getElementById("lockCode").value;
    lockError.textContent = "";
    try {
      const res = await fetch("/api/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      if (!res.ok) {
        const err = await res.json();
        lockError.textContent = err.error || "Unlock failed.";
        return;
      }
      document.getElementById("lockBanner")?.remove();
      poll();
    } catch (err2) { lockError.textContent = "Unlock failed."; }
  });

  // actions + audit
  async function loadActions() {
    try {
      const [svcRes, profRes] = await Promise.all([fetch("/api/services"), fetch("/api/profiles")]);
      const svc = svcRes.ok ? await svcRes.json() : null;
      const prof = profRes.ok ? await profRes.json() : null;
      const box = document.getElementById("actionsBox");
      if (!box) return;
      const svcHtml = svc?.state === "observed" && svc.value.length
        ? `<h3>User services</h3><ul class="action-list">${svc.value.map(s =>
            `<li><span class="iface">${esc(s.name)}</span> <span class="cidr">${esc(s.active)} / ${esc(s.sub)}</span>
               <button type="button" data-action="restart" data-name="${esc(s.name)}">Restart</button>
               <button type="button" data-action="start" data-name="${esc(s.name)}">Start</button></li>`).join("")}</ul>`
        : (svc?.state === "unavailable" ? `<p class="muted">${esc(svc.detail || "user services unavailable")}</p>` : "");
      const profHtml = prof?.state === "observed" && prof.value.length
        ? `<h3>Network profiles</h3><ul class="action-list">${prof.value.map(p =>
            `<li><span class="iface">${esc(p.name)}</span> <span class="cidr">${esc(p.type)}${p.device ? " · " + esc(p.device) : ""}</span>
               <button type="button" data-profile="1" data-name="${esc(p.name)}">Activate</button></li>`).join("")}</ul>`
        : (prof?.state === "unavailable" ? `<p class="muted">${esc(prof.detail || "no profiles")}</p>` : "");
      box.innerHTML = (svcHtml + profHtml) || '<p class="muted">No managed services or profiles currently available.</p>';
    } catch (e3) { console.debug("actions load failed", e3); }
  }

  async function loadAudit() {
    try {
      const res = await fetch("/api/audit");
      const data = res.ok ? await res.json() : null;
      const box = document.getElementById("auditBox");
      if (!box) return;
      const actions = data?.actions || [];
      const codeOf = a => {
        const raw = a?.result;
        const parsed = typeof raw === "string" ? (() => { try { return JSON.parse(raw); } catch { return null; } })() : raw;
        return parsed?.code;
      };
      box.innerHTML = actions.length ? `<ul class="action-list">${actions.map(a =>
        `<li><span class="iface">${esc(a.action_type)}</span> <span class="cidr">${esc(a.parameters || "")}</span>
           <span class="tag ${codeOf(a) === 0 ? "dot-ok" : "dot-bad"}">${esc(codeOf(a) === 0 ? "ok" : "failed")}</span></li>`).join("")}</ul>`
        : '<p class="muted">The SQLite audit log is populated once approved actions run.</p>';
    } catch (e4) { console.debug("audit load failed", e4); }
  }

  loadActions();
  loadAudit();

  // approved-action flow: approve (issue single-use token) -> execute -> render outcome
  document.addEventListener("click", async ev => {
    const btn = ev.target.closest("[data-action], [data-profile]");
    if (!btn || !document.body.contains(document.getElementById("actionsBox"))) return;
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = "…";
    const action_type = btn.hasAttribute("data-profile") ? "nm_activate" : `service_${btn.dataset.action}`;
    const name = btn.dataset.name;
    try {
      const approve = await fetch("/api/approve", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action_type, parameters: { name } }),
      });
      const approved = await approve.json();
      if (!approve.ok) { btn.textContent = original; alert(approved.error || "Approval failed."); return; }
      const execute = await fetch("/api/execute", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approval_token: approved.approval_token }),
      });
      const outcome = await execute.json();
      btn.textContent = original;
      btn.disabled = false;
      alert(`${action_type} ${name}: ${outcome.outcome || (outcome.error || "failed")}`);
      loadAudit();
    } catch (e5) { btn.textContent = original; btn.disabled = false; }
  });

  // theme toggle
  const root = document.documentElement;
  const btn = document.getElementById("themeToggle");
  function apply(t) { root.classList.toggle("light", t === "light"); }
  apply(localStorage.getItem("theme") || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
  btn?.addEventListener("click", () => {
    const next = root.classList.contains("light") ? "dark" : "light";
    localStorage.setItem("theme", next);
    apply(next);
  });
})();