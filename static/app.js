/* بازارگرد — دستیار خرید حضوری تهران. Vanilla JS, no build step. */
"use strict";

// ───────────────────────── helpers
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fa = (n) => String(n).replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[d]);
const money = (n) => fa(Math.round(n).toLocaleString("en-US")).replace(/,/g, "٬");
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const DAYS_FA = ["یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه"]; // Date.getUTCDay()
const store = {
  get(k, d) { try { const v = localStorage.getItem("bg:" + k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("bg:" + k, JSON.stringify(v)); } catch { /* private mode */ } },
};
async function api(path, body) {
  const opt = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error((await r.text()).slice(0, 200));
  return r.json();
}
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.classList.add("on");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("on"), 2400);
}
const ic = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;

// ───────────────────────── state
const DEF = { lat: 35.6955, lon: 51.4195, radius_km: 3, min_price: null, max_price: null, open_now: false, when: null,
  trabpay: false, installment: false, fast: false, warranty: false, try_before_buy: false, area_label: "فردوسی" };
const S = {
  cfg: null,
  f: { ...DEF, ...store.get("filters", {}), when: null },
  q: "", sort: "relevance", res: null, understood: [], loading: false,
  trip: store.get("trip", []), tripRes: null,
  follow: store.get("follow", {}),
  chat: [], compare: [], whenLabel: "همین حالا",
  tab: "search", map: null, layer: null, offers: {},
};
const saveF = () => { const { when, ...rest } = S.f; store.set("filters", rest); };

function fparams(extra = {}) {
  const p = new URLSearchParams();
  const f = { ...S.f, ...extra };
  for (const k of ["lat", "lon", "radius_km", "min_price", "max_price", "when"]) if (f[k] != null && f[k] !== "") p.set(k, f[k]);
  for (const k of ["open_now", "trabpay", "installment", "fast", "warranty", "try_before_buy"]) if (f[k]) p.set(k, "true");
  if (extra.sort) p.set("sort", extra.sort);
  return p;
}
const fbody = () => { const { area_label, ...r } = S.f; return { ...r, area_label }; };

// ───────────────────────── small view builders
const hoursChip = (h) => {
  const cls = { open: "ok", closing_soon: "warn", closed: "bad", unknown: "mute" }[h.state] || "mute";
  return `<span class="chip ${cls}">${ic("clock")}${esc(h.label)}</span>`;
};
function condChips(c) {
  const out = [];
  if (c.trabpay) out.push(`<span class="chip info">ترب‌پی ${c.trabpay.confirmed ? "حضوری" : "(حضوری تأیید نشده)"}${c.trabpay.within_limits === false ? " — خارج از سقف/کف" : ""}</span>`);
  for (const i of c.installments.slice(0, 3)) out.push(`<span class="chip info">${esc(i.name)}${i.confirmed ? "" : " (اعلام‌شده)"}</span>`);
  if (c.fast_delivery === true) out.push(`<span class="chip mute">ارسال فوری</span>`);
  else if (c.fast_delivery === false) out.push(`<span class="chip mute">بدون ارسال فوری</span>`);
  if (c.warranty) out.push(`<span class="chip mute">${c.warranty.none ? "بدون گارانتی" : "گارانتی " + esc(c.warranty.provider) + (c.warranty.months ? " — " + fa(c.warranty.months) + " ماه" : "")}</span>`);
  if (c.try_before_buy) out.push(`<span class="chip ok">تست و تحویل در حضور مشتری</span>`);
  return out.join("");
}
function priceWarnings(o) {
  const w = [];
  const p = o.price;
  if (p.state === "missing") w.push("فروشنده قیمتی ثبت نکرده؛ فقط با تماس می‌توان فهمید.");
  if (p.state === "invalid") w.push("عدد قیمت در دیتا نامعتبر است و در رتبه‌بندی لحاظ نشده.");
  if (p.outlier) w.push("این قیمت با بقیهٔ فروشنده‌های همین کالا فاصلهٔ زیادی دارد؛ حتماً تأیید بگیرید.");
  if (p.unit_hint) w.push("عنوان فروشنده نشان می‌دهد قیمت ممکن است برای واحد (مثلاً هر متر) باشد.");
  return w.map((t) => `<div class="note warn">${esc(t)}</div>`).join("");
}
function sellerCard(o, opt = {}) {
  S.offers[o.offer_id] = { ...o, product: opt.product };
  const sh = o.shop;
  const place = [sh.mall, sh.floor && !String(sh.mall || "").includes("طبقه") ? "طبقه " + sh.floor : null, sh.unit ? "واحد " + sh.unit : null].filter(Boolean).join("، ");
  const lvl = o.price_updated.level;
  const priceCell = o.price.value
    ? `<b class="big">${esc(o.price.display.replace(" تومان", ""))}</b><small>تومان</small>` : `<b>${esc(o.price.display)}</b>`;
  const dateCell = o.price_updated.jalali ? `<b>${esc(o.price_updated.jalali)}</b><small>${esc(o.price_updated.label)}${lvl === "stale" ? " — قدیمی" : ""}</small>` : `<b>نامشخص</b>`;
  return `<article class="seller ${opt.best ? "best" : ""} ${opt.dim ? "dim" : ""}" data-oid="${esc(o.offer_id)}">
    <div class="head"><div><h4>${esc(sh.name)}</h4><div class="addr">${esc(place || sh.address)}</div></div>
      <div class="dist"><b>${esc(o.distance_label)}</b>حدود ${fa(o.walk_min)} دقیقه پیاده</div></div>
    <div class="chips" style="margin-top:6px">${hoursChip(o.hours)}${condChips(o.conditions)}</div>
    <div class="truth">
      <div><span>قیمت ثبت‌شده</span>${priceCell}</div>
      <div class="st-${lvl}"><span>آخرین تغییر قیمت</span>${dateCell}</div>
      <div class="unv"><span>موجودی</span><b>تأییدنشده</b><small>باید از فروشنده پرسید</small></div>
    </div>
    ${priceWarnings(o)}
    ${o.reasons && o.reasons.length ? `<div class="reasons">${o.reasons.map(esc).join(" · ")}</div>` : ""}
    <div class="acts">
      <button class="btn sm" data-act="follow" data-oid="${esc(o.offer_id)}">${ic("bell")}پیگیری موجودی</button>
      <button class="btn sm sec" data-act="addtrip" data-oid="${esc(o.offer_id)}">${ic("plus")}به برنامه خرید</button>
      <button class="btn sm sec" data-act="onmap" data-oid="${esc(o.offer_id)}">${ic("map")}روی نقشه</button>
    </div>
  </article>`;
}

