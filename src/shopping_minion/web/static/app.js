/* Shopping Minion: one Vue app, one screen per run state (LLD-M2 section 7).
 * It talks only to this server (/api/...). Nothing here reaches the store. */
const { createApp, ref, reactive, computed, onMounted, onBeforeUnmount } = Vue;

const UNITS = ["un", "g", "kg", "ml", "l", "pct", "cx", "lata", "dz"];
const CANCELLABLE = [
  "reading_list", "reviewing_list", "syncing_history", "searching", "deciding",
  "picking", "reviewing_cart", "filling_cart",
];

class ApiError extends Error {
  constructor(status, body) {
    super((body && (body.detail || body.message)) || `erro ${status}`);
    this.status = status;
    this.body = body;
  }
}

const moneyFmt = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const confFmt = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function money(v) {
  const n = Number(v);
  return Number.isFinite(n) ? moneyFmt.format(n) : "—";
}

createApp({
  setup() {
    // ---- run snapshot ----------------------------------------------------
    const run = ref({ state: null });
    const state = computed(() => run.value.state);
    const toast = ref("");
    const offline = ref(false);
    const reconnecting = ref(false); // the event stream dropped and isn't back yet
    const busy = ref(false);
    const now = ref(Date.now());

    // idle
    const runs = ref([]);
    const access = ref(null);
    const uploading = ref(false);
    const MAX_PHOTOS = 5;
    const pending = ref([]); // {file, url}: photos picked, not sent yet (the pages, in order)
    const photoUrls = ref([]); // object URLs of the pages sent from this browser session
    const photoTab = ref(0);
    const photoFailed = ref(false); // the server's copy (GET /api/run/photo) didn't load
    // The pages sent from here if we have them, else the server's copies, so the photos also
    // show after a reload or when the upload came from the phone.
    const photoCount = computed(() => photoUrls.value.length || run.value.photos || 0);
    const listPhoto = computed(() => {
      const i = Math.min(photoTab.value, Math.max(0, photoCount.value - 1));
      if (photoUrls.value[i]) return photoUrls.value[i];
      const id = run.value && run.value.run_id;
      return id && photoCount.value && !photoFailed.value ? `/api/run/photo?i=${i}&run=${id}` : null;
    });

    // reviewing_list
    const rows = ref([]);
    const saveText = ref("");
    let rowSeq = 0;
    let rowsRunId = null;
    let saveTimer = null;

    // searching / deciding / filling
    const searchEvents = ref([]);
    const fillEvents = ref([]);

    // picking
    const pick = ref(null);
    const selected = ref(-1);
    const pickTotal = ref(0);

    // reviewing_cart
    const cart = ref(null);

    // reading_list timer
    let readingSince = null;

    // ---- api ---------------------------------------------------------------
    async function api(method, path, body, form) {
      const opts = { method, headers: {} };
      if (form) {
        opts.body = form;
      } else if (body !== undefined) {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
      }
      const res = await window.fetch(path, opts);
      let data = null;
      try {
        data = await res.json();
      } catch (_) {
        data = null;
      }
      if (res.status === 409) {
        // The run is not where the page thought it was: resync and re-render.
        refresh(true);
        throw new ApiError(409, data);
      }
      if (!res.ok) throw new ApiError(res.status, data);
      return data;
    }

    function fail(err) {
      if (err instanceof ApiError && err.status === 409) return; // resynced by api()
      toast.value = err.message || String(err);
    }

    // ---- refresh: GET /api/run and what the state needs ----------------------
    let refreshToken = 0;
    let refreshTimer = null;
    function scheduleRefresh() {
      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(refresh, 40);
    }

    let forceReload = false; // survives a newer refresh superseding this one
    async function refresh(force = false) {
      if (force === true) forceReload = true;
      const mine = ++refreshToken;
      let r;
      try {
        r = await api("GET", "/api/run");
        offline.value = false;
      } catch (err) {
        if (!(err instanceof ApiError)) offline.value = true;
        return;
      }
      if (mine !== refreshToken) return;
      const forced = forceReload;
      forceReload = false;
      await applyRun(r, mine, forced);
    }

    async function applyRun(r, mine, force) {
      const prev = run.value.state;
      run.value = r;
      const s = r.state;

      if (s === "reading_list" && readingSince === null) readingSince = Date.now();
      if (s !== "reading_list") readingSince = null;

      if (s === "idle") {
        if (prev !== "idle") {
          clearPhoto();
          resetProgress();
          if (prev !== null && lastSeq > 0) {
            // A new run may number its events from 1 again: listen from the start.
            lastSeq = 0;
            subscribe();
          }
          pick.value = null;
          cart.value = null;
          loadIdleExtras();
        }
      }
      if (s === "reviewing_list") {
        const id = r.run_id ?? null;
        if (prev !== "reviewing_list" || id !== rowsRunId) {
          rowsRunId = id;
          rows.value = fromServerList(r.list).map(toRow);
          saveText.value = "";
        }
      }
      if (s === "picking") {
        // Reload only on entry or after our own answer, so a stray event can't reset the selection.
        if (force || prev !== "picking" || !pick.value) await loadPick(mine);
      } else {
        pick.value = null;
        pickTotal.value = 0;
      }
      if (s === "reviewing_cart") {
        if (r.cart_draft) cart.value = r.cart_draft;
      }
    }

    function resetProgress() {
      searchEvents.value = [];
      fillEvents.value = [];
    }

    async function loadIdleExtras() {
      try {
        runs.value = (await api("GET", "/api/runs")) || [];
      } catch (_) {
        runs.value = [];
      }
      try {
        const a = await api("GET", "/api/access"); // answers only on the desktop
        access.value = a && a.qr_svg ? a : null;
      } catch (_) {
        access.value = null;
      }
    }

    // ---- events (SSE) ----------------------------------------------------------
    let source = null;
    let lastSeq = 0;
    let reconnectTimer = null;

    function subscribe() {
      if (source) source.close();
      source = new EventSource(`/api/run/events?after=${lastSeq}`);
      source.onopen = () => {
        offline.value = false;
        reconnecting.value = false;
      };
      source.onmessage = (m) => {
        let ev;
        try {
          ev = JSON.parse(m.data);
        } catch (_) {
          return;
        }
        if (typeof ev.seq === "number") lastSeq = Math.max(lastSeq, ev.seq);
        onEvent(ev);
      };
      source.onerror = () => {
        // Close and reopen from the last seq we saw, so nothing is missed.
        source.close();
        source = null;
        offline.value = true;
        reconnecting.value = true;
        clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(() => {
          refresh();
          subscribe();
        }, 1500);
      };
    }

    // Coming back to the foreground, a phone may have silently lost the stream: reopen it
    // from the last seq and reload the snapshot.
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible") return;
      clearTimeout(reconnectTimer);
      refresh();
      subscribe();
    });

    function onEvent(ev) {
      const d = ev.data || {};
      switch (ev.kind) {
        case "state":
          if (d.state === "reading_list" || d.state === "idle") resetProgress();
          if (d.state === "searching") searchEvents.value = [];
          if (d.state === "filling_cart") fillEvents.value = [];
          scheduleRefresh();
          break;
        case "search":
          searchEvents.value = [...searchEvents.value.filter((e) => e.i !== d.i), d];
          break;
        case "fill":
          fillEvents.value = [...fillEvents.value.filter((e) => e.i !== d.i), d];
          break;
        case "decide":
          scheduleRefresh();
          break;
        case "error":
          scheduleRefresh(); // the failed state carries the message
          break;
      }
    }

    // ---- idle: upload ------------------------------------------------------------
    function clearPhoto() {
      photoUrls.value.forEach((u) => URL.revokeObjectURL(u));
      photoUrls.value = [];
      photoTab.value = 0;
      photoFailed.value = false;
    }

    function onPhotos(ev) {
      const files = Array.from(ev.target.files || []);
      ev.target.value = "";
      for (const file of files) {
        if (pending.value.length >= MAX_PHOTOS) {
          toast.value = `no máximo ${MAX_PHOTOS} fotos por lista`;
          break;
        }
        pending.value.push({ file, url: URL.createObjectURL(file) });
      }
    }
    function removePending(i) {
      const [gone] = pending.value.splice(i, 1);
      if (gone) URL.revokeObjectURL(gone.url);
    }

    async function readList() {
      if (!pending.value.length) return;
      uploading.value = true;
      try {
        const form = new FormData();
        pending.value.forEach((p) => form.append("photos", p.file));
        await api("POST", "/api/run", undefined, form);
        clearPhoto();
        photoUrls.value = pending.value.map((p) => p.url);
        pending.value = [];
        await refresh();
      } catch (err) {
        fail(err);
      } finally {
        uploading.value = false;
      }
    }

    function dateText(v) {
      const d = new Date(v);
      return Number.isNaN(d.getTime()) ? String(v ?? "") : d.toLocaleString("pt-BR");
    }
    function countText(items) {
      const n = Array.isArray(items) ? items.length : Number(items);
      if (!Number.isFinite(n)) return "";
      return `${n} ${n === 1 ? "item" : "itens"}`;
    }

    const elapsed = computed(() =>
      readingSince === null ? 0 : Math.max(0, Math.floor((now.value - readingSince) / 1000)),
    );

    // ---- reviewing_list ------------------------------------------------------------
    function fromServerList(list) {
      if (Array.isArray(list)) return list;
      if (list && Array.isArray(list.items)) return list.items;
      return [];
    }

    function toRow(it) {
      return {
        key: ++rowSeq,
        source_line: it.source_line ?? "",
        name: it.name ?? "",
        search_term: it.search_term ?? "",
        constraints: (it.constraints || []).join(", "),
        brand: it.brand ?? "",
        qty_value: it.quantity ? it.quantity.value : "",
        qty_unit: it.quantity ? it.quantity.unit : "un",
        qty_on: !!it.quantity, // the quantity field is showing (the list has one, or "+ quantidade")
        open: false, // "mais": name, constraints and brand
        needs_review: !!it.needs_review,
      };
    }

    function toItem(row) {
      const value = Number(row.qty_value);
      const name = String(row.name).trim();
      return {
        source_line: String(row.source_line).trim(),
        name,
        search_term: String(row.search_term).trim() || name,
        constraints: String(row.constraints)
          .split(",")
          .map((c) => c.trim())
          .filter(Boolean),
        brand: String(row.brand ?? "").trim() || null,
        quantity:
          row.qty_value !== "" && row.qty_value !== null && value > 0
            ? { value, unit: row.qty_unit }
            : null,
        needs_review: !!row.needs_review,
      };
    }

    function touchList() {
      saveText.value = "salvando…";
      clearTimeout(saveTimer);
      saveTimer = setTimeout(saveList, 600);
    }

    async function saveList() {
      clearTimeout(saveTimer);
      saveTimer = null;
      if (state.value !== "reviewing_list") return;
      try {
        await api("PUT", "/api/run/list", { items: rows.value.map(toItem) });
        saveText.value = "salvo";
      } catch (err) {
        saveText.value = "não salvo";
        fail(err);
        throw err;
      }
    }

    // One card per line: consecutive items with the same source_line share a card.
    const cards = computed(() => {
      const out = [];
      rows.value.forEach((row, i) => {
        const line = String(row.source_line).trim();
        const last = out[out.length - 1];
        if (line && last && last.line === line) last.items.push({ row, i });
        else out.push({ key: row.key, line, items: [{ row, i }] });
      });
      return out;
    });
    const cardOpen = (card) => card.items.some((it) => it.row.open);
    function toggleMore(card) {
      const open = !cardOpen(card);
      card.items.forEach((it) => (it.row.open = open));
    }
    function addQuantity(row) {
      row.qty_on = true;
    }
    function dropQuantity(row) {
      row.qty_on = false;
      row.qty_value = "";
      touchList();
    }

    function addRow() {
      const row = toRow({
        source_line: "", name: "", search_term: "", constraints: [], needs_review: false,
      });
      row.open = true; // a row typed by hand has no reading: name and the rest are what to fill
      rows.value.push(row);
      touchList();
    }
    function removeRow(i) {
      rows.value.splice(i, 1);
      touchList();
    }

    async function confirmList() {
      busy.value = true;
      try {
        if (saveTimer !== null || saveText.value === "salvando…") await saveList();
        await api("POST", "/api/run/list/confirm");
        await refresh();
      } catch (err) {
        fail(err);
      } finally {
        busy.value = false;
      }
    }

    // ---- searching / filling progress -------------------------------------------------
    const searchList = computed(() => [...searchEvents.value].sort((a, b) => a.i - b.i));
    const searchLast = computed(() => searchList.value[searchList.value.length - 1] || null);
    const searchTotal = computed(() => Math.max(0, ...searchEvents.value.map((e) => e.n || 0)));
    const searchDone = computed(() => Math.max(0, ...searchEvents.value.map((e) => e.i || 0)));

    const FILL_DONE = ["added", "untouched", "failed"];
    const fillList = computed(() => [...fillEvents.value].sort((a, b) => a.i - b.i));
    const fillTotal = computed(() => Math.max(0, ...fillEvents.value.map((e) => e.n || 0)));
    const fillDone = computed(() => fillEvents.value.filter((e) => FILL_DONE.includes(e.status)).length);

    function pct(done, total) {
      return total ? `${Math.min(100, Math.round((100 * done) / total))}%` : "0%";
    }
    function fillLabel(status) {
      return (
        { added: "adicionado", untouched: "já estava", failed: "falhou" }[status] || status || "…"
      );
    }
    function fillClass(status) {
      return { added: "ok", untouched: "muted", failed: "bad" }[status] || "";
    }

    // ---- picking ----------------------------------------------------------------------------
    const cands = ref([]);

    async function loadPick(mine) {
      let p;
      try {
        p = await api("GET", "/api/run/picks/next");
      } catch (err) {
        fail(err);
        return;
      }
      if (mine !== undefined && mine !== refreshToken) return;
      const jev = p.jev || { choice: null, confidence: null, nothing_fit: false };
      p.jev = jev;
      // Jev's pick first.
      const list = [...(p.candidates || [])];
      const at = list.findIndex((c) => c.product_id === jev.choice);
      if (at > 0) list.unshift(...list.splice(at, 1));
      cands.value = list;
      selected.value = at >= 0 && list[0].available ? 0 : -1;
      // `position`/`total` come from the backend; the max of `left` is the fallback.
      pickTotal.value = p.total || Math.max(pickTotal.value, p.left || 0);
      pick.value = p;
    }

    const pickPosition = computed(() => {
      if (!pick.value) return 0;
      return pick.value.position || pickTotal.value - pick.value.left + 1;
    });

    const jevNote = computed(() => {
      const j = pick.value && pick.value.jev;
      if (!j) return "";
      if (j.nothing_fit) return "Jev: nada parece servir";
      if (j.confidence !== null && j.confidence !== undefined) {
        return `Jev não tem certeza (${confFmt.format(j.confidence)})`;
      }
      return "";
    });

    async function sendPick(productId) {
      if (busy.value || !pick.value) return;
      busy.value = true;
      try {
        await api("POST", "/api/run/picks", { index: pick.value.index, product_id: productId });
        await refresh(true);
      } catch (err) {
        fail(err);
      } finally {
        busy.value = false;
      }
    }

    function takeCandidate(i) {
      const c = cands.value[i];
      if (!c || !c.available) return;
      sendPick(c.product_id);
    }
    function skipPick() {
      sendPick(null);
    }

    function moveSelection(step) {
      const n = cands.value.length;
      if (!n) return;
      let i = selected.value;
      for (let tries = 0; tries < n; tries++) {
        i = i < 0 ? (step > 0 ? 0 : n - 1) : (i + step + n) % n;
        if (cands.value[i].available) {
          selected.value = i;
          const el = document.querySelector(`[data-test="cand-${cands.value[i].product_id}"]`);
          if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
          return;
        }
      }
    }

    function onKey(ev) {
      if (state.value !== "picking" || !pick.value) return;
      const tag = ev.target && ev.target.tagName;
      if (["INPUT", "SELECT", "TEXTAREA"].includes(tag) || ev.ctrlKey || ev.metaKey || ev.altKey) {
        return;
      }
      if (ev.key === "Enter") {
        ev.preventDefault();
        if (selected.value >= 0) takeCandidate(selected.value);
      } else if (ev.key === "ArrowRight" || ev.key === "ArrowDown") {
        ev.preventDefault();
        moveSelection(1);
      } else if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") {
        ev.preventDefault();
        moveSelection(-1);
      } else if (ev.key === "0") {
        ev.preventDefault();
        skipPick();
      }
    }

    // ---- reviewing_cart ----------------------------------------------------------------------------
    const lineProduct = (l) => l.product || l.candidate || {};
    const lineItems = (l) =>
      (l.items || []).map((it) => (typeof it === "string" ? it : it.name || it.source_line || ""));
    const skipped = computed(() =>
      ((cart.value && cart.value.skipped) || []).map((s) =>
        typeof s === "string" ? s : (s.item && s.item.name) || s.name || "",
      ),
    );

    async function putCart(line) {
      try {
        const r = await api("PUT", "/api/run/cart-draft", { lines: [line] });
        cart.value = (r && r.cart_draft) || r;
      } catch (err) {
        fail(err);
        refresh();
      }
    }

    function editLine(l, value, unit) {
      const v = Number(value);
      if (!(v > 0)) {
        refresh(); // reject the edit and show the stored value again
        return;
      }
      return putCart({ line_id: l.line_id, quantity: { value: v, unit }, remove: false });
    }
    function removeLine(l) {
      return putCart({ line_id: l.line_id, quantity: null, remove: true });
    }

    async function confirmCart() {
      busy.value = true;
      try {
        await api("POST", "/api/run/cart-draft/confirm");
        await refresh();
      } catch (err) {
        fail(err);
      } finally {
        busy.value = false;
      }
    }

    // ---- done ------------------------------------------------------------------------------------------------
    const outcome = computed(() => run.value.outcome || null);
    // Every line that isn't ok, with the add's message or the check's verdict (from the server).
    const problems = computed(() => (outcome.value && outcome.value.problems) || []);
    const oks = computed(() => ((outcome.value && outcome.value.checks) || []).filter((c) => c.ok));
    const extras = computed(() => (outcome.value && outcome.value.extras) || []);
    const checkNames = (c) => (c.item_names || (c.item_name ? [c.item_name] : [])).join(" + ");

    async function resetRun() {
      busy.value = true;
      try {
        await api("DELETE", "/api/run");
        await refresh();
      } catch (err) {
        fail(err);
      } finally {
        busy.value = false;
      }
    }

    // The same cart pass on the failed lines only; the server goes done -> filling_cart -> done.
    async function retryFailed() {
      if (busy.value) return;
      busy.value = true;
      try {
        await api("POST", "/api/run/retry");
        await refresh(true);
      } catch (err) {
        fail(err);
      } finally {
        busy.value = false;
      }
    }

    // ---- cancel -----------------------------------------------------------------------------------------------
    const canCancel = computed(() => CANCELLABLE.includes(state.value));
    async function cancelRun() {
      try {
        await api("POST", "/api/run/cancel");
        await refresh();
      } catch (err) {
        fail(err);
      }
    }

    // ---- lifecycle ----------------------------------------------------------------------------------------------
    let ticker = null;
    onMounted(async () => {
      document.addEventListener("keydown", onKey);
      ticker = setInterval(() => {
        now.value = Date.now();
      }, 1000);
      await refresh();
      subscribe();
    });
    onBeforeUnmount(() => {
      document.removeEventListener("keydown", onKey);
      clearInterval(ticker);
      if (source) source.close();
    });

    return {
      units: UNITS, run, state, toast, offline, reconnecting, busy, canCancel, runs, access, uploading,
      pending, maxPhotos: MAX_PHOTOS, photoCount, photoTab, listPhoto, photoFailed,
      rows, cards, cardOpen, toggleMore, addQuantity, dropQuantity, saveText, pick, cands, selected, pickTotal, pickPosition, jevNote, cart, skipped,
      elapsed, searchList, searchLast, searchTotal, searchDone, fillList, fillTotal, fillDone,
      outcome, problems, oks, extras,
      money, pct, dateText, countText, fillLabel, fillClass, checkNames, lineProduct, lineItems,
      onPhotos, removePending, readList, touchList, addRow, removeRow, confirmList, takeCandidate, skipPick, editLine,
      removeLine, confirmCart, resetRun, retryFailed, cancelRun,
    };
  },
}).mount("#app");
