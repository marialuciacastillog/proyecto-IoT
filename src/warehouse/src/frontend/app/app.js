// Front de una bodega. La misma imagen corre en wh-001, wh-002 y wh-003:
// Nginx manda /api/... al balanceador de ESTA bodega, así que cada front solo ve su propia bodega.
const API = "/api";
const CATALOG = "/catalog";  // Catálogo global de la central (Nginx lo reenvía a la IP elástica)
const LOW_STOCK = 10;  // mismo umbral que el reporte low_stock de la central

// config.js lo genera Nginx con la variable WAREHOUSE_ID del contenedor
const config = window.APP_CONFIG || {};

const state = {
    warehouseId: config.warehouseId && !config.warehouseId.includes("$") ? config.warehouseId : "",
    products: [],
    stock: {},       // id del producto -> { quantity, reserved_quantity }
    movements: [],   // todos los movimientos de la bodega
    catalog: [],     // Productos del catálogo global
    lots: [],        // lotes con unidades (o todos, si se marca "ver lotes vacíos")
    reservations: [],
    alerts: [],
    transactions: { summary: null, transactions: [] }
};


// ---------- Utilidades ----------

// Escapa texto antes de meterlo en el HTML (los nombres de productos los escribe un usuario)
function esc(value) {
    const div = document.createElement("div");
    div.textContent = value === null || value === undefined ? "" : String(value);
    return div.innerHTML;
}

function formatDate(value) {
    return value ? new Date(value).toLocaleString("es-CO") : "-";
}

function formatNumber(value) {
    return Number(value || 0).toLocaleString("es-CO");
}

function toast(message, type) {
    const element = document.getElementById("toast");
    element.textContent = message;
    element.className = "toast show " + (type || "ok");
    clearTimeout(element.timer);
    element.timer = setTimeout(() => { element.className = "toast"; }, 4500);
}

// fetch con manejo de errores: devuelve el JSON o lanza un Error con el detalle de FastAPI.
// Cada llamada lleva el token (authHeader, de session.js). Si la API responde 401, se vuelve al login.
async function api(path, options) {
    const request = Object.assign({}, options);
    request.headers = Object.assign({}, request.headers, authHeader());
    const response = await fetch(API + path, request);
    if (response.status === 401 && path !== "/health") {
        handleUnauthorized();
    }
    let data = null;
    try { data = await response.json(); } catch (error) { data = null; }
    if (!response.ok) {
        let detail = "Error " + response.status;
        if (data && data.detail) {
            detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
        }
        throw new Error(detail);
    }
    return { data: data, headers: response.headers };
}

// Lo mismo que api() pero contra el catálogo de la central
async function catalogApi(path, options) {
    const request = Object.assign({}, options);
    request.headers = Object.assign({}, request.headers, authHeader());
    const response = await fetch(CATALOG + path, request);
    let data = null;
    try { data = await response.json(); } catch (error) { data = null; }
    if (!response.ok) {
        let detail = "Error " + response.status;
        if (data && data.detail) {
            detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
        }
        throw new Error(detail);
    }
    return data;
}

function formatMoney(value) {
    return value === null || value === undefined ? "-" : "$" + Number(value).toLocaleString("es-CO", { maximumFractionDigits: 2 });
}

const movementLabels = {
    PURCHASE: ["Compra", "ok"],
    SALE: ["Venta", "error"],
    RECEIPT: ["Entrada", "ok"],
    RETURN: ["Devolución", "ok"],
    TRANSFER_IN: ["Transferencia entrante", "ok"],
    SHIPMENT: ["Salida", "error"],
    TRANSFER_OUT: ["Transferencia saliente", "error"],
    RESERVATION: ["Reserva", "warn"],
    RESERVATION_CONFIRM: ["Reserva confirmada", "warn"],
    RELEASE: ["Reserva liberada", "neutral"]
};

const reservationKinds = { SALE: "Venta", MANUAL: "Manual", TRANSFER: "Transferencia" };
const reservationStatus = {
    ACTIVE: ["Activa", "warn"], SOLD: ["Vendida", "ok"], RELEASED: ["Liberada", "neutral"],
    EXPIRED: ["Vencida", "error"], CONSUMED: ["Salió en transferencia", "neutral"]
};
const alertStatus = { OPEN: ["Abierta", "error"], RESOLVED: ["Resuelta", "ok"], REVIEWED: ["Revisada", "neutral"] };
const alertLevels = { ERROR: ["Incongruencia", "error"], WARNING: ["Aviso", "warn"], INFO: ["Info", "neutral"] };

function badge(pair, fallback) {
    const label = pair || [fallback, "neutral"];
    return '<span class="badge ' + label[1] + '">' + esc(label[0]) + "</span>";
}

function formatDay(value) {
    return value ? new Date(value + "T00:00:00").toLocaleDateString("es-CO") : "-";
}

