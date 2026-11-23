<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import {
  fetchEpisodes,
  fetchFrames,
  fetchHeat,
  fetchHeatCells,
  fetchStations,
  fetchVariables,
  type Episode,
  type EpisodeKind,
  type Frames,
  type HeatCells,
  type HeatSummary,
  type Station,
  type Variable,
} from "./api";
import MapView from "./components/MapView.vue";
import Timeline from "./components/Timeline.vue";
import {
  addDays,
  clampDay,
  formatClock,
  formatDay,
  formatMoment,
  formatRange,
  formatSpan,
  localDay,
} from "./dates";
import {
  colorFor,
  EPISODES,
  formatValue,
  HEAT_STOPS,
  heatGradient,
  legendGradient,
  SCALES,
  type Scale,
} from "./scales";

const DAYS = 7;
const SPEEDS = [1, 2, 4];
const STEP_MS = 650;

const variables = ref<Variable[]>([]);
const stations = ref<Station[]>([]);
const frames = ref<Frames | null>(null);
const scale = ref<Scale>(SCALES[0]!);
const start = ref("");
const index = ref(0);
const selected = ref<number | null>(null);
const playing = ref(false);
const speed = ref(1);
const loading = ref(true);
const error = ref("");
/** Aviso cuando la semana pedida no existe para la variable y se muestra otra. */
const notice = ref("");

const heatSummary = ref<HeatSummary | null>(null);
const heatCells = ref<HeatCells | null>(null);
const heatOn = ref(true);
const heatPeriod = ref("all");
const PERIODS: Record<string, string> = {
  all: "Todo el año",
  DEF: "Dic a feb",
  MAM: "Mar a may",
  JJA: "Jun a ago",
  SON: "Sep a nov",
};
/** Periodos disponibles, en orden de calendario. */
const heatPeriods = computed(() =>
  Object.keys(PERIODS).filter((key) => heatSummary.value?.periods.some((p) => p.period === key)),
);
const heatScenes = computed(
  () => heatSummary.value?.periods.find((p) => p.period === heatPeriod.value)?.scenes ?? 0,
);

let heatRequest: AbortController | undefined;
async function loadHeat() {
  heatRequest?.abort();
  heatRequest = new AbortController();
  try {
    heatCells.value = await fetchHeatCells(heatPeriod.value, heatRequest.signal);
  } catch (cause) {
    if ((cause as Error).name !== "AbortError") heatCells.value = null;
  }
}
watch(heatPeriod, () => void loadHeat());

const episodes = ref<Episode[]>([]);
const episodeFilter = ref<EpisodeKind | "all">("all");
const activeEpisode = ref<Episode | null>(null);
/** Hora a la que saltar cuando termine de cargar la semana del episodio. */
let pendingPeak: string | null = null;

const episodeKinds = computed(() =>
  (Object.keys(EPISODES) as EpisodeKind[])
    .map((kind) => ({ kind, count: episodes.value.filter((e) => e.kind === kind).length }))
    .filter(({ count }) => count > 0),
);
const listedEpisodes = computed(() =>
  episodeFilter.value === "all"
    ? episodes.value
    : episodes.value.filter((e) => e.kind === episodeFilter.value),
);

/** Episodios de la variable que se ve, con su posición en horas dentro de la semana. */
const bands = computed(() => {
  const first = frames.value?.timestamps[0];
  if (!first) return [];
  const origin = new Date(first).getTime();
  const span = frames.value!.timestamps.length;
  const toHour = (timestamp: string) => (new Date(timestamp).getTime() - origin) / 3_600_000;
  return episodes.value
    .filter((e) => EPISODES[e.kind].scale === scale.value.code)
    .map((e) => ({
      id: e.id,
      from: toHour(e.starts_at),
      to: toHour(e.ends_at),
      color: EPISODES[e.kind].color,
      active: activeEpisode.value?.id === e.id,
    }))
    .filter((band) => band.to > 0 && band.from < span);
});

