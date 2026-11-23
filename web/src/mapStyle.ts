import type { StyleSpecification } from "maplibre-gl";

/**
 * Estilo propio sobre teselas abiertas: vectores de OpenFreeMap (esquema OpenMapTiles)
 * y relieve Terrarium. El mapa se queda en tonos petróleo para que el color lo pongan los datos.
 */
const TERRAIN_TILES = ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"];
const TERRAIN_ATTRIBUTION =
  "Relieve: Mapzen, <a href='https://registry.opendata.aws/terrain-tiles/'>AWS Terrain Tiles</a>";

const dem = {
  type: "raster-dem",
  tiles: TERRAIN_TILES,
  encoding: "terrarium",
  tileSize: 256,
  maxzoom: 14,
} as const;

const LABEL_FONT = ["Noto Sans Regular"];

export const TERRAIN_EXAGGERATION = 1.5;

export const mapStyle: StyleSpecification = {
  version: 8,
  glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
  sources: {
    base: { type: "vector", url: "https://tiles.openfreemap.org/planet" },
    // Dos fuentes de relieve: una para el terreno 3D y otra para el sombreado.
    terrain: { ...dem, attribution: TERRAIN_ATTRIBUTION },
    hillshade: dem,
  },
  sky: {
    "sky-color": "#3b6f78",
    "horizon-color": "#b9d6cf",
    "fog-color": "#a9cbc4",
    "sky-horizon-blend": 0.6,
    "horizon-fog-blend": 0.7,
    "fog-ground-blend": 0.35,
  },
  terrain: { source: "terrain", exaggeration: TERRAIN_EXAGGERATION },
  layers: [
    { id: "land", type: "background", paint: { "background-color": "#2a5257" } },
    {
      id: "forest",
      type: "fill",
      source: "base",
      "source-layer": "landcover",
      filter: ["in", ["get", "class"], ["literal", ["wood", "grass", "farmland"]]],
      paint: { "fill-color": "#27564f", "fill-opacity": 0.7 },
    },
    {
      id: "urban",
      type: "fill",
      source: "base",
      "source-layer": "landuse",
      filter: ["in", ["get", "class"], ["literal", ["residential", "commercial", "industrial"]]],
      paint: { "fill-color": "#3d686b", "fill-opacity": 0.75 },
    },
    {
      id: "relief",
      type: "hillshade",
      source: "hillshade",
      paint: {
        "hillshade-exaggeration": 0.55,
        "hillshade-shadow-color": "#0b2226",
        "hillshade-highlight-color": "#cfe6df",
        "hillshade-accent-color": "#17393d",
        "hillshade-illumination-direction": 315,
      },
    },
    {
      id: "water",
      type: "fill",
      source: "base",
      "source-layer": "water",
      paint: { "fill-color": "#16383f" },
    },
    {
      id: "rivers",
      type: "line",
      source: "base",
      "source-layer": "waterway",
      paint: { "line-color": "#7fb6c2", "line-opacity": 0.45, "line-width": 1 },
    },
    {
      id: "streets",
      type: "line",
      source: "base",
      "source-layer": "transportation",
      minzoom: 12,
      filter: ["in", ["get", "class"], ["literal", ["secondary", "tertiary", "minor"]]],
      paint: { "line-color": "#6f9596", "line-opacity": 0.35, "line-width": 0.6 },
    },
    {
      id: "roads",
      type: "line",
      source: "base",
      "source-layer": "transportation",
      filter: ["in", ["get", "class"], ["literal", ["motorway", "trunk", "primary"]]],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#a7c4c0",
        "line-opacity": 0.5,
        "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.6, 14, 2.4],
      },
    },
    {
      id: "places",
      type: "symbol",
      source: "base",
      "source-layer": "place",
      filter: ["in", ["get", "class"], ["literal", ["city", "town", "village"]]],
      layout: {
        "text-field": ["coalesce", ["get", "name:es"], ["get", "name"]],
        "text-font": LABEL_FONT,
        "text-size": ["match", ["get", "class"], "city", 15, "town", 13, 11],
        "text-letter-spacing": 0.04,
        "text-padding": 8,
      },
      paint: {
        "text-color": "#eaf2ef",
        "text-opacity": 0.85,
        "text-halo-color": "#12343a",
        "text-halo-width": 1.4,
      },
    },
  ],
};
