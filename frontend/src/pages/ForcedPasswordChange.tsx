// Shown instead of the workspace while the account has a temporary password (set by an
// administrator, or the old default). The server refuses every other request until it's changed.
import { useNavigate } from "react-router-dom";
import { useAuth, useUser } from "../auth/AuthContext";
import { Button, Card, Icon } from "../components/base/ui";
import { ChangePasswordForm } from "../components/feature/ChangePasswordDrawer";

export default function ForcedPasswordChange() {
  const user = useUser();
  const { logout } = useAuth();
  const navigate = useNavigate();
  return (
    <div className="grid min-h-dvh place-items-center bg-ink-50 px-4 py-10">
      <div className="w-full max-w-md animate-rise-in">
        <div className="mb-6 flex items-center gap-3">
          <div className="grid size-11 place-items-center rounded-xl bg-brand-600 font-display font-bold text-white">C9</div>
          <div className="font-display text-lg font-semibold">CITYLAND 9</div>
        </div>
        <Card className="p-6">
          <div className="mb-4 flex gap-3">
            <span className="grid size-10 shrink-0 place-items-center rounded-full bg-copper-50 text-xl text-copper-600"><Icon name="lock-password-line" /></span>
            <div>
              <h1 className="text-[20px] font-semibold text-ink-900">Choose a new password</h1>
              <p className="text-ink-500">
                Signed in as <b className="text-ink-800">{user.username}</b>. Your current password is temporary, so it must be changed before you continue.
              </p>
            </div>
          </div>
          <ChangePasswordForm submitLabel="Save new password and continue" onDone={() => navigate("/", { replace: true })} />
        </Card>
        <div className="mt-4 text-center">
          <Button variant="ghost" icon="logout-box-r-line" onClick={async () => { await logout(); navigate("/login", { replace: true }); }}>Sign out instead</Button>
        </div>
      </div>
    </div>
  );
}
