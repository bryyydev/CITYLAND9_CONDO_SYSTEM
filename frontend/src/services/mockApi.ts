// PROTOTYPE data service (npm run dev:mock). In-memory, resets on reload. Never in production.
//
// It follows the server's business rules so the prototype behaves like the real system:
//  - bills are frozen when issued; status is derived from the balance (Paid / Partially Paid / Overdue / Unpaid)
//  - condo dues = area x rate; parking/storage from the assigned units; storage counts from 2026-10
//  - water = usage x rate; penalty on prior unpaid balances; due date is the 8th
//  - a payment above the balance becomes an advance payment (condo dues only)
//  - every payment gets the next gap-free official receipt number for its year (OR-YYYY-NNNNNN)
//  - CHECK / ONLINE payments need a reference number
//  - gate pass requests: today .. +90 days; reject needs a reason; cancel only while Requested
//  - Move In/Out certificate generation fails with HTTP 500 like the server does today (Bug D1)

import { addDays, addMonths, todayIso } from "../lib/format";
import { fromCents, isAmount, toCents } from "../lib/money";
import { previewPayroll } from "../lib/payroll";
import { permissionsForRole } from "../config/permissions";
import { ROLES } from "../config/roles";
import { PERSONAS } from "./personas";
import type { DataService } from "./api";
import { ApiError } from "./http";
import type * as T from "./types";

/** Set to false to preview the certificate screen as it will work once Bug D1 is fixed on the server. */
export const SIMULATE_D1 = true;

const LATENCY = 220;
const wait = <X>(value: X): Promise<X> => new Promise((res) => setTimeout(() => res(structuredClone(value)), LATENCY));
const fail = (status: number, message: string): Promise<never> =>
  new Promise((_, rej) => setTimeout(() => rej(new ApiError(status, message)), LATENCY));

const TODAY = todayIso();
const THIS_MONTH = TODAY.slice(0, 7);
const nowUtc = () => new Date().toISOString().slice(0, 19);
const STORAGE_FROM = "2026-10";
const RESIDENTIAL: T.UnitType[] = ["STUDIO TYPE", "1 BEDROOM", "2 BEDROOM", "3 BEDROOM"];

// ------------------------------------------------------------------ session (Role Switcher)
const SESSION_KEY = "cl9.mock.role";
let currentRole: T.Role | null = (() => {
  try {
    return (sessionStorage.getItem(SESSION_KEY) as T.Role | null) ?? null;
  } catch {
    return null;
  }
})();

export function setMockRole(role: T.Role | null) {
  currentRole = role;
  try {
    if (role) sessionStorage.setItem(SESSION_KEY, role);
    else sessionStorage.removeItem(SESSION_KEY);
  } catch {
    /* prototype only */
  }
}

const actor = () => (currentRole ? PERSONAS[currentRole].username : "system");

// ------------------------------------------------------------------ settings
const rates: T.RatesAndRules = {
  corporationName: "CITYLAND 9 CONDOMINIUM CORPORATION",
  address: "9 Dela Rosa Street, Barangay Pio del Pilar, Makati City",
  ratesPerSqm: { studio: "85.00", oneBed: "85.00", twoBed: "90.00", threeBed: "95.00" },
  parkingRatePerSqm: "60.00",
  storageRatePerSqm: "45.00",
  waterRate: "50.00",
  waterAutoCompute: true,
  penaltyRate: "10",
  penaltyIncludes: { condo: true, parking: false, storage: false, water: false },
  storageInTotalFrom: STORAGE_FROM,
  onlinePaymentUrl: "",
  onlinePaymentInstructions: "Pay at the Admin Office (Ground Floor), Mon–Sat 8:00 AM–5:00 PM, by cash, check or bank transfer.",
  smtpHost: "",
  smtpPort: "587",
  smtpSender: "",
  smtpUsername: "",
  smtpPasswordSet: false,
};

const rateFor = (type: T.UnitType): number => {
  const r = rates.ratesPerSqm;
  return toCents({ "STUDIO TYPE": r.studio, "1 BEDROOM": r.oneBed, "2 BEDROOM": r.twoBed, "3 BEDROOM": r.threeBed }[type as "STUDIO TYPE"] ?? "0");
};

// ------------------------------------------------------------------ people & units
interface Person { id: number; unitId: number; type: "Owner" | "Tenant"; name: string; contactNo: string; email: string; status: "Current" | "Past"; moveIn: string | null; moveOut: string | null }
interface MockUnit { id: number; unitNo: string; floor: string; type: T.UnitType; areaSqm: number; ratePerSqmCents: number | null; status: "Occupied" | "Vacant"; occupancy: "Owner" | "Tenant"; active: boolean; parkingId: number | null; storageId: number | null }

const OWNERS = ["Juan Dela Cruz", "Maria Santos", "Pedro Reyes", "Ana Garcia", "Jose Mendoza", "Liza Ramos", "Carlo Aquino", "Rosa Villareal",
  "Miguel Torres", "Grace Lim", "Antonio Navarro", "Cristina Castillo", "Ramon Gonzales", "Teresa Domingo", "Eduardo Pascual", "Isabel Flores",
  "Fernando Soriano", "Patricia Chua", "Roberto Salazar", "Angelica Rivera", "Danilo Mercado", "Beatriz Tan", "Marcus Villanueva", "Lourdes Valdez"];
const TENANTS = ["Kevin Ong", "Janine Cruz", "Mark Bernardo", "Sheila Manalo", "Paolo Robles", "Nicole Yap"];

const units: MockUnit[] = [];
const people: Person[] = [];
let nextPersonId = 1;
{
  let id = 1;
  const types: T.UnitType[] = ["STUDIO TYPE", "1 BEDROOM", "2 BEDROOM"];
  const areas: Record<string, number> = { "STUDIO TYPE": 28, "1 BEDROOM": 42, "2 BEDROOM": 64, "3 BEDROOM": 86 };
  // Parking and storage units first (they are assigned to residential units).
  for (let p = 1; p <= 10; p++) units.push({ id: id++, unitNo: `P-${String(p).padStart(2, "0")}`, floor: "B1", type: "PARKING", areaSqm: 12.5, ratePerSqmCents: null, status: "Occupied", occupancy: "Owner", active: true, parkingId: null, storageId: null });
  for (let s = 1; s <= 6; s++) units.push({ id: id++, unitNo: `S-${String(s).padStart(2, "0")}`, floor: "B2", type: "STORAGE", areaSqm: 4, ratePerSqmCents: null, status: "Occupied", occupancy: "Owner", active: true, parkingId: null, storageId: null });
  let o = 0;
  for (let floor = 5; floor <= 12; floor++) {
    for (let n = 1; n <= 3; n++) {
      const type = floor === 12 && n === 1 ? "3 BEDROOM" : types[(floor + n) % 3];
      const vacant = (floor === 7 && n === 3) || (floor === 10 && n === 2);
      const withTenant = !vacant && (floor + n) % 4 === 0;
      const unit: MockUnit = { id: id++, unitNo: `${floor}-${String(n).padStart(2, "0")}`, floor: `${floor}F`, type, areaSqm: areas[type], ratePerSqmCents: null,
        status: vacant ? "Vacant" : "Occupied", occupancy: withTenant ? "Tenant" : "Owner", active: true,
        parkingId: o < 10 && !vacant ? units[o].id : null, storageId: o < 6 && !vacant ? units[10 + o].id : null };
      units.push(unit);
      const ownerName = unit.unitNo === "12-01" ? "Marcus Villanueva" : OWNERS[o % OWNERS.length];
      people.push({ id: nextPersonId++, unitId: unit.id, type: "Owner", name: ownerName, contactNo: `0917${String(1000000 + o * 7919).slice(-7)}`,
        email: unit.unitNo === "12-01" ? "marcus.v@email.com" : `${ownerName.toLowerCase().replace(/[^a-z]+/g, ".")}@email.com`,
        status: "Current", moveIn: `20${18 + (o % 7)}-0${1 + (o % 9)}-15`, moveOut: null });
      if (withTenant) {
        const t = TENANTS[(floor + n) % TENANTS.length];
        people.push({ id: nextPersonId++, unitId: unit.id, type: "Tenant", name: t, contactNo: `0998${String(2000000 + o * 3571).slice(-7)}`, email: `${t.toLowerCase().replace(/[^a-z]+/g, ".")}@email.com`, status: "Current", moveIn: "2025-06-01", moveOut: null });
      }
      if (floor === 9 && n === 1) people.push({ id: nextPersonId++, unitId: unit.id, type: "Tenant", name: "Arnel Pineda", contactNo: "09281234567", email: "", status: "Past", moveIn: "2023-02-01", moveOut: "2025-05-31" });
      o++;
    }
  }
}
const unitById = (id: number) => units.find((u) => u.id === id)!;
const unitByNo = (no: string) => units.find((u) => u.unitNo.toLowerCase() === no.trim().toLowerCase());
const residential = () => units.filter((u) => u.active && RESIDENTIAL.includes(u.type));
const currentOwner = (unitId: number) => people.filter((p) => p.unitId === unitId && p.type === "Owner" && p.status === "Current").at(-1) ?? null;
const currentTenant = (unitId: number) => people.filter((p) => p.unitId === unitId && p.type === "Tenant" && p.status === "Current").at(-1) ?? null;
const payerName = (unitId: number) => (currentTenant(unitId) ?? currentOwner(unitId))?.name ?? "—";
const RESIDENT_UNIT = unitByNo("12-01")!;

