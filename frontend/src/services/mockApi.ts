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
import { RULE_DEFAULTS, attendanceDeductions, lateMinutes, statutory, undertimeMinutes } from "../lib/payroll";
import { permissionsForRole } from "../config/permissions";
import { ROLES } from "../config/roles";
import { PERSONAS } from "./personas";
import type { DataService } from "./api";
import { ApiError } from "./http";
import type * as T from "./types";

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
  booksClosedThrough: "",
  dueDay: 8,
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
  { id: 1, certificateNo: "CL9-2026-00001", moveType: "Move In", personType: "Tenant", personName: "Kevin Ong", unitNo: "6-02", moveDate: "2026-06-01", certificateDate: "2026-06-01", issuedBy: "rico.dizon" },
  { id: 2, certificateNo: "CL9-2026-00002", moveType: "Move Out", personType: "Tenant", personName: "Arnel Pineda", unitNo: "9-01", moveDate: "2025-05-31", certificateDate: "2026-06-03", issuedBy: "rico.dizon" },
  { id: 3, certificateNo: "CL9-2026-00003", moveType: "Move In", personType: "Owner", personName: "Beatriz Tan", unitNo: "11-03", moveDate: "2026-08-15", certificateDate: "2026-08-15", issuedBy: "melissa.bautista" },
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
  const v = 1 + ["AquaFix", "CoolAir"].findIndex((n) => (vendor as string).startsWith(n));
  return { id: nextTicketId, unitId: u.id, unitNo: u.unitNo, ticketNo: `MT-${d.replace(/-/g, "")}-${String(nextTicketId++).padStart(5, "0")}`, category: category as string, title: title as string,
    description: description as string, priority: priority as string, status: status as T.TicketStatus, assignedTo: assignedTo as string, vendorId: v || null,
    vendor: v ? ["AquaFix Plumbing Services", "CoolAir Services"][v - 1] : "", resolution: resolution as string, requestedAt: `${d}T02:30:00`, source: "resident" as const };
});
const TICKET_CATEGORIES = ["General", "Plumbing", "Electrical", "Aircon", "Common Area", "Other"];
const TICKET_PRIORITIES = ["Low", "Normal", "High", "Urgent"];

let nextVendorId = 6;
const vendors: T.Vendor[] = ([
  ["AquaFix Plumbing Services", "Plumbing", "Ramil Santos", "0917 555 0142", "service@aquafix.ph", "Active"],
  ["CoolAir Services", "Aircon", "Joy Lagman", "0918 222 7831", "joy@coolair.ph", "Active"],
  ["Brightline Electrical", "Electrical", "Noel Garcia", "0922 413 0099", "noel@brightline.ph", "Active"],
  ["Liftmaster Elevators", "Elevator maintenance", "Carla Uy", "(02) 8812 3344", "carla@liftmaster.ph", "Active"],
  ["PestAway Inc.", "Pest control", "Dennis Co", "0917 800 1212", "", "Inactive"],
] as const).map(([name, serviceType, contactPerson, contactNo, email, status], i) => ({ id: i + 1, name, serviceType, contactPerson, contactNo, email, address: "", notes: "", status }));

let nextDocId = 5;
const documents: T.DocumentRecord[] = [
  { id: 1, title: "House Rules 2026", category: "Policies", description: "", audience: "residents", unitId: null, unitNo: null, fileName: "House Rules 2026.pdf", filePath: "\\\\ADMIN-PC\\Shared\\Policies", addedAt: "2026-01-10", addedBy: "melissa.bautista" },
  { id: 2, title: "Fire Safety Inspection Certificate", category: "Compliance", description: "", audience: "admin", unitId: null, unitNo: null, fileName: "BFP-FSIC-2026.pdf", filePath: "Cabinet A, folder 3", addedAt: "2026-03-02", addedBy: "melissa.bautista" },
  { id: 3, title: "Deed of Sale – 12-01", category: "Ownership", description: "", audience: "unit", unitId: unitByNo("12-01")!.id, unitNo: "12-01", fileName: "Deed 12-01.pdf", filePath: "Cabinet B, unit folders", addedAt: "2025-11-20", addedBy: "melissa.bautista" },
  { id: 4, title: "Elevator Maintenance Contract", category: "Contracts", description: "", audience: "admin", unitId: null, unitNo: null, fileName: "Liftmaster contract 2026.pdf", filePath: "Cabinet A, folder 7", addedAt: "2026-02-14", addedBy: "melissa.bautista" },
];

let nextNoticeId = 1;
const announcements: T.Announcement[] = [
  ["Water interruption – October 4", "Maynilad will conduct maintenance on October 4, 9:00 AM to 3:00 PM. Please store enough water.", -1, "melissa.bautista"],
  ["Annual general assembly", "The annual assembly of unit owners is on October 25, 2:00 PM at the function room.", -6, "melissa.bautista"],
  ["Elevator 2 back in service", "Elevator 2 has been repaired and is back in service. Thank you for your patience.", -14, "melissa.bautista"],
  ["Payroll cut-off reminder (staff)", "Submit overtime and leave forms before the 13th for the 15th payroll.", -9, "jason.fernandez"],
].map(([title, message, days, by]) => ({ id: nextNoticeId++, title: title as string, message: message as string, publishDate: addDays(TODAY, days as number),
  audience: (title as string).includes("(staff)") ? "staff" : "residents", published: true, createdBy: by as string }));

// ------------------------------------------------------------------ HR
const hrRules: Record<string, string> = { ...RULE_DEFAULTS };
const employees: T.Employee[] = [
  ["Rico Dizon", "Front Desk Officer", "Administration", "22000.00"], ["Ben Salvador", "Maintenance Technician", "Engineering", "21000.00"], ["Carmela Reyes", "Admin Assistant", "Administration", "20000.00"],
  ["Rodel Manalang", "Security Guard", "Security", "19500.00"], ["Jomar Pascual", "Security Guard", "Security", "19500.00"], ["Aileen Soriano", "Housekeeping", "Housekeeping", "17500.00"],
  ["Nestor Villanueva", "Electrician", "Engineering", "24000.00"], ["Karen Uy", "Accountant", "Finance", "38000.00"], ["Melissa Bautista", "Property Manager", "Administration", "65000.00"], ["Jason Fernandez", "HR & Payroll Officer", "Human Resources", "42000.00"],
].map(([fullName, position, department, salary], i) => ({ id: i + 1, employeeNo: `EMP-${String(101 + i)}`, fullName, position, department, status: i === 5 ? "On Leave" : "Active",
  dateHired: `20${16 + (i % 9)}-0${1 + (i % 9)}-01`, monthlySalary: salary, contactNo: `0917${String(3300000 + i * 1111)}`, email: "", notes: "" }) as T.Employee);
let nextEmployeeId = employees.length + 1;
const empName = (id: number) => employees.find((e) => e.id === id)?.fullName ?? `Employee #${id}`;

interface MockAttendance { id: number; employeeId: number; date: string; timeIn: string | null; timeOut: string | null; status: string; remarks: string }
let nextAttendanceId = 1;
const attendance: MockAttendance[] = [];
for (let d = -6; d <= 0; d++) {
  const date = addDays(TODAY, d);
  if (new Date(`${date}T00:00:00Z`).getUTCDay() === 0) continue;
  for (const e of employees) {
    if (d === 0 && e.id > 6) continue; // today: some entries still missing
    const late = (e.id + d + 14) % 6 === 0;
    const status = e.status === "On Leave" ? "LEAVE" : late ? "LATE" : "PRESENT";
    attendance.push({ id: nextAttendanceId++, employeeId: e.id, date, timeIn: status === "LEAVE" ? null : late ? "08:22" : "07:55", timeOut: d === 0 || status === "LEAVE" ? null : "17:04", status, remarks: "" });
  }
}
const attendanceOut = (a: MockAttendance): T.AttendanceRecord => ({ ...a, employeeName: empName(a.employeeId), lateMinutes: lateMinutes(a, Number(hrRules.hr_grace_minutes)), undertimeMinutes: undertimeMinutes(a) });

let nextLeaveId = 5, nextOvertimeId = 4, nextPayrollId = 1, nextLoanId = 3;
const leave: T.LeaveRequest[] = [
  { id: 1, employeeId: 6, employeeName: "Aileen Soriano", leaveType: "SICK", from: addDays(TODAY, -2), to: addDays(TODAY, 1), days: 4, reason: "Dengue, with medical certificate", status: "Approved", decidedBy: "jason.fernandez" },
  { id: 2, employeeId: 4, employeeName: "Rodel Manalang", leaveType: "VACATION", from: addDays(TODAY, 10), to: addDays(TODAY, 12), days: 3, reason: "Family event in Pampanga", status: "Pending", decidedBy: "" },
  { id: 3, employeeId: 2, employeeName: "Ben Salvador", leaveType: "EMERGENCY", from: addDays(TODAY, 2), to: addDays(TODAY, 2), days: 1, reason: "Child's school emergency", status: "Pending", decidedBy: "" },
  { id: 4, employeeId: 3, employeeName: "Carmela Reyes", leaveType: "VACATION", from: addDays(TODAY, -30), to: addDays(TODAY, -28), days: 3, reason: "Personal", status: "Rejected", decidedBy: "jason.fernandez" },
];
const hourlyOf = (e: T.Employee) => toCents(e.monthlySalary) / 100 / Number(hrRules.hr_working_days) / Number(hrRules.hr_work_hours_per_day);
const overtime: T.OvertimeRequest[] = ([[7, -2, 3, "Emergency pump repair", "Pending"], [5, -4, 4, "Covered night shift", "Approved"], [2, -1, 2, "Elevator contractor escort", "Pending"]] as const)
  .map(([eid, d, hours, reason, status], i) => ({ id: i + 1, employeeId: eid, employeeName: empName(eid), date: addDays(TODAY, d), hours, multiplier: 1.25,
    amount: fromCents(Math.round(hourlyOf(employees[eid - 1]) * hours * 1.25 * 100)), reason, status, decidedBy: status === "Approved" ? "jason.fernandez" : "" }));
