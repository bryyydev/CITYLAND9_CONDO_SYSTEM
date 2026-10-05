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
  /** The temporary password is a resident activation code (first sign-in). */
  activationPending?: boolean;
  /** Residents: every unit the account may open (one account per person). */
  units?: { id: number; unitNo: string; personType: string }[];
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
  /** Other charges (Edit SOA). */
  other?: Money;
  /** Adjustment (Edit SOA); negative = credit / discount. */
  adjustment?: Money;
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

/** How one dues line was priced (only when it really gives the charged amount; null = not recorded). */
export interface ChargeBasis {
  kind: "condo" | "parking" | "storage";
  basis: "sqm" | "manual";
  unitNo: string | null;
  area: string | null;
  rate: string | null;
}

export interface SoaDetail extends SoaRow {
  /** e.g. SOA-202609-00012 */
  statementNo: string;
  issueDate: IsoDate | null;
  periodStart: IsoDate;
  periodEnd: IsoDate;
  /** Amount due as first issued (null for statements issued before this was recorded). */
  issuedAmount: Money | null;
  account: { unitNo: string; unitType: string; floor: string; billTo: string; ownerName: string; tenantName: string };
  property: { name: string; address: string };
  charges: SoaCharges;
  bases: { condo: ChargeBasis | null; parking: ChargeBasis | null; storage: ChargeBasis | null; recorded: boolean };
  penaltyInfo: { amount: Money; rate: string; base: Money; eligible: string[]; overdue: boolean; recorded: boolean; explanation: string };
  /** Earlier months whose charges are still unpaid (they make up the previous balance). */
  previousUnpaid: { id: number; month: Month; dueDate: IsoDate | null; balance: Money }[];
  correctedByHand: boolean;
  payment: { instructions: string; onlinePaymentUrl: string | null; qrAvailable: boolean };
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

// ---- Units Directory (live: /api/units)
export interface UnitRef { id: number; unitNo: string }
export type UnitKind = "residential" | "PARKING" | "STORAGE";

export interface UnitRow {
  id: number;
  unitNo: string;
  floor: string;
  /** As stored (the classic list: STUDIO TYPE, 1 BEDROOM, …, PARKING, STORAGE). */
  type: string;
  areaSqm: number;
  ratePerSqm: string;
  /** The rate billing uses: the unit's own rate, or the Rates & Rules rate for its type. */
  effectiveRatePerSqm: string;
  autoRate: boolean;
  duesMode: "per_sqm" | "manual";
  manualMonthlyDues: Money;
  occupancy: "Owner" | "Tenant";
  status: "Occupied" | "Vacant";
  active: boolean;
  ownerName: string | null;
  tenantName: string | null;
  contactNo: string | null;
  email: string | null;
  parkingUnit: UnitRef | null;
  storageUnit: UnitRef | null;
  /** Parking/storage units: the residential unit it is assigned to. */
  assignedTo: UnitRef | null;
  /** What billing charges each month (the billing engine's own functions). */
  dues: { condo: Money; parking: Money; storage: Money };
  monthlyTotal: Money;
  /** Has an area but its condo dues come out as ₱0 (no rate for its type). */
  zeroDues: boolean;
  latestBill: { id: number; month: Month; status: string } | null;
}

export interface UnitQuery {
  q: string; kind: UnitKind; floor: string; status: "" | "Occupied" | "Vacant"; page: number; perPage: number;
  /** The search also finds past owners/tenants and contact numbers/emails. */
  past?: boolean;
}
export interface UnitPage { units: UnitRow[]; total: number; page: number; perPage: number; counts: Record<UnitKind, number> }

export interface UnitOptions {
  parking: { id: number; unitNo: string; floor: string; assignedTo: UnitRef | null }[];
  storage: { id: number; unitNo: string; floor: string; assignedTo: UnitRef | null }[];
  unitTypes: string[];
  floors: string[];
  /** Rates & Rules rate per type ("0" = no rate for that spelling). */
  typeRates: Record<string, string>;
}

export interface UnitPersonRecord {
  id: number;
  kind: "Owner" | "Tenant";
  name: string;
  contactNo: string;
  email: string;
  moveIn: IsoDate | null;
  moveOut: IsoDate | null;
  status: "Current" | "Past";
  notes: string;
  receiveSoaEmail: boolean;
  includeInSoa: boolean;
  representative: boolean;
}

export interface UnitDetail extends UnitRow {
  owners: UnitPersonRecord[];
  tenants: UnitPersonRecord[];
  bills: { id: number; month: Month; dueDate: IsoDate | null; total: Money; paid: Money; balance: Money; status: string }[];
}

export interface UnitInput {
  floor: string;
  type: string;
  areaSqm: string;
  ratePerSqm: string;
  autoRate: boolean;
  duesMode: "per_sqm" | "manual";
  manualMonthlyDues: string;
  occupancy: "Owner" | "Tenant";
  status: "Occupied" | "Vacant";
  parkingUnitId: number | null;
  storageUnitId: number | null;
}

export interface PersonInput {
  name: string;
  contactNo: string;
  email: string;
  moveIn: string;
  moveOut: string;
  status: "Current" | "Past";
  notes: string;
  receiveSoaEmail: boolean;
  includeInSoa: boolean;
  representative: boolean;
}

export interface UnitCreate extends UnitInput {
  unitNo: string;
  owner: PersonInput | null;
  tenant: PersonInput | null;
}

export interface BillRow extends SoaRow {
  unitId: number;
  unitNo: string;
  unitType: UnitType | string;
  payerName: string;
  /** Corrected by hand (Edit SOA): never re-priced automatically. */
  manualOverride?: boolean;
}

/** The amounts a hand-corrected SOA's total is made of (Edit SOA). */
export interface SoaAmounts {
  condoDues: Money;
  parking: Money;
  storage: Money;
  water: Money;
  other: Money;
  /** Negative = credit / discount. */
  adjustment: Money;
  penalty: Money;
  previousBalance: Money;
}

export interface BillDetail extends SoaDetail {
  unitId: number;
  unitNo: string;
  unitType?: string;
  payerName: string;
  manualOverride: boolean;
  contact?: { name: string; kind: "Owner" | "Tenant"; contactNo: string; email: string } | null;
  overdueMonths?: Month[];
  /** Stored amounts, for the Edit SOA form. */
  stored?: SoaAmounts;
  emailRecipients?: { name: string; email: string; type: "Owner" | "Tenant" }[];
  /** Payment QR image (served by the local server). */
  qrUrl?: string;
  /** The bill's month is in a closed period: payments and corrections are refused. */
  closed?: boolean;
  corporation?: string;
  address?: string;
  paymentInstructions?: string;
}

export interface GenerateBillsResult {
  month: Month;
  created: number;
  skipped: number;
}

/** What "Generate bills" would do for a month. */
export interface GeneratePreview {
  month: Month;
  residentialUnits: number;
  alreadyBilled: number;
  toCreate: number;
  readings: number;
  missingReadings: string[];
  /** Units that would be billed ₱0 condo dues (no rate). */
  zeroDuesUnits: string[];
  dueDate: IsoDate;
  closed: boolean;
  closedThrough: Month | null;
}

export interface PaymentInput {
  amount: string;
  method: PaymentMethod;
  type: "FULL" | "PARTIAL";
  reference: string;
  date: IsoDate;
  remarks: string;
  /** One payment per form: a second submit with the same token is refused (409). */
  formToken: string;
}

export interface RecordPaymentResult {
  receiptNo: string;
  receiptId?: number;
  appliedToBill: Money;
  excessToAdvance: Money;
  status: BillStatus;
}

export interface SoaCorrection extends SoaAmounts {
  dueDate: IsoDate | "";
  /** Required: shown on the SOA and kept in the audit log. */
  note: string;
}

export interface SoaEmailOverview {
  month: Month;
  smtpConfigured: boolean;
  optedIn: number;
  rows: { billId: number; unitNo: string; status: BillStatus; recipients: { name: string; email: string; type: string }[] }[];
}

export interface SoaEmailResult { sent: number; skipped: number; failed: number; errors: string[] }

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
  receiptId?: number | null;
  payerName?: string;
  /** Last billing month covered. */
  endMonth?: Month;
  remarks?: string;
  /** Its receipt was voided: no longer applied. */
  reversed?: boolean;
}

