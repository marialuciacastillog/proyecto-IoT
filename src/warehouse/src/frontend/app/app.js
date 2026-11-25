// Front de una bodega. La misma imagen corre en wh-001, wh-002 y wh-003:
// Nginx manda /api/... al balanceador de ESTA bodega, así que cada front solo ve su propia bodega.
const API = "/api";
const LOW_STOCK = 10;  // mismo umbral que el reporte low_stock de la central

// config.js lo genera Nginx con la variable WAREHOUSE_ID del contenedor
const config = window.APP_CONFIG || {};

const state = {
    warehouseId: config.warehouseId && !config.warehouseId.includes("$") ? config.warehouseId : "",
    products: [],
    stock: {},       // id del producto -> { quantity, reserved_quantity }
    movements: []    // todos los movimientos de la bodega
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

// fetch con manejo de errores: devuelve el JSON o lanza un Error con el detalle de FastAPI
async function api(path, options) {
    const response = await fetch(API + path, options);
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

const movementLabels = {
    RECEIPT: ["Entrada", "ok"],
    RETURN: ["Devolución", "ok"],
    TRANSFER_IN: ["Transferencia entrante", "ok"],
    SHIPMENT: ["Salida", "error"],
    TRANSFER_OUT: ["Transferencia saliente", "error"],
    RESERVATION: ["Reserva", "warn"],
    RESERVATION_CONFIRM: ["Reserva confirmada", "warn"],
    RELEASE: ["Reserva liberada", "neutral"]
};

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

        // Existencias y movimientos de cada producto (la API los da por producto)
        const stockResults = await Promise.all(state.products.map(p => api("/inventory/" + p.id).catch(() => null)));
        const movementResults = await Promise.all(state.products.map(p => api("/inventory/" + p.id + "/movements").catch(() => null)));

        state.stock = {};
        state.products.forEach((p, i) => {
            const stock = stockResults[i] ? stockResults[i].data : null;
            state.stock[p.id] = stock || { quantity: 0, reserved_quantity: 0 };
            // Si Nginx no recibió WAREHOUSE_ID, se toma de las respuestas de la API
            if (!state.warehouseId && stock && stock.warehouse_id) {
                state.warehouseId = stock.warehouse_id;
            }
        });

        state.movements = [];
        movementResults.forEach(result => {
            if (result) {
                state.movements = state.movements.concat(result.data);
            }
        });
        state.movements.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
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
    loadCache();
}


// ---------- Pintar cada vista ----------

function renderAll() {
    renderSummary();
    renderProducts();
    renderStock();
    renderProductSelect();
    renderHistory();
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
        body.innerHTML = '<tr><td colspan="5" class="empty">No hay productos</td></tr>';
        return;
    }
    body.innerHTML = list.map(p =>
        "<tr><td>" + p.id + "</td><td><strong>" + esc(p.sku) + "</strong></td><td>" + esc(p.name) +
        (p.description ? '<span class="sub">' + esc(p.description) + "</span>" : "") +
        "</td><td>" + esc(p.category || "-") + "</td><td>" + esc(p.unit) + "</td></tr>"
    ).join("");
}

function renderStock() {
    const text = document.getElementById("stock-search").value.trim();
    const list = state.products.filter(p => matches(p, text));
    const body = document.getElementById("stock-body");
    if (list.length === 0) {
        body.innerHTML = '<tr><td colspan="5" class="empty">No hay productos</td></tr>';
        return;
    }
    body.innerHTML = list.map(p => {
        const stock = state.stock[p.id];
        const quantity = stock.quantity < LOW_STOCK
            ? '<span class="badge warn">' + formatNumber(stock.quantity) + "</span>"
            : formatNumber(stock.quantity);
        return "<tr><td><strong>" + esc(p.sku) + "</strong></td><td>" + esc(p.name) + '<span class="sub">' + esc(p.unit) +
            '</span></td><td class="num">' + quantity + '</td><td class="num">' + formatNumber(stock.reserved_quantity) +
            '</td><td><button class="link" data-history="' + p.id + '">Movimientos</button></td></tr>';
    }).join("");
}

function renderProductSelect() {
    const select = document.getElementById("m-product");
    const current = select.value;
    if (state.products.length === 0) {
        select.innerHTML = '<option value="">Primero crea un producto</option>';
        return;
    }
    select.innerHTML = state.products.map(p =>
        '<option value="' + p.id + '">' + esc(p.sku) + " · " + esc(p.name) + "</option>"
    ).join("");
    if (current) {
        select.value = current;
    }
}

