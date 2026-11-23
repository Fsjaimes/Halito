export interface Variable {
  code: string;
  name: string;
  unit: string;
  kind: "meteo" | "pollutant";
  first_day: string;
  last_day: string;
}

export interface Station {
  id: number;
  source: "ideam" | "cdmb";
  name: string;
  municipality: string;
  lat: number;
  lon: number;
  elevation_m: number | null;
  variables: string[];
}

/** values[i][j]: valor en timestamps[i] de la estación stations[j]. */
export interface Frames {
  variable: string;
  unit: string;
  stations: number[];
  timestamps: string[];
  values: (number | null)[][];
}

export interface HeatSummary {
  scenes_used: number;
  scenes_discarded: number;
  first_scene: string | null;
  last_scene: string | null;
  periods: { period: string; scenes: number; cells: number }[];
}

/** Columnas alineadas: cells[i] tiene lst_c[i], anomaly_c[i] y n_scenes[i]. */
export interface HeatCells {
  period: string;
  cells: string[];
  lst_c: number[];
  anomaly_c: number[];
  n_scenes: number[];
}

export type EpisodeKind = "heat" | "pm25" | "o3" | "heavy_rain";

/** Tramo en que una o varias estaciones superaron un umbral. ends_at es exclusivo. */
export interface Episode {
  id: number;
  kind: EpisodeKind;
  variable: string;
  starts_at: string;
  ends_at: string;
  peak_value: number;
  peak_at: string;
  peak_station_id: number;
  stations: number[];
  threshold: number;
  severity: string;
  summary: string;
  calm_hours: number;
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, { signal });
  if (!response.ok) throw new Error(`La API respondió ${response.status}`);
  return (await response.json()) as T;
}

export const fetchVariables = () => get<Variable[]>("/variables");
export const fetchStations = () => get<Station[]>("/stations");
export const fetchFrames = (variable: string, start: string, days: number, signal: AbortSignal) =>
  get<Frames>(`/frames?variable=${variable}&start=${start}&days=${days}`, signal);
export const fetchHeat = () => get<HeatSummary>("/heat");
export const fetchHeatCells = (period: string, signal: AbortSignal) =>
  get<HeatCells>(`/heat/cells?period=${period}`, signal);
export const fetchEpisodes = () => get<Episode[]>("/episodes");