export interface AdvanceList {
  advances: AdvancePayment[];
  totals: { count: number; received: Money; remaining: Money };
}

export interface AdvanceUnitOption { id: number; unitNo: string; payerName: string; monthlyDues: Money }

export interface AdvanceInput {
  unitId: number;
  amount: string;
  startMonth: Month;
  months: number;
  method: PaymentMethod;
  reference: string;
  date: IsoDate;
  remarks: string;
  /** One payment per form: a second submit with the same token is refused (409). */
  formToken: string;
}

export interface OfficialReceipt extends ReceiptRow {
  unitId: number;
  unitNo: string;
  receivedBy: string;
  backfilled: boolean;
  voidedBy?: string;
  voidedAt?: IsoDateTime | null;
}

export interface ReceiptQuery {
  from: IsoDate;
  to: IsoDate;
  q: string;
  method: PaymentMethod | "";
  status: "" | "valid" | "void";
}

export interface ReceiptLedger {
  from: IsoDate;
  to: IsoDate;
  receipts: OfficialReceipt[];
  /** Receipts matching the filter (the list holds at most 1000). */
  total: number;
  truncated: boolean;
  /** Money received in the range: receipts that are not void. */
  collected: Money;
  byMethod: Record<PaymentMethod, Money>;
  voidCount: number;
}