const loans: T.HrLoan[] = [
  { id: 1, employeeId: 4, employeeName: "Rodel Manalang", loanType: "SSS", referenceNo: "SSS-SL-2026-0419", originalAmount: "20000.00", balance: "14000.00", monthlyDeduction: "1000.00", status: "ACTIVE", notes: "" },
  { id: 2, employeeId: 2, employeeName: "Ben Salvador", loanType: "COMPANY", referenceNo: "", originalAmount: "6000.00", balance: "2000.00", monthlyDeduction: "1000.00", status: "ACTIVE", notes: "Cash advance" },
];
interface MockPayroll extends T.PayrollDetail { createdToken?: string }
const payrolls: MockPayroll[] = [];

// ------------------------------------------------------------------ administration
interface MockUser { id: number; username: string; role: T.Role; active: boolean; createdAt: string; mustChangePassword?: boolean; passwordChangedAt?: string }
let nextUserId = 1;
const users: MockUser[] = [
  ...(["super_admin", "admin", "manager", "staff", "accounting"] as T.Role[]).map((role) => ({ id: nextUserId++, username: PERSONAS[role].username, role, active: true, createdAt: "2026-01-05T01:00:00Z" })),
  { id: nextUserId++, username: "carmela.reyes", role: "staff", active: true, createdAt: "2026-02-11T02:00:00Z" },
  { id: nextUserId++, username: "old.cashier", role: "accounting", active: false, createdAt: "2024-07-01T01:00:00Z" },
  { id: nextUserId++, username: "marcus.v", role: "resident", active: true, createdAt: "2026-03-02T03:00:00Z" },
];
const meId = () => users.find((u) => u.username === actor())?.id ?? -1;
const userRow = (u: MockUser): T.UserAccount => {
  const isSelf = u.id === meId();
  const reset = u.role === "super_admin" ? "Superadmin passwords can't be reset here. The account owner changes it from their own account menu." : null;
  const del = isSelf ? "You can't delete the account you're signed in with."
    : u.role === "super_admin" && !users.some((x) => x.role === "super_admin" && x.active && x.id !== u.id) ? "This is the last active Superadmin account, so it can't be deleted."
    : u.role === "resident" ? "This account belongs to a resident. Remove or deactivate it under Resident Accounts." : null;
  const edit = isSelf ? "You can't change your own role or status. Another Superadmin can do it."
    : u.role === "resident" ? "This account belongs to a resident. Manage it under Resident Accounts." : null;
  return { ...u, roleLabel: ROLES[u.role].label, mustChangePassword: Boolean(u.mustChangePassword), passwordChangedAt: u.passwordChangedAt ?? null, isSelf,
    canEdit: !edit, editBlockedReason: edit, canResetPassword: !reset, resetBlockedReason: reset, canDelete: !del, deleteBlockedReason: del };
};
const failFields = (fields: Record<string, string>): Promise<never> =>
  new Promise((_, rej) => setTimeout(() => rej(new ApiError(400, Object.values(fields)[0], fields)), LATENCY));
interface MockResidentAccount { id: number; username: string; unitId: number | null; personType: "Owner" | "Tenant"; personId: number | null; displayName: string; active: boolean; mustChangePassword: boolean; createdAt: string }
let nextResidentAccountId = 1001;
const residentAccount = (username: string, unitNo: string, personType: "Owner" | "Tenant", link: boolean, name?: string): MockResidentAccount => {
  const unit = unitByNo(unitNo)!;
  const person = link ? (personType === "Owner" ? currentOwner(unit.id) : currentTenant(unit.id)) : null;
  return { id: nextResidentAccountId++, username, unitId: unit.id, personType, personId: person?.id ?? null, displayName: name ?? person?.name ?? username,
    active: true, mustChangePassword: false, createdAt: "2026-03-02T03:00:00Z" };
};
const residentAccounts: MockResidentAccount[] = [
  residentAccount("marcus.v", "12-01", "Owner", true, "Marcus Villanueva"),
  residentAccount("owner.501", "5-01", "Owner", true),
  residentAccount("owner.1103", "11-03", "Owner", true),
  residentAccount("tenant.602", "6-02", "Tenant", false, "Kevin Ong"),
];
const residentRow = (a: MockResidentAccount): T.ResidentAccount => {
  const unit = a.unitId ? units.find((u) => u.id === a.unitId) ?? null : null;
  const person = a.personId ? people.find((p) => p.id === a.personId && p.type === a.personType && p.unitId === a.unitId) ?? null : null;
  const [status, statusReason]: [T.ResidentAccountStatus, string | null] =
    !unit ? ["unlinked", "This login isn't linked to a unit, so it can't use the resident portal. Link it to a unit."]
    : !a.active ? ["inactive", "Deactivated. The resident can't sign in until the account is reactivated."]
    : !unit.active ? ["ended", "Your unit is no longer active in the system. Please contact the administrator."]
    : a.personId && (!person || person.status !== "Current") ? ["ended", `Your resident portal access has ended because you are no longer listed as a current ${a.personType.toLowerCase()} of unit ${unit.unitNo}. Please contact the administrator if this is a mistake.`]
    : ["active", null];
  return { id: a.id, username: a.username, displayName: a.displayName, unit: unit ? { id: unit.id, unitNo: unit.unitNo } : null, personType: a.personType,
    personId: a.personId, linked: Boolean(person), linkedName: person?.name ?? null, linkedStatus: person?.status ?? null, status, statusReason,
    active: a.active && Boolean(unit), mustChangePassword: a.mustChangePassword, createdAt: a.createdAt };
};
/** Same checks as backend/app/routes/resident_accounts.py validate_link; returns the display name to save. */
const residentLinkErrors = (input: Partial<T.ResidentAccountLink>, fields: Record<string, string>, selfId: number | null, existing?: MockResidentAccount) => {
  const unit = units.find((u) => u.id === input.unitId && u.active);
  if (!unit) fields.unitId = "Choose an active unit.";
  const type = input.personType ?? "Owner";
  let name = (input.displayName ?? "").trim();
  if (input.personId != null) {
    const person = people.find((p) => p.id === input.personId && p.type === type);
    const unchanged = existing && existing.personId === input.personId && existing.personType === type && existing.unitId === input.unitId;
    const other = residentAccounts.find((a) => a.id !== selfId && a.active && a.personType === type && a.personId === input.personId);
    if (!person || (unit && person.unitId !== unit.id)) fields.personId = `Choose a ${type.toLowerCase()} of this unit.`;
    else if (!unchanged && person.status !== "Current") fields.personId = `${person.name} is no longer a current ${type.toLowerCase()} of this unit.`;
    else if (!unchanged && other) fields.personId = `${person.name} already has a portal account (${other.username}).`;
    else if (!name) name = person.name;
  }
  if (!name) fields.displayName = "Enter the resident's name.";
  return name;
};

let nextAuditId = 1;
const auditLogs: T.AuditLog[] = [];
const audit = (action: string, username = actor(), at = nowUtc()) => auditLogs.unshift({ id: nextAuditId++, at, username, action });
/** Same filter as /api/admin/audit-logs (dates compared in Manila time, UTC+8). */
const filterAudit = ({ q, user, from, to }: Omit<T.AuditLogQuery, "page" | "perPage">) => {
  const needle = q.trim().toLowerCase();
  const manilaDay = (at: string) => new Date(new Date(`${at.replace(/Z?$/, "Z")}`).getTime() + 8 * 3600e3).toISOString().slice(0, 10);
  return auditLogs.filter((a) => (!needle || a.action.toLowerCase().includes(needle) || a.username.toLowerCase().includes(needle))
    && (!user || a.username.toLowerCase() === user.toLowerCase()) && (!from || manilaDay(a.at) >= from) && (!to || manilaDay(a.at) <= to));
};
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
const billDetail = (b: MockBill): T.BillDetail => ({ ...soaDetail(b), unitId: b.unitId, unitNo: unitById(b.unitId).unitNo, payerName: payerName(b.unitId), manualOverride: b.manual,
  stored: { condoDues: fromCents(b.condo), parking: fromCents(b.parking), storage: fromCents(b.storage), water: fromCents(b.water), other: "0.00", adjustment: "0.00", penalty: fromCents(b.penalty), previousBalance: fromCents(b.previous) },
  previousUnpaid: [], overdueMonths: [], emailRecipients: [], closed: false, corporation: rates.corporationName, address: rates.address, paymentInstructions: rates.onlinePaymentInstructions });
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
const unitRowOut = (u: MockUnit): T.UnitRow => {
  const base = unitOut(u);
  const ref = (id: number | null) => (id ? { id, unitNo: unitById(id).unitNo } : null);
  const user = units.find((x) => x.parkingId === u.id || x.storageId === u.id);
  const condo = RESIDENTIAL.includes(u.type) ? condoCents(u) : assetCents(u.id, toCents(u.type === "PARKING" ? rates.parkingRatePerSqm : rates.storageRatePerSqm));
  const parking = RESIDENTIAL.includes(u.type) ? assetCents(u.parkingId, toCents(rates.parkingRatePerSqm)) : 0;
  const last = bills.filter((b) => b.unitId === u.id).at(-1);
  return { id: u.id, unitNo: u.unitNo, floor: u.floor, type: u.type, areaSqm: u.areaSqm, ratePerSqm: base.ratePerSqm, effectiveRatePerSqm: base.ratePerSqm,
    autoRate: u.ratePerSqmCents === null, duesMode: "per_sqm", manualMonthlyDues: "0.00", occupancy: u.occupancy, status: u.status, active: u.active,
    ownerName: base.ownerName, tenantName: base.tenantName, contactNo: base.contactNo, email: base.email,
    parkingUnit: ref(u.parkingId), storageUnit: ref(u.storageId), assignedTo: user ? { id: user.id, unitNo: user.unitNo } : null,
    dues: { condo: fromCents(condo), parking: fromCents(parking), storage: "0.00" }, monthlyTotal: fromCents(condo + parking),
    zeroDues: condo === 0 && u.areaSqm > 0,
    latestBill: last ? { id: last.id, month: last.month, status: statusOf(last) } : null };
};
const unitDetailOut = (u: MockUnit): T.UnitDetail => ({
  ...unitRowOut(u),
  owners: people.filter((p) => p.unitId === u.id && p.type === "Owner").map((p) => personRecord(p)),
  tenants: people.filter((p) => p.unitId === u.id && p.type === "Tenant").map((p) => personRecord(p)),
  bills: bills.filter((b) => b.unitId === u.id).slice(-12).reverse().map((b) => ({ id: b.id, month: b.month, dueDate: b.dueDate,
    total: fromCents(totalCents(b)), paid: fromCents(b.paid), balance: fromCents(Math.max(balanceCents(b), 0)), status: statusOf(b) })),
});
const personRecord = (p: Person): T.UnitPersonRecord => ({ id: p.id, kind: p.type, name: p.name, contactNo: p.contactNo, email: p.email, moveIn: p.moveIn, moveOut: p.moveOut,
  status: p.status, notes: "", receiveSoaEmail: Boolean(p.email), includeInSoa: true, representative: p.type === "Tenant" && p.status === "Current" });
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

