// Live data service: the local Flask server.
// Modules whose API isn't built yet reject with NotConnectedError, and config/modules.ts marks
// them live: false so their pages link to the classic screen instead of calling here.

import type { DataService } from "./api";
import { NotConnectedError, downloadFile, http, setCsrfToken } from "./http";
import type * as T from "./types";

const notConnected = (feature: string) => () => Promise.reject(new NotConnectedError(feature));
const r = (unitId: number) => `/resident/units/${unitId}`;
const reportParams = (q: T.ReportQuery) => {
  const params = new URLSearchParams({ period: q.period });
  if (q.period === "custom") {
    if (q.from) params.set("from", q.from);
    if (q.to) params.set("to", q.to);
  }
  return params;
};
const auditParams = (q: Partial<T.AuditLogQuery>) => {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v !== "" && v !== undefined) params.set(k, String(v));
  return params;
};

export const liveApi: DataService = {
  auth: {
    me: async () => (await http<{ user: T.SessionUser }>("/auth/me")).user,
    login: async (username, password) => {
      const data = await http<{ user: T.SessionUser; csrfToken: string }>("/auth/login", { method: "POST", body: { username, password } });
      setCsrfToken(data.csrfToken); // the session was renewed, so is the token
      return data;
    },
    logout: async () => {
      try {
        await http("/auth/logout", { method: "POST" });
      } finally {
        setCsrfToken(null);
      }
    },
    changePassword: (currentPassword, newPassword) =>
      http("/auth/password", { method: "POST", body: { currentPassword, newPassword } }),
  },

  resident: {
    summary: (unitId) => http(`${r(unitId)}/summary`),
    statements: async (unitId) => (await http<{ statements: T.SoaRow[] }>(`${r(unitId)}/soa`)).statements,
    statement: (unitId, billId) => http(`${r(unitId)}/soa/${billId}`),
    receipts: (unitId) => http(`${r(unitId)}/receipts`),
    receipt: (unitId, receiptId) => http(`${r(unitId)}/receipts/${receiptId}`),
    downloadStatementPdf: (unitId, billId) => downloadFile(`${r(unitId)}/soa/${billId}/pdf`, { expect: "application/pdf", fallbackName: `SOA_${billId}.pdf` }),
    water: async (unitId) => (await http<{ readings: T.WaterReadingRow[] }>(`${r(unitId)}/water`)).readings,
    tickets: (unitId) => http(`${r(unitId)}/maintenance`),
    fileTicket: async (unitId, input) =>
      (await http<{ ticket: T.ResidentTicket }>(`${r(unitId)}/maintenance`, { method: "POST", body: input })).ticket,
    gatePasses: (unitId) => http(`${r(unitId)}/gate-passes`),
    requestGatePass: async (unitId, input) =>
      (await http<{ pass: T.ResidentGatePass }>(`${r(unitId)}/gate-passes`, { method: "POST", body: input })).pass,
    cancelGatePass: async (unitId, passId) =>
      (await http<{ pass: T.ResidentGatePass }>(`${r(unitId)}/gate-passes/${passId}/cancel`, { method: "POST" })).pass,
    profile: async (unitId) => (await http<{ profile: T.ResidentProfile }>(`${r(unitId)}/profile`)).profile,
    updateContact: async (unitId, input) =>
      (await http<{ profile: T.ResidentProfile }>(`${r(unitId)}/profile`, { method: "PUT", body: input })).profile,
    notices: async () => (await http<{ notices: T.Notice[] }>("/resident/notices")).notices,
    documents: async (unitId) => (await http<{ documents: T.ResidentDocument[] }>(`${r(unitId)}/documents`)).documents,
  },

  dashboards: {
    property: notConnected("The property dashboard"),
    hr: notConnected("The HR dashboard"),
    staff: notConnected("The staff dashboard"),
    system: notConnected("The system dashboard"),
  },
  units: {
    list: notConnected("Units"),
    people: notConnected("Unit owners and tenants"),
    page: (q) => {
      const params = new URLSearchParams({ kind: q.kind, page: String(q.page), perPage: String(q.perPage) });
      if (q.q.trim()) params.set("q", q.q.trim());
      if (q.floor) params.set("floor", q.floor);
      if (q.status) params.set("status", q.status);
      if (q.past) params.set("past", "1");
      return http(`/units?${params}`);
    },
    options: () => http("/units/options"),
    detail: async (id) => (await http<{ unit: T.UnitDetail }>(`/units/${id}`)).unit,
    create: async (input) => (await http<{ unit: T.UnitDetail }>("/units", { method: "POST", body: input })).unit,
    update: async (id, input) => (await http<{ unit: T.UnitDetail }>(`/units/${id}`, { method: "PUT", body: input })).unit,
    addPerson: async (id, kind, input) => {
      const r = await http<{ unit: T.UnitDetail; createdPersonId?: number }>(`/units/${id}/${kind === "Owner" ? "owners" : "tenants"}`, { method: "POST", body: input });
      return { ...r.unit, createdPersonId: r.createdPersonId };
    },
    updatePerson: async (id, kind, personId, input) =>
      (await http<{ unit: T.UnitDetail }>(`/units/${id}/${kind === "Owner" ? "owners" : "tenants"}/${personId}`, { method: "PUT", body: input })).unit,
  },
  billing: {
    list: async (month) => (await http<{ bills: T.BillRow[] }>(`/billing?month=${month}`)).bills,
    detail: async (id) => (await http<{ bill: T.BillDetail }>(`/billing/${id}`)).bill,
    generate: (month) => http("/billing/generate", { method: "POST", body: { month } }),
    recordPayment: (id, input) => http(`/billing/${id}/payments`, { method: "POST", body: input }),
    recalculate: async (id) => {
      const r = await http<{ changed: boolean; bill: T.BillDetail }>(`/billing/${id}/recalculate`, { method: "POST" });
      return { changed: r.changed, detail: r.bill };
    },
    emailSoas: (month, billIds) => http("/billing/email/send", { method: "POST", body: billIds ? { month, billIds } : { month } }),
    preview: (month) => http(`/billing/preview?month=${month}`),
    correctSoa: async (id, input) => (await http<{ bill: T.BillDetail }>(`/billing/${id}/soa`, { method: "PUT", body: input })).bill,
    emailBill: (id) => http(`/billing/${id}/email`, { method: "POST" }),
    emailOverview: (month) => http(`/billing/email?month=${month}`),
    downloadSoaPdf: (id, fallbackName) => downloadFile(`/billing/${id}/soa.pdf`, { expect: "application/pdf", fallbackName }),
  },
  advances: {
    list: (q = {}) => {
      const params = new URLSearchParams();
      if (q.unit) params.set("unit", String(q.unit));
      if (q.status) params.set("status", q.status);
      return http(`/advances?${params}`);
    },
    options: () => http("/advances/options"),
    record: (input) => http("/advances", { method: "POST", body: input }),
  },
  receipts: {
    ledger: (q) => {
      const params = new URLSearchParams({ from: q.from, to: q.to });
      if (q.q.trim()) params.set("q", q.q.trim());
      if (q.method) params.set("method", q.method);
      if (q.status) params.set("status", q.status);
      return http(`/receipts?${params}`);
    },
    detail: (id) => http(`/receipts/${id}`),
    void: (id, reason, formToken) => http(`/receipts/${id}/void`, { method: "POST", body: { reason, formToken } }),
  },
  water: {
    month: (month) => http(`/water?month=${month}`),
    previousReading: async (unitId, month) => (await http<{ previous: number }>(`/water/previous?unit=${unitId}&month=${month}`)).previous,
    save: (input) => http("/water", { method: "POST", body: input }),
    correct: (id, input) => http(`/water/${id}`, { method: "PUT", body: input }),
    pay: (id, input) => http(`/water/${id}/payments`, { method: "POST", body: input }),
  },
  gatePasses: {
    list: async () => (await http<{ passes: T.AdminGatePass[] }>("/gate-passes")).passes,
    issue: async (input) => (await http<{ pass: T.AdminGatePass }>("/gate-passes", { method: "POST", body: input })).pass,
    review: async (id, decision, note) => (await http<{ pass: T.AdminGatePass }>(`/gate-passes/${id}/review`, { method: "POST", body: { decision, note } })).pass,
  },
  certificates: {
    list: async () => (await http<{ certificates: T.Certificate[] }>("/certificates")).certificates,
    options: async () => (await http<{ units: T.CertificateUnit[] }>("/certificates/options")).units,
    generate: async (input) => (await http<{ certificate: T.Certificate }>("/certificates", { method: "POST", body: input })).certificate,
    detail: (id) => http(`/certificates/${id}`),
  },
  expenses: {
    list: (month) => http(`/expenses?month=${month}`),
    add: async (input) => (await http<{ expense: T.Expense }>("/expenses", { method: "POST", body: input })).expense,
  },
  maintenance: {
    board: () => http("/maintenance"),
    create: async (input) => (await http<{ ticket: T.MaintenanceTicket }>("/maintenance", { method: "POST", body: input })).ticket,
    update: async (id, input) => (await http<{ ticket: T.MaintenanceTicket }>(`/maintenance/${id}`, { method: "PUT", body: input })).ticket,
  },
  vendors: {
    list: async () => (await http<{ vendors: T.Vendor[] }>("/vendors")).vendors,
    add: async (input) => (await http<{ vendor: T.Vendor }>("/vendors", { method: "POST", body: input })).vendor,
    update: async (id, input) => (await http<{ vendor: T.Vendor }>(`/vendors/${id}`, { method: "PUT", body: input })).vendor,
  },
  documents: {
    list: () => http("/documents"),
    add: async (input) => (await http<{ document: T.DocumentRecord }>("/documents", { method: "POST", body: input })).document,
  },
  announcements: {
    list: async () => (await http<{ announcements: T.Announcement[] }>("/announcements")).announcements,
    publish: async (input) => (await http<{ announcement: T.Announcement }>("/announcements", { method: "POST", body: input })).announcement,
  },
  hr: {
    employees: () => http("/hr/employees"),
    addEmployee: async (input) => (await http<{ employee: T.Employee }>("/hr/employees", { method: "POST", body: input })).employee,
    updateEmployee: async (id, input) => (await http<{ employee: T.Employee }>(`/hr/employees/${id}`, { method: "PUT", body: input })).employee,
    deleteEmployee: (id) => http(`/hr/employees/${id}`, { method: "DELETE" }),
    attendanceDay: (date) => http(`/hr/attendance?date=${date}`),
    saveAttendance: (date, records) => http("/hr/attendance", { method: "PUT", body: { date, records } }),
    attendanceHistory: (id, from, to) => http(`/hr/attendance/history/${id}?from=${from}&to=${to}`),
    overtime: () => http("/hr/overtime"),
    fileOvertime: async (input) => (await http<{ request: T.OvertimeRequest }>("/hr/overtime", { method: "POST", body: input })).request,
    decideOvertime: async (id, status) => (await http<{ request: T.OvertimeRequest }>(`/hr/overtime/${id}/decision`, { method: "POST", body: { status } })).request,
    leave: () => http("/hr/leave"),
    fileLeave: async (input) => (await http<{ request: T.LeaveRequest }>("/hr/leave", { method: "POST", body: input })).request,
    decideLeave: async (id, status) => (await http<{ request: T.LeaveRequest }>(`/hr/leave/${id}/decision`, { method: "POST", body: { status } })).request,
    payroll: (from, to) => http(`/hr/payroll?from=${from}&to=${to}`),
    previewPayroll: async (input) => (await http<{ preview: T.PayrollPreview }>("/hr/payroll/preview", { method: "POST", body: input })).preview,
    generatePayroll: async (input) => (await http<{ payroll: T.PayrollDetail }>("/hr/payroll", { method: "POST", body: input })).payroll,
    payslip: (id) => http(`/hr/payroll/${id}`),
    editStatutory: async (id, statutory) => (await http<{ payroll: T.PayrollDetail }>(`/hr/payroll/${id}/statutory`, { method: "PUT", body: statutory })).payroll,
    advancePayroll: async (id, status) => (await http<{ payroll: T.PayrollDetail }>(`/hr/payroll/${id}/status`, { method: "POST", body: { status: status.toUpperCase() } })).payroll,
    calculate: async (input) => (await http<{ statutory: T.Statutory }>("/hr/payroll/preview", { method: "POST", body: input })).statutory,
    thirteenthMonth: (year) => http(`/hr/thirteenth-month?year=${year}`),
    payrollReport: (year) => http(`/hr/payroll-reports?year=${year}`),
    loans: () => http("/hr/loans"),
    addLoan: async (input) => (await http<{ loan: T.HrLoan }>("/hr/loans", { method: "POST", body: input })).loan,
    updateLoan: async (id, input) => (await http<{ loan: T.HrLoan }>(`/hr/loans/${id}`, { method: "PUT", body: input })).loan,
    rules: async () => (await http<{ settings: T.PayrollRule[] }>("/hr/settings")).settings,
    saveRules: async (values) => (await http<{ settings: T.PayrollRule[] }>("/hr/settings", { method: "PUT", body: { values } })).settings,
  },
  reports: {
    get: (q) => http(`/reports?${reportParams(q)}`),
    // A plain navigation: the server answers with an attachment, so the page stays where it is.
    exportExcel: async (q) => window.location.assign(`/api/reports/export.xlsx?${reportParams(q)}`),
  },
  admin: {
    users: (q) => {
      const params = new URLSearchParams({ page: String(q.page), perPage: String(q.perPage) });
      if (q.q.trim()) params.set("q", q.q.trim());
      if (q.role) params.set("role", q.role);
      if (q.status) params.set("status", q.status);
      return http(`/admin/users?${params}`);
    },
    createUser: async (input) => (await http<{ user: T.UserAccount }>("/admin/users", { method: "POST", body: input })).user,
    resetUserPassword: (userId, newPassword) => http(`/admin/users/${userId}/password`, { method: "POST", body: { newPassword } }),
    updateUser: async (userId, input) => (await http<{ user: T.UserAccount }>(`/admin/users/${userId}`, { method: "PATCH", body: input })).user,
    deleteUser: (userId) => http(`/admin/users/${userId}`, { method: "DELETE" }),
    residentAccounts: (q) => {
      const params = new URLSearchParams({ page: String(q.page), perPage: String(q.perPage) });
      if (q.q) params.set("q", q.q);
      if (q.status) params.set("status", q.status);
      return http(`/admin/resident-accounts?${params}`);
    },
    residentAccountOptions: () => http("/admin/resident-accounts/options"),
    createResidentAccount: async (input) => (await http<{ account: T.ResidentAccount }>("/admin/resident-accounts", { method: "POST", body: input })).account,
    updateResidentAccount: async (userId, input) =>
      (await http<{ account: T.ResidentAccount }>(`/admin/resident-accounts/${userId}`, { method: "PATCH", body: input })).account,
    resetResidentPassword: (userId, newPassword) => http(`/admin/resident-accounts/${userId}/password`, { method: "POST", body: { newPassword } }),
    residentPeople: (q) => {
      const params = new URLSearchParams();
      if (q.q) params.set("q", q.q);
      if (q.state) params.set("state", q.state);
      return http(`/admin/resident-accounts/people?${params}`);
    },
    provisionResident: (input) => http("/admin/resident-accounts/provision", { method: "POST", body: input }),
    issueActivationCode: (userId, formToken) => http(`/admin/resident-accounts/${userId}/activation-code`, { method: "POST", body: { formToken } }),
    endResidentLink: async (userId, linkId, reason) =>
      (await http<{ account: T.ResidentAccount }>(`/admin/resident-accounts/${userId}/links/${linkId}/end`, { method: "POST", body: { reason } })).account,
    auditLogs: (q) => http(`/admin/audit-logs?${auditParams(q)}`),
    // A plain navigation: the server answers with a CSV attachment, so the page stays where it is.
    exportAuditLogs: async (q) => window.location.assign(`/api/admin/audit-logs/export.csv?${auditParams(q)}`),
    rates: async () => (await http<{ rates: T.RatesAndRules }>("/admin/rates")).rates,
    saveRates: async (input) => (await http<{ rates: T.RatesAndRules }>("/admin/rates", { method: "PUT", body: input })).rates,
    collections: async (months) => (await http<{ months: T.CollectionSummary[] }>(`/reports/monthly?months=${months}`)).months,
    system: () => http("/admin/system"),
    importDatabase: (file) => {
      const form = new FormData();
      form.append("file", file);
      return http("/admin/system/import", { method: "POST", body: form });
    },
  },
};
