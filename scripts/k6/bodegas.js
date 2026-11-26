// Prueba 2: bodegas con carga distinta (RNF-08, RNF-09, RNF-16)
// Al mismo tiempo:
//   - wh-002 (3 réplicas de la API detrás de su balanceador) recibe carga fuerte: sube de 10 a 120 consultas por segundo
//   - wh-001 recibe carga suave y constante: 10 consultas por segundo
// Pasa si wh-001 sigue respondiendo rápido (95 % en menos de 300 ms) aunque wh-002 esté cargada:
// cada bodega tiene sus propias máquinas, así que la carga de una no le llega a la otra.
import http from 'k6/http';
import { check } from 'k6';
import { WH1, WH2, login, auth, productIds, randomItem } from './comun.js';

export const options = {
    scenarios: {
        wh002_fuerte: {
            executor: 'ramping-arrival-rate',
            exec: 'cargaFuerte',
            startRate: 10,
            timeUnit: '1s',
            preAllocatedVUs: 50,
            maxVUs: 300,
            stages: [
                { duration: '1m30s', target: 120 },
                { duration: '1m30s', target: 120 },
                { duration: '30s', target: 0 }
            ]
        },
        wh001_suave: {
            executor: 'constant-arrival-rate',
            exec: 'cargaSuave',
            rate: 10,
            timeUnit: '1s',
            duration: '3m30s',
            preAllocatedVUs: 10,
            maxVUs: 50
        }
    },
    thresholds: {
        'http_req_duration{scenario:wh001_suave}': ['p(95)<300'],
        'http_req_failed{scenario:wh001_suave}': ['rate<0.01'],
        'http_req_duration{scenario:wh002_fuerte}': ['p(95)<1500'],
        'http_req_failed{scenario:wh002_fuerte}': ['rate<0.05']
    }
};

export function setup() {
    const token1 = login('juli.encargada1', 'Encargo2026');
    const token2 = login('pedro.encargado2', 'Encargo2026');
    return { token1: token1, token2: token2, ids1: productIds(WH1, token1), ids2: productIds(WH2, token2) };
}

export function cargaFuerte(data) {
    const response = http.get(WH2 + '/inventory/' + randomItem(data.ids2), auth(data.token2, { bodega: 'wh-002' }));
    check(response, { 'wh-002 responde 200': r => r.status === 200 });
}

export function cargaSuave(data) {
    const response = http.get(WH1 + '/inventory/' + randomItem(data.ids1), auth(data.token1, { bodega: 'wh-001' }));
    check(response, { 'wh-001 responde 200': r => r.status === 200 });
}