// Cuánto le falta a una reserva para vencerse, en palabras
function timeLeft(minutes) {
    let text = "vencida, se libera en el próximo minuto";
    if (minutes >= 60 * 24) {
        text = "en " + Math.floor(minutes / (60 * 24)) + " d " + Math.floor((minutes % (60 * 24)) / 60) + " h";
    } else if (minutes >= 60) {
        text = "en " + Math.floor(minutes / 60) + " h " + (minutes % 60) + " min";
    } else if (minutes >= 0) {
        text = "en " + minutes + " min";
    }
    return text;
}

function movementBadge(type) {
    const label = movementLabels[type] || [type, "neutral"];
    return '<span class="badge ' + label[1] + '">' + esc(label[0]) + "</span>";
}

function productById(id) {
    return state.products.find(p => p.id === id);
}


// ---------- Navegación ----------

function showView(name) {
    document.querySelectorAll(".view").forEach(v => v.classList.toggle("active", v.id === "view-" + name));
    document.querySelectorAll("#nav button").forEach(b => b.classList.toggle("active", b.dataset.view === name));
}


// ---------- Carga de datos ----------

async function checkHealth() {
    const status = document.getElementById("api-status");
    try {
        const result = await api("/health");
        status.textContent = "API en línea";
        status.className = "status-dot ok";
        document.getElementById("api-instance").textContent = result.data.instance || "-";
    } catch (error) {
        status.textContent = "API sin conexión";
        status.className = "status-dot error";
    }
}

async function loadAll() {
    try {
        const products = await api("/products?limit=1000");
        state.products = products.data;

        // Existencias de cada producto (la API las da por producto, con caché en Redis)
        const stockResults = await Promise.all(state.products.map(p => api("/inventory/" + p.id).catch(() => null)));

        state.stock = {};
        state.products.forEach((p, i) => {
            const stock = stockResults[i] ? stockResults[i].data : null;
            state.stock[p.id] = stock || { quantity: 0, reserved_quantity: 0 };
            // Si Nginx no recibió WAREHOUSE_ID, se toma de las respuestas de la API
            if (!state.warehouseId && stock && stock.warehouse_id) {
                state.warehouseId = stock.warehouse_id;
            }
        });

        // Movimientos, lotes, reservas, alertas y compras/ventas de toda la bodega
        const includeEmpty = document.getElementById("lot-empty").checked;
        const results = await Promise.all([
            api("/movements?limit=1000"),
            api("/lots?include_empty=" + includeEmpty),
            api("/reservations"),
            api("/alerts"),
            api("/transactions")
        ]);
        state.movements = results[0].data;
        state.lots = results[1].data;
        state.reservations = results[2].data;
        state.alerts = results[3].data;
        state.transactions = results[4].data;
    } catch (error) {
        toast("No se pudo cargar el inventario: " + error.message, "error");
    }
    document.getElementById("warehouse-name").textContent = state.warehouseId ? state.warehouseId.toUpperCase() : "-";
    renderAll();
}

async function loadCache() {
    try {
        const result = await api("/cache/stats");
        const stats = result.data;
        document.getElementById("cache-ratio").textContent = Math.round(stats.hit_ratio * 100) + "%";
        document.getElementById("cache-hits").textContent = formatNumber(stats.hits);
        document.getElementById("cache-misses").textContent = formatNumber(stats.misses);
        let text = stats.redis_ok ? "Redis conectado." : "Redis no responde: la API está leyendo directo de PostgreSQL.";
        if (!stats.cache_enabled) {
            text = "La caché está apagada (CACHE_ENABLED=false).";
        }
        document.getElementById("cache-state").textContent = text;
    } catch (error) {
        document.getElementById("cache-state").textContent = "No se pudieron leer las estadísticas de la caché.";
    }
}

function refresh() {
    checkHealth();
    loadAll();
    // Las estadísticas de la caché solo las ve el Administrador Global
    if (hasRole("ADMIN_GLOBAL")) {
        loadCache();
    }
}


// ---------- Entrada después del login ----------

// Un Admin por Bodega o un Encargado de OTRA bodega no puede usar este front
function startApp(session) {
    const notice = document.getElementById("wrong-warehouse");
    const wrongWarehouse = !isOwnWarehouse(session, state.warehouseId);
    document.querySelectorAll(".view").forEach(v => v.classList.toggle("role-hidden", wrongWarehouse));
    notice.classList.toggle("role-hidden", !wrongWarehouse);
    if (wrongWarehouse) {
        notice.textContent = "Tu usuario pertenece a la bodega " + String(session.warehouseId).toUpperCase() +
            ". Entra al front de esa bodega para trabajar con ella.";
        checkHealth();
    } else {
        refresh();
        loadWarehouseTitle();
        if (hasRole("ADMIN_BODEGA")) {
            loadCatalog();
        }
    }
}


// ---------- Pintar cada vista ----------

function renderAll() {
    renderSummary();
    renderProducts();
    renderStock();
    renderProductSelect();
    renderHistory();
    renderReservations();
    renderLots();
    renderTransactions();
    renderAlerts();
    updateMovementForm();
}

