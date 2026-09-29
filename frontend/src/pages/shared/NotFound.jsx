import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext.jsx";
import { EmptyState } from "../../components/States.jsx";
import { homePath } from "../../navigation.js";

export default function NotFound() {
  const { user } = useAuth();
  return (
    <div className="card">
      <EmptyState icon="search" title="Page not found" action={<Link className="btn" to={homePath(user)}>Back to home</Link>}>
        That address doesn't match any page in your portal.
      </EmptyState>
    </div>
  );
}
