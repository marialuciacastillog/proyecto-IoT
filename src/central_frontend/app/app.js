// Front de la central. Nginx reenvía cada prefijo a su API dentro de iot-central-network:
//   /svc/reports/   -> API de reportes e inventario global (iot-reports-api)
//   /svc/transfers/ -> API de transferencias (central_transfers_api)
//   /svc/iot/       -> ingesta IoT (iot_ingestion), solo para ver si está viva
//   /svc/identity/  -> identidad: login y usuarios (central_identity)
const SERVICES = {
    reports: "/svc/reports",
    transfers: "/svc/transfers",
    iot: "/svc/iot",
    identity: "/svc/identity",
    catalog: "/svc/catalog",
    notifications: "/svc/notifications"
};

// Quién puede usar cada parte (los mismos permisos que revisa el backend)
const REPORT_ROLES = ["ADMIN_GLOBAL", "LIDER"];

const FINAL_TRANSFER = ["COMPLETED", "FAILED", "RESERVATION_RELEASED"];
const FINAL_REPORT = ["COMPLETED", "FAILED"];

const state = {
    transfers: [],
    reportTypes: [],
    jobs: [],
    users: [],
    warehouses: [],
    catalog: [],
    editingWarehouse: null,
    editingProduct: null,
    requests: [],
    notifications: [],
    dlq: [],
    contracts: [],
    contractTypes: [],
    timer: null
};


// ---------- Utilidades ----------

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

function formatMoney(value) {
    return value === null || value === undefined ? "-" : "$" + Number(value).toLocaleString("es-CO", { maximumFractionDigits: 2 });
}

function toast(message, type) {
    const element = document.getElementById("toast");
    element.textContent = message;
    element.className = "toast show " + (type || "ok");
    clearTimeout(element.timer);
    element.timer = setTimeout(() => { element.className = "toast"; }, 4500);
}

// Cada llamada lleva el token (authHeader, de session.js). Si la API responde 401, se vuelve al login.
async function api(service, path, options) {
    const request = Object.assign({}, options);
    request.headers = Object.assign({}, request.headers, authHeader());
    const response = await fetch(SERVICES[service] + path, request);
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
    return data;
}