function renderSummary() {
    let available = 0;
    let reserved = 0;
    const low = [];
    state.products.forEach(p => {
        const stock = state.stock[p.id];
        available += stock.quantity;
        reserved += stock.reserved_quantity;
        if (stock.quantity < LOW_STOCK) {
            low.push(p);
        }
    });
    document.getElementById("stat-products").textContent = formatNumber(state.products.length);
    document.getElementById("stat-available").textContent = formatNumber(available);
    document.getElementById("stat-reserved").textContent = formatNumber(reserved);
    document.getElementById("stat-low").textContent = formatNumber(low.length);
    const openAlerts = state.alerts.filter(a => a.status === "OPEN").length;
    document.getElementById("stat-reservations").textContent = formatNumber(state.reservations.filter(r => r.status === "ACTIVE").length);
    document.getElementById("stat-alerts").textContent = formatNumber(openAlerts);
    const summary = state.transactions.summary || { sales: { total: 0 }, purchases: { total: 0 } };
    document.getElementById("stat-sales").textContent = formatMoney(summary.sales.total);
    document.getElementById("stat-purchases").textContent = formatMoney(summary.purchases.total);
    const count = document.getElementById("alerts-count");
    count.textContent = openAlerts;
    count.classList.toggle("role-hidden", openAlerts === 0);

    const body = document.getElementById("low-stock-body");
    if (low.length === 0) {
        body.innerHTML = '<tr><td colspan="3" class="empty">Ningún producto por debajo de ' + LOW_STOCK + " unidades</td></tr>";
        return;
    }
    low.sort((a, b) => state.stock[a.id].quantity - state.stock[b.id].quantity);
    body.innerHTML = low.map(p =>
        "<tr><td>" + esc(p.sku) + "</td><td>" + esc(p.name) + '</td><td class="num"><span class="badge warn">' +
        formatNumber(state.stock[p.id].quantity) + "</span></td></tr>"
    ).join("");
}

function matches(product, text) {
    const haystack = [product.sku, product.name, product.category].join(" ").toLowerCase();
    return haystack.includes(text.toLowerCase());
}

function renderProducts() {
    const text = document.getElementById("product-search").value.trim();
    const list = state.products.filter(p => matches(p, text));
    const body = document.getElementById("products-body");
    if (list.length === 0) {
        body.innerHTML = '<tr><td colspan="7" class="empty">Esta bodega todavía no maneja productos</td></tr>';
        return;
    }
    body.innerHTML = list.map(p =>
        "<tr><td>" + p.id + "</td><td><strong>" + esc(p.sku) + "</strong></td><td>" + esc(p.name) +
        '<span class="sub">' + esc(p.unit) + (p.description ? " · " + esc(p.description) : "") + "</span>" +
        "</td><td>" + esc(p.category || "-") + "</td><td>" + esc(p.supplier || "-") + '</td><td class="num">' +
        formatMoney(p.sale_price) + '</td><td class="num">' + formatMoney(p.wholesale_price) + "</td></tr>"
    ).join("");
}

function renderStock() {
    const text = document.getElementById("stock-search").value.trim();
    const list = state.products.filter(p => matches(p, text));
    const body = document.getElementById("stock-body");
    if (list.length === 0) {
        body.innerHTML = '<tr><td colspan="6" class="empty">No hay productos</td></tr>';
        return;
    }
    body.innerHTML = list.map(p => {
        const stock = state.stock[p.id];
        const quantity = stock.quantity < LOW_STOCK
            ? '<span class="badge warn">' + formatNumber(stock.quantity) + "</span>"
            : formatNumber(stock.quantity);
        return "<tr><td><strong>" + esc(p.sku) + "</strong></td><td>" + esc(p.name) + '<span class="sub">' + esc(p.unit) +
            '</span></td><td class="num">' + quantity + '</td><td class="num">' + formatNumber(stock.reserved_quantity) +
            '</td><td class="num">' + formatNumber(state.lots.filter(l => l.product_id === p.id && l.quantity > 0).length) +
            '</td><td><button class="link" data-history="' + p.id + '">Historial</button></td></tr>';
    }).join("");
}

function renderProductSelect() {
    const options = state.products.map(p =>
        '<option value="' + p.id + '">' + esc(p.sku) + " · " + esc(p.name) + "</option>"
    ).join("");
    ["m-product", "r-product"].forEach(id => {
        const select = document.getElementById(id);
        const current = select.value;
        select.innerHTML = state.products.length === 0 ? '<option value="">Agrega productos desde el catálogo</option>' : options;
        if (current) {
            select.value = current;
        }
    });
    const lotSelect = document.getElementById("lot-product");
    const currentLot = lotSelect.value;
    lotSelect.innerHTML = '<option value="">Todos los productos</option>' + options;
    lotSelect.value = currentLot;
}

