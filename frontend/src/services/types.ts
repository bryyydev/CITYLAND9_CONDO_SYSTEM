// Domain types shared by the mock and the live (Flask) data services.
//
// Rules carried over from the server (see docs/ and backend/legacy_app.py):
//  - Money travels as a string with two decimals ("1234.50"). The client only formats it.
//  - Bill status is computed by the server from the balance; the client never decides it.
//  - Official receipt numbers (OR-YYYY-NNNNNN) are issued by the server; never created here.
//  - Timestamps are UTC ISO strings; they are shown in Asia/Manila time.

export type Role = "super_admin" | "admin" | "manager" | "staff" | "accounting" | "resident";
export type Money = string;
export type IsoDate = string; // YYYY-MM-DD
export type IsoDateTime = string; // UTC
export type Month = string; // YYYY-MM

export interface SessionUser {
  username: string;
  role: Role;
  roleLabel: string;
  portal: string;
  unitId: number | null;
  permissions: string[];
  /** Friendly details; the prototype fills them from the role persona. */
  displayName?: string;
  email?: string;
  subtitle?: string;
  /** Temporary password: a new one must be chosen before anything else. */
  mustChangePassword?: boolean;
  passwordPolicy?: { minLength: number; maxLength: number };
}

// ---------------------------------------------------------------- resident portal (live API)
export type BillStatus = "Paid" | "Partially Paid" | "Overdue" | "Unpaid";

export interface SoaRow {
  id: number;
  billingMonth: Month;
  dueDate: IsoDate | null;
  status: BillStatus;
  total: Money;
  amountPaid: Money;
  balance: Money;
}

export interface SoaCharges {
  condoDues: Money;
  parking: Money;
  storage: Money;
  storageIncluded: boolean;
  storageFrom: Month;
  water: Money;
  waterUsage: number | null;
  waterRate: Money | null;
  waterPaidSeparately: boolean;
  penalty: Money;
  previousBalance: Money;
  advanceApplied: Money;
  currentCharges: Money;
}

export interface SoaPayment {
  date: IsoDate | null;
  amount: Money;
  receiptNo: string | null;
  method: PaymentMethod;
  type: string;
  reference: string;
  /** The payment's official receipt was voided; it no longer counts toward the bill. */
  reversed?: boolean;
}

export interface SoaDetail extends SoaRow {
  charges: SoaCharges;
  note: string;
  payments: SoaPayment[];
}

export interface ResidentSummary {
  unit: { id: number; unitNo: string; floor: string | null; type: string | null; status: string | null };
  residentName: string | null;
  outstandingBalance: Money;
  latestSoa: SoaRow | null;
  openTickets: number;
}

export type TicketStatus = "Open" | "In Progress" | "Resolved" | "Closed";
export interface ResidentTicket {
  ticketNo: string;
  category: string;
  title: string;
  description: string;
  priority: string;
  status: TicketStatus;
  resolution: string;
  requestedAt: IsoDateTime | null;
}

export interface Notice {
  id: number;
  title: string;
  message: string;
  publishDate: IsoDate | null;
}

export interface ReceiptItem {
  kind: "bill" | "water" | "advance";
  label: string;
  month: Month;
  amount: Money;
}

export interface ReceiptRow {
  id: number;
  receiptNo: string;
  date: IsoDate;
  amount: Money;
  method: PaymentMethod;
  reference: string;
  items: ReceiptItem[];
  /** Voided receipts keep their number but are not money received. */
  voided?: boolean;
  voidReason?: string;
}

export interface ReceiptDetail {
  corporation: string;
  address: string;
  unit: { id: number; unitNo: string };
  receivedFrom: string;
  receipt: ReceiptRow & { remarks: string; receivedBy: string; backfilled: boolean };
}

export interface WaterReadingRow {
  month: Month;
  readingDate: IsoDate | null;
  previous: number;
  current: number;
  usage: number;
  rate: Money;
  amount: Money;
  paidSeparately: boolean;
}

export type GatePassType = "Visitor" | "Delivery" | "Move-in" | "Move-out";
export type GatePassStatus = "Requested" | "Issued" | "Rejected" | "Cancelled";
export interface ResidentGatePass {
  id: number;
  type: GatePassType;
  date: IsoDate | null;
  name: string;
  purpose: string;
  status: GatePassStatus;
  reviewNote: string;
  requestedAt: IsoDateTime | null;
}

export interface ResidentProfile {
  username: string;
  displayName: string;
  unit: { unitNo: string; floor: string | null; type: string | null; areaSqm: number | null };
  personType: string;
  name: string | null;
  contactNo: string;
  email: string;
  moveIn: IsoDate | null;
  canEdit: boolean;
}

