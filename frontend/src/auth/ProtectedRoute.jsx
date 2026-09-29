import { Navigate, Outlet, useLocation } from "react-router-dom";
import { ErrorState, FullPageLoading } from "../components/States.jsx";
import Forbidden from "../pages/shared/Forbidden.jsx";
import { useAuth } from "./AuthContext.jsx";

/**
 * Route guard. Checks, in order:
 *   1. the session check finished (loading screen meanwhile)
 *   2. someone is signed in            -> otherwise to /login (then back here)
 *   3. `roles` includes the user's role -> otherwise 403 page
 *   4. `permission` is granted          -> otherwise 403 page
 *
 * <ProtectedRoute roles={["accounting"]}><Layout/></ProtectedRoute>
 * <Route element={<ProtectedRoute permission="billing" />}> ...child routes... </Route>
 *
 * This only decides what React renders. The Flask API checks the same rules on every request.
 */
export default function ProtectedRoute({ roles, permission, children }) {
  const { user, checkError, refresh, hasRole, can } = useAuth();
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
  if ((roles && !hasRole(roles)) || (permission && !can(permission))) return <Forbidden />;
  return children ?? <Outlet />;
}
