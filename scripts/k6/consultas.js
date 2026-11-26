// Prueba 1: consultas frecuentes (RNF-06)
// 50 usuarios consultan el inventario de wh-001 al mismo tiempo. Cada usuario hace una consulta por segundo.
// Sube de 0 a 50 usuarios en 1 minuto, se mantiene 2 minutos y baja en 30 segundos.
// Pasa si el 95 % de las consultas tarda menos de 500 ms y fallan menos del 1 %.
import http from 'k6/http';
import { check, sleep } from 'k6';
import { WH1, login, auth, productIds, randomItem } from './comun.js';

export const options = {
    scenarios: {
        consultas: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                { duration: '1m', target: 50 },
                { duration: '2m', target: 50 },
                { duration: '30s', target: 0 }
            ]
        }
    },
    thresholds: {
        'http_req_duration{tipo:inventario}': ['p(95)<500'],
        'http_req_failed{tipo:inventario}': ['rate<0.01'],
        checks: ['rate>0.99']
    }
};

export function setup() {
    const token = login('juli.encargada1', 'Encargo2026');
    return { token: token, ids: productIds(WH1, token) };
}

export default function (data) {
    const response = http.get(WH1 + '/inventory/' + randomItem(data.ids), auth(data.token, { tipo: 'inventario' }));
    check(response, { 'la consulta responde 200': r => r.status === 200 });
    sleep(1);
}