// El formulario cambia según el tipo: las entradas piden lote y vencimiento; las salidas, de qué lote;
// compras y ventas piden precio y proveedor o cliente
function updateMovementForm() {
    const type = document.getElementById("m-type").value;
    const productId = parseInt(document.getElementById("m-product").value, 10);
    const product = productById(productId);
    const isEntry = ["PURCHASE", "RECEIPT", "RETURN"].includes(type);
    const hasPrice = ["PURCHASE", "SALE"].includes(type);
    document.getElementById("m-entry-lot").classList.toggle("role-hidden", !isEntry);
    document.getElementById("m-exit-lot").classList.toggle("role-hidden", isEntry);
    document.getElementById("m-price-row").classList.toggle("role-hidden", !hasPrice);
    document.getElementById("m-price-type-field").classList.toggle("role-hidden", type !== "SALE");
    document.getElementById("m-counterparty-label").textContent = type === "SALE" ? "Cliente" : "Proveedor";
    const priceType = document.getElementById("m-price-type").value;
    let catalogPrice = null;
    if (product && type === "PURCHASE") {
        catalogPrice = product.cost;
    } else if (product && type === "SALE") {
        catalogPrice = priceType === "MAYORISTA" ? product.wholesale_price : product.sale_price;
    }
    document.getElementById("m-price").placeholder = catalogPrice !== null ? "Del catálogo: " + formatMoney(catalogPrice) : "";
    document.getElementById("m-counterparty").placeholder = type === "PURCHASE" && product ? (product.supplier || "") : "Opcional";
    const lots = state.lots.filter(l => l.product_id === productId && l.quantity > 0);
    const lotOptions = lots.map(l => '<option value="' + esc(l.lot_code) + '">' + esc(l.lot_code) + " · quedan " + l.quantity +
        (l.expiry_date ? " · vence " + formatDay(l.expiry_date) : "") + (l.expired ? " (vencido)" : "") + "</option>").join("");
    const lotSelect = document.getElementById("m-lot-out");
    const currentLot = lotSelect.value;
    lotSelect.innerHTML = '<option value="">Automático: el que vence primero</option>' + lotOptions;
    lotSelect.value = lots.some(l => l.lot_code === currentLot) ? currentLot : "";
    const hints = {
        PURCHASE: "La compra suma al disponible, entra a un lote y queda en el histórico de compras con su costo.",
        SALE: "La venta resta del disponible, sale de los lotes (nunca de uno vencido) y queda en el histórico de ventas.",
        RECEIPT: "Entrada sin compra, por ejemplo un ajuste. Entra a un lote.",
        SHIPMENT: "Salida sin venta, por ejemplo una baja por daño o por vencimiento.",
        RETURN: "Un cliente devuelve unidades: vuelven al disponible en un lote de devoluciones."
    };
    document.getElementById("m-hint").textContent = hints[type] || "";
}

const historyGroups = {
    BUY: ["PURCHASE"],
    SELL: ["SALE"],
    IN: ["PURCHASE", "RECEIPT", "RETURN", "TRANSFER_IN"],
    OUT: ["SALE", "SHIPMENT", "TRANSFER_OUT", "RESERVATION_CONFIRM"],
    TR: ["TRANSFER_IN", "TRANSFER_OUT", "RESERVATION_CONFIRM"],
    RES: ["RESERVATION", "RESERVATION_CONFIRM", "RELEASE"]
};

function renderHistory() {
    const group = document.getElementById("history-type").value;
    const text = document.getElementById("history-search").value.trim().toLowerCase();
    const list = state.movements.filter(m => {
        const okType = !group || historyGroups[group].includes(m.type);
        const okText = !text || (m.sku + " " + (m.reference || "") + " " + (m.lot_code || "")).toLowerCase().includes(text);
        return okType && okText;
    });
    const body = document.getElementById("history-body");
    if (list.length === 0) {
        body.innerHTML = '<tr><td colspan="8" class="empty">Sin movimientos</td></tr>';
        return;
    }
    body.innerHTML = list.slice(0, 500).map(m =>
        "<tr><td>" + formatDate(m.created_at) + "</td><td>" + esc(m.sku) +
        "</td><td>" + movementBadge(m.type) + '</td><td class="num">' + formatNumber(m.quantity) +
        "</td><td>" + esc(m.lot_code || "-") + '</td><td class="num">' + formatMoney(m.total) +
        "</td><td>" + esc(m.reference || "-") + "</td><td>" + esc(m.created_by || "-") + "</td></tr>"
    ).join("");
}

// Historial completo de un producto (RF-27): lo arma la API con el lote, el precio y cómo quedó el stock
async function openProductHistory(productId) {
    const product = productById(productId);
    document.getElementById("product-modal-title").textContent = "Historial de " + (product ? product.sku + " · " + product.name : productId);
    const body = document.getElementById("product-modal-body");
    body.innerHTML = '<tr><td colspan="10" class="empty">Cargando...</td></tr>';
    document.getElementById("product-modal").classList.add("open");
    try {
        const result = await api("/inventory/" + productId + "/movements");
        const list = result.data.slice().reverse();
        body.innerHTML = list.length === 0
            ? '<tr><td colspan="10" class="empty">Sin movimientos</td></tr>'
            : list.map(m =>
                "<tr><td>" + formatDate(m.created_at) + "</td><td>" + movementBadge(m.type) + '</td><td class="num">' +
                formatNumber(m.quantity) + "</td><td>" + esc(m.lot_code || "-") + '</td><td class="num">' + formatMoney(m.unit_price) +
                '</td><td class="num">' + formatMoney(m.total) + "</td><td>" + esc(m.reference || "-") +
                (m.counterparty ? '<span class="sub">' + esc(m.counterparty) + "</span>" : "") + "</td><td>" + esc(m.created_by || "-") +
                '</td><td class="num">' + formatNumber(m.available_after) + '</td><td class="num">' + formatNumber(m.reserved_after) + "</td></tr>"
            ).join("");
    } catch (error) {
        body.innerHTML = '<tr><td colspan="10" class="empty">' + esc(error.message) + "</td></tr>";
    }
}