function openEpisode(episode: Episode) {
  const target = SCALES.find((s) => s.code === EPISODES[episode.kind].scale);
  if (!target) return;
  playing.value = false;
  activeEpisode.value = episode;
  scale.value = target;
  selected.value = episode.peak_station_id;
  pendingPeak = episode.peak_at;
  // La semana se centra en el pico: empieza tres días antes.
  settleWeek(addDays(localDay(episode.peak_at), -3));
  notice.value = "";
}

function closeEpisode() {
  activeEpisode.value = null;
}

const variable = computed(() => variables.value.find((v) => v.code === scale.value.code));
const available = computed(() => SCALES.filter((s) => variables.value.some((v) => v.code === s.code)));
const lastStart = computed(() => (variable.value ? addDays(variable.value.last_day, 1 - DAYS) : ""));
const end = computed(() => (start.value ? addDays(start.value, DAYS - 1) : ""));
const canGoBack = computed(() => !!variable.value && start.value > variable.value.first_day);
const canGoForward = computed(() => !!variable.value && start.value < lastStart.value);

const series = computed(() =>
  (frames.value?.stations ?? []).map((id, column) => ({
    id,
    values: frames.value!.values.map((row) => row[column] ?? null),
  })),
);
const valuesNow = computed(() => {
  const row = frames.value?.values[index.value];
  return new Map((frames.value?.stations ?? []).map((id, column) => [id, row?.[column] ?? null]));
});
const active = computed(
  () => new Set(series.value.filter((s) => s.values.some((v) => v !== null)).map((s) => s.id)),
);
const now = computed(() => frames.value?.timestamps[index.value]);

const station = computed(() => stations.value.find((s) => s.id === selected.value) ?? null);
const stationSeries = computed(() => series.value.find((s) => s.id === selected.value)?.values ?? null);
const stationNow = computed(() => stationSeries.value?.[index.value] ?? null);
const stationStats = computed(() => {
  const present = (stationSeries.value ?? []).filter((v): v is number => v != null);
  if (!present.length) return null;
  return {
    min: Math.min(...present),
    max: Math.max(...present),
    hours: present.length,
    total: stationSeries.value!.length,
  };
});
const SOURCES = { ideam: "IDEAM", cdmb: "CDMB" } as const;

let request: AbortController | undefined;
async function loadFrames() {
  if (!variable.value) return;
  request?.abort();
  request = new AbortController();
  loading.value = true;
  error.value = "";
  try {
    const data = await fetchFrames(scale.value.code, start.value, DAYS, request.signal);
    const keepHour = frames.value?.timestamps.length === data.timestamps.length;
    frames.value = data;
    const peak = pendingPeak ? new Date(pendingPeak).getTime() : null;
    pendingPeak = null;
    const peakIndex = peak === null ? -1 : data.timestamps.findIndex((t) => new Date(t).getTime() === peak);
    if (peakIndex >= 0) index.value = peakIndex;
    else if (!keepHour) index.value = lastHourWithData(data);
    loading.value = false;
  } catch (cause) {
    if ((cause as Error).name === "AbortError") return;
    error.value = "No se pudo cargar la semana. Comprueba que la API esté encendida (make api).";
    loading.value = false;
  }
}

function lastHourWithData(data: Frames): number {
  for (let i = data.values.length - 1; i >= 0; i--) {
    if (data.values[i]!.some((value) => value !== null)) return i;
  }
  return 0;
}

/** Sitúa la semana dentro de lo que la variable tiene y avisa si hubo que moverla. */
function settleWeek(wanted: string) {
  const v = variable.value;
  if (!v) return;
  const settled = clampDay(wanted || lastStart.value, v.first_day, lastStart.value);
  notice.value =
    wanted && settled !== wanted
      ? `${scale.value.label} tiene datos hasta el ${formatDay(v.last_day)}. Se muestra su última semana.`
      : "";
  if (settled === start.value) void loadFrames();
  else start.value = settled;
}