const historyGroups = {
    IN: ["RECEIPT", "RETURN", "TRANSFER_IN"],
    OUT: ["SHIPMENT", "TRANSFER_OUT"],
    RES: ["RESERVATION", "RESERVATION_CONFIRM", "RELEASE"]
};

function renderHistory() {
    const group = document.getElementById("history-type").value;
    const text = document.getElementById("history-search").value.trim().toLowerCase();
    const list = state.movements.filter(m => {
        const product = productById(m.product_id);
        const sku = product ? product.sku : "";
        const okType = !group || historyGroups[group].includes(m.type);
        const okText = !text || (sku + " " + (m.reference || "")).toLowerCase().includes(text);
        return okType && okText;
    });
    const body = document.getElementById("history-body");
    if (list.length === 0) {
        body.innerHTML = '<tr><td colspan="5" class="empty">Sin movimientos</td></tr>';
        return;
    }
    body.innerHTML = list.slice(0, 500).map(m => {
        const product = productById(m.product_id);
        return "<tr><td>" + formatDate(m.created_at) + "</td><td>" + esc(product ? product.sku : m.product_id) +
            "</td><td>" + movementBadge(m.type) + '</td><td class="num">' + formatNumber(m.quantity) +
            "</td><td>" + esc(m.reference || "-") + "</td></tr>";
    }).join("");
}

function openProductHistory(productId) {
    const product = productById(productId);
    document.getElementById("product-modal-title").textContent = "Movimientos de " + (product ? product.sku + " · " + product.name : productId);
    const list = state.movements.filter(m => m.product_id === productId);
    document.getElementById("product-modal-body").innerHTML = list.length === 0
        ? '<tr><td colspan="4" class="empty">Sin movimientos</td></tr>'
        : list.map(m =>
            "<tr><td>" + formatDate(m.created_at) + "</td><td>" + movementBadge(m.type) + '</td><td class="num">' +
            formatNumber(m.quantity) + "</td><td>" + esc(m.reference || "-") + "</td></tr>"
        ).join("");
    document.getElementById("product-modal").classList.add("open");
}


// ---------- Formularios ----------

async function createProduct(event) {
    event.preventDefault();
    const form = event.target;
    const payload = {
        sku: document.getElementById("p-sku").value.trim(),
        name: document.getElementById("p-name").value.trim(),
        unit: document.getElementById("p-unit").value.trim(),
        category: document.getElementById("p-category").value.trim() || null,
        description: document.getElementById("p-description").value.trim() || null
    };
    form.querySelector("button").disabled = true;
    try {
        const result = await api("/products", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        toast("Producto " + result.data.sku + " creado con inventario en 0");
        form.reset();
        await loadAll();
    } catch (error) {
        toast(error.message, "error");
    }
    form.querySelector("button").disabled = false;
}

async function createMovement(event) {
    event.preventDefault();
    const form = event.target;
    const headers = { "Content-Type": "application/json" };
    const correlationId = document.getElementById("m-correlation").value.trim();
    if (correlationId) {
        headers["X-Correlation-ID"] = correlationId;
    }
    const payload = {
        product_id: parseInt(document.getElementById("m-product").value, 10),
        type: document.getElementById("m-type").value,
        quantity: parseInt(document.getElementById("m-quantity").value, 10),
        reference: document.getElementById("m-reference").value.trim() || null
    };
    form.querySelector("button").disabled = true;
    try {
        const result = await api("/inventory/movements", { method: "POST", headers: headers, body: JSON.stringify(payload) });
        const usedId = result.headers.get("X-Correlation-ID") || "-";
        toast("Movimiento registrado. Correlation ID: " + usedId);
        document.getElementById("m-quantity").value = "";
        document.getElementById("m-reference").value = "";
        document.getElementById("m-correlation").value = "";
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
    document.getElementById("product-form").addEventListener("submit", createProduct);
    document.getElementById("movement-form").addEventListener("submit", createMovement);
    document.getElementById("reset-cache-btn").addEventListener("click", resetCache);
    document.getElementById("product-search").addEventListener("input", renderProducts);
    document.getElementById("stock-search").addEventListener("input", renderStock);
    document.getElementById("history-type").addEventListener("change", renderHistory);
    document.getElementById("history-search").addEventListener("input", renderHistory);
    document.getElementById("stock-body").addEventListener("click", event => {
        const id = event.target.dataset.history;
        if (id) {
            openProductHistory(parseInt(id, 10));
        }
    });
    document.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", () => {
        b.closest(".modal").classList.remove("open");
    }));
    refresh();
});
