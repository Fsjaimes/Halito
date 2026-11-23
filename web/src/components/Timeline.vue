<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { formatClock, formatMoment, formatShortDay, hourOf } from "../dates";
import { formatValue, type Scale } from "../scales";

const props = defineProps<{
  timestamps: string[];
  /** Una serie por estación, alineada con timestamps. */
  series: { id: number; values: (number | null)[] }[];
  /** Estación resaltada; sin ella se resalta la mediana de todas. */
  highlighted: number | null;
  scale: Scale;
  /** Episodios de la semana como franjas, en horas desde el inicio (fin exclusivo). */
  bands: { id: number; from: number; to: number; color: string; active: boolean }[];
}>();
const index = defineModel<number>({ required: true });

const HEIGHT = 132;
const PAD = { top: 16, right: 12, bottom: 24, left: 40 };

const root = ref<HTMLElement>();
const width = ref(800);
let observer: ResizeObserver | undefined;
onMounted(() => {
  observer = new ResizeObserver(([entry]) => (width.value = entry!.contentRect.width));
  observer.observe(root.value!);
});
onBeforeUnmount(() => observer?.disconnect());

const last = computed(() => Math.max(1, props.timestamps.length - 1));
const innerWidth = computed(() => Math.max(1, width.value - PAD.left - PAD.right));
const innerHeight = HEIGHT - PAD.top - PAD.bottom;
const x = (i: number) => PAD.left + (i / last.value) * innerWidth.value;

const median = computed(() =>
  props.timestamps.map((_, i) => {
    const present = props.series
      .map((s) => s.values[i])
      .filter((v): v is number => v != null)
      .sort((a, b) => a - b);
    if (!present.length) return null;
    const mid = present.length >> 1;
    return present.length % 2 ? present[mid]! : (present[mid - 1]! + present[mid]!) / 2;
  }),
);

const main = computed(
  () => props.series.find((s) => s.id === props.highlighted)?.values ?? median.value,
);

const domain = computed<[number, number]>(() => {
  const all = props.series.flatMap((s) => s.values).filter((v): v is number => v != null);
  if (!all.length) return props.scale.domain;
  const min = Math.min(...all);
  const max = Math.max(...all);
  const pad = (max - min || 1) * 0.08;
  return [props.scale.domain[0] === 0 ? Math.max(0, min - pad) : min - pad, max + pad];
});
const y = (value: number) => {
  const [min, max] = domain.value;
  return PAD.top + (1 - (value - min) / (max - min)) * innerHeight;
};