function choose(next: Scale) {
  if (next.code === scale.value.code) return;
  activeEpisode.value = null;
  scale.value = next;
  if (selected.value !== null && !stations.value.find((s) => s.id === selected.value)?.variables.includes(next.code)) {
    selected.value = null;
  }
  settleWeek(start.value);
}

function shiftWeek(direction: number) {
  playing.value = false;
  settleWeek(addDays(start.value, direction * DAYS));
  notice.value = "";
}

function pickDay(event: Event) {
  const day = (event.target as HTMLInputElement).value;
  if (!day) return;
  playing.value = false;
  settleWeek(day);
}

watch(start, () => void loadFrames());

let timer: number | undefined;
watch([playing, speed], () => {
  window.clearInterval(timer);
  if (!playing.value) return;
  timer = window.setInterval(() => {
    const lastIndex = (frames.value?.timestamps.length ?? 1) - 1;
    if (index.value >= lastIndex) playing.value = false;
    else index.value += 1;
  }, STEP_MS / speed.value);
});

function togglePlay() {
  const lastIndex = (frames.value?.timestamps.length ?? 1) - 1;
  if (!playing.value && index.value >= lastIndex) index.value = 0;
  playing.value = !playing.value;
}

function onKey(event: KeyboardEvent) {
  const target = event.target as HTMLElement;
  if (event.code !== "Space" || ["BUTTON", "INPUT"].includes(target.tagName)) return;
  event.preventDefault();
  togglePlay();
}

async function boot() {
  loading.value = true;
  error.value = "";
  try {
    [variables.value, stations.value] = await Promise.all([fetchVariables(), fetchStations()]);
    if (!available.value.length) {
      error.value = "La base de datos está vacía. Carga datos con make ingest.";
      loading.value = false;
      return;
    }
    scale.value = available.value[0]!;
    settleWeek("");
    // El calor de superficie es opcional: si aún no se ha calculado, el mapa funciona sin él.
    heatSummary.value = await fetchHeat().catch(() => null);
    episodes.value = await fetchEpisodes().catch(() => []);
    if (heatSummary.value?.periods.length) void loadHeat();
  } catch {
    error.value = "No hay conexión con la API. Enciéndela con make api y vuelve a intentar.";
    loading.value = false;
  }
}

onMounted(() => {
  window.addEventListener("keydown", onKey);
  void boot();
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKey);
  window.clearInterval(timer);
  request?.abort();
});
</script>

