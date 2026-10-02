// Live data service: the local Flask server.
// Connected today: /api/auth/* and /api/resident/*. Every other module still runs on its
// classic Flask screen; its methods reject with NotConnectedError, and config/modules.ts marks
// those modules live: false so their pages link to the classic screen instead of calling here.

import type { DataService } from "./api";
import { NotConnectedError, http, setCsrfToken } from "./http";
import type * as T from "./types";

const notConnected = (feature: string) => () => Promise.reject(new NotConnectedError(feature));
const r = (unitId: number) => `/resident/units/${unitId}`;

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
  },

  dashboards: {
    property: notConnected("The property dashboard"),
    hr: notConnected("The HR dashboard"),
    staff: notConnected("The staff dashboard"),
    system: notConnected("The system dashboard"),
  },
  units: { list: notConnected("Units"), people: notConnected("Unit owners and tenants") },
  billing: {
    list: notConnected("Billing"),
    detail: notConnected("Billing"),
    generate: notConnected("Bill generation"),
    recordPayment: notConnected("Payments"),
    recalculate: notConnected("SOA recalculation"),
    emailSoas: notConnected("SOA email"),
  },
  advances: { list: notConnected("Advance payments"), record: notConnected("Advance payments") },
  receipts: { list: notConnected("Official receipts") },
  water: { list: notConnected("Water readings"), previousReading: notConnected("Water readings"), save: notConnected("Water readings") },
  gatePasses: { list: notConnected("Gate passes"), issue: notConnected("Gate passes"), review: notConnected("Gate passes") },
  certificates: { list: notConnected("Move certificates"), generate: notConnected("Move certificates") },
  expenses: { list: notConnected("Expenses"), add: notConnected("Expenses") },
  maintenance: { list: notConnected("Maintenance"), update: notConnected("Maintenance") },
  vendors: { list: notConnected("Vendors") },
  documents: { list: notConnected("Documents") },
  announcements: { list: notConnected("Announcements"), publish: notConnected("Announcements") },
  hr: {
    employees: notConnected("Employees"),
    attendance: notConnected("Attendance"),
    saveAttendance: notConnected("Attendance"),
    leave: notConnected("Leave"),
    overtime: notConnected("Overtime"),
    decideLeave: notConnected("Leave"),
    decideOvertime: notConnected("Overtime"),
    payroll: notConnected("Payroll"),
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
    residentAccounts: notConnected("Resident accounts"),
    auditLogs: notConnected("Audit logs"),
    rates: notConnected("Rates & Rules"),
    saveRates: notConnected("Rates & Rules"),
    collections: notConnected("Collections"),
  },
};
