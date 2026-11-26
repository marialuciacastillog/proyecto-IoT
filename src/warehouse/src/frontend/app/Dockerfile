FROM nginx:1.25-alpine

# Copiar archivos estáticos directos al directorio web de Nginx
COPY ./index.html /usr/share/nginx/html/index.html
COPY ./styles.css /usr/share/nginx/html/styles.css
COPY ./app.js /usr/share/nginx/html/app.js
COPY ./session.js /usr/share/nginx/html/session.js

COPY ./default.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 80 443