// ───────────────────────── tabs
function setTab(t) {
  S.tab = t; document.body.dataset.tab = t;
  $$(".tab").forEach((e) => e.classList.toggle("on", e.id === "tab-" + t));
  $$("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  if (t === "map" || matchMedia("(min-width:1000px)").matches) setTimeout(() => S.map && S.map.invalidateSize(), 60);
  if (t === "follow") renderFollow(true);
  if (t === "trip") renderTrip();
  if (t === "chat") renderChat();
}
$("#tabs").addEventListener("click", (e) => { const b = e.target.closest("button[data-tab]"); if (b) setTab(b.dataset.tab); });

// ───────────────────────── sheets
function openSheet({ title, body, footer = "", tall = false, onMount }) {
  const root = $("#sheet-root");
  const wrap = document.createElement("div");
  wrap.className = "";
  wrap.innerHTML = `<div class="scrim"></div><div class="sheet ${tall ? "tall" : ""}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
    <div class="sheet-h"><h2>${title}</h2><button class="icon-btn" data-close aria-label="بستن">${ic("x")}</button></div>
    <div class="sheet-b">${body}</div>${footer ? `<div class="sheet-f">${footer}</div>` : ""}</div>`;
  root.appendChild(wrap);
  root.style.pointerEvents = "auto";
  requestAnimationFrame(() => wrap.classList.add("on"));
  const close = () => {
    wrap.classList.remove("on");
    setTimeout(() => { wrap.remove(); if (!root.children.length) root.style.pointerEvents = "none"; }, 220);
  };
  wrap.addEventListener("click", (e) => { if (e.target.classList.contains("scrim") || e.target.closest("[data-close]")) close(); });
  const onKey = (e) => { if (e.key === "Escape") { close(); document.removeEventListener("keydown", onKey); } };
  document.addEventListener("keydown", onKey);
  const api_ = { el: wrap, close, body: $(".sheet-b", wrap) };
  onMount && onMount(api_);
  return api_;
}

// ───────────────────────── location / filters / time
function locationSheet() {
  const areas = S.cfg.areas.slice(0, 26);
  openSheet({
    title: "از کجا شروع می‌کنید؟",
    body: `<div class="field"><button class="btn" id="gps" style="width:100%">${ic("gps")}موقعیت فعلی من</button>
      <div class="hint" style="margin-top:6px">اگر موقعیت‌تان بیرون تهران باشد، به فردوسی برمی‌گردیم.</div></div>
      <div class="field"><span class="lab">یا یک محله / مرکز خرید را انتخاب کنید</span>
      <div class="areas">${areas.map((a) => `<button class="chip x" data-area="${esc(a.name)}">${esc(a.name)} <small>${fa(a.n)} فروشگاه</small></button>`).join("")}</div></div>
      <div class="field"><button class="btn sec" id="pick" style="width:100%">${ic("map")}انتخاب نقطه روی نقشه</button></div>`,
    onMount: ({ el, close }) => {
      $("#gps", el).onclick = () => {
        if (!navigator.geolocation) return toast("مرورگر موقعیت‌یابی ندارد");
        navigator.geolocation.getCurrentPosition((p) => { setOrigin(p.coords.latitude, p.coords.longitude, "موقعیت من", true); close(); },
          () => toast("اجازهٔ موقعیت داده نشد؛ یک محله انتخاب کنید"), { timeout: 8000 });
      };
      $$("[data-area]", el).forEach((b) => b.onclick = () => { const a = areas.find((x) => x.name === b.dataset.area); setOrigin(a.lat, a.lon, a.name); close(); });
      $("#pick", el).onclick = () => { close(); S.picking = true; setTab("map"); toast("روی نقشه ضربه بزنید تا مبدأ عوض شود"); };
    },
  });
}
function setOrigin(lat, lon, label, gps = false) {
  const inTehran = lat > 35.55 && lat < 35.85 && lon > 51.2 && lon < 51.6;
  if (!inTehran) { toast("این نقطه بیرون تهران است؛ فردوسی انتخاب شد"); lat = DEF.lat; lon = DEF.lon; label = DEF.area_label; }
  Object.assign(S.f, { lat, lon, area_label: label }); saveF(); paintLoc(); drawBase();
  if (S.q) runSearch(); else renderSearch();
  if (gps) toast("موقعیت شما ثبت شد");
}
const paintLoc = () => { $("#locLabel").textContent = S.f.area_label || "نقطهٔ انتخابی"; };

const RADII = [0.5, 1, 2, 3, 5, 8, 12, 0];
const radLabel = (r) => (r === 0 ? "کل تهران" : r < 1 ? fa(r * 1000) + " متر" : fa(r) + " کیلومتر");
function filtersSheet() {
  const f = { ...S.f };
  const idx = Math.max(0, RADII.indexOf(f.radius_km));
  const tog = (k, lab) => `<button data-t="${k}" class="${f[k] ? "on" : ""}" aria-pressed="${!!f[k]}">${lab}</button>`;
  openSheet({
    title: "محدوده، بودجه و شرایط خرید",
    body: `<div class="field"><span class="lab">محدوده: <b id="radv">${radLabel(f.radius_km)}</b></span>
        <input type="range" id="rad" min="0" max="${RADII.length - 1}" step="1" value="${idx}" aria-label="شعاع جستجو"></div>
      <div class="field"><span class="lab">بودجه (تومان)</span>
        <div class="two"><input type="number" inputmode="numeric" id="minp" placeholder="حداقل" value="${f.min_price ?? ""}"><input type="number" inputmode="numeric" id="maxp" placeholder="حداکثر" value="${f.max_price ?? ""}"></div>
        <div class="tog" style="margin-top:8px">${[500000, 1000000, 3000000, 10000000].map((v) => `<button data-max="${v}">زیر ${money(v)}</button>`).join("")}</div>
        <div class="hint" style="margin-top:6px">وقتی بودجه می‌گذارید، آفرهای بدون قیمت یا با قیمت نامعتبر نمایش داده نمی‌شوند.</div></div>
      <div class="field"><span class="lab">زمان مراجعه</span>
        <button class="btn sec" id="when" style="width:100%">${ic("clock")}<span id="whenl">${esc(S.whenLabel)}</span></button>
        <div class="tog" style="margin-top:8px">${tog("open_now", "فقط فروشگاه‌های باز در زمان رسیدن")}</div>
        <div class="hint" style="margin-top:6px">ساعت رسیدن = زمان مراجعه + مدت پیاده‌روی تا فروشگاه.</div></div>
      <div class="field"><span class="lab">شرایط خرید</span>
        <div class="tog">${tog("trabpay", "ترب‌پی")}${tog("installment", "خرید قسطی")}${tog("fast", "ارسال فوری")}${tog("warranty", "گارانتی")}${tog("try_before_buy", "تست و تحویل حضوری")}</div></div>`,
    footer: `<button class="btn ghost" id="reset">حذف همه</button><button class="btn" id="apply">نمایش نتایج</button>`,
    onMount: ({ el, close }) => {
      $("#rad", el).oninput = (e) => { f.radius_km = RADII[+e.target.value]; $("#radv", el).textContent = radLabel(f.radius_km); };
      $$("[data-t]", el).forEach((b) => b.onclick = () => { f[b.dataset.t] = !f[b.dataset.t]; b.classList.toggle("on", f[b.dataset.t]); b.setAttribute("aria-pressed", f[b.dataset.t]); });
      $$("[data-max]", el).forEach((b) => b.onclick = () => { $("#maxp", el).value = b.dataset.max; });
      $("#when", el).onclick = () => timeSheet((label) => { $("#whenl", el).textContent = label; f.when = S.f.when; });
      $("#reset", el).onclick = () => { Object.assign(S.f, { radius_km: 3, min_price: null, max_price: null, open_now: false, when: null, trabpay: false, installment: false, fast: false, warranty: false, try_before_buy: false }); S.whenLabel = "همین حالا"; saveF(); close(); S.q ? runSearch() : renderSearch(); };
      $("#apply", el).onclick = () => {
        const mn = parseInt($("#minp", el).value), mx = parseInt($("#maxp", el).value);
        Object.assign(S.f, { radius_km: f.radius_km, open_now: f.open_now, trabpay: f.trabpay, installment: f.installment, fast: f.fast, warranty: f.warranty,
          try_before_buy: f.try_before_buy, min_price: isNaN(mn) ? null : mn, max_price: isNaN(mx) ? null : mx });
        saveF(); close(); S.q ? runSearch() : renderSearch();
      };
    },
  });
}
function timeSheet(done) {
  const now = new Date(S.cfg.now_iso);
  const ymd = S.cfg.now_iso.slice(0, 10).split("-").map(Number);
  const days = [...Array(7)].map((_, i) => { const d = new Date(Date.UTC(ymd[0], ymd[1] - 1, ymd[2] + i)); return { iso: d.toISOString().slice(0, 10), name: i === 0 ? "امروز" : i === 1 ? "فردا" : DAYS_FA[d.getUTCDay()] }; });
  const nowHM = S.cfg.now_iso.slice(11, 16);
  openSheet({
    title: "چه زمانی می‌روید؟",
    body: `<div class="field"><span class="lab">روز</span><div class="tog" id="days">${days.map((d, i) => `<button data-i="${i}" class="${i === 0 ? "on" : ""}">${d.name}</button>`).join("")}</div></div>
      <div class="field"><label for="tm">ساعت</label><input type="time" id="tm" value="${nowHM}"></div>
      <div class="tog">${["10:00", "14:00", "17:00", "20:00"].map((t) => `<button data-q="${t}">${fa(t)}</button>`).join("")}</div>`,
    footer: `<button class="btn ghost" id="nowb">همین حالا</button><button class="btn" id="ok">ثبت زمان</button>`,
    onMount: ({ el, close }) => {
      let di = 0;
      $$("#days button", el).forEach((b) => b.onclick = () => { di = +b.dataset.i; $$("#days button", el).forEach((x) => x.classList.toggle("on", x === b)); });
      $$("[data-q]", el).forEach((b) => b.onclick = () => { $("#tm", el).value = b.dataset.q; });
      $("#nowb", el).onclick = () => { S.f.when = null; S.whenLabel = "همین حالا"; close(); done && done(S.whenLabel); afterTime(); };
      $("#ok", el).onclick = () => {
        const t = $("#tm", el).value || nowHM;
        S.f.when = `${days[di].iso}T${t}:00+03:30`; S.whenLabel = `${days[di].name} ساعت ${fa(t)}`;
        close(); done && done(S.whenLabel); afterTime();
      };
    },
  });
  function afterTime() { if (S.q && !$("#sheet-root .sheet")) runSearch(); renderSearch(); }
}

// ───────────────────────── search tab
const EXAMPLES = ["دوربین مداربسته زیر ۳ میلیون نزدیک شیخ هادی", "ساعت مچی ترب‌پی الان باز", "باتری قلمی، کابل شارژ و چسب برق برنامه خرید", "لامپ ادیسونی تا ۱ کیلومتر"];
function activeChips() {
  const f = S.f, c = [];
  c.push(`<button class="chip x" data-clear="radius_km">${ic("pin")}${esc(f.area_label || "نقطهٔ انتخابی")} · ${radLabel(f.radius_km)}</button>`);
  if (f.max_price) c.push(`<button class="chip x" data-clear="max_price">تا ${money(f.max_price)} ✕</button>`);
  if (f.min_price) c.push(`<button class="chip x" data-clear="min_price">از ${money(f.min_price)} ✕</button>`);
  if (f.when) c.push(`<button class="chip x" data-clear="when">${esc(S.whenLabel)} ✕</button>`);
  const lab = { open_now: "باز در زمان رسیدن", trabpay: "ترب‌پی", installment: "قسطی", fast: "ارسال فوری", warranty: "گارانتی", try_before_buy: "تست و تحویل" };
  for (const k in lab) if (f[k]) c.push(`<button class="chip x" data-clear="${k}">${lab[k]} ✕</button>`);
  return c.join("");
}
function renderSearch() {
  const root = $("#tab-search");
  const filtered = S.f.max_price || S.f.min_price || S.f.open_now || S.f.when || S.f.trabpay || S.f.installment || S.f.fast || S.f.warranty || S.f.try_before_buy || S.f.radius_km !== 3;
  root.innerHTML = `
    <form class="smart" id="sf" role="search">
      ${ic("search")}<input id="q" type="search" enterkeyhint="search" autocomplete="off" placeholder="چه چیزی می‌خواهید بخرید؟" value="${esc(S.qRaw || S.q)}" aria-label="جستجوی محصول">
      <button class="go" aria-label="جستجو">${ic("back")}</button>
    </form>
    ${S.res || S.loading ? "" : `<div class="ex" aria-label="نمونه جستجو">${EXAMPLES.map((e) => `<button type="button" data-ex="${esc(e)}">${esc(e)}</button>`).join("")}</div>`}
    <div class="chips" style="margin:10px 0 2px" id="achips">${activeChips()}</div>
    <div class="ctlbar">
      <button class="filterbtn ${filtered ? "act" : ""}" id="fbtn">${ic("sliders")}فیلترها</button>
      <div class="seg" role="tablist" aria-label="مرتب‌سازی">
        ${[["relevance", "مرتبط‌ترین"], ["cheap", "ارزان‌ترین"], ["near", "نزدیک‌ترین"]].map(([k, l]) => `<button data-sort="${k}" class="${S.sort === k ? "on" : ""}" role="tab" aria-selected="${S.sort === k}">${l}</button>`).join("")}
      </div>
    </div>
    <div id="understood"></div>
    <div id="results">${resultsHTML()}</div>
    <div class="meta">قیمت‌ها از دیتاست ترب (به‌روزرسانی ${esc(S.cfg.snapshot_jalali)}) هستند و موجودی هیچ کالایی تأیید نشده است.</div>
    ${S.compare.length >= 1 ? `<div style="position:sticky;bottom:6px;display:flex;justify-content:center;margin-top:10px"><button class="btn" id="cmp">${ic("compare")}مقایسه ${fa(S.compare.length)} کالا</button></div>` : ""}`;
  paintUnderstood();
}
function paintUnderstood() {
  const el = $("#understood"); if (!el) return;
  el.innerHTML = S.understood.length ? `<div class="understood"><b>فهمیدم:</b>${S.understood.map((t) => `<span class="chip">${esc(t)}</span>`).join("")}</div>` : "";
}
function resultsHTML() {
  if (S.loading) return `<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>`;
  const r = S.res;
  if (!r) return `<div class="empty"><h3>خرید حضوری، بدون سرگردانی</h3><p>اسم کالا را بنویسید؛ محدوده، بودجه و ساعت کاری را هم می‌توانید در همان جمله بگویید.</p></div>`;
  if (!r.items.length) return `<div class="empty"><h3>چیزی با این شرایط پیدا نشد</h3><ul>${r.hints.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>
    <div><button class="btn sec" id="widen">گسترش محدوده</button></div></div>`;
  return `<div class="meta">${fa(r.total)} محصول در محدودهٔ شما</div>` + r.items.map(productCard).join("");
}
function productCard(p) {
  const b = p.best, picked = S.compare.includes(p.id);
  const price = p.no_price ? `<b style="font-size:14px">قیمت ثبت نشده</b>` : `<span class="from">از</span><b>${esc(p.price_min_display.replace(" تومان", ""))}</b><span class="unit">تومان</span>`;
  const lvl = b.price_updated.level;
  return `<article class="pcard" data-pid="${esc(p.id)}">
    <div class="row" tabindex="0" role="button" data-open="${esc(p.id)}" aria-label="${esc(p.title)}">
      <div class="thumb" style="background-image:url('${esc(p.image || "")}')"></div>
      <div style="flex:1;min-width:0"><h3>${esc(p.title)}</h3>
        <div class="sub">${esc(p.category)}${p.brand ? " · " + esc(p.brand) : ""}</div>
        <div class="pricetag">${price}</div>
        <div class="sub">${fa(p.sellers_in_range)} فروشنده در محدوده · نزدیک‌ترین ${esc(p.nearest_label)}</div></div>
    </div>
    <div class="why">${hoursChip(b.hours)}
      <span class="chip ${lvl === "fresh" ? "ok" : lvl === "aging" ? "warn" : "bad"}">قیمت ${esc(b.price_updated.label)}</span>
      <span class="chip mute">موجودی تأییدنشده</span>
      <span style="flex-basis:100%">${b.reasons.map(esc).join(" · ")}</span></div>
    <div class="acts"><button class="btn sm" data-open="${esc(p.id)}">فروشنده‌ها و مقایسه</button>
      <button class="btn sm sec" data-cmp="${esc(p.id)}" aria-pressed="${picked}">${picked ? "✓ در مقایسه" : "مقایسهٔ کالا"}</button></div>
  </article>`;
}
async function runSearch(opts = {}) {
  S.loading = true; renderSearch();
  const p = fparams({ sort: S.sort }); p.set("q", S.q); p.set("limit", "30");
  try { S.res = await api("/api/search?" + p); } catch (e) { S.res = { items: [], hints: ["ارتباط با سرور برقرار نشد؛ دوباره تلاش کنید."], total: 0 }; }
  S.loading = false; renderSearch(); drawResultsOnMap();
  if (opts.scroll !== false) $("#tab-search").scrollTop = 0;
}
async function smartSearch(text) {
  text = text.trim(); if (!text) return;
  S.qRaw = text; S.understood = [];
  let u;
  try { u = await api("/api/understand?text=" + encodeURIComponent(text)); } catch { u = { query: text, flags: {}, items: [] }; }
  if (u.trip_hint && u.items.length >= 2) {
    u.items.forEach((q) => addTripItem({ query: q }, true));
    toast(`${fa(u.items.length)} کالا به برنامه خرید اضافه شد`);
    S.qRaw = ""; setTab("trip"); planTrip(); return;
  }
  if (u.area) { Object.assign(S.f, { lat: u.lat, lon: u.lon, area_label: u.area }); paintLoc(); S.understood.push("محدودهٔ " + u.area); }
  if (u.radius_km) { S.f.radius_km = u.radius_km; S.understood.push("شعاع " + radLabel(u.radius_km)); }
  if (u.max_price) { S.f.max_price = u.max_price; S.understood.push("سقف " + money(u.max_price) + " تومان"); }
  if (u.min_price) { S.f.min_price = u.min_price; S.understood.push("حداقل " + money(u.min_price) + " تومان"); }
  const lab = { open_now: "باز در زمان رسیدن", trabpay: "ترب‌پی", installment: "قسطی", fast: "ارسال فوری", warranty: "گارانتی", try_before_buy: "تست و تحویل" };
  for (const k in u.flags) if (u.flags[k]) { S.f[k] = true; S.understood.push(lab[k]); }
  if (u.sort) S.sort = u.sort;
  saveF();
  S.q = u.query || text;
  await runSearch();
}
$("#tab-search").addEventListener("submit", (e) => { e.preventDefault(); smartSearch($("#q").value); });
$("#tab-search").addEventListener("click", (e) => {
  const t = e.target;
  let b;
  if ((b = t.closest("[data-ex]"))) { $("#q").value = b.dataset.ex; smartSearch(b.dataset.ex); }
  else if ((b = t.closest("[data-sort]"))) { S.sort = b.dataset.sort; S.q ? runSearch({ scroll: false }) : renderSearch(); }
  else if (t.closest("#fbtn")) filtersSheet();
  else if ((b = t.closest("[data-clear]"))) {
    const k = b.dataset.clear;
    if (k === "radius_km") return filtersSheet();
    S.f[k] = (k === "max_price" || k === "min_price" || k === "when") ? null : false; if (k === "when") S.whenLabel = "همین حالا";
    saveF(); S.q ? runSearch({ scroll: false }) : renderSearch();
  }
  else if ((b = t.closest("[data-cmp]"))) toggleCompare(b.dataset.cmp);
  else if ((b = t.closest("[data-open]"))) openProduct(b.dataset.open);
  else if (t.closest("#widen")) { S.f.radius_km = 0; S.f.open_now = false; saveF(); runSearch(); }
  else if (t.closest("#cmp")) compareSheet();
});
$("#tab-search").addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target.matches("[data-open].row, .row[data-open]")) { e.preventDefault(); openProduct(e.target.dataset.open); } });
$("#locChip").onclick = locationSheet;