function postJson(body) {
    return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

function transferBadge(status) {
    let kind = "info";
    if (status === "COMPLETED") kind = "ok";
    if (status === "FAILED") kind = "error";
    if (status === "COMPENSATING" || status === "RESERVATION_RELEASED") kind = "warn";
    return '<span class="badge ' + kind + '">' + esc(status) + "</span>";
}

function reportBadge(status) {
    let kind = "info";
    if (status === "COMPLETED") kind = "ok";
    if (status === "FAILED") kind = "error";
    if (status === "PENDING") kind = "neutral";
    return '<span class="badge ' + kind + '">' + esc(status) + "</span>";
}


// ---------- Navegación ----------

function showView(name) {
    document.querySelectorAll(".view").forEach(v => v.classList.toggle("active", v.id === "view-" + name));
    document.querySelectorAll("#nav button").forEach(b => b.classList.toggle("active", b.dataset.view === name));
}


// ---------- Estado de los servicios ----------

async function checkService(service, elementId, label) {
    const element = document.getElementById(elementId);
    try {
        await api(service, "/health");
        element.textContent = label + ": en línea";
        element.className = "status-dot ok";
    } catch (error) {
        element.textContent = label + ": sin conexión";
        element.className = "status-dot error";
    }
}

function checkServices() {
    checkService("reports", "status-reports", "Reportes");
    checkService("transfers", "status-transfers", "Transferencias");
    checkService("iot", "status-iot", "Ingesta IoT");
    checkService("identity", "status-identity", "Identidad");
    checkService("catalog", "status-catalog", "Catálogo");
    checkService("notifications", "status-notifications", "Notificaciones");
}


// ---------- Transferencias ----------

async function loadTransfers() {
    try {
        state.transfers = await api("transfers", "/transfers");
    } catch (error) {
        state.transfers = [];
        document.getElementById("transfers-body").innerHTML =
            '<tr><td colspan="9" class="empty">No se pudo leer la API de transferencias</td></tr>';
        document.getElementById("summary-transfers").innerHTML =
            '<tr><td colspan="4" class="empty">No se pudo leer la API de transferencias</td></tr>';
        return;
    }
    renderTransfers();
}

function renderTransfers() {
    const list = state.transfers;
    const active = list.filter(t => !FINAL_TRANSFER.includes(t.status)).length;
    const completed = list.filter(t => t.status === "COMPLETED").length;
    const failed = list.length - active - completed;
    document.getElementById("stat-active").textContent = active;
    document.getElementById("stat-completed").textContent = completed;
    document.getElementById("stat-failed").textContent = failed;

    const body = document.getElementById("transfers-body");
    const summary = document.getElementById("summary-transfers");
    if (list.length === 0) {
        body.innerHTML = '<tr><td colspan="9" class="empty">Todavía no hay transferencias</td></tr>';
        summary.innerHTML = '<tr><td colspan="4" class="empty">Todavía no hay transferencias</td></tr>';
        return;
    }
    body.innerHTML = list.map(t =>
        '<tr><td><button class="link" data-transfer="' + esc(t.transfer_id) + '">' + esc(t.transfer_id) + "</button></td>" +
        "<td>" + esc(t.source_warehouse) + "</td><td>" + esc(t.destination_warehouse) + "</td><td>" + esc(t.product_id) +
        '</td><td class="num">' + formatNumber(t.quantity) + "</td><td>" + transferBadge(t.status) + "</td><td>" +
        esc(t.reason || "-") + "</td><td>" + esc(t.requested_by || "-") + "</td><td>" + formatDate(t.updated_at) + "</td></tr>"
    ).join("");
    summary.innerHTML = list.slice(0, 6).map(t =>
        '<tr><td><button class="link" data-transfer="' + esc(t.transfer_id) + '">' + esc(t.transfer_id) + "</button></td>" +
        "<td>" + esc(t.source_warehouse) + " → " + esc(t.destination_warehouse) + "</td><td>" + esc(t.product_id) +
        " × " + formatNumber(t.quantity) + "</td><td>" + transferBadge(t.status) + "</td></tr>"
    ).join("");
}

async function openTransfer(transferId) {
    const modalBody = document.getElementById("transfer-modal-body");
    document.getElementById("transfer-modal-title").textContent = "Transferencia " + transferId;
    modalBody.innerHTML = '<p class="sub">Cargando...</p>';
    document.getElementById("transfer-modal").classList.add("open");
    try {
        const t = await api("transfers", "/transfers/" + encodeURIComponent(transferId));
        const steps = (t.history || []).map(h =>
            "<li>" + transferBadge(h.status) + '<span class="sub">' + formatDate(h.created_at) +
            (h.reason ? " · " + esc(h.reason) : "") + "</span></li>"
        ).join("");
        modalBody.innerHTML =
            "<p>" + esc(t.source_warehouse) + " → " + esc(t.destination_warehouse) + " · " + esc(t.product_id) +
            " × " + formatNumber(t.quantity) + " · estado actual " + transferBadge(t.status) + "</p>" +
            '<p class="sub">Pedida por ' + esc(t.requested_by || "-") + (t.justification ? " · Motivo: " + esc(t.justification) : "") + "</p>" +
            '<p class="sub">El transfer_id también es el correlationId de los movimientos en las bodegas y en audit_log.</p>' +
            '<h2 class="mt">Historial</h2><ul class="timeline">' + (steps || "<li>Sin historial</li>") + "</ul>";
    } catch (error) {
        modalBody.innerHTML = '<p class="sub">' + esc(error.message) + "</p>";
    }
}

async function createTransfer(event) {
    event.preventDefault();
    const form = event.target;
    const payload = {
        source_warehouse: document.getElementById("t-source").value,
        destination_warehouse: document.getElementById("t-destination").value,
        product_id: document.getElementById("t-product").value.trim(),
        quantity: parseInt(document.getElementById("t-quantity").value, 10),
        justification: document.getElementById("t-justification").value.trim() || null
    };
    if (payload.source_warehouse === payload.destination_warehouse) {
        toast("El origen y el destino deben ser distintos", "error");
        return;
    }
    form.querySelector("button").disabled = true;
    try {
        const result = await api("transfers", "/transfers", postJson(payload));
        toast("Transferencia " + result.transfer_id + " solicitada");
        document.getElementById("t-quantity").value = "";
        document.getElementById("t-justification").value = "";
        await loadTransfers();
        schedulePolling();
    } catch (error) {
        toast(error.message, "error");
    }
    form.querySelector("button").disabled = false;
}


// ---------- Inventario global ----------

async function queryGlobal(event) {
    event.preventDefault();
    const productId = document.getElementById("global-product").value.trim();
    const result = document.getElementById("global-result");
    result.innerHTML = '<p class="sub">Consultando...</p>';
    try {
        const data = await api("reports", "/global/inventory/" + encodeURIComponent(productId));
        const rows = data.warehouses.map(w =>
            "<tr><td>" + esc(w.warehouse_id) + '</td><td class="num">' + formatNumber(w.quantity) + "</td><td>" +
            formatDate(w.updated_at) + "</td></tr>"
        ).join("");
        result.innerHTML =
            '<div class="grid grid-4"><div class="stat"><div class="label">Total de ' + esc(data.product_id) +
            '</div><div class="value">' + formatNumber(data.total_quantity) + '</div></div><div class="stat"><div class="label">Bodegas con el producto</div><div class="value">' +
            data.warehouses.length + "</div></div></div>" +
            '<div class="table-wrap mt"><table><thead><tr><th>Bodega</th><th class="num">Cantidad</th><th>Último dato recibido</th></tr></thead><tbody>' +
            rows + "</tbody></table></div>" +
            '<p class="sub mt">Consistencia eventual: si una bodega está caída se muestra su último dato conocido.</p>';
    } catch (error) {
        result.innerHTML = '<p class="sub">' + esc(error.message) + "</p>";
    }
}


// ---------- Reportes ----------

// Nombres en español de los reportes (el nombre interno sigue siendo el de reports/worker/queries.py)
const REPORT_LABELS = {
    global_inventory: "Inventario global", inventory_by_warehouse: "Inventario por bodega", low_stock: "Poco stock",
    transfers: "Transferencias", audit: "Auditoría de eventos", movements_history: "Historial de movimientos",
    sales_purchases: "Compras y ventas", sales_by_product: "Compras y ventas por producto",
    warehouse_summary: "Consolidado por bodega", stock_alerts: "Alertas de las bodegas", contracts: "Contratos"
};

function reportLabel(type) {
    return REPORT_LABELS[type] || type;
}

async function loadReportTypes() {
    try {
        const data = await api("reports", "/reports/types");
        state.reportTypes = data.report_types;
        document.getElementById("r-type").innerHTML = data.report_types.map(r =>
            '<option value="' + esc(r.report_type) + '">' + esc(reportLabel(r.report_type)) + "</option>"
        ).join("");
        document.getElementById("r-format").innerHTML = data.formats.map(f =>
            '<option value="' + esc(f) + '">' + esc(f.toUpperCase()) + "</option>"
        ).join("");
        showReportDescription();
    } catch (error) {
        document.getElementById("r-description").textContent = "No se pudo leer la API de reportes.";
    }
}

// Solo se muestran los filtros que acepta el reporte elegido
function showReportDescription() {
    const selected = document.getElementById("r-type").value;
    const type = state.reportTypes.find(r => r.report_type === selected);
    document.getElementById("r-description").textContent = type ? type.description : "";
    const filters = type && type.filters ? type.filters : [];
    document.querySelectorAll("#r-filters [data-filter]").forEach(field => {
        field.classList.toggle("role-hidden", !filters.includes(field.dataset.filter));
    });
    document.getElementById("r-filters").classList.toggle("role-hidden", filters.length === 0);
}

function describeParams(params) {
    const parts = [];
    if (params && params.date_from) parts.push("desde " + params.date_from);
    if (params && params.date_to) parts.push("hasta " + params.date_to);
    if (params && params.warehouse_id) parts.push(params.warehouse_id);
    if (params && params.product_id) parts.push(params.product_id);
    return parts.join(" · ");
}

async function loadJobs() {
    try {
        const data = await api("reports", "/reports/jobs?limit=30");
        state.jobs = data.jobs;
    } catch (error) {
        state.jobs = [];
        document.getElementById("reports-body").innerHTML =
            '<tr><td colspan="6" class="empty">No se pudo leer la API de reportes</td></tr>';
        document.getElementById("summary-reports").innerHTML =
            '<tr><td colspan="4" class="empty">No se pudo leer la API de reportes</td></tr>';
        return;
    }
    renderJobs();
}

function renderJobs() {
    const list = state.jobs;
    document.getElementById("stat-reports").textContent = list.filter(j => j.status === "COMPLETED").length;
    const body = document.getElementById("reports-body");
    const summary = document.getElementById("summary-reports");
    if (list.length === 0) {
        body.innerHTML = '<tr><td colspan="6" class="empty">Todavía no se han pedido reportes</td></tr>';
        summary.innerHTML = '<tr><td colspan="4" class="empty">Todavía no se han pedido reportes</td></tr>';
        return;
    }
    body.innerHTML = list.map(j => {
        const action = j.status === "COMPLETED"
            ? '<button class="link" data-download="' + esc(j.job_id) + '">Descargar</button>'
            : "";
        const params = describeParams(j.params);
        return "<tr><td>" + esc(reportLabel(j.report_type)) + (params ? '<span class="sub">' + esc(params) + "</span>" : "") +
            "</td><td>" + esc(String(j.format).toUpperCase()) + "</td><td>" +
            reportBadge(j.status) + "</td><td>" + formatDate(j.created_at) + "</td><td>" + formatDate(j.updated_at) +
            "</td><td>" + action + "</td></tr>";
    }).join("");
    summary.innerHTML = list.slice(0, 6).map(j =>
        "<tr><td>" + esc(reportLabel(j.report_type)) + "</td><td>" + esc(String(j.format).toUpperCase()) + "</td><td>" +
        reportBadge(j.status) + "</td><td>" + formatDate(j.created_at) + "</td></tr>"
    ).join("");
}

async function createReport(event) {
    event.preventDefault();
    const form = event.target;
    const payload = {
        report_type: document.getElementById("r-type").value,
        format: document.getElementById("r-format").value
    };
    const type = state.reportTypes.find(r => r.report_type === payload.report_type);
    const filters = type && type.filters ? type.filters : [];
    const values = {
        date_from: document.getElementById("rf-from").value,
        date_to: document.getElementById("rf-to").value,
        warehouse_id: document.getElementById("rf-warehouse").value,
        product_id: document.getElementById("rf-product").value.trim()
    };
    filters.forEach(name => {
        if (values[name]) {
            payload[name] = values[name];
        }
    });
    form.querySelector("button").disabled = true;
    try {
        await api("reports", "/reports", postJson(payload));
        toast("Reporte pedido. Aparecerá como COMPLETED cuando el worker lo suba a S3");
        await loadJobs();
        schedulePolling();
    } catch (error) {
        toast(error.message, "error");
    }
    form.querySelector("button").disabled = false;
}

async function downloadReport(jobId) {
    try {
        const data = await api("reports", "/reports/jobs/" + encodeURIComponent(jobId) + "/download");
        window.open(data.download_url, "_blank");
    } catch (error) {
        toast(error.message, "error");
    }
}


// ---------- Actualización automática ----------

// Mientras haya transferencias o reportes sin terminar, se vuelve a preguntar cada 3 s
function schedulePolling() {
    clearTimeout(state.timer);
    const pendingTransfers = state.transfers.some(t => !FINAL_TRANSFER.includes(t.status));
    const pendingReports = state.jobs.some(j => !FINAL_REPORT.includes(j.status));
    if (pendingTransfers || pendingReports) {
        state.timer = setTimeout(async () => {
            const loads = [loadTransfers()];
            if (hasRole(...REPORT_ROLES)) {
                loads.push(loadJobs());
            }
            await Promise.all(loads);
            schedulePolling();
        }, 3000);
    }
}

async function refresh() {
    checkServices();
    const loads = [loadTransfers()];
    if (hasRole(...REPORT_ROLES)) {
        loads.push(loadJobs());
    }
    await Promise.all(loads);
    schedulePolling();
}


// ---------- Estadísticas (RF-18) ----------

const MOVEMENT_LABELS = {
    PURCHASE: "Compras", SALE: "Ventas", RECEIPT: "Entradas", SHIPMENT: "Salidas", RETURN: "Devoluciones",
    TRANSFER_IN: "Transferencias recibidas", RESERVATION: "Reservas", RELEASE: "Reservas liberadas",
    RESERVATION_CONFIRM: "Salidas por transferencia", TRANSFER_OUT: "Transferencias enviadas", SIN_TIPO: "Sin tipo (antes de los lotes)"
};
const ALERT_LABELS = {
    INCONGRUENCIA: "Incongruencias", RESERVA_VENCIDA: "Reservas vencidas", LOTE_VENCIDO: "Lotes vencidos",
    TRANSFERENCIA_SIN_CERRAR: "Transferencias sin cerrar"
};

async function loadStats() {
    const days = document.getElementById("st-days").value;
    const warehouse = document.getElementById("st-warehouse").value;
    try {
        const data = await api("reports", "/stats?days=" + days + (warehouse ? "&warehouse_id=" + encodeURIComponent(warehouse) : ""));
        renderStats(data);
    } catch (error) {
        document.getElementById("st-days-chart").innerHTML = '<p class="sub">' + esc(error.message) + "</p>";
    }
}

function emptyRow(columns, text) {
    return '<tr><td colspan="' + columns + '" class="empty">' + text + "</td></tr>";
}

function renderStats(data) {
    const t = data.totals;
    document.getElementById("st-sales").textContent = formatMoney(t.sales_amount);
    document.getElementById("st-sales-hint").textContent = formatNumber(t.sales_count) + " ventas · " + formatNumber(t.sales_units) + " unidades";
    document.getElementById("st-purchases").textContent = formatMoney(t.purchases_amount);
    document.getElementById("st-purchases-hint").textContent = formatNumber(t.purchases_count) + " compras · " + formatNumber(t.purchases_units) + " unidades";
    document.getElementById("st-balance").textContent = formatMoney(t.balance);
    document.getElementById("st-movements").textContent = formatNumber(t.movements);
    const units = data.inventory.reduce((sum, row) => sum + Number(row.units), 0);
    const low = data.inventory.reduce((sum, row) => sum + Number(row.low_stock), 0);
    document.getElementById("st-units").textContent = formatNumber(units);
    document.getElementById("st-low").textContent = formatNumber(low);
    const transfers = data.transfers.reduce((sum, row) => sum + Number(row.transfers), 0);
    document.getElementById("st-transfer-rate").textContent = data.transfer_success_rate === null ? "-" : data.transfer_success_rate + "%";
    document.getElementById("st-transfer-hint").textContent = formatNumber(transfers) + " transferencias en total";
    document.getElementById("st-alerts").textContent = formatNumber(data.alerts.reduce((sum, row) => sum + Number(row.alerts), 0));

    // Barras: cada día tiene una barra de ventas y otra de compras, en proporción al día más alto
    const chart = document.getElementById("st-days-chart");
    const top = Math.max(1, ...data.by_day.map(d => Math.max(Number(d.sales_amount), Number(d.purchases_amount))));
    chart.innerHTML = data.by_day.length === 0 ? '<p class="sub">No hay movimientos en estos días</p>' : data.by_day.map(d =>
        '<div class="bar-day" title="' + esc(d.day) + ": ventas " + formatMoney(d.sales_amount) + ", compras " + formatMoney(d.purchases_amount) + '">' +
        '<div class="bar-pair"><div class="bar sales" style="height:' + Math.round(Number(d.sales_amount) * 150 / top) + 'px"></div>' +
        '<div class="bar purchases" style="height:' + Math.round(Number(d.purchases_amount) * 150 / top) + 'px"></div></div>' +
        '<div class="bar-label">' + esc(String(d.day).slice(5)) + "</div></div>").join("");

    const inventory = {};
    data.inventory.forEach(row => { inventory[row.warehouse_id] = row.units; });
    const warehouses = new Set(data.by_warehouse.map(w => w.warehouse_id).concat(Object.keys(inventory)));
    document.getElementById("st-warehouses").innerHTML = warehouses.size === 0 ? emptyRow(5, "Sin datos") : Array.from(warehouses).sort().map(id => {
        const row = data.by_warehouse.find(w => w.warehouse_id === id) || { sales_amount: 0, purchases_amount: 0, movements: 0 };
        return "<tr><td>" + esc(id) + '</td><td class="num">' + formatNumber(inventory[id] || 0) + '</td><td class="num">' + formatMoney(row.sales_amount) +
            '</td><td class="num">' + formatMoney(row.purchases_amount) + '</td><td class="num">' + formatNumber(row.movements) + "</td></tr>";
    }).join("");
    document.getElementById("st-top").innerHTML = data.top_products.length === 0 ? emptyRow(3, "Todavía no hay ventas") : data.top_products.map(p =>
        "<tr><td><strong>" + esc(p.product_id) + '</strong><span class="sub">' + esc(p.name || "") + '</span></td><td class="num">' +
        formatNumber(p.units) + '</td><td class="num">' + formatMoney(p.amount) + "</td></tr>").join("");
    document.getElementById("st-types").innerHTML = data.movement_types.length === 0 ? emptyRow(3, "Sin movimientos") : data.movement_types.map(m =>
        "<tr><td>" + esc(MOVEMENT_LABELS[m.movement_type] || m.movement_type) + '</td><td class="num">' + formatNumber(m.movements) +
        '</td><td class="num">' + formatNumber(m.units) + "</td></tr>").join("");
    const rows = data.transfers.map(tr =>
        "<tr><td>" + transferBadge(tr.status) + '</td><td class="num">' + formatNumber(tr.transfers) + '</td><td class="num">' +
        formatNumber(tr.units) + " u · " + (tr.average_seconds === null ? "-" : tr.average_seconds + " s") + "</td></tr>").concat(
        data.alerts.map(a => '<tr><td><span class="badge warn">' + esc(ALERT_LABELS[a.alert_type] || a.alert_type) + '</span></td><td class="num">' +
            formatNumber(a.alerts) + '</td><td class="num">alertas</td></tr>'));
    document.getElementById("st-transfers").innerHTML = rows.length === 0 ? emptyRow(3, "Sin transferencias ni alertas") : rows.join("");
}


// ---------- Contratos (RF-19, RF-20) ----------

const VALIDITY = {
    VIGENTE: ["Vigente", "ok"], POR_VENCER: ["Por vencer", "warn"], VENCIDO: ["Vencido", "error"],
    FUTURO: ["Aún no empieza", "info"], ARCHIVADO: ["Archivado", "neutral"]
};

async function loadContractTypes() {
    try {
        state.contractTypes = await api("reports", "/contracts/types");
    } catch (error) {
        state.contractTypes = [];
    }
    const options = state.contractTypes.map(t => '<option value="' + esc(t.contract_type) + '">' + esc(t.label) + "</option>").join("");
    document.getElementById("ct-type").innerHTML = options;
    document.getElementById("ctf-type").innerHTML = '<option value="">Todos los tipos</option>' + options;
}

async function loadContracts() {
    const params = new URLSearchParams();
    const warehouse = document.getElementById("ctf-warehouse").value;
    const type = document.getElementById("ctf-type").value;
    const search = document.getElementById("ctf-search").value.trim();
    if (warehouse) params.set("warehouse_id", warehouse);
    if (type) params.set("contract_type", type);
    if (search) params.set("search", search);
    params.set("include_archived", document.getElementById("ctf-archived").checked);
    try {
        state.contracts = await api("reports", "/contracts?" + params.toString());
    } catch (error) {
        state.contracts = [];
        document.getElementById("contracts-body").innerHTML = emptyRow(8, esc(error.message));
        return;
    }
    renderContracts();
}

function renderContracts() {
    const body = document.getElementById("contracts-body");
    const isLeader = hasRole("LIDER");
    if (state.contracts.length === 0) {
        body.innerHTML = emptyRow(8, "No hay contratos");
        return;
    }
    body.innerHTML = state.contracts.map(c => {
        const validity = VALIDITY[c.validity] || [c.validity, "neutral"];
        const linked = [c.warehouse_id, c.product_code].filter(Boolean).map(esc).join(" · ") || "-";
        let actions = '<button class="link" data-contract-download="' + c.id + '">Descargar</button> · <button class="link" data-contract-verify="' + c.id + '">Verificar</button>';
        if (isLeader && c.status === "ACTIVE") {
            actions += ' · <button class="link" data-contract-archive="' + c.id + '">Archivar</button>';
        }
        const dates = esc(c.start_date) + " a " + (c.end_date ? esc(c.end_date) : "sin fecha de fin");
        return "<tr><td><strong>" + esc(c.label) + "</strong> " + esc(c.title) + '<span class="sub">' + esc(c.file_name) + " · " +
            formatNumber(Math.ceil(c.size_bytes / 1024)) + " KB</span></td><td>" + esc(c.type_label) + "</td><td>" + esc(c.counterparty) +
            "</td><td>" + linked + '</td><td><span class="badge ' + validity[1] + '">' + esc(validity[0]) + '</span><span class="sub">' + dates +
            (c.archive_reason ? " · " + esc(c.archive_reason) : "") + '</span></td><td class="num">' + formatMoney(c.value) + "</td><td>" +
            formatDate(c.uploaded_at) + '<span class="sub">' + esc(c.uploaded_by) + "</span></td><td>" + actions + "</td></tr>";
    }).join("");
}

// El archivo va como formulario (multipart): el navegador pone solo el Content-Type
async function uploadContract(event) {
    event.preventDefault();
    const form = event.target;
    const file = document.getElementById("ct-file").files[0];
    if (!file) {
        toast("Elige el archivo del contrato", "error");
        return;
    }
    if (file.size > 10 * 1024 * 1024) {
        toast("El archivo pesa más de 10 MB", "error");
        return;
    }
    const data = new FormData();
    data.append("file", file);
    [["title", "ct-title"], ["contract_type", "ct-type"], ["counterparty", "ct-counterparty"], ["start_date", "ct-start"],
     ["end_date", "ct-end"], ["warehouse_id", "ct-warehouse"], ["product_code", "ct-product"], ["value", "ct-value"], ["notes", "ct-notes"]].forEach(pair => {
        const value = document.getElementById(pair[1]).value.trim();
        if (value) {
            data.append(pair[0], value);
        }
    });
    form.querySelector("button[type=submit]").disabled = true;
    try {
        const contract = await api("reports", "/contracts", { method: "POST", body: data });
        toast("Contrato " + contract.label + " guardado en S3");
        form.reset();
        await loadContracts();
    } catch (error) {
        toast(error.message, "error");
    }
    form.querySelector("button[type=submit]").disabled = false;
}

async function contractAction(event) {
    const target = event.target.dataset;
    try {
        if (target.contractDownload) {
            const data = await api("reports", "/contracts/" + target.contractDownload + "/download");
            window.open(data.download_url, "_blank");
        } else if (target.contractVerify) {
            const data = await api("reports", "/contracts/" + target.contractVerify + "/verify");
            toast(data.intact ? data.contract + ": el archivo en S3 es el mismo que se cargó" : data.contract + ": ¡el archivo cambió!", data.intact ? "ok" : "error");
        } else if (target.contractArchive) {
            openArchive(parseInt(target.contractArchive, 10));
        }
    } catch (error) {
        toast(error.message, "error");
    }
}

function openArchive(contractId) {
    const c = state.contracts.find(item => item.id === contractId);
    document.getElementById("detail-title").textContent = "Archivar " + c.label;
    document.getElementById("detail-body").innerHTML = "<p>" + esc(c.title) + " · " + esc(c.counterparty) + "</p>" +
        '<p class="sub">El contrato no se borra: queda archivado con el motivo y se puede seguir consultando.</p>' +
        '<div class="field mt"><label for="ct-archive-reason">Motivo</label><textarea id="ct-archive-reason" placeholder="Por ejemplo: se cargó el archivo equivocado"></textarea></div>' +
        '<button class="btn btn-primary" id="ct-archive-confirm">Archivar</button>';
    document.getElementById("detail-modal").classList.add("open");
    document.getElementById("ct-archive-confirm").addEventListener("click", async () => {
        try {
            await api("reports", "/contracts/" + c.id + "/archive", postJson({ reason: document.getElementById("ct-archive-reason").value.trim() }));
            toast(c.label + " archivado");
            document.getElementById("detail-modal").classList.remove("open");
            await loadContracts();
        } catch (error) {
            toast(error.message, "error");
        }
    });
}


// ---------- Usuarios (solo Administrador Global) ----------

async function loadUsers() {
    try {
        state.users = await api("identity", "/users");
    } catch (error) {
        state.users = [];
        document.getElementById("users-body").innerHTML =
            '<tr><td colspan="8" class="empty">' + esc(error.message) + "</td></tr>";
        return;
    }
    renderUsers();
}

function renderUsers() {
    const body = document.getElementById("users-body");
    const me = getSession() || { username: "" };
    body.innerHTML = state.users.map(u => {
        const status = u.active ? '<span class="badge ok">Activo</span>' : '<span class="badge neutral">Desactivado</span>';
        let actions = "";
        if (u.username !== me.username) {
            actions = '<button class="link" data-user-toggle="' + u.id + '">' + (u.active ? "Desactivar" : "Activar") + "</button> " +
                '<button class="link" data-user-reset="' + u.id + '">Restablecer contraseña</button>';
        }
        return "<tr><td><strong>" + esc(u.username) + "</strong></td><td>" + esc(u.full_name) + "</td><td>" +
            esc(ROLE_LABELS[u.role] || u.role) + "</td><td>" + esc(u.warehouse_id || "-") + "</td><td>" + status +
            "</td><td>" + formatDate(u.last_login_at) + "</td><td>" + esc(u.created_by || "-") + "</td><td>" + actions + "</td></tr>";
    }).join("");
}

function toggleWarehouseField() {
    const role = document.getElementById("u-role").value;
    document.getElementById("u-warehouse-field").classList.toggle("role-hidden", !WAREHOUSE_ROLES.includes(role));
}

async function createUser(event) {
    event.preventDefault();
    const form = event.target;
    const role = document.getElementById("u-role").value;
    const payload = {
        username: document.getElementById("u-username").value.trim(),
        password: document.getElementById("u-password").value,
        full_name: document.getElementById("u-name").value.trim(),
        role: role,
        warehouse_id: WAREHOUSE_ROLES.includes(role) ? document.getElementById("u-warehouse").value : null
    };
    form.querySelector("button").disabled = true;
    try {
        await api("identity", "/users", postJson(payload));
        toast("Usuario " + payload.username + " creado. Entrégale su usuario y contraseña inicial");
        form.reset();
        toggleWarehouseField();
        await loadUsers();
    } catch (error) {
        toast(error.message, "error");
    }
    form.querySelector("button").disabled = false;
}

async function toggleUser(userId) {
    const user = state.users.find(u => u.id === userId);
    try {
        await api("identity", "/users/" + userId, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ active: !user.active })
        });
        toast("Usuario " + user.username + (user.active ? " desactivado" : " activado"));
        await loadUsers();
    } catch (error) {
        toast(error.message, "error");
    }
}