// ---------------------------------------------------------------- property & billing
export type PaymentMethod = "CASH" | "CHECK" | "ONLINE";
export type UnitType = "STUDIO TYPE" | "1 BEDROOM" | "2 BEDROOM" | "3 BEDROOM" | "PARKING" | "STORAGE";

export interface Unit {
  id: number;
  unitNo: string;
  floor: string;
  type: UnitType;
  areaSqm: number;
  ratePerSqm: Money;
  occupancy: "Owner" | "Tenant";
  status: "Occupied" | "Vacant";
  active: boolean;
  ownerName: string | null;
  tenantName: string | null;
  contactNo: string | null;
  email: string | null;
  parkingUnitNo: string | null;
  storageUnitNo: string | null;
  monthlyDues: Money;
}

export interface BillRow extends SoaRow {
  unitId: number;
  unitNo: string;
  unitType: UnitType;
  payerName: string;
}

export interface BillDetail extends SoaDetail {
  unitId: number;
  unitNo: string;
  payerName: string;
  manualOverride: boolean;
}

export interface GenerateBillsResult {
  month: Month;
  created: number;
  skipped: number;
}

export interface PaymentInput {
  amount: string;
  method: PaymentMethod;
  type: "FULL" | "PARTIAL";
  reference: string;
  date: IsoDate;
  remarks: string;
}

export interface RecordPaymentResult {
  receiptNo: string;
  appliedToBill: Money;
  excessToAdvance: Money;
  status: BillStatus;
}

export interface AdvancePayment {
  id: number;
  unitId: number;
  unitNo: string;
  date: IsoDate;
  amount: Money;
  startMonth: Month;
  months: number;
  monthlyAmount: Money;
  method: PaymentMethod;
  reference: string;
  applied: Money;
  remaining: Money;
  receiptNo: string;
}

export interface AdvanceInput {
  unitId: number;
  amount: string;
  startMonth: Month;
  months: number;
  method: PaymentMethod;
  reference: string;
  date: IsoDate;
  remarks: string;
}

export interface OfficialReceipt extends ReceiptRow {
  unitId: number;
  unitNo: string;
  receivedBy: string;
  backfilled: boolean;
}

export interface WaterReadingAdmin {
  id: number;
  unitId: number;
  unitNo: string;
  month: Month;
  previous: number;
  current: number;
  rate: Money;
  usage: number;
  amount: Money;
  readingDate: IsoDate;
  paid: boolean;
}

export interface WaterReadingInput {
  unitId: number;
  month: Month;
  previous: number;
  current: number;
  rate: string;
  readingDate: IsoDate;
}

// ---------------------------------------------------------------- operations & community
export interface AdminGatePass extends ResidentGatePass {
  unitNo: string;
  requestedBy: string | null;
  reviewedBy: string | null;
  source: "resident" | "office";
}

export interface GatePassInput {
  type: GatePassType;
  date: IsoDate;
  unitNo: string;
  name: string;
  purpose: string;
}

export type MoveType = "Move In" | "Move Out";
export interface Certificate {
  id: number;
  certificateNo: string;
  moveType: MoveType;
  personType: "Owner" | "Tenant";
  personName: string;
  unitNo: string;
  moveDate: IsoDate | null;
  certificateDate: IsoDate;
  issuedBy: string;
}

export interface CertificateInput {
  unitId: number;
  personType: "Owner" | "Tenant";
  personId: number;
  moveType: MoveType;
  certificateDate: IsoDate;
}

export interface UnitPerson {
  id: number;
  type: "Owner" | "Tenant";
  name: string;
  status: "Current" | "Past";
  moveIn: IsoDate | null;
  moveOut: IsoDate | null;
}

export interface Expense {
  id: number;
  date: IsoDate;
  category: string;
  description: string;
  amount: Money;
}

export interface MaintenanceTicket extends ResidentTicket {
  id: number;
  unitNo: string;
  assignedTo: string;
  vendor: string;
}

export interface TicketUpdate {
  status: TicketStatus;
  priority: string;
  assignedTo: string;
  vendor: string;
  resolution: string;
}

export interface Vendor {
  id: number;
  name: string;
  serviceType: string;
  contactPerson: string;
  contactNo: string;
  email: string;
  status: "Active" | "Inactive";
}

export interface DocumentRecord {
  id: number;
  title: string;
  category: string;
  audience: "admin" | "residents" | "unit";
  unitNo: string | null;
  fileName: string;
  addedAt: IsoDate;
}

export interface Announcement extends Notice {
  audience: string;
  published: boolean;
  createdBy: string;
}

// ---------------------------------------------------------------- HR & payroll
export interface Employee {
  id: number;
  employeeNo: string;
  fullName: string;
  position: string;
  department: string;
  status: "Active" | "On Leave" | "Resigned";
  dateHired: IsoDate;
  monthlySalary: Money;
  contactNo: string;
}