/** Trazo que se interrumpe donde faltan datos, en lugar de unir los extremos del hueco. */
function path(values: (number | null)[]): string {
  let d = "";
  let pen = false;
  values.forEach((value, i) => {
    if (value == null) return void (pen = false);
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(value).toFixed(1)}`;
    pen = true;
  });
  return d;
}

const background = computed(() =>
  props.series.filter((s) => s.id !== props.highlighted).map((s) => ({ id: s.id, d: path(s.values) })),
);
const mainPath = computed(() => path(main.value));

/** Franjas de noche (de 6 p. m. a 6 a. m.) como tramos contiguos de horas. */
const nights = computed(() => {
  const bands: { x: number; width: number }[] = [];
  let start: number | null = null;
  props.timestamps.forEach((timestamp, i) => {
    const hour = hourOf(timestamp);
    const dark = hour >= 18 || hour < 6;
    if (dark && start === null) start = i;
    if ((!dark || i === props.timestamps.length - 1) && start !== null) {
      bands.push({ x: x(start), width: x(i) - x(start) });
      start = null;
    }
  });
  return bands;
});

const days = computed(() =>
  props.timestamps
    .map((timestamp, i) => ({ timestamp, i }))
    .filter(({ timestamp }) => hourOf(timestamp) === 12)
    // En pantallas angostas las etiquetas se pisan: se muestra un día sí y otro no.
    .filter((_, day, all) => innerWidth.value / all.length >= 58 || day % 2 === 0)
    .map(({ timestamp, i }) => ({ x: x(i), label: formatShortDay(timestamp) })),
);
const midnights = computed(() =>
  props.timestamps.flatMap((timestamp, i) => (hourOf(timestamp) === 0 && i > 0 ? [x(i)] : [])),
);
const ticks = computed(() => {
  const [min, max] = domain.value;
  return [0.15, 0.5, 0.85].map((t) => {
    const value = min + (max - min) * t;
    return { y: y(value), label: formatValue({ ...props.scale, decimals: 0 }, value) };
  });
});

const visibleBands = computed(() =>
  props.bands.map((band) => {
    const from = Math.max(0, band.from);
    const to = Math.min(props.timestamps.length, band.to);
    // Una franja de una sola hora se ve como una marca de al menos 4 px.
    const left = x(from);
    return { ...band, x: left, width: Math.max(4, x(Math.min(to, last.value)) - left) };
  }),
);

const current = computed(() => main.value[index.value] ?? null);
const now = computed(() => props.timestamps[index.value]);

function seek(event: PointerEvent) {
  const box = root.value!.getBoundingClientRect();
  const t = (event.clientX - box.left - PAD.left) / innerWidth.value;
  index.value = Math.round(Math.min(1, Math.max(0, t)) * last.value);
}
function onPointerDown(event: PointerEvent) {
  (event.currentTarget as Element).setPointerCapture(event.pointerId);
  seek(event);
}
function onPointerMove(event: PointerEvent) {
  if (event.buttons & 1) seek(event);
}
function onKey(event: KeyboardEvent) {
  const step: Record<string, number> = {
    ArrowLeft: -1,
    ArrowRight: 1,
    PageDown: -24,
    PageUp: 24,
    Home: -Infinity,
    End: Infinity,
  };
  const delta = step[event.key];
  if (delta === undefined) return;
  event.preventDefault();
  index.value = Math.min(last.value, Math.max(0, index.value + delta));
}
</script>

<template>
  <div
    ref="root"
    class="timeline"
    role="slider"
    tabindex="0"
    aria-label="Hora de la semana"
    :aria-valuemin="0"
    :aria-valuemax="last"
    :aria-valuenow="index"
    :aria-valuetext="now ? `${formatMoment(now)}, ${formatClock(now)}` : ''"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @keydown="onKey"
  >
    <svg :width="width" :height="HEIGHT" aria-hidden="true">
      <rect
        v-for="(night, i) in nights"
        :key="`n${i}`"
        class="timeline__night"
        :x="night.x"
        :y="PAD.top"
        :width="night.width"
        :height="innerHeight"
      />
      <line
        v-for="(mx, i) in midnights"
        :key="`m${i}`"
        class="timeline__midnight"
        :x1="mx"
        :x2="mx"
        :y1="PAD.top"
        :y2="PAD.top + innerHeight"
      />
      <g v-for="tick in ticks" :key="tick.label">
        <line class="timeline__grid" :x1="PAD.left" :x2="width - PAD.right" :y1="tick.y" :y2="tick.y" />
        <text class="timeline__tick" :x="PAD.left - 8" :y="tick.y + 4" text-anchor="end">
          {{ tick.label }}
        </text>
      </g>
      <g v-for="band in visibleBands" :key="`b${band.id}`">
        <rect
          v-if="band.active"
          class="timeline__episode-area"
          :x="band.x"
          :y="PAD.top"
          :width="band.width"
          :height="innerHeight"
          :fill="band.color"
        />
        <rect
          class="timeline__episode"
          :class="{ 'timeline__episode--active': band.active }"
          :x="band.x"
          :y="3"
          :width="band.width"
          height="6"
          rx="3"
          :fill="band.color"
        />
      </g>
      <path v-for="line in background" :key="line.id" class="timeline__others" :d="line.d" />
      <path class="timeline__main" :d="mainPath" />
      <text
        v-for="day in days"
        :key="day.label"
        class="timeline__day"
        :x="day.x"
        :y="HEIGHT - 6"
        text-anchor="middle"
      >
        {{ day.label }}
      </text>
      <line
        class="timeline__playhead"
        :x1="x(index)"
        :x2="x(index)"
        :y1="PAD.top - 4"
        :y2="PAD.top + innerHeight"
      />
      <circle v-if="current !== null" class="timeline__dot" :cx="x(index)" :cy="y(current)" r="5" />
    </svg>
  </div>
</template>

<style>
.timeline {
  position: relative;
  cursor: ew-resize;
  touch-action: none;
  user-select: none;
  border-radius: 8px;
}
.timeline svg {
  display: block;
}
.timeline__night {
  fill: rgb(6 24 27 / 0.42);
}
.timeline__midnight,
.timeline__grid {
  stroke: var(--line);
  stroke-width: 1;
}
.timeline__grid {
  stroke-dasharray: 2 5;
}
.timeline__tick,
.timeline__day {
  fill: var(--fog-dim);
  font-size: 11.5px;
  font-variant-numeric: tabular-nums;
}
.timeline__episode {
  opacity: 0.75;
}
.timeline__episode--active {
  opacity: 1;
}
.timeline__episode-area {
  opacity: 0.14;
}
.timeline__others {
  fill: none;
  stroke: var(--fog);
  stroke-opacity: 0.2;
  stroke-width: 1;
}
.timeline__main {
  fill: none;
  stroke: var(--sun);
  stroke-width: 2.2;
  stroke-linejoin: round;
  stroke-linecap: round;
}
.timeline__playhead {
  stroke: var(--fog);
  stroke-width: 1.5;
}
.timeline__dot {
  fill: var(--sun);
  stroke: var(--petrol);
  stroke-width: 2;
}
</style>
