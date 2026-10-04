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
    documents(unitId: number): Promise<T.ResidentDocument[]>;
  };
  dashboards: {
    property(): Promise<T.PropertyDashboard>;
    hr(): Promise<T.HrDashboard>;
    staff(): Promise<T.StaffDashboard>;
    system(): Promise<T.SystemDashboard>;
  };
  units: {
    /** Prototype only (mock pages that still use the simple unit list). */
    list(): Promise<T.Unit[]>;
    people(unitId: number): Promise<T.UnitPerson[]>;
    page(query: T.UnitQuery): Promise<T.UnitPage>;
    options(): Promise<T.UnitOptions>;
    detail(unitId: number): Promise<T.UnitDetail>;
    create(input: T.UnitCreate): Promise<T.UnitDetail>;
    update(unitId: number, input: T.UnitInput): Promise<T.UnitDetail>;
    addPerson(unitId: number, kind: "Owner" | "Tenant", input: T.PersonInput): Promise<T.UnitDetail>;
    updatePerson(unitId: number, kind: "Owner" | "Tenant", personId: number, input: T.PersonInput): Promise<T.UnitDetail>;
  };
  billing: {
    list(month: T.Month): Promise<T.BillRow[]>;
    detail(billId: number): Promise<T.BillDetail>;
    generate(month: T.Month): Promise<T.GenerateBillsResult>;
    recordPayment(billId: number, input: T.PaymentInput): Promise<T.RecordPaymentResult>;
    recalculate(billId: number): Promise<{ changed: boolean; detail: T.BillDetail }>;
    /** Email the month's SOAs: all bills, or only `billIds`. */
    emailSoas(month: T.Month, billIds?: number[]): Promise<T.SoaEmailResult>;
    preview(month: T.Month): Promise<T.GeneratePreview>;
    correctSoa(billId: number, input: T.SoaCorrection): Promise<T.BillDetail>;
    emailBill(billId: number): Promise<T.SoaEmailResult>;
    emailOverview(month: T.Month): Promise<T.SoaEmailOverview>;
  };
  advances: {
    list(query?: { unit?: number; status?: "" | "open" | "used" | "reversed" }): Promise<T.AdvanceList>;
    options(): Promise<{ units: T.AdvanceUnitOption[] }>;
    record(input: T.AdvanceInput): Promise<{ advance: T.AdvancePayment; receiptNo: string; receiptId: number }>;
  };
  receipts: {
    ledger(query: T.ReceiptQuery): Promise<T.ReceiptLedger>;
    detail(receiptId: number): Promise<T.OfficialReceiptDetail>;
    /** Void a receipt (reason required, one void per form token); its payments are reversed. */
    void(receiptId: number, reason: string, formToken: string): Promise<T.OfficialReceiptDetail>;
  };
  water: {
    month(month: T.Month): Promise<T.WaterMonth>;
    previousReading(unitId: number, month: T.Month): Promise<number>;
    /** Create or correct the unit's reading for the month. */
    save(input: T.WaterReadingInput): Promise<{ reading: T.WaterReadingAdmin; created: boolean; billUpdated: boolean }>;
    correct(readingId: number, input: Omit<T.WaterReadingInput, "unitId" | "month">): Promise<{ reading: T.WaterReadingAdmin; created: boolean; billUpdated: boolean }>;
    pay(readingId: number, input: T.WaterPaymentInput): Promise<{ reading: T.WaterReadingAdmin; receiptNo: string; receiptId: number; applied: T.Money }>;
  };
  gatePasses: {
    list(): Promise<T.AdminGatePass[]>;
    issue(input: T.GatePassInput): Promise<T.AdminGatePass>;
    review(passId: number, decision: "approve" | "reject", note: string): Promise<T.AdminGatePass>;
  };
  certificates: {
    list(): Promise<T.Certificate[]>;
    options(): Promise<T.CertificateUnit[]>;
    generate(input: T.CertificateInput): Promise<T.Certificate>;
    detail(id: number): Promise<T.CertificatePrint>;
  };
  expenses: {
    list(month: T.Month): Promise<T.ExpenseMonth>;
    add(input: Omit<T.Expense, "id">): Promise<T.Expense>;
  };
  maintenance: {
    board(): Promise<T.MaintenanceBoard>;
    create(input: T.TicketInput): Promise<T.MaintenanceTicket>;
    update(id: number, input: T.TicketUpdate): Promise<T.MaintenanceTicket>;
  };
  vendors: {
    list(): Promise<T.Vendor[]>;
    add(input: T.VendorInput): Promise<T.Vendor>;
    update(id: number, input: T.VendorInput): Promise<T.Vendor>;
  };
  documents: {
    list(): Promise<{ documents: T.DocumentRecord[]; units: { id: number; unitNo: string }[] }>;
    add(input: T.DocumentInput): Promise<T.DocumentRecord>;
  };
  announcements: {
    list(): Promise<T.Announcement[]>;
    publish(input: { title: string; message: string; audience: string; published: boolean }): Promise<T.Announcement>;
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
    users(query: T.UserQuery): Promise<T.UserPage>;
    createUser(input: { username: string; password: string; role: T.Role }): Promise<T.UserAccount>;
    /** Reset ANOTHER account's password (the signed-in user's own: auth.changePassword). */
    resetUserPassword(userId: number, newPassword: string): Promise<void>;
    updateUser(userId: number, input: T.UserUpdate): Promise<T.UserAccount>;
    deleteUser(userId: number): Promise<void>;
    residentAccounts(query: T.ResidentAccountQuery): Promise<T.ResidentAccountPage>;
    residentAccountOptions(): Promise<T.ResidentAccountOptions>;
    createResidentAccount(input: T.ResidentAccountLink & { username: string; password: string }): Promise<T.ResidentAccount>;
    updateResidentAccount(userId: number, input: T.ResidentAccountUpdate): Promise<T.ResidentAccount>;
    resetResidentPassword(userId: number, newPassword: string): Promise<void>;
    auditLogs(query: T.AuditLogQuery): Promise<T.AuditLogPage>;
    /** Download the filtered audit log as CSV (the whole result, not just the current page). */
    exportAuditLogs(query: Omit<T.AuditLogQuery, "page" | "perPage">): Promise<void>;
    rates(): Promise<T.RatesAndRules>;
    saveRates(input: T.RatesAndRulesUpdate): Promise<T.RatesAndRules>;
    collections(months: number): Promise<T.CollectionSummary[]>;
    system(): Promise<T.SystemOverview>;
    /** Import an Excel workbook (a database backup is taken first; nothing is imported if it fails). */
    importDatabase(file: File): Promise<T.ImportResult>;
  };
}

/** Build-time constant (Vite replaces it), so prototype-only UI is dropped from live builds. */
export const IS_MOCK = import.meta.env.MODE === "mock";
export const api: DataService = dataService;
/** Prototype only (no-op in live builds): which persona the Role Switcher selected. */
export const setMockRole: (role: T.Role | null) => void = setRole;
/** Prototype identities (null in live builds). */
export const PERSONAS: Record<T.Role, Persona> | null = personas;