async function resetUserPassword(userId) {
    const user = state.users.find(u => u.id === userId);
    const newPassword = window.prompt("Nueva contraseña para " + user.username + " (mínimo 8 caracteres, con letras y números):");
    if (newPassword) {
        try {
            await api("identity", "/users/" + userId + "/reset-password", postJson({ new_password: newPassword }));
            toast("Contraseña de " + user.username + " restablecida");
        } catch (error) {
            toast(error.message, "error");
        }
    }
}


// ---------- Bodegas ----------

// Llena los selectores de bodega (transferencias y usuarios) con las bodegas ACTIVAS del registro
function fillWarehouseSelects() {
    const active = state.warehouses.filter(w => w.status === "ACTIVE");
    const options = active.map(w => '<option value="' + esc(w.warehouse_id) + '">' + esc(w.warehouse_id) + " · " + esc(w.name) + "</option>").join("");
    ["t-source", "t-destination", "u-warehouse"].forEach(id => {
        const select = document.getElementById(id);
        const current = select.value;
        select.innerHTML = options;
        if (current && active.some(w => w.warehouse_id === current)) {
            select.value = current;
        }
    });
    // Filtros por bodega (reportes, estadísticas y contratos): todas las bodegas, también las archivadas
    const all = state.warehouses.map(w => '<option value="' + esc(w.warehouse_id) + '">' + esc(w.warehouse_id) + " · " + esc(w.name) + "</option>").join("");
    [["rf-warehouse", "Todas las bodegas"], ["st-warehouse", "Todas las bodegas"], ["ctf-warehouse", "Todas las bodegas"], ["ct-warehouse", "Ninguna en particular"]].forEach(pair => {
        const select = document.getElementById(pair[0]);
        const current = select.value;
        select.innerHTML = '<option value="">' + pair[1] + "</option>" + all;
        select.value = current;
    });
    if (active.length > 1 && document.getElementById("t-destination").value === document.getElementById("t-source").value) {
        document.getElementById("t-destination").value = active.find(w => w.warehouse_id !== document.getElementById("t-source").value).warehouse_id;
    }
}