const condoCents = (u: MockUnit) => Math.round(u.areaSqm * (u.ratePerSqmCents ?? rateFor(u.type)));
const assetCents = (assetId: number | null, rateCents: number) => (assetId ? Math.round(unitById(assetId).areaSqm * rateCents) : 0);

// ------------------------------------------------------------------ water
interface MockReading { id: number; unitId: number; month: string; previous: number; current: number; rateCents: number; readingDate: string; paidCents: number }
const readings: MockReading[] = [];
let nextReadingId = 1;
const FIRST_MONTH = "2026-04";
for (const u of residential()) {
  let meter = 100 + u.id * 3;
  for (let m = FIRST_MONTH; m < THIS_MONTH; m = addMonths(m, 1)) {
    const use = u.status === "Vacant" ? 0 : 6 + ((u.id * 7 + Number(m.slice(5))) % 11);
    readings.push({ id: nextReadingId++, unitId: u.id, month: m, previous: meter, current: meter + use, rateCents: 5000, readingDate: `${m}-05`, paidCents: 0 });
    meter += use;
  }
}
const readingFor = (unitId: number, month: string) => readings.find((r) => r.unitId === unitId && r.month === month);
const readingCents = (r: MockReading | undefined) => (r && rates.waterAutoCompute ? Math.round(Math.max(r.current - r.previous, 0) * r.rateCents) : 0);

// ------------------------------------------------------------------ bills, payments, advances, receipts
interface MockPayment { id: number; billId: number; amountCents: number; date: string; method: T.PaymentMethod; type: string; reference: string }
interface MockBill { id: number; unitId: number; month: string; condo: number; parking: number; storage: number; water: number; penalty: number; previous: number; advance: number; paid: number; dueDate: string; note: string; manual: boolean }
interface MockAdvance { id: number; unitId: number; date: string; amountCents: number; startMonth: string; months: number; method: T.PaymentMethod; reference: string; appliedCents: number }
interface MockReceipt { id: number; year: number; seq: number; unitId: number; date: string; amountCents: number; method: T.PaymentMethod; reference: string; remarks: string; receivedBy: string; backfilled: boolean; items: { kind: T.ReceiptItem["kind"]; refId: number; cents: number }[] }

const bills: MockBill[] = [];
const payments: MockPayment[] = [];
const advances: MockAdvance[] = [];
const receipts: MockReceipt[] = [];
let nextBillId = 1, nextPaymentId = 1, nextAdvanceId = 1, nextReceiptId = 1;

const waterIncluded = (b: MockBill) => !(readingFor(b.unitId, b.month)?.paidCents ?? 0);
const storageIncluded = (b: MockBill) => b.month >= rates.storageInTotalFrom;
const currentCharges = (b: MockBill) => b.condo + b.parking + (storageIncluded(b) ? b.storage : 0) + (waterIncluded(b) ? b.water : 0);
const totalCents = (b: MockBill) => currentCharges(b) + b.penalty + b.previous - b.advance;
const balanceCents = (b: MockBill) => totalCents(b) - b.paid;

function statusOf(b: MockBill): T.BillStatus {
  if (balanceCents(b) <= 0) return "Paid";
  if (b.paid > 0) return "Partially Paid";
  if (TODAY > b.dueDate) return "Overdue";
  return "Unpaid";
}

/** Next gap-free OR number for the year (the server locks the year's sequence while issuing). */
function issueReceipt(unitId: number, date: string, method: T.PaymentMethod, reference: string, remarks: string, items: MockReceipt["items"], backfilled = false, receivedBy = actor()) {
  const year = Number(date.slice(0, 4));
  const seq = Math.max(0, ...receipts.filter((r) => r.year === year).map((r) => r.seq)) + 1;
  const receipt: MockReceipt = { id: nextReceiptId++, year, seq, unitId, date, amountCents: items.reduce((s, i) => s + i.cents, 0), method, reference, remarks, receivedBy, backfilled, items };
  receipts.push(receipt);
  return receipt;
}
const orNo = (r: MockReceipt) => `OR-${r.year}-${String(r.seq).padStart(6, "0")}`;

function applyAdvances(bill: MockBill) {
  let need = bill.condo - bill.advance;
  for (const a of advances.filter((x) => x.unitId === bill.unitId && x.startMonth <= bill.month).sort((x, y) => x.date.localeCompare(y.date) || x.id - y.id)) {
    if (need <= 0) break;
    const monthly = Math.floor(a.amountCents / a.months);
    const left = a.amountCents - a.appliedCents;
    const use = Math.min(need, left, Math.max(monthly, 0) || left);
    if (use <= 0) continue;
    a.appliedCents += use;
    bill.advance += use;
    need -= use;
  }
}

function createBill(u: MockUnit, month: string): MockBill {
  const earlier = bills.filter((b) => b.unitId === u.id && b.month < month).sort((a, b) => a.month.localeCompare(b.month));
  const prior = earlier.at(-1);
  const previous = prior ? Math.max(balanceCents(prior), 0) : 0;
  const priorCondoUnpaid = prior ? Math.min(previous, prior.condo) : 0;
  const bill: MockBill = {
    id: nextBillId++, unitId: u.id, month, condo: condoCents(u), parking: assetCents(u.parkingId, toCents(rates.parkingRatePerSqm)),
    storage: assetCents(u.storageId, toCents(rates.storageRatePerSqm)), water: readingCents(readingFor(u.id, month)),
    penalty: rates.penaltyIncludes.condo ? Math.round(priorCondoUnpaid * Number(rates.penaltyRate) / 100) : 0,
    previous, advance: 0, paid: 0, dueDate: `${month}-08`, note: "", manual: false,
  };
  // An earlier bill's unpaid balance is carried into this one (the latest SOA holds what is owed).
  bills.push(bill);
  applyAdvances(bill);
  return bill;
}

function pay(bill: MockBill, amountCents: number, date: string, method: T.PaymentMethod, reference: string, remarks = "", type = "FULL", receivedBy = actor()) {
  const balance = Math.max(balanceCents(bill), 0);
  const applied = Math.min(amountCents, balance);
  const excess = amountCents - applied;
  const items: MockReceipt["items"] = [];
  if (applied > 0) {
    const p: MockPayment = { id: nextPaymentId++, billId: bill.id, amountCents: applied, date, method, type, reference };
    payments.push(p);
    bill.paid += applied;
    items.push({ kind: "bill", refId: p.id, cents: applied });
  }
  if (excess > 0) {
    const a: MockAdvance = { id: nextAdvanceId++, unitId: bill.unitId, date, amountCents: excess, startMonth: bill.month, months: 1, method, reference, appliedCents: 0 };
    advances.push(a);
    items.push({ kind: "advance", refId: a.id, cents: excess });
  }
  const receipt = issueReceipt(bill.unitId, date, method, reference, remarks, items, false, receivedBy);
  return { receipt, applied, excess };
}

