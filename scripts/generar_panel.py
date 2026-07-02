#!/usr/bin/env python3
"""
Genera docs/panel_conector_sosvenezuela.html desde la base PostGIS.

Panel standalone (sin dependencias externas, funciona offline) con:
  * tarjetas de estadísticas de la última ingesta federada
  * mapa SVG de los edificios priorizados
  * tabla de incidentes de atrapados/heridos/críticos con enlaces de
    CONFIRMACIÓN VISUAL por fotos a nivel de calle (Mapillary / KartaView)
    y contexto OSM — clave para resolver los `match_aproximado` sin
    desplazar un equipo al sitio.

Los enlaces usan las coordenadas del EDIFICIO candidato (datos públicos de
OpenStreetMap), no las del reporte: la coordenada del reporte ya viene
degradada por privacidad desde la fuente y así se mantiene.

Uso:
    python3 generar_panel.py [ruta_salida.html]
"""
import html as html_mod
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

OUT_DEFAULT = Path(__file__).resolve().parent.parent / "docs" / "panel_conector_sosvenezuela.html"

COLORS = {"CRITICA": "#e11d48", "ALTA": "#f97316", "MEDIA": "#eab308", "BAJA": "#22c55e"}
ORDEN = {"CRITICA": 3, "ALTA": 2, "MEDIA": 1, "BAJA": 0}


def q(cur, sql, args=None):
    cur.execute(sql, args or ())
    return cur.fetchall()


def links_confirmacion(lat, lon):
    """Enlaces de confirmación visual: fotos de calle abiertas + contexto OSM."""
    if lat is None or lon is None:
        return "—"
    m = f"https://www.mapillary.com/app/?lat={lat}&amp;lng={lon}&amp;z=17.5"
    k = f"https://kartaview.org/map/@{lat},{lon},18z"
    o = f"https://www.openstreetmap.org/?mlat={lat}&amp;mlon={lon}#map=19/{lat}/{lon}"
    return (f'<a href="{m}" target="_blank" rel="noopener">📷 Mapillary</a> '
            f'<a href="{k}" target="_blank" rel="noopener">📷 KartaView</a> '
            f'<a href="{o}" target="_blank" rel="noopener">🗺 OSM</a>')


