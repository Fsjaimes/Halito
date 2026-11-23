# Despliegue de Hálito

Todo corre con Docker Compose en un solo servidor: la base de datos, la API, la interfaz servida
por Caddy (que obtiene el certificado HTTPS solo) y dos tareas de ingesta que se lanzan a mano o
desde `cron`.

```
Internet ──► Caddy (web) ──► /        archivos de la interfaz
                         └─► /api/*   API (FastAPI) ──► PostgreSQL + TimescaleDB + PostGIS
```

## Qué servidor hace falta

- Linux con Docker y el plugin de Compose.
- 2 GB de RAM y unos 10 GB de disco bastan para los datos actuales.
- Puertos 80 y 443 abiertos.
- Un dominio o subdominio apuntando a la IP del servidor (registro `A`). Sin dominio funciona por
  HTTP en la IP, pero sin certificado.

## 1. Configurar

En el servidor, con una copia del repositorio:

```bash
cp .env.example .env
```

Y en `.env`:

```bash
HALITO_DB_PASSWORD=una-contraseña-larga-y-aleatoria
SITE_ADDRESS=halito.tudominio.com
HALITO_SOCRATA_APP_TOKEN=tu-token-de-datos.gov.co
```

Con `SITE_ADDRESS=:80` sirve por HTTP en cualquier dirección (útil para probar).

## 2. Levantar

```bash
docker compose -f docker-compose.prod.yml up -d --build --wait
```

La API aplica las migraciones al arrancar. Comprobación:

```bash
curl https://halito.tudominio.com/api/health
```

## 3. Cargar los datos

Hay dos caminos.

**a) Copiar los datos de tu equipo** (minutos). En tu equipo, con la base de desarrollo
encendida:

```bash
scripts/copy-data.sh dump
```

Sube `data/halito.dump` (unos 20 MB) al servidor, a la misma ruta, y allí:

```bash
scripts/copy-data.sh restore
```

La restauración reemplaza lo que haya en la base de producción.

**b) Descargarlo todo desde cero en el servidor** (horas, según el portal):

```bash
docker compose -f docker-compose.prod.yml run --rm ingest
docker compose -f docker-compose.prod.yml run --rm heat
```

## 4. Mantenerlo al día

El clima del IDEAM se publica con uno o dos días de retraso y Landsat pasa cada 8 días. Una
entrada de `crontab -e` en el servidor basta:

```cron
# Estaciones y episodios, cada día a las 5:30 a. m.
30 5 * * * cd /ruta/a/halito && docker compose -f docker-compose.prod.yml run --rm ingest >> ingest.log 2>&1
# Calor del suelo, los lunes
0 6 * * 1 cd /ruta/a/halito && docker compose -f docker-compose.prod.yml run --rm heat >> heat.log 2>&1
```

Las dos tareas son incrementales: solo piden lo nuevo y se pueden repetir sin duplicar datos. Si
el portal falla para una estación, la siguiente corrida la reintenta.

## 5. Copias de seguridad

```bash
docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U halito -d halito --format=custom > halito-$(date +%F).dump
```

Se restaura igual que en el paso 3a.

## Actualizar a una versión nueva

```bash
git pull
docker compose -f docker-compose.prod.yml up -d --build --wait
```

## Probarlo en tu equipo

El mismo despliegue corre en local con un proyecto aparte, sin tocar la base de desarrollo:

```bash
COMPOSE_PROJECT_NAME=halito-prod docker compose -f docker-compose.prod.yml up -d --build --wait
```

Con `HTTP_PORT=8080` y `HTTPS_PORT=8443` en el `.env` si los puertos 80 y 443 están ocupados.