// Seed history: bills from April to last month with realistic payment behaviour.
for (let m = FIRST_MONTH; m < THIS_MONTH; m = addMonths(m, 1)) {
  for (const u of residential()) {
    const bill = createBill(u, m);
    const behaviour = (u.id + Number(m.slice(5))) % 9;
    const cashier = ["melissa.bautista", "karen.uy"][u.id % 2];
    const methods: T.PaymentMethod[] = ["CASH", "ONLINE", "CHECK"];
    const method = methods[u.id % 3];
    const ref = method === "CASH" ? "" : `${method === "CHECK" ? "CHK" : "BDO"}-${m.replace("-", "")}${String(u.id).padStart(3, "0")}`;
    const isLatest = addMonths(m, 1) === THIS_MONTH;
    if (u.unitNo === "12-01") {
      if (!isLatest) pay(bill, balanceCents(bill), `${m}-06`, "ONLINE", `GC-${m.replace("-", "")}1201`, "", "FULL", cashier);
      continue;
    }
    if (behaviour <= 5) pay(bill, balanceCents(bill), `${m}-0${3 + (u.id % 5)}`, method, ref, "", "FULL", cashier);
    else if (behaviour === 6) pay(bill, Math.round(balanceCents(bill) / 2 / 100) * 100, `${m}-10`, method, ref, "", "PARTIAL", cashier);
    else if (behaviour === 7 && !isLatest) pay(bill, balanceCents(bill) + 500000, `${m}-02`, method, ref, "Paid ahead", "FULL", cashier);
    // behaviour 8: unpaid (carried forward, penalised next month)
  }
}
// One prepaid 3-month advance and one receipt created for an old payment (before ORs existed).
{
  const u = unitByNo("8-02")!;
  const a: MockAdvance = { id: nextAdvanceId++, unitId: u.id, date: `${THIS_MONTH}-01`, amountCents: condoCents(u) * 3, startMonth: THIS_MONTH, months: 3, method: "CHECK", reference: "CHK-778120", appliedCents: 0 };
  advances.push(a);
  issueReceipt(u.id, a.date, "CHECK", "CHK-778120", "3 months advance", [{ kind: "advance", refId: a.id, cents: a.amountCents }], false, "karen.uy");
  receipts.filter((r) => r.date < "2026-05-01").slice(0, 6).forEach((r) => (r.backfilled = true));
}

// ------------------------------------------------------------------ operations & community
let nextPassId = 1;
const gatePasses: (T.AdminGatePass & { unitId: number })[] = [];
const addPass = (p: Omit<T.AdminGatePass, "id" | "unitNo"> & { unitNo: string }) => {
  const u = unitByNo(p.unitNo)!;
  const pass = { ...p, id: nextPassId++, unitId: u.id };
  gatePasses.unshift(pass);
  return pass;
};
addPass({ type: "Visitor", date: addDays(TODAY, -3), unitNo: "6-02", name: "Lorna Bautista", purpose: "Family visit, 2 guests", status: "Issued", reviewNote: "", requestedAt: null, requestedBy: null, reviewedBy: "rico.dizon", source: "office" });
addPass({ type: "Delivery", date: addDays(TODAY, -1), unitNo: "9-03", name: "LBC Express", purpose: "Appliance delivery via service elevator", status: "Issued", reviewNote: "", requestedAt: null, requestedBy: null, reviewedBy: "rico.dizon", source: "office" });
addPass({ type: "Move-out", date: addDays(TODAY, -2), unitNo: "11-03", name: "Lipat Bahay Movers", purpose: "Furniture, 1 truck", status: "Rejected", reviewNote: "Unpaid balance must be settled before move-out clearance.", requestedAt: `${addDays(TODAY, -5)}T02:14:00`, requestedBy: "owner.1103", reviewedBy: "melissa.bautista", source: "resident" });
addPass({ type: "Move-in", date: addDays(TODAY, 3), unitNo: "12-01", name: "ABC Movers", purpose: "Sofa and 20 boxes, 1 truck, plate NDX 4521", status: "Requested", reviewNote: "", requestedAt: `${addDays(TODAY, -1)}T01:40:00`, requestedBy: "marcus.v", reviewedBy: null, source: "resident" });
addPass({ type: "Visitor", date: TODAY, unitNo: "5-01", name: "Engr. Paolo Cruz", purpose: "Aircon inspection", status: "Requested", reviewNote: "", requestedAt: `${TODAY}T00:05:00`, requestedBy: "owner.501", reviewedBy: null, source: "resident" });

let nextCertId = 4;
const certificates: T.Certificate[] = [
  { id: 1, certificateNo: "MC-2026-0007", moveType: "Move In", personType: "Tenant", personName: "Kevin Ong", unitNo: "6-02", moveDate: "2026-06-01", certificateDate: "2026-06-01", issuedBy: "rico.dizon" },
  { id: 2, certificateNo: "MC-2026-0008", moveType: "Move Out", personType: "Tenant", personName: "Arnel Pineda", unitNo: "9-01", moveDate: "2025-05-31", certificateDate: "2026-06-03", issuedBy: "rico.dizon" },
  { id: 3, certificateNo: "MC-2026-0009", moveType: "Move In", personType: "Owner", personName: "Beatriz Tan", unitNo: "11-03", moveDate: "2026-08-15", certificateDate: "2026-08-15", issuedBy: "melissa.bautista" },
];

let nextExpenseId = 1;
const expenses: T.Expense[] = [
  ["Utilities", "Meralco – common areas", "48250.00", -25], ["Utilities", "Maynilad – bulk water", "31120.50", -22], ["Security", "Security agency – September", "96000.00", -20],
  ["Repairs", "Elevator 2 door sensor replacement", "18500.00", -15], ["Supplies", "Janitorial supplies", "6420.75", -12], ["Repairs", "Lobby light fixtures", "4380.00", -6],
  ["Utilities", "PLDT – admin office internet", "2899.00", -3], ["Supplies", "Printer ink and bond paper", "2150.00", 0],
].map(([category, description, amount, days]) => ({ id: nextExpenseId++, date: addDays(TODAY, days as number), category: category as string, description: description as string, amount: amount as string }));

let nextTicketId = 1;
const tickets: (T.MaintenanceTicket & { unitId: number })[] = [
  ["12-01", "Plumbing", "Leaking kitchen faucet", "Water drips even when fully closed.", "Normal", "In Progress", "Rico D.", "AquaFix Plumbing", "Plumber scheduled Friday 10 AM.", -4],
  ["5-03", "Electrical", "Bedroom outlet not working", "Two outlets near the window have no power.", "High", "Open", "", "", "", -1],
  ["8-02", "Aircon", "Aircon not cooling", "Split type unit, living room.", "Normal", "Resolved", "Ben S.", "CoolAir Services", "Cleaned and recharged freon.", -12],
  ["10-01", "Common Area", "Hallway light busted (10F)", "Near the fire exit.", "Low", "Closed", "Ben S.", "", "Replaced LED tube.", -20],
  ["6-01", "Plumbing", "Clogged bathroom drain", "Slow drain in the shower area.", "Urgent", "Open", "", "", "", 0],
].map(([unitNo, category, title, description, priority, status, assignedTo, vendor, resolution, days]) => {
  const u = unitByNo(unitNo as string)!;
  const d = addDays(TODAY, days as number);
  return { id: nextTicketId, unitId: u.id, unitNo: u.unitNo, ticketNo: `MT-${d.replace(/-/g, "")}${String(nextTicketId++).padStart(4, "0")}`, category: category as string, title: title as string,
    description: description as string, priority: priority as string, status: status as T.TicketStatus, assignedTo: assignedTo as string, vendor: vendor as string, resolution: resolution as string, requestedAt: `${d}T02:30:00` };
});