async function loadWarehouses() {
    const archived = document.getElementById("w-archived").checked;
    try {
        state.warehouses = await api("catalog", "/warehouses?include_archived=" + archived);
    } catch (error) {
        state.warehouses = [];
        document.getElementById("warehouses-body").innerHTML = '<tr><td colspan="8" class="empty">' + esc(error.message) + "</td></tr>";
        return;
    }
    fillWarehouseSelects();
    renderWarehouses();
}

function renderWarehouses() {
    const body = document.getElementById("warehouses-body");
    const isAdmin = hasRole("ADMIN_GLOBAL");
    if (state.warehouses.length === 0) {
        body.innerHTML = '<tr><td colspan="8" class="empty">No hay bodegas</td></tr>';
        return;
    }
    body.innerHTML = state.warehouses.map(w => {
        const status = w.status === "ACTIVE" ? '<span class="badge ok">Activa</span>' : '<span class="badge neutral">Archivada</span>';
        let actions = "";
        if (isAdmin && w.status === "ACTIVE") {
            actions = '<button class="link" data-wh-edit="' + esc(w.warehouse_id) + '">Editar</button> ' +
                '<button class="link" data-wh-delete="' + esc(w.warehouse_id) + '">Eliminar</button>';
        }
        if (isAdmin && w.status !== "ACTIVE") {
            actions = '<button class="link" data-wh-restore="' + esc(w.warehouse_id) + '">Reactivar</button>';
        }
        return "<tr><td><strong>" + esc(w.warehouse_id) + "</strong></td><td>" + esc(w.name) +
            (w.description ? '<span class="sub">' + esc(w.description) + "</span>" : "") + "</td><td>" + esc(w.nit) +
            "</td><td>" + esc(w.location) + "</td><td>" + esc(w.manager_name) +
            (w.manager_username ? '<span class="sub">' + esc(w.manager_username) + "</span>" : "") +
            '</td><td class="num">' + formatNumber(w.products) + "</td><td>" + status + "</td><td>" + actions + "</td></tr>";
    }).join("");
}