// ---------- Reservas ----------

function renderReservations() {
    const filter = document.getElementById("reservation-status").value;
    const list = state.reservations.filter(r => !filter || r.status === filter);
    const body = document.getElementById("reservations-body");
    const canAct = hasRole("ADMIN_BODEGA") || hasRole("ENCARGADO");
    if (list.length === 0) {
        body.innerHTML = '<tr><td colspan="7" class="empty">' + (filter ? "No hay reservas activas" : "No hay reservas") + "</td></tr>";
        return;
    }
    body.innerHTML = list.map(r => {
        let expires = formatDate(r.expires_at);
        if (r.status === "ACTIVE") {
            expires += '<span class="sub">' + (r.kind === "TRANSFER" ? "la cierra la saga" : timeLeft(r.minutes_left)) + "</span>";
        } else {
            expires = '<span class="sub">' + esc((r.closed_by || "") + " · " + formatDate(r.closed_at)) + "</span>";
        }
        let actions = "";
        if (r.status === "ACTIVE" && r.kind !== "TRANSFER" && canAct) {
            actions = '<button class="link" data-sell="' + esc(r.id) + '">Vender</button> · <button class="link" data-release="' + esc(r.id) + '">Liberar</button>';
        }
        const detail = [r.counterparty, r.reason, r.reference].filter(Boolean).map(esc).join(" · ");
        return "<tr><td><strong>" + esc(r.id) + '</strong><span class="sub">' + esc(r.created_by) + "</span></td><td>" + esc(r.sku) +
            '<span class="sub">' + (detail || esc(r.product_name)) + '</span></td><td class="num">' + formatNumber(r.quantity) +
            "</td><td>" + esc(reservationKinds[r.kind] || r.kind) + "</td><td>" + expires + "</td><td>" +
            badge(reservationStatus[r.status], r.status) + "</td><td>" + actions + "</td></tr>";
    }).join("");
}

function toggleReservationKind() {
    const manual = document.getElementById("r-kind").value === "MANUAL";
    document.getElementById("r-hours-field").classList.toggle("role-hidden", !manual);
    document.getElementById("r-client-field").classList.toggle("role-hidden", manual);
}

async function createReservation(event) {
    event.preventDefault();
    const form = event.target;
    const kind = document.getElementById("r-kind").value;
    const payload = {
        product_id: parseInt(document.getElementById("r-product").value, 10),
        quantity: parseInt(document.getElementById("r-quantity").value, 10),
        kind: kind,
        hours: kind === "MANUAL" ? parseInt(document.getElementById("r-hours").value, 10) : null,
        reason: document.getElementById("r-reason").value.trim() || null,
        counterparty: kind === "SALE" ? (document.getElementById("r-client").value.trim() || null) : null
    };
    form.querySelector("button").disabled = true;
    try {
        const reservation = (await api("/reservations", postJson(payload))).data;
        toast("Reserva " + reservation.id + " creada. Vence el " + formatDate(reservation.expires_at));
        document.getElementById("r-quantity").value = "";
        document.getElementById("r-reason").value = "";
        document.getElementById("r-client").value = "";
        await loadAll();
    } catch (error) {
        toast(error.message, "error");
    }
    form.querySelector("button").disabled = false;
}

function postJson(payload) {
    return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) };
}

function openAction(title, html) {
    document.getElementById("action-title").textContent = title;
    document.getElementById("action-body").innerHTML = html;
    document.getElementById("action-modal").classList.add("open");
}

function closeAction() {
    document.getElementById("action-modal").classList.remove("open");
}

function openSell(reservationId) {
    const r = state.reservations.find(item => item.id === reservationId);
    const product = productById(r.product_id);
    openAction("Vender la reserva " + r.id,
        '<p class="sub">' + esc(r.sku) + " · " + esc(r.product_name) + " x" + r.quantity + "</p>" +
        '<div class="form-row mt"><div class="field"><label for="s-price-type">Precio</label><select id="s-price-type">' +
        '<option value="DETAL">Detal (' + formatMoney(product ? product.sale_price : null) + ')</option>' +
        '<option value="MAYORISTA">Mayorista (' + formatMoney(product ? product.wholesale_price : null) + ")</option></select></div>" +
        '<div class="field"><label for="s-price">Precio por unidad</label><input id="s-price" type="number" min="0" step="0.01" placeholder="Del catálogo"></div></div>' +
        '<div class="field"><label for="s-client">Cliente</label><input id="s-client" value="' + esc(r.counterparty || "") + '"></div>' +
        '<button class="btn btn-primary" id="s-confirm">Registrar la venta</button>');
    document.getElementById("s-confirm").addEventListener("click", async () => {
        const price = document.getElementById("s-price").value;
        try {
            await api("/reservations/" + r.id + "/sell", postJson({
                price_type: document.getElementById("s-price-type").value,
                unit_price: price === "" ? null : Number(price),
                counterparty: document.getElementById("s-client").value.trim() || null
            }));
            toast("Reserva " + r.id + " vendida");
            closeAction();
            await loadAll();
        } catch (error) {
            toast(error.message, "error");
        }
    });
}