export type AttendanceStatus = "Present" | "Late" | "Absent" | "On Leave" | "Half Day";
export interface AttendanceRecord {
  id: number;
  employeeId: number;
  employeeName: string;
  date: IsoDate;
  timeIn: string | null;
  timeOut: string | null;
  status: AttendanceStatus;
  lateMinutes: number;
}

export type ApprovalStatus = "Pending" | "Approved" | "Rejected";
export interface LeaveRequest {
  id: number;
  employeeId: number;
  employeeName: string;
  leaveType: string;
  from: IsoDate;
  to: IsoDate;
  days: number;
  reason: string;
  status: ApprovalStatus;
}

export interface OvertimeRequest {
  id: number;
  employeeId: number;
  employeeName: string;
  date: IsoDate;
  hours: number;
  reason: string;
  status: ApprovalStatus;
}

export interface PayrollRecord {
  id: number;
  employeeId: number;
  employeeName: string;
  periodStart: IsoDate;
  periodEnd: IsoDate;
  basic: Money;
  overtime: Money;
  allowances: Money;
  deductions: Money;
  netPay: Money;
  status: "Draft" | "Released";
}

// ---------------------------------------------------------------- administration
export interface UserAccount {
  id: number;
  username: string;
  role: Role;
  roleLabel: string;
  active: boolean;
  createdAt: IsoDateTime | null;
  /** Temporary password (new account or reset): a new one must be chosen at the next sign-in. */
  mustChangePassword: boolean;
  passwordChangedAt: IsoDateTime | null;
  /** The signed-in user's own account. */
  isSelf: boolean;
  /** Decided by the server; the reason explains a protected account. */
  canEdit: boolean;
  editBlockedReason: string | null;
  canResetPassword: boolean;
  resetBlockedReason: string | null;
  canDelete: boolean;
  deleteBlockedReason: string | null;
}

export interface UserQuery {
  q: string;
  role: Role | "";
  status: "active" | "inactive" | "";
  page: number;
  perPage: number;
}

/** Change an account's role and/or status. The note is kept in the audit log. */
export interface UserUpdate {
  role?: Role;
  active?: boolean;
  reason?: string;
}

export interface UserPage {
  users: UserAccount[];
  total: number;
  page: number;
  perPage: number;
  roles: { value: Role; label: string }[];
  creatableRoles: { value: Role; label: string }[];
  minPasswordLength: number;
}

export interface ResidentAccount {
  id: number;
  username: string;
  unitNo: string;
  personType: "Owner" | "Tenant";
  displayName: string;
  linked: boolean;
  active: boolean;
}

export interface AuditLog {
  id: number;
  at: IsoDateTime;
  username: string;
  action: string;
}

export interface RatesAndRules {
  corporationName: string;
  address: string;
  ratesPerSqm: Record<"studio" | "oneBed" | "twoBed" | "threeBed", Money>;
  parkingRatePerSqm: Money;
  storageRatePerSqm: Money;
  waterRate: Money;
  waterAutoCompute: boolean;
  penaltyRate: string;
  penaltyIncludes: Record<"condo" | "parking" | "storage" | "water", boolean>;
  storageInTotalFrom: Month;
  onlinePaymentUrl: string;
  onlinePaymentInstructions: string;
  smtpHost: string;
  smtpPort: string;
  smtpSender: string;
  smtpUsername: string;
  /** D7: the SMTP password is write-only. The server only says whether one is saved. */
  smtpPasswordSet: boolean;
}

export interface RatesAndRulesUpdate extends Omit<RatesAndRules, "smtpPasswordSet"> {
  /** Empty = keep the saved password. */
  smtpPassword: string;
}

export interface CollectionSummary {
  month: Month;
  billed: Money;
  collected: Money;
  outstanding: Money;
  collectionRate: number;
}

export interface PropertyDashboard {
  month: Month;
  units: { residential: number; occupied: number; vacant: number; parking: number; storage: number };
  occupancyByFloor: { floor: string; occupied: number; vacant: number }[];
  billing: { generated: boolean; bills: number; paid: number; partial: number; overdue: number; unpaid: number };
  collections: CollectionSummary[];
  pendingGatePasses: number;
  openTickets: number;
  waterReadingsEntered: number;
}

export interface HrDashboard {
  headcount: number;
  presentToday: number;
  lateToday: number;
  onLeave: number;
  pendingLeave: number;
  pendingOvertime: number;
  monthlyPayroll: Money;
}

export interface StaffDashboard {
  gatePassesToday: number;
  pendingRequests: number;
  openTickets: number;
  expensesThisMonth: Money;
  certificatesThisMonth: number;
  attendanceEntered: number;
  attendanceExpected: number;
}

export interface SystemDashboard {
  users: Record<Role, number>;
  residentAccounts: number;
  unlinkedResidentAccounts: number;
  auditToday: number;
  lastBackup: IsoDateTime | null;
  schemaRevision: string;
}