function resetWarehouseForm() {
    state.editingWarehouse = null;
    document.getElementById("warehouse-form").reset();
    document.getElementById("w-id").disabled = false;
    document.getElementById("w-form-title").textContent = "Nueva bodega";
    document.getElementById("w-submit").textContent = "Registrar bodega";
    document.getElementById("w-cancel").classList.add("role-hidden");
}

function editWarehouse(warehouseId) {
    const w = state.warehouses.find(item => item.warehouse_id === warehouseId);
    state.editingWarehouse = warehouseId;
    document.getElementById("w-id").value = w.warehouse_id;
    document.getElementById("w-id").disabled = true;
    document.getElementById("w-nit").value = w.nit;
    document.getElementById("w-name").value = w.name;
    document.getElementById("w-location").value = w.location;
    document.getElementById("w-manager").value = w.manager_name;
    document.getElementById("w-manager-user").value = w.manager_username || "";
    document.getElementById("w-description").value = w.description || "";
    document.getElementById("w-form-title").textContent = "Editar bodega " + warehouseId;
    document.getElementById("w-submit").textContent = "Guardar cambios";
    document.getElementById("w-cancel").classList.remove("role-hidden");
    document.getElementById("warehouse-form").scrollIntoView({ behavior: "smooth" });
}

async function saveWarehouse(event) {
    event.preventDefault();
    const form = event.target;
    const payload = {
        nit: document.getElementById("w-nit").value.trim(),
        name: document.getElementById("w-name").value.trim(),
        location: document.getElementById("w-location").value.trim(),
        manager_name: document.getElementById("w-manager").value.trim(),
        manager_username: document.getElementById("w-manager-user").value.trim() || null,
        description: document.getElementById("w-description").value.trim() || null
    };
    form.querySelector("button[type=submit]").disabled = true;
    try {
        if (state.editingWarehouse) {
            await api("catalog", "/warehouses/" + state.editingWarehouse, {
                method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
            });
            toast("Bodega " + state.editingWarehouse + " actualizada");
        } else {
            payload.warehouse_id = document.getElementById("w-id").value.trim().toLowerCase();
            await api("catalog", "/warehouses", postJson(payload));
            toast("Bodega " + payload.warehouse_id + " registrada");
        }
        resetWarehouseForm();
        await loadWarehouses();
    } catch (error) {
        toast(error.message, "error");
    }
    form.querySelector("button[type=submit]").disabled = false;
}

async function deleteWarehouse(warehouseId) {
    if (window.confirm("¿Eliminar la bodega " + warehouseId + "? Si tiene historia se archiva en vez de borrarse.")) {
        try {
            const result = await api("catalog", "/warehouses/" + warehouseId, { method: "DELETE" });
            toast(result.message);
            await loadWarehouses();
        } catch (error) {
            toast(error.message, "error");
        }
    }
}

async function restoreWarehouse(warehouseId) {
    try {
        await api("catalog", "/warehouses/" + warehouseId, {
            method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "ACTIVE" })
        });
        toast("Bodega " + warehouseId + " reactivada");
        await loadWarehouses();
    } catch (error) {
        toast(error.message, "error");
    }
}


// ---------- Catálogo ----------

async function loadCatalog() {
    const search = document.getElementById("c-search").value.trim();
    try {
        state.catalog = await api("catalog", "/products?search=" + encodeURIComponent(search));
    } catch (error) {
        state.catalog = [];
        document.getElementById("catalog-body").innerHTML = '<tr><td colspan="9" class="empty">' + esc(error.message) + "</td></tr>";
        return;
    }
    renderCatalog();
    // El selector de producto de los contratos usa el catálogo completo (sin la búsqueda)
    if (search === "") {
        const select = document.getElementById("ct-product");
        const current = select.value;
        select.innerHTML = '<option value="">Ninguno en particular</option>' + state.catalog.map(p =>
            '<option value="' + esc(p.product_code) + '">' + esc(p.product_code) + " · " + esc(p.name) + "</option>").join("");
        select.value = current;
    }
}

function renderCatalog() {
    const body = document.getElementById("catalog-body");
    // Si la sesión se cerró mientras cargaban los datos, se pinta sin acciones
    const session = getSession() || { role: "", warehouseId: "" };
    if (state.catalog.length === 0) {
        body.innerHTML = '<tr><td colspan="9" class="empty">No hay productos en el catálogo</td></tr>';
        return;
    }
    body.innerHTML = state.catalog.map(p => {
        let action = "";
        if (session.role === "ADMIN_GLOBAL") {
            action = '<button class="link" data-product-edit="' + esc(p.product_code) + '">Editar</button>';
        }
        if (session.role === "ADMIN_BODEGA") {
            action = p.warehouses.includes(session.warehouseId)
                ? '<span class="badge ok">En tu bodega</span>'
                : '<button class="link" data-product-assign="' + esc(p.product_code) + '">Agregar a mi bodega</button>';
        }
        const warehouses = p.warehouses.length ? p.warehouses.map(w => '<span class="badge info">' + esc(w) + "</span>").join(" ") : '<span class="sub">Ninguna</span>';
        return "<tr><td><strong>" + esc(p.product_code) + "</strong></td><td>" + esc(p.name) + '<span class="sub">' +
            esc(p.category) + " · " + esc(p.unit) + "</span></td><td>" + esc(p.supplier) + '</td><td class="num">' +
            formatMoney(p.cost) + '</td><td class="num">' + formatMoney(p.sale_price) + '</td><td class="num">' +
            formatMoney(p.wholesale_price) + '</td><td class="num">' + formatNumber(p.initial_quantity) + "</td><td>" +
            warehouses + "</td><td>" + action + "</td></tr>";
    }).join("");
}

function resetProductForm() {
    state.editingProduct = null;
    document.getElementById("catalog-form").reset();
    document.getElementById("c-unit").value = "UNIDADES";
    document.getElementById("c-initial").value = "0";
    document.getElementById("c-code").disabled = false;
    document.getElementById("c-form-title").textContent = "Nuevo producto";
    document.getElementById("c-submit").textContent = "Crear producto";
    document.getElementById("c-cancel").classList.add("role-hidden");
}