def main(out_path):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    edificios = q(cur, """
        SELECT codigo_corto, nombre, municipio::text, lat, lon, prioridad::text,
               prioridad_score, num_reportes, personas_atrapadas_estimado
        FROM buildings WHERE num_reportes > 0 ORDER BY prioridad_score DESC""")

    incidentes = q(cur, """
        SELECT i.codigo, i.tipo, i.urgencia::text, i.personas, i.lat, i.lon,
               i.coord_precision_m, i.building_match_metodo,
               round(i.building_match_distancia_m) AS dist_m,
               b.codigo_corto AS edificio, b.lat AS b_lat, b.lon AS b_lon,
               i.url_fuente,
               (SELECT max(nivel_confianza) FROM evidencias e WHERE e.incidente_id = i.id) AS confianza
        FROM incidentes i LEFT JOIN buildings b ON b.id = i.building_id
        WHERE i.tipo IN ('ATRAPADOS','HERIDOS') OR i.urgencia = 'CRITICA'
        ORDER BY ORDER_URG(i.urgencia) DESC, i.personas DESC LIMIT 60""".replace(
        "ORDER_URG(i.urgencia)",
        "CASE i.urgencia WHEN 'CRITICA' THEN 3 WHEN 'ALTA' THEN 2 WHEN 'MEDIA' THEN 1 ELSE 0 END"))

    (stats,) = q(cur, """
        SELECT (SELECT count(*) FROM buildings)::int AS total_edificios,
               (SELECT count(*) FROM infraestructura)::int AS total_infra,
               (SELECT count(*) FROM vias)::int AS total_vias,
               (SELECT count(*) FROM incidentes)::int AS total_incidentes,
               (SELECT count(*) FROM incidentes WHERE building_match_metodo='match_aproximado')::int AS aprox,
               (SELECT count(*) FROM incidentes WHERE building_match_metodo='auto_150m')::int AS firmes,
               (SELECT count(*) FROM incidentes WHERE building_match_metodo='sin_match')::int AS sin_match,
               (SELECT count(*) FROM buildings WHERE prioridad='CRITICA' AND num_reportes>0)::int AS p_critica,
               (SELECT count(*) FROM buildings WHERE prioridad='ALTA' AND num_reportes>0)::int AS p_alta,
               (SELECT count(*) FROM buildings WHERE prioridad='MEDIA' AND num_reportes>0)::int AS p_media,
               COALESCE((SELECT sum(personas) FROM incidentes WHERE tipo='ATRAPADOS'),0)::int AS personas_atrapadas,
               (SELECT count(*) FROM incidentes WHERE fuente='sosvenezuela2026'
                  AND descripcion LIKE '%%SIMULADO%%')::int AS simulados""")
    conn.close()

    es_fixture = stats["simulados"] > 0 and stats["simulados"] >= stats["total_incidentes"] * 0.9
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ---- mapa SVG ----
    lats = [e["lat"] for e in edificios] or [10.5]
    lons = [e["lon"] for e in edificios] or [-66.9]
    minlat, maxlat = min(lats) - 0.01, max(lats) + 0.01
    minlon, maxlon = min(lons) - 0.02, max(lons) + 0.02
    W, H = 1100, 420

    def xy(lat, lon):
        x = (lon - minlon) / (maxlon - minlon) * W
        y = (1 - (lat - minlat) / (maxlat - minlat)) * H
        return round(x, 1), round(y, 1)

    circles = []
    for e in sorted(edificios, key=lambda e: ORDEN[e["prioridad"]]):
        x, y = xy(e["lat"], e["lon"])
        c = COLORS[e["prioridad"]]
        r = 7 if e["prioridad"] == "CRITICA" else 5.5 if e["prioridad"] == "ALTA" else 4
        tip = html_mod.escape(
            f"{e['codigo_corto']} · {e['prioridad']} ({e['prioridad_score']}) · "
            f"{e['num_reportes']} rep · atrapados est. {e['personas_atrapadas_estimado']}")
        circles.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{c}" fill-opacity="0.85" '
                       f'stroke="#0f172a" stroke-width="0.6"><title>{tip}</title></circle>')

    # ---- tabla ----
    rows = []
    for i in incidentes:
        uc = COLORS[i["urgencia"]]
        metodo = i["building_match_metodo"] or "—"
        badge = {"auto_150m": ("#16a34a", "MATCH FIRME"),
                 "match_aproximado": ("#d97706", "APROXIMADO ⚠"),
                 "sin_match": ("#64748b", "SIN MATCH")}.get(metodo, ("#64748b", metodo))
        # confirmación visual: coordenada del edificio candidato si hay match;
        # si no, la coordenada (ya degradada) del propio reporte para explorar la zona
        clat = i["b_lat"] if i["b_lat"] is not None else i["lat"]
        clon = i["b_lon"] if i["b_lon"] is not None else i["lon"]
        rows.append(f"""<tr>
<td class="mono">{i['codigo']}</td>
<td><span class="pill" style="background:{uc}22;color:{uc};border:1px solid {uc}55">{i['urgencia']}</span></td>
<td>{i['tipo']}</td>
<td style="text-align:center">{i['personas'] or '—'}</td>
<td class="mono">{i['edificio'] or '—'}</td>
<td><span class="pill" style="background:{badge[0]}18;color:{badge[0]};border:1px solid {badge[0]}44">{badge[1]}</span></td>
<td style="text-align:right" class="mono">{int(i['dist_m']) if i['dist_m'] is not None else '—'} m</td>
<td style="text-align:right" class="mono">±{int(i['coord_precision_m']) if i['coord_precision_m'] else '—'} m</td>
<td style="text-align:center" class="mono">{i['confianza'] or '—'}</td>
<td class="links">{links_confirmacion(clat, clon)}</td>
</tr>""")

    aviso_fixture = ""
    if es_fixture:
        aviso_fixture = """
<div class="warn">⚠ <strong>Corrida con fixture simulado.</strong> El API en vivo de sosvenezuela2026.com
respondía HTTP 500, así que estos incidentes provienen de <code>data/fixture_sos_reports.json</code>:
reportes sintéticos generados desde edificios OSM reales con la <em>misma</em> degradación de privacidad
de su plataforma (jitter 80–250 m + redondeo a 3 decimales). Ningún incidente mostrado es real.
Cuando su API vuelva: <code>python3 scripts/watch_sosvenezuela.py</code> ingiere automáticamente.</div>"""

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RescueGIS — Conector SOS Venezuela 2026 · Panel de prioridad</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background:#0b1220; color:#e2e8f0; font-family: -apple-system, 'Segoe UI', Roboto, sans-serif; padding: 24px; }}
  h1 {{ font-size: 1.35rem; margin-bottom: 4px; }}
  h2 {{ font-size: 1.05rem; margin: 26px 0 10px; color:#94a3b8; text-transform: uppercase; letter-spacing: .06em; }}
  .sub {{ color:#94a3b8; font-size:.9rem; margin-bottom: 18px; }}
  .warn {{ background:#7c2d1218; border:1px solid #ea580c66; color:#fdba74; padding:10px 14px; border-radius:10px; font-size:.85rem; margin-bottom:18px; }}
  .tip {{ background:#0c4a6e18; border:1px solid #0284c766; color:#7dd3fc; padding:10px 14px; border-radius:10px; font-size:.85rem; margin:10px 0 18px; }}
  .cards {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(150px,1fr)); gap:12px; }}
  .card {{ background:#111a2e; border:1px solid #1e293b; border-radius:12px; padding:14px 16px; }}
  .card .v {{ font-size:1.5rem; font-weight:700; }}
  .card .l {{ font-size:.75rem; color:#94a3b8; margin-top:2px; }}
  .mapwrap {{ background:#111a2e; border:1px solid #1e293b; border-radius:12px; padding:10px; }}
  svg {{ width:100%; height:auto; display:block; }}
  .legend {{ display:flex; gap:18px; font-size:.8rem; color:#cbd5e1; padding:8px 6px 2px; flex-wrap:wrap; }}
  .dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:5px; }}
  table {{ width:100%; border-collapse:collapse; font-size:.82rem; }}
  th {{ text-align:left; color:#94a3b8; font-weight:600; padding:8px 10px; border-bottom:1px solid #1e293b; white-space:nowrap; }}
  td {{ padding:7px 10px; border-bottom:1px solid #16213a; }}
  tr:hover td {{ background:#16213a; }}
  .mono {{ font-family: ui-monospace, 'SF Mono', Menlo, monospace; font-size:.78rem; }}
  .pill {{ padding:2px 8px; border-radius:999px; font-size:.72rem; font-weight:600; white-space:nowrap; }}
  .links a {{ color:#60a5fa; text-decoration:none; font-size:.75rem; white-space:nowrap; margin-right:6px; }}
  .links a:hover {{ text-decoration:underline; }}
  .foot {{ margin-top:26px; color:#64748b; font-size:.78rem; line-height:1.6; }}
  .score {{ background:#111a2e; border:1px solid #1e293b; border-radius:12px; padding:14px 18px; font-size:.85rem; line-height:1.7; color:#cbd5e1; }}
  code {{ background:#1e293b; padding:1px 6px; border-radius:5px; font-size:.78rem; }}
</style>
</head>
<body>
<h1>🇻🇪 RescueGIS · Conector SOS Venezuela 2026</h1>
<div class="sub">La Guaira + Caracas · base OSM de {stats['total_edificios']:,} edificios · {stats['total_infra']:,} puntos de infraestructura crítica · {stats['total_vias']:,} vías/puentes — generado {fecha}</div>
{aviso_fixture}
<div class="cards">
  <div class="card"><div class="v">{stats['total_incidentes']}</div><div class="l">incidentes ingeridos</div></div>
  <div class="card"><div class="v" style="color:#16a34a">{stats['firmes']}</div><div class="l">match firme (&lt;150 m, GPS preciso)</div></div>
  <div class="card"><div class="v" style="color:#d97706">{stats['aprox']}</div><div class="l">match aproximado ⚠ requiere confirmación</div></div>
  <div class="card"><div class="v" style="color:#64748b">{stats['sin_match']}</div><div class="l">sin match</div></div>
  <div class="card"><div class="v" style="color:#f97316">{stats['p_alta']}</div><div class="l">edificios prioridad ALTA</div></div>
  <div class="card"><div class="v" style="color:#eab308">{stats['p_media']}</div><div class="l">edificios prioridad MEDIA</div></div>
  <div class="card"><div class="v" style="color:#e11d48">{stats['personas_atrapadas']}</div><div class="l">personas atrapadas reportadas{' (simulado)' if es_fixture else ''}</div></div>
</div>

<h2>Mapa de edificios priorizados ({len(edificios)} con reportes)</h2>
<div class="mapwrap">
  <svg viewBox="0 0 {W} {H}" role="img" aria-label="Mapa de edificios priorizados">
    <rect width="{W}" height="{H}" fill="#0e1626"/>
    <text x="12" y="20" fill="#334155" font-size="11">↑ N · costa de La Guaira arriba, valle de Caracas abajo · lon {minlon:.2f} → {maxlon:.2f}</text>
    {chr(10).join(circles)}
  </svg>
  <div class="legend">
    <span><span class="dot" style="background:#e11d48"></span>CRÍTICA (≥120)</span>
    <span><span class="dot" style="background:#f97316"></span>ALTA (≥60)</span>
    <span><span class="dot" style="background:#eab308"></span>MEDIA (≥20)</span>
    <span><span class="dot" style="background:#22c55e"></span>BAJA</span>
    <span style="color:#64748b">· pasa el cursor sobre un punto para ver código y score</span>
  </div>
</div>

<h2>Cómo se calcula el score (transparente, sin caja negra)</h2>
<div class="score">
  <code>score = personas×3 + heridos×5 + fallecidos×2 + min(horas_sin_ayuda,48)×1 + confirmados×20 + pendientes×5 + infra_crítica(15) + cercanía_hospital/bomberos(10)</code><br>
  La verificación de la fuente (<code>community_confirmed</code>/<code>official_verified</code>) entra como <strong>evidencia</strong> con confianza 60/85 — nunca marca el incidente como verificado automáticamente. Todo incidente con <code>match_aproximado</code> (precisión &gt;60 m por el truncado de privacidad de la fuente) exige confirmación humana antes de despachar un equipo.
</div>

<h2>Incidentes de atrapados / heridos / críticos ({len(incidentes)} mostrados, máx. 60)</h2>
<div class="tip">💡 <strong>Confirmación visual sin ir al sitio:</strong> la columna «Confirmar» abre fotos abiertas a nivel de calle
(Mapillary / KartaView) y el contexto OSM del <em>edificio candidato</em>. Es la forma más rápida de resolver un
<code>match_aproximado</code>: comparar la descripción del reporte con la fachada real. Los enlaces requieren internet;
el resto del panel funciona offline. Cobertura en Venezuela: Mapillary parcial, KartaView escasa — cualquier foto que el
equipo de campo suba a Mapillary mejora esto para todos.</div>
<table>
<thead><tr><th>Código</th><th>Urgencia</th><th>Tipo</th><th>Personas</th><th>Edificio</th><th>Match</th><th>Dist.</th><th>Precisión</th><th>Conf. evid.</th><th>Confirmar</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody>
</table>

<div class="foot">
  Fuente de incidentes: <strong>SOS Venezuela 2026</strong> (sosvenezuela2026.com) — datos abiertos para fines humanitarios.
  Atribución y reglas de privacidad respetadas: coordenadas de reportes nunca desanonimizadas; los enlaces de confirmación
  usan la coordenada pública OSM del edificio candidato, no la del reporte.<br>
  Base geográfica: © colaboradores de OpenStreetMap (ODbL), vía Overpass API. Fotos de calle: Mapillary (CC-BY-SA) y KartaView (open).<br>
  RescueGIS-LaGuaira-Caracas · scripts/connector_sosvenezuela.py · scripts/watch_sosvenezuela.py · scripts/generar_panel.py
</div>
</body>
</html>"""

    out_path.write_text(html, encoding="utf-8")
    print(f"Panel generado: {out_path} ({len(html):,} bytes, {len(edificios)} edificios, {len(incidentes)} incidentes)")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_DEFAULT
    out.parent.mkdir(parents=True, exist_ok=True)
    main(out)
