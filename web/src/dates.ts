/** Las fechas viajan como «AAAA-MM-DD» y las horas como ISO con −05:00 (hora de Colombia). */

const TZ = "America/Bogota";

export function addDays(day: string, amount: number): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + amount);
  return date.toISOString().slice(0, 10);
}

export const clampDay = (day: string, min: string, max: string) =>
  day < min ? min : day > max ? max : day;

/** Hora del día (0–23) leída del propio texto, que ya está en hora local. */
export const hourOf = (timestamp: string) => Number(timestamp.slice(11, 13));

const moment = new Intl.DateTimeFormat("es-CO", {
  timeZone: TZ,
  weekday: "long",
  day: "numeric",
  month: "long",
});
const clock = new Intl.DateTimeFormat("es-CO", {
  timeZone: TZ,
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
});
const shortDay = new Intl.DateTimeFormat("es-CO", { timeZone: TZ, weekday: "short", day: "numeric" });
const range = new Intl.DateTimeFormat("es-CO", {
  timeZone: "UTC",
  day: "numeric",
  month: "short",
  year: "numeric",
});

export const formatMoment = (timestamp: string) => moment.format(new Date(timestamp));
export const formatClock = (timestamp: string) => clock.format(new Date(timestamp));
export const formatShortDay = (timestamp: string) => shortDay.format(new Date(timestamp));
export const formatRange = (start: string, end: string) =>
  range.formatRange(new Date(`${start}T00:00:00Z`), new Date(`${end}T00:00:00Z`));
export const formatDay = (day: string) => range.format(new Date(`${day}T00:00:00Z`));

const isoDay = new Intl.DateTimeFormat("en-CA", { timeZone: TZ });
/** Día «AAAA-MM-DD» en hora de Colombia de un instante cualquiera. */
export const localDay = (timestamp: string) => isoDay.format(new Date(timestamp));

const spanDay = new Intl.DateTimeFormat("es-CO", {
  timeZone: TZ,
  day: "numeric",
  month: "short",
  year: "numeric",
});
/** «10 feb 2025» o «10–12 feb 2025»; el fin es exclusivo, así que se resta un instante. */
export const formatSpan = (start: string, end: string) =>
  spanDay.formatRange(new Date(start), new Date(new Date(end).getTime() - 1));