<template>
  <MapView
    :stations="stations"
    :values="valuesNow"
    :active="active"
    :scale="scale"
    :selected="selected"
    :heat="heatOn ? heatCells : null"
    @select="selected = $event"
  />

  <header class="masthead panel">
    <h1 class="masthead__name">Hálito</h1>
    <p class="masthead__tagline">El calor y el aire de Bucaramanga, hora a hora.</p>

    <div class="chips" role="radiogroup" aria-label="Qué mostrar">
      <button
        v-for="option in available"
        :key="option.code"
        type="button"
        role="radio"
        class="chip"
        :aria-checked="option.code === scale.code"
        @click="choose(option)"
      >
        {{ option.label }}
      </button>
    </div>

    <div class="legend">
      <div class="legend__bar" :style="{ background: legendGradient(scale) }"></div>
      <div class="legend__ends">
        <span>{{ scale.domain[0] }}</span>
        <span>{{ scale.unit }}</span>
        <span>{{ scale.domain[1] }}{{ scale.stepped ? " o más" : "" }}</span>
      </div>
      <p v-if="scale.note" class="legend__note">{{ scale.note }}</p>
    </div>

    <section v-if="heatSummary?.periods.length" class="heat">
      <label class="switch">
        <input v-model="heatOn" type="checkbox" role="switch" />
        <span class="switch__track" aria-hidden="true"></span>
        Calor del suelo
      </label>
      <template v-if="heatOn">
        <div class="chips chips--small" role="radiogroup" aria-label="Época del año">
          <button
            v-for="period in heatPeriods"
            :key="period"
            type="button"
            role="radio"
            class="chip"
            :aria-checked="period === heatPeriod"
            @click="heatPeriod = period"
          >
            {{ PERIODS[period] }}
          </button>
        </div>
        <div class="legend">
          <div class="legend__bar" :style="{ background: heatGradient() }"></div>
          <div class="legend__ends">
            <span>{{ HEAT_STOPS[0]![0] }}</span>
            <span>°C frente a la mediana</span>
            <span>+{{ HEAT_STOPS[HEAT_STOPS.length - 1]![0] }}</span>
          </div>
          <p class="legend__note">
            Temperatura del suelo y los techos hacia las 10:30 a. m., según {{ heatScenes }} imágenes
            de satélite. No es la del aire. Toca una zona para ver su dato.
          </p>
        </div>
      </template>
    </section>
  </header>

  <aside v-if="station" class="station panel" aria-live="polite">
    <button type="button" class="station__close" aria-label="Cerrar estación" @click="selected = null">
      ×
    </button>
    <h2 class="station__name">{{ station.name }}</h2>
    <p class="station__place">
      {{ station.municipality }}<template v-if="station.elevation_m">, {{ station.elevation_m }} m</template>
      <span class="station__source">{{ SOURCES[station.source] }}</span>
    </p>
    <p class="station__reading">
      <template v-if="stationNow !== null">
        <span class="station__value" :style="{ color: colorFor(scale, stationNow) }">
          {{ formatValue(scale, stationNow) }}
        </span>
        <span class="station__unit">{{ scale.unit }}</span>
      </template>
      <span v-else class="station__missing">Sin dato a esta hora</span>
    </p>
    <dl v-if="stationStats" class="station__stats">
      <div>
        <dt>Mínima de la semana</dt>
        <dd>{{ formatValue(scale, stationStats.min) }} {{ scale.unit }}</dd>
      </div>
      <div>
        <dt>Máxima de la semana</dt>
        <dd>{{ formatValue(scale, stationStats.max) }} {{ scale.unit }}</dd>
      </div>
      <div>
        <dt>Horas con dato</dt>
        <dd>{{ stationStats.hours }} de {{ stationStats.total }}</dd>
      </div>
    </dl>
    <p v-else class="station__missing">Esta estación no reportó {{ scale.label.toLowerCase() }} esta semana.</p>
  </aside>

  <section class="dock panel" :class="{ 'dock--with-episodes': episodes.length }" aria-label="Línea de tiempo">
    <div class="dock__main">
    <div class="dock__bar">
      <button
        type="button"
        class="play"
        :aria-label="playing ? 'Pausar' : 'Reproducir la semana'"
        :disabled="!frames"
        @click="togglePlay"
      >
        <svg v-if="playing" viewBox="0 0 16 16" aria-hidden="true">
          <rect x="3" y="2" width="3.6" height="12" rx="1" />
          <rect x="9.4" y="2" width="3.6" height="12" rx="1" />
        </svg>
        <svg v-else viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.2v11.6a.6.6 0 0 0 .9.5l9.2-5.8a.6.6 0 0 0 0-1L4.900 1.700a.6.6 0 0 0-.9.5Z" /></svg>
      </button>
      <button
        type="button"
        class="speed"
        :aria-label="`Velocidad ${speed}×. Cambiar`"
        @click="speed = SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length]!"
      >
        {{ speed }}×
      </button>

      <p class="moment" aria-live="off">
        <template v-if="now">
          <span class="moment__clock">{{ formatClock(now) }}</span>
          <span class="moment__day">{{ formatMoment(now) }}</span>
        </template>
      </p>

      <div class="week">
        <button type="button" class="week__step" :disabled="!canGoBack" aria-label="Semana anterior" @click="shiftWeek(-1)">
          ‹
        </button>
        <label class="week__range">
          <span>{{ start ? formatRange(start, end) : "" }}</span>
          <span class="visually-hidden">Ir a la semana que empieza el día</span>
          <input
            type="date"
            :value="start"
            :min="variable?.first_day"
            :max="lastStart"
            @change="pickDay"
          />
        </label>
        <button type="button" class="week__step" :disabled="!canGoForward" aria-label="Semana siguiente" @click="shiftWeek(1)">
          ›
        </button>
      </div>
    </div>

    <p v-if="notice" class="dock__notice" role="status">{{ notice }}</p>

    <div v-if="error" class="dock__state" role="alert">
      <p>{{ error }}</p>
      <button type="button" class="retry" @click="variables.length ? loadFrames() : boot()">Volver a intentar</button>
    </div>
    <Timeline
      v-else-if="frames"
      v-model="index"
      :class="{ 'timeline--loading': loading }"
      :timestamps="frames.timestamps"
      :series="series"
      :highlighted="selected"
      :scale="scale"
      :bands="bands"
    />
    <p v-else class="dock__state">Cargando la semana…</p>

    <div v-if="activeEpisode" class="episode-note" :style="{ '--tone': EPISODES[activeEpisode.kind].color }">
      <p>
        <strong>{{ EPISODES[activeEpisode.kind].label }}, {{ formatSpan(activeEpisode.starts_at, activeEpisode.ends_at) }}.</strong>
        {{ activeEpisode.summary }}
      </p>
      <button type="button" class="episode-note__close" aria-label="Cerrar episodio" @click="closeEpisode">×</button>
    </div>
    <p v-else-if="frames && !error" class="dock__caption">
      <span class="dock__swatch"></span>
      {{ station ? station.name : "Mediana de las estaciones" }}
      <span class="dock__faint">
        Las líneas tenues son las demás estaciones; las franjas oscuras, la noche<template v-if="bands.length">; las marcas de color, episodios</template>.
      </span>
    </p>
    <p class="credits">
      Datos del IDEAM y la CDMB en
      <a href="https://www.datos.gov.co" target="_blank" rel="noopener">datos.gov.co</a>. Mapa de
      <a href="https://openfreemap.org" target="_blank" rel="noopener">OpenFreeMap</a>, ©
      <a href="https://www.openmaptiles.org" target="_blank" rel="noopener">OpenMapTiles</a>, con datos de
      <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>.
      Relieve de Mapzen en
      <a href="https://registry.opendata.aws/terrain-tiles/" target="_blank" rel="noopener">AWS Terrain Tiles</a>.
    </p>
    </div>

    <aside v-if="episodes.length" class="episodes" aria-label="Episodios">
      <div class="episodes__head">
        <h2 class="episodes__title">Episodios</h2>
        <div class="episodes__filters" role="radiogroup" aria-label="Tipo de episodio">
          <button
            type="button"
            role="radio"
            class="tag"
            :aria-checked="episodeFilter === 'all'"
            @click="episodeFilter = 'all'"
          >
            Todos
          </button>
          <button
            v-for="option in episodeKinds"
            :key="option.kind"
            type="button"
            role="radio"
            class="tag"
            :style="{ '--tone': EPISODES[option.kind].color }"
            :aria-checked="episodeFilter === option.kind"
            @click="episodeFilter = option.kind"
          >
            {{ EPISODES[option.kind].label }} {{ option.count }}
          </button>
        </div>
      </div>
      <ol class="episodes__list">
        <li v-for="episode in listedEpisodes" :key="episode.id">
          <button
            type="button"
            class="episode"
            :class="{ 'episode--active': activeEpisode?.id === episode.id }"
            :style="{ '--tone': EPISODES[episode.kind].color }"
            :aria-current="activeEpisode?.id === episode.id"
            @click="openEpisode(episode)"
          >
            <span class="episode__kind">{{ EPISODES[episode.kind].label }}</span>
            <span class="episode__when">{{ formatSpan(episode.starts_at, episode.ends_at) }}</span>
            <span class="episode__severity">{{ episode.severity }}</span>
          </button>
        </li>
      </ol>
    </aside>
  </section>