export interface OfficialReceiptDetail extends ReceiptDetail {
  voidedBy: string;
  voidedAt: IsoDateTime | null;
  /** The receipt's month is in a closed period: it can't be voided. */
  closed: boolean;
  /** The signed-in user may void it, and it isn't void yet. */
  canVoid: boolean;
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
  paid?: boolean;
  payerName?: string;
  paidAmount?: Money;
  balance?: Money;
  status?: "Paid" | "Partially Paid" | "Unpaid";
  paidDate?: IsoDate | null;
  /** The month's bill, when generated (its water follows this reading). */
  billId?: number | null;
  receipts?: string[];
}

export interface WaterReadingInput {
  unitId: number;
  month: Month;
  /** Empty = last month's current reading. */
  previous: string;
  current: string;
  /** Empty = the Rates & Rules water rate. */
  rate: string;
  readingDate: IsoDate;
}

export interface WaterMonth {
  month: Month;
  readings: WaterReadingAdmin[];
  /** Residential units without a reading this month, with the reading to start from. */
  missing: { unitId: number; unitNo: string; payerName: string; previous: number; previousMonth: Month | null }[];
  /** Water still unpaid, any month. */
  unpaid: WaterReadingAdmin[];
  defaultRate: string;
  autoCompute: boolean;
  totals: { read: number; toRead: number; usage: number; amount: Money; unpaid: Money };
}

