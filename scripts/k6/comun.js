// Cosas que usan todas las pruebas de k6: direcciones, inicio de sesión y encabezados.
// Las direcciones llegan como variables de entorno desde correr.sh (las IPs fijas de las máquinas).
// Todo va por HTTPS, igual que un usuario de verdad: k6 confía en la CA del proyecto (SSL_CERT_FILE).
import http from 'k6/http';

export const CENTRAL = 'https://' + __ENV.CENTRAL;
export const WH1 = 'https://' + __ENV.WH1 + '/api';
export const WH2 = 'https://' + __ENV.WH2 + '/api';

// Inicia sesión en el servicio de identidad de la central y devuelve el token
export function login(username, password) {
    const response = http.post(CENTRAL + '/svc/identity/auth/login',
        JSON.stringify({ username: username, password: password }),
        { headers: { 'Content-Type': 'application/json' }, tags: { tipo: 'login' } });
    if (response.status !== 200) {
        throw new Error('No se pudo iniciar sesión como ' + username + ' (' + response.status + '): ' + response.body);
    }
    return response.json('access_token');
}

// Encabezados con el token y, si se quiere, etiquetas para separar las métricas
export function auth(token, tags) {
    return {
        headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
        tags: tags || {}
    };
}

// Productos de una bodega (para escoger al azar cuál consultar)
export function productIds(base, token) {
    const response = http.get(base + '/products', auth(token, { tipo: 'preparacion' }));
    const ids = response.status === 200 ? response.json().map(product => product.id) : [];
    if (ids.length === 0) {
        throw new Error('La bodega ' + base + ' no tiene productos. Corre primero las pruebas de scripts/pruebas/');
    }
    return ids;
}

export function randomItem(list) {
    return list[Math.floor(Math.random() * list.length)];
}