function toggleCompare(id) {
  const i = S.compare.indexOf(id);
  if (i >= 0) S.compare.splice(i, 1); else if (S.compare.length >= 3) return toast("حداکثر ۳ کالا را می‌شود مقایسه کرد"); else S.compare.push(id);
  renderSearch();
}

// ───────────────────────── product sheet
async function openProduct(id, sort = "best") {
  const sh = openSheet({ title: "در حال بارگذاری…", body: `<div class="skeleton"></div><div class="skeleton"></div>`, tall: true });
  let d;
  try { d = await api(`/api/product/${encodeURIComponent(id)}?` + fparams({ sort })); }
  catch { sh.body.innerHTML = `<div class="empty"><h3>محصول بارگذاری نشد</h3></div>`; return; }
  $(".sheet-h h2", sh.el).textContent = d.title;
  const st = d.stats;
  const best = d.in_range[0];
  const render = (curSort) => {
    sh.body.innerHTML = `
      ${d.images.length ? `<div class="gallery">${d.images.map((u) => `<img loading="lazy" src="${esc(u)}" alt="">`).join("")}</div>` : ""}
      <div class="stats"><div>کمترین قیمت ثبت‌شده<b>${st.min ? money(st.min) : "—"}</b></div><div>میانه<b>${st.median ? money(st.median) : "—"}</b></div><div>بیشترین<b>${st.max ? money(st.max) : "—"}</b></div></div>
      <div class="note">قیمت‌های بالا از میان ${fa(st.priced)} فروشنده با قیمت معتبر (از ${fa(st.sellers_total)} فروشندهٔ ثبت‌شده در کل تهران) هستند — آخرین به‌روزرسانی دیتا ${esc(st.snapshot)}.</div>
      <div class="seg" style="margin:8px 0">${[["best", "بهترین انتخاب"], ["cheap", "ارزان‌ترین"], ["near", "نزدیک‌ترین"]].map(([k, l]) => `<button data-s="${k}" class="${curSort === k ? "on" : ""}">${l}</button>`).join("")}</div>
      <div class="meta">${d.in_range.length ? fa(d.in_range.length) + " فروشنده با شرایط شما" : "هیچ فروشنده‌ای با این شرایط نیست"}</div>
      ${d.in_range.map((o, i) => sellerCard(o, { best: i === 0 && curSort === "best", product: { id: d.id, title: d.title, image: d.images[0] } })).join("")}
      ${!d.in_range.length ? `<div class="note warn">${d.out_of_range_count ? `${fa(d.out_of_range_count)} فروشنده خارج از محدوده یا فیلترهای شما هست. ` : ""}محدوده را بیشتر کنید یا فیلترها را کم کنید.</div>` : ""}
      ${d.out_of_range_count ? `<details class="specs"><summary>${fa(d.out_of_range_count)} فروشندهٔ دیگر (خارج از محدوده/فیلتر)</summary>${d.out_of_range.map((o) => sellerCard(o, { dim: true, product: { id: d.id, title: d.title, image: d.images[0] } })).join("")}</details>` : ""}
      ${d.specs.length ? `<details class="specs"><summary>مشخصات فنی</summary>${d.specs.map(([k, v]) => `<div class="spec"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join("")}</details>` : ""}`;
    drawOffersOnMap(d.in_range, d.out_of_range.slice(0, 8));
  };
  render("best");
  sh.body.addEventListener("click", async (e) => {
    const s = e.target.closest("[data-s]");
    if (s) { const nd = await api(`/api/product/${encodeURIComponent(id)}?` + fparams({ sort: s.dataset.s })); d.in_range = nd.in_range; render(s.dataset.s); return; }
    sellerAction(e, sh);
  });
}
function sellerAction(e, sheet) {
  const b = e.target.closest("[data-act]"); if (!b) return;
  const o = S.offers[b.dataset.oid]; if (!o) return;
  const act = b.dataset.act;
  if (act === "follow") { addFollow(o); }
  else if (act === "addtrip") { addTripItem({ product_id: o.product.id, label: o.product.title }); }
  else if (act === "onmap") { sheet && sheet.close(); setTab("map"); focusOffer(o); }
}

// ───────────────────────── compare products
async function compareSheet() {
  const sh = openSheet({ title: "مقایسهٔ کالاها", body: `<div class="skeleton"></div>`, tall: true });
  const ds = await Promise.all(S.compare.map((id) => api(`/api/product/${encodeURIComponent(id)}?` + fparams())));
  const keys = [];
  ds.forEach((d) => d.specs.forEach(([k]) => { if (!keys.includes(k)) keys.push(k); }));
  const sp = (d, k) => (d.specs.find((s) => s[0] === k) || [])[1] || "—";
  const cell = (d) => { const o = d.in_range[0]; return o ? `${esc(o.price.display)}<small style="display:block;color:var(--ink-3)">${esc(o.shop.name)} · ${esc(o.distance_label)}</small>` : "در محدوده نیست"; };
  const rows = [["بهترین گزینه در محدوده", cell], ["فروشنده در محدوده", (d) => fa(d.in_range.length)], ["کمترین قیمت کل تهران", (d) => (d.stats.min ? money(d.stats.min) + " تومان" : "—")], ["ساعت کاری بهترین گزینه", (d) => (d.in_range[0] ? esc(d.in_range[0].hours.label) : "—")]];
  const tbl = (rs) => rs.map(([l, fn]) => `<tr><th scope="row">${esc(l)}</th>${ds.map((d) => `<td>${fn(d)}</td>`).join("")}</tr>`).join("");
  sh.body.innerHTML = `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px" class="cmpt">
    <thead><tr><th></th>${ds.map((d) => `<th style="text-align:start;padding:6px;min-width:130px;vertical-align:top">${esc(d.title)}<br><button class="btn sm" data-o="${esc(d.id)}" style="margin-top:6px">فروشنده‌ها</button></th>`).join("")}</tr></thead>
    <tbody>${tbl(rows)}${tbl(keys.slice(0, 14).map((k) => [k, (d) => esc(sp(d, k))]))}</tbody></table></div>
    <style>.cmpt th,.cmpt td{border-bottom:1px solid var(--line);padding:7px 6px;text-align:start;vertical-align:top}.cmpt th[scope=row]{color:var(--ink-3);font-weight:500;min-width:90px}</style>`;
  sh.body.onclick = (e) => { const b = e.target.closest("[data-o]"); if (b) { sh.close(); openProduct(b.dataset.o); } };
}

// ───────────────────────── map
function initMap() {
  S.map = L.map("map", { zoomControl: false, attributionControl: true }).setView([S.f.lat, S.f.lon], 15);
  L.control.zoom({ position: "topleft" }).addTo(S.map);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(S.map);
  S.layer = L.layerGroup().addTo(S.map);
  S.base = L.layerGroup().addTo(S.map);
  S.map.on("click", (e) => { if (S.picking) { S.picking = false; setOrigin(e.latlng.lat, e.latlng.lng, "نقطهٔ انتخابی"); toast("مبدأ جدید ثبت شد"); } else $("#mapcard").innerHTML = ""; });
  drawBase();
}
function drawBase() {
  if (!S.map) return;
  S.base.clearLayers();
  L.marker([S.f.lat, S.f.lon], { icon: L.divIcon({ className: "", html: '<div class="pin me"></div>', iconSize: [20, 20], iconAnchor: [10, 10] }), zIndexOffset: 1000, keyboard: false }).addTo(S.base);
  if (S.f.radius_km > 0) L.circle([S.f.lat, S.f.lon], { radius: S.f.radius_km * 1000, color: "#e2231a", weight: 1.5, fillOpacity: 0.05, dashArray: "5 6" }).addTo(S.base);
}
function pinFor(o, label) {
  const cls = { open: "open", closing_soon: "soon", closed: "closed", unknown: "unknown" }[o.hours.state];
  return L.marker([o.shop.lat, o.shop.lon], { icon: L.divIcon({ className: "", html: `<div class="pin ${cls}"><span>${label ?? ""}</span></div>`, iconSize: [30, 30], iconAnchor: [15, 30] }), keyboard: false });
}
function showMapCard(o) {
  S.offers[o.offer_id] = S.offers[o.offer_id] || o;
  const sh = o.shop;
  $("#mapcard").innerHTML = `<div class="mapcard"><div class="head" style="display:flex;justify-content:space-between;gap:8px"><b>${esc(sh.name)}</b><span>${esc(o.distance_label)}</span></div>
    <div class="addr" style="font-size:12.5px;color:var(--ink-2)">${esc(sh.mall || sh.address)}</div>
    <div style="margin:6px 0">${hoursChip(o.hours)} <span class="chip mute">${esc(o.price.display)}</span></div>
    <div style="display:flex;gap:8px"><button class="btn sm" data-act="follow" data-oid="${esc(o.offer_id)}">${ic("bell")}پیگیری</button>
    <a class="btn sm sec" target="_blank" rel="noopener" href="https://www.google.com/maps/dir/?api=1&origin=${S.f.lat},${S.f.lon}&destination=${sh.lat},${sh.lon}&travelmode=walking">مسیر پیاده</a></div></div>`;
}
$("#mapcard").addEventListener("click", (e) => sellerAction(e, null));
function drawOffersOnMap(offers, dim = []) {
  if (!S.map) return;
  S.map.invalidateSize();
  S.layer.clearLayers();
  const pts = [[S.f.lat, S.f.lon]];
  offers.forEach((o, i) => { const m = pinFor(o, fa(i + 1)).addTo(S.layer); m.on("click", () => showMapCard(o)); pts.push([o.shop.lat, o.shop.lon]); S.offers[o.offer_id] = { ...o, product: S.offers[o.offer_id]?.product }; });
  dim.forEach((o) => { pinFor(o, "").addTo(S.layer).setOpacity(0.45).on("click", () => showMapCard(o)); });
  if (pts.length > 1) S.map.fitBounds(pts, { padding: [40, 40], maxZoom: 17 });
}
function drawResultsOnMap() {
  if (!S.map || !S.res) return;
  S.map.invalidateSize();
  const offs = S.res.items.map((p) => p.best);
  S.layer.clearLayers();
  const pts = [[S.f.lat, S.f.lon]];
  S.res.items.forEach((p, i) => { const o = p.best; const m = pinFor(o, fa(i + 1)).addTo(S.layer); m.on("click", () => { showMapCard({ ...o, product: { id: p.id, title: p.title } }); }); pts.push([o.shop.lat, o.shop.lon]); });
  if (pts.length > 1) S.map.fitBounds(pts, { padding: [40, 40], maxZoom: 16 });
}
function focusOffer(o) { S.map.invalidateSize(); setTimeout(() => { S.map.invalidateSize(); S.map.setView([o.shop.lat, o.shop.lon], 17); showMapCard(o); }, 80); }

// ───────────────────────── follow-ups
const fkey = (pid, shopId) => pid + "|" + shopId;
function saveFollow() { store.set("follow", S.follow); const n = Object.values(S.follow).filter((x) => x.status === "no" || x.status === "diff").length; const bf = $("#b-follow"); const tot = Object.keys(S.follow).length; bf.hidden = !tot; bf.textContent = fa(tot); bf.style.background = n ? "var(--brick)" : "var(--teal)"; }
function addFollow(o) {
  const pr = o.product || (S.offers[o.offer_id] || {}).product;
  if (!pr) return toast("اول وارد صفحهٔ محصول شوید");
  const k = fkey(pr.id, o.shop.id);
  if (S.follow[k]) { toast("قبلاً در پیگیری‌ها هست"); return setTab("follow"); }
  S.follow[k] = { key: k, pid: pr.id, title: pr.title, image: pr.image, shop: o.shop, price_display: o.price.display, price_value: o.price.value, price_jalali: o.price_updated.jalali,
    price_age: o.price_updated.label, hours: o.hours, status: "pending", added: Date.now(), walk: o.walk_min, distance: o.distance_label };
  saveFollow(); toast("به پیگیری‌ها اضافه شد — پیش از رفتن موجودی را بپرسید");
}
function script(x) {
  const close = x.hours && x.hours.closes ? ` اگر موجود است تا ساعت ${x.hours.closes} می‌رسم.` : " اگر موجود است امروز می‌آیم.";
  return `سلام، وقت بخیر. «${x.title}» را الان دارید؟ قیمتش ${x.price_value ? x.price_display + " (ثبت‌شده در ترب" + (x.price_jalali ? "، " + x.price_jalali : "") + ")" : "چقدر است؟"} درست است؟${close} گارانتی و قیمت نهایی را هم بفرمایید.`;
}
const STATUS = [["pending", "هنوز نپرسیدم"], ["asked", "پرسیدم؛ منتظرم"], ["yes", "موجود است"], ["no", "ندارد"], ["diff", "قیمت فرق دارد"]];
async function renderFollow(refresh) {
  const root = $("#tab-follow");
  const items = Object.values(S.follow).sort((a, b) => b.added - a.added);
  if (!items.length) { root.innerHTML = `<div class="empty"><h3>هنوز چیزی را پیگیری نمی‌کنید</h3><p>در صفحهٔ هر فروشنده «پیگیری موجودی» را بزنید. اینجا پیام آمادهٔ پرسیدن موجودی می‌گیرید و اگر فروشنده نداشت، جایگزین پیشنهاد می‌دهیم.</p></div>`; return; }
  root.innerHTML = `<div class="note">موجودی در دیتاست ثبت نشده؛ این بخش کمک می‌کند پیش از رفتن مطمئن شوید. شمارهٔ فروشگاه‌ها در دیتاست نمونه است.</div>` + items.map(followCard).join("");
  if (refresh) refreshFollowHours(items);
}
function followCard(x) {
  const sh = x.shop;
  const alt = x.alt ? altHTML(x) : "";
  return `<article class="fcard" data-k="${esc(x.key)}">
    <h4>${esc(x.title)}</h4>
    <div class="sub" style="font-size:12.5px;color:var(--ink-2)">${esc(sh.name)} · ${esc(sh.mall || sh.address)} · ${esc(x.distance || "")}</div>
    <div class="chips" style="margin-top:6px">${x.hours ? hoursChip(x.hours) : ""}<span class="chip mute">ثبت‌شده: ${esc(x.price_display)}${x.price_jalali ? " — " + esc(x.price_jalali) : ""}</span></div>
    <div class="status" role="group" aria-label="وضعیت پیگیری">${STATUS.map(([k, l]) => `<button class="s-${k} ${x.status === k ? "on" : ""}" data-st="${k}">${l}</button>`).join("")}</div>
    ${x.status === "pending" || x.status === "asked" ? `<div class="script" dir="rtl">${esc(script(x))}</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn sm sec" data-copy>${ic("copy")}کپی پیام</button>
      ${sh.phone ? `<a class="btn sm" href="tel:${esc(String(sh.phone).replace(/[^\d+]/g, ""))}">${ic("phone")}تماس (${esc(sh.phone)})</a>` : ""}</div>` : ""}
    ${x.status === "yes" ? `<div class="note" style="background:var(--ok-l)">عالی. ${x.hours && x.hours.closes ? "فروشگاه تا " + esc(x.hours.closes) + " باز است." : ""} <button class="btn sm" data-act2="route">مسیر روی نقشه</button></div>` : ""}
    ${alt}
    <div style="margin-top:8px"><button class="btn ghost sm" data-del>حذف از پیگیری</button></div>
  </article>`;
}
function altHTML(x) {
  if (x.alt === "loading") return `<div class="alt"><div class="skeleton" style="height:60px"></div></div>`;
  const a = x.alt;
  const same = a.same_product.slice(0, 3), sim = a.similar_products.slice(0, 3);
  return `<div class="alt"><h5>${x.status === "diff" ? "گزینه‌های ارزان‌تر/جایگزین" : "جایگزین‌ها"}</h5>
    ${same.length ? `<div class="meta" style="margin:2px 0">همین کالا، فروشندهٔ دیگر</div>${same.map((o) => { S.offers[o.offer_id] = { ...o, product: { id: x.pid, title: x.title, image: x.image } }; return `<div class="altrow"><div><b>${esc(o.shop.name)}</b> · ${esc(o.distance_label)}<br><span class="chip ${o.hours.state === "open" ? "ok" : "mute"}">${esc(o.hours.label)}</span> ${esc(o.price.display)}</div><button class="btn sm" data-act="follow" data-oid="${esc(o.offer_id)}">پیگیری</button></div>`; }).join("")}` : `<div class="meta">فروشندهٔ دیگری برای همین کالا در محدوده نیست${a.same_product_wider.length ? " — " + fa(a.same_product_wider.length) + " فروشنده دورتر هست." : "."}</div>`}
    ${sim.length ? `<div class="meta" style="margin:6px 0 2px">کالای مشابه (${esc(sim[0].why.join("، ") || "هم‌رده")})</div>${sim.map((s) => `<div class="altrow"><div><b>${esc(s.title)}</b><br>${esc(s.offer.price.display)} · ${esc(s.offer.shop.name)} · ${esc(s.offer.distance_label)}</div><button class="btn sm sec" data-openp="${esc(s.id)}">مشاهده</button></div>`).join("")}` : ""}</div>`;
}
$("#tab-follow").addEventListener("click", async (e) => {
  const card = e.target.closest(".fcard"); const t = e.target;
  if (t.closest("[data-act]")) return sellerAction(e, null);
  if (t.closest("[data-openp]")) return openProduct(t.closest("[data-openp]").dataset.openp);
  if (!card) return;
  const x = S.follow[card.dataset.k]; if (!x) return;
  let b;
  if ((b = t.closest("[data-st]"))) {
    x.status = b.dataset.st; saveFollow();
    if (x.status === "no" || x.status === "diff") {
      x.alt = "loading"; renderFollow();
      try { x.alt = await api("/api/alternatives", { product_id: x.pid, exclude_shop_ids: [x.shop.id], ref_price: x.price_value, filters: fbody() }); }
      catch { x.alt = { same_product: [], same_product_wider: [], similar_products: [] }; }
    }
    renderFollow();
  } else if (t.closest("[data-copy]")) { try { await navigator.clipboard.writeText(script(x)); toast("پیام کپی شد"); } catch { toast("کپی نشد؛ متن را انتخاب کنید"); } }
  else if (t.closest("[data-del]")) { delete S.follow[x.key]; saveFollow(); renderFollow(); }
  else if (t.closest("[data-act2]")) { setTab("map"); focusOffer({ offer_id: x.key, shop: x.shop, hours: x.hours || { state: "unknown", label: "" }, price: { display: x.price_display }, distance_label: x.distance }); }
});
async function refreshFollowHours(items) {
  const byP = {}; items.forEach((x) => (byP[x.pid] = byP[x.pid] || []).push(x));
  let changed = false;
  for (const pid of Object.keys(byP)) {
    try {
      const d = await api(`/api/product/${encodeURIComponent(pid)}?` + fparams({ radius_km: 0 }));
      [...d.in_range, ...d.out_of_range].forEach((o) => { const x = S.follow[fkey(pid, o.shop.id)]; if (x) { x.hours = o.hours; x.distance = o.distance_label; changed = true; } });
    } catch { /* offline: keep old */ }
  }
  if (changed && S.tab === "follow") { saveFollow(); renderFollow(); }
}

// ───────────────────────── trip planner
function saveTrip() { store.set("trip", S.trip); const b = $("#b-trip"); b.hidden = !S.trip.length; b.textContent = fa(S.trip.length); }
function addTripItem(it, silent) {
  const label = it.label || it.query;
  if (!label) return;
  if (S.trip.some((x) => (x.product_id && x.product_id === it.product_id) || (!x.product_id && x.query === it.query))) return silent ? 0 : toast("قبلاً در برنامه هست");
  S.trip.push({ ...it, label }); saveTrip(); S.tripRes = null;
  if (!silent) toast("به برنامهٔ خرید اضافه شد");
}
function renderTrip() {
  const root = $("#tab-trip");
  const items = S.trip;
  root.innerHTML = `
    <form class="smart" id="tf">${ic("plus")}<input id="ti" type="text" autocomplete="off" placeholder="کالا اضافه کنید (مثلاً چسب برق)" aria-label="افزودن کالا"><button class="go" aria-label="افزودن">${ic("plus")}</button></form>
    <div class="chips" style="margin:10px 0 2px">${activeChips().replace(/data-clear/g, 'data-tclear')}</div>
    <div class="trip-items">${items.length ? items.map((x, i) => {
      const f = S.tripRes && S.tripRes.items[i];
      return `<div class="titem"><span>${esc(x.label)}</span>${f ? `<span class="found chip ${f.found ? "ok" : "bad"}">${f.found ? fa(f.sellers) + " فروشگاه" : "پیدا نشد"}</span>` : ""}<button class="icon-btn" data-rm="${i}" aria-label="حذف ${esc(x.label)}">${ic("x")}</button></div>`;
    }).join("") : `<div class="empty"><h3>چند کالا را با هم بخرید</h3><p>کالاها را اینجا اضافه کنید تا مسیری بچینیم که کمترین توقف یا کمترین هزینه را داشته باشد؛ با توجه به ساعت کاری و فاصله.</p></div>`}</div>
    ${items.length ? `<button class="btn" id="plan" style="width:100%">${ic("route")}بچین</button>` : ""}
    <div id="plans">${S.tripLoading ? '<div class="skeleton"></div>' : tripHTML()}</div>`;
}
function tripHTML() {
  const r = S.tripRes; if (!r) return "";
  const unmet = r.unmet.map((u) => `<div class="note warn"><b>${esc(u.label)}</b>: ${esc(u.reason)} ${u.nearest_km ? `<button class="btn sm sec" data-widen="${Math.ceil(u.nearest_km + 0.5)}" style="margin-top:6px">جستجو تا ${fa(Math.ceil(u.nearest_km + 0.5))} کیلومتر</button>` : ""}</div>`).join("");
  return unmet + r.plans.map((p, pi) => `<section class="plan ${p.recommended ? "rec" : ""}">
    <h3>${esc(p.title)} ${p.recommended ? '<span class="chip ok">پیشنهاد ما</span>' : ""}</h3>
    ${p.tradeoff ? `<div style="font-size:13px;color:var(--ink-2)">${esc(p.tradeoff)}</div>` : ""}
    <div class="sumrow"><span><b>${fa(p.stops_count)}</b> توقف</span><span><b>${fa(p.minutes)}</b> دقیقه</span><span>پیاده <b>${esc(p.walk_label)}</b></span><span>جمع <b>${esc(p.total_display)}</b>${p.unpriced_items ? ` + ${fa(p.unpriced_items)} بدون قیمت` : ""}</span></div>
    ${p.warnings.length ? `<div class="note warn">${p.warnings.map(esc).join("<br>")}</div>` : ""}
    <div class="timeline">${p.stops.map((s, si) => `<div class="stop"><div class="leg">${si === 0 ? "از مبدأ" : "از توقف قبل"}: ${esc(s.leg_label)}${s.leg_walk_min ? " — " + fa(s.leg_walk_min) + " دقیقه" : ""}</div>
      <h4>${esc(s.shop.name)} <small style="font-weight:400;color:var(--ink-3)">${esc(s.shop.mall || "")}</small></h4>
      <div>${hoursChip(s.hours_at_arrival)} <small style="color:var(--ink-3)">در زمان رسیدن</small></div>
      <div class="its">${s.items.map((i) => { S.offers[i.offer.offer_id] = { ...i.offer, product: { id: null, title: i.label } }; return `<div><span>${esc(i.label)}</span><b>${esc(i.offer.price.display)}</b></div><div style="color:var(--ink-3);font-size:12px">${esc(i.offer.seller_title || "")}</div>`; }).join("")}</div></div>`).join("")}</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn sm" data-showplan="${pi}">${ic("map")}نمایش روی نقشه</button><button class="btn sm sec" data-followplan="${pi}">${ic("bell")}پیگیری موجودی همه</button></div>
  </section>`).join("");
}
async function planTrip() {
  if (!S.trip.length) return;
  S.tripLoading = true; renderTrip();
  try { S.tripRes = await api("/api/trip", { items: S.trip.map(({ product_id, query, label }) => ({ product_id, query, label })), filters: fbody() }); }
  catch { toast("برنامه‌ریزی انجام نشد"); }
  S.tripLoading = false; renderTrip();
  setTimeout(() => $("#plans") && $("#plans").scrollIntoView({ block: "start" }), 50);
}
$("#tab-trip").addEventListener("submit", (e) => { e.preventDefault(); const v = $("#ti").value.trim(); if (v) { addTripItem({ query: v }); renderTrip(); $("#ti").focus(); } });
$("#tab-trip").addEventListener("click", (e) => {
  const t = e.target; let b;
  if ((b = t.closest("[data-rm]"))) { S.trip.splice(+b.dataset.rm, 1); S.tripRes = null; saveTrip(); renderTrip(); }
  else if (t.closest("#plan")) planTrip();
  else if (t.closest("[data-tclear]")) filtersSheet();
  else if ((b = t.closest("[data-widen]"))) { S.f.radius_km = RADII.find((r) => r === 0 || r >= +b.dataset.widen) ?? 0; saveF(); planTrip(); }
  else if ((b = t.closest("[data-showplan]"))) showPlanOnMap(S.tripRes.plans[+b.dataset.showplan]);
  else if ((b = t.closest("[data-followplan]"))) {
    S.tripRes.plans[+b.dataset.followplan].stops.forEach((s) => s.items.forEach((i) => {
      const o = { ...i.offer, product: { id: S.trip[i.item_index].product_id || i.offer.offer_id, title: i.label } };
      const k = fkey(o.product.id, o.shop.id);
      if (!S.follow[k]) S.follow[k] = { key: k, pid: o.product.id, title: i.label, shop: o.shop, price_display: o.price.display, price_value: o.price.value, price_jalali: o.price_updated.jalali, hours: o.hours, status: "pending", added: Date.now(), distance: o.distance_label, tripOnly: true };
    }));
    saveFollow(); toast("همهٔ توقف‌ها به پیگیری‌ها اضافه شد"); setTab("follow");
  }
});
function showPlanOnMap(p) {
  setTab("map"); S.map.invalidateSize(); S.layer.clearLayers();
  const pts = [[S.f.lat, S.f.lon]];
  p.stops.forEach((s, i) => {
    const o = { shop: s.shop, hours: s.hours_at_arrival };
    pinFor(o, fa(i + 1)).addTo(S.layer).bindTooltip(s.shop.name);
    pts.push([s.shop.lat, s.shop.lon]);
  });
  L.polyline(pts, { color: "#e2231a", weight: 5, opacity: 0.85, dashArray: "10 8" }).addTo(S.layer);
  S.map.fitBounds(pts, { padding: [50, 50], maxZoom: 17 });
}

// ───────────────────────── assistant chat
const CHAT_HINTS = ["دوربین مداربسته زیر ۳ میلیون نزدیک شیخ هادی", "باتری قلمی و کابل شارژ و چسب برق رو یه جا بخرم", "ساعت مچی ترب‌پی الان باز تا ۲ کیلومتر"];
function renderChat() {
  const root = $("#tab-chat");
  if (!root.dataset.init) {
    root.dataset.init = 1;
    root.innerHTML = `<div class="msgs" id="msgs"></div><div class="chatbar"><div class="ex">${CHAT_HINTS.map((h) => `<button data-ex="${esc(h)}">${esc(h)}</button>`).join("")}</div>
      <form class="smart" id="cf"><input id="ci" type="text" autocomplete="off" placeholder="با دستیار حرف بزنید…" aria-label="پیام به دستیار"><button class="go" aria-label="ارسال">${ic("back")}</button></form></div>`;
  }
  paintChat();
}
function paintChat() {
  const m = $("#msgs"); if (!m) return;
  if (!S.chat.length) { m.innerHTML = `<div class="empty"><h3>دستیار خرید</h3><p>مثل یک آدم حرف بزنید: کالا، بودجه، محله و ساعت. من جستجو می‌کنم، مقایسه می‌کنم و اگر فروشنده نداشت جایگزین می‌دهم.</p></div>`; return; }
  m.innerHTML = S.chat.map((x) => x.role === "user" ? `<div class="msg u">${esc(x.content)}</div>` : `<div class="msg a">${x.pending ? "در حال بررسی…" : esc(x.content)}${x.note ? `<div class="meta">${esc(x.note)}</div>` : ""}${(x.cards || []).map(chatCard).join("")}${x.mode ? `<div class="meta" style="margin:6px 0 0">${x.mode === "llm" ? "پاسخ با هوش مصنوعی و ابزارهای جستجو" : "حالت قاعده‌محور (بدون LLM)"}</div>` : ""}</div>`).join("");
  const last = m.lastElementChild; if (last) last.scrollIntoView({ block: S.chat[S.chat.length - 1].role === 'user' ? 'end' : 'start' });
}
function chatCard(c) {
  if (c.type === "products") return `<div class="cards">${c.items.slice(0, 4).map((p) => `<div class="mini" data-open="${esc(p.id)}"><div class="thumb" style="background-image:url('${esc(p.image || "")}')"></div><div><b>${esc(p.title)}</b><small>${esc(p.price_min_display || "قیمت ثبت نشده")} · ${esc(p.best.shop.name)} · ${esc(p.nearest_label)}</small></div></div>`).join("")}
    ${c.total > 4 ? `<button class="btn sm sec" data-showres="${esc(c.query)}" style="margin-top:8px">همهٔ ${fa(c.total)} نتیجه در جستجو</button>` : ""}</div>`;
  if (c.type === "trip") return `<div class="cards">${c.plan.plans.map((p) => `<div class="mini" data-plan><div><b>${esc(p.title)}${p.recommended ? " ✓" : ""} — ${fa(p.stops_count)} توقف، ${esc(p.total_display)}</b><small>${p.stops.map((s) => esc(s.shop.name)).join(" ← ")}</small></div></div>`).join("")}
    <button class="btn sm" data-opentrip style="margin-top:8px">باز کردن برنامه</button></div>`;
  if (c.type === "offers") return `<div class="cards">${c.offers.slice(0, 4).map((o) => `<div class="mini"><div><b>${esc(o.shop.name)} — ${esc(o.price.display)}</b><small>${esc(o.distance_label)} · ${esc(o.hours.label)}</small></div></div>`).join("")}</div>`;
  if (c.type === "alternatives") return `<div class="cards">${c.data.similar_products.slice(0, 3).map((s) => `<div class="mini" data-open="${esc(s.id)}"><div class="thumb" style="background-image:url('${esc(s.image || "")}')"></div><div><b>${esc(s.title)}</b><small>${esc(s.offer.price.display)} · ${esc(s.why.join("، "))}</small></div></div>`).join("")}</div>`;
  return "";
}
async function sendChat(text) {
  text = text.trim(); if (!text) return;
  S.chat.push({ role: "user", content: text });
  const pend = { role: "assistant", content: "", pending: true }; S.chat.push(pend); paintChat();
  try {
    const hist = S.chat.filter((x) => !x.pending).map(({ role, content }) => ({ role, content }));
    const r = await api("/api/chat", { messages: hist, filters: fbody() });
    Object.assign(pend, { pending: false, content: r.reply, cards: r.cards, mode: r.mode, note: r.note });
    pend.state = r.state;
    const t = r.cards.find((c) => c.type === "trip"); if (t) S.lastTripPlan = t.plan;
  } catch { Object.assign(pend, { pending: false, content: "ارتباط با سرور برقرار نشد؛ دوباره تلاش کنید." }); }
  paintChat();
}
$("#tab-chat").addEventListener("submit", (e) => { e.preventDefault(); const v = $("#ci").value; $("#ci").value = ""; sendChat(v); });
$("#tab-chat").addEventListener("click", (e) => {
  const t = e.target; let b;
  if ((b = t.closest("[data-ex]"))) sendChat(b.dataset.ex);
  else if ((b = t.closest("[data-open]"))) openProduct(b.dataset.open);
  else if (t.closest("[data-opentrip]")) { if (S.lastTripPlan) { S.tripRes = S.lastTripPlan; S.trip = S.lastTripPlan.items.map((i) => ({ query: i.label, label: i.label })); saveTrip(); } setTab("trip"); }
  else if ((b = t.closest("[data-showres]"))) { const last = [...S.chat].reverse().find((m) => m.state); if (last && last.state.filters) { const { when, ...rest } = last.state.filters; Object.assign(S.f, rest); paintLoc(); saveF(); } S.q = b.dataset.showres; S.qRaw = S.q; S.understood = []; setTab("search"); runSearch(); }
});
$("#tab-map").innerHTML = "";

// ───────────────────────── boot
async function boot() {
  try { S.cfg = await api("/api/config"); }
  catch { document.body.innerHTML = '<div class="empty" style="padding:60px 20px"><h3>سرور در دسترس نیست</h3></div>'; return; }
  $("#snap").textContent = "";
  paintLoc(); saveFollow(); saveTrip();
  document.body.dataset.tab = "search";
  initMap(); renderSearch(); renderTrip();
  const url = new URL(location.href);
  const q = url.searchParams.get("q"); if (q) { $("#q").value = q; smartSearch(q); }
  window.__S = S; // handy for debugging / demo scripts
}
boot();
