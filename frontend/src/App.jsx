import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth/AuthContext.jsx";
import ProtectedRoute from "./auth/ProtectedRoute.jsx";
import Layout from "./components/Layout.jsx";
import { NAV, PAGES, RESIDENT_NAV, homePath } from "./navigation.js";
import AccountingHome from "./pages/accounting/Home.jsx";
import AdminHome from "./pages/admin/Home.jsx";
import HrHome from "./pages/hr/Home.jsx";
import ResidentHome from "./pages/resident/Home.jsx";
import Maintenance from "./pages/resident/Maintenance.jsx";
import Notices from "./pages/resident/Notices.jsx";
import Statement from "./pages/resident/Statement.jsx";
import Statements from "./pages/resident/Statements.jsx";
import LegacyModule from "./pages/shared/LegacyModule.jsx";
import Login from "./pages/shared/Login.jsx";
import NotFound from "./pages/shared/NotFound.jsx";
import StaffHome from "./pages/staff/Home.jsx";
import SuperadminHome from "./pages/superadmin/Home.jsx";

// One portal per role. A user can only enter their own portal (roles={[role]});
// inside it, every module route is also checked against the server-sent permissions.
const STAFF_PORTALS = [
  { role: "super_admin", base: "superadmin", title: "Superadmin", Home: SuperadminHome },
  { role: "admin", base: "admin", title: "Admin", Home: AdminHome },
  { role: "manager", base: "hr", title: "HR & Payroll", Home: HrHome },
  { role: "staff", base: "staff", title: "Staff", Home: StaffHome },
  { role: "accounting", base: "accounting", title: "Accounting", Home: AccountingHome },
];

/** "/" -> the signed-in user's portal home. */
function HomeRedirect() {
  const { user } = useAuth();
  return <Navigate to={homePath(user)} replace />;
}

/** Old links like /app/billing (before portals existed) -> /app/<portal>/billing. */
function OldPathRedirect() {
  const { user } = useAuth();
  const { pathname } = useLocation();
  const page = PAGES.find((p) => p.path === pathname);
  if (page && user.portal && user.portal !== "resident") return <Navigate to={`/${user.portal}${page.path}`} replace />;
  return <Navigate to={homePath(user)} replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />

      {STAFF_PORTALS.map((portal) => (
        <Route
          key={portal.base}
          path={`/${portal.base}`}
          element={
            <ProtectedRoute roles={[portal.role]}>
              <Layout nav={NAV} base={`/${portal.base}`} title={portal.title} />
            </ProtectedRoute>
          }
        >
          <Route index element={<portal.Home />} />
          {PAGES.map((page) => (
            <Route
              key={page.key}
              path={page.path.slice(1)}
              element={<ProtectedRoute permission={page.key}><LegacyModule page={page} /></ProtectedRoute>}
            />
          ))}
          <Route path="*" element={<NotFound />} />
        </Route>
      ))}

      <Route
        path="/resident"
        element={
          <ProtectedRoute roles={["resident"]}>
            <Layout nav={RESIDENT_NAV} base="/resident" title="Resident Portal" />
          </ProtectedRoute>
        }
      >
        <Route index element={<ResidentHome />} />
        <Route path="soa" element={<Statements />} />
        <Route path="soa/:billId" element={<Statement />} />
        <Route path="maintenance" element={<Maintenance />} />
        <Route path="notices" element={<Notices />} />
        <Route path="*" element={<NotFound />} />
      </Route>

      <Route path="/" element={<ProtectedRoute><HomeRedirect /></ProtectedRoute>} />
      <Route path="*" element={<ProtectedRoute><OldPathRedirect /></ProtectedRoute>} />
    </Routes>
  );
}
