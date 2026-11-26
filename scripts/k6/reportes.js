// Prueba 4: los reportes no bloquean las APIs (RNF-07)
// Durante 3 minutos el Líder consulta el inventario consolidado de la central (20 por segundo). Es la misma API
// y la misma base que usan los reportes, así que si los reportes la bloquearan, aquí se notaría.
// Entre el minuto 1 y el minuto 2, además pide un reporte pesado por segundo (historial de movimientos).
// Pedir un reporte solo lo deja en la cola: el worker lo arma aparte y lo sube a S3.
// Pasa si:
//   - pedir un reporte responde rápido (202 en menos de 500 ms)
//   - las consultas de inventario siguen igual de rápidas mientras se piden los reportes
import http from 'k6/http';
import { check } from 'k6';
import { Trend } from 'k6/metrics';
import { CENTRAL, login, auth, randomItem } from './comun.js';

const duranteReportes = new Trend('inventario_mientras_se_piden_reportes', true);
const sinReportes = new Trend('inventario_sin_reportes', true);

export const options = {
    scenarios: {
        consultas: {
            executor: 'constant-arrival-rate',
            exec: 'consultar',
            rate: 20,
            timeUnit: '1s',
            duration: '3m',
            preAllocatedVUs: 20,
            maxVUs: 100
        },
        reportes: {
            executor: 'constant-arrival-rate',
            exec: 'pedirReporte',
            rate: 1,
            timeUnit: '1s',
            startTime: '1m',
            duration: '1m',
            preAllocatedVUs: 5,
            maxVUs: 20
        }
    },
    thresholds: {
        'http_req_duration{tipo:pedir_reporte}': ['p(95)<500'],
        inventario_mientras_se_piden_reportes: ['p(95)<500'],
        inventario_sin_reportes: ['p(95)<500'],
        'checks{etapa:final}': ['rate==1']
    }
};

export function setup() {
    const leader = login('malu.lider', 'Lider2026x');
    // Solo los productos que ya tienen inventario en alguna bodega (los demás responden 404)
    const codes = http.get(CENTRAL + '/svc/catalog/products', auth(leader, { tipo: 'preparacion' })).json()
        .map(product => product.product_code).slice(0, 30)
        .filter(code => http.get(CENTRAL + '/svc/reports/global/inventory/' + code, auth(leader, { tipo: 'preparacion' })).status === 200);
    return { leader: leader, codes: codes.length > 0 ? codes : ['P-001'], start: Date.now() };
}

export function consultar(data) {
    const response = http.get(CENTRAL + '/svc/reports/global/inventory/' + randomItem(data.codes), auth(data.leader, { tipo: 'inventario' }));
    check(response, { 'la consulta responde 200': r => r.status === 200 });
    // Se guarda aparte según si en ese momento se estaban pidiendo reportes (minuto 1 a 2)
    const seconds = (Date.now() - data.start) / 1000;
    if (seconds >= 60 && seconds < 120) {
        duranteReportes.add(response.timings.duration);
    } else {
        sinReportes.add(response.timings.duration);
    }
}

export function pedirReporte(data) {
    const response = http.post(CENTRAL + '/svc/reports/reports',
        JSON.stringify({ report_type: 'movements_history', format: 'csv' }),
        auth(data.leader, { tipo: 'pedir_reporte' }));
    check(response, { 'el reporte queda en la cola (202)': r => r.status === 202 });
}

// Al final se mira que los reportes pedidos no hayan fallado (puede que algunos sigan en la cola)
export function teardown(data) {
    const list = http.get(CENTRAL + '/svc/reports/reports/jobs?limit=200', auth(data.leader, { tipo: 'revision' })).json('jobs') || [];
    const recent = list.filter(job => job.report_type === 'movements_history' && Date.parse(job.created_at) >= data.start);
    const count = { COMPLETED: 0, PENDING: 0, PROCESSING: 0, FAILED: 0 };
    recent.forEach(job => { count[job.status] = (count[job.status] || 0) + 1; });
    console.log('Reportes pedidos: ' + recent.length + ' | listos: ' + count.COMPLETED + ' | en cola o armándose: ' +
        (count.PENDING + count.PROCESSING) + ' | fallidos: ' + count.FAILED);
    check(count, { 'ningún reporte falló': c => c.FAILED === 0 }, { etapa: 'final' });
}