const vendors: T.Vendor[] = [
  { id: 1, name: "AquaFix Plumbing Services", serviceType: "Plumbing", contactPerson: "Ramil Santos", contactNo: "0917 555 0142", email: "service@aquafix.ph", status: "Active" },
  { id: 2, name: "CoolAir Services", serviceType: "Aircon", contactPerson: "Joy Lagman", contactNo: "0918 222 7831", email: "joy@coolair.ph", status: "Active" },
  { id: 3, name: "Brightline Electrical", serviceType: "Electrical", contactPerson: "Noel Garcia", contactNo: "0922 413 0099", email: "noel@brightline.ph", status: "Active" },
  { id: 4, name: "Liftmaster Elevators", serviceType: "Elevator maintenance", contactPerson: "Carla Uy", contactNo: "(02) 8812 3344", email: "carla@liftmaster.ph", status: "Active" },
  { id: 5, name: "PestAway Inc.", serviceType: "Pest control", contactPerson: "Dennis Co", contactNo: "0917 800 1212", email: "", status: "Inactive" },
];

const documents: T.DocumentRecord[] = [
  { id: 1, title: "House Rules 2026", category: "Policies", audience: "residents", unitNo: null, fileName: "House Rules 2026.pdf", addedAt: "2026-01-10" },
  { id: 2, title: "Fire Safety Inspection Certificate", category: "Compliance", audience: "admin", unitNo: null, fileName: "BFP-FSIC-2026.pdf", addedAt: "2026-03-02" },
  { id: 3, title: "Deed of Sale – 12-01", category: "Ownership", audience: "unit", unitNo: "12-01", fileName: "Deed 12-01.pdf", addedAt: "2025-11-20" },
  { id: 4, title: "Elevator Maintenance Contract", category: "Contracts", audience: "admin", unitNo: null, fileName: "Liftmaster contract 2026.pdf", addedAt: "2026-02-14" },
];

let nextNoticeId = 1;
const announcements: T.Announcement[] = [
  ["Water interruption – October 4", "Maynilad will conduct maintenance on October 4, 9:00 AM to 3:00 PM. Please store enough water.", -1, "melissa.bautista"],
  ["Annual general assembly", "The annual assembly of unit owners is on October 25, 2:00 PM at the function room.", -6, "melissa.bautista"],
  ["Elevator 2 back in service", "Elevator 2 has been repaired and is back in service. Thank you for your patience.", -14, "melissa.bautista"],
  ["Payroll cut-off reminder (staff)", "Submit overtime and leave forms before the 13th for the 15th payroll.", -9, "jason.fernandez"],
].map(([title, message, days, by]) => ({ id: nextNoticeId++, title: title as string, message: message as string, publishDate: addDays(TODAY, days as number), audience: "residents", published: true, createdBy: by as string }));

// ------------------------------------------------------------------ HR
const employees: T.Employee[] = [
  ["Rico Dizon", "Front Desk Officer", "Administration", "22000.00"], ["Ben Salvador", "Maintenance Technician", "Engineering", "21000.00"], ["Carmela Reyes", "Admin Assistant", "Administration", "20000.00"],
  ["Rodel Manalang", "Security Guard", "Security", "19500.00"], ["Jomar Pascual", "Security Guard", "Security", "19500.00"], ["Aileen Soriano", "Housekeeping", "Housekeeping", "17500.00"],
  ["Nestor Villanueva", "Electrician", "Engineering", "24000.00"], ["Karen Uy", "Accountant", "Finance", "38000.00"], ["Melissa Bautista", "Property Manager", "Administration", "65000.00"], ["Jason Fernandez", "HR & Payroll Officer", "Human Resources", "42000.00"],
].map(([fullName, position, department, salary], i) => ({ id: i + 1, employeeNo: `EMP-${String(101 + i)}`, fullName, position, department, status: i === 5 ? "On Leave" : "Active", dateHired: `20${16 + (i % 9)}-0${1 + (i % 9)}-01`, monthlySalary: salary, contactNo: `0917${String(3300000 + i * 1111)}` }) as T.Employee);

let nextAttendanceId = 1;
const attendance: T.AttendanceRecord[] = [];
for (let d = -6; d <= 0; d++) {
  const date = addDays(TODAY, d);
  if (new Date(`${date}T00:00:00Z`).getUTCDay() === 0) continue;
  for (const e of employees) {
    if (d === 0 && e.id > 6) continue; // today: some entries still missing
    const late = (e.id + d + 14) % 6 === 0;
    const status: T.AttendanceStatus = e.status === "On Leave" ? "On Leave" : late ? "Late" : "Present";
    attendance.push({ id: nextAttendanceId++, employeeId: e.id, employeeName: e.fullName, date, timeIn: status === "On Leave" ? null : late ? "08:22" : "07:55", timeOut: d === 0 || status === "On Leave" ? null : "17:04", status, lateMinutes: late ? 22 : 0 });
  }
}

const leave: T.LeaveRequest[] = [
  { id: 1, employeeId: 6, employeeName: "Aileen Soriano", leaveType: "Sick Leave", from: addDays(TODAY, -2), to: addDays(TODAY, 1), days: 4, reason: "Dengue, with medical certificate", status: "Approved" },
  { id: 2, employeeId: 4, employeeName: "Rodel Manalang", leaveType: "Vacation Leave", from: addDays(TODAY, 10), to: addDays(TODAY, 12), days: 3, reason: "Family event in Pampanga", status: "Pending" },
  { id: 3, employeeId: 2, employeeName: "Ben Salvador", leaveType: "Emergency Leave", from: addDays(TODAY, 2), to: addDays(TODAY, 2), days: 1, reason: "Child's school emergency", status: "Pending" },
  { id: 4, employeeId: 3, employeeName: "Carmela Reyes", leaveType: "Vacation Leave", from: addDays(TODAY, -30), to: addDays(TODAY, -28), days: 3, reason: "Personal", status: "Rejected" },
];
const overtime: T.OvertimeRequest[] = [
  { id: 1, employeeId: 7, employeeName: "Nestor Villanueva", date: addDays(TODAY, -2), hours: 3, reason: "Emergency pump repair", status: "Pending" },
  { id: 2, employeeId: 5, employeeName: "Jomar Pascual", date: addDays(TODAY, -4), hours: 4, reason: "Covered night shift", status: "Approved" },
  { id: 3, employeeId: 2, employeeName: "Ben Salvador", date: addDays(TODAY, -1), hours: 2, reason: "Elevator contractor escort", status: "Pending" },
];

// ------------------------------------------------------------------ administration
let nextUserId = 1;
const users: T.UserAccount[] = [
  ...(["super_admin", "admin", "manager", "staff", "accounting"] as T.Role[]).map((role) => ({ id: nextUserId++, username: PERSONAS[role].username, role, active: true, createdAt: "2026-01-05T01:00:00" })),
  { id: nextUserId++, username: "carmela.reyes", role: "staff", active: true, createdAt: "2026-02-11T02:00:00" },
  { id: nextUserId++, username: "old.cashier", role: "accounting", active: false, createdAt: "2024-07-01T01:00:00" },
];
const residentAccounts: T.ResidentAccount[] = [
  { id: 1, username: "marcus.v", unitNo: "12-01", personType: "Owner", displayName: "Marcus Villanueva", linked: true, active: true },
  { id: 2, username: "owner.501", unitNo: "5-01", personType: "Owner", displayName: people.find((p) => p.unitId === unitByNo("5-01")!.id)!.name, linked: true, active: true },
  { id: 3, username: "owner.1103", unitNo: "11-03", personType: "Owner", displayName: people.find((p) => p.unitId === unitByNo("11-03")!.id)!.name, linked: true, active: true },
  { id: 4, username: "tenant.602", unitNo: "6-02", personType: "Tenant", displayName: "Kevin Ong", linked: false, active: true },
];

let nextAuditId = 1;
const auditLogs: T.AuditLog[] = [];
const audit = (action: string, username = actor(), at = nowUtc()) => auditLogs.unshift({ id: nextAuditId++, at, username, action });
[
  ["melissa.bautista", "Login", -2], ["melissa.bautista", `Generated ${residential().length} detailed bills for ${addMonths(THIS_MONTH, -1)}`, -2],
  ["karen.uy", `Issued ${orNo(receipts.at(-1)!)} - Recorded 3-month advance condo dues payment for unit 8-02`, -1], ["rico.dizon", "Created gate pass", -1],
  ["rafael.cruz", "Updated Rates & Rules", -1], ["marcus.v", "Requested Move-in gate pass for unit 12-01", -1], ["jason.fernandez", "Approved overtime #2", 0],
].forEach(([u, a, d]) => audit(a as string, u as string, `${addDays(TODAY, d as number)}T0${3 + nextAuditId % 5}:1${nextAuditId}:00`));
auditLogs.sort((a, b) => b.at.localeCompare(a.at));

