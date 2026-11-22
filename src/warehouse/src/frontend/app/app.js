// La URL base apunta de forma relativa para aprovechar el proxy/mapeo de puertos en Docker Compose
const API_BASE = '/api';

document.addEventListener('DOMContentLoaded', () => {
    checkHealth();
    loadDashboard();

    document.getElementById('product-form').addEventListener('submit', createProduct);
    document.getElementById('movement-form').addEventListener('submit', executeMovement);
    document.getElementById('refresh-stock-btn').addEventListener('click', loadDashboard);
    document.querySelector('.close-modal').addEventListener('click', () => document.getElementById('history-modal').style.display = 'none');
});

async function checkHealth() {
    const badge = document.getElementById('health-status');
    try {
        const res = await fetch(`${API_BASE}/health`);
        if (res.ok) {
            badge.textContent = "ONLINE (Node Connected)";
            badge.className = "status-badge ok";
        } else { throw new Error(); }
    } catch {
        badge.textContent = "OFFLINE (API Disconnected)";
        badge.className = "status-badge error";
    }
}

async function loadDashboard() {
    const tbody = document.querySelector('#stock-table tbody');
    tbody.innerHTML = '<tr><td colspan="6">Cargando inventarios...</td></tr>';
    
    try {
        const prodRes = await fetch(`${API_BASE}/products`);
        const products = await prodRes.json();
        
        tbody.innerHTML = '';
        if (products.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6">No hay productos registrados.</td></tr>';
            return;
        }

        for (const product of products) {
            const stockRes = await fetch(`${API_BASE}/inventory/${product.id}`);
            const stock = await stockRes.json();
            
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${product.id}</td>
                <td><strong>${product.sku}</strong></td>
                <td>${product.name} <br/><small style="color:var(--text-muted)">${product.unit}</small></td>
                <td style="color:var(--accent-green); font-weight:bold;">${stock.quantity}</td>
                <td style="color:var(--text-muted);">${stock.reserved_quantity}</td>
                <td><span class="action-link" onclick="openHistory(${product.id}, '${product.name}')">Ver Historial</span></td>
            `;
            tbody.appendChild(tr);
        }
    } catch (err) {
        showToast("Error al cargar la información del inventario", "error");
    }
}

async function createProduct(e) {
    e.preventDefault();
    const payload = {
        sku: document.getElementById('sku').value,
        name: document.getElementById('name').value,
        category: document.getElementById('category').value || null,
        unit: document.getElementById('unit').value,
        description: document.getElementById('description').value || null
    };

    try {
        const res = await fetch(`${API_BASE}/products`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        
        if (!res.ok) throw new Error(data.detail || "Error en el servidor");
        
        showToast(`Producto ${data.sku} creado exitosamente`, "ok");
        document.getElementById('product-form').reset();
        loadDashboard();
    } catch (err) {
        showToast(err.message, "error");
    }
}

async function executeMovement(e) {
    e.preventDefault();
    const payload = {
        product_id: parseInt(document.getElementById('move-product-id').value),
        type: document.getElementById('move-type').value,
        quantity: parseInt(document.getElementById('move-quantity').value),
        reference: document.getElementById('move-reference').value || null
    };

    try {
        const res = await fetch(`${API_BASE}/inventory/movements`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (!res.ok) throw new Error(data.detail || "Error al procesar movimiento");

        showToast(`Movimiento ${data.type} ejecutado con éxito`, "ok");
        document.getElementById('movement-form').reset();
        loadDashboard();
    } catch (err) {
        showToast(err.message, "error");
    }
}

async function openHistory(productId, productName) {
    document.getElementById('modal-title').textContent = `Historial — ${productName}`;
    const tbody = document.getElementById('history-table-body');
    tbody.innerHTML = '<tr><td colspan="5">Cargando...</td></tr>';
    document.getElementById('history-modal').style.display = 'flex';

    try {
        const res = await fetch(`${API_BASE}/inventory/${productId}/movements`);
        const movements = await res.json();
        
        tbody.innerHTML = '';
        if(movements.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5">Sin movimientos previos.</td></tr>';
            return;
        }

        movements.forEach(m => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${m.id}</td>
                <td><span style="font-weight:bold; color:${m.type.includes('IN')||m.type==='RECEIPT'?'var(--accent-green)':'var(--accent-red)'}">${m.type}</span></td>
                <td>${m.quantity}</td>
                <td>${m.reference || '-'}</td>
                <td>${new Date(m.created_at).toLocaleString()}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch {
        tbody.innerHTML = '<tr><td colspan="5">Error al leer los movimientos.</td></tr>';
    }
}

function showToast(message, type) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.style.background = type === 'ok' ? '#15803d' : '#b91c1c';
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 4000);
}
