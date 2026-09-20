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
          return `<div class="net-item"><span class="iface">${i.ifname}</span><span class="cidr">${addrs || "—"}</span></div>`;
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