// ------------------------------------------------------------------ mappers
const soaRow = (b: MockBill): T.SoaRow => ({ id: b.id, billingMonth: b.month, dueDate: b.dueDate, status: statusOf(b), total: fromCents(totalCents(b)), amountPaid: fromCents(b.paid), balance: fromCents(Math.max(balanceCents(b), 0)) });
const billRow = (b: MockBill): T.BillRow => {
  const u = unitById(b.unitId);
  return { ...soaRow(b), unitId: u.id, unitNo: u.unitNo, unitType: u.type, payerName: payerName(u.id) };
};
function soaDetail(b: MockBill): T.SoaDetail {
  const r = readingFor(b.unitId, b.month);
  return {
    ...soaRow(b),
    charges: {
      condoDues: fromCents(b.condo), parking: fromCents(b.parking), storage: fromCents(b.storage), storageIncluded: storageIncluded(b), storageFrom: rates.storageInTotalFrom,
      water: fromCents(b.water), waterUsage: r ? Math.max(r.current - r.previous, 0) : null, waterRate: r ? fromCents(r.rateCents) : null, waterPaidSeparately: !waterIncluded(b),
      penalty: fromCents(b.penalty), previousBalance: fromCents(b.previous), advanceApplied: fromCents(b.advance), currentCharges: fromCents(currentCharges(b)),
    },
    note: b.note,
    payments: payments.filter((p) => p.billId === b.id).map((p) => ({ date: p.date, amount: fromCents(p.amountCents), receiptNo: receiptFor("bill", p.id), method: p.method, type: p.type, reference: p.reference })),
  };
}
const billDetail = (b: MockBill): T.BillDetail => ({ ...soaDetail(b), unitId: b.unitId, unitNo: unitById(b.unitId).unitNo, payerName: payerName(b.unitId), manualOverride: b.manual });
function receiptFor(kind: T.ReceiptItem["kind"], refId: number) {
  const r = receipts.find((x) => x.items.some((i) => i.kind === kind && i.refId === refId));
  return r ? orNo(r) : null;
}
function itemOf(i: MockReceipt["items"][number]): T.ReceiptItem {
  if (i.kind === "bill") {
    const month = bills.find((b) => b.id === payments.find((p) => p.id === i.refId)!.billId)!.month;
    return { kind: "bill", label: `Statement of Account — ${month}`, month, amount: fromCents(i.cents) };
  }
  if (i.kind === "water") {
    const month = readings.find((r) => r.id === i.refId)!.month;
    return { kind: "water", label: `Water — ${month}`, month, amount: fromCents(i.cents) };
  }
  const month = advances.find((a) => a.id === i.refId)!.startMonth;
  return { kind: "advance", label: `Advance condo dues from ${month}`, month, amount: fromCents(i.cents) };
}
const receiptRow = (r: MockReceipt): T.ReceiptRow => ({ id: r.id, receiptNo: orNo(r), date: r.date, amount: fromCents(r.amountCents), method: r.method, reference: r.reference, items: r.items.map(itemOf) });
const officialReceipt = (r: MockReceipt): T.OfficialReceipt => ({ ...receiptRow(r), unitId: r.unitId, unitNo: unitById(r.unitId).unitNo, receivedBy: r.receivedBy, backfilled: r.backfilled });
const unitOut = (u: MockUnit): T.Unit => {
  const owner = currentOwner(u.id), tenant = currentTenant(u.id);
  return { id: u.id, unitNo: u.unitNo, floor: u.floor, type: u.type, areaSqm: u.areaSqm, ratePerSqm: fromCents(u.ratePerSqmCents ?? rateFor(u.type)), occupancy: u.occupancy, status: u.status, active: u.active,
    ownerName: owner?.name ?? null, tenantName: tenant?.name ?? null, contactNo: (tenant ?? owner)?.contactNo ?? null, email: (tenant ?? owner)?.email ?? null,
    parkingUnitNo: u.parkingId ? unitById(u.parkingId).unitNo : null, storageUnitNo: u.storageId ? unitById(u.storageId).unitNo : null,
    monthlyDues: RESIDENTIAL.includes(u.type) ? fromCents(condoCents(u) + assetCents(u.parkingId, toCents(rates.parkingRatePerSqm))) : "0.00" };
};
const readingOut = (r: MockReading): T.WaterReadingAdmin => ({ id: r.id, unitId: r.unitId, unitNo: unitById(r.unitId).unitNo, month: r.month, previous: r.previous, current: r.current, rate: fromCents(r.rateCents),
  usage: Math.max(r.current - r.previous, 0), amount: fromCents(readingCents(r)), readingDate: r.readingDate, paid: r.paidCents > 0 });
const advanceOut = (a: MockAdvance): T.AdvancePayment => ({ id: a.id, unitId: a.unitId, unitNo: unitById(a.unitId).unitNo, date: a.date, amount: fromCents(a.amountCents), startMonth: a.startMonth, months: a.months,
  monthlyAmount: fromCents(Math.floor(a.amountCents / a.months)), method: a.method, reference: a.reference, applied: fromCents(a.appliedCents), remaining: fromCents(a.amountCents - a.appliedCents), receiptNo: receiptFor("advance", a.id) ?? "" });
const residentPass = ({ id, type, date, name, purpose, status, reviewNote, requestedAt }: T.AdminGatePass): T.ResidentGatePass => ({ id, type, date, name, purpose, status, reviewNote, requestedAt });

function collectionsFor(month: string): T.CollectionSummary {
  const monthBills = bills.filter((b) => b.month === month);
  const billed = monthBills.reduce((s, b) => s + currentCharges(b) + b.penalty, 0);
  const collected = receipts.filter((r) => r.date.startsWith(month)).reduce((s, r) => s + r.amountCents, 0);
  const latest = residential().map((u) => bills.filter((b) => b.unitId === u.id && b.month <= month).at(-1)).filter(Boolean) as MockBill[];
  const outstanding = latest.reduce((s, b) => s + Math.max(balanceCents(b), 0), 0);
  return { month, billed: fromCents(billed), collected: fromCents(collected), outstanding: fromCents(outstanding), collectionRate: billed ? Math.min(collected / billed, 1) : 0 };
}

const needResidentUnit = (unitId: number) => (unitId === RESIDENT_UNIT.id || currentRole !== "resident" ? null : fail(403, "You can only view your own unit."));
const refRequired = (method: T.PaymentMethod, reference: string) => (method === "CHECK" || method === "ONLINE") && !reference.trim();