function editProduct(code) {
    const p = state.catalog.find(item => item.product_code === code);
    state.editingProduct = code;
    document.getElementById("c-code").value = p.product_code;
    document.getElementById("c-code").disabled = true;
    document.getElementById("c-name").value = p.name;
    document.getElementById("c-category").value = p.category;
    document.getElementById("c-supplier").value = p.supplier;
    document.getElementById("c-cost").value = p.cost;
    document.getElementById("c-price").value = p.sale_price;
    document.getElementById("c-wholesale").value = p.wholesale_price;
    document.getElementById("c-unit").value = p.unit;
    document.getElementById("c-initial").value = p.initial_quantity;
    document.getElementById("c-description").value = p.description || "";
    document.getElementById("c-form-title").textContent = "Editar " + code + " (el cambio llega a las bodegas que lo manejan)";
    document.getElementById("c-submit").textContent = "Guardar cambios";
    document.getElementById("c-cancel").classList.remove("role-hidden");
    document.getElementById("catalog-form").scrollIntoView({ behavior: "smooth" });
}

async function saveProduct(event) {
    event.preventDefault();
    const form = event.target;
    const payload = {
        name: document.getElementById("c-name").value.trim(),
        category: document.getElementById("c-category").value.trim(),
        supplier: document.getElementById("c-supplier").value.trim(),
        unit: document.getElementById("c-unit").value.trim(),
        cost: document.getElementById("c-cost").value,
        sale_price: document.getElementById("c-price").value,
        wholesale_price: document.getElementById("c-wholesale").value,
        initial_quantity: parseInt(document.getElementById("c-initial").value, 10),
        description: document.getElementById("c-description").value.trim() || null
    };
    form.querySelector("button[type=submit]").disabled = true;
    try {
        if (state.editingProduct) {
            await api("catalog", "/products/" + encodeURIComponent(state.editingProduct), {
                method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
            });
            toast("Producto " + state.editingProduct + " actualizado. Las bodegas que lo manejan reciben el cambio");
        } else {
            payload.product_code = document.getElementById("c-code").value.trim().toUpperCase();
            await api("catalog", "/products", postJson(payload));
            toast("Producto " + payload.product_code + " creado en el catálogo");
        }
        resetProductForm();
        await loadCatalog();
    } catch (error) {
        toast(error.message, "error");
    }
    form.querySelector("button[type=submit]").disabled = false;
}

// El Administrador por Bodega agrega un producto del catálogo a su bodega (RF-08)
async function assignProduct(code) {
    const session = getSession() || { warehouseId: "" };
    const product = state.catalog.find(item => item.product_code === code);
    const answer = window.prompt("¿Con cuántas unidades entra " + code + " a " + session.warehouseId + "?", String(product.initial_quantity));
    if (answer !== null) {
        const quantity = parseInt(answer, 10);
        if (isNaN(quantity) || quantity < 0) {
            toast("La cantidad debe ser un número igual o mayor que 0", "error");
        } else {
            try {
                const result = await api("catalog", "/warehouses/" + session.warehouseId + "/products",
                    postJson({ product_code: code, initial_quantity: quantity }));
                toast(result.message);
                await loadCatalog();
            } catch (error) {
                toast(error.message, "error");
            }
        }
    }
}


// ---------- Solicitudes de productos (RF-05, RF-06, RF-07) ----------

const FIELD_LABELS = {
    name: "Nombre", category: "Categoría", supplier: "Proveedor", unit: "Unidad", cost: "Costo",
    sale_price: "Precio de venta", wholesale_price: "Precio mayorista", initial_quantity: "Cantidad inicial",
    description: "Descripción"
};
const REQUEST_TYPES = { NEW_PRODUCT: "Producto nuevo", UPDATE_PRODUCT: "Cambio de producto" };
const MONEY_FIELDS = ["cost", "sale_price", "wholesale_price"];

function requestBadge(status) {
    const labels = { PENDING: ["Pendiente", "warn"], APPROVED: ["Aprobada", "ok"], REJECTED: ["Rechazada", "error"] };
    const label = labels[status] || [status, "neutral"];
    return '<span class="badge ' + label[1] + '">' + esc(label[0]) + "</span>";
}

function showFieldValue(field, value) {
    let text = value === null || value === undefined || value === "" ? "-" : String(value);
    if (MONEY_FIELDS.includes(field) && text !== "-") {
        text = formatMoney(value);
    }
    return esc(text);
}

async function loadRequests() {
    const status = document.getElementById("r-filter").value;
    try {
        state.requests = await api("catalog", "/requests" + (status ? "?status=" + status : ""));
    } catch (error) {
        state.requests = [];
        document.getElementById("requests-body").innerHTML = '<tr><td colspan="8" class="empty">' + esc(error.message) + "</td></tr>";
        return;
    }
    renderRequests();
}

function renderRequests() {
    const body = document.getElementById("requests-body");
    const isAdmin = hasRole("ADMIN_GLOBAL");
    if (state.requests.length === 0) {
        body.innerHTML = '<tr><td colspan="8" class="empty">No hay solicitudes</td></tr>';
        return;
    }
    body.innerHTML = state.requests.map(r => {
        const action = isAdmin && r.status === "PENDING" ? "Revisar" : "Ver";
        return "<tr><td><strong>" + esc(r.label) + "</strong></td><td>" + esc(REQUEST_TYPES[r.request_type]) + "</td><td>" +
            esc(r.product_code) + (r.data.name ? '<span class="sub">' + esc(r.data.name) + "</span>" : "") + "</td><td>" +
            esc(r.warehouse_id) + "</td><td>" + esc(r.requested_by) + "</td><td>" + requestBadge(r.status) + "</td><td>" +
            formatDate(r.created_at) + '</td><td><button class="link" data-request="' + r.id + '">' + action + "</button></td></tr>";
    }).join("");
}

// Llena el formulario con los datos actuales del producto (para pedir un cambio)
function fillRequestFromProduct() {
    const product = state.catalog.find(p => p.product_code === document.getElementById("q-product").value);
    if (product) {
        document.getElementById("q-name").value = product.name;
        document.getElementById("q-category").value = product.category;
        document.getElementById("q-supplier").value = product.supplier;
        document.getElementById("q-cost").value = product.cost;
        document.getElementById("q-price").value = product.sale_price;
        document.getElementById("q-wholesale").value = product.wholesale_price;
        document.getElementById("q-unit").value = product.unit;
        document.getElementById("q-description").value = product.description || "";
    }
}

function toggleRequestType() {
    const isUpdate = document.getElementById("q-type").value === "UPDATE_PRODUCT";
    document.getElementById("q-code-field").classList.toggle("role-hidden", isUpdate);
    document.getElementById("q-product-field").classList.toggle("role-hidden", !isUpdate);
    document.getElementById("q-initial").closest(".field").classList.toggle("role-hidden", isUpdate);
    document.getElementById("q-hint").textContent = isUpdate
        ? "Cambia solo lo que quieres corregir. Si se aprueba, el cambio llega a todas las bodegas que manejan el producto."
        : "Si se aprueba, el producto se crea en el catálogo y se agrega a tu bodega con la cantidad inicial.";
    if (isUpdate && state.catalog.length === 0) {
        document.getElementById("q-product").innerHTML = '<option value="">El catálogo todavía no tiene productos</option>';
    } else if (isUpdate) {
        document.getElementById("q-product").innerHTML = state.catalog.map(p =>
            '<option value="' + esc(p.product_code) + '">' + esc(p.product_code) + " · " + esc(p.name) + "</option>"
        ).join("");
        fillRequestFromProduct();
    } else {
        document.getElementById("request-form").reset();
        document.getElementById("q-type").value = "NEW_PRODUCT";
        document.getElementById("q-unit").value = "UNIDADES";
        document.getElementById("q-initial").value = "0";
    }
}

function readRequestForm() {
    return {
        name: document.getElementById("q-name").value.trim(),
        category: document.getElementById("q-category").value.trim(),
        supplier: document.getElementById("q-supplier").value.trim(),
        unit: document.getElementById("q-unit").value.trim(),
        cost: document.getElementById("q-cost").value,
        sale_price: document.getElementById("q-price").value,
        wholesale_price: document.getElementById("q-wholesale").value,
        description: document.getElementById("q-description").value.trim() || null
    };
}

