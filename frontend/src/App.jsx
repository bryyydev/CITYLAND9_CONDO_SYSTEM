import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth/AuthContext.jsx";
import Layout from "./components/Layout.jsx";
import { EmptyState, ErrorState, FullPageLoading } from "./components/States.jsx";
import { PAGES, homePath } from "./navigation.js";
import LegacyModule from "./pages/LegacyModule.jsx";
import Login from "./pages/Login.jsx";

function RequireAuth({ children }) {
  const { user, checkError, refresh } = useAuth();
  const location = useLocation();
  if (checkError) {
    return (
      <div className="full-center">
        <ErrorState title="Can't reach the CityLand 9 server" message={checkError} onRetry={refresh} />
      </div>
    );
  }
  if (user === undefined) return <FullPageLoading />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return children;
}

// Hiding a page here is only for the menu. The Flask API enforces the real permission.
function Guarded({ page }) {
  const { can } = useAuth();
  if (!can(page.key)) {
    return (
      <EmptyState icon="shield" title="You don't have access to this module">
        Ask the administrator if you need it. Your role's modules are listed in the menu.
      </EmptyState>
    );
  }
  return <LegacyModule page={page} />;
}

function Home() {
  const { user } = useAuth();
  return <Navigate to={homePath(user)} replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth><Layout /></RequireAuth>}>
        <Route index element={<Home />} />
        {PAGES.map((page) => (
          <Route key={page.key} path={page.path} element={<Guarded page={page} />} />
        ))}
        <Route path="*" element={<EmptyState icon="search" title="Page not found">That address doesn't match any module.</EmptyState>} />
      </Route>
    </Routes>
  );
}