</template>

<style>
.masthead,
.station,
.dock {
  z-index: 1;
}
.masthead {
  position: absolute;
  top: var(--gutter);
  left: var(--gutter);
  width: min(340px, calc(100vw - 2 * var(--gutter)));
  padding: 18px 20px 16px;
}
.masthead__name {
  margin: 0;
  font-family: var(--display);
  font-size: 2.6rem;
  font-weight: 700;
  font-stretch: 85%;
  line-height: 0.95;
  letter-spacing: -0.02em;
}
.masthead__tagline {
  margin: 6px 0 16px;
  color: var(--fog-dim);
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chip {
  padding: 6px 13px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: none;
  cursor: pointer;
  transition: background-color 0.15s ease;
}
.chip:hover {
  background: var(--petrol-lift);
}
.chip[aria-checked="true"] {
  border-color: var(--sun);
  background: var(--sun);
  color: var(--ink);
  font-weight: 600;
}

.legend {
  margin-top: 16px;
}

.heat {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}
.heat .legend {
  margin-top: 12px;
}
.switch {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 600;
  cursor: pointer;
}
.switch input {
  position: absolute;
  opacity: 0;
}
.switch__track {
  position: relative;
  width: 38px;
  height: 22px;
  border-radius: 999px;
  background: var(--petrol-lift);
  border: 1px solid var(--line);
  transition: background-color 0.15s ease;
}
.switch__track::after {
  content: "";
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--fog);
  transition: transform 0.15s ease;
}
.switch input:checked + .switch__track {
  background: var(--sun);
}
.switch input:checked + .switch__track::after {
  transform: translateX(16px);
  background: var(--ink);
}
.switch input:focus-visible + .switch__track {
  outline: 2px solid var(--sun);
  outline-offset: 2px;
}
.chips--small {
  margin-top: 12px;
}
.chips--small .chip {
  padding: 4px 10px;
  font-size: 0.85rem;
}
.legend__bar {
  height: 8px;
  border-radius: 4px;
}
.legend__ends {
  display: flex;
  justify-content: space-between;
  margin-top: 5px;
  color: var(--fog-dim);
  font-size: 0.8rem;
  font-variant-numeric: tabular-nums;
}
.legend__note {
  margin: 8px 0 0;
  color: var(--fog-dim);
  font-size: 0.8rem;
}