function openRelease(reservationId) {
    const r = state.reservations.find(item => item.id === reservationId);
    openAction("Liberar la reserva " + r.id,
        '<p class="sub">Las ' + r.quantity + " unidades de " + esc(r.sku) + " vuelven a estar disponibles.</p>" +
        '<div class="field mt"><label for="l-note">Nota</label><input id="l-note" placeholder="Opcional: por qué se libera"></div>' +
        '<button class="btn btn-primary" id="l-confirm">Liberar</button>');
    document.getElementById("l-confirm").addEventListener("click", async () => {
        try {
            await api("/reservations/" + r.id + "/release", postJson({ note: document.getElementById("l-note").value.trim() || null }));
            toast("Reserva " + r.id + " liberada");
            closeAction();
            await loadAll();
        } catch (error) {
            toast(error.message, "error");
        }
    });
}


// ---------- Lotes ----------

function renderLots() {
    const productId = parseInt(document.getElementById("lot-product").value, 10);
    const list = state.lots.filter(l => !productId || l.product_id === productId);
    const body = document.getElementById("lots-body");
    if (list.length === 0) {
        body.innerHTML = '<tr><td colspan="7" class="empty">No hay lotes</td></tr>';
        return;
    }
    body.innerHTML = list.map(l => {
        let expiry = l.expiry_date ? formatDay(l.expiry_date) : "-";
        if (l.expired) {
            expiry = '<span class="badge error">Vencido ' + formatDay(l.expiry_date) + "</span>";
        }
        return "<tr><td><strong>" + esc(l.sku) + '</strong><span class="sub">' + esc(l.product_name) + "</span></td><td>" +
            esc(l.lot_code) + '</td><td class="num">' + formatNumber(l.quantity) + '</td><td class="num">' + formatNumber(l.initial_quantity) +
            "</td><td>" + expiry + "</td><td>" + formatDate(l.received_at) + '<span class="sub">' + esc(l.created_by) + "</span></td>" +
            '<td><button class="link" data-lot="' + l.product_id + "|" + esc(l.lot_code) + '">Seguimiento</button></td></tr>';
    }).join("");
}

// Seguimiento de un lote: qué movimientos le metieron o le sacaron unidades
async function openLot(value) {
    const parts = value.split("|");
    try {
        const lot = (await api("/lots/" + parts[0] + "/" + encodeURIComponent(parts[1]))).data;
        const rows = lot.movements.map(m =>
            "<tr><td>" + formatDate(m.created_at) + "</td><td>" + movementBadge(m.type) + '</td><td class="num">' +
            formatNumber(m.lot_quantity) + "</td><td>" + esc(m.reference || "-") + "</td><td>" + esc(m.created_by) + "</td></tr>").join("");
        openAction("Lote " + lot.lot_code + " · " + lot.sku,
            '<p class="sub">Quedan ' + lot.quantity + " de " + lot.initial_quantity + " unidades" +
            (lot.expiry_date ? " · vence el " + formatDay(lot.expiry_date) : "") + "</p>" +
            '<div class="table-wrap mt"><table><thead><tr><th>Fecha</th><th>Movimiento</th><th class="num">Unidades del lote</th><th>Referencia</th><th>Usuario</th></tr></thead><tbody>' +
            rows + "</tbody></table></div>");
    } catch (error) {
        toast(error.message, "error");
    }
}


// ---------- Compras y ventas ----------

function renderTransactions() {
    const summary = state.transactions.summary;
    if (summary) {
        document.getElementById("tx-purchases").textContent = formatNumber(summary.purchases.count);
        document.getElementById("tx-purchases-units").textContent = formatNumber(summary.purchases.units) + " unidades";
        document.getElementById("tx-purchases-total").textContent = formatMoney(summary.purchases.total);
        document.getElementById("tx-sales").textContent = formatNumber(summary.sales.count);
        document.getElementById("tx-sales-units").textContent = formatNumber(summary.sales.units) + " unidades";
        document.getElementById("tx-sales-total").textContent = formatMoney(summary.sales.total);
    }
    const type = document.getElementById("transaction-type").value;
    const list = state.transactions.transactions.filter(t => !type || t.type === type);
    const body = document.getElementById("transactions-body");
    if (list.length === 0) {
        body.innerHTML = '<tr><td colspan="9" class="empty">Todavía no hay compras ni ventas</td></tr>';
        return;
    }
    body.innerHTML = list.map(t =>
        "<tr><td>" + formatDate(t.created_at) + "</td><td>" + movementBadge(t.type) +
        (t.price_type ? '<span class="sub">' + esc(t.price_type === "MAYORISTA" ? "Mayorista" : "Detal") + "</span>" : "") +
        "</td><td>" + esc(t.sku) + '<span class="sub">' + esc(t.product_name) + '</span></td><td class="num">' + formatNumber(t.quantity) +
        '</td><td class="num">' + formatMoney(t.unit_price) + '</td><td class="num">' + formatMoney(t.total) + "</td><td>" +
        esc(t.counterparty || "-") + "</td><td>" + esc(t.lot_code || "-") + "</td><td>" + esc(t.created_by) + "</td></tr>"
    ).join("");
}