async function createRequest(event) {
    event.preventDefault();
    const form = event.target;
    const type = document.getElementById("q-type").value;
    const values = readRequestForm();
    let code = document.getElementById("q-code").value.trim().toUpperCase();
    let data = values;
    let valid = true;
    if (type === "NEW_PRODUCT") {
        data.initial_quantity = parseInt(document.getElementById("q-initial").value, 10) || 0;
    } else {
        // En un cambio solo se manda lo que es distinto a lo que ya tiene el catálogo
        code = document.getElementById("q-product").value;
        const product = state.catalog.find(p => p.product_code === code);
        data = {};
        if (!product) {
            toast("Elige el producto que quieres cambiar (si la lista está vacía, espera a que cargue el catálogo)", "error");
            valid = false;
        }
        Object.keys(product ? values : {}).forEach(field => {
            const current = product[field] === null || product[field] === undefined ? "" : String(product[field]);
            const proposed = values[field] === null ? "" : String(values[field]);
            const changed = MONEY_FIELDS.includes(field) ? Number(current) !== Number(proposed) : current !== proposed;
            if (changed) {
                data[field] = values[field];
            }
        });
        if (product && Object.keys(data).length === 0) {
            toast("No cambiaste ningún dato del producto", "error");
            valid = false;
        }
    }
    if (valid) {
        form.querySelector("button[type=submit]").disabled = true;
        try {
            const result = await api("catalog", "/requests", postJson({
                request_type: type, product_code: code, data: data, reason: document.getElementById("q-reason").value.trim()
            }));
            toast("Solicitud " + result.label + " enviada al Administrador Global");
            document.getElementById("q-reason").value = "";
            toggleRequestType();
            await loadRequests();
        } catch (error) {
            toast(error.message, "error");
        }
        form.querySelector("button[type=submit]").disabled = false;
    }
}

function openRequest(requestId) {
    const r = state.requests.find(item => item.id === requestId);
    const product = state.catalog.find(p => p.product_code === r.product_code);
    let rows = "";
    if (r.request_type === "UPDATE_PRODUCT") {
        rows = Object.keys(r.data).map(field =>
            "<tr><td>" + esc(FIELD_LABELS[field] || field) + "</td><td>" + showFieldValue(field, product ? product[field] : "-") +
            "</td><td><strong>" + showFieldValue(field, r.data[field]) + "</strong></td></tr>").join("");
        rows = '<table class="changes"><thead><tr><th>Dato</th><th>Actual</th><th>Propuesto</th></tr></thead><tbody>' + rows + "</tbody></table>";
    } else {
        rows = Object.keys(r.data).map(field =>
            "<tr><td>" + esc(FIELD_LABELS[field] || field) + "</td><td>" + showFieldValue(field, r.data[field]) + "</td></tr>").join("");
        rows = '<table class="changes"><tbody>' + rows + "</tbody></table>";
    }
    let decision = "";
    if (r.status === "PENDING" && hasRole("ADMIN_GLOBAL")) {
        decision = '<div class="field mt"><label for="d-note">Nota para quien la pidió</label><textarea id="d-note" placeholder="Obligatoria si la rechazas"></textarea></div>' +
            '<div class="toolbar"><button class="btn btn-primary" data-decide="approve" data-id="' + r.id + '">Aprobar</button>' +
            '<button class="btn btn-light" data-decide="reject" data-id="' + r.id + '">Rechazar</button></div>';
    } else if (r.status !== "PENDING") {
        decision = '<p class="mt">' + requestBadge(r.status) + " por " + esc(r.decided_by) + " el " + formatDate(r.decided_at) +
            (r.decision_note ? " · " + esc(r.decision_note) : "") + "</p>";
    }
    // Productos del catálogo con el mismo nombre: puede ser el mismo producto pedido dos veces
    let similar = "";
    if (r.similar && r.similar.length > 0) {
        similar = '<div class="notice mt">Ojo: en el catálogo ya hay un producto con ese nombre: ' +
            r.similar.map(item => "<strong>" + esc(item) + "</strong>").join(", ") +
            ". Si es el mismo, recházala y pídele que lo agregue desde el catálogo. Si el proveedor también es el mismo, no se va a poder aprobar.</div>";
    }
    document.getElementById("detail-title").textContent = r.label + " · " + REQUEST_TYPES[r.request_type] + " " + r.product_code;
    document.getElementById("detail-body").innerHTML =
        '<p class="sub">Pedida por ' + esc(r.requested_by) + " (" + esc(r.warehouse_id) + ") el " + formatDate(r.created_at) + "</p>" +
        "<p><strong>Motivo:</strong> " + esc(r.reason) + "</p>" + similar + '<div class="table-wrap mt">' + rows + "</div>" + decision;
    document.getElementById("detail-modal").classList.add("open");
}

async function decideRequest(requestId, action) {
    const note = document.getElementById("d-note").value.trim();
    try {
        const result = await api("catalog", "/requests/" + requestId + "/" + action, postJson({ note: note }));
        toast(action === "approve" ? result.message : "Solicitud " + result.label + " rechazada");
        document.getElementById("detail-modal").classList.remove("open");
        await Promise.all([loadRequests(), loadCatalog()]);
    } catch (error) {
        toast(error.message, "error");
    }
}


// ---------- Notificaciones (RF-16) ----------

const CATEGORY_LABELS = { SOLICITUD: "Solicitud", TRANSFERENCIA: "Transferencia", DLQ: "DLQ", ALERTA: "Alerta" };

function levelBadge(notification) {
    const kinds = { INFO: "info", WARNING: "warn", ERROR: "error" };
    return '<span class="badge ' + (kinds[notification.level] || "neutral") + '">' +
        esc(CATEGORY_LABELS[notification.category] || notification.category) + "</span>";
}

function showUnread(count) {
    const badge = document.getElementById("unread-count");
    badge.textContent = count;
    badge.classList.toggle("role-hidden", !count);
}

async function loadNotifications() {
    const onlyUnread = document.getElementById("n-unread").checked;
    try {
        const data = await api("notifications", "/notifications?unread_only=" + onlyUnread);
        state.notifications = data.notifications;
        showUnread(data.unread);
    } catch (error) {
        state.notifications = [];
        document.getElementById("notifications-body").innerHTML = '<tr><td colspan="4" class="empty">' + esc(error.message) + "</td></tr>";
        return;
    }
    renderNotifications();
}

function renderNotifications() {
    const body = document.getElementById("notifications-body");
    if (state.notifications.length === 0) {
        body.innerHTML = '<tr><td colspan="4" class="empty">No hay notificaciones</td></tr>';
        return;
    }
    body.innerHTML = state.notifications.map(n =>
        '<tr class="' + (n.read ? "" : "unread") + '"><td>' + formatDate(n.created_at) + "</td><td>" + levelBadge(n) + "</td><td>" +
        esc(n.title) + '<span class="sub">' + esc(n.message) + "</span></td><td>" +
        (n.read ? '<span class="sub">Leída</span>' : '<button class="link" data-notification="' + n.id + '">Marcar leída</button>') + "</td></tr>"
    ).join("");
}

async function markNotification(notificationId) {
    try {
        await api("notifications", "/notifications/" + notificationId + "/read", { method: "POST" });
        await loadNotifications();
    } catch (error) {
        toast(error.message, "error");
    }
}

async function markAllNotifications() {
    try {
        const result = await api("notifications", "/notifications/read-all", { method: "POST" });
        toast(result.marked + " notificación(es) marcadas como leídas");
        await loadNotifications();
    } catch (error) {
        toast(error.message, "error");
    }
}

async function refreshUnread() {
    try {
        const data = await api("notifications", "/notifications/unread-count");
        showUnread(data.unread);
    } catch (error) {
        showUnread(0);
    }
}


// ---------- DLQ (solo Administrador Global) ----------

