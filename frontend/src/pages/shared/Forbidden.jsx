import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext.jsx";
import { EmptyState } from "../../components/States.jsx";
import { homePath } from "../../navigation.js";

export default function Forbidden() {
  const { user } = useAuth();
  return (
    <div className="full-center">
      <div className="card" style={{ maxWidth: 560 }}>
        <EmptyState
          icon="shield"
          title="You don't have access to this page"
          action={<Link className="btn primary" to={homePath(user)}>Go to my home page</Link>}
        >
          Your account ({user?.roleLabel}) can't open this part of the system. Ask the administrator if you need it.
        </EmptyState>
      </div>
    </div>
  );
}