// ---------- Alertas ----------

function renderAlerts() {
    const filter = document.getElementById("alert-status").value;
    const list = state.alerts.filter(a => !filter || a.status === filter);
    const body = document.getElementById("alerts-body");
    const canReview = hasRole("ADMIN_BODEGA") || hasRole("ADMIN_GLOBAL");
    if (list.length === 0) {
        body.innerHTML = '<tr><td colspan="5" class="empty">' + (filter ? "No hay alertas abiertas: todo cuadra" : "No hay alertas") + "</td></tr>";
        return;
    }
    body.innerHTML = list.map(a => {
        let statusHtml = badge(alertStatus[a.status], a.status);
        if (a.status === "REVIEWED") {
            statusHtml += '<span class="sub">' + esc(a.reviewed_by) + ": " + esc(a.review_note) + "</span>";
        } else if (a.status === "RESOLVED") {
            statusHtml += '<span class="sub">' + formatDate(a.resolved_at) + "</span>";
        }
        const action = a.status === "OPEN" && canReview ? '<button class="link" data-review="' + a.id + '">Revisar</button>' : "";
        return "<tr><td>" + formatDate(a.created_at) + "</td><td>" + badge(alertLevels[a.level], a.level) + "</td><td><strong>" +
            esc(a.title) + '</strong><span class="sub">' + esc(a.message) + "</span></td><td>" + statusHtml + "</td><td>" + action + "</td></tr>";
    }).join("");
}

function openReview(alertId) {
    const a = state.alerts.find(item => item.id === alertId);
    openAction("Revisar alerta",
        "<p><strong>" + esc(a.title) + '</strong></p><p class="sub">' + esc(a.message) + "</p>" +
        '<div class="field mt"><label for="v-note">Qué revisaste o qué hiciste</label><textarea id="v-note" placeholder="Por ejemplo: se contó físicamente y se registró la salida que faltaba"></textarea></div>' +
        '<p class="sub">Si es una incongruencia y el stock sigue sin cuadrar, el vigilante la sigue viendo: se marca resuelta cuando cuadre.</p>' +
        '<button class="btn btn-primary mt" id="v-confirm">Marcar revisada</button>');
    document.getElementById("v-confirm").addEventListener("click", async () => {
        try {
            await api("/alerts/" + a.id + "/review", postJson({ note: document.getElementById("v-note").value.trim() }));
            toast("Alerta revisada");
            closeAction();
            await loadAll();
        } catch (error) {
            toast(error.message, "error");
        }
    });
}


// ---------- Formularios ----------

// ---------- Agregar productos del catálogo ----------

async function loadCatalog() {
    try {
        state.catalog = await catalogApi("/products");
    } catch (error) {
        state.catalog = [];
    }
    renderAssignSelect();
}

// Solo se ofrecen los productos que esta bodega todavía no maneja
function renderAssignSelect() {
    const select = document.getElementById("a-product");
    const pending = state.catalog.filter(p => !p.warehouses.includes(state.warehouseId));
    if (pending.length === 0) {
        select.innerHTML = '<option value="">No hay productos nuevos en el catálogo</option>';
        document.getElementById("a-detail").textContent = "";
        return;
    }
    select.innerHTML = pending.map(p =>
        '<option value="' + esc(p.product_code) + '">' + esc(p.product_code) + " · " + esc(p.name) + "</option>"
    ).join("");
    showAssignDetail();
}

function showAssignDetail() {
    const product = state.catalog.find(p => p.product_code === document.getElementById("a-product").value);
    if (product) {
        document.getElementById("a-detail").textContent = product.category + " · Proveedor: " + product.supplier +
            " · Venta " + formatMoney(product.sale_price) + " · Mayorista " + formatMoney(product.wholesale_price);
        document.getElementById("a-quantity").value = product.initial_quantity;
    }
}

async function assignProduct(event) {
    event.preventDefault();
    const form = event.target;
    const code = document.getElementById("a-product").value;
    if (!code) {
        return;
    }
    form.querySelector("button").disabled = true;
    try {
        const result = await catalogApi("/warehouses/" + state.warehouseId + "/products", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ product_code: code, initial_quantity: parseInt(document.getElementById("a-quantity").value, 10) })
        });
        toast(result.message);
        await loadCatalog();
        // El producto llega por el aviso del catálogo: se vuelve a cargar en unos segundos
        setTimeout(loadAll, 4000);
        setTimeout(loadAll, 10000);
    } catch (error) {
        toast(error.message, "error");
    }
    form.querySelector("button").disabled = false;
}

