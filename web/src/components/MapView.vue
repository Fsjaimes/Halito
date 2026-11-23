<script setup lang="ts">
import { cellToBoundary } from "h3-js";
import maplibregl from "maplibre-gl";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";

import type { HeatCells, Station } from "../api";
import { mapStyle } from "../mapStyle";
import {
  colorFor,
  formatDegrees,
  formatSigned,
  formatValue,
  fraction,
  HEAT_STOPS,
  type Scale,
} from "../scales";

const props = defineProps<{
  stations: Station[];
  /** Valor de cada estación en la hora elegida; sin entrada, la estación no mide la variable. */
  values: Map<number, number | null>;
  /** Estaciones con algún dato en la semana; las demás se reducen a un punto. */
  active: Set<number>;
  scale: Scale;
  selected: number | null;
  /** Calor de superficie por celda; null oculta la capa. */
  heat: HeatCells | null;
}>();
const emit = defineEmits<{ select: [id: number | null] }>();

const container = ref<HTMLElement>();
let map: maplibregl.Map | undefined;
const plumes = new Map<number, { marker: maplibregl.Marker; el: HTMLButtonElement }>();

let popup: maplibregl.Popup | undefined;

/** Dibuja las celdas H3 como polígonos nativos del mapa, que se pliegan solos sobre el relieve. */
function paintHeat() {
  const source = map?.getSource("heat") as maplibregl.GeoJSONSource | undefined;
  if (!source) return;
  popup?.remove();
  const heat = props.heat;
  source.setData({
    type: "FeatureCollection",
    features: (heat?.cells ?? []).map((cell, i) => ({
      type: "Feature",
      properties: { anomaly: heat!.anomaly_c[i], lst: heat!.lst_c[i], scenes: heat!.n_scenes[i] },
      geometry: { type: "Polygon", coordinates: [cellToBoundary(cell, true)] },
    })),
  });
}

