// Prueba 3: ventas al mismo tiempo (RNF-05)
// Antes de empezar se compran 300 unidades de P-002 en wh-001. Luego 50 usuarios venden 1 unidad de P-002
// sin parar durante 1 minuto y medio: son muchas más ventas que unidades, así que muchas tienen que ser rechazadas.
// Al final se revisa que:
//   - el stock nunca quedó negativo
//   - el stock final = stock inicial - ventas que sí se hicieron (ni una de más, ni una de menos)
//   - los lotes suman lo mismo que el stock
//   - ninguna venta dio error 500 (las rechazadas responden 400 "Stock insuficiente")
import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';
import { WH1, login, auth } from './comun.js';

const ventasHechas = new Counter('ventas_hechas');
const ventasSinStock = new Counter('ventas_rechazadas_sin_stock');

export const options = {
    scenarios: {
        ventas: { executor: 'constant-vus', vus: 50, duration: '1m30s' }
    },
    thresholds: {
        // 400 (sin stock) es una respuesta esperada; solo cuentan como fallo los errores 500 o de conexión
        'http_req_failed{tipo:venta}': ['rate<0.01'],
        'checks{etapa:final}': ['rate==1']
    }
};

http.setResponseCallback(http.expectedStatuses(200, 201, 400));

function stock(data) {
    return http.get(WH1 + '/inventory/' + data.id, auth(data.token, { tipo: 'revision' })).json();
}

export function setup() {
    const token = login('juli.encargada1', 'Encargo2026');
    const products = http.get(WH1 + '/products', auth(token, { tipo: 'preparacion' })).json();
    // Se usa P-002 (Tuerca) para no gastar el stock de P-001, que usan las otras pruebas
    const product = products.find(item => item.sku === 'P-002');
    if (!product) {
        throw new Error('wh-001 no tiene P-002. Corre primero las pruebas de scripts/pruebas/');
    }
    // Nombre de esta corrida: va en la referencia de cada venta para contarlas al final
    const prueba = 'K6-' + Date.now();
    const purchase = http.post(WH1 + '/inventory/movements', JSON.stringify({
        product_id: product.id, type: 'PURCHASE', quantity: 300, lot_code: 'L-' + prueba, reference: prueba
    }), auth(token, { tipo: 'preparacion' }));
    if (purchase.status !== 201) {
        throw new Error('No se pudo hacer la compra inicial: ' + purchase.status + ' ' + purchase.body);
    }
    const data = { token: token, id: product.id, sku: product.sku, prueba: prueba };
    data.before = stock(data).quantity;
    console.log('Producto ' + product.sku + ': hay ' + data.before + ' unidades disponibles al empezar');
    return data;
}

export default function (data) {
    const response = http.post(WH1 + '/inventory/movements', JSON.stringify({
        product_id: data.id, type: 'SALE', quantity: 1, reference: data.prueba
    }), auth(data.token, { tipo: 'venta' }));
    if (response.status === 201) {
        ventasHechas.add(1);
    } else if (response.status === 400) {
        ventasSinStock.add(1);
    }
    check(response, { 'la venta se hace (201) o se rechaza sin stock (400)': r => r.status === 201 || r.status === 400 });
}

export function teardown(data) {
    const after = stock(data);
    const sales = http.get(WH1 + '/movements?type=SALE&limit=2000', auth(data.token, { tipo: 'revision' })).json()
        .filter(movement => movement.reference === data.prueba).length;
    const lots = http.get(WH1 + '/lots?product_id=' + data.id, auth(data.token, { tipo: 'revision' })).json()
        .reduce((sum, lot) => sum + lot.quantity, 0);
    console.log('Ventas registradas: ' + sales + ' | disponible al final: ' + after.quantity +
        ' | reservado: ' + after.reserved_quantity + ' | suma de los lotes: ' + lots);
    check(after, {
        'el stock nunca quedó negativo': s => s.quantity >= 0,
        'stock final = stock inicial - ventas hechas': s => s.quantity === data.before - sales,
        'los lotes suman lo mismo que el stock': s => lots === s.quantity + s.reserved_quantity
    }, { etapa: 'final' });
}
