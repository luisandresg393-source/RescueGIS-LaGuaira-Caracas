# Cómo instalar y probar (≈10 minutos)

## Requisitos

- Docker + Docker Compose (recomendado), **o** PostgreSQL 17 + PostGIS 3.5 instalados localmente.
- Python 3.10+.

## 1. Clonar y configurar

```bash
git clone <URL-de-este-repo>
cd RescueGIS-LaGuaira-Caracas

cp .env.example .env
# Edita .env y define una contraseña real para PGPASSWORD

pip install -r requirements.txt
export $(cat .env | xargs)
```

## 2. Levantar PostGIS

**Opción A — Docker (recomendado):**

```bash
cd docker
docker compose up -d
cd ..
```

**Opción B — PostgreSQL local ya instalado:**

```bash
sudo -u postgres psql -c "ALTER USER postgres PASSWORD '<tu password de .env>';"
sudo -u postgres psql -c "CREATE DATABASE rescuegis;"
sudo -u postgres psql -d rescuegis -c "CREATE EXTENSION IF NOT EXISTS postgis;"
```

## 3. Crear el esquema

```bash
psql -h $PGHOST -U $PGUSER -d $PGDATABASE -f sql/01_schema.sql
psql -h $PGHOST -U $PGUSER -d $PGDATABASE -f sql/02_modelo_rescate.sql
```

## 4. Cargar datos de muestra (500 edificios de La Guaira, sin tocar Overpass)

```bash
cd scripts
python3 load_buildings.py la_guaira
```

Esto usa el archivo `data_samples/buildings_la_guaira_SAMPLE_500.json`, que
ya está incluido en el repositorio — no necesitas conexión a Overpass para
esta prueba rápida.

## 5. (Opcional) Descargar el inventario completo real

Esto descarga los ~96,634 edificios reales de La Guaira y Caracas desde la
API pública de Overpass. Puede tardar varios minutos dependiendo de la carga
del servidor público.

```bash
export RESCUEGIS_DATA_DIR=./data   # carpeta separada, ignorada por git

python3 download_overpass.py buildings la_guaira
python3 download_overpass.py buildings caracas
python3 download_overpass.py infra la_guaira
python3 download_overpass.py infra caracas
python3 download_overpass.py roads la_guaira
python3 download_overpass.py roads caracas

python3 load_buildings.py la_guaira
python3 load_buildings.py caracas
python3 load_infra.py la_guaira
python3 load_infra.py caracas
python3 load_vias.py la_guaira
python3 load_vias.py caracas

python3 precompute_distancias.py
```

## 6. Probar el flujo de un reporte

Sigue `docs/EJEMPLO_DE_REPORTE.md` paso a paso — son solo sentencias SQL que
puedes pegar directamente en `psql`.

## 7. Verificar visualmente

Abre `docs/mapa_verificacion_offline.html` en cualquier navegador (no necesita
servidor ni conexión a internet: dibuja los puntos en un `<canvas>`, sin
depender de tiles de mapa). Útil para una validación rápida de cobertura.

---

¿Problemas? Abre un issue siguiendo `CONTRIBUTING.md`, o revisa
`docs/ARQUITECTURA.md` para entender cómo encajan las piezas.