function reportRange(q: T.ReportQuery): [string, string, string] {
  const today = new Date(`${TODAY}T00:00:00`);
  const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  if (q.period === "daily") return [TODAY, TODAY, TODAY];
  if (q.period === "weekly") { const s = addDays(TODAY, -((today.getDay() + 6) % 7)); return [s, addDays(s, 6), `Week of ${s}`]; }
  if (q.period === "yearly") return [`${TODAY.slice(0, 4)}-01-01`, `${TODAY.slice(0, 4)}-12-31`, TODAY.slice(0, 4)];
  if (q.period === "custom") { const a = q.from || `${THIS_MONTH}-01`, b = q.to || TODAY; return a <= b ? [a, b, `${a} – ${b}`] : [b, a, `${b} – ${a}`]; }
  const end = new Date(today.getFullYear(), today.getMonth() + 1, 0);
  return [`${THIS_MONTH}-01`, iso(end), THIS_MONTH];
}

function propertyReport(q: T.ReportQuery): T.PropertyReport {
  const [from, to, label] = reportRange(q);
  const inRange = (d: string) => d >= from && d <= to;
  // Prototype: a bill counts in the period of its billing month's first day.
  const periodBills = bills.filter((b) => inRange(`${b.month}-01`));
  const sum = (f: (b: MockBill) => number) => periodBills.reduce((s, b) => s + f(b), 0);
  const charges = { condo: sum((b) => b.condo), parking: sum((b) => b.parking), storage: sum((b) => (storageIncluded(b) ? b.storage : 0)), water: sum((b) => b.water), other: 0, adjustment: 0, penalty: sum((b) => b.penalty) };
  const valid = receipts.filter((r) => inRange(r.date) && !(r as { voided?: boolean }).voided);
  const voided = receipts.filter((r) => inRange(r.date) && (r as { voided?: boolean }).voided);
  const kind = (k: string) => valid.reduce((s, r) => s + r.items.filter((i) => i.kind === k).reduce((t, i) => t + i.cents, 0), 0);
  const byMethod: Record<string, string> = { CASH: "0.00", CHECK: "0.00", ONLINE: "0.00" };
  for (const m of ["CASH", "CHECK", "ONLINE"]) byMethod[m] = fromCents(valid.filter((r) => r.method === m).reduce((s, r) => s + r.amountCents, 0));
  const exp = expenses.filter((e) => inRange(e.date)).sort((a, b) => a.date.localeCompare(b.date));
  const expCents = exp.reduce((s, e) => s + toCents(e.amount), 0);
  const byCategory: Record<string, string> = {};
  for (const e of exp) byCategory[e.category] = fromCents(toCents(byCategory[e.category] ?? "0") + toCents(e.amount));
  const collected = valid.reduce((s, r) => s + r.amountCents, 0);
  const latest = residential().map((u) => bills.filter((b) => b.unitId === u.id).at(-1)).filter(Boolean) as MockBill[];
  const owing = latest.filter((b) => balanceCents(b) > 0);
  const res = residential();
  return {
    period: q.period, label, from, to,
    property: { residentialUnits: res.length, occupied: res.filter((u) => u.status === "Occupied").length, vacant: res.filter((u) => u.status !== "Occupied").length,
      currentTenants: people.filter((p) => p.type === "Tenant" && p.status === "Current").length, withParking: res.filter((u) => u.parkingId).length },
    billing: { bills: periodBills.length, charged: fromCents(Object.values(charges).reduce((s, c) => s + c, 0)), advanceCredits: fromCents(sum((b) => b.advance)),
      charges: Object.fromEntries(Object.entries(charges).map(([k, v]) => [k, fromCents(v)])) as T.PropertyReport["billing"]["charges"] },
    collections: { total: fromCents(collected), bills: fromCents(kind("bill")), water: fromCents(kind("water")), advances: fromCents(kind("advance")), byMethod,
      receipts: valid.length, withoutReceipt: 0, voided: { count: voided.length, amount: fromCents(voided.reduce((s, r) => s + r.amountCents, 0)) } },
    expenses: { total: fromCents(expCents), byCategory, rows: exp },
    netCashFlow: fromCents(collected - expCents),
    receivables: { total: fromCents(owing.reduce((s, b) => s + balanceCents(b), 0)), units: owing.length, overdueUnits: owing.filter((b) => TODAY > b.dueDate).length },
    transactions: [...valid].sort((a, b) => a.date.localeCompare(b.date) || a.seq - b.seq).map((r) => ({ date: r.date, receiptNo: orNo(r), receiptId: r.id, unitNo: unitById(r.unitId).unitNo,
      method: r.method, reference: r.reference, description: receiptRow(r).items.map((i) => i.label).join("; "), amount: fromCents(r.amountCents) })),
  };
}

const RULE_META: [string, string, string, string][] = [
  ["hr_working_days", "Working days per month", "Attendance", "days"], ["hr_work_hours_per_day", "Work hours per day", "Attendance", "hours"], ["hr_grace_minutes", "Grace period for late", "Attendance", "minutes"],
  ["hr_sss_employee_rate", "Employee share", "SSS", "%"], ["hr_sss_employer_rate", "Employer share", "SSS", "%"], ["hr_sss_max_msc", "Maximum monthly salary credit", "SSS", "₱"],
  ["hr_sss_ec_threshold", "EC: salary credit up to", "SSS", "₱"], ["hr_sss_ec_low", "EC (employer) up to the threshold", "SSS", "₱"], ["hr_sss_ec_high", "EC (employer) above the threshold", "SSS", "₱"],
  ["hr_philhealth_rate", "Premium rate", "PhilHealth", "%"], ["hr_philhealth_min_base", "Salary floor", "PhilHealth", "₱"], ["hr_philhealth_max_base", "Salary ceiling", "PhilHealth", "₱"], ["hr_philhealth_employee_share", "Employee share of the premium", "PhilHealth", "%"],
  ["hr_pagibig_max_base", "Maximum fund salary", "Pag-IBIG", "₱"], ["hr_pagibig_low_rate", "Employee rate (salary ≤ ₱1,500)", "Pag-IBIG", "%"], ["hr_pagibig_high_rate", "Employee rate (above ₱1,500)", "Pag-IBIG", "%"], ["hr_pagibig_employer_rate", "Employer rate", "Pag-IBIG", "%"],
  ["hr_bir_exempt_threshold", "Tax-exempt up to (monthly)", "BIR withholding", "₱"], ["hr_bir_bracket2", "15% bracket up to", "BIR withholding", "₱"], ["hr_bir_bracket3", "20% bracket up to", "BIR withholding", "₱"],
  ["hr_bir_bracket4", "25% bracket up to", "BIR withholding", "₱"], ["hr_bir_bracket5", "30% bracket up to", "BIR withholding", "₱"], ["hr_bir_rate2", "Rate, 2nd bracket", "BIR withholding", "%"],
  ["hr_bir_rate3", "Rate, 3rd bracket", "BIR withholding", "%"], ["hr_bir_rate4", "Rate, 4th bracket", "BIR withholding", "%"], ["hr_bir_rate5", "Rate, 5th bracket", "BIR withholding", "%"], ["hr_bir_rate6", "Rate, top bracket", "BIR withholding", "%"],
  ["hr_13th_month_ceiling", "Tax-exempt 13th month & benefits", "13th month", "₱"],
];
const mockRules = (): T.PayrollRule[] => RULE_META.map(([key, label, group, unit]) => ({ key, label, group, unit, value: hrRules[key], default: RULE_DEFAULTS[key] }));