.station {
  position: absolute;
  top: var(--gutter);
  right: calc(var(--gutter) + 52px);
  width: min(280px, calc(100vw - 2 * var(--gutter)));
  padding: 16px 18px;
}
.station__close {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 30px;
  height: 30px;
  border: 0;
  border-radius: 50%;
  background: none;
  font-size: 1.3rem;
  line-height: 1;
  cursor: pointer;
}
.station__close:hover {
  background: var(--petrol-lift);
}
.station__name {
  margin: 0 28px 0 0;
  font-family: var(--display);
  font-size: 1.25rem;
  font-weight: 650;
  line-height: 1.15;
}
.station__place {
  margin: 4px 0 0;
  color: var(--fog-dim);
  font-size: 0.88rem;
}
.station__source {
  margin-left: 8px;
  padding: 1px 7px;
  border: 1px solid var(--line);
  border-radius: 5px;
  font-size: 0.75rem;
}
.station__reading {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin: 12px 0 10px;
}
.station__value {
  font-family: var(--display);
  font-size: 3.2rem;
  font-weight: 700;
  font-stretch: 85%;
  font-variant-numeric: tabular-nums;
  line-height: 1;
  transition: color 0.35s ease;
}
.station__unit {
  color: var(--fog-dim);
  font-size: 1.05rem;
}
.station__missing {
  margin: 0;
  color: var(--fog-dim);
}
.station__stats {
  margin: 0;
  border-top: 1px solid var(--line);
}
.station__stats div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 0;
  border-bottom: 1px solid var(--line);
  font-size: 0.88rem;
}
.station__stats div:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}
.station__stats dt {
  color: var(--fog-dim);
}
.station__stats dd {
  margin: 0;
  font-variant-numeric: tabular-nums;
}

