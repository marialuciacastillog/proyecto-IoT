// Sesión del usuario. Este archivo es el MISMO en el front de bodega y en el de la central.
// - Muestra la pantalla de login si no hay sesión (o si se venció).
// - Guarda el token en sessionStorage: se borra al cerrar la pestaña.
// - authHeader() da el encabezado Authorization para cada llamada a la API.
// - Los elementos con data-roles="ROL1,ROL2" solo se ven para esos roles (applyRoles).
const SESSION_KEY = "iotSession";
const WAREHOUSE_ROLES = ["ADMIN_BODEGA", "ENCARGADO"];
const ROLE_LABELS = {
    ADMIN_GLOBAL: "Administrador Global",
    ADMIN_BODEGA: "Administrador por Bodega",
    ENCARGADO: "Encargado de recepción y envío",
    LIDER: "Líder"
};

// Opciones que da cada front al llamar startWithSession (urls, título, qué hacer al entrar)
const sessionOptions = {};


function getSession() {
    let session = null;
    try {
        session = JSON.parse(sessionStorage.getItem(SESSION_KEY));
    } catch (error) {
        session = null;
    }
    if (session && session.expiresAt <= Date.now()) {
        session = null;
    }
    return session;
}

function saveSession(login) {
    const session = {
        token: login.access_token,
        username: login.username,
        role: login.role,
        warehouseId: login.warehouse_id,
        expiresAt: Date.now() + login.expires_in_seconds * 1000
    };
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
    return session;
}

function clearSession() {
    sessionStorage.removeItem(SESSION_KEY);
}

function authHeader() {
    const session = getSession();
    return session ? { Authorization: "Bearer " + session.token } : {};
}

function hasRole(...roles) {
    const session = getSession();
    return session !== null && roles.includes(session.role);
}

// En el front de bodega, los roles de bodega solo pueden hacer cambios si el front es de SU bodega
function isOwnWarehouse(session, warehouseId) {
    return !WAREHOUSE_ROLES.includes(session.role) || !warehouseId ||
        String(session.warehouseId || "").toLowerCase() === String(warehouseId).toLowerCase();
}

// Muestra u oculta cada elemento con data-roles según el rol del usuario
function applyRoles() {
    const session = getSession();
    document.querySelectorAll("[data-roles]").forEach(element => {
        const roles = element.dataset.roles.split(",").map(r => r.trim());
        const allowed = session !== null && roles.includes(session.role) && isOwnWarehouse(session, sessionOptions.warehouseId);
        element.classList.toggle("role-hidden", !allowed);
    });
    // Si la vista abierta quedó oculta, se abre la primera que sí se pueda ver
    const active = document.querySelector("#nav button.active");
    if (active && active.classList.contains("role-hidden")) {
        const first = document.querySelector("#nav button:not(.role-hidden)");
        if (first) {
            first.click();
        }
    }
}


// ---------- Pantalla de login ----------

function buildLoginScreen() {
    const screen = document.createElement("div");
    screen.id = "login-screen";
    screen.className = "login-screen";
    screen.innerHTML =
        '<form class="login-box" id="login-form">' +
        "<h1>Warehouse Cloud Platform</h1>" +
        '<p class="sub" id="login-title"></p>' +
        '<div class="field mt"><label for="login-user">Usuario</label><input id="login-user" autocomplete="username" required></div>' +
        '<div class="field"><label for="login-password">Contraseña</label><input id="login-password" type="password" autocomplete="current-password" required></div>' +
        '<p class="login-error" id="login-error"></p>' +
        '<button class="btn btn-primary" type="submit">Iniciar sesión</button>' +
        "</form>";
    document.body.appendChild(screen);
    document.getElementById("login-form").addEventListener("submit", submitLogin);
    return screen;
}

function showLogin(message) {
    const screen = document.getElementById("login-screen") || buildLoginScreen();
    document.getElementById("login-title").textContent = sessionOptions.title || "";
    document.getElementById("login-error").textContent = message || "";
    document.getElementById("login-password").value = "";
    screen.classList.remove("hidden");
    document.getElementById("login-user").focus();
}

async function submitLogin(event) {
    event.preventDefault();
    const button = event.target.querySelector("button");
    const errorText = document.getElementById("login-error");
    button.disabled = true;
    errorText.textContent = "";
    try {
        const response = await fetch(sessionOptions.loginUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                username: document.getElementById("login-user").value.trim(),
                password: document.getElementById("login-password").value
            })
        });
        let data = null;
        try { data = await response.json(); } catch (error) { data = null; }
        if (response.ok) {
            saveSession(data);
            enterApp();
        } else {
            errorText.textContent = data && data.detail ? data.detail : "No se pudo iniciar sesión (" + response.status + ")";
        }
    } catch (error) {
        errorText.textContent = "No hay conexión con el servicio de identidad";
    }
    button.disabled = false;
}