async function loadWarehouseTitle() {
    try {
        const warehouse = await catalogApi("/warehouses/" + state.warehouseId);
        document.getElementById("warehouse-title").textContent = "· " + warehouse.name;
    } catch (error) {
        document.getElementById("warehouse-title").textContent = "";
    }
}

async function createMovement(event) {
    event.preventDefault();
    const form = event.target;
    const headers = { "Content-Type": "application/json" };
    const correlationId = document.getElementById("m-correlation").value.trim();
    if (correlationId) {
        headers["X-Correlation-ID"] = correlationId;
    }
    const type = document.getElementById("m-type").value;
    const isEntry = ["PURCHASE", "RECEIPT", "RETURN"].includes(type);
    const price = document.getElementById("m-price").value;
    const payload = {
        product_id: parseInt(document.getElementById("m-product").value, 10),
        type: type,
        quantity: parseInt(document.getElementById("m-quantity").value, 10),
        reference: document.getElementById("m-reference").value.trim() || null,
        lot_code: (isEntry ? document.getElementById("m-lot").value.trim() : document.getElementById("m-lot-out").value) || null,
        expiry_date: isEntry ? (document.getElementById("m-expiry").value || null) : null
    };
    if (type === "PURCHASE" || type === "SALE") {
        payload.unit_price = price === "" ? null : Number(price);
        payload.counterparty = document.getElementById("m-counterparty").value.trim() || null;
    }
    if (type === "SALE") {
        payload.price_type = document.getElementById("m-price-type").value;
    }
    form.querySelector("button").disabled = true;
    try {
        const result = await api("/inventory/movements", { method: "POST", headers: headers, body: JSON.stringify(payload) });
        const usedId = result.headers.get("X-Correlation-ID") || "-";
        toast("Movimiento registrado. Correlation ID: " + usedId);
        ["m-quantity", "m-reference", "m-correlation", "m-lot", "m-expiry", "m-price", "m-counterparty"].forEach(id => {
            document.getElementById(id).value = "";
        });
        await loadAll();
    } catch (error) {
        toast(error.message, "error");
    }
    form.querySelector("button").disabled = false;
}

async function resetCache() {
    try {
        await api("/cache/stats", { method: "DELETE" });
        toast("Contadores de la caché reiniciados");
        loadCache();
    } catch (error) {
        toast(error.message, "error");
    }
}


// ---------- Eventos ----------

document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("#nav button").forEach(b => b.addEventListener("click", () => showView(b.dataset.view)));
    document.querySelectorAll("[data-refresh]").forEach(b => b.addEventListener("click", refresh));
    document.getElementById("assign-form").addEventListener("submit", assignProduct);
    document.getElementById("a-product").addEventListener("change", showAssignDetail);
    document.getElementById("movement-form").addEventListener("submit", createMovement);
    document.getElementById("reset-cache-btn").addEventListener("click", resetCache);
    document.getElementById("product-search").addEventListener("input", renderProducts);
    document.getElementById("stock-search").addEventListener("input", renderStock);
    document.getElementById("history-type").addEventListener("change", renderHistory);
    document.getElementById("history-search").addEventListener("input", renderHistory);
    ["m-type", "m-product", "m-price-type"].forEach(id => document.getElementById(id).addEventListener("change", updateMovementForm));
    document.getElementById("reservation-form").addEventListener("submit", createReservation);
    document.getElementById("r-kind").addEventListener("change", toggleReservationKind);
    document.getElementById("reservation-status").addEventListener("change", renderReservations);
    document.getElementById("lot-product").addEventListener("change", renderLots);
    document.getElementById("lot-empty").addEventListener("change", loadAll);
    document.getElementById("transaction-type").addEventListener("change", renderTransactions);
    document.getElementById("alert-status").addEventListener("change", renderAlerts);
    document.getElementById("reservations-body").addEventListener("click", event => {
        if (event.target.dataset.sell) {
            openSell(event.target.dataset.sell);
        } else if (event.target.dataset.release) {
            openRelease(event.target.dataset.release);
        }
    });
    document.getElementById("lots-body").addEventListener("click", event => {
        if (event.target.dataset.lot) {
            openLot(event.target.dataset.lot);
        }
    });
    document.getElementById("alerts-body").addEventListener("click", event => {
        if (event.target.dataset.review) {
            openReview(parseInt(event.target.dataset.review, 10));
        }
    });
    document.getElementById("stock-body").addEventListener("click", event => {
        const id = event.target.dataset.history;
        if (id) {
            openProductHistory(parseInt(id, 10));
        }
    });
    document.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", () => {
        b.closest(".modal").classList.remove("open");
    }));
    // Primero el login. El login lo atiende la central (Nginx reenvía /identity/ al puerto 8008)
    startWithSession({
        loginUrl: "/identity/auth/login",
        changePasswordUrl: "/identity/auth/change-password",
        title: state.warehouseId ? "Bodega " + state.warehouseId.toUpperCase() : "Bodega",
        warehouseId: state.warehouseId,
        onEnter: startApp
    });
});