// ------------------------------------------------------------------ the service
export const mockApi: DataService = {
  auth: {
    me: () => {
      if (!currentRole) return fail(401, "Not signed in.");
      const cfg = ROLES[currentRole];
      return wait({ username: PERSONAS[cfg.role].username, role: currentRole, roleLabel: cfg.label, portal: cfg.portal, unitId: currentRole === "resident" ? RESIDENT_UNIT.id : null,
        permissions: permissionsForRole(currentRole), displayName: PERSONAS[cfg.role].displayName, email: PERSONAS[cfg.role].email, subtitle: PERSONAS[cfg.role].subtitle });
    },
    login: async (username) => {
      const cfg = Object.values(ROLES).find((c) => PERSONAS[c.role].username === username.trim().toLowerCase());
      if (!cfg) return fail(401, "Invalid username or password.");
      setMockRole(cfg.role);
      audit("Login", PERSONAS[cfg.role].username);
      return { user: await mockApi.auth.me(), csrfToken: null };
    },
    logout: async () => {
      audit("Logout");
      setMockRole(null);
    },
    changePassword: (current, next) => {
      if (!current) return fail(400, "Current password is incorrect.");
      if (next.length < 8) return fail(400, "New password must be at least 8 characters.");
      if (next === current) return fail(400, "New password must be different from the current password.");
      audit(`Changed password for user ${actor()}`);
      return wait(undefined);
    },
  },

  resident: {
    summary: (unitId) => needResidentUnit(unitId) ?? (() => {
      const u = unitById(unitId);
      const latest = bills.filter((b) => b.unitId === unitId).sort((a, b) => b.month.localeCompare(a.month))[0];
      return wait({ unit: { id: u.id, unitNo: u.unitNo, floor: u.floor, type: u.type, status: u.status }, residentName: PERSONAS.resident.displayName,
        outstandingBalance: latest ? soaRow(latest).balance : "0.00", latestSoa: latest ? soaRow(latest) : null,
        openTickets: tickets.filter((t) => t.unitId === unitId && t.status !== "Closed").length });
    })(),
    statements: (unitId) => needResidentUnit(unitId) ?? wait(bills.filter((b) => b.unitId === unitId).sort((a, b) => b.month.localeCompare(a.month)).map(soaRow)),
    statement: (unitId, billId) => {
      const b = bills.find((x) => x.id === billId);
      if (!b || b.unitId !== unitId) return fail(404, "Statement not found.");
      const u = unitById(unitId);
      return needResidentUnit(unitId) ?? wait({ unit: { id: u.id, unitNo: u.unitNo, type: u.type }, corporation: rates.corporationName, statement: soaDetail(b) });
    },
    receipts: (unitId) => {
      const rows = receipts.filter((r) => r.unitId === unitId).sort((a, b) => b.date.localeCompare(a.date) || b.id - a.id);
      return needResidentUnit(unitId) ?? wait({ receipts: rows.map(receiptRow), totalPaid: fromCents(rows.reduce((s, r) => s + r.amountCents, 0)) });
    },
    receipt: (unitId, receiptId) => {
      const r = receipts.find((x) => x.id === receiptId);
      if (!r || r.unitId !== unitId) return fail(404, "Receipt not found.");
      return needResidentUnit(unitId) ?? wait({ corporation: rates.corporationName, address: rates.address, unit: { id: unitId, unitNo: unitById(unitId).unitNo }, receivedFrom: payerName(unitId),
        receipt: { ...receiptRow(r), remarks: r.remarks, receivedBy: r.receivedBy, backfilled: r.backfilled } });
    },
    water: (unitId) => needResidentUnit(unitId) ?? wait(readings.filter((r) => r.unitId === unitId).sort((a, b) => b.month.localeCompare(a.month)).slice(0, 24).map((r) => ({
      month: r.month, readingDate: r.readingDate, previous: r.previous, current: r.current, usage: Math.max(r.current - r.previous, 0), rate: fromCents(r.rateCents), amount: fromCents(readingCents(r)), paidSeparately: r.paidCents > 0 }))),
    tickets: (unitId) => needResidentUnit(unitId) ?? wait({ tickets: tickets.filter((t) => t.unitId === unitId).map(({ ticketNo, category, title, description, priority, status, resolution, requestedAt }) =>
      ({ ticketNo, category, title, description, priority, status, resolution, requestedAt })), categories: ["General", "Plumbing", "Electrical", "Aircon", "Common Area", "Other"], priorities: ["Normal", "Low", "High", "Urgent"] }),
    fileTicket: (unitId, input) => {
      const denied = needResidentUnit(unitId);
      if (denied) return denied;
      if (!input.title.trim() || !input.description.trim()) return fail(400, "Title and description are required.");
      const u = unitById(unitId);
      const t = { id: nextTicketId, unitId, unitNo: u.unitNo, ticketNo: `MT-${TODAY.replace(/-/g, "")}${String(nextTicketId++).padStart(4, "0")}`, ...input, status: "Open" as const, assignedTo: "", vendor: "", resolution: "", requestedAt: nowUtc() };
      tickets.unshift(t);
      audit(`Created maintenance ticket ${t.ticketNo}`);
      return wait({ ticketNo: t.ticketNo, category: t.category, title: t.title, description: t.description, priority: t.priority, status: t.status, resolution: "", requestedAt: t.requestedAt });
    },
    gatePasses: (unitId) => needResidentUnit(unitId) ?? wait({ passes: gatePasses.filter((p) => p.unitId === unitId).map(residentPass), types: ["Visitor", "Delivery", "Move-in", "Move-out"], maxDaysAhead: 90 }),
    requestGatePass: (unitId, input) => {
      const denied = needResidentUnit(unitId);
      if (denied) return denied;
      if (!input.name.trim() || !input.purpose.trim()) return fail(400, "Name and details are required.");
      if (input.date < TODAY || input.date > addDays(TODAY, 90)) return fail(400, "The date must be between today and 90 days from now.");
      const p = addPass({ ...input, unitNo: unitById(unitId).unitNo, status: "Requested", reviewNote: "", requestedAt: nowUtc(), requestedBy: actor(), reviewedBy: null, source: "resident" });
      audit(`Requested ${input.type} gate pass #${p.id} for unit ${p.unitNo}`);
      return wait(residentPass(p));
    },
    cancelGatePass: (unitId, passId) => {
      const denied = needResidentUnit(unitId);
      if (denied) return denied;
      const p = gatePasses.find((x) => x.id === passId);
      if (!p || p.unitId !== unitId) return fail(404, "Request not found.");
      if (p.status !== "Requested") return fail(409, `This request is already ${p.status.toLowerCase()} and can no longer be cancelled.`);
      p.status = "Cancelled";
      audit(`Cancelled gate pass request #${p.id}`);
      return wait(residentPass(p));
    },
    profile: (unitId) => {
      const u = unitById(unitId);
      const owner = currentOwner(unitId)!;
      return needResidentUnit(unitId) ?? wait({ username: actor(), displayName: owner.name, unit: { unitNo: u.unitNo, floor: u.floor, type: u.type, areaSqm: u.areaSqm }, personType: "Owner",
        name: owner.name, contactNo: owner.contactNo, email: owner.email, moveIn: owner.moveIn, canEdit: true });
    },
    updateContact: async (unitId, input) => {
      const denied = needResidentUnit(unitId);
      if (denied) return denied;
      if (input.contactNo.length > 80 || !/^[0-9+()\-\s/]*$/.test(input.contactNo)) return fail(400, "Enter a valid contact number (digits, spaces, + ( ) - /).");
      if (input.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(input.email)) return fail(400, "Enter a valid email address.");
      const owner = currentOwner(unitId)!;
      owner.contactNo = input.contactNo;
      owner.email = input.email;
      audit(`Resident ${actor()} updated own contact details (unit ${unitById(unitId).unitNo})`);
      return mockApi.resident.profile(unitId);
    },
    notices: () => wait(announcements.filter((a) => a.published).map(({ id, title, message, publishDate }) => ({ id, title, message, publishDate }))),
  },

  dashboards: {
    property: () => {
      const res = residential();
      const floors = [...new Set(res.map((u) => u.floor))];
      const monthBills = bills.filter((b) => b.month === THIS_MONTH);
      const count = (s: T.BillStatus) => monthBills.filter((b) => statusOf(b) === s).length;
      return wait({
        month: THIS_MONTH,
        units: { residential: res.length, occupied: res.filter((u) => u.status === "Occupied").length, vacant: res.filter((u) => u.status === "Vacant").length,
          parking: units.filter((u) => u.type === "PARKING").length, storage: units.filter((u) => u.type === "STORAGE").length },
        occupancyByFloor: floors.map((floor) => ({ floor, occupied: res.filter((u) => u.floor === floor && u.status === "Occupied").length, vacant: res.filter((u) => u.floor === floor && u.status === "Vacant").length })),
        billing: { generated: monthBills.length > 0, bills: monthBills.length, paid: count("Paid"), partial: count("Partially Paid"), overdue: count("Overdue"), unpaid: count("Unpaid") },
        collections: [5, 4, 3, 2, 1, 0].map((n) => collectionsFor(addMonths(THIS_MONTH, -n))),
        pendingGatePasses: gatePasses.filter((p) => p.status === "Requested").length,
        openTickets: tickets.filter((t) => t.status === "Open" || t.status === "In Progress").length,
        waterReadingsEntered: readings.filter((r) => r.month === THIS_MONTH).length,
      });
    },
    hr: () => {
      const today = attendance.filter((a) => a.date === TODAY);
      return wait({ headcount: employees.filter((e) => e.status !== "Resigned").length, presentToday: today.filter((a) => a.status === "Present" || a.status === "Late").length,
        lateToday: today.filter((a) => a.status === "Late").length, onLeave: employees.filter((e) => e.status === "On Leave").length,
        pendingLeave: leave.filter((l) => l.status === "Pending").length, pendingOvertime: overtime.filter((o) => o.status === "Pending").length,
        monthlyPayroll: fromCents(employees.reduce((s, e) => s + toCents(e.monthlySalary), 0)) });
    },
    staff: () => wait({
      gatePassesToday: gatePasses.filter((p) => p.date === TODAY && p.status === "Issued").length,
      pendingRequests: gatePasses.filter((p) => p.status === "Requested").length,
      openTickets: tickets.filter((t) => t.status === "Open" || t.status === "In Progress").length,
      expensesThisMonth: fromCents(expenses.filter((e) => e.date.startsWith(THIS_MONTH)).reduce((s, e) => s + toCents(e.amount), 0)),
      certificatesThisMonth: certificates.filter((c) => c.certificateDate.startsWith(THIS_MONTH)).length,
      attendanceEntered: attendance.filter((a) => a.date === TODAY).length,
      attendanceExpected: employees.filter((e) => e.status === "Active").length,
    }),
    system: () => wait({
      users: Object.fromEntries((["super_admin", "admin", "manager", "staff", "accounting", "resident"] as T.Role[]).map((r) => [r, r === "resident" ? residentAccounts.length : users.filter((u) => u.role === r && u.active).length])) as Record<T.Role, number>,
      residentAccounts: residentAccounts.length, unlinkedResidentAccounts: residentAccounts.filter((a) => !a.linked).length,
      auditToday: auditLogs.filter((a) => a.at.startsWith(TODAY)).length, lastBackup: `${addDays(TODAY, -1)}T10:00:00`, schemaRevision: "0007_gate_pass_requests",
    }),
  },

  units: {
    list: () => wait(units.map(unitOut)),
    people: (unitId) => wait(people.filter((p) => p.unitId === unitId).map(({ id, type, name, status, moveIn, moveOut }) => ({ id, type, name, status, moveIn, moveOut }))),
  },

  billing: {
    list: (month) => wait(bills.filter((b) => b.month === month).sort((a, b) => unitById(a.unitId).unitNo.localeCompare(unitById(b.unitId).unitNo, undefined, { numeric: true })).map(billRow)),
    detail: (billId) => {
      const b = bills.find((x) => x.id === billId);
      return b ? wait(billDetail(b)) : fail(404, "Bill not found.");
    },
    generate: (month) => {
      if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) return fail(400, "Invalid billing month.");
      let created = 0, skipped = 0;
      for (const u of residential()) {
        if (bills.some((b) => b.unitId === u.id && b.month === month)) skipped++;
        else { createBill(u, month); created++; }
      }
      audit(`Generated ${created} detailed bills for ${month}`);
      return wait({ month, created, skipped });
    },
    recordPayment: (billId, input) => {
      const b = bills.find((x) => x.id === billId);
      if (!b) return fail(404, "Bill not found.");
      if (!isAmount(input.amount)) return fail(400, "Please enter a payment amount.");
      if (!["CASH", "CHECK", "ONLINE"].includes(input.method)) return fail(400, "Invalid payment method.");
      if (refRequired(input.method, input.reference)) return fail(400, "Reference number is required for Check or Online payments.");
      const { receipt, applied, excess } = pay(b, toCents(input.amount), input.date, input.method, input.reference.trim(), input.remarks, input.type);
      audit(`Issued ${orNo(receipt)} - Recorded ${input.type.toLowerCase()} ${input.method.toLowerCase()} payment for unit ${unitById(b.unitId).unitNo}, billing ${b.month}: applied ₱${fromCents(applied)}` + (excess ? `, excess ₱${fromCents(excess)} moved to advance.` : ""));
      return wait({ receiptNo: orNo(receipt), appliedToBill: fromCents(applied), excessToAdvance: fromCents(excess), status: statusOf(b) });
    },
    recalculate: (billId) => {
      const b = bills.find((x) => x.id === billId);
      if (!b) return fail(404, "Bill not found.");
      if (b.manual) return fail(409, "This SOA was corrected by hand. Use Edit SOA to change its amounts.");
      const u = unitById(b.unitId);
      const before = [b.condo, b.parking, b.storage].join();
      b.condo = condoCents(u);
      b.parking = assetCents(u.parkingId, toCents(rates.parkingRatePerSqm));
      b.storage = assetCents(u.storageId, toCents(rates.storageRatePerSqm));
      const changed = before !== [b.condo, b.parking, b.storage].join();
      if (changed) audit(`Recalculated SOA for unit ${u.unitNo}, billing ${b.month} from current rates`);
      return wait({ changed, detail: billDetail(b) });
    },
    emailSoas: (month) => {
      if (!rates.smtpHost || !rates.smtpSender) return fail(400, "Configure SMTP Host and Sender Email under Rates & Rules before sending email.");
      const n = bills.filter((b) => b.month === month).length;
      audit(`Sent SOA emails for ${month}`);
      return wait({ sent: n, skipped: 0 });
    },
  },

  advances: {
    list: () => wait([...advances].sort((a, b) => b.date.localeCompare(a.date) || b.id - a.id).map(advanceOut)),
    record: (input) => {
      if (!unitById(input.unitId)) return fail(400, "Please select a valid unit.");
      if (!isAmount(input.amount) || input.months < 1) return fail(400, "Advance amount and coverage months are required.");
      if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(input.startMonth)) return fail(400, "Please enter a valid start billing month.");
      if (refRequired(input.method, input.reference)) return fail(400, "Reference number is required for Check or Online payments.");
      const a: MockAdvance = { id: nextAdvanceId++, unitId: input.unitId, date: input.date, amountCents: toCents(input.amount), startMonth: input.startMonth, months: input.months, method: input.method, reference: input.reference.trim(), appliedCents: 0 };
      advances.push(a);
      bills.filter((b) => b.unitId === a.unitId && b.month >= a.startMonth).sort((x, y) => x.month.localeCompare(y.month)).forEach(applyAdvances);
      const receipt = issueReceipt(a.unitId, a.date, a.method, a.reference, input.remarks, [{ kind: "advance", refId: a.id, cents: a.amountCents }]);
      audit(`Issued ${orNo(receipt)} - Recorded ${a.months}-month advance condo dues payment for unit ${unitById(a.unitId).unitNo}: ${fromCents(a.amountCents)}`);
      return wait(advanceOut(a));
    },
  },

  receipts: {
    list: ({ from, to, q }) => {
      const needle = q.trim().toLowerCase();
      return wait(receipts.filter((r) => r.date >= from && r.date <= to)
        .map(officialReceipt)
        .filter((r) => !needle || [r.receiptNo, r.unitNo, r.reference].some((v) => v.toLowerCase().includes(needle)))
        .sort((a, b) => b.receiptNo.localeCompare(a.receiptNo)));
    },
  },

  water: {
    list: (month) => wait(readings.filter((r) => r.month === month).map(readingOut).sort((a, b) => a.unitNo.localeCompare(b.unitNo, undefined, { numeric: true }))),
    previousReading: (unitId, month) => wait(readings.filter((r) => r.unitId === unitId && r.month < month).sort((a, b) => b.month.localeCompare(a.month))[0]?.current ?? 0),
    save: (input) => {
      if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(input.month)) return fail(400, "Invalid reading month.");
      if (input.current < input.previous) return fail(400, "The current reading can't be lower than the previous reading.");
      if (!isAmount(input.rate)) return fail(400, "Enter the water rate per cubic meter.");
      let r = readingFor(input.unitId, input.month);
      if (!r) readings.push((r = { id: nextReadingId++, unitId: input.unitId, month: input.month, previous: 0, current: 0, rateCents: 0, readingDate: input.readingDate, paidCents: 0 }));
      Object.assign(r, { previous: input.previous, current: input.current, rateCents: toCents(input.rate), readingDate: input.readingDate });
      const bill = bills.find((b) => b.unitId === input.unitId && b.month === input.month);
      if (bill) bill.water = readingCents(r); // the month's bill follows the corrected reading
      audit(`Saved water reading for unit ${unitById(input.unitId).unitNo}, ${input.month}`);
      return wait(readingOut(r));
    },
  },

  gatePasses: {
    list: () => wait(gatePasses.map(({ unitId: _u, ...p }) => p)),
    issue: (input) => {
      const u = unitByNo(input.unitNo);
      if (!u) return fail(400, "Unit not found. Use the unit number, e.g. 12-01.");
      if (!input.name.trim() || !input.purpose.trim()) return fail(400, "Name and purpose are required.");
      const p = addPass({ ...input, unitNo: u.unitNo, status: "Issued", reviewNote: "", requestedAt: null, requestedBy: null, reviewedBy: actor(), source: "office" });
      audit("Created gate pass");
      const { unitId: _u, ...out } = p;
      return wait(out);
    },
    review: (passId, decision, note) => {
      const p = gatePasses.find((x) => x.id === passId);
      if (!p || p.status !== "Requested") return fail(409, "That request was not found or was already handled.");
      if (decision === "reject" && !note.trim()) return fail(400, "Give the resident a reason when rejecting a request.");
      p.status = decision === "approve" ? "Issued" : "Rejected";
      p.reviewNote = note.trim();
      p.reviewedBy = actor();
      audit(`${decision === "approve" ? "Approved" : "Rejected"} gate pass request #${p.id} (${p.type}, unit ${p.unitNo})`);
      const { unitId: _u, ...out } = p;
      return wait(out);
    },
  },

  certificates: {
    list: () => wait([...certificates].sort((a, b) => b.id - a.id)),
    generate: (input) => {
      const person = people.find((p) => p.id === input.personId && p.unitId === input.unitId && p.type === input.personType);
      if (!person) return fail(400, "Choose the owner or tenant for this certificate.");
      // Bug D1: the server crashes while saving the certificate (issued_by=current_user.username).
      if (SIMULATE_D1) return fail(500, "Server error. The error was logged.");
      const c: T.Certificate = { id: nextCertId, certificateNo: `MC-2026-${String(nextCertId++ + 6).padStart(4, "0")}`, moveType: input.moveType, personType: input.personType, personName: person.name,
        unitNo: unitById(input.unitId).unitNo, moveDate: input.moveType === "Move In" ? person.moveIn : person.moveOut, certificateDate: input.certificateDate, issuedBy: actor() };
      certificates.push(c);
      audit(`Generated ${c.moveType} certificate ${c.certificateNo}`);
      return wait(c);
    },
  },

  expenses: {
    list: (month) => wait(expenses.filter((e) => e.date.startsWith(month)).sort((a, b) => b.date.localeCompare(a.date))),
    add: (input) => {
      if (!input.description.trim() || !input.category.trim()) return fail(400, "Category and description are required.");
      if (!isAmount(input.amount)) return fail(400, "Enter the amount.");
      const e = { ...input, id: nextExpenseId++, amount: fromCents(toCents(input.amount)) };
      expenses.push(e);
      audit(`Recorded expense: ${e.category} ${e.amount}`);
      return wait(e);
    },
  },

  maintenance: {
    list: () => wait(tickets.map(({ unitId: _u, ...t }) => t)),
    update: (id, input) => {
      const t = tickets.find((x) => x.id === id);
      if (!t) return fail(404, "Maintenance ticket not found.");
      Object.assign(t, input);
      audit(`Updated maintenance ticket ${t.ticketNo}`);
      const { unitId: _u, ...out } = t;
      return wait(out);
    },
  },

  vendors: { list: () => wait(vendors) },
  documents: { list: () => wait(documents) },

  announcements: {
    list: () => wait([...announcements].sort((a, b) => (b.publishDate ?? "").localeCompare(a.publishDate ?? ""))),
    publish: (input) => {
      if (!input.title.trim() || !input.message.trim()) return fail(400, "Title and message are required.");
      const a: T.Announcement = { id: nextNoticeId++, ...input, publishDate: TODAY, published: true, createdBy: actor() };
      announcements.push(a);
      audit(`Published announcement "${a.title}"`);
      return wait(a);
    },
  },

  hr: {
    employees: () => wait(employees),
    attendance: (date) => wait(attendance.filter((a) => a.date === date)),
    saveAttendance: (input) => {
      const e = employees.find((x) => x.id === input.employeeId);
      if (!e) return fail(400, "Choose an employee.");
      const late = input.timeIn && input.timeIn > "08:00" ? (Number(input.timeIn.slice(0, 2)) - 8) * 60 + Number(input.timeIn.slice(3, 5)) : 0;
      let row = attendance.find((a) => a.employeeId === input.employeeId && a.date === input.date);
      if (!row) attendance.push((row = { id: nextAttendanceId++, employeeId: e.id, employeeName: e.fullName, date: input.date, timeIn: null, timeOut: null, status: "Present", lateMinutes: 0 }));
      Object.assign(row, input, { lateMinutes: input.status === "Late" || late > 0 ? late : 0, status: input.status === "Present" && late > 0 ? "Late" : input.status });
      audit(`Recorded attendance for ${e.fullName} on ${input.date}`);
      return wait(row);
    },
    leave: () => wait(leave),
    overtime: () => wait(overtime),
    // D5: requests are always created Pending; only these approval actions change the status.
    decideLeave: (id, status) => {
      const l = leave.find((x) => x.id === id);
      if (!l || l.status !== "Pending") return fail(409, "This request was already decided.");
      l.status = status;
      audit(`${status} leave #${id} for ${l.employeeName}`);
      return wait(l);
    },
    decideOvertime: (id, status) => {
      const o = overtime.find((x) => x.id === id);
      if (!o || o.status !== "Pending") return fail(409, "This request was already decided.");
      o.status = status;
      audit(`${status} overtime #${id} for ${o.employeeName}`);
      return wait(o);
    },
    payroll: (period) => wait(employees.filter((e) => e.status !== "Resigned").map((e, i) => {
      const otHours = overtime.filter((o) => o.employeeId === e.id && o.status === "Approved").reduce((s, o) => s + o.hours, 0);
      const hourly = toCents(e.monthlySalary) / 100 / 22 / 8;
      const p = previewPayroll({ basicMonthly: toCents(e.monthlySalary) / 100, overtimePay: Math.round(otHours * hourly * 1.25 * 100) / 100, taxableAllowances: 0, nonTaxableAllowances: 1500, lateUndertimeDeduction: 0, loanDeductions: i % 4 === 0 ? 1200 : 0 });
      return { id: i + 1, employeeId: e.id, employeeName: e.fullName, periodStart: `${period}-01`, periodEnd: `${period}-30`, basic: e.monthlySalary, overtime: fromCents(Math.round(otHours * hourly * 1.25 * 100)),
        allowances: "1500.00", deductions: p.totalDeductions, netPay: p.netPay, status: period < THIS_MONTH ? "Released" : "Draft" } as T.PayrollRecord;
    })),
  },

  admin: {
    users: () => wait(users),
    createUser: (input) => {
      if (!input.username.trim()) return fail(400, "Username and password are required.");
      if (users.some((u) => u.username === input.username.trim())) return fail(400, "Username already exists.");
      if (!ROLES[input.role]) return fail(400, "Please choose a valid role.");
      if (input.password.length < 8) return fail(400, "Password must be at least 8 characters.");
      const u: T.UserAccount = { id: nextUserId++, username: input.username.trim(), role: input.role, active: true, createdAt: nowUtc() };
      users.push(u);
      audit(`Created user ${u.username} (${u.role})`);
      return wait(u);
    },
    residentAccounts: () => wait(residentAccounts),
    auditLogs: (q) => {
      const needle = q.trim().toLowerCase();
      return wait(auditLogs.filter((a) => !needle || a.action.toLowerCase().includes(needle) || a.username.toLowerCase().includes(needle)).slice(0, 300));
    },
    rates: () => wait(rates),
    saveRates: (input) => {
      const { smtpPassword, ...rest } = input;
      Object.assign(rates, rest, { smtpPasswordSet: rates.smtpPasswordSet || smtpPassword.length > 0 });
      audit("Updated Rates & Rules");
      return wait(rates);
    },
    collections: (months) => wait(Array.from({ length: months }, (_, i) => collectionsFor(addMonths(THIS_MONTH, i - months + 1)))),
  },
};