async function loadDlq() {
    try {
        state.dlq = await api("notifications", "/dlq");
        const actions = await api("notifications", "/dlq/actions");
        document.getElementById("dlq-actions-body").innerHTML = actions.length === 0
            ? '<tr><td colspan="5" class="empty">Todavía no se ha atendido ningún mensaje</td></tr>'
            : actions.map(a => "<tr><td>" + formatDate(a.created_at) + "</td><td>" + esc(a.queue_name) + "</td><td>" +
                (a.action === "REDRIVE" ? '<span class="badge ok">Reprocesado</span>' : '<span class="badge neutral">Descartado</span>') +
                "</td><td>" + esc(a.justification || "-") + "</td><td>" + esc(a.performed_by) + "</td></tr>").join("");
    } catch (error) {
        state.dlq = [];
        document.getElementById("dlq-body").innerHTML = '<tr><td colspan="4" class="empty">' + esc(error.message) + "</td></tr>";
        return;
    }
    document.getElementById("dlq-body").innerHTML = state.dlq.map(q =>
        "<tr><td><strong>" + esc(q.queue) + "</strong></td><td>" + esc(q.source_queue) + '</td><td class="num">' +
        (q.messages > 0 ? '<span class="badge error">' + q.messages + "</span>" : "0") + "</td><td>" +
        (q.messages > 0 ? '<button class="link" data-dlq="' + esc(q.queue) + '">Ver mensajes</button>' : "") + "</td></tr>"
    ).join("");
}

async function openDlq(queue) {
    document.getElementById("detail-title").textContent = "Mensajes en " + queue;
    document.getElementById("detail-body").innerHTML = '<p class="sub">Cargando...</p>';
    document.getElementById("detail-modal").classList.add("open");
    try {
        const data = await api("notifications", "/dlq/" + encodeURIComponent(queue) + "/messages");
        document.getElementById("detail-body").innerHTML = data.messages.length === 0
            ? '<p class="sub">La DLQ está vacía</p>'
            : data.messages.map(m =>
                '<div class="dlq-message"><p><strong>Motivo:</strong> ' + esc(m.reason) + '<span class="sub">Llegó el ' + formatDate(m.sent_at) +
                '</span></p><pre class="body">' + esc(m.body) + '</pre><div class="toolbar">' +
                '<button class="btn btn-primary" data-dlq-redrive="' + esc(m.message_id) + '" data-queue="' + esc(queue) + '">Reprocesar</button>' +
                '<button class="btn btn-light" data-dlq-discard="' + esc(m.message_id) + '" data-queue="' + esc(queue) + '">Descartar</button></div></div>'
            ).join("");
    } catch (error) {
        document.getElementById("detail-body").innerHTML = '<p class="sub">' + esc(error.message) + "</p>";
    }
}

async function actOnDlq(queue, messageId, action) {
    let justification = "";
    let proceed = true;
    if (action === "discard") {
        justification = window.prompt("¿Por qué se descarta este mensaje? (queda registrado)") || "";
        proceed = justification.trim().length > 0;
    } else {
        proceed = window.confirm("¿Devolver el mensaje a " + queue.replace(/-dlq$/, "") + "?");
    }
    if (proceed) {
        try {
            const result = await api("notifications", "/dlq/" + encodeURIComponent(queue) + "/messages/" +
                encodeURIComponent(messageId) + "/" + action, postJson({ justification: justification }));
            toast(result.message);
            await openDlq(queue);
            await loadDlq();
        } catch (error) {
            toast(error.message, "error");
        }
    }
}


// ---------- Entrada después del login ----------

async function startApp(session) {
    // Primero las bodegas del registro, porque llenan los selectores de bodega
    await loadWarehouses();
    loadCatalog();
    // El Administrador por Bodega solo puede pedir transferencias desde su bodega
    const source = document.getElementById("t-source");
    const note = document.getElementById("t-source-note");
    if (session.role === "ADMIN_BODEGA") {
        source.value = session.warehouseId;
        source.disabled = true;
        note.textContent = "Como Administrador por Bodega solo puedes enviar desde " + session.warehouseId + ".";
    } else {
        source.disabled = false;
        note.textContent = "";
    }
    if (hasRole(...REPORT_ROLES)) {
        loadReportTypes();
        loadStats();
        loadContractTypes();
        loadContracts();
    }
    if (hasRole("ADMIN_GLOBAL")) {
        toggleWarehouseField();
        loadUsers();
        loadDlq();
    }
    if (hasRole("ADMIN_GLOBAL", "ADMIN_BODEGA")) {
        loadRequests();
        loadNotifications();
        // El contador de notificaciones se revisa cada 30 segundos
        clearInterval(state.unreadTimer);
        state.unreadTimer = setInterval(refreshUnread, 30000);
    }
    refresh();
}


// ---------- Eventos ----------

document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("#nav button").forEach(b => b.addEventListener("click", () => showView(b.dataset.view)));
    document.querySelectorAll("[data-refresh]").forEach(b => b.addEventListener("click", refresh));
    document.getElementById("transfer-form").addEventListener("submit", createTransfer);
    document.getElementById("global-form").addEventListener("submit", queryGlobal);
    document.getElementById("report-form").addEventListener("submit", createReport);
    document.getElementById("r-type").addEventListener("change", showReportDescription);
    document.getElementById("st-refresh").addEventListener("click", loadStats);
    document.getElementById("st-days").addEventListener("change", loadStats);
    document.getElementById("st-warehouse").addEventListener("change", loadStats);
    document.getElementById("contract-form").addEventListener("submit", uploadContract);
    document.getElementById("ct-refresh").addEventListener("click", loadContracts);
    ["ctf-warehouse", "ctf-type", "ctf-archived"].forEach(id => document.getElementById(id).addEventListener("change", loadContracts));
    document.getElementById("ctf-search").addEventListener("input", loadContracts);
    document.getElementById("contracts-body").addEventListener("click", contractAction);
    document.addEventListener("click", event => {
        const transferId = event.target.dataset.transfer;
        const jobId = event.target.dataset.download;
        if (transferId) {
            openTransfer(transferId);
        }
        if (jobId) {
            downloadReport(jobId);
        }
        if (event.target.dataset.userToggle) {
            toggleUser(parseInt(event.target.dataset.userToggle, 10));
        }
        if (event.target.dataset.userReset) {
            resetUserPassword(parseInt(event.target.dataset.userReset, 10));
        }
        const data = event.target.dataset;
        if (data.whEdit) {
            editWarehouse(data.whEdit);
        }
        if (data.whDelete) {
            deleteWarehouse(data.whDelete);
        }
        if (data.whRestore) {
            restoreWarehouse(data.whRestore);
        }
        if (data.productEdit) {
            editProduct(data.productEdit);
        }
        if (data.productAssign) {
            assignProduct(data.productAssign);
        }
        if (data.request) {
            openRequest(parseInt(data.request, 10));
        }
        if (data.decide) {
            decideRequest(parseInt(data.id, 10), data.decide);
        }
        if (data.notification) {
            markNotification(parseInt(data.notification, 10));
        }
        if (data.dlq) {
            openDlq(data.dlq);
        }
        if (data.dlqRedrive) {
            actOnDlq(data.queue, data.dlqRedrive, "redrive");
        }
        if (data.dlqDiscard) {
            actOnDlq(data.queue, data.dlqDiscard, "discard");
        }
    });
    document.getElementById("warehouse-form").addEventListener("submit", saveWarehouse);
    document.getElementById("w-cancel").addEventListener("click", resetWarehouseForm);
    document.getElementById("w-archived").addEventListener("change", loadWarehouses);
    document.getElementById("warehouses-refresh").addEventListener("click", loadWarehouses);
    document.getElementById("catalog-form").addEventListener("submit", saveProduct);
    document.getElementById("c-cancel").addEventListener("click", resetProductForm);
    document.getElementById("c-search").addEventListener("input", loadCatalog);
    document.getElementById("catalog-refresh").addEventListener("click", loadCatalog);
    document.getElementById("request-form").addEventListener("submit", createRequest);
    document.getElementById("q-type").addEventListener("change", toggleRequestType);
    document.getElementById("q-product").addEventListener("change", fillRequestFromProduct);
    document.getElementById("r-filter").addEventListener("change", loadRequests);
    document.getElementById("requests-refresh").addEventListener("click", loadRequests);
    document.getElementById("n-unread").addEventListener("change", loadNotifications);
    document.getElementById("notifications-read-all").addEventListener("click", markAllNotifications);
    document.getElementById("dlq-refresh").addEventListener("click", loadDlq);
    document.getElementById("user-form").addEventListener("submit", createUser);
    document.getElementById("u-role").addEventListener("change", toggleWarehouseField);
    document.getElementById("users-refresh").addEventListener("click", loadUsers);
    document.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", () => {
        b.closest(".modal").classList.remove("open");
    }));
    // Primero el login
    startWithSession({
        loginUrl: SERVICES.identity + "/auth/login",
        changePasswordUrl: SERVICES.identity + "/auth/change-password",
        title: "Nodo central",
        warehouseId: null,
        onEnter: startApp
    });
});