.dock {
  position: absolute;
  right: var(--gutter);
  bottom: var(--gutter);
  left: var(--gutter);
  padding: 12px 16px 10px;
}
.dock__bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.play,
.speed,
.week__step {
  display: grid;
  place-items: center;
  height: 40px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: none;
  cursor: pointer;
}
.play {
  width: 40px;
  border-color: var(--sun);
  background: var(--sun);
  color: var(--ink);
}
.play svg {
  width: 16px;
  height: 16px;
  fill: currentColor;
}
.speed {
  min-width: 44px;
  padding: 0 10px;
  font-variant-numeric: tabular-nums;
}
.week__step {
  width: 36px;
  font-size: 1.3rem;
  line-height: 1;
}
.speed:hover,
.week__step:hover:not(:disabled) {
  background: var(--petrol-lift);
}
button:disabled {
  opacity: 0.35;
  cursor: default;
}

.moment {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin: 0 auto 0 8px;
  min-width: 0;
}
.moment__clock {
  font-family: var(--display);
  font-size: 1.7rem;
  font-weight: 650;
  font-stretch: 85%;
  font-variant-numeric: tabular-nums;
  line-height: 1;
  white-space: nowrap;
}
.moment__day {
  overflow: hidden;
  color: var(--fog-dim);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.week {
  display: flex;
  align-items: center;
  gap: 6px;
}
.week__range {
  position: relative;
  display: grid;
  place-items: center;
  height: 40px;
  padding: 0 14px;
  border: 1px solid var(--line);
  border-radius: 10px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  cursor: pointer;
}
.week__range:hover {
  background: var(--petrol-lift);
}
.week__range:focus-within {
  outline: 2px solid var(--sun);
  outline-offset: 2px;
}
/* El selector de fecha nativo cubre la etiqueta, invisible: al tocarla se abre el calendario. */
.week__range input {
  position: absolute;
  inset: 0;
  width: 100%;
  opacity: 0;
  cursor: pointer;
  color-scheme: dark;
}
.week__range input::-webkit-calendar-picker-indicator {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  cursor: pointer;
}

.dock__notice {
  margin: 0 0 6px;
  padding: 6px 10px;
  border-left: 3px solid var(--sun);
  background: rgb(242 194 48 / 0.1);
  font-size: 0.88rem;
}
.dock__state {
  display: grid;
  place-items: center;
  gap: 10px;
  min-height: 132px;
  margin: 0;
  color: var(--fog-dim);
  text-align: center;
}
.dock__state p {
  margin: 0;
}
.retry {
  padding: 8px 16px;
  border: 0;
  border-radius: 10px;
  background: var(--sun);
  color: var(--ink);
  font-weight: 600;
  cursor: pointer;
}
.timeline--loading {
  opacity: 0.45;
}
.dock__caption {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 2px 0 0;
  font-size: 0.82rem;
}
.dock__swatch {
  width: 18px;
  height: 3px;
  border-radius: 2px;
  background: var(--sun);
}
.dock__faint {
  margin-left: 6px;
  color: var(--fog-dim);
}
.credits {
  margin: 4px 0 0;
  color: var(--fog-dim);
  font-size: 0.72rem;
}
.credits a {
  color: inherit;
}


.dock--with-episodes {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 270px;
  gap: 18px;
}
.dock__main {
  min-width: 0;
}

.episodes {
  display: flex;
  flex-direction: column;
  min-height: 0;
  max-height: 236px;
  padding-left: 16px;
  border-left: 1px solid var(--line);
}
.episodes__head {
  display: grid;
  gap: 8px;
  margin-bottom: 8px;
}
.episodes__title {
  margin: 0;
  font-family: var(--display);
  font-size: 1.05rem;
  font-weight: 650;
}
.episodes__filters {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.tag {
  --tone: var(--fog);
  padding: 2px 9px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: none;
  font-size: 0.8rem;
  cursor: pointer;
}
.tag:hover {
  background: var(--petrol-lift);
}
.tag[aria-checked="true"] {
  border-color: var(--tone);
  background: color-mix(in srgb, var(--tone) 22%, transparent);
}
.episodes__list {
  flex: 1;
  margin: 0;
  padding: 0 4px 0 0;
  overflow-y: auto;
  list-style: none;
  scrollbar-width: thin;
}
.episode {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0 10px;
  width: 100%;
  padding: 6px 8px 6px 12px;
  border: 0;
  border-left: 3px solid var(--tone);
  border-radius: 0 8px 8px 0;
  background: none;
  text-align: left;
  cursor: pointer;
}
.episode + .episode {
  margin-top: 2px;
}
.episode:hover {
  background: var(--petrol-lift);
}
.episode--active {
  background: color-mix(in srgb, var(--tone) 18%, transparent);
}
.episode__kind {
  font-weight: 600;
  font-size: 0.88rem;
}
.episode__when {
  justify-self: end;
  color: var(--fog-dim);
  font-size: 0.82rem;
  font-variant-numeric: tabular-nums;
}
.episode__severity {
  grid-column: 1 / -1;
  color: var(--fog-dim);
  font-size: 0.78rem;
}

.episode-note {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-top: 2px;
  padding: 6px 8px 6px 12px;
  border-left: 3px solid var(--tone);
  background: color-mix(in srgb, var(--tone) 12%, transparent);
  font-size: 0.86rem;
}
.episode-note p {
  flex: 1;
  margin: 0;
  max-width: 90ch;
}
.episode-note__close {
  flex: none;
  width: 26px;
  height: 26px;
  border: 0;
  border-radius: 50%;
  background: none;
  font-size: 1.1rem;
  line-height: 1;
  cursor: pointer;
}
.episode-note__close:hover {
  background: var(--petrol-lift);
}

@media (max-width: 1100px) {
  .dock--with-episodes {
    display: block;
  }
  .episodes {
    max-height: none;
    margin-top: 8px;
    padding: 8px 0 0;
    border-top: 1px solid var(--line);
    border-left: 0;
  }
  .episodes__list {
    display: flex;
    gap: 6px;
    padding: 0 0 4px;
    overflow-x: auto;
    overflow-y: hidden;
  }
  .episode {
    flex: none;
    width: 190px;
  }
  .episode + .episode {
    margin-top: 0;
  }
}

@media (max-width: 720px) {
  /* En el teléfono se hace zoom y se gira con los dedos; los botones quedarían bajo el panel. */
  .maplibregl-ctrl-top-right {
    display: none;
  }
  .masthead {
    padding: 12px 14px;
  }
  .masthead__name {
    font-size: 1.9rem;
  }
  .masthead__tagline,
  .legend__note,
  .dock__faint,
  .moment__day {
    display: none;
  }
  .masthead__name {
    margin-bottom: 10px;
  }
  .chips {
    flex-wrap: nowrap;
    margin: 0 -14px;
    padding: 0 14px;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .chip {
    flex: none;
  }
  .heat .chips,
  .heat .legend,
  .episodes__filters {
    display: none;
  }
  .episode-note p {
    display: -webkit-box;
    overflow: hidden;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 3;
  }
  .credits {
    font-size: 0.66rem;
  }
  .heat {
    margin-top: 10px;
    padding-top: 10px;
  }
  .station {
    top: auto;
    right: var(--gutter);
    bottom: 236px;
    left: var(--gutter);
    width: auto;
  }
  .station__value {
    font-size: 2.4rem;
  }
  .dock__bar {
    flex-wrap: wrap;
  }
  .week {
    width: 100%;
  }
  .week__range {
    flex: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  * {
    transition: none !important;
  }
}
</style>