export interface WaterPaymentInput {
  amount: string;
  method: PaymentMethod;
  type: "FULL" | "PARTIAL";
  reference: string;
  date: IsoDate;
  formToken: string;
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

/** Residential units with every owner and tenant on record (current and past), for the certificate form. */
export interface CertificateUnit {
  id: number;
  unitNo: string;
  people: UnitPerson[];
}

/** One certificate with the letterhead details, for printing. */
export interface CertificatePrint {
  certificate: Certificate;
  corporation: string;
  address: string;
  unit: { unitNo: string; type: string; areaSqm: number | null };
  person: { contactNo: string; email: string; representative: boolean };
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

export interface ExpenseMonth {
  month: Month;
  expenses: Expense[];
  total: Money;
  byCategory: Record<string, Money>;
  /** Categories used before (suggestions for the form). */
  categories: string[];
}

export interface MaintenanceTicket extends ResidentTicket {
  id: number;
  unitId: number;
  unitNo: string;
  assignedTo: string;
  vendorId: number | null;
  vendor: string;
  source: "resident" | "office";
}

export interface MaintenanceBoard {
  tickets: MaintenanceTicket[];
  categories: string[];
  priorities: string[];
  statuses: TicketStatus[];
  /** Active vendors only. */
  vendors: { id: number; name: string; serviceType: string }[];
  units: { id: number; unitNo: string }[];
}

export interface TicketInput {
  unitId: number;
  category: string;
  priority: string;
  title: string;
  description: string;
}

export interface TicketUpdate {
  status: TicketStatus;
  priority: string;
  assignedTo: string;
  vendorId: number | null;
  resolution: string;
}

export interface Vendor {
  id: number;
  name: string;
  serviceType: string;
  contactPerson: string;
  contactNo: string;
  email: string;
  address: string;
  notes: string;
  status: "Active" | "Inactive";
}
export type VendorInput = Omit<Vendor, "id">;

export type DocumentAudience = "admin" | "residents" | "unit";
export interface DocumentRecord {
  id: number;
  title: string;
  category: string;
  description: string;
  audience: DocumentAudience;
  unitId: number | null;
  unitNo: string | null;
  fileName: string;
  /** Where the file is kept (shared folder / cabinet); files are not uploaded. */
  filePath: string;
  addedAt: IsoDate | null;
  addedBy: string;
}

export interface DocumentInput {
  title: string;
  category: string;
  description: string;
  audience: DocumentAudience;
  unitId: number | null;
  fileName: string;
  filePath: string;
}

/** A document record as a resident sees it (shared with all residents, or with their unit). */
export interface ResidentDocument {
  id: number;
  title: string;
  category: string;
  description: string;
  fileName: string;
  filePath: string;
  forUnit: boolean;
  addedAt: IsoDate | null;
}

export interface Announcement extends Notice {
  audience: string;
  published: boolean;
  createdBy: string;
}

// ---------------------------------------------------------------- HR & payroll
export type EmploymentStatus = "Active" | "On Leave" | "Inactive" | "Separated";
export interface Employee {
  id: number;
  employeeNo: string;
  fullName: string;
  position: string;
  department: string;
  status: EmploymentStatus;
  contactNo: string;
  email: string;
  dateHired: IsoDate | null;
  monthlySalary: Money;
  notes: string;
  /** Has attendance, payroll, leave, overtime or loan records (then it can't be deleted). */
  hasRecords?: boolean;
}
export type EmployeeInput = Omit<Employee, "id" | "hasRecords">;

/** PRESENT, LATE, UNDERTIME, LATE/UNDERTIME, ABSENT, LEAVE, REST DAY (as the server stores them). */
export type AttendanceStatus = string;
export interface AttendanceRecord {
  id: number;
  employeeId: number;
  employeeName: string;
  date: IsoDate;
  timeIn: string | null;
  timeOut: string | null;
  status: AttendanceStatus;
  remarks: string;
  /** After 8:00 plus the grace period, as payroll counts it. */
  lateMinutes: number;
  /** Before 17:00 (undertime statuses only). */
  undertimeMinutes: number;
}
export interface AttendanceDay {
  date: IsoDate;
  statuses: AttendanceStatus[];
  employees: { id: number; fullName: string; employeeNo: string; status: EmploymentStatus; record: AttendanceRecord | null }[];
  schedule: { start: string; end: string; graceMinutes: number };
}
/** status "" removes the day's record. */
export interface AttendanceInput { employeeId: number; status: AttendanceStatus; timeIn: string | null; timeOut: string | null; remarks: string }
export interface AttendanceHistory {
  employee: Employee;
  from: IsoDate;
  to: IsoDate;
  records: AttendanceRecord[];
  counts: Record<string, number>;
  lateMinutes: number;
  undertimeMinutes: number;
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
  decidedBy: string;
}
export interface OvertimeRequest {
  id: number;
  employeeId: number;
  employeeName: string;
  date: IsoDate;
  hours: number;
  multiplier: number;
  amount: Money;
  reason: string;
  status: ApprovalStatus;
  decidedBy: string;
}

export type PayrollStatus = "Draft" | "Final" | "Paid";
export interface PayrollRecord {
  id: number;
  employeeId: number;
  employeeName: string;
  periodStart: IsoDate;
  periodEnd: IsoDate;
  basic: Money;
  overtime: Money;
  allowances: Money;
  /** Other deductions incl. loan deductions. */
  deductions: Money;
  absences: Money;
  lateUndertime: Money;
  gross: Money;
  netPay: Money;
  status: PayrollStatus;
  remarks: string;
  nextStatus: PayrollStatus | null;
}
export interface Statutory {
  sssEmployee: Money; sssEmployer: Money; sssEcEmployer: Money;
  philhealthEmployee: Money; philhealthEmployer: Money;
  pagibigEmployee: Money; pagibigEmployer: Money;
  withholdingTax: Money; thirteenthMonth: Money;
  grossPay: Money; totalEmployeeDeductions: Money; totalEmployerCost: Money; netPay: Money;
}
export interface PayrollDetail extends PayrollRecord {
  employee: Employee | null;
  statutory: Statutory;
  /** false once Paid. */
  editable: boolean;
}
export interface PayrollPeriod {
  from: IsoDate;
  to: IsoDate;
  payroll: PayrollRecord[];
  employees: { id: number; fullName: string; employeeNo: string; monthlySalary: Money; hasPayroll: boolean }[];
}
export interface PayrollInput {
  employeeId: number;
  from: IsoDate;
  to: IsoDate;
  /** "" = the monthly salary / the approved overtime of the period. */
  basic: string;
  overtime: string;
  allowances: string;
  deductions: string;
  status?: "DRAFT" | "FINAL" | "PAID";
  remarks?: string;
  formToken?: string;
}
export interface PayrollPreview {
  basic: Money; overtime: Money; approvedOvertime: Money; allowances: Money; deductions: Money;
  loanDeductions: Money; absences: Money; lateUndertime: Money; statutory: Statutory;
}
export interface CalculatorInput { basic: string; overtime: string; allowances: string; deductions: string; lateUndertime: string }
export interface ThirteenthMonth {
  year: number;
  ceiling: Money;
  rows: { employeeId: number; employeeName: string; employeeNo: string; payrolls: number; basicTotal: Money; thirteenth: Money; exempt: Money; taxable: Money }[];
  total: Money;
  taxable: Money;
}
export interface PayrollReport { year: number; rows: PayrollRecord[]; totals: Record<string, Money> }
export interface HrLoan {
  id: number;
  employeeId: number;
  employeeName: string;
  loanType: string;
  referenceNo: string;
  originalAmount: Money;
  balance: Money;
  monthlyDeduction: Money;
  status: "ACTIVE" | "HOLD" | "PAID";
  notes: string;
}
export interface LoanInput { employeeId: number; loanType: string; referenceNo: string; originalAmount: string; balance: string; monthlyDeduction: string; notes: string }
export interface PayrollRule { key: string; label: string; group: string; unit: string; value: string; default: string }

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

/** active: can use the portal · ended: the linked owner/tenant moved out or the unit closed ·
 *  inactive: deactivated by an administrator · unlinked: a resident login with no unit yet. */
export type ResidentAccountStatus = "active" | "pending" | "ended" | "inactive" | "unlinked";

export interface ResidentAccount {
  /** The login's user id. */
  id: number;
  username: string;
  displayName: string;
  unit: { id: number; unitNo: string } | null;
  personType: "Owner" | "Tenant";
  /** The owner/tenant record the login is linked to (null: not linked to a person). */
  personId: number | null;
  linked: boolean;
  linkedName: string | null;
  linkedStatus: string | null;
  status: ResidentAccountStatus;
  /** Why the resident can't use the portal (null when active). */
  statusReason: string | null;
  active: boolean;
  mustChangePassword: boolean;
  /** Pending activation: when the current activation code stops working (null: no expiry / not pending). */
  activationExpiresAt: IsoDateTime | null;
  activationExpired: boolean;
  /** Units this account may open (one account per person; history included). */
  links: ResidentUnitLink[];
  createdAt: IsoDateTime | null;
}

export interface ResidentUnitLink {
  id: number | null;
  unitId: number;
  unitNo: string;
  personType: "Owner" | "Tenant";
  personId: number | null;
  personName: string | null;
  active: boolean;
  /** Why this link gives no access now (moved out, unit inactive), or null. */
  problem: string | null;
  endedAt: IsoDateTime | null;
  endReason: string | null;
}

/** none | pending activation | active | disabled | ended (no unit left) */
export type PortalState = "none" | "pending" | "active" | "disabled" | "ended";
export interface PortalPerson {
  personType: "Owner" | "Tenant";
  personId: number;
  name: string;
  unitId: number;
  unitNo: string;
  email: string;
  contactNo: string;
  state: PortalState;
  account: { id: number; username: string } | null;
}
export interface PortalPeoplePage { people: PortalPerson[]; counts: Record<PortalState, number>; activationDays: number }

/** Shown ONCE, to the administrator who issued it. */
export interface ActivationCredentials { username: string; activationCode: string; expiresAt: IsoDateTime }
export interface ProvisionCandidate { userId: number; username: string; displayName: string; units: string[]; reasons: string[] }
export interface ProvisionInput { personType: "Owner" | "Tenant"; personId: number; mode: "auto" | "new" | "link"; linkUserId?: number; formToken: string }
export interface ProvisionResult { account: ResidentAccount; credentials: ActivationCredentials | null; linked: boolean }

export interface ResidentAccountQuery {
  q: string;
  status: ResidentAccountStatus | "";
  page: number;
  perPage: number;
}

export interface ResidentAccountPage {
  accounts: ResidentAccount[];
  total: number;
  page: number;
  perPage: number;
  counts: Record<ResidentAccountStatus, number> & { notLinkedToPerson: number };
  minPasswordLength: number;
}

export interface ResidentAccountOptions {
  /** Active units with their CURRENT owners and tenants; `account` = username already linked to that person. */
  units: { id: number; unitNo: string; people: { id: number; personType: "Owner" | "Tenant"; name: string; account: string | null }[] }[];
  personTypes: ("Owner" | "Tenant")[];
  minPasswordLength: number;
}

export interface ResidentAccountLink {
  unitId: number;
  personType: "Owner" | "Tenant";
  personId: number | null;
  displayName: string;
}

export interface ResidentAccountUpdate extends Partial<ResidentAccountLink> {
  active?: boolean;
  reason?: string;
}

export interface AuditLog {
  id: number;
  at: IsoDateTime;
  username: string;
  action: string;
  entityType?: string | null;
  entityId?: number | null;
  /** Note entered by the person who made the change. */
  reason?: string | null;
  /** Structured before/after values recorded with the entry (never passwords). */
  details?: unknown;
}

export interface AuditLogQuery {
  q: string;
  user: string;
  /** Philippine dates (YYYY-MM-DD), inclusive; "" = no limit. */
  from: IsoDate | "";
  to: IsoDate | "";
  page: number;
  perPage: number;
}

export interface AuditLogPage {
  entries: AuditLog[];
  total: number;
  page: number;
  perPage: number;
  /** Usernames that appear in the log (for the filter). */
  users: string[];
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
  /** Last closed billing month: payments, voids, bill generation and corrections dated in it or earlier are refused. "" = none closed. */
  booksClosedThrough: Month | "";
  /** Read-only: bills fall due on this day of the month. */
  dueDay: number;
  onlinePaymentUrl: string;
  onlinePaymentInstructions: string;
  smtpHost: string;
  smtpPort: string;
  smtpSender: string;
  smtpUsername: string;
  /** D7: the SMTP password is write-only. The server only says whether one is saved. */
  smtpPasswordSet: boolean;
}

export interface RatesAndRulesUpdate extends Omit<RatesAndRules, "smtpPasswordSet" | "dueDay"> {
  /** Empty = keep the saved password. */
  smtpPassword: string;
  /** Remove the saved SMTP password. */
  smtpPasswordClear: boolean;
}

/** Settings page: backup status and Excel export / import (GET /api/admin/system). */
export interface SystemOverview {
  backup: { ok: boolean; status: string | null; ageHours: number | null; detail: string | null; maxAgeHours: number };
  database: { engine: string };
  export: { url: string };
  import: { maxUploadMb: number; maxUnpackedMb: number; maxRowsPerSheet: number; allowed: boolean };
}

export interface ImportResult {
  message: string;
  /** Per sheet found in the workbook: rows inserted / updated. */
  counts: Record<string, { inserted: number; updated: number }>;
}

// ---------------------------------------------------------------- reports
export type ReportPeriod = "daily" | "weekly" | "monthly" | "yearly" | "custom";
export interface ReportQuery {
  period: ReportPeriod;
  /** Custom range only (YYYY-MM-DD). */
  from?: IsoDate;
  to?: IsoDate;
}
export interface ReportTransaction {
  date: IsoDate;
  /** null: recorded without an official receipt (e.g. imported). */
  receiptNo: string | null;
  receiptId: number | null;
  unitNo: string;
  method: string;
  reference: string;
  description: string;
  amount: Money;
}
export interface PropertyReport {
  period: ReportPeriod;
  label: string;
  from: IsoDate;
  to: IsoDate;
  property: { residentialUnits: number; occupied: number; vacant: number; currentTenants: number; withParking: number };
  /** Bills generated in the period: their own charges (previous balances are not counted again). */
  billing: { bills: number; charged: Money; advanceCredits: Money; charges: Record<"condo" | "parking" | "storage" | "water" | "other" | "adjustment" | "penalty", Money> };
  /** Money received: official receipts (voided ones excluded) plus payments recorded without a receipt. */
  collections: { total: Money; bills: Money; water: Money; advances: Money; byMethod: Record<string, Money>; receipts: number; withoutReceipt: number; voided: { count: number; amount: Money } };
  expenses: { total: Money; byCategory: Record<string, Money>; rows: Expense[] };
  netCashFlow: Money;
  /** What units still owe today, each unit counted once. */
  receivables: { total: Money; units: number; overdueUnits: number };
  transactions: ReportTransaction[];
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