function addHeatLayer() {
  map!.addSource("heat", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map!.addLayer(
    {
      id: "heat",
      type: "fill",
      source: "heat",
      paint: {
        "fill-color": ["interpolate", ["linear"], ["get", "anomaly"], ...HEAT_STOPS.flat()],
        "fill-opacity": 0.82,
        "fill-antialias": false,
      },
    },
    // Debajo del sombreado: el relieve sigue leyéndose sobre el color.
    "relief",
  );
  map!.on("click", "heat", (event) => {
    const cell = event.features?.[0]?.properties;
    if (!cell) return;
    const anomaly = Number(cell.anomaly);
    const verdict =
      Math.abs(anomaly) < 0.5
        ? "Como la mediana del área"
        : `${formatSigned(anomaly)} °C ${anomaly > 0 ? "más caliente" : "más fresco"} que la mediana`;
    popup?.remove();
    popup = new maplibregl.Popup({ closeButton: false, maxWidth: "260px", className: "heat-popup" })
      .setLngLat(event.lngLat)
      .setHTML(
        `<strong>${verdict}</strong>` +
          `<span>Suelo a ${formatDegrees(Number(cell.lst))} °C, mediana de ${cell.scenes} imágenes</span>`,
      )
      .addTo(map!);
  });
  map!.on("mouseenter", "heat", () => (map!.getCanvas().style.cursor = "pointer"));
  map!.on("mouseleave", "heat", () => (map!.getCanvas().style.cursor = ""));
  paintHeat();
}

const STEM_MIN = 14;
const STEM_RANGE = 78;

function buildPlume(station: Station) {
  const el = document.createElement("button");
  el.type = "button";
  el.className = "plume";
  el.innerHTML =
    '<span class="plume__value"></span><span class="plume__stem"></span>' +
    '<span class="plume__foot"></span><span class="plume__name"></span>';
  el.querySelector(".plume__name")!.textContent = station.name;
  el.addEventListener("click", (event) => {
    event.stopPropagation();
    emit("select", props.selected === station.id ? null : station.id);
  });
  // Las estaciones del norte quedan detrás de las del sur en la vista inclinada.
  el.style.zIndex = String(Math.round((8 - station.lat) * 1000));
  const marker = new maplibregl.Marker({ element: el, anchor: "bottom" })
    .setLngLat([station.lon, station.lat])
    .addTo(map!);
  plumes.set(station.id, { marker, el });
}

function paint() {
  if (!map) return;
  for (const station of props.stations) {
    if (!plumes.has(station.id)) buildPlume(station);
    const { el } = plumes.get(station.id)!;
    const measures = props.values.has(station.id);
    const value = props.values.get(station.id) ?? null;
    el.hidden = !measures;
    el.classList.toggle("plume--empty", value === null);
    el.classList.toggle("plume--dormant", !props.active.has(station.id));
    el.classList.toggle("plume--selected", props.selected === station.id);
    el.style.setProperty("--stem", `${value === null ? 6 : STEM_MIN + STEM_RANGE * fraction(props.scale, value)}px`);
    el.style.setProperty("--tone", value === null ? "#8aa5a3" : colorFor(props.scale, value));
    const text = value === null ? "sin dato" : formatValue(props.scale, value);
    el.querySelector(".plume__value")!.textContent = text;
    el.setAttribute(
      "aria-label",
      `${station.name}: ${value === null ? "sin dato" : `${text} ${props.scale.unit}`}`,
    );
    el.setAttribute("aria-pressed", String(props.selected === station.id));
  }
}

onMounted(() => {
  map = new maplibregl.Map({
    container: container.value!,
    style: mapStyle,
    center: [-73.15, 7.085],
    zoom: 10.9,
    pitch: 56,
    bearing: -14,
    maxPitch: 75,
    minZoom: 8.5,
    maxZoom: 15,
    maxBounds: [
      [-74.3, 6.2],
      [-72.2, 8.0],
    ],
    // Los créditos del mapa van escritos en el panel inferior, que taparía el control nativo.
    attributionControl: false,
  });
  // Deja libre la franja que ocupa la línea de tiempo: el centro del mapa sube.
  map.setPadding({ top: 0, right: 0, bottom: 190, left: 0 });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
  map.on("click", () => emit("select", null));
  // En cuanto el estilo está listo, sin esperar a que terminen de llegar todas las teselas.
  map.once("style.load", addHeatLayer);
  map.on("error", (event) => console.error("Mapa:", event.error?.message ?? event));
  paint();
});

onBeforeUnmount(() => map?.remove());

watch(() => [props.stations, props.values, props.active, props.scale, props.selected], paint);
watch(() => props.heat, paintHeat);
</script>

<template>
  <div ref="container" class="map" role="application" aria-label="Mapa de estaciones"></div>
</template>

<style>
.map {
  position: absolute;
  inset: 0;
  /* Contexto de apilado propio: las plumas quedan siempre por debajo de los paneles. */
  isolation: isolate;
  z-index: 0;
  background: var(--petrol-deep);
}

/* La pluma: un tallo que crece con el valor y una etiqueta con la cifra. */
.plume {
  --stem: 14px;
  --tone: #8aa5a3;
  display: grid;
  justify-items: center;
  padding: 0;
  border: 0;
  background: none;
  color: var(--ink);
  font: inherit;
  cursor: pointer;
}
.plume__value {
  padding: 2px 9px 3px;
  border-radius: 999px;
  background: var(--tone);
  color: #10282b;
  font-family: var(--display);
  font-size: 0.95rem;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
  white-space: nowrap;
  box-shadow: 0 2px 10px rgb(6 24 27 / 0.45);
  transition: background-color 0.35s ease;
}
.plume__stem {
  width: 4px;
  height: var(--stem);
  border-radius: 0 0 2px 2px;
  background: linear-gradient(var(--tone), color-mix(in srgb, var(--tone) 35%, transparent));
  transition: height 0.35s ease;
}
.plume__foot {
  width: 9px;
  height: 9px;
  margin-top: -4px;
  border: 2px solid var(--fog);
  border-radius: 50%;
  background: var(--tone);
  transition: background-color 0.35s ease;
}
.plume__name {
  position: absolute;
  top: 100%;
  margin-top: 4px;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--petrol);
  color: var(--fog);
  font-size: 0.78rem;
  white-space: nowrap;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s ease;
}
.plume:hover .plume__name,
.plume:focus-visible .plume__name,
.plume--selected .plume__name {
  opacity: 1;
}
.plume--empty .plume__value {
  background: rgb(18 52 58 / 0.8);
  color: var(--fog-dim);
  font-family: var(--body);
  font-size: 0.72rem;
  font-weight: 500;
}
.plume--dormant .plume__value,
.plume--dormant .plume__stem {
  display: none;
}
.plume--dormant .plume__foot {
  margin-top: 0;
  border-color: var(--fog-dim);
  background: var(--petrol);
}
.plume--selected .plume__value {
  outline: 2px solid var(--fog);
  outline-offset: 2px;
}
.plume:focus-visible {
  outline: none;
}
.plume:focus-visible .plume__value {
  outline: 2px solid var(--sun);
  outline-offset: 2px;
}

.heat-popup .maplibregl-popup-content {
  display: grid;
  gap: 2px;
  padding: 10px 14px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--petrol);
  color: var(--fog);
  font-family: var(--body);
  font-size: 0.88rem;
  line-height: 1.35;
}
.heat-popup .maplibregl-popup-content span {
  color: var(--fog-dim);
  font-size: 0.8rem;
}
.heat-popup .maplibregl-popup-tip {
  border-top-color: var(--petrol);
  border-bottom-color: var(--petrol);
}

.maplibregl-ctrl-top-right {
  top: 12px;
  right: 12px;
}
.maplibregl-ctrl-group {
  background: var(--petrol);
  box-shadow: none;
}
.maplibregl-ctrl-group button + button {
  border-top-color: rgb(234 242 239 / 0.15);
}
.maplibregl-ctrl-group button .maplibregl-ctrl-icon {
  filter: invert(1) brightness(1.6);
}
.maplibregl-ctrl-attrib {
  background: rgb(18 52 58 / 0.75) !important;
  color: var(--fog-dim);
  font-size: 0.68rem;
}
.maplibregl-ctrl-attrib a {
  color: var(--fog);
}

@media (prefers-reduced-motion: reduce) {
  .plume__value,
  .plume__stem,
  .plume__foot {
    transition: none;
  }
}
</style>
