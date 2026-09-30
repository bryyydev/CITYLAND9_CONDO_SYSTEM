// THE data service used by every page. Pages never call fetch() directly.
//
// Two implementations share this interface:
//   liveApi  - the local Flask server (/api/...). Used by `npm run build` and `npm run dev`.
//   mockApi  - in-memory prototype data. Only with `npm run dev:mock` (vite --mode mock).
// vite.config.ts points "@data-source" at source.mock.ts only in mock mode, so the prototype
// data can't end up in a production bundle.
//
// Modules whose Flask API doesn't exist yet are listed in config/modules.ts with live: false;
// in live mode those pages show a link to the classic screen instead of calling the API.

import { PERSONAS as personas, dataService, setMockRole as setRole } from "@data-source";
import type { Persona } from "./personas";
import type * as T from "./types";

export interface DataService {
  auth: {
    me(): Promise<T.SessionUser>;
    login(username: string, password: string): Promise<{ user: T.SessionUser; csrfToken: string | null }>;
    logout(): Promise<void>;
    changePassword(currentPassword: string, newPassword: string): Promise<void>;
  };
  resident: {
    summary(unitId: number): Promise<T.ResidentSummary>;
    statements(unitId: number): Promise<T.SoaRow[]>;
    statement(unitId: number, billId: number): Promise<{ unit: { id: number; unitNo: string; type: string | null }; corporation: string; statement: T.SoaDetail }>;
    receipts(unitId: number): Promise<{ receipts: T.ReceiptRow[]; totalPaid: T.Money }>;
    receipt(unitId: number, receiptId: number): Promise<T.ReceiptDetail>;
    water(unitId: number): Promise<T.WaterReadingRow[]>;
    tickets(unitId: number): Promise<{ tickets: T.ResidentTicket[]; categories: string[]; priorities: string[] }>;
    fileTicket(unitId: number, input: { title: string; description: string; category: string; priority: string }): Promise<T.ResidentTicket>;
    gatePasses(unitId: number): Promise<{ passes: T.ResidentGatePass[]; types: T.GatePassType[]; maxDaysAhead: number }>;
    requestGatePass(unitId: number, input: { type: T.GatePassType; date: T.IsoDate; name: string; purpose: string }): Promise<T.ResidentGatePass>;
    cancelGatePass(unitId: number, passId: number): Promise<T.ResidentGatePass>;
    profile(unitId: number): Promise<T.ResidentProfile>;
    updateContact(unitId: number, input: { contactNo: string; email: string }): Promise<T.ResidentProfile>;
    notices(): Promise<T.Notice[]>;
  };
  dashboards: {
    property(): Promise<T.PropertyDashboard>;
    hr(): Promise<T.HrDashboard>;
    staff(): Promise<T.StaffDashboard>;
    system(): Promise<T.SystemDashboard>;
  };
  units: {
    list(): Promise<T.Unit[]>;
    people(unitId: number): Promise<T.UnitPerson[]>;
  };
  billing: {
    list(month: T.Month): Promise<T.BillRow[]>;
    detail(billId: number): Promise<T.BillDetail>;
    generate(month: T.Month): Promise<T.GenerateBillsResult>;
    recordPayment(billId: number, input: T.PaymentInput): Promise<T.RecordPaymentResult>;
    recalculate(billId: number): Promise<{ changed: boolean; detail: T.BillDetail }>;
    emailSoas(month: T.Month): Promise<{ sent: number; skipped: number }>;
  };
  advances: {
    list(): Promise<T.AdvancePayment[]>;
    record(input: T.AdvanceInput): Promise<T.AdvancePayment>;
  };
  receipts: {
    list(range: { from: T.IsoDate; to: T.IsoDate; q: string }): Promise<T.OfficialReceipt[]>;
  };
  water: {
    list(month: T.Month): Promise<T.WaterReadingAdmin[]>;
    previousReading(unitId: number, month: T.Month): Promise<number>;
    save(input: T.WaterReadingInput): Promise<T.WaterReadingAdmin>;
  };
  gatePasses: {
    list(): Promise<T.AdminGatePass[]>;
    issue(input: T.GatePassInput): Promise<T.AdminGatePass>;
    review(passId: number, decision: "approve" | "reject", note: string): Promise<T.AdminGatePass>;
  };
  certificates: {
    list(): Promise<T.Certificate[]>;
    generate(input: T.CertificateInput): Promise<T.Certificate>;
  };
  expenses: {
    list(month: T.Month): Promise<T.Expense[]>;
    add(input: Omit<T.Expense, "id">): Promise<T.Expense>;
  };
  maintenance: {
    list(): Promise<T.MaintenanceTicket[]>;
    update(id: number, input: T.TicketUpdate): Promise<T.MaintenanceTicket>;
  };
  vendors: { list(): Promise<T.Vendor[]> };
  documents: { list(): Promise<T.DocumentRecord[]> };
  announcements: {
    list(): Promise<T.Announcement[]>;
    publish(input: { title: string; message: string; audience: string }): Promise<T.Announcement>;
  };
  hr: {
    employees(): Promise<T.Employee[]>;
    attendance(date: T.IsoDate): Promise<T.AttendanceRecord[]>;
    saveAttendance(input: Omit<T.AttendanceRecord, "id" | "employeeName" | "lateMinutes">): Promise<T.AttendanceRecord>;
    leave(): Promise<T.LeaveRequest[]>;
    overtime(): Promise<T.OvertimeRequest[]>;
    decideLeave(id: number, status: "Approved" | "Rejected"): Promise<T.LeaveRequest>;
    decideOvertime(id: number, status: "Approved" | "Rejected"): Promise<T.OvertimeRequest>;
    payroll(period: T.Month): Promise<T.PayrollRecord[]>;
  };
  admin: {
    users(): Promise<T.UserAccount[]>;
    createUser(input: { username: string; password: string; role: T.Role }): Promise<T.UserAccount>;
    residentAccounts(): Promise<T.ResidentAccount[]>;
    auditLogs(q: string): Promise<T.AuditLog[]>;
    rates(): Promise<T.RatesAndRules>;
    saveRates(input: T.RatesAndRulesUpdate): Promise<T.RatesAndRules>;
    collections(months: number): Promise<T.CollectionSummary[]>;
  };
}

/** Build-time constant (Vite replaces it), so prototype-only UI is dropped from live builds. */
export const IS_MOCK = import.meta.env.MODE === "mock";
export const api: DataService = dataService;
/** Prototype only (no-op in live builds): which persona the Role Switcher selected. */
export const setMockRole: (role: T.Role | null) => void = setRole;
/** Prototype identities (null in live builds). */
export const PERSONAS: Record<T.Role, Persona> | null = personas;
