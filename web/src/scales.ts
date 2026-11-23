import type { EpisodeKind } from "./api";

/** Cómo se nombra, se formatea y se colorea cada variable que muestra el mapa. */

type Stop = [value: number, color: string];

export interface Scale {
  code: string;
  label: string;
  unit: string;
  /** Rango que cubre la rampa de color y la altura de las plumas. */
  domain: [number, number];
  stops: Stop[];
  /** Escalonada (categorías) en lugar de continua. */
  stepped?: boolean;
  decimals: number;
  note?: string;
}

export const SCALES: Scale[] = [
  {
    code: "temp_2m",
    label: "Temperatura",
    unit: "°C",
    domain: [14, 34],
    stops: [
      [14, "#2c7fb8"],
      [20, "#7fcdbb"],
      [25, "#ffe08a"],
      [29, "#f58b3c"],
      [34, "#c2185b"],
    ],
    decimals: 1,
  },
  {
    code: "rh_2m",
    label: "Humedad",
    unit: "%",
    domain: [30, 100],
    stops: [
      [30, "#e8c98a"],
      [65, "#8fd0c0"],
      [100, "#1f78a8"],
    ],
    decimals: 0,
  },
  {
    code: "precip",
    label: "Lluvia",
    unit: "mm",
    domain: [0, 20],
    stops: [
      [0, "#c9d8d4"],
      [2, "#7cc4e8"],
      [8, "#2f7fd6"],
      [20, "#5b3fc4"],
    ],
    decimals: 1,
  },
  {
    code: "wind_speed",
    label: "Viento",
    unit: "m/s",
    domain: [0, 8],
    stops: [
      [0, "#c9d8d4"],
      [3, "#9ad0a8"],
      [8, "#f2c230"],
    ],
    decimals: 1,
  },
  {
    code: "pm25",
    label: "PM2.5",
    unit: "µg/m³",
    domain: [0, 80],
    // Categorías del índice de calidad del aire (ICA) para PM2.5.
    stops: [
      [0, "#4cc38a"],
      [12, "#f2c230"],
      [37, "#f58b3c"],
      [55, "#e5484d"],
      [150, "#8e4ec6"],
    ],
    stepped: true,
    decimals: 0,
    note: "Colores del índice de calidad del aire, que se calcula sobre la media de 24 horas.",
  },
  {
    code: "o3",
    label: "Ozono",
    unit: "µg/m³",
    domain: [0, 160],
    stops: [
      [0, "#c9d8d4"],
      [60, "#8fd0c0"],
      [100, "#f2c230"],
      [160, "#e5484d"],
    ],
    decimals: 0,
  },
];

const channels = (hex: string) =>
  [1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16)) as [number, number, number];

export function colorFor(scale: Scale, value: number): string {
  const stops = scale.stops;
  let upper = stops.findIndex(([threshold]) => value < threshold);
  if (upper === -1) return stops[stops.length - 1]![1];
  if (upper === 0) return stops[0]![1];
  const [low, lowColor] = stops[upper - 1]!;
  if (scale.stepped) return lowColor;
  const [high, highColor] = stops[upper]!;
  const t = (value - low) / (high - low);
  const a = channels(lowColor);
  const b = channels(highColor);
  const mix = a.map((channel, i) => Math.round(channel + (b[i]! - channel) * t));
  return `rgb(${mix.join(" ")})`;
}

/** Posición del valor dentro del rango de la escala, entre 0 y 1. */
export function fraction(scale: Scale, value: number): number {
  const [min, max] = scale.domain;
  return Math.min(1, Math.max(0, (value - min) / (max - min)));
}

export function legendGradient(scale: Scale): string {
  const [min, max] = scale.domain;
  const at = (value: number) => `${(((Math.min(value, max) - min) / (max - min)) * 100).toFixed(1)}%`;
  if (!scale.stepped) {
    return `linear-gradient(90deg, ${scale.stops.map(([v, c]) => `${c} ${at(v)}`).join(", ")})`;
  }
  const bands = scale.stops.map(([value, color], i) => {
    const next = scale.stops[i + 1]?.[0] ?? max;
    return `${color} ${at(value)} ${at(next)}`;
  });
  return `linear-gradient(90deg, ${bands.join(", ")})`;
}

const formatters = new Map<number, Intl.NumberFormat>();

export function formatValue(scale: Scale, value: number): string {
  let formatter = formatters.get(scale.decimals);
  if (!formatter) {
    formatter = new Intl.NumberFormat("es-CO", {
      minimumFractionDigits: scale.decimals,
      maximumFractionDigits: scale.decimals,
    });
    formatters.set(scale.decimals, formatter);
  }
  return formatter.format(value);
}

/** Anomalía de temperatura de superficie: frío en azul, neutro en arena, caliente en magenta. */
export const HEAT_STOPS: [number, string][] = [
  [-8, "#2c7fb8"],
  [-4, "#7fcdbb"],
  [0, "#f1e8cf"],
  [3, "#f58b3c"],
  [7, "#c2185b"],
];

export const heatGradient = () => {
  const [min, max] = [HEAT_STOPS[0]![0], HEAT_STOPS[HEAT_STOPS.length - 1]![0]];
  const stops = HEAT_STOPS.map(([v, c]) => `${c} ${(((v - min) / (max - min)) * 100).toFixed(1)}%`);
  return `linear-gradient(90deg, ${stops.join(", ")})`;
};

const signed = new Intl.NumberFormat("es-CO", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
  signDisplay: "exceptZero",
});
const plain = new Intl.NumberFormat("es-CO", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
export const formatSigned = (value: number) => signed.format(value);
export const formatDegrees = (value: number) => plain.format(value);


export const EPISODES: Record<EpisodeKind, { label: string; color: string; scale: string }> = {
  heat: { label: "Calor", color: "#f58b3c", scale: "temp_2m" },
  pm25: { label: "PM2.5", color: "#e5484d", scale: "pm25" },
  o3: { label: "Ozono", color: "#b07ce8", scale: "o3" },
  heavy_rain: { label: "Aguacero", color: "#5aa9f0", scale: "precip" },
};
