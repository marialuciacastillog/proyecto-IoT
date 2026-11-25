// Front de la central. Nginx reenvía cada prefijo a su API dentro de iot-central-network:
//   /svc/reports/   -> API de reportes e inventario global (iot-reports-api)
//   /svc/transfers/ -> API de transferencias (central_transfers_api)
//   /svc/iot/       -> ingesta IoT (iot_ingestion), solo para ver si está viva
//   /svc/identity/  -> identidad: login y usuarios (central_identity), Etapa A
const SERVICES = {
    reports: "/svc/reports",
    transfers: "/svc/transfers",
    iot: "/svc/iot",
    identity: "/svc/identity"
};

// Etapa A: quién puede usar cada parte (los mismos permisos que revisa el backend)
const REPORT_ROLES = ["ADMIN_GLOBAL", "LIDER"];

const FINAL_TRANSFER = ["COMPLETED", "FAILED", "RESERVATION_RELEASED"];
const FINAL_REPORT = ["COMPLETED", "FAILED"];

const state = {
    transfers: [],
    reportTypes: [],
    jobs: [],
    users: [],
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

function toast(message, type) {
    const element = document.getElementById("toast");
    element.textContent = message;
    element.className = "toast show " + (type || "ok");
    clearTimeout(element.timer);
    element.timer = setTimeout(() => { element.className = "toast"; }, 4500);
}

// Etapa A: cada llamada lleva el token (authHeader, de session.js). Si la API responde 401, se vuelve al login.
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

async function loadReportTypes() {
    try {
        const data = await api("reports", "/reports/types");
        state.reportTypes = data.report_types;
        document.getElementById("r-type").innerHTML = data.report_types.map(r =>
            '<option value="' + esc(r.report_type) + '">' + esc(r.report_type) + "</option>"
        ).join("");
        document.getElementById("r-format").innerHTML = data.formats.map(f =>
            '<option value="' + esc(f) + '">' + esc(f.toUpperCase()) + "</option>"
        ).join("");
        showReportDescription();
    } catch (error) {
        document.getElementById("r-description").textContent = "No se pudo leer la API de reportes.";
    }
}

function showReportDescription() {
    const selected = document.getElementById("r-type").value;
    const type = state.reportTypes.find(r => r.report_type === selected);
    document.getElementById("r-description").textContent = type ? type.description : "";
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
        return "<tr><td>" + esc(j.report_type) + "</td><td>" + esc(String(j.format).toUpperCase()) + "</td><td>" +
            reportBadge(j.status) + "</td><td>" + formatDate(j.created_at) + "</td><td>" + formatDate(j.updated_at) +
            "</td><td>" + action + "</td></tr>";
    }).join("");
    summary.innerHTML = list.slice(0, 6).map(j =>
        "<tr><td>" + esc(j.report_type) + "</td><td>" + esc(String(j.format).toUpperCase()) + "</td><td>" +
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


// ---------- Usuarios (Etapa A, solo Administrador Global) ----------

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
    const me = getSession();
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


// ---------- Etapa A: entrada después del login ----------

function startApp(session) {
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
    }
    if (hasRole("ADMIN_GLOBAL")) {
        toggleWarehouseField();
        loadUsers();
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
    });
    document.getElementById("user-form").addEventListener("submit", createUser);
    document.getElementById("u-role").addEventListener("change", toggleWarehouseField);
    document.getElementById("users-refresh").addEventListener("click", loadUsers);
    document.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", () => {
        b.closest(".modal").classList.remove("open");
    }));
    // Etapa A: primero el login
    startWithSession({
        loginUrl: SERVICES.identity + "/auth/login",
        changePasswordUrl: SERVICES.identity + "/auth/change-password",
        title: "Nodo central",
        warehouseId: null,
        onEnter: startApp
    });
});