// ---------- Usuario en la barra lateral ----------

function renderUserBox() {
    const session = getSession();
    let box = document.getElementById("user-box");
    if (!box) {
        box = document.createElement("div");
        box.id = "user-box";
        box.className = "user-box";
        document.querySelector(".sidebar-footer").appendChild(box);
    }
    box.innerHTML =
        "<strong>" + escapeText(session.username) + "</strong>" +
        "<div>" + escapeText(ROLE_LABELS[session.role] || session.role) +
        (session.warehouseId ? " · " + escapeText(session.warehouseId.toUpperCase()) : "") + "</div>" +
        '<div class="mt-small"><button class="link-light" id="change-password-btn">Cambiar contraseña</button>' +
        '<button class="link-light" id="logout-btn">Cerrar sesión</button></div>';
    document.getElementById("logout-btn").addEventListener("click", logout);
    document.getElementById("change-password-btn").addEventListener("click", openPasswordModal);
}

function escapeText(value) {
    const div = document.createElement("div");
    div.textContent = value === null || value === undefined ? "" : String(value);
    return div.innerHTML;
}

function logout() {
    clearSession();
    location.reload();
}

// La API respondió 401: el token se venció o ya no sirve
function handleUnauthorized() {
    clearSession();
    showLogin("Tu sesión se venció. Vuelve a iniciar sesión.");
}


// ---------- Cambiar mi contraseña ----------

function openPasswordModal() {
    let modal = document.getElementById("password-modal");
    if (!modal) {
        modal = document.createElement("div");
        modal.id = "password-modal";
        modal.className = "modal";
        modal.innerHTML =
            '<form class="modal-box small" id="password-form">' +
            '<div class="card-header"><h2>Cambiar contraseña</h2><button class="btn btn-light" type="button" data-close>Cerrar</button></div>' +
            '<div class="field"><label for="pw-current">Contraseña actual</label><input id="pw-current" type="password" required></div>' +
            '<div class="field"><label for="pw-new">Nueva contraseña</label><input id="pw-new" type="password" required minlength="8"></div>' +
            '<p class="sub">Mínimo 8 caracteres, con letras y números.</p>' +
            '<p class="login-error" id="pw-error"></p>' +
            '<button class="btn btn-primary" type="submit">Guardar</button></form>';
        document.body.appendChild(modal);
        modal.querySelector("[data-close]").addEventListener("click", () => modal.classList.remove("open"));
        document.getElementById("password-form").addEventListener("submit", submitPasswordChange);
    }
    document.getElementById("password-form").reset();
    document.getElementById("pw-error").textContent = "";
    modal.classList.add("open");
}

async function submitPasswordChange(event) {
    event.preventDefault();
    const errorText = document.getElementById("pw-error");
    const headers = Object.assign({ "Content-Type": "application/json" }, authHeader());
    try {
        const response = await fetch(sessionOptions.changePasswordUrl, {
            method: "POST",
            headers: headers,
            body: JSON.stringify({
                current_password: document.getElementById("pw-current").value,
                new_password: document.getElementById("pw-new").value
            })
        });
        let data = null;
        try { data = await response.json(); } catch (error) { data = null; }
        if (response.ok) {
            document.getElementById("password-modal").classList.remove("open");
            if (typeof toast === "function") {
                toast("Contraseña actualizada");
            }
        } else {
            errorText.textContent = data && data.detail ? data.detail : "Error " + response.status;
        }
    } catch (error) {
        errorText.textContent = "No hay conexión con el servicio de identidad";
    }
}


// ---------- Arranque ----------

function enterApp() {
    const session = getSession();
    const screen = document.getElementById("login-screen");
    if (screen) {
        screen.classList.add("hidden");
    }
    renderUserBox();
    applyRoles();
    // Cierra la sesión sola cuando el token se vence
    clearTimeout(sessionOptions.expiryTimer);
    sessionOptions.expiryTimer = setTimeout(handleUnauthorized, session.expiresAt - Date.now());
    sessionOptions.onEnter(session);
}

// Cada front la llama al cargar la página con: loginUrl, changePasswordUrl, title, warehouseId y onEnter
function startWithSession(options) {
    Object.assign(sessionOptions, options);
    if (getSession()) {
        enterApp();
    } else {
        showLogin("");
    }
}