/** A payroll preview with the server's rules (prototype). Returns an error message instead when invalid. */
function mockPreview(input: T.PayrollInput): T.PayrollPreview | string {
  const e = employees.find((x) => x.id === input.employeeId);
  if (!e) return "Choose the employee.";
  if (!input.from || !input.to || input.to < input.from) return "Enter a valid period.";
  const monthly = toCents(e.monthlySalary) / 100;
  const approved = overtime.filter((o) => o.employeeId === e.id && o.status === "Approved" && o.date >= input.from && o.date <= input.to).reduce((t, o) => t + toCents(o.amount), 0) / 100;
  const ot = input.overtime === "" ? approved : toCents(input.overtime) / 100;
  const basic = input.basic === "" ? monthly : toCents(input.basic) / 100;
  const allowances = toCents(input.allowances || "0") / 100, deductions = toCents(input.deductions || "0") / 100;
  const { absences, lateUndertime } = attendanceDeductions(hrRules, monthly, attendance.filter((a) => a.employeeId === e.id && a.date >= input.from && a.date <= input.to));
  const loanCents = loans.filter((l) => l.employeeId === e.id && l.status === "ACTIVE").reduce((t, l) => t + Math.min(toCents(l.monthlyDeduction), toCents(l.balance)), 0);
  const st = statutory(hrRules, basic + ot + allowances, basic, deductions + loanCents / 100, absences, lateUndertime);
  return { basic: basic.toFixed(2), overtime: ot.toFixed(2), approvedOvertime: approved.toFixed(2), allowances: allowances.toFixed(2), deductions: deductions.toFixed(2),
    loanDeductions: fromCents(loanCents), absences: absences.toFixed(2), lateUndertime: lateUndertime.toFixed(2), statutory: st };
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
        permissions: permissionsForRole(currentRole), mustChangePassword: false, passwordPolicy: { minLength: 10, maxLength: 128 }, displayName: PERSONAS[cfg.role].displayName, email: PERSONAS[cfg.role].email, subtitle: PERSONAS[cfg.role].subtitle });
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
      if (next.length < 10) return fail(400, "Use at least 10 characters.");
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
      const t = { id: nextTicketId, unitId, unitNo: u.unitNo, ticketNo: `MT-${TODAY.replace(/-/g, "")}-${String(nextTicketId++).padStart(5, "0")}`, ...input, status: "Open" as const, assignedTo: "", vendorId: null, vendor: "", resolution: "", requestedAt: nowUtc(), source: "resident" as const };
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
    notices: () => wait(announcements.filter((a) => a.published && a.audience !== "staff").map(({ id, title, message, publishDate }) => ({ id, title, message, publishDate }))),
    documents: (unitId) => needResidentUnit(unitId) ?? wait(documents.filter((d) => d.audience === "residents" || (d.audience === "unit" && d.unitId === unitId))
      .map(({ id, title, category, description, fileName, filePath, audience, addedAt }) => ({ id, title, category, description, fileName, filePath, forUnit: audience === "unit", addedAt }))),
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
      return wait({ headcount: employees.filter((e) => e.status !== "Separated" && e.status !== "Inactive").length, presentToday: today.filter((a) => a.status === "PRESENT" || a.status === "LATE").length,
        lateToday: today.filter((a) => a.status === "LATE").length, onLeave: employees.filter((e) => e.status === "On Leave").length,
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
      residentAccounts: residentAccounts.length, unlinkedResidentAccounts: residentAccounts.map(residentRow).filter((a) => !a.linked).length,
      auditToday: auditLogs.filter((a) => a.at.startsWith(TODAY)).length, lastBackup: `${addDays(TODAY, -1)}T10:00:00`, schemaRevision: "0007_gate_pass_requests",
    }),
  },

  units: {
    list: () => wait(units.map(unitOut)),
    people: (unitId) => wait(people.filter((p) => p.unitId === unitId).map(({ id, type, name, status, moveIn, moveOut }) => ({ id, type, name, status, moveIn, moveOut }))),
    page: ({ q, kind, floor, status, page, perPage, past }) => {
      const ofKind = (u: MockUnit, k: T.UnitKind) => (k === "residential" ? !["PARKING", "STORAGE"].includes(u.type) : u.type === k);
      const needle = q.trim().toLowerCase();
      const rows = units.filter((u) => u.active && ofKind(u, kind) && (!floor || u.floor === floor) && (!status || u.status === status)
        && (!needle || [u.unitNo, currentOwner(u.id)?.name ?? "", currentTenant(u.id)?.name ?? "",
          ...(past ? people.filter((p) => p.unitId === u.id).flatMap((p) => [p.name, p.contactNo, p.email]) : [])].some((v) => v.toLowerCase().includes(needle))))
        .sort((a, b) => a.unitNo.localeCompare(b.unitNo, undefined, { numeric: true }));
      const counts = Object.fromEntries((["residential", "PARKING", "STORAGE"] as T.UnitKind[]).map((k) => [k, units.filter((u) => u.active && ofKind(u, k)).length])) as Record<T.UnitKind, number>;
      return wait({ units: rows.slice((page - 1) * perPage, page * perPage).map(unitRowOut), total: rows.length, page, perPage, counts });
    },
    options: () => {
      const asset = (k: string) => units.filter((u) => u.active && u.type === k).map((a) => {
        const owner = units.find((u) => u.parkingId === a.id || u.storageId === a.id);
        return { id: a.id, unitNo: a.unitNo, floor: a.floor, assignedTo: owner ? { id: owner.id, unitNo: owner.unitNo } : null };
      });
      return wait({ parking: asset("PARKING"), storage: asset("STORAGE"), unitTypes: ["STUDIO TYPE", "1 BEDROOM", "2 BEDROOM", "3 BEDROOM", "PARKING", "STORAGE"],
        floors: [...new Set(units.map((u) => u.floor))].sort(), typeRates: Object.fromEntries((["STUDIO TYPE", "1 BEDROOM", "2 BEDROOM", "3 BEDROOM"] as T.UnitType[]).map((t) => [t, fromCents(rateFor(t))])) });
    },
    detail: (id) => (unitById(id) ? wait(unitDetailOut(unitById(id))) : fail(404, "Unit not found.")),
    create: (input) => {
      if (!input.unitNo.trim()) return failFields({ unitNo: "Unit number is required." });
      if (unitByNo(input.unitNo)) return failFields({ unitNo: "That unit number already exists." });
      const u: MockUnit = { id: Math.max(...units.map((x) => x.id)) + 1, unitNo: input.unitNo.trim(), floor: input.floor, type: input.type as T.UnitType, areaSqm: Number(input.areaSqm) || 0,
        ratePerSqmCents: input.ratePerSqm ? toCents(input.ratePerSqm) : null, status: input.status, occupancy: input.occupancy, active: true, parkingId: input.parkingUnitId, storageId: input.storageUnitId };
      units.push(u);
      for (const [p, type] of [[input.owner, "Owner"], [input.tenant, "Tenant"]] as const)
        if (p?.name.trim()) people.push({ id: nextPersonId++, unitId: u.id, type, name: p.name.trim(), contactNo: p.contactNo, email: p.email, status: "Current", moveIn: p.moveIn || null, moveOut: null });
      audit(`Created unit ${u.unitNo}`);
      return wait(unitDetailOut(u));
    },
    update: (id, input) => {
      const u = unitById(id);
      if (!u) return fail(404, "Unit not found.");
      Object.assign(u, { floor: input.floor, type: input.type, areaSqm: Number(input.areaSqm) || 0, ratePerSqmCents: input.ratePerSqm ? toCents(input.ratePerSqm) : null,
        status: input.status, occupancy: input.occupancy, parkingId: input.parkingUnitId, storageId: input.storageUnitId });
      audit(`Updated unit ${u.unitNo}`);
      return wait(unitDetailOut(u));
    },
    addPerson: (id, kind, input) => {
      const u = unitById(id);
      if (!u) return fail(404, "Unit not found.");
      if (!input.name.trim()) return failFields({ name: `${kind} name is required.` });
      people.push({ id: nextPersonId++, unitId: id, type: kind, name: input.name.trim(), contactNo: input.contactNo, email: input.email,
        status: input.moveOut ? "Past" : "Current", moveIn: input.moveIn || null, moveOut: input.moveOut || null });
      if (kind === "Tenant" && !input.moveOut) u.status = "Occupied";
      audit(`Added ${kind.toLowerCase()} ${input.name} to unit ${u.unitNo}`);
      return wait(unitDetailOut(u));
    },
    updatePerson: (id, kind, personId, input) => {
      const p = people.find((x) => x.id === personId && x.unitId === id && x.type === kind);
      if (!p) return fail(404, `${kind} not found.`);
      if (!input.name.trim()) return failFields({ name: `${kind} name is required.` });
      Object.assign(p, { name: input.name.trim(), contactNo: input.contactNo, email: input.email, status: input.status, moveIn: input.moveIn || null,
        moveOut: input.status === "Current" ? null : input.moveOut || TODAY });
      audit(`Updated ${kind.toLowerCase()} ${p.name} in unit ${unitById(id).unitNo}`);
      return wait(unitDetailOut(unitById(id)));
    },
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
    emailSoas: (month, billIds) => {
      if (!rates.smtpHost || !rates.smtpSender) return fail(409, "Set the SMTP host and sender email under Rates & Rules before sending SOAs.");
      const n = bills.filter((b) => b.month === month && (!billIds || billIds.includes(b.id))).length;
      audit(`Sent SOA emails for ${month}`);
      return wait({ sent: n, skipped: 0, failed: 0, errors: [] });
    },
    preview: (month) => {
      const res = residential();
      const billed = new Set(bills.filter((b) => b.month === month).map((b) => b.unitId));
      const read = new Set(readings.filter((r) => r.month === month).map((r) => r.unitId));
      return wait({ month, residentialUnits: res.length, alreadyBilled: res.filter((u) => billed.has(u.id)).length, toCreate: res.filter((u) => !billed.has(u.id)).length,
        readings: res.filter((u) => read.has(u.id)).length, missingReadings: res.filter((u) => !read.has(u.id) && !billed.has(u.id)).map((u) => u.unitNo).slice(0, 50),
        zeroDuesUnits: [], dueDate: `${month}-08`, closed: false, closedThrough: null });
    },
    correctSoa: (billId, input) => {
      const b = bills.find((x) => x.id === billId);
      if (!b) return fail(404, "Bill not found.");
      if (!input.note.trim()) return failFields({ note: "Explain the correction (it is shown on the SOA and kept in the audit log)." });
      Object.assign(b, { condo: toCents(input.condoDues), parking: toCents(input.parking), storage: toCents(input.storage), water: toCents(input.water),
        penalty: toCents(input.penalty), previous: toCents(input.previousBalance), dueDate: input.dueDate || b.dueDate, note: input.note.trim(), manual: true });
      audit(`Manual SOA correction for unit ${unitById(b.unitId).unitNo}, billing ${b.month}`);
      return wait(billDetail(b));
    },
    emailBill: () => (rates.smtpHost && rates.smtpSender ? wait({ sent: 1, skipped: 0, failed: 0, errors: [] }) : fail(409, "Set the SMTP host and sender email under Rates & Rules before sending SOAs.")),
    emailOverview: (month) => wait({ month, smtpConfigured: Boolean(rates.smtpHost && rates.smtpSender), optedIn: 0,
      rows: bills.filter((b) => b.month === month).map((b) => ({ billId: b.id, unitNo: unitById(b.unitId).unitNo, status: statusOf(b), recipients: [] })) }),
  },

  advances: {
    list: (q = {}) => {
      const rows = [...advances].filter((a) => !q.unit || a.unitId === q.unit).sort((a, b) => b.date.localeCompare(a.date) || b.id - a.id).map(advanceOut)
        .filter((a) => !q.status || (q.status === "open" ? toCents(a.remaining) > 0 : q.status === "used" ? toCents(a.remaining) <= 0 : false));
      return wait({ advances: rows, totals: { count: rows.length, received: fromCents(rows.reduce((s, a) => s + toCents(a.amount), 0)), remaining: fromCents(rows.reduce((s, a) => s + toCents(a.remaining), 0)) } });
    },
    options: () => wait({ units: residential().map((u) => ({ id: u.id, unitNo: u.unitNo, payerName: payerName(u.id), monthlyDues: fromCents(condoCents(u)) })) }),
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
      return wait({ advance: advanceOut(a), receiptNo: orNo(receipt), receiptId: receipt.id });
    },
  },

  receipts: {
    ledger: ({ from, to, q, method, status }) => {
      const needle = q.trim().toLowerCase();
      const rows = receipts.filter((r) => r.date >= from && r.date <= to)
        .map(officialReceipt)
        .filter((r) => (!needle || [r.receiptNo, r.unitNo, r.reference].some((v) => v.toLowerCase().includes(needle)))
          && (!method || r.method === method) && (!status || (status === "void") === Boolean(r.voided)))
        .sort((a, b) => b.receiptNo.localeCompare(a.receiptNo));
      const valid = rows.filter((r) => !r.voided);
      const sum = (m?: string) => fromCents(valid.filter((r) => !m || r.method === m).reduce((s, r) => s + toCents(r.amount), 0));
      return wait({ from, to, receipts: rows, total: rows.length, truncated: false, collected: sum(),
        byMethod: { CASH: sum("CASH"), CHECK: sum("CHECK"), ONLINE: sum("ONLINE") } as Record<T.PaymentMethod, T.Money>, voidCount: rows.length - valid.length });
    },
    detail: (id) => {
      const r = receipts.find((x) => x.id === id);
      if (!r) return fail(404, "Receipt not found.");
      return wait({ corporation: rates.corporationName, address: rates.address, unit: { id: r.unitId, unitNo: unitById(r.unitId).unitNo },
        receivedFrom: payerName(r.unitId), receipt: { ...officialReceipt(r), remarks: "", receivedBy: r.receivedBy, backfilled: r.backfilled },
        voidedBy: "", voidedAt: null, closed: false, canVoid: currentRole === "super_admin" || currentRole === "accounting" });
    },
    void: () => fail(409, "Prototype: voiding is only available in the live system."),
  },

  water: {
    month: (month) => {
      const rows = readings.filter((r) => r.month === month).map(readingOut).sort((a, b) => a.unitNo.localeCompare(b.unitNo, undefined, { numeric: true }));
      const done = new Set(rows.map((r) => r.unitId));
      const missing = residential().filter((u) => !done.has(u.id)).map((u) => {
        const prev = readings.filter((r) => r.unitId === u.id && r.month < month).sort((a, b) => b.month.localeCompare(a.month))[0];
        return { unitId: u.id, unitNo: u.unitNo, payerName: payerName(u.id), previous: prev?.current ?? 0, previousMonth: prev?.month ?? null };
      });
      const unpaid = readings.map(readingOut).filter((r) => !r.paid && toCents(r.amount) > 0);
      return wait({ month, readings: rows, missing, unpaid, defaultRate: rates.waterRate, autoCompute: rates.waterAutoCompute,
        totals: { read: rows.length, toRead: missing.length, usage: rows.reduce((s, r) => s + r.usage, 0), amount: fromCents(rows.reduce((s, r) => s + toCents(r.amount), 0)),
          unpaid: fromCents(unpaid.reduce((s, r) => s + toCents(r.amount), 0)) } });
    },
    correct: (id, input) => {
      const r = readings.find((x) => x.id === id);
      if (!r) return fail(404, "Water reading not found.");
      return mockApi.water.save({ ...input, unitId: r.unitId, month: r.month });
    },
    pay: () => fail(409, "Prototype: water payments are only available in the live system."),
    previousReading: (unitId, month) => wait(readings.filter((r) => r.unitId === unitId && r.month < month).sort((a, b) => b.month.localeCompare(a.month))[0]?.current ?? 0),
    save: (input) => {
      if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(input.month)) return fail(400, "Invalid reading month.");
      const previous = input.previous === "" ? (readings.filter((x) => x.unitId === input.unitId && x.month < input.month).sort((a, b) => b.month.localeCompare(a.month))[0]?.current ?? 0) : Number(input.previous);
      const current = Number(input.current);
      if (current < previous) return failFields({ current: "The current reading can't be lower than the previous reading." });
      const rate = input.rate === "" ? rates.waterRate : input.rate;
      let r = readingFor(input.unitId, input.month);
      const created = !r;
      if (!r) readings.push((r = { id: nextReadingId++, unitId: input.unitId, month: input.month, previous: 0, current: 0, rateCents: 0, readingDate: input.readingDate, paidCents: 0 }));
      Object.assign(r, { previous, current, rateCents: toCents(rate), readingDate: input.readingDate });
      const bill = bills.find((b) => b.unitId === input.unitId && b.month === input.month);
      if (bill) bill.water = readingCents(r); // the month's bill follows the corrected reading
      audit(`Saved water reading for unit ${unitById(input.unitId).unitNo}, ${input.month}`);
      return wait({ reading: readingOut(r), created, billUpdated: Boolean(bill) });
    },
  },

  gatePasses: {
    list: () => wait(gatePasses.map(({ unitId: _u, ...p }) => p)),
    issue: (input) => {
      const u = unitByNo(input.unitNo);
      const errors: Record<string, string> = {};
      if (!u) errors.unitNo = "Enter an existing unit number, e.g. 12-01.";
      if (!input.name.trim()) errors.name = "Name is required.";
      if (!input.purpose.trim()) errors.purpose = "Purpose is required.";
      if (!u || Object.keys(errors).length) return failFields(errors);
      const p = addPass({ ...input, unitNo: u.unitNo, status: "Issued", reviewNote: "", requestedAt: null, requestedBy: null, reviewedBy: actor(), source: "office" });
      audit("Created gate pass");
      const { unitId: _u, ...out } = p;
      return wait(out);
    },
    review: (passId, decision, note) => {
      const p = gatePasses.find((x) => x.id === passId);
      if (!p) return fail(404, "Request not found.");
      if (p.status !== "Requested") return fail(409, `This request was already handled (${p.status.toLowerCase()}).`);
      if (decision === "reject" && !note.trim()) return failFields({ note: "Give the resident a reason when rejecting a request." });
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
    options: () => wait(units.filter((u) => u.active && u.type !== "PARKING" && u.type !== "STORAGE").map((u) => ({ id: u.id, unitNo: u.unitNo,
      people: people.filter((p) => p.unitId === u.id).map(({ id, type, name, status, moveIn, moveOut }) => ({ id, type, name, status, moveIn, moveOut })) }))),
    generate: (input) => {
      const person = people.find((p) => p.id === input.personId && p.unitId === input.unitId && p.type === input.personType);
      if (!person) return failFields({ personId: `Choose the ${input.personType.toLowerCase()} of this unit.` });
      const c: T.Certificate = { id: nextCertId, certificateNo: `CL9-${input.certificateDate.slice(0, 4)}-${String(nextCertId++).padStart(5, "0")}`, moveType: input.moveType, personType: input.personType, personName: person.name,
        unitNo: unitById(input.unitId).unitNo, moveDate: input.moveType === "Move In" ? person.moveIn : person.moveOut, certificateDate: input.certificateDate, issuedBy: actor() };
      certificates.push(c);
      audit(`Generated ${c.moveType} certificate ${c.certificateNo}`);
      return wait(c);
    },
    detail: (id) => {
      const c = certificates.find((x) => x.id === id);
      if (!c) return fail(404, "Certificate not found.");
      const u = unitByNo(c.unitNo)!;
      const person = people.find((p) => p.unitId === u.id && p.type === c.personType && p.name === c.personName);
      return wait({ certificate: c, corporation: rates.corporationName, address: rates.address, unit: { unitNo: u.unitNo, type: u.type, areaSqm: u.areaSqm },
        person: { contactNo: person?.contactNo ?? "", email: person?.email ?? "", representative: false } });
    },
  },

  expenses: {
    list: (month) => {
      const rows = expenses.filter((e) => e.date.startsWith(month)).sort((a, b) => b.date.localeCompare(a.date) || b.id - a.id);
      const by: Record<string, number> = {};
      rows.forEach((e) => (by[e.category] = (by[e.category] ?? 0) + toCents(e.amount)));
      return wait({ month, expenses: rows, total: fromCents(rows.reduce((t, e) => t + toCents(e.amount), 0)),
        byCategory: Object.fromEntries(Object.keys(by).sort().map((k) => [k, fromCents(by[k])])), categories: [...new Set(expenses.map((e) => e.category))].sort() });
    },
    add: (input) => {
      const errors: Record<string, string> = {};
      if (!input.category.trim()) errors.category = "Category is required.";
      if (!input.description.trim()) errors.description = "Description is required.";
      if (!isAmount(input.amount)) errors.amount = "Enter an amount greater than zero.";
      if (Object.keys(errors).length) return failFields(errors);
      const e = { ...input, id: nextExpenseId++, amount: fromCents(toCents(input.amount)) };
      expenses.push(e);
      audit(`Recorded expense: ${e.category} ${e.amount}`);
      return wait(e);
    },
  },

  maintenance: {
    board: () => wait({ tickets: [...tickets].sort((a, b) => b.id - a.id), categories: TICKET_CATEGORIES, priorities: TICKET_PRIORITIES, statuses: ["Open", "In Progress", "Resolved", "Closed"] as T.TicketStatus[],
      vendors: vendors.filter((v) => v.status === "Active").map(({ id, name, serviceType }) => ({ id, name, serviceType })),
      units: units.filter((u) => u.active).map(({ id, unitNo }) => ({ id, unitNo })) }),
    create: (input) => {
      const u = units.find((x) => x.id === input.unitId && x.active);
      const errors: Record<string, string> = {};
      if (!u) errors.unitId = "Choose the unit.";
      if (!input.title.trim()) errors.title = "Title is required.";
      if (!input.description.trim()) errors.description = "Description is required.";
      if (!u || Object.keys(errors).length) return failFields(errors);
      const t = { ...input, id: nextTicketId, unitId: u.id, unitNo: u.unitNo, ticketNo: `MT-${TODAY.replace(/-/g, "")}-${String(nextTicketId++).padStart(5, "0")}`,
        status: "Open" as const, assignedTo: "", vendorId: null, vendor: "", resolution: "", requestedAt: nowUtc(), source: "office" as const };
      tickets.unshift(t);
      audit(`Created maintenance ticket ${t.ticketNo} for unit ${u.unitNo}`);
      return wait(t);
    },
    update: (id, input) => {
      const t = tickets.find((x) => x.id === id);
      if (!t) return fail(404, "Maintenance ticket not found.");
      const v = input.vendorId ? vendors.find((x) => x.id === input.vendorId) : null;
      if (input.vendorId && !v) return failFields({ vendorId: "Choose a vendor from the list." });
      Object.assign(t, input, { vendor: v?.name ?? "" });
      audit(`Updated maintenance ticket ${t.ticketNo}`);
      return wait(t);
    },
  },

  vendors: {
    list: () => wait([...vendors].sort((a, b) => a.name.localeCompare(b.name))),
    add: (input) => {
      if (!input.name.trim()) return failFields({ name: "Vendor name is required." });
      if (input.email && !input.email.includes("@")) return failFields({ email: "Enter a valid email address." });
      const v = { ...input, id: nextVendorId++ };
      vendors.push(v);
      audit(`Created vendor: ${v.name}`);
      return wait(v);
    },
    update: (id, input) => {
      const v = vendors.find((x) => x.id === id);
      if (!v) return fail(404, "Vendor not found.");
      if (!input.name.trim()) return failFields({ name: "Vendor name is required." });
      if (input.email && !input.email.includes("@")) return failFields({ email: "Enter a valid email address." });
      Object.assign(v, input);
      audit(`Updated vendor: ${v.name} (${v.status})`);
      return wait(v);
    },
  },
  documents: {
    list: () => wait({ documents: [...documents].sort((a, b) => (b.addedAt ?? "").localeCompare(a.addedAt ?? "")), units: units.filter((u) => u.active).map(({ id, unitNo }) => ({ id, unitNo })) }),
    add: (input) => {
      if (!input.title.trim()) return failFields({ title: "Title is required." });
      const u = input.audience === "unit" ? units.find((x) => x.id === input.unitId) : null;
      if (input.audience === "unit" && !u) return failFields({ unitId: "Choose the unit." });
      const d: T.DocumentRecord = { ...input, id: nextDocId++, category: input.category.trim() || "General", unitId: u?.id ?? null, unitNo: u?.unitNo ?? null, addedAt: TODAY, addedBy: actor() };
      documents.push(d);
      audit(`Added document record: ${d.title}`);
      return wait(d);
    },
  },

  announcements: {
    list: () => wait([...announcements].sort((a, b) => (b.publishDate ?? "").localeCompare(a.publishDate ?? ""))),
    publish: (input) => {
      if (!input.title.trim() || !input.message.trim()) return fail(400, "Title and message are required.");
      const a: T.Announcement = { id: nextNoticeId++, ...input, publishDate: TODAY, createdBy: actor() };
      announcements.push(a);
      audit(`Published announcement "${a.title}"`);
      return wait(a);
    },
  },

  hr: {
    employees: () => wait({ employees: employees.map((e) => ({ ...e, hasRecords: attendance.some((a) => a.employeeId === e.id) || payrolls.some((x) => x.employeeId === e.id) })).sort((x, y) => x.fullName.localeCompare(y.fullName)),
      statuses: ["Active", "On Leave", "Inactive", "Separated"] as T.EmploymentStatus[] }),
    addEmployee: (input) => {
      const errors: Record<string, string> = {};
      if (!input.employeeNo.trim()) errors.employeeNo = "Employee No. is required.";
      else if (employees.some((e) => e.employeeNo.toLowerCase() === input.employeeNo.trim().toLowerCase())) errors.employeeNo = "That employee number is already used.";
      if (!input.fullName.trim()) errors.fullName = "Full name is required.";
      if (!isAmount(input.monthlySalary || "0") && input.monthlySalary !== "0") errors.monthlySalary = "Enter the monthly salary.";
      if (Object.keys(errors).length) return failFields(errors);
      const e: T.Employee = { ...input, id: nextEmployeeId++, monthlySalary: fromCents(toCents(input.monthlySalary || "0")), hasRecords: false };
      employees.push(e);
      audit(`Added employee ${e.employeeNo} (${e.fullName})`);
      return wait(e);
    },
    updateEmployee: (id, input) => {
      const e = employees.find((x) => x.id === id);
      if (!e) return fail(404, "Employee not found.");
      if (!input.fullName.trim()) return failFields({ fullName: "Full name is required." });
      Object.assign(e, { ...input, employeeNo: e.employeeNo, monthlySalary: fromCents(toCents(input.monthlySalary || "0")) });
      audit(`Updated employee ${e.employeeNo}`);
      return wait(e);
    },
    deleteEmployee: (id) => {
      const i = employees.findIndex((x) => x.id === id);
      if (i < 0) return fail(404, "Employee not found.");
      if (attendance.some((a) => a.employeeId === id) || payrolls.some((x) => x.employeeId === id)) return fail(409, `${employees[i].fullName} has records, which must be kept. Set the status to Separated instead.`);
      audit(`Deleted employee ${employees[i].employeeNo}`);
      employees.splice(i, 1);
      return wait(undefined);
    },
    attendanceDay: (date) => wait({ date, statuses: ["PRESENT", "LATE", "UNDERTIME", "LATE/UNDERTIME", "ABSENT", "LEAVE", "REST DAY"],
      employees: employees.filter((e) => e.status !== "Inactive" && e.status !== "Separated").map((e) => {
        const a = attendance.find((x) => x.employeeId === e.id && x.date === date);
        return { id: e.id, fullName: e.fullName, employeeNo: e.employeeNo, status: e.status, record: a ? attendanceOut(a) : null };
      }), schedule: { start: "08:00", end: "17:00", graceMinutes: Number(hrRules.hr_grace_minutes) } }),
    saveAttendance: (date, records) => {
      if (date > TODAY) return failFields({ date: "Attendance can't be entered for a future date." });
      let saved = 0, cleared = 0;
      for (const r of records) {
        const i = attendance.findIndex((x) => x.employeeId === r.employeeId && x.date === date);
        if (!r.status) { if (i >= 0) { attendance.splice(i, 1); cleared++; } continue; }
        const noTimes = ["ABSENT", "LEAVE", "REST DAY"].includes(r.status);
        if (!noTimes && r.timeIn && r.timeOut && r.timeOut <= r.timeIn) return failFields({ records: `${empName(r.employeeId)}: time out must be after time in.` });
        const row = { employeeId: r.employeeId, date, status: r.status, timeIn: noTimes ? null : r.timeIn, timeOut: noTimes ? null : r.timeOut, remarks: r.remarks };
        if (i >= 0) Object.assign(attendance[i], row); else attendance.push({ id: nextAttendanceId++, ...row });
        saved++;
      }
      audit(`Saved attendance for ${date}: ${saved} record(s)`);
      return wait({ saved, cleared });
    },
    attendanceHistory: (id, from, to) => {
      const e = employees.find((x) => x.id === id);
      if (!e) return fail(404, "Employee not found.");
      const rows = attendance.filter((a) => a.employeeId === id && a.date >= from && a.date <= to).sort((x, y) => y.date.localeCompare(x.date)).map(attendanceOut);
      const counts: Record<string, number> = {};
      rows.forEach((r) => (counts[r.status] = (counts[r.status] ?? 0) + 1));
      return wait({ employee: e, from, to, records: rows, counts, lateMinutes: rows.reduce((t, r) => t + r.lateMinutes, 0), undertimeMinutes: rows.reduce((t, r) => t + r.undertimeMinutes, 0) });
    },
    overtime: () => wait({ requests: [...overtime].sort((x, y) => y.date.localeCompare(x.date)), multipliers: [{ value: 1.25, label: "Ordinary day" }, { value: 1.3, label: "Rest / special day" }, { value: 2.6, label: "Regular holiday" }, { value: 3.38, label: "Rest day + regular holiday" }] }),
    fileOvertime: (input) => {
      const e = employees.find((x) => x.id === input.employeeId);
      const hours = Number(input.hours);
      if (!e) return failFields({ employeeId: "Choose the employee." });
      if (!(hours >= 0.25 && hours <= 24)) return failFields({ hours: "Hours must be between 0.25 and 24." });
      const mult = Number(input.multiplier || "1.25");
      const o: T.OvertimeRequest = { id: nextOvertimeId++, employeeId: e.id, employeeName: e.fullName, date: input.date, hours, multiplier: mult,
        amount: fromCents(Math.round(hourlyOf(e) * hours * mult * 100)), reason: input.reason, status: "Pending", decidedBy: "" };
      overtime.push(o);
      audit(`Filed overtime for ${e.fullName}`);
      return wait(o);
    },
    decideOvertime: (id, status) => {
      const o = overtime.find((x) => x.id === id);
      if (!o) return fail(404, "Overtime request not found.");
      if (o.status !== "Pending") return fail(409, `This overtime request was already ${o.status.toLowerCase()}.`);
      Object.assign(o, { status, decidedBy: actor() });
      audit(`${status} overtime request #${id}`);
      return wait(o);
    },
    leave: () => wait({ requests: [...leave].sort((x, y) => y.from.localeCompare(x.from)), types: ["VACATION", "SICK", "EMERGENCY", "SERVICE INCENTIVE", "MATERNITY", "PATERNITY", "OTHER"] }),
    fileLeave: (input) => {
      const e = employees.find((x) => x.id === input.employeeId);
      if (!e) return failFields({ employeeId: "Choose the employee." });
      if (!input.from || !input.to || input.to < input.from) return failFields({ to: "The leave ends before it starts." });
      if (leave.some((l) => l.employeeId === e.id && l.status !== "Rejected" && l.from <= input.to && l.to >= input.from)) return failFields({ from: `${e.fullName} already has leave filed for part of these dates.` });
      const days = Math.round((Date.parse(input.to) - Date.parse(input.from)) / 86400000) + 1;
      const l: T.LeaveRequest = { id: nextLeaveId++, employeeId: e.id, employeeName: e.fullName, leaveType: input.leaveType, from: input.from, to: input.to, days, reason: input.reason, status: "Pending", decidedBy: "" };
      leave.push(l);
      audit(`Filed leave for ${e.fullName}`);
      return wait(l);
    },
    decideLeave: (id, status) => {
      const l = leave.find((x) => x.id === id);
      if (!l) return fail(404, "Leave request not found.");
      if (l.status !== "Pending") return fail(409, `This leave request was already ${l.status.toLowerCase()}.`);
      Object.assign(l, { status, decidedBy: actor() });
      audit(`${status} leave request #${id}`);
      return wait(l);
    },
    payroll: (from, to) => wait({ from, to, payroll: payrolls.filter((x) => x.periodStart <= to && x.periodEnd >= from),
      employees: employees.filter((e) => e.status !== "Inactive" && e.status !== "Separated").map((e) => ({ id: e.id, fullName: e.fullName, employeeNo: e.employeeNo, monthlySalary: e.monthlySalary,
        hasPayroll: payrolls.some((x) => x.employeeId === e.id && x.periodStart <= to && x.periodEnd >= from) })) }),
    previewPayroll: (input) => {
      const pv = mockPreview(input);
      return typeof pv === "string" ? failFields({ employeeId: pv }) : wait(pv);
    },
    generatePayroll: (input) => {
      const pv = mockPreview(input);
      if (typeof pv === "string") return failFields({ employeeId: pv });
      const e = employees.find((x) => x.id === input.employeeId)!;
      const clash = payrolls.find((x) => x.employeeId === e.id && x.periodStart <= input.to && x.periodEnd >= input.from);
      if (clash) return fail(409, `${e.fullName} already has a payroll for ${clash.periodStart} to ${clash.periodEnd}. Open it instead of generating another.`);
      const status = (input.status ?? "DRAFT") === "DRAFT" ? "Draft" : input.status === "FINAL" ? "Final" : "Paid";
      const deductions = fromCents(toCents(input.deductions || "0") + toCents(pv.loanDeductions));
      const p: MockPayroll = { id: nextPayrollId++, employeeId: e.id, employeeName: e.fullName, periodStart: input.from, periodEnd: input.to, basic: pv.basic, overtime: pv.overtime,
        allowances: pv.allowances, deductions, absences: pv.absences, lateUndertime: pv.lateUndertime, gross: pv.statutory.grossPay, netPay: pv.statutory.netPay,
        status, remarks: input.remarks ?? "", nextStatus: status === "Draft" ? "Final" : status === "Final" ? "Paid" : null, employee: e, statutory: pv.statutory, editable: status !== "Paid" };
      for (const l of loans.filter((x) => x.employeeId === e.id && x.status === "ACTIVE")) {
        const take = Math.min(toCents(l.monthlyDeduction), toCents(l.balance));
        l.balance = fromCents(toCents(l.balance) - take);
        if (toCents(l.balance) <= 0) l.status = "PAID";
      }
      payrolls.unshift(p);
      audit(`Generated payroll for ${e.employeeNo}, ${input.from} to ${input.to}`);
      return wait(p);
    },
    payslip: (id) => {
      const p = payrolls.find((x) => x.id === id);
      return p ? wait({ payroll: p, corporation: rates.corporationName, address: rates.address }) : fail(404, "Payroll not found.");
    },
    editStatutory: (id, st) => {
      const p = payrolls.find((x) => x.id === id);
      if (!p) return fail(404, "Payroll not found.");
      if (p.status === "Paid") return fail(409, "This payroll is already paid; its amounts can't be changed.");
      const other = toCents(p.deductions) + toCents(p.absences) + toCents(p.lateUndertime);
      const ded = toCents(st.sssEmployee) + toCents(st.philhealthEmployee) + toCents(st.pagibigEmployee) + toCents(st.withholdingTax) + other;
      p.statutory = { ...st, grossPay: p.gross, totalEmployeeDeductions: fromCents(ded), netPay: fromCents(toCents(p.gross) - ded),
        totalEmployerCost: fromCents(toCents(st.sssEmployer) + toCents(st.sssEcEmployer) + toCents(st.philhealthEmployer) + toCents(st.pagibigEmployer)) };
      p.netPay = p.statutory.netPay;
      audit(`Edited statutory amounts of payroll #${id}`);
      return wait(p);
    },
    advancePayroll: (id, status) => {
      const p = payrolls.find((x) => x.id === id);
      if (!p) return fail(404, "Payroll not found.");
      if (p.nextStatus !== status) return fail(409, p.nextStatus ? `A ${p.status.toLowerCase()} payroll can only move to ${p.nextStatus.toLowerCase()}.` : "This payroll is already paid.");
      p.status = status;
      p.nextStatus = status === "Final" ? "Paid" : null;
      p.editable = status !== "Paid";
      audit(`Payroll #${id} -> ${status}`);
      return wait(p);
    },
    calculate: (input) => {
      const n = (v: string) => toCents(v || "0") / 100;
      return wait(statutory(hrRules, n(input.basic) + n(input.overtime) + n(input.allowances), n(input.basic), n(input.deductions), 0, n(input.lateUndertime)));
    },
    thirteenthMonth: (year) => {
      const ceiling = Number(hrRules.hr_13th_month_ceiling);
      const rows = employees.map((e) => {
        const ps = payrolls.filter((x) => x.employeeId === e.id && x.periodEnd.startsWith(String(year)));
        const basic = ps.reduce((t, x) => t + toCents(x.basic), 0) / 100;
        const amt = Math.round(basic / 12 * 100) / 100, exempt = Math.min(amt, ceiling);
        return { employeeId: e.id, employeeName: e.fullName, employeeNo: e.employeeNo, payrolls: ps.length, basicTotal: basic.toFixed(2), thirteenth: amt.toFixed(2), exempt: exempt.toFixed(2), taxable: (amt - exempt).toFixed(2) };
      });
      return wait({ year, ceiling: ceiling.toFixed(2), rows, total: fromCents(rows.reduce((t, r) => t + toCents(r.thirteenth), 0)), taxable: fromCents(rows.reduce((t, r) => t + toCents(r.taxable), 0)) });
    },
    payrollReport: (year) => {
      const rows = payrolls.filter((x) => x.periodEnd.startsWith(String(year)));
      const sum = (f: (x: MockPayroll) => string) => fromCents(rows.reduce((t, x) => t + toCents(f(x)), 0));
      return wait({ year, rows, totals: { basic: sum((x) => x.basic), overtime: sum((x) => x.overtime), allowances: sum((x) => x.allowances), net: sum((x) => x.netPay),
        sssEmployee: sum((x) => x.statutory.sssEmployee), sssEmployer: sum((x) => x.statutory.sssEmployer), philhealthEmployee: sum((x) => x.statutory.philhealthEmployee),
        philhealthEmployer: sum((x) => x.statutory.philhealthEmployer), pagibigEmployee: sum((x) => x.statutory.pagibigEmployee), pagibigEmployer: sum((x) => x.statutory.pagibigEmployer),
        withholdingTax: sum((x) => x.statutory.withholdingTax) } });
    },
    loans: () => wait({ loans: [...loans].sort((x, y) => y.id - x.id), types: ["SSS", "PAG-IBIG", "COMPANY", "OTHER"] }),
    addLoan: (input) => {
      const e = employees.find((x) => x.id === input.employeeId);
      if (!e) return failFields({ employeeId: "Choose the employee." });
      if (!isAmount(input.originalAmount)) return failFields({ originalAmount: "Enter the loan amount." });
      if (!isAmount(input.monthlyDeduction)) return failFields({ monthlyDeduction: "Enter the monthly deduction." });
      const balance = input.balance ? toCents(input.balance) : toCents(input.originalAmount);
      if (balance > toCents(input.originalAmount)) return failFields({ balance: "The balance can't be more than the loan amount." });
      const l: T.HrLoan = { id: nextLoanId++, employeeId: e.id, employeeName: e.fullName, loanType: input.loanType, referenceNo: input.referenceNo, originalAmount: fromCents(toCents(input.originalAmount)),
        balance: fromCents(balance), monthlyDeduction: fromCents(toCents(input.monthlyDeduction)), status: balance > 0 ? "ACTIVE" : "PAID", notes: input.notes };
      loans.push(l);
      audit(`Added ${l.loanType} loan for ${e.fullName}`);
      return wait(l);
    },
    updateLoan: (id, input) => {
      const l = loans.find((x) => x.id === id);
      if (!l) return fail(404, "Loan not found.");
      if (input.status === "ACTIVE" && toCents(l.balance) <= 0) return failFields({ status: "This loan has no balance left." });
      Object.assign(l, { status: input.status, monthlyDeduction: fromCents(toCents(input.monthlyDeduction)), notes: input.notes });
      audit(`Updated loan #${id}`);
      return wait(l);
    },
    rules: () => wait(mockRules()),
    saveRules: (values) => {
      for (const [k, v] of Object.entries(values)) {
        if (!(k in RULE_DEFAULTS)) return failFields({ [k]: "Unknown rule." });
        if (!/^\d+(\.\d+)?$/.test(String(v).trim())) return failFields({ [k]: "Enter a number." });
      }
      Object.assign(hrRules, values);
      audit("Updated Payroll Rules");
      return wait(mockRules());
    },
  },

  reports: {
    get: (q) => wait(propertyReport(q)),
    exportExcel: async () => { throw new ApiError(0, "Excel export runs on the server. In the live system this downloads the report workbook."); },
  },
  admin: {
    users: ({ q, role, status, page, perPage }) => {
      const needle = q.trim().toLowerCase();
      const rows = users.filter((u) => (!needle || u.username.toLowerCase().includes(needle)) && (!role || u.role === role) && (!status || u.active === (status === "active")))
        .sort((a, b) => a.username.localeCompare(b.username));
      const roleList = (["super_admin", "admin", "manager", "staff", "accounting", "resident"] as T.Role[]).map((r) => ({ value: r, label: ROLES[r].label }));
      return wait({ users: rows.slice((page - 1) * perPage, page * perPage).map(userRow), total: rows.length, page, perPage,
        roles: roleList, creatableRoles: roleList.filter((r) => r.value !== "resident"), minPasswordLength: 10 });
    },
    createUser: (input) => {
      const fields: Record<string, string> = {};
      if (!/^[A-Za-z0-9._-]{3,80}$/.test(input.username)) fields.username = "Use 3–80 letters, numbers, dots, dashes or underscores.";
      else if (users.some((u) => u.username.toLowerCase() === input.username.toLowerCase())) fields.username = "That username is already taken.";
      if (input.password.length < 10) fields.password = "Use at least 10 characters.";
      if (input.role === "resident") fields.role = "Resident accounts are created under Resident Accounts, linked to the owner or tenant.";
      else if (!ROLES[input.role]) fields.role = "Choose a role from the list.";
      if (Object.keys(fields).length) return failFields(fields);
      const u: MockUser = { id: nextUserId++, username: input.username, role: input.role, active: true, createdAt: `${nowUtc()}Z`, mustChangePassword: true };
      users.push(u);
      audit(`Created user ${u.username} (${u.role})`);
      return wait(userRow(u));
    },
    resetUserPassword: (userId, newPassword) => {
      const u = users.find((x) => x.id === userId);
      if (!u) return fail(404, "User not found.");
      if (u.id === meId()) return fail(403, "To change your own password, use Change password in your account menu.");
      const row = userRow(u);
      if (!row.canResetPassword) return fail(403, row.resetBlockedReason!);
      if (newPassword.length < 10) return failFields({ newPassword: "Use at least 10 characters." });
      u.mustChangePassword = true;
      u.passwordChangedAt = `${nowUtc()}Z`;
      audit(`Reset password for user ${u.username}`);
      return wait(undefined);
    },
    updateUser: (userId, input) => {
      const u = users.find((x) => x.id === userId);
      if (!u) return fail(404, "User not found.");
      const row = userRow(u);
      if (!row.canEdit) return fail(u.role === "resident" ? 409 : 403, row.editBlockedReason!);
      const role = input.role ?? u.role;
      const active = input.active ?? u.active;
      if (role === "resident") return failFields({ role: "Resident accounts are managed under Resident Accounts." });
      if (!ROLES[role]) return failFields({ role: "Choose a role from the list." });
      if ((input.reason ?? "").length > 500) return failFields({ reason: "Keep the note under 500 characters." });
      if (role === u.role && active === u.active) return fail(400, "Nothing to change: the role and status are already like this.");
      const losesSa = u.role === "super_admin" && u.active && (role !== "super_admin" || !active);
      if (losesSa && !users.some((x) => x.role === "super_admin" && x.active && x.id !== u.id))
        return fail(409, "This is the last active Superadmin account. Make another account an active Superadmin first.");
      const changes = [role !== u.role ? `role ${ROLES[u.role].label} -> ${ROLES[role].label}` : "", active !== u.active ? (active ? "activated" : "deactivated") : ""].filter(Boolean);
      Object.assign(u, { role, active });
      audit(`Updated user ${u.username}: ${changes.join(", ")}`);
      return wait(userRow(u));
    },
    deleteUser: (userId) => {
      const u = users.find((x) => x.id === userId);
      if (!u) return fail(404, "User not found.");
      const row = userRow(u);
      if (!row.canDelete) return fail(u.role === "resident" ? 409 : 403, row.deleteBlockedReason!);
      users.splice(users.indexOf(u), 1);
      audit(`Deleted user ${u.username} (${u.role})`);
      return wait(undefined);
    },
    residentAccounts: ({ q, status, page, perPage }) => {
      const all = residentAccounts.map(residentRow).sort((a, b) => a.username.localeCompare(b.username));
      const statuses: T.ResidentAccountStatus[] = ["active", "ended", "inactive", "unlinked"];
      const counts = { ...Object.fromEntries(statuses.map((st) => [st, all.filter((a) => a.status === st).length])),
        notLinkedToPerson: all.filter((a) => a.unit && !a.linked).length } as T.ResidentAccountPage["counts"];
      const needle = q.trim().toLowerCase();
      const rows = all.filter((a) => (!needle || [a.username, a.displayName, a.unit?.unitNo ?? "", a.linkedName ?? ""].some((v) => v.toLowerCase().includes(needle)))
        && (!status || a.status === status));
      return wait({ accounts: rows.slice((page - 1) * perPage, page * perPage), total: rows.length, page, perPage, counts, minPasswordLength: 10 });
    },
    residentAccountOptions: () => wait({
      units: units.filter((u) => u.active).sort((a, b) => a.unitNo.localeCompare(b.unitNo, undefined, { numeric: true })).map((u) => ({
        id: u.id, unitNo: u.unitNo,
        people: people.filter((p) => p.unitId === u.id && p.status === "Current").map((p) => ({ id: p.id, personType: p.type, name: p.name,
          account: residentAccounts.find((a) => a.active && a.personType === p.type && a.personId === p.id)?.username ?? null })),
      })),
      personTypes: ["Owner", "Tenant"] as ("Owner" | "Tenant")[], minPasswordLength: 10,
    }),
    createResidentAccount: (input) => {
      const fields: Record<string, string> = {};
      if (!/^[A-Za-z0-9._-]{3,80}$/.test(input.username)) fields.username = "Use 3–80 letters, numbers, dots, dashes or underscores.";
      else if ([...users, ...residentAccounts].some((u) => u.username.toLowerCase() === input.username.toLowerCase())) fields.username = "That username is already taken.";
      if (input.password.length < 10) fields.password = "Use at least 10 characters.";
      const name = residentLinkErrors(input, fields, null);
      if (Object.keys(fields).length) return failFields(fields);
      const a: MockResidentAccount = { id: nextResidentAccountId++, username: input.username, unitId: input.unitId, personType: input.personType,
        personId: input.personId, displayName: name, active: true, mustChangePassword: true, createdAt: `${nowUtc()}Z` };
      residentAccounts.push(a);
      audit(`Created resident portal user ${a.username} for unit ${units.find((u) => u.id === a.unitId)!.unitNo}`);
      return wait(residentRow(a));
    },
    updateResidentAccount: (userId, input) => {
      const a = residentAccounts.find((x) => x.id === userId);
      if (!a) return fail(404, "Resident account not found.");
      const fields: Record<string, string> = {};
      const link = { unitId: input.unitId ?? a.unitId ?? undefined, personType: input.personType ?? a.personType,
        personId: "personId" in input ? input.personId ?? null : a.personId, displayName: input.displayName ?? a.displayName };
      const name = residentLinkErrors(link, fields, a.id, a);
      if ((input.reason ?? "").length > 500) fields.reason = "Keep the note under 500 characters.";
      if (Object.keys(fields).length) return failFields(fields);
      const next = { unitId: link.unitId!, personType: link.personType, personId: link.personId, displayName: name, active: input.active ?? a.active };
      if (next.unitId === a.unitId && next.personType === a.personType && next.personId === a.personId && next.displayName === a.displayName && next.active === a.active)
        return fail(400, "Nothing to change: the account is already like this.");
      const parts = [next.active !== a.active ? (next.active ? "reactivated" : "deactivated") : "", next.unitId !== a.unitId ? "unit changed" : "",
        next.personId !== a.personId || next.personType !== a.personType ? "link changed" : "", next.displayName !== a.displayName ? "name changed" : ""].filter(Boolean);
      Object.assign(a, next);
      audit(`Updated resident portal user ${a.username}: ${parts.join(", ")}`);
      return wait(residentRow(a));
    },
    resetResidentPassword: (userId, newPassword) => {
      const a = residentAccounts.find((x) => x.id === userId);
      if (!a) return fail(404, "Resident account not found.");
      if (newPassword.length < 10) return failFields({ newPassword: "Use at least 10 characters." });
      a.mustChangePassword = true;
      audit(`Reset password for resident portal user ${a.username}`);
      return wait(undefined);
    },
    auditLogs: ({ page, perPage, ...filter }) => {
      const rows = filterAudit(filter);
      return wait({ entries: rows.slice((page - 1) * perPage, page * perPage), total: rows.length, page, perPage,
        users: [...new Set(auditLogs.map((a) => a.username))].sort() });
    },
    exportAuditLogs: async (filter) => {
      const cell = (v: string) => `"${(/^[=+\-@]/.test(v) ? `'${v}` : v).replace(/"/g, '""')}"`;
      const csv = filterAudit(filter).map((a) => [a.at.replace("T", " ").slice(0, 19), a.username, a.action].map(cell).join(",")).join("\n");
      const link = document.createElement("a");
      link.href = URL.createObjectURL(new Blob([`﻿"When (UTC, prototype)","User","Action"\n${csv}\n`], { type: "text/csv" }));
      link.download = "cityland9-audit-log-prototype.csv";
      link.click();
      URL.revokeObjectURL(link.href);
    },
    rates: () => wait(rates),
    saveRates: (input) => {
      const { smtpPassword, smtpPasswordClear, ...rest } = input;
      // Same checks as backend/app/routes/rates.py (the ones the form can trigger).
      const fields: Record<string, string> = {};
      const num = (v: string, max = 100000) => /^\d{1,6}(\.\d{1,4})?$/.test(v.trim()) && Number(v) <= max;
      (["studio", "oneBed", "twoBed", "threeBed"] as const).forEach((k) => { if (!num(rest.ratesPerSqm[k])) fields[`ratesPerSqm.${k}`] = "Enter a rate, e.g. 85.00."; });
      if (!num(rest.parkingRatePerSqm)) fields.parkingRatePerSqm = "Enter a rate, e.g. 60.00.";
      if (!num(rest.storageRatePerSqm)) fields.storageRatePerSqm = "Enter a rate, e.g. 45.00.";
      if (!num(rest.waterRate)) fields.waterRate = "Enter a rate, e.g. 50.00.";
      if (!num(rest.penaltyRate, 100)) fields.penaltyRate = "Penalty rate (%) can't be more than 100.";
      if (!rest.corporationName.trim()) fields.corporationName = "Corporation name is required (it is printed on every SOA).";
      if (rest.smtpPort && !/^\d+$/.test(rest.smtpPort)) fields.smtpPort = "SMTP port must be a number from 1 to 65535, e.g. 587.";
      if (Object.keys(fields).length) return failFields(fields);
      Object.assign(rates, rest, { smtpPasswordSet: smtpPasswordClear ? false : rates.smtpPasswordSet || smtpPassword.length > 0 });
      audit("Updated Rates & Rules");
      return wait(rates);
    },
    collections: (months) => wait(Array.from({ length: months }, (_, i) => collectionsFor(addMonths(THIS_MONTH, i - months + 1)))),
    system: () => wait({
      backup: { ok: true, status: "ok", ageHours: 14.2, detail: null, maxAgeHours: 26 },
      database: { engine: "MySQL / MariaDB" }, export: { url: "/database/export.xlsx" },
      import: { maxUploadMb: 20, maxUnpackedMb: 200, maxRowsPerSheet: 50000, allowed: true },
    }),
    importDatabase: (file) => {
      if (!/\.(xlsx|xlsm)$/i.test(file.name)) return fail(400, "Please select an Excel .xlsx file.");
      audit(`Imported database from Excel (optimized): ${file.name}`);
      return wait({ message: "Prototype: nothing was imported (mock data).", counts: {} });
    },
  },
};